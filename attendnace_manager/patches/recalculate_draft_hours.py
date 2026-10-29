import frappe
from frappe.utils import get_datetime

from attendnace_manager.attendnace_manager.doctype.attendance_draft.attendance_draft import (
	_hour_fields,
	calculate_hours,
	get_attendance,
	machine_windows,
)


def execute():
	"""Work out status, duty hours and overtime on drafts saved before the draft held them.

	A submitted draft whose times are still on its Attendance also writes the hours there, since it
	was applied without them. Pending drafts are then recalculated from the attendance as it is now.
	"""
	as_dt = lambda v: get_datetime(v) if v else None
	hour_fields = _hour_fields()
	latest = {}
	for name in frappe.get_all(
		"Attendance Draft", filters={"docstatus": 1}, order_by="creation asc", pluck="name", limit_page_length=0
	):
		doc = frappe.get_doc("Attendance Draft", name)
		att = get_attendance(doc.attendance)
		if not att:
			continue
		if not doc.original_status:
			# Applied before the snapshot existed; such drafts only ever changed times, not status.
			doc.original_status = att.status
			_s, doc.original_working_hours, *hours = calculate_hours(
				doc.employee, doc.attendance_date, att.status, as_dt(doc.original_in_time),
				as_dt(doc.original_out_time), att.get("custom_late_minutes") or 0, att.status, att.shift,
			)
			for field, value in zip(("duty_hours", "overtime", "less_duty_hour"), hours):
				doc.set("original_" + field, value)
		doc.set_hours(att)
		doc.db_update()
		latest[doc.attendance] = (doc, att)

	for doc, att in latest.values():
		if (as_dt(doc.in_time), as_dt(doc.out_time)) != (as_dt(att.in_time), as_dt(att.out_time)):
			continue
		values = {"status": doc.new_status, "working_hours": doc.working_hours}
		for field, att_field in hour_fields.items():
			values[att_field] = doc.get(field)
		frappe.db.set_value("Attendance", doc.attendance, values)

	for name in frappe.get_all("Attendance Draft", filters={"docstatus": 0}, pluck="name", limit_page_length=0):
		doc = frappe.get_doc("Attendance Draft", name)
		att = get_attendance(doc.attendance)
		if not att:
			continue
		doc.set_snapshot(att)
		doc.machine_in_time, doc.machine_out_time = machine_windows([att])[doc.attendance]
		doc.set_hours(att)
		doc.db_update()
