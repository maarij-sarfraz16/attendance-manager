from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, getdate
from frappe.utils.caching import request_cache

# A missing punch can only be added this close to the punch the machine does have.
MAX_SPAN = timedelta(hours=24)
STANDARD_HOURS = 8.0
# Attendance fields filled by attendance_custom_logic's overtime calculation, if installed.
HOUR_FIELDS = {"duty_hours": "custom_duty_hours", "overtime": "custom_overtime", "less_duty_hour": "custom_less_duty_hour"}


def _punch_window(logs):
	"""(first IN, last OUT after it) from punches sorted by time; a punch without a log type counts as either.

	Either side is None when the machine has no such punch.
	"""
	start = next((get_datetime(l.time) for l in logs if l.log_type != "OUT"), None)
	if not start:
		return None, None
	end = next((get_datetime(l.time) for l in reversed(logs) if l.log_type != "IN" and get_datetime(l.time) > start), None)
	return start, end


def machine_windows(attendances):
	"""Return {attendance name: (machine_in, machine_out)}: the times the biometric machine recorded.

	`attendances` need name, employee, attendance_date, in_time and out_time. Employee Checkins
	linked to the attendance are used (an overnight session links its next-day OUT too); with none
	linked, that employee's unlinked punches on the attendance date. A side with no punch falls back
	to the attendance's time as the machine import first wrote it, i.e. before any draft from this
	app changed it. Shifts play no part.
	"""
	if not attendances:
		return {}

	logs = {}
	for l in frappe.get_all(
		"Employee Checkin",
		filters={"attendance": ["in", [a["name"] for a in attendances]]},
		fields=["attendance", "log_type", "time"],
		order_by="time asc",
		limit_page_length=0,
	):
		logs.setdefault(l.attendance, []).append(l)

	unlinked = [a for a in attendances if a["name"] not in logs]
	if unlinked:
		dates = [getdate(a["attendance_date"]) for a in unlinked]
		day_logs = {}
		for l in frappe.get_all(
			"Employee Checkin",
			filters=[
				["employee", "in", list({a["employee"] for a in unlinked})],
				["attendance", "is", "not set"],
				["time", ">=", datetime.combine(min(dates), datetime.min.time())],
				["time", "<", datetime.combine(max(dates) + timedelta(days=1), datetime.min.time())],
			],
			fields=["employee", "log_type", "time"],
			order_by="time asc",
			limit_page_length=0,
		):
			day_logs.setdefault((l.employee, get_datetime(l.time).date()), []).append(l)
		for a in unlinked:
			logs[a["name"]] = day_logs.get((a["employee"], getdate(a["attendance_date"])), [])

	# The oldest draft's "current" times are the attendance as imported, before any draft was applied.
	imported = {}
	for d in frappe.get_all(
		"Attendance Draft",
		filters={"attendance": ["in", [a["name"] for a in attendances]]},
		fields=["attendance", "original_in_time", "original_out_time"],
		order_by="creation asc",
		limit_page_length=0,
	):
		imported.setdefault(d.attendance, (d.original_in_time, d.original_out_time))

	windows = {}
	for a in attendances:
		start, end = _punch_window(logs[a["name"]])
		att_in, att_out = imported.get(a["name"], (a.get("in_time"), a.get("out_time")))
		start = start or (get_datetime(att_in) if att_in else None)
		end = end or (get_datetime(att_out) if att_out else None)
		if start and end and end <= start:
			end = None
		windows[a["name"]] = (start, end)
	return windows


def time_bounds(machine_in, machine_out):
	"""(earliest, latest) an IN or OUT time may be set to, or (None, None) when nothing was punched.

	Between the machine check-in and check-out. A missing check-out can be added anywhere in the
	24 hours after the check-in; likewise a missing check-in in the 24 hours before the check-out.
	"""
	if not machine_in and not machine_out:
		return None, None
	if not machine_out:
		return machine_in, machine_in + MAX_SPAN
	if not machine_in:
		return machine_out - MAX_SPAN, machine_out
	return machine_in, machine_out


def _slab(hours):
	"""Hours floored to a 15 minute slab."""
	minutes = int(flt(hours) * 60)
	return float(f"{(minutes - minutes % 15) / 60:.2f}")


