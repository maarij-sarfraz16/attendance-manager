import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate

from attendnace_manager.attendnace_manager.doctype.attendance_draft.attendance_draft import (
	_hour_fields,
	calculate_hours as _calculate_hours,
	get_attendance as _get_attendance,
	machine_windows as _machine_windows,
	manager_of as _manager_of,
	time_bounds as _time_bounds,
	viewer_employee as _viewer_employee,
)

_FMT = "%Y-%m-%d %H:%M:%S"

def _scope_filters():
	"""Employee filters for who the current user may see on this page.

	Everyone, including Administrator and System Manager, sees only the
	employees whose `reports_to` is their own Employee record.
	"""
	me = _viewer_employee()
	if not me:
		return None
	return {"status": "Active", "reports_to": me}


@frappe.whitelist()
def get_team_attendance(employee=None, employee_name=None, from_date=None, to_date=None, department=None):
	scope = _scope_filters()
	if scope is None:
		return {"can_edit": False, "rows": [], "message": _("Your user is not linked to an active Employee.")}

	me = scope["reports_to"]
	manager = frappe.db.get_value("Employee", me, "employee_name") or me
	departments = sorted(
		d
		for d in frappe.get_all("Employee", filters=scope, pluck="department", distinct=True, limit_page_length=0)
		if d
	)

	filters = dict(scope)
	if department:
		filters["department"] = department
	if employee:
		filters["name"] = ["like", f"%{employee}%"]
	if employee_name:
		filters["employee_name"] = ["like", f"%{employee_name}%"]

	team = frappe.get_all(
		"Employee",
		filters=filters,
		fields=["name", "employee_name", "department"],
		order_by="name asc",
		limit_page_length=0,
	)
	employees = [e.name for e in team]
	can_edit = True
	if not employees:
		return {
			"can_edit": can_edit,
			"rows": [],
			"manager": manager,
			"departments": departments,
			"message": _("No employees report to you."),
		}

	from_date = getdate(from_date) if from_date else None
	to_date = getdate(to_date) if to_date else None
	if from_date and to_date and from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))

	att_filters = {"employee": ["in", employees], "docstatus": 1}
	if from_date and to_date:
		att_filters["attendance_date"] = ["between", [from_date, to_date]]
	elif from_date:
		att_filters["attendance_date"] = [">=", from_date]
	elif to_date:
		att_filters["attendance_date"] = ["<=", to_date]

	hour_fields = _hour_fields()
	rows = frappe.get_all(
		"Attendance",
		filters=att_filters,
		fields=["name", "employee", "employee_name", "department", "attendance_date", "status", "shift", "in_time", "out_time",
			*hour_fields.values()],
		order_by="employee asc, attendance_date asc",
		limit_page_length=0,
	)

	drafts = {
		d.attendance: d
		for d in frappe.get_all(
			"Attendance Draft",
			filters={"attendance": ["in", [r.name for r in rows]], "docstatus": 0},
			fields=["name", "attendance", "in_time", "out_time", "status", "new_status", "duty_hours", "overtime"],
		)
	} if rows else {}

	dept = {e.name: e.department for e in team}
	windows = _machine_windows(rows)

	for r in rows:
		start, end = windows[r.name]
		r["department"] = dept.get(r.employee) or ""
		# Everyone listed reports to the viewer.
		r["reports_to_name"] = manager
		# Only the direct manager may edit; every row here is a direct report of the viewer.
		lo, hi = _time_bounds(start, end)
		# Times move only inside the machine bracket (checked again on save); nothing punched, nothing to edit.
		r["editable"] = bool(lo)
		r["machine_in"] = start.strftime(_FMT) if start else None
		r["machine_out"] = end.strftime(_FMT) if end else None
		r["min_in_time"] = lo.strftime(_FMT) if lo else None
		r["max_out_time"] = hi.strftime(_FMT) if hi else None
		r["duty_hours"] = flt(r.pop(hour_fields["duty_hours"], 0)) if "duty_hours" in hour_fields else None
		r["overtime"] = flt(r.pop(hour_fields["overtime"], 0)) if "overtime" in hour_fields else None
		r.pop(hour_fields.get("less_duty_hour"), None)
		r["attendance_date"] = str(r["attendance_date"])
		r["in_time"] = r["in_time"].strftime(_FMT) if r["in_time"] else None
		r["out_time"] = r["out_time"].strftime(_FMT) if r["out_time"] else None

		# A pending draft is what the row will become once submitted, so its values are
		# shown in place of the attendance's; the attendance's own status is kept for "was".
		d = drafts.get(r.name)
		r["draft"] = d.name if d else None
		r["att_status"] = r.status
		r["draft_status"] = None
		if d:
			r["in_time"] = get_datetime(d.in_time).strftime(_FMT) if d.in_time else None
			r["out_time"] = get_datetime(d.out_time).strftime(_FMT) if d.out_time else None
			r["status"] = d.new_status or r.status
			r["draft_status"] = d.status or None
			r["duty_hours"] = flt(d.duty_hours)
			r["overtime"] = flt(d.overtime)

	# Every team member shows for every day in the range; days without a submitted
	# record get a read-only "No attendance" row (range capped at 93 days).
	days = [None]
	if from_date and to_date and (to_date - from_date).days <= 92:
		days = [from_date + timedelta(days=i) for i in range((to_date - from_date).days + 1)]
	have = {(r.employee, r.attendance_date) for r in rows}
	with_rows = {r.employee for r in rows}
	for e in team:
		for d in days:
			if (e.name, str(d) if d else None) in have:
				continue
			if d is None and e.name in with_rows:
				continue
			rows.append(
				{
					"name": None,
					"employee": e.name,
					"employee_name": e.employee_name,
					"department": e.department,
					"attendance_date": str(d) if d else None,
					"status": _("No attendance"),
					"shift": None,
					"in_time": None,
					"out_time": None,
					"reports_to_name": manager,
					"machine_in": None,
					"machine_out": None,
					"min_in_time": None,
					"max_out_time": None,
					"duty_hours": None,
					"overtime": None,
					"editable": False,
				}
			)

	rows.sort(key=lambda r: (r["employee"], r["attendance_date"] or ""))

	statuses = [s for s in (frappe.get_meta("Attendance").get_field("status").options or "").split("\n") if s]
	return {"can_edit": can_edit, "rows": rows, "manager": manager, "departments": departments, "statuses": statuses}


