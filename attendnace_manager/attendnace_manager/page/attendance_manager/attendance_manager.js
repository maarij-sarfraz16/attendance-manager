frappe.pages["attendance_manager"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Attendance Manager"),
		single_column: true,
	});

	const API = "attendnace_manager.attendnace_manager.page.attendance_manager.attendance_manager";
	// Default view is yesterday's attendance.
	const yesterday = frappe.datetime.add_days(frappe.datetime.get_today(), -1);
	// Filters are remembered per user so a page reload keeps them until cleared.
	const STORAGE_KEY = `attendance_manager_filters:${frappe.session.user}`;
	const saved = (() => {
		try {
			return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
		} catch (e) {
			return {};
		}
	})();

	// Bootstrap's table/card defaults are hard-coded light colours; use Frappe's theme variables so
	// the tables read correctly in both light and dark mode.
	$(`<style>
		.attendance-manager .table { color: var(--text-color); background: transparent; }
		.attendance-manager .table th,
		.attendance-manager .table td { border-color: var(--table-border-color, var(--border-color)); background: transparent; vertical-align: middle; }
		.attendance-manager .table thead th,
		.attendance-manager .table tfoot td { background: var(--subtle-fg); color: var(--heading-color, var(--text-color)); }
		.attendance-manager .table tbody tr.table-warning > td { background: var(--bg-yellow); color: var(--text-on-yellow); }
		.attendance-manager .ta-total { background: var(--card-bg); color: var(--text-color); border: 1px solid var(--border-color); }
		.attendance-manager .ta-total h6 { color: var(--heading-color, var(--text-color)); }
	</style>`).appendTo(page.body);

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
	const $id = $filters.find(".ta-id").val(saved.employee || "");
	const $name = $filters.find(".ta-name").val(saved.employee_name || "");
	const $dept = $filters.find(".ta-dept");
	if (saved.department) {
		// Department options arrive with the first load; add the saved one so it survives until then.
		$("<option>").val(saved.department).text(saved.department).appendTo($dept);
		$dept.val(saved.department);
	}
	const $from = $filters.find(".ta-from").val(saved.from_date || yesterday);
	const $to = $filters.find(".ta-to").val(saved.to_date || yesterday);

	function saveFilters(filters) {
		try {
			localStorage.setItem(STORAGE_KEY, JSON.stringify(filters));
		} catch (e) {
			// Storage unavailable (private mode etc.) — filters just won't persist.
		}
	}

	page.set_primary_action(__("Load"), () => load(true));
	page.add_inner_button(__("Save as Draft"), save);
		page.add_inner_button(__("View Drafts"), () => frappe.set_route("List", "Attendance Draft"));

	const $body = $('<div class="attendance-manager mt-3"></div>').appendTo(page.body);
	let rows = [];
	let canEdit = false;
	let statuses = [];
	const dirty = {};

	// Filter as you type (debounced), like the Employee list view.
	let timer;
	let loadSeq = 0;
	// Filters of the last load; typing fires "input" and leaving the box fires "change", which must not load twice.
	let lastFilters = null;
	$filters.find("input, select").on("input change", () => {
		clearTimeout(timer);
		timer = setTimeout(load, 400);
	});

	function updateFilterLabel() {
		const n = [$id.val(), $name.val(), $dept.val()].filter((v) => v && v.trim()).length
			+ ($from.val() !== yesterday ? 1 : 0)
			+ ($to.val() !== yesterday ? 1 : 0);
		$filters.find(".filter-label").text(n ? __("{0} filters", [n]) : __("Filter"));
	}

	$filters.find(".ta-clear").on("click", () => {
		$id.val("");
		$name.val("");
		$dept.val("");
		$from.val(yesterday);
		$to.val(yesterday);
		clearTimeout(timer);
		load();
	});

	function load(force) {
		if ($from.val() && $to.val() && $from.val() > $to.val()) {
			frappe.show_alert({ message: __("From Date cannot be after To Date."), indicator: "orange" });
			return;
		}
		updateFilterLabel();
		const filters = {
			employee: $id.val().trim(),
			employee_name: $name.val().trim(),
			department: $dept.val() || "",
			from_date: $from.val(),
			to_date: $to.val(),
		};
		const key = JSON.stringify(filters);
		if (force !== true && key === lastFilters) return;
		lastFilters = key;
		saveFilters(filters);
		// Ignore replies to older requests so a slow filtered load can't overwrite a newer one (e.g. after Clear).
		const seq = ++loadSeq;
		frappe.call({
			method: `${API}.get_team_attendance`,
			args: filters,
			callback: (r) => {
				if (seq !== loadSeq) return;
				const res = r.message || {};
				rows = res.rows || [];
				canEdit = !!res.can_edit;
				statuses = res.statuses || [];
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

	const machineTime = (v) => (v ? frappe.utils.escape_html(v) : "-");
	// No hours (absent, leave, missing OUT) shows "-" rather than 0.
	const hours = (v) => (!+v ? "-" : String(+(+v).toFixed(2)));

	// The attendance's own status, when the shown status (from an edit or a pending draft) differs.
	const wasText = (st, r) => {
		const att = r.att_status || r.status;
		return st !== att ? __("was {0}", [att]) : "";
	};

	// Status, duty hours and overtime cells; a pending change is shown as "current → new".
	function setResult($tr, r, next) {
		const cell = (cur, nxt) =>
			next && String(cur) !== String(nxt)
				? `${frappe.utils.escape_html(cur)} → <b>${frappe.utils.escape_html(nxt)}</b>`
				: frappe.utils.escape_html(cur);
		const $sel = $tr.find(".t-status-sel");
		if ($sel.length) {
			const st = (next && next.status) || r.status;
			$sel.val(st);
			$tr.find(".t-status-was").text(wasText(st, r));
		} else {
			$tr.find(".t-status").html(cell(r.status || "", next && next.status));
		}
		// Hours show only the value the change would give, not "current → new".
		const hourCell = (cur, nxt) =>
			next && nxt !== cur ? `<b>${frappe.utils.escape_html(nxt)}</b>` : frappe.utils.escape_html(cur);
		$tr.find(".t-duty").html(hourCell(hours(r.duty_hours), next && hours(next.duty_hours)));
		$tr.find(".t-ot").html(hourCell(hours(r.overtime), next && hours(next.overtime)));
	}

	const draftLink = (name) =>
		name ? `<a href="/app/attendance-draft/${encodeURIComponent(name)}">${frappe.utils.escape_html(name)}</a>` : "-";

	function render(message) {
		if (!rows.length) {
			$body.html(`<p class="text-muted">${message || __("No records found.")}</p>`);
			return;
		}

		// Escaped once, not once per row.
		const option = (s, sel) =>
			`<option value="${frappe.utils.escape_html(s)}"${sel ? " selected" : ""}>${frappe.utils.escape_html(__(s))}</option>`;
		const optionHtml = Object.fromEntries(statuses.map((s) => [s, option(s, false)]));
		const optionsFor = (cur) =>
			(statuses.includes(cur) ? "" : option(cur, true)) +
			statuses.map((s) => (s === cur ? option(s, true) : optionHtml[s])).join("");

		const trs = rows
			.map((r, i) => {
				const dis = canEdit && r.editable ? "" : "disabled";
				// Status can be changed on any day with an attendance record, punched or not.
				const status = canEdit && r.name
					? `<select class="form-control input-sm t-status-sel" style="min-width:120px;">${optionsFor(r.status)}</select>
						<div class="t-status-was small text-muted">${frappe.utils.escape_html(wasText(r.status, r))}</div>`
					: frappe.utils.escape_html(r.status || "");
				return `<tr data-i="${i}">
					<td>${frappe.utils.escape_html(r.employee_name || "")}</td>
					<td>${frappe.utils.escape_html(r.employee)}</td>
					<td>${frappe.utils.escape_html(r.department || "-")}</td>
					<td>${frappe.utils.escape_html(r.reports_to_name || "-")}</td>
					<td>${r.attendance_date ? frappe.datetime.str_to_user(r.attendance_date) : "-"}</td>
					<td class="t-status text-nowrap">${status}</td>
					<td>${frappe.utils.escape_html(r.shift || "-")}</td>
					<td class="text-nowrap">${machineTime(r.machine_in)}</td>
					<td class="text-nowrap">${machineTime(r.machine_out)}</td>
					<td><input type="text" class="form-control input-sm t-in" ${dis} autocomplete="off" style="min-width:150px;"
						placeholder="YYYY-MM-DD HH:mm" title="${__("24-hour format")}" value="${toInput(r.in_time)}"></td>
					<td><input type="text" class="form-control input-sm t-out" ${dis} autocomplete="off" style="min-width:150px;"
						placeholder="YYYY-MM-DD HH:mm" title="${__("24-hour format")}" value="${toInput(r.out_time)}"></td>
					<td class="t-duty text-nowrap">${hours(r.duty_hours)}</td>
					<td class="t-ot text-nowrap">${hours(r.overtime)}</td>
					<td class="t-draft">${draftLink(r.draft)}</td>
				</tr>`;
			})
			.join("");

		const hint = canEdit
			? __("Changes are saved as drafts and only reach the Attendance once an Attendance User verifies and submits the draft. You can review, delete or cancel your drafts but not submit them. IN and OUT times can only be set between Machine In and Machine Out, whatever the shift. If there is no Machine Out, an OUT time can be added anywhere in the 24 hours after Machine In. A day with only an IN time is Absent; adding the OUT time makes it Present and works out duty hours and overtime. Status can also be picked by hand. Times on rows with no machine punch at all cannot be edited.")
			: __("View only.");

		$body.html(`
			<p class="text-muted">${hint}</p>
			<div class="table-responsive"><table class="table table-bordered">
				<thead><tr>
					<th>${__("Full Name")}</th><th>${__("ID")}</th>
					<th>${__("Department")}</th><th>${__("Reports To")}</th><th>${__("Date")}</th><th>${__("Status")}</th>
					<th>${__("Shift")}</th><th>${__("Machine In")}</th><th>${__("Machine Out")}</th><th>${__("In Time")}</th><th>${__("Out Time")}</th>
					<th>${__("Duty Hours")}</th><th>${__("Overtime")}</th><th>${__("Draft")}</th>
				</tr></thead>
				<tbody>${trs}</tbody>
			</table></div>
			${totalsHtml()}`);

		// Frappe's own calendar + time picker (24-hour, same as Datetime fields in forms). Created on
		// first focus: building one per input up front stalls the page on large date ranges.
		// Handlers live on $body (which outlives each render), so they are rebound, not stacked.
		$body.off(".ta");
		$body.on("focusin.ta", "input.t-in:not([disabled]), input.t-out:not([disabled])", function () {
			const $inp = $(this);
			if ($inp.data("datepicker")) return;
			$inp.datepicker({
				language: frappe.boot.user.language && frappe.boot.user.language.split("-")[0] || "en",
				dateFormat: "yyyy-mm-dd",
				timepicker: true,
				timeFormat: "hh:ii",
				autoClose: false,
				todayButton: new Date(),
				position: "bottom left",
				// Checked once when the picker closes, not on every slider tick.
				onSelect: (fd, d, inst) => {
					if (inst.__init_done) $inp.val(fd.replace(/\s+/g, " "));
				},
				onHide: (inst, animationCompleted) => {
					if (!animationCompleted) $inp.trigger("change");
				},
			});
			const inst = $inp.data("datepicker");
			const v = toServer($inp.val());
			if (v) inst.selectDate(new Date(v.replace(" ", "T")));
			$inp.val(toInput(v || ""));
			inst.__init_done = true;
			// The focus that created it has already passed, so open it now.
			inst.show();
		});

		// Why a changed time is not allowed, or null. Bounds come from the server (machine punches, to the second).
		function timeError(label, v, r) {
			if (!v) return __("{0} time cannot be cleared.", [label]);
			if (v < r.min_in_time)
				return r.machine_in
					? __("{0} time cannot be earlier than machine check-in {1}.", [label, r.min_in_time])
					: __("{0} time cannot be earlier than {1} (24 hours before machine check-out).", [label, r.min_in_time]);
			if (v > r.max_out_time)
				return r.machine_out
					? __("{0} time cannot be later than machine check-out {1}.", [label, r.max_out_time])
					: __("{0} time cannot be later than {1} (24 hours after machine check-in).", [label, r.max_out_time]);
			return null;
		}

		// Shows what status, duty hours and overtime the new times would give; stale replies are ignored.
		function preview($tr, r, change) {
			const token = (r._preview = (r._preview || 0) + 1);
			if (!change) return setResult($tr, r, null);
			frappe.call({
				method: `${API}.preview_hours`,
				args: { name: r.name, in_time: change.in_time, out_time: change.out_time, status: change.status },
				callback: (res) => {
					if (token === r._preview && res.message) setResult($tr, r, res.message);
				},
			});
		}

		$body.on("change.ta", "input.t-in, input.t-out, select.t-status-sel", function () {
			const $tr = $(this).closest("tr");
			const r = rows[$tr.data("i")];
			// A status picked by hand wins; otherwise it follows the times (IN only = Absent, IN + OUT = Present).
			if ($(this).hasClass("t-status-sel")) r._status = $(this).val();
			const $in = $tr.find(".t-in");
			const $out = $tr.find(".t-out");
			// The inputs show minutes only, so the originals are compared at that precision.
			const origIn = toServer(toInput(r.in_time));
			const origOut = toServer(toInput(r.out_time));
			let inV = toServer($in.val());
			let outV = toServer($out.val());

			if (inV === null || outV === null) {
				frappe.show_alert({ message: __("Use 24-hour format: YYYY-MM-DD HH:mm (e.g. 2026-10-23 13:30)."), indicator: "red" });
				if (inV === null) inV = origIn;
				if (outV === null) outV = origOut;
			}

			// Client-side check for fast feedback; the server enforces it again. A rejected value
			// falls back to the original so what is shown always matches what will be saved.
			const inErr = inV !== origIn && timeError(__("IN"), inV, r);
			if (inErr) {
				frappe.show_alert({ message: inErr, indicator: "red" });
				inV = origIn;
			}
			const outErr = outV !== origOut && timeError(__("OUT"), outV, r);
			if (outErr) {
				frappe.show_alert({ message: outErr, indicator: "red" });
				outV = origOut;
			}
			if (inV && outV && outV <= inV) {
				frappe.show_alert({ message: __("OUT time must be after IN time."), indicator: "red" });
				if ($(this).hasClass("t-in")) inV = origIn;
				else outV = origOut;
			}
			$in.val(toInput(inV));
			$out.val(toInput(outV));

			const timesSame = inV === origIn && outV === origOut;
			if (timesSame && r._status === r.status) r._status = null;
			if (timesSame && !r._status) {
				delete dirty[r.name];
				$tr.removeClass("table-warning");
				preview($tr, r, null);
			} else {
				// An untouched time is sent exactly as stored, seconds included.
				dirty[r.name] = {
					name: r.name,
					in_time: inV === origIn ? r.in_time : inV,
					out_time: outV === origOut ? r.out_time : outV,
					// A status hand-picked in the pending draft is kept unless changed here.
					status: r._status || r.draft_status || null,
				};
				$tr.addClass("table-warning");
				preview($tr, r, dirty[r.name]);
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

	// The attendance itself is unchanged until the draft is submitted, but the row now shows
	// the draft's values (as it will after a reload), so further edits compare against them.
	function markDrafted(saved) {
		saved.forEach((s) => {
			const i = rows.findIndex((r) => r.name === s.attendance);
			if (i < 0) return;
			const r = rows[i];
			const change = dirty[s.attendance] || {};
			r.att_status = r.att_status || r.status;
			r.draft = s.draft;
			r.in_time = change.in_time || null;
			r.out_time = change.out_time || null;
			r.status = s.status || r.status;
			r.draft_status = change.status || null;
			r.duty_hours = s.duty_hours;
			r.overtime = s.overtime;
			r._status = null;
			delete dirty[s.attendance];
			const $tr = $body.find(`tr[data-i="${i}"]`).removeClass("table-warning");
			$tr.find(".t-draft").html(draftLink(s.draft));
			r._preview = (r._preview || 0) + 1;
			setResult($tr, r, null);
		});
	}

	load();
};
