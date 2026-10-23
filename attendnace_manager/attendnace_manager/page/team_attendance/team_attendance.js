frappe.pages["team_attendance"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Team Attendance"),
		single_column: true,
	});

	const API = "attendnace_manager.attendnace_manager.page.team_attendance.team_attendance";
	const today = frappe.datetime.get_today();

	const f_id = page.add_field({ fieldname: "employee", label: __("Employee ID"), fieldtype: "Data" });
	const f_name = page.add_field({ fieldname: "employee_name", label: __("Employee Name"), fieldtype: "Data" });
	const f_from = page.add_field({ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.month_start() });
	const f_to = page.add_field({ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: today });

	page.set_primary_action(__("Load"), load);
	page.add_inner_button(__("Save Changes"), save);

	const $body = $('<div class="team-attendance mt-3"></div>').appendTo(page.body);
	let rows = [];
	let canEdit = false;
	const dirty = {};

	[f_id, f_name].forEach((f) =>
		f.$input.on("keydown", (e) => e.key === "Enter" && load())
	);

	function load() {
		frappe.call({
			method: `${API}.get_team_attendance`,
			args: {
				employee: f_id.get_value(),
				employee_name: f_name.get_value(),
				from_date: f_from.get_value(),
				to_date: f_to.get_value(),
			},
			freeze: true,
			callback: (r) => {
				const res = r.message || {};
				rows = res.rows || [];
				canEdit = !!res.can_edit;
				Object.keys(dirty).forEach((k) => delete dirty[k]);
				render(res.message);
			},
		});
	}

	// "2026-10-23 09:05:00" -> "2026-10-23T09:05" for <input type=datetime-local>
	const toInput = (v) => (v ? v.replace(" ", "T").slice(0, 16) : "");
	const toServer = (v) => (v ? v.replace("T", " ") + ":00" : "");

	function render(message) {
		if (!rows.length) {
			$body.html(`<p class="text-muted">${message || __("No records found.")}</p>`);
			return;
		}

		const trs = rows
			.map((r, i) => {
				const dis = canEdit && r.min_in_time ? "" : "disabled";
				return `<tr data-i="${i}">
					<td>${frappe.utils.escape_html(r.employee)}</td>
					<td>${frappe.utils.escape_html(r.employee_name || "")}</td>
					<td>${frappe.utils.escape_html(r.department || "")}</td>
					<td>${frappe.utils.escape_html(r.reports_to_name || "-")}</td>
					<td>${frappe.datetime.str_to_user(r.attendance_date)}</td>
					<td>${frappe.utils.escape_html(r.status || "")}</td>
					<td>${frappe.utils.escape_html(r.shift || "-")}</td>
					<td><input type="datetime-local" class="form-control input-sm t-in" ${dis}
						value="${toInput(r.in_time)}" min="${toInput(r.min_in_time)}"></td>
					<td><input type="datetime-local" class="form-control input-sm t-out" ${dis}
						value="${toInput(r.out_time)}" max="${toInput(r.max_out_time)}"></td>
				</tr>`;
			})
			.join("");

		const hint = canEdit
			? __("You can move IN time later than shift start and OUT time earlier than shift end.")
			: __("View only.");

		$body.html(`
			<p class="text-muted">${hint}</p>
			<div class="table-responsive"><table class="table table-bordered">
				<thead><tr>
					<th>${__("ID")}</th><th>${__("Name")}</th><th>${__("Department")}</th>
					<th>${__("Reports To")}</th><th>${__("Date")}</th><th>${__("Status")}</th>
					<th>${__("Shift")}</th><th>${__("In Time")}</th><th>${__("Out Time")}</th>
				</tr></thead>
				<tbody>${trs}</tbody>
			</table></div>`);

		$body.find("input").on("change", function () {
			const $tr = $(this).closest("tr");
			const r = rows[$tr.data("i")];
			const inV = toServer($tr.find(".t-in").val());
			const outV = toServer($tr.find(".t-out").val());
			const origIn = toInput(r.in_time) ? toServer(toInput(r.in_time)) : "";
			const origOut = toInput(r.out_time) ? toServer(toInput(r.out_time)) : "";

			// Client-side check for fast feedback; the server enforces it again.
			if (inV && r.min_in_time && inV < r.min_in_time.slice(0, 16) + ":00") {
				frappe.show_alert({ message: __("IN time cannot be earlier than shift start."), indicator: "red" });
				$tr.find(".t-in").val(toInput(r.in_time));
				return;
			}
			if (outV && r.max_out_time && outV > r.max_out_time.slice(0, 16) + ":00") {
				frappe.show_alert({ message: __("OUT time cannot be later than shift end."), indicator: "red" });
				$tr.find(".t-out").val(toInput(r.out_time));
				return;
			}

			if (inV === origIn && outV === origOut) {
				delete dirty[r.name];
				$tr.removeClass("table-warning");
			} else {
				dirty[r.name] = { name: r.name, in_time: inV, out_time: outV };
				$tr.addClass("table-warning");
			}
		});
	}

	function save() {
		const changes = Object.values(dirty);
		if (!changes.length) {
			frappe.show_alert({ message: __("No changes to save."), indicator: "orange" });
			return;
		}
		frappe.call({
			method: `${API}.update_times`,
			args: { changes: JSON.stringify(changes) },
			freeze: true,
			callback: (r) => {
				frappe.show_alert({ message: r.message, indicator: "green" });
				load();
			},
		});
	}

	load();
};
