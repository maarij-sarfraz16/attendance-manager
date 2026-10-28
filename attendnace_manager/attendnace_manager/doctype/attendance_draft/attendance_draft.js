frappe.ui.form.on("Attendance Draft", {
	setup(frm) {
		frm.set_query("attendance", () => ({ filters: { docstatus: 1 } }));
	},
});