def shift_hours(shift):
	"""Length of a Shift Type in hours (an overnight shift wraps past midnight); 8 without a shift."""
	if not shift:
		return STANDARD_HOURS
	start, end = frappe.get_cached_value("Shift Type", shift, ["start_time", "end_time"]) or (None, None)
	if start is None or end is None:
		return STANDARD_HOURS
	seconds = (get_datetime(f"2000-01-01 {end}") - get_datetime(f"2000-01-01 {start}")).total_seconds()
	if seconds <= 0:
		seconds += 24 * 3600
	return round(seconds / 3600, 2)


def calculate_hours(employee, attendance_date, status, in_time, out_time, late_minutes=0, status_override=None, shift=None):
	"""Return (status, working_hours, duty_hours, overtime, less_duty_hour) for the given times.

	Same rules as attendance_custom_logic (minimum working hours, overtime slabs, holiday
	overtime), computed without writing anything, except that duty hours are the length of the
	attendance's `shift` (8 without one) and every employee gets overtime for time worked beyond
	it. Unless `status_override` is given, a Present/Absent/Half Day day is Absent while it lacks
	an IN or an OUT (or enough working hours), and an Absent day becomes Present once it has both.
	"""
	duty_hours = shift_hours(shift)
	working_hours = round((out_time - in_time).total_seconds() / 3600, 2) if in_time and out_time else 0.0
	emp = frappe.db.get_value(
		"Employee", employee, ["employment_type", "holiday_list", "custom_allow_overtime"], as_dict=True
	) or frappe._dict()
	min_hours = 2 if emp.custom_allow_overtime else 4

	if status_override:
		status = status_override
	elif status in ("Present", "Absent", "Half Day"):
		if working_hours < min_hours:
			status = "Absent"
		elif status == "Absent":
			status = "Present"
	if status not in ("Present", "Half Day") or not working_hours:
		return status, working_hours, 0.0, 0.0, 0.0

	if (
		status == "Present"
		and emp.custom_allow_overtime
		and (emp.employment_type or "").lower() == "permanent"
		and emp.holiday_list
		and frappe.db.exists("Holiday", {"parent": emp.holiday_list, "holiday_date": attendance_date})
	):
		# Whole holiday counts as overtime, rounded to the nearest 15 minutes.
		return status, working_hours, 0.0, float(f"{15 * round(int(working_hours * 60) / 15) / 60:.2f}"), 0.0

	if working_hours < duty_hours:
		duty = _slab(round(working_hours, 2))
		return status, working_hours, duty, 0.0, round(duty_hours - duty, 2) if duty > 0 else 0.0

	overtime_minutes = int((out_time - in_time).total_seconds() // 60) - int(round(duty_hours * 60))
	if overtime_minutes <= 0:
		return status, working_hours, duty_hours, 0.0, 0.0
	if not late_minutes:
		rounded = overtime_minutes // 30 * 30
	elif overtime_minutes < 15:
		rounded = 0
	elif overtime_minutes < 30:
		rounded = 15
	elif overtime_minutes < 45:
		rounded = 30
	elif overtime_minutes < 70:
		rounded = 45
	else:
		rounded = overtime_minutes // 15 * 15
	return status, working_hours, duty_hours, float(f"{rounded / 60:.2f}"), 0.0


def _hour_fields():
	"""Draft field -> Attendance field, for the hour fields this site's Attendance has."""
	meta = frappe.get_meta("Attendance")
	return {k: v for k, v in HOUR_FIELDS.items() if meta.has_field(v)}


def viewer_employee():
	return _employee_of(frappe.session.user)


def manager_of(employee):
	"""The `reports_to` of an Employee (looked up once per request)."""
	return _manager_of(employee) if employee else None


@request_cache
def _manager_of(employee):
	return frappe.db.get_value("Employee", employee, "reports_to")


VERIFIER_ROLES = {"Attendance User"}


def _is_verifier(user):
	# Administrator holds every role but does not verify; see `has_permission`.
	return user != "Administrator" and bool(VERIFIER_ROLES & set(frappe.get_roles(user)))


# Permission checks run several times per save or list load; these lookups are done once per request.
@request_cache
def _employee_of(user):
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name")


@request_cache
def _reportees(me):
	return frappe.get_all("Employee", filters={"reports_to": me}, pluck="name", limit_page_length=0) if me else []


# Composite indexes the page's queries rely on, on tables owned by other apps. Multi-column
# indexes are never dropped by schema sync, so they survive those apps' migrations.
INDEXES = {
	"Employee Checkin": (["attendance", "time"], ["employee", "time"]),
	"Employee": (["reports_to", "status"], ["user_id", "status"]),
	"Attendance": (["employee", "attendance_date"],),
	"Attendance Draft": (["attendance", "docstatus"], ["employee", "docstatus"]),
}


def ensure_indexes():
	"""Run after every migrate; adds only the indexes that are missing."""
	for doctype, indexes in INDEXES.items():
		for fields in indexes:
			frappe.db.add_index(doctype, fields)


def get_permission_query_conditions(user=None):
	"""Administrator and Attendance Users see every draft; a manager only drafts of their reportees;
	anyone else sees nothing."""
	user = user or frappe.session.user
	if user == "Administrator" or _is_verifier(user):
		return ""
	team = _reportees(_employee_of(user))
	if not team:
		return "1=0"
	return "`tabAttendance Draft`.`employee` in ({0})".format(", ".join(frappe.db.escape(e) for e in team))


def _doc_employee(doc):
	if doc.get("employee"):
		return doc.employee
	if doc.get("attendance"):
		return frappe.db.get_value("Attendance", doc.attendance, "employee")


def has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		# Full rights over every draft except submitting it, which is left to an Attendance User.
		return ptype != "submit"
	is_verifier = _is_verifier(user)
	emp = _doc_employee(doc)
	me = _employee_of(user)
	is_manager = bool(me and emp and manager_of(emp) == me)

	if doc.is_new() and not emp:
		# Blank form: only someone who manages people may start one.
		return bool(_reportees(me))
	if ptype in ("write", "create", "delete"):
		# Only the direct manager creates, edits or deletes.
		return is_manager
	if ptype == "submit":
		# Maker-checker: an Attendance User verifies, never the manager who made the draft.
		return is_verifier and not is_manager
	# read, select, report, export, print, email, share, cancel, ...
	return is_verifier or is_manager


def get_attendance(name):
	"""The Attendance fields a draft snapshots and calculates from."""
	late_field = ["custom_late_minutes"] if frappe.get_meta("Attendance").has_field("custom_late_minutes") else []
	return frappe.db.get_value(
		"Attendance",
		name,
		["name", "employee", "employee_name", "department", "attendance_date", "shift", "in_time", "out_time",
		 "status", "working_hours", "docstatus", *_hour_fields().values(), *late_field],
		as_dict=True,
	)


class AttendanceDraft(Document):
	def validate(self):
		att = get_attendance(self.attendance)
		if not att or att.docstatus != 1:
			frappe.throw(_("Attendance {0} must be submitted.").format(self.attendance))

		self.set_snapshot(att)
		# Plain saves only: the person verifying (submitting) need not be the manager.
		if self.docstatus == 0 and self._action == "save":
			self.validate_manager()
			self.validate_no_other_draft()
		self.validate_times(att)
		self.set_hours(att)

	def set_snapshot(self, att):
		"""Copy the attendance as it is now: what the draft overwrites, so cancelling puts it back exactly."""
		self.employee = att.employee
		self.employee_name = att.employee_name
		self.department = att.department
		self.attendance_date = att.attendance_date
		self.shift = att.shift
		self.original_in_time = att.in_time
		self.original_out_time = att.out_time
		self.original_status = att.status
		self.original_working_hours = att.working_hours
		for field, att_field in _hour_fields().items():
			self.set("original_" + field, flt(att.get(att_field)))

	def set_hours(self, att):
		"""Status, working hours, duty hours and overtime the new times give."""
		self.new_status, self.working_hours, self.duty_hours, self.overtime, self.less_duty_hour = calculate_hours(
			self.employee,
			self.attendance_date,
			self.original_status or att.status,
			get_datetime(self.in_time) if self.in_time else None,
			get_datetime(self.out_time) if self.out_time else None,
			att.get("custom_late_minutes") or 0,
			self.status,
			att.shift,
		)

	def before_submit(self):
		self.check_actor("submit")

	def before_cancel(self):
		self.check_actor("cancel")

	def check_actor(self, action):
		if not has_permission(self, action):
			frappe.throw(_("You are not allowed to {0} this draft.").format(_(action)), frappe.PermissionError)

	def validate_manager(self):
		if frappe.session.user == "Administrator":
			return
		me = viewer_employee()
		if not me or manager_of(self.employee) != me:
			frappe.throw(_("{0} does not report to you.").format(self.employee_name), frappe.PermissionError)

	def validate_no_other_draft(self):
		other = frappe.db.exists(
			"Attendance Draft", {"attendance": self.attendance, "docstatus": 0, "name": ["!=", self.name]}
		)
		if other:
			frappe.throw(_("Draft {0} is already pending for this attendance.").format(other))

	def validate_times(self, att):
		"""Changed times must lie between the machine check-in and check-out (see `time_bounds`).
		A missing check-out (or check-in) may be added. Shift and overtime play no part.
		A status-only change needs no machine punch."""
		new_in = get_datetime(self.in_time) if self.in_time else None
		new_out = get_datetime(self.out_time) if self.out_time else None
		old_in = get_datetime(self.original_in_time) if self.original_in_time else None
		old_out = get_datetime(self.original_out_time) if self.original_out_time else None
		times_changed = new_in != old_in or new_out != old_out
		if not times_changed and (not self.status or self.status == att.status):
			frappe.throw(_("Nothing changed: new times and status match the current attendance."))

		label = f"{self.employee_name} ({self.attendance_date})"
		# Recomputed on every save and on submit, so punches always come from the machine data.
		machine_in, machine_out = machine_windows([att])[self.attendance]
		self.machine_in_time = machine_in
		self.machine_out_time = machine_out
		if not times_changed:
			return
		lo, hi = time_bounds(machine_in, machine_out)
		if not lo:
			frappe.throw(_("{0}: the machine has no check-in or check-out, times cannot be changed.").format(label))

		fmt = "%d-%m-%Y %H:%M:%S"
		for name, new, old in ((_("IN"), new_in, old_in), (_("OUT"), new_out, old_out)):
			if new == old:
				continue
			if not new:
				frappe.throw(_("{0}: {1} time cannot be cleared.").format(label, name))
			if new < lo:
				frappe.throw(
					_("{0}: {1} time cannot be earlier than machine check-in {2}.").format(label, name, lo.strftime(fmt))
					if machine_in
					else _("{0}: {1} time cannot be earlier than {2} (24 hours before machine check-out).").format(label, name, lo.strftime(fmt))
				)
			if new > hi:
				frappe.throw(
					_("{0}: {1} time cannot be later than machine check-out {2}.").format(label, name, hi.strftime(fmt))
					if machine_out
					else _("{0}: {1} time cannot be later than {2} (24 hours after machine check-in).").format(label, name, hi.strftime(fmt))
				)
		if new_in and new_out and new_out <= new_in:
			frappe.throw(_("{0}: OUT time must be after IN time.").format(label))

	def _values(self, prefix):
		"""Attendance values held by this draft: prefix "" for the new ones, "original_" for the old."""
		values = {
			"in_time": self.get(prefix + "in_time"),
			"out_time": self.get(prefix + "out_time"),
			"status": self.get("original_status" if prefix else "new_status"),
			"working_hours": flt(self.get(prefix + "working_hours")),
		}
		for field, att_field in _hour_fields().items():
			values[att_field] = flt(self.get(prefix + field))
		return values

	def on_submit(self):
		self.apply(self._values(""), self._values("original_"))

	def on_cancel(self):
		self.apply(self._values("original_"), self._values(""))

	def apply(self, new, old):
		"""Write times, status and hours to the Attendance (the only place they change) and log it there."""
		frappe.db.set_value("Attendance", self.attendance, new)
		labels = {"in_time": "In Time", "out_time": "Out Time", "status": "Status", "working_hours": "Working Hours",
			"custom_duty_hours": "Duty Hours", "custom_overtime": "Overtime"}
		def show(f, v):
			if v in (None, ""):
				return "-"
			return str(get_datetime(v)) if f in ("in_time", "out_time") else str(v)

		changes = ", ".join(
			f"{label} <b>{show(f, old[f])}</b> → <b>{show(f, new[f])}</b>"
			for f, label in labels.items()
			if f in new and show(f, new[f]) != show(f, old[f])
		)
		frappe.get_doc("Attendance", self.attendance).add_comment(
			"Info",
			f"{frappe.session.user} changed {changes} ({frappe.utils.get_link_to_form(self.doctype, self.name)})",
		)
		# Tells any open Attendance list view to refresh just this one record.
		frappe.publish_realtime(
			"list_update", {"doctype": "Attendance", "name": self.attendance}, after_commit=True
		)
