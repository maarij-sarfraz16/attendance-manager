from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, getdate


def shift_window(shift, attendance_date):
	"""Return (shift_start, shift_end) datetimes for a given date, or (None, None)."""
	if not shift:
		return None, None

	start, end = frappe.db.get_value("Shift Type", shift, ["start_time", "end_time"]) or (None, None)
	if start is None or end is None:
		return None, None

	day = datetime.combine(getdate(attendance_date), datetime.min.time())
	shift_start = day + start
	shift_end = day + end
	if shift_end <= shift_start:  # night shift ends next day
		shift_end += timedelta(days=1)
	return shift_start, shift_end


def viewer_employee():
	return frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "name")


HR_ROLES = {"HR User"}


def _is_hr(user):
	return user == "Administrator" or bool(HR_ROLES & set(frappe.get_roles(user)))


def _employee_of(user):
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name")


def _reportees(me):
	return frappe.get_all("Employee", filters={"reports_to": me}, pluck="name", limit_page_length=0) if me else []


def get_permission_query_conditions(user=None):
	"""HR sees every draft; a manager only drafts of their reportees; anyone else sees nothing."""
	user = user or frappe.session.user
	if _is_hr(user):
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
	is_hr = _is_hr(user)
	emp = _doc_employee(doc)
	me = _employee_of(user)
	is_manager = bool(me and emp and frappe.db.get_value("Employee", emp, "reports_to") == me)

	if doc.is_new() and not emp:
		# Blank form: only someone who manages people may start one.
		return is_hr or bool(_reportees(me))
	if ptype in ("write", "create", "delete"):
		# HR may view/submit/cancel but only the direct manager edits.
		return is_manager
	# read, select, report, export, print, email, share, submit, cancel, ...
	return is_hr or is_manager


class AttendanceDraft(Document):
	def validate(self):
		att = frappe.db.get_value(
			"Attendance",
			self.attendance,
			["employee", "employee_name", "department", "attendance_date", "shift", "in_time", "out_time", "docstatus"],
			as_dict=True,
		)
		if not att or att.docstatus != 1:
			frappe.throw(_("Attendance {0} must be submitted.").format(self.attendance))

		self.employee = att.employee
		self.employee_name = att.employee_name
		self.department = att.department
		self.attendance_date = att.attendance_date
		self.shift = att.shift
		self.original_in_time = att.in_time
		self.original_out_time = att.out_time

		# Plain saves only: the person verifying (submitting) need not be the manager.
		if self.docstatus == 0 and self._action == "save":
			self.validate_manager()
			self.validate_no_other_draft()
		self.validate_times()

	def before_submit(self):
		self.check_actor("submit")

	def before_cancel(self):
		self.check_actor("cancel")

	def check_actor(self, action):
		if not has_permission(self, action):
			frappe.throw(_("You are not allowed to {0} this draft.").format(_(action)), frappe.PermissionError)

	def validate_manager(self):
		me = viewer_employee()
		if not me or frappe.db.get_value("Employee", self.employee, "reports_to") != me:
			frappe.throw(_("{0} does not report to you.").format(self.employee_name), frappe.PermissionError)

	def validate_no_other_draft(self):
		other = frappe.db.exists(
			"Attendance Draft", {"attendance": self.attendance, "docstatus": 0, "name": ["!=", self.name]}
		)
		if other:
			frappe.throw(_("Draft {0} is already pending for this attendance.").format(other))

	def validate_times(self):
		"""IN may only move later than shift start, OUT earlier than shift end (lifted for overtime employees)."""
		new_in = get_datetime(self.in_time) if self.in_time else None
		new_out = get_datetime(self.out_time) if self.out_time else None
		old_in = get_datetime(self.original_in_time) if self.original_in_time else None
		old_out = get_datetime(self.original_out_time) if self.original_out_time else None
		if new_in == old_in and new_out == old_out:
			frappe.throw(_("Nothing changed: new times match the current attendance."))

		label = f"{self.employee_name} ({self.attendance_date})"
		if not frappe.db.get_value("Employee", self.employee, "custom_allow_overtime"):
			shift_start, shift_end = shift_window(self.shift, self.attendance_date)
			if not shift_start:
				frappe.throw(_("{0} has no shift on {1}, times cannot be changed.").format(self.employee_name, self.attendance_date))
			if new_in != old_in and (not new_in or new_in < shift_start):
				frappe.throw(_("{0}: IN time cannot be earlier than shift start {1}.").format(label, shift_start.strftime("%H:%M")))
			if new_out != old_out and (not new_out or new_out > shift_end):
				frappe.throw(_("{0}: OUT time cannot be later than shift end {1}.").format(label, shift_end.strftime("%H:%M")))
		if new_in and new_out and new_out <= new_in:
			frappe.throw(_("{0}: OUT time must be after IN time.").format(label))

	def on_submit(self):
		self.apply_times(self.in_time, self.out_time, self.original_in_time, self.original_out_time)

	def on_cancel(self):
		self.apply_times(self.original_in_time, self.original_out_time, self.in_time, self.out_time)

	def apply_times(self, new_in, new_out, old_in, old_out):
		"""Write times to the Attendance (the only place they change) and log it there."""
		values = {"in_time": new_in, "out_time": new_out}
		if new_in and new_out:
			values["working_hours"] = round((get_datetime(new_out) - get_datetime(new_in)).total_seconds() / 3600, 2)
		frappe.db.set_value("Attendance", self.attendance, values)
		frappe.get_doc("Attendance", self.attendance).add_comment(
			"Info",
			f"{frappe.session.user} changed In Time <b>{old_in or '-'}</b> → <b>{new_in or '-'}</b>, "
			f"Out Time <b>{old_out or '-'}</b> → <b>{new_out or '-'}</b> "
			f"({frappe.utils.get_link_to_form(self.doctype, self.name)})",
		)
		# Tells any open Attendance list view to refresh just this one record.
		frappe.publish_realtime(
			"list_update", {"doctype": "Attendance", "name": self.attendance}, after_commit=True
		)
