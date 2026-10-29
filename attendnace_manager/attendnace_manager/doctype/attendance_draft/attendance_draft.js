frappe.ui.form.on("Attendance Draft", {
	setup(frm) {
		frm.set_query("attendance", () => ({ filters: { docstatus: 1 } }));
	},
	refresh(frm) {
		// Administrator may do everything but submit; only an Attendance User verifies.
		if (frappe.session.user === "Administrator" && frm.doc.docstatus === 0 && !frm.is_new() && !frm.is_dirty()) {
			frm.page.clear_primary_action();
		}
	},
});
