import json
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate

ADMIN_ROLES = {"System Manager", "HR Manager"}


def _is_admin():
	return bool(ADMIN_ROLES & set(frappe.get_roles()))


def _viewer_employee():
	return frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "name")


def _scope_filters():
	"""Employee filters for who the current user may see on this page.

	Admins see everyone (view only). Anyone else sees only the employees whose
	`reports_to` is their own Employee record, so a manager never sees peers.
	"""
	if _is_admin():
		return {"status": "Active"}

	me = _viewer_employee()
	if not me:
		return None
	return {"status": "Active", "reports_to": me}


def _shift_window(shift, attendance_date):
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


@frappe.whitelist()
def get_team_attendance(employee=None, employee_name=None, from_date=None, to_date=None):
	scope = _scope_filters()
	if scope is None:
		return {"can_edit": False, "rows": [], "message": _("Your user is not linked to an active Employee.")}

	filters = dict(scope)
	if employee:
		filters["name"] = ["like", f"%{employee}%"]
	if employee_name:
		filters["employee_name"] = ["like", f"%{employee_name}%"]

	employees = frappe.get_all("Employee", filters=filters, pluck="name", limit_page_length=0)
	can_edit = not _is_admin()
	if not employees:
		return {"can_edit": can_edit, "rows": []}

	att_filters = {"employee": ["in", employees], "docstatus": 1}
	if from_date and to_date:
		att_filters["attendance_date"] = ["between", [from_date, to_date]]

	rows = frappe.get_all(
		"Attendance",
		filters=att_filters,
		fields=["name", "employee", "employee_name", "department", "attendance_date", "status", "shift", "in_time", "out_time"],
		order_by="employee asc, attendance_date asc",
		limit_page_length=1000,
	)

	reports_to = {
		e.name: e.reports_to_name
		for e in frappe.db.sql(
			"""SELECT e.name, m.employee_name AS reports_to_name
			FROM `tabEmployee` e LEFT JOIN `tabEmployee` m ON m.name = e.reports_to
			WHERE e.name IN %(emps)s""",
			{"emps": employees},
			as_dict=True,
		)
	}

	for r in rows:
		start, end = _shift_window(r.shift, r.attendance_date)
		r["reports_to_name"] = reports_to.get(r.employee) or ""
		r["min_in_time"] = start.strftime("%Y-%m-%d %H:%M:%S") if start else None
		r["max_out_time"] = end.strftime("%Y-%m-%d %H:%M:%S") if end else None
		r["attendance_date"] = str(r["attendance_date"])
		r["in_time"] = str(r["in_time"]) if r["in_time"] else None
		r["out_time"] = str(r["out_time"]) if r["out_time"] else None

	return {"can_edit": can_edit, "rows": rows}


@frappe.whitelist()
def update_times(changes):
	"""Save in/out time changes. Each change: {name, in_time, out_time}.

	Rules, enforced here regardless of what the browser sends:
	  - caller must be the direct manager (reports_to) of the employee; admins are view only
	  - IN time may only move later than shift start
	  - OUT time may only move earlier than shift end
	  - OUT must stay after IN
	"""
	if _is_admin():
		frappe.throw(_("Admins have view-only access on this page."), frappe.PermissionError)

	me = _viewer_employee()
	if not me:
		frappe.throw(_("Your user is not linked to an active Employee."), frappe.PermissionError)

	changes = json.loads(changes) if isinstance(changes, str) else changes
	if not changes:
		return _("Nothing to update")

	# Validate everything first so a bad row doesn't leave a partial save.
	updates = []
	for c in changes:
		att = frappe.db.get_value(
			"Attendance",
			c.get("name"),
			["name", "employee", "employee_name", "attendance_date", "shift", "in_time", "out_time", "docstatus"],
			as_dict=True,
		)
		if not att or att.docstatus != 1:
			frappe.throw(_("Attendance {0} not found").format(c.get("name")))

		if frappe.db.get_value("Employee", att.employee, "reports_to") != me:
			frappe.throw(
				_("{0} does not report to you.").format(att.employee_name), frappe.PermissionError
			)

		new_in = get_datetime(c["in_time"]) if c.get("in_time") else None
		new_out = get_datetime(c["out_time"]) if c.get("out_time") else None
		old_in = get_datetime(att.in_time) if att.in_time else None
		old_out = get_datetime(att.out_time) if att.out_time else None
		if new_in == old_in and new_out == old_out:
			continue

		shift_start, shift_end = _shift_window(att.shift, att.attendance_date)
		if not shift_start:
			frappe.throw(_("{0} has no shift on {1}, times cannot be changed.").format(att.employee_name, att.attendance_date))

		label = f"{att.employee_name} ({att.attendance_date})"
		if new_in != old_in:
			if not new_in or new_in < shift_start:
				frappe.throw(_("{0}: IN time cannot be earlier than shift start {1}.").format(label, shift_start.strftime("%H:%M")))
		if new_out != old_out:
			if not new_out or new_out > shift_end:
				frappe.throw(_("{0}: OUT time cannot be later than shift end {1}.").format(label, shift_end.strftime("%H:%M")))
		if new_in and new_out and new_out <= new_in:
			frappe.throw(_("{0}: OUT time must be after IN time.").format(label))

		updates.append((att, new_in, new_out, old_in, old_out))

	for att, new_in, new_out, old_in, old_out in updates:
		values = {"in_time": new_in, "out_time": new_out}
		if new_in and new_out:
			values["working_hours"] = round((new_out - new_in).total_seconds() / 3600, 2)
		frappe.db.set_value("Attendance", att.name, values)
		frappe.get_doc("Attendance", att.name).add_comment(
			"Info",
			f"{frappe.session.user} changed In Time <b>{old_in or '-'}</b> → <b>{new_in or '-'}</b>, "
			f"Out Time <b>{old_out or '-'}</b> → <b>{new_out or '-'}</b> (Team Attendance)",
		)

	return _("{0} record(s) updated").format(len(updates))
