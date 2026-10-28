frappe.pages["attendance_manager"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Attendance Manager"),
		single_column: true,
	});

	const API = "attendnace_manager.attendnace_manager.page.attendance_manager.attendance_manager";
	const today = frappe.datetime.get_today();

	const $manager = $(`<div class="ta-manager mb-2"></div>`).appendTo(page.body);
	const $filters = $(`
		<div class="standard-filter-section flex" style="gap:8px; flex-wrap:wrap; align-items:center;">
			<input type="text" class="form-control input-xs ta-id" placeholder="${__("ID")}" autocomplete="off" style="width:150px;">
			<input type="text" class="form-control input-xs ta-name" placeholder="${__("Full Name")}" autocomplete="off" style="width:200px;">
			<select class="form-control input-xs ta-dept" style="width:200px;" title="${__("Department")}">
				<option value="">${__("All Departments")}</option>
			</select>
			<input type="date" class="form-control input-xs ta-from" style="width:160px;" title="${__("From Date")}">
			<input type="date" class="form-control input-xs ta-to" style="width:160px;" title="${__("To Date")}">
			<div class="filter-selector btn-group">
				<button class="btn btn-default btn-sm ta-filter-label" style="pointer-events:none;">
					${frappe.utils.icon("filter", "sm")} <span class="filter-label">${__("Filter")}</span>
				</button>
				<button class="btn btn-default btn-sm ta-clear" title="${__("Clear Filters")}">
					${frappe.utils.icon("close", "sm")}
				</button>
			</div>
		</div>`).appendTo(page.body);
	const $id = $filters.find(".ta-id");
	const $name = $filters.find(".ta-name");
	const $dept = $filters.find(".ta-dept");
	const $from = $filters.find(".ta-from").val(frappe.datetime.month_start());
	const $to = $filters.find(".ta-to").val(today);

	page.set_primary_action(__("Load"), load);
	page.add_inner_button(__("Save as Draft"), save);
		page.add_inner_button(__("View Drafts"), () => frappe.set_route("List", "Attendance Draft"));

	const $body = $('<div class="attendance-manager mt-3"></div>').appendTo(page.body);
	let rows = [];
	let canEdit = false;
	const dirty = {};

	// Filter as you type (debounced), like the Employee list view.
	let timer;
	$filters.find("input, select").on("input change", () => {
		clearTimeout(timer);
		timer = setTimeout(load, 400);
	});

	function updateFilterLabel() {
		const n = [$id.val(), $name.val(), $dept.val()].filter((v) => v && v.trim()).length
			+ ($from.val() !== frappe.datetime.month_start() ? 1 : 0)
			+ ($to.val() !== today ? 1 : 0);
		$filters.find(".filter-label").text(n ? __("{0} filters", [n]) : __("Filter"));
	}

	$filters.find(".ta-clear").on("click", () => {
		$id.val("");
		$name.val("");
		$dept.val("");
		$from.val(frappe.datetime.month_start());
		$to.val(today);
		clearTimeout(timer);
		load();
	});

	function load() {
		if ($from.val() && $to.val() && $from.val() > $to.val()) {
			frappe.show_alert({ message: __("From Date cannot be after To Date."), indicator: "orange" });
			return;
		}
		updateFilterLabel();
		frappe.call({
			method: `${API}.get_team_attendance`,
			args: {
				employee: $id.val().trim(),
				employee_name: $name.val().trim(),
				department: $dept.val(),
				from_date: $from.val(),
				to_date: $to.val(),
			},
			callback: (r) => {
				const res = r.message || {};
				rows = res.rows || [];
				canEdit = !!res.can_edit;
				renderHeader(res);
				Object.keys(dirty).forEach((k) => delete dirty[k]);
				render(res.message);
			},
		});
	}

	function renderHeader(res) {
		if (res.manager) {
			$manager.html(`<strong>${__("Reporting to")}:</strong> ${frappe.utils.escape_html(res.manager)}`);
		}
		if (!res.departments) return;
		const cur = $dept.val();
		$dept.html(
			`<option value="">${__("All Departments")}</option>` +
				res.departments
					.map((d) => `<option value="${frappe.utils.escape_html(d)}">${frappe.utils.escape_html(d)}</option>`)
					.join("")
		);
		$dept.val(res.departments.includes(cur) ? cur : "");
	}

	// Time is edited as plain text in 24-hour format (datetime-local follows the browser locale and shows AM/PM).
	// "2026-10-23 09:05:00" -> "2026-10-23 09:05"
	const toInput = (v) => (v ? v.slice(0, 16) : "");
	// "2026-10-23 9:05" -> "2026-10-23 09:05:00"; "" for empty, null for invalid.
	const toServer = (v) => {
		v = (v || "").trim();
		if (!v) return "";
		const m = v.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})$/);
		if (!m || +m[2] > 23 || +m[3] > 59) return null;
		return `${m[1]} ${m[2].padStart(2, "0")}:${m[3]}:00`;
	};

	// Totals for whatever is currently listed: one row per department shown, plus a grand total.
	function totalsHtml() {
		const groups = {};
		rows.forEach((r) => {
			const d = r.department || __("No Department");
			const g = (groups[d] = groups[d] || { emps: new Set(), count: 0, ins: 0, outs: 0, present: 0, absent: 0 });
			g.emps.add(r.employee);
			g.count++;
			if (r.in_time) g.ins++;
			if (r.out_time) g.outs++;
			// Days with no submitted record (no document name) count as absent.
			if (r.name && r.status === "Present") g.present++;
			else if (!r.name || r.status === "Absent") g.absent++;
		});
		const names = Object.keys(groups).sort();
		const cells = (g) => `<td>${g.ins}</td><td>${g.outs}</td><td>${g.present}</td><td>${g.absent}</td>`;

		const all = { emps: new Set(), count: 0, ins: 0, outs: 0, present: 0, absent: 0 };
		names.forEach((d) => {
			const g = groups[d];
			g.emps.forEach((e) => all.emps.add(e));
			all.count += g.count;
			all.ins += g.ins;
			all.outs += g.outs;
			all.present += g.present;
			all.absent += g.absent;
		});

		const body = names
			.map(
				(d) => `<tr><td>${frappe.utils.escape_html(d)}</td><td>${groups[d].emps.size}</td>
					<td>${groups[d].count}</td>${cells(groups[d])}</tr>`
			)
			.join("");

		return `<div class="ta-total card mt-3" style="padding:12px 16px;">
			<h6 style="margin-bottom:8px;">${__("Total")}</h6>
			<div class="table-responsive"><table class="table table-bordered" style="margin-bottom:0;">
				<thead><tr>
					<th>${__("Department")}</th><th>${__("Employees")}</th><th>${__("Records")}</th>
					<th>${__("Check-ins")}</th><th>${__("Check-outs")}</th>
					<th>${__("Present")}</th><th>${__("Absent")}</th>
				</tr></thead>
				<tbody>${body}</tbody>
				<tfoot><tr style="font-weight:bold;">
					<td>${__("Total")}</td><td>${all.emps.size}</td><td>${all.count}</td>${cells(all)}
				</tr></tfoot>
			</table></div>
		</div>`;
	}

	const draftLink = (name) =>
		name ? `<a href="/app/attendance-draft/${encodeURIComponent(name)}">${frappe.utils.escape_html(name)}</a>` : "-";

	function render(message) {
		if (!rows.length) {
			$body.html(`<p class="text-muted">${message || __("No records found.")}</p>`);
			return;
		}

		const trs = rows
			.map((r, i) => {
				const dis = canEdit && r.editable ? "" : "disabled";
				return `<tr data-i="${i}">
					<td>${frappe.utils.escape_html(r.employee_name || "")}</td>
					<td>${frappe.utils.escape_html(r.employee)}</td>
					<td>${frappe.utils.escape_html(r.department || "-")}</td>
					<td>${frappe.utils.escape_html(r.reports_to_name || "-")}</td>
					<td>${r.attendance_date ? frappe.datetime.str_to_user(r.attendance_date) : "-"}</td>
					<td>${frappe.utils.escape_html(r.status || "")}</td>
					<td>${frappe.utils.escape_html(r.shift || "-")}</td>
					<td><input type="text" class="form-control input-sm t-in" ${dis} autocomplete="off" style="min-width:150px;"
						placeholder="YYYY-MM-DD HH:mm" title="${__("24-hour format")}" value="${toInput(r.in_time)}"></td>
					<td><input type="text" class="form-control input-sm t-out" ${dis} autocomplete="off" style="min-width:150px;"
						placeholder="YYYY-MM-DD HH:mm" title="${__("24-hour format")}" value="${toInput(r.out_time)}"></td>
						<td class="t-draft">${draftLink(r.draft)}</td>
				</tr>`;
			})
			.join("");

		const hint = canEdit
			? __("Changes are saved as drafts and only reach the Attendance once someone verifies and submits the draft. You can move IN time later than shift start and OUT time earlier than shift end. Employees with Allow Overtime can be edited to any time.")
			: __("View only.");

		$body.html(`
			<p class="text-muted">${hint}</p>
			<div class="table-responsive"><table class="table table-bordered">
				<thead><tr>
					<th>${__("Full Name")}</th><th>${__("ID")}</th>
					<th>${__("Department")}</th><th>${__("Reports To")}</th><th>${__("Date")}</th><th>${__("Status")}</th>
					<th>${__("Shift")}</th><th>${__("In Time")}</th><th>${__("Out Time")}</th><th>${__("Draft")}</th>
				</tr></thead>
				<tbody>${trs}</tbody>
			</table></div>
			${totalsHtml()}`);

		// Frappe's own calendar + time picker (24-hour, same as Datetime fields in forms).
		$body.find("input.t-in:not([disabled]), input.t-out:not([disabled])").each(function () {
			const $inp = $(this);
			$inp.datepicker({
				language: frappe.boot.user.language && frappe.boot.user.language.split("-")[0] || "en",
				dateFormat: "yyyy-mm-dd",
				timepicker: true,
				timeFormat: "hh:ii",
				autoClose: false,
				todayButton: new Date(),
				position: "bottom left",
				onSelect: (fd, d, inst) => {
					if (inst.__init_done) $inp.val(fd.replace(/\s+/g, " ")).trigger("change");
				},
			});
			const inst = $inp.data("datepicker");
			const v = toServer($inp.val());
			if (v) inst.selectDate(new Date(v.replace(" ", "T")));
			$inp.val(toInput(v || ""));
			inst.__init_done = true;
		});

		$body.find("input").on("change", function () {
			const $tr = $(this).closest("tr");
			const r = rows[$tr.data("i")];
			const inV = toServer($tr.find(".t-in").val());
			const outV = toServer($tr.find(".t-out").val());
			const origIn = toServer(toInput(r.in_time));
			const origOut = toServer(toInput(r.out_time));

			if (inV === null || outV === null) {
				frappe.show_alert({ message: __("Use 24-hour format: YYYY-MM-DD HH:mm (e.g. 2026-10-23 13:30)."), indicator: "red" });
				$tr.find(".t-in").val(toInput(r.in_time));
				$tr.find(".t-out").val(toInput(r.out_time));
				return;
			}
			$tr.find(".t-in").val(toInput(inV));
			$tr.find(".t-out").val(toInput(outV));

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
			method: `${API}.save_drafts`,
			args: { changes: JSON.stringify(changes) },
			freeze: true,
			callback: (r) => {
				const res = r.message || {};
				frappe.show_alert({ message: res.message, indicator: "green" });
				markDrafted(res.saved || []);
			},
		});
	}

	// The attendance itself is unchanged; just clear the edit mark and link the draft.
	function markDrafted(saved) {
		saved.forEach((s) => {
			const i = rows.findIndex((r) => r.name === s.attendance);
			if (i < 0) return;
			rows[i].draft = s.draft;
			delete dirty[s.attendance];
			$body.find(`tr[data-i="${i}"]`).removeClass("table-warning").find(".t-draft").html(draftLink(s.draft));
		});
	}

	load();
};
