import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import getdate

from attendnace_manager.attendnace_manager.doctype.attendance_draft.attendance_draft import (
	shift_window as _shift_window,
	viewer_employee as _viewer_employee,
)

def _scope_filters():
	"""Employee filters for who the current user may see on this page.

	Everyone, including Administrator, System Manager and HR Manager, sees only the
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
		fields=["name", "employee_name", "department", "custom_allow_overtime"],
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

	rows = frappe.get_all(
		"Attendance",
		filters=att_filters,
		fields=["name", "employee", "employee_name", "department", "attendance_date", "status", "shift", "in_time", "out_time"],
		order_by="employee asc, attendance_date asc",
		limit_page_length=0,
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

	drafts = {
		d.attendance: d.name
		for d in frappe.get_all(
			"Attendance Draft",
			filters={"attendance": ["in", [r.name for r in rows]], "docstatus": 0},
			fields=["name", "attendance"],
		)
	} if rows else {}

	dept = {e.name: e.department for e in team}
	overtime = {e.name: bool(e.custom_allow_overtime) for e in team}

	for r in rows:
		start, end = _shift_window(r.shift, r.attendance_date)
		r["department"] = dept.get(r.employee) or ""
		r["reports_to_name"] = reports_to.get(r.employee) or ""
		# Only the direct manager may edit; every row here is a direct report of the viewer.
		r["draft"] = drafts.get(r.name)
		r["overtime_allowed"] = overtime.get(r.employee, False)
		# Overtime employees may be edited freely, even without a shift.
		r["editable"] = bool(start and end) or r["overtime_allowed"]
		r["min_in_time"] = start.strftime("%Y-%m-%d %H:%M:%S") if start and not r["overtime_allowed"] else None
		r["max_out_time"] = end.strftime("%Y-%m-%d %H:%M:%S") if end and not r["overtime_allowed"] else None
		r["attendance_date"] = str(r["attendance_date"])
		r["in_time"] = str(r["in_time"]) if r["in_time"] else None
		r["out_time"] = str(r["out_time"]) if r["out_time"] else None

	# Every team member shows for every day in the range; days without a submitted
	# record get a read-only "No attendance" row (range capped at 93 days).
	days = [None]
	if from_date and to_date and (to_date - from_date).days <= 92:
		days = [from_date + timedelta(days=i) for i in range((to_date - from_date).days + 1)]
	have = {(r.employee, r.attendance_date) for r in rows}
	for e in team:
		for d in days:
			if (e.name, str(d) if d else None) in have:
				continue
			if d is None and any(r.employee == e.name for r in rows):
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
					"reports_to_name": reports_to.get(e.name) or "",
					"min_in_time": None,
					"max_out_time": None,
					"editable": False,
				}
			)

	rows.sort(key=lambda r: (r["employee"], r["attendance_date"] or ""))

	return {"can_edit": can_edit, "rows": rows, "manager": manager, "departments": departments}


@frappe.whitelist()
def save_drafts(changes):
	"""Save in/out time changes as Attendance Drafts. Each change: {name, in_time, out_time}.

	Nothing touches the Attendance until a draft is verified and submitted. All rules
	(direct manager only, shift limits, overtime exemption) live in the Attendance Draft
	doctype. An existing pending draft for the same attendance is updated, not duplicated.
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
		doc.save()
		saved.append({"attendance": doc.attendance, "draft": doc.name})

	return {"message": _("{0} draft(s) saved for verification").format(len(saved)), "saved": saved}