@frappe.whitelist()
def preview_hours(name, in_time=None, out_time=None, status=None):
	"""Status, duty hours and overtime the attendance would get with these times (nothing is saved)."""
	# Called on every edit, so only the needed fields are read, not the whole document.
	att = _get_attendance(name)
	if not att:
		frappe.throw(_("Attendance {0} not found.").format(name), frappe.DoesNotExistError)
	me = _viewer_employee()
	if not me or _manager_of(att.employee) != me:
		frappe.throw(_("{0} does not report to you.").format(att.employee_name), frappe.PermissionError)
	status, working_hours, duty_hours, overtime, _less = _calculate_hours(
		att.employee,
		att.attendance_date,
		att.status,
		get_datetime(in_time) if in_time else None,
		get_datetime(out_time) if out_time else None,
		att.get("custom_late_minutes") or 0,
		status or None,
		att.shift,
	)
	return {"status": status, "working_hours": working_hours, "duty_hours": duty_hours, "overtime": overtime}


@frappe.whitelist()
def save_drafts(changes):
	"""Save in/out time and status changes as Attendance Drafts. Each change: {name, in_time, out_time, status}.

	Nothing touches the Attendance until a draft is verified and submitted. All rules
	(direct manager only, times within the machine check-in/check-out) live in the Attendance
	Draft doctype, which also works out the new status, duty hours and overtime. An existing
	pending draft for the same attendance is updated, not duplicated.
	"""
	changes = json.loads(changes) if isinstance(changes, str) else changes
	if not changes:
		return {"message": _("Nothing to save"), "saved": []}

	saved = []
	for c in changes:
		existing = frappe.db.get_value("Attendance Draft", {"attendance": c.get("name"), "docstatus": 0})
		doc = frappe.get_doc("Attendance Draft", existing) if existing else frappe.new_doc("Attendance Draft")
		doc.attendance = c.get("name")
		doc.in_time = c.get("in_time") or None
		doc.out_time = c.get("out_time") or None
		doc.status = c.get("status") or None
		doc.save()
		saved.append(
			{"attendance": doc.attendance, "draft": doc.name, "status": doc.new_status,
			 "duty_hours": doc.duty_hours, "overtime": doc.overtime}
		)

	return {"message": _("{0} draft(s) saved for verification").format(len(saved)), "saved": saved}
