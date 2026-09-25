import {SeatMap} from "./map.js";
import {confirmTranslated, translate} from "./i18n.js";
import {dialog, downloadJSON, escape, isExcluded, numeric, seatName, uid, seatOverlap, seatVertices} from "./ui.js";

export class PlanEditor
{
    constructor(app, container, plan)
    {
        this.app = app;
        this.container = container;
        this.plan = plan;
        this.selected = new Set();
        this.undoStack = [];
        this.dirty = false;
        this.preview = null;
        this.render();
    }

    destroy()
    {
        this.map?.destroy();
    }

    checkpoint()
    {
        this.undoStack.push(JSON.stringify({seats: this.plan.seats, zones: this.plan.zones}));
        if (this.undoStack.length > 30)
        {
            this.undoStack.shift();
        }
        this.dirty = true;
    }

    render()
    {
        const plan = this.plan;
        const editable = plan.state === "draft";
        this.container.innerHTML = `<div class="editor-header"><div><p class="eyebrow">${escape(translate("PDF SEAT MAP · {0}", translate(plan.state.toUpperCase())))}</p><h2 translate="no">${escape(plan.name)}</h2><p id="plan-count" class="muted"></p></div><div class="actions"><button id="editor-duplicate" class="button secondary">Duplicate</button><button id="editor-export" class="button secondary">Export overlay</button><label class="button secondary ${editable ? "" : "disabled"}">Import overlay<input type="file" id="editor-import" accept="application/json,.json" hidden ${editable ? "" : "disabled"}></label><button id="editor-save" class="button" ${editable ? "" : "disabled"}>Save draft</button><button id="editor-publish" class="button dark" ${editable ? "" : "disabled"}>Publish</button></div></div>
        ${editable ? '<p class="notice">Draft only. Review seat identities and cross-outs before publishing. Selecting a shape does not approve it.</p>' : '<p class="notice">Published plans are immutable. Duplicate this plan to revise its overlay, or upload a new PDF. Existing events keep their current version until you explicitly switch it.</p>'}
        <div class="workspace"><section class="map-panel"><div class="tool-strip" id="editor-tools">${[["select", "Select"], ["move", "Move"], ["add", "Add seat"], ["grid", "Draw grid"], ["quadgrid", "4-corner grid"], ["corners", "Edit corners"], ["exclude", "Exclude area"], ["region", "Detection region"]].map(([tool, label]) => `<button data-tool="${tool}" ${editable || tool === "select" ? "" : "disabled"}>${label}</button>`).join("")}<button id="editor-undo" ${editable ? "" : "disabled"}>Undo</button></div><div id="editor-map"></div><div class="legend"><span class="dot review"></span>Needs review <span class="dot free"></span>Approved <span class="dot blocked"></span>Excluded <span class="dot selected"></span>Selected</div></section>
        <aside class="inspector"><details open><summary>Selection <span id="selection-count">0</span></summary><div id="selection-summary" class="selection-list muted">Click a seat, or drag to select an area.</div><form id="seat-form"><div class="two"><label>Section<input name="section" maxlength="80" placeholder="Keep existing"></label><label>Row<input name="row" maxlength="40" placeholder="Keep existing"></label></div><label>Seat label <small>(one seat only)</small><input name="label" maxlength="40" placeholder="Printed number"></label><div class="three"><label>Width, px<input type="number" name="width" min="2" step="0.1" placeholder="22"></label><label>Height, px<input type="number" name="height" min="2" step="0.1" placeholder="32"></label><label>Angle, °<input type="number" name="angle" min="-180" max="180" step="0.1" placeholder="0"></label></div><div class="two"><label>Seat availability<select name="blocked"><option value="">Keep existing</option><option value="false">Free</option><option value="true">Excluded</option></select></label><label>Review state<select name="reviewed"><option value="">Keep existing</option><option value="true">Approved</option><option value="false">Needs review</option></select></label></div><label>Internal note<textarea name="note" rows="2" maxlength="300" placeholder="Keep existing"></textarea></label><button class="button full" ${editable ? "" : "disabled"}>Apply to selection</button></form><div class="actions wrap"><button id="approve-selected" class="button secondary" ${editable ? "" : "disabled"}>Approve selected</button><button id="block-selected" class="button secondary" ${editable ? "" : "disabled"}>Exclude selected</button><button id="delete-selected" class="button danger" ${editable ? "" : "disabled"}>Delete shapes</button></div><button id="uncertain-selected" class="text-button">Select low-confidence shapes</button><button id="renumber" class="text-button" ${editable ? "" : "disabled"}>Renumber selected shapes…</button></details>
        <details open><summary>Automatic detection</summary><p class="muted small">Precision fitting uses independent quadrilateral edges, not enclosing rectangles. Deterministic pixel search and optional repeated-symbol recovery; no AI or OCR. Scans receive provisional D-labels; every candidate needs review.</p><form id="detect-form"><label>Detection method<select name="strategy"><option value="boundaries">Precision: four-border pixel fitting</option><option value="multiscale">Multi-pass: faint and rotated seats</option><option value="legacy">Legacy detector (comparison)</option></select></label><label>Section for candidates<input name="section" value="Detected" required maxlength="80"></label><div class="two"><label>Minimum side, px<input name="min_size" type="number" value="9" min="4" max="500"></label><label>Maximum side, px<input name="max_size" type="number" value="70" min="8" max="800"></label><label>Box fill ratio<input name="rectangularity" type="number" value="0.63" min="0.4" max="0.99" step="0.01"></label><label>Close kernel, px<input name="close_size" type="number" value="2" min="1" max="5"></label><label>Nearby shapes<input name="neighbours" type="number" value="2" min="0" max="8"></label></div><label class="check"><input name="recover_groups" type="checkbox" checked>Recover separate groups and faint chair pairs (slower)</label><label class="check"><input name="repair_symbols" type="checkbox" checked>Repair faint small outlines</label><div class="two"><label>Small symbol max side, px<input name="symbol_max_size" type="number" value="32" min="8" max="100"></label><label>Repair gap length, px<input name="gap_size" type="number" value="7" min="3" max="9"></label></div><label class="check"><input name="detect_crosses" type="checkbox" checked>Flag possible cross-outs</label><p id="detection-roi" class="small muted">Region: entire current page</p><button type="button" id="clear-region" class="text-button">Clear detection region</button><button class="button secondary full" ${editable ? "" : "disabled"}>Detect candidates</button></form><div id="detection-result"></div></details>
        <details><summary>Exclusion areas <span id="zone-count"></span></summary><p class="small muted">Seats whose centers fall inside an area stay unavailable, even when their individual flag is free. Remove the area to change that rule.</p><div id="zone-list"></div></details>
        <details><summary>Plan settings & review</summary><label>Plan name<input id="plan-name" value="${escape(plan.name)}" maxlength="160" ${editable ? "" : "disabled"}></label><label>Notes<textarea id="plan-notes" rows="4" maxlength="2000" ${editable ? "" : "disabled"}>${escape(plan.notes)}</textarea></label><button id="approve-all" class="button secondary full" ${editable ? "" : "disabled"}>Mark all shapes reviewed…</button><p class="small muted">Only use this after checking all pages, labels, exclusions and false detections. Publication does not automatically open event bookings.</p></details></aside></div>`;
        this.map = new SeatMap(this.container.querySelector("#editor-map"), {base: this.app.base, planId: plan.id, pages: plan.pages, seats: plan.seats, zones: plan.zones, edit: editable, onSeat: (id, event) => this.selectSeat(id, event), onDraw: (tool, bounds, event) => this.draw(tool, bounds, event), onAdd: (point) => this.addSeat(point), onMove: (id, dx, dy) => this.moveSeats(id, dx, dy), onQuad: (points, page) => this.quadGrid(points, page), onCorner: (id, corner, point) => this.editCorner(id, corner, point), onPage: () => { this.selected.clear(); this.refresh(); }});
        this.container.querySelectorAll("[data-tool]").forEach((button) => button.onclick = () =>
        {
            this.map.setTool(button.dataset.tool);
            this.container.querySelectorAll("[data-tool]").forEach((item) => item.classList.toggle("active", item === button));
        });
        this.container.querySelector('[data-tool="select"]').click();
        this.container.querySelector("#uncertain-selected").onclick = () =>
        {
            this.selected = new Set(this.plan.seats.filter((seat) => seat.page === this.map.page && seat.source.startsWith("deterministic-boundaries") && seat.score < 0.35).map((seat) => seat.id));
            this.refresh();
        };
        this.container.querySelector("#editor-undo").onclick = () => this.undo();
        this.container.querySelector("#editor-save").onclick = () => this.app.run(() => this.save());
        this.container.querySelector("#editor-publish").onclick = () => this.app.run(() => this.publish());
        this.container.querySelector("#editor-export").onclick = () => downloadJSON({format: "seatplan-overlay-v1", plan: this.plan}, `seatplan-${plan.id}.json`);
        this.container.querySelector("#editor-duplicate").onclick = () => this.app.run(async () =>
        {
            if (this.dirty && confirmTranslated("Save your changes before duplicating?") === true)
            {
                await this.save();
            }
            const response = await this.app.api(`/api/admin/plans/${plan.id}/clone`, {method: "POST"});
            this.dirty = false;
            await this.app.loadPlans(response.id);
        });
        this.container.querySelector("#editor-import").onchange = (event) => this.app.run(() => this.importOverlay(event.target.files[0]));
        this.container.querySelector("#seat-form").onsubmit = (event) => { event.preventDefault(); this.applyProperties(event.target); };
        this.container.querySelector("#approve-selected").onclick = () => this.bulk({reviewed: true});
        this.container.querySelector("#block-selected").onclick = () => this.bulk({blocked: true});
        this.container.querySelector("#delete-selected").onclick = () => this.deleteSelected();
        this.container.querySelector("#approve-all").onclick = () =>
        {
            if (confirmTranslated("I have checked EVERY shape, printed seat identity, missing seat and crossed-out area on ALL pages. Mark all candidates reviewed?") === true)
            {
                this.checkpoint();
                this.plan.seats.forEach((seat) => seat.reviewed = true);
                this.refresh();
            }
        };
        this.container.querySelector("#renumber").onclick = () => this.renumber();
        this.container.querySelector("#detect-form").onsubmit = (event) => { event.preventDefault(); this.app.run(() => this.detect(event.target)); };
        this.container.querySelector("#clear-region").onclick = () =>
        {
            this.region = null;
            this.map.options.region = null;
            this.container.querySelector("#detection-roi").textContent = "Region: entire current page";
            this.map.render();
        };
        this.container.querySelector("#plan-name").oninput = (event) => { this.plan.name = event.target.value; this.dirty = true; };
        this.container.querySelector("#plan-notes").oninput = (event) => { this.plan.notes = event.target.value; this.dirty = true; };
        this.refresh();
    }

    refresh()
    {
        const seats = this.plan.seats;
        const blocked = seats.filter((seat) => isExcluded(seat, this.plan.zones)).length;
        const pending = seats.filter((seat) => seat.reviewed === false).length;
        this.container.querySelector("#plan-count").textContent = translate("{0} shapes · {1} excluded · {2} need review · revision {3}", seats.length, blocked, pending, this.plan.revision) + (this.dirty ? ` · ${translate("unsaved changes")}` : "");
        this.container.querySelector("#selection-count").textContent = this.selected.size;
        const selection = seats.filter((seat) => this.selected.has(seat.id));
        this.container.querySelector("#selection-summary").innerHTML = selection.length ? selection.slice(0, 12).map((seat) => `<div>${escape(seatName(seat))}<small>${isExcluded(seat, this.plan.zones) ? "Excluded" : seat.reviewed ? "Approved" : "Needs review"}</small></div>`).join("") + (selection.length > 12 ? `<p>+ ${selection.length - 12} more</p>` : "") : "Click a seat, or drag to select an area.";
        const form = this.container.querySelector("#seat-form");
        form.reset();
        if (selection.length === 1)
        {
            const seat = selection[0], page = this.plan.pages[seat.page];
            for (const name of ["section", "row", "label", "note", "angle"])
            {
                form.elements[name].value = seat[name];
            }
            form.elements.width.value = (seat.w * page.width).toFixed(1);
            form.elements.height.value = (seat.h * page.height).toFixed(1);
            form.elements.blocked.value = String(seat.blocked);
            form.elements.reviewed.value = String(seat.reviewed);
        }
        this.container.querySelector("#zone-count").textContent = this.plan.zones.length;
        this.container.querySelector("#zone-list").innerHTML = this.plan.zones.map((zone) => `<div class="zone-row"><span>${escape(zone.name)}<small>Page ${zone.page + 1}</small></span>${this.plan.state === "draft" ? `<button class="text-button danger-text" data-zone="${escape(zone.id)}">Remove</button>` : ""}</div>`).join("") || '<p class="small muted">No exclusion areas. Use “Exclude area” to draw one.</p>';
        this.container.querySelectorAll("[data-zone]").forEach((button) => button.onclick = () =>
        {
            if (confirmTranslated("Remove this exclusion area? Individually blocked seats stay blocked until you change their flags.") === true)
            {
                this.checkpoint();
                this.plan.zones = this.plan.zones.filter((zone) => zone.id !== button.dataset.zone);
                this.refresh();
            }
        });
        this.map.update(this.plan.seats, this.plan.zones, this.selected);
    }

    selectSeat(id, event)
    {
        if (this.plan.seats.some((seat) => seat.id === id) === false)
        {
            return;
        }
        if (event.ctrlKey !== true && event.metaKey !== true && event.shiftKey !== true)
        {
            this.selected.clear();
        }
        if (this.selected.has(id))
        {
            this.selected.delete(id);
        }
        else
        {
            this.selected.add(id);
        }
        this.refresh();
    }

    draw(tool, bounds, event)
    {
        if (tool === "select" || tool === "move")
        {
            if (event.ctrlKey !== true && event.metaKey !== true && event.shiftKey !== true)
            {
                this.selected.clear();
            }
            this.plan.seats.filter((seat) => seat.page === bounds.page && seat.x >= bounds.x && seat.x <= bounds.x + bounds.w && seat.y >= bounds.y && seat.y <= bounds.y + bounds.h).forEach((seat) => this.selected.add(seat.id));
            this.refresh();
        }
        else if (tool === "grid")
        {
            this.gridDialog(bounds);
        }
        else if (tool === "exclude")
        {
            this.checkpoint();
            this.plan.zones.push({id: uid(), page: bounds.page, name: `Excluded area ${this.plan.zones.length + 1}`, points: [[bounds.x, bounds.y], [bounds.x + bounds.w, bounds.y], [bounds.x + bounds.w, bounds.y + bounds.h], [bounds.x, bounds.y + bounds.h]]});
            this.refresh();
        }
        else if (tool === "region")
        {
            this.region = bounds;
            this.map.options.region = bounds;
            this.container.querySelector("#detection-roi").textContent = `Region: drawn area on page ${bounds.page + 1}`;
            this.map.render();
        }
    }

    addSeat(point)
    {
        if (this.plan.state !== "draft")
        {
            return;
        }
        const form = this.container.querySelector("#seat-form"), page = this.plan.pages[point.page];
        const w = numeric(form, "width", 22) / page.width, h = numeric(form, "height", 32) / page.height;
        if (point.x - w / 2 < 0 || point.x + w / 2 > 1 || point.y - h / 2 < 0 || point.y + h / 2 > 1)
        {
            this.app.toast("The seat would extend beyond the page.", true);
            return;
        }
        this.checkpoint();
        const id = uid();
        this.plan.seats.push({id, ...point, w, h, angle: numeric(form, "angle"), section: form.elements.section.value || "Manual", row: form.elements.row.value || "", label: `M${this.plan.seats.length + 1}`, blocked: false, reviewed: false, source: "manual", note: "", score: 0});
        this.selected = new Set([id]);
        this.refresh();
    }

    moveSeats(id, dx, dy)
    {
        if (this.selected.has(id) === false)
        {
            this.selected = new Set([id]);
        }
        const selected = this.plan.seats.filter((seat) => this.selected.has(seat.id));
        if (selected.some((seat) => seat.x + dx - seat.w / 2 < 0 || seat.x + dx + seat.w / 2 > 1 || seat.y + dy - seat.h / 2 < 0 || seat.y + dy + seat.h / 2 > 1))
        {
            this.app.toast("The moved selection would extend beyond the page.", true);
            return;
        }
        this.checkpoint();
        selected.forEach((seat) => { seat.x += dx; seat.y += dy; seat.reviewed = false; });
        this.refresh();
    }

    applyProperties(form)
    {
        if (this.selected.size === 0 || this.plan.state !== "draft")
        {
            return;
        }
        this.checkpoint();
        for (const seat of this.plan.seats.filter((item) => this.selected.has(item.id)))
        {
            const page = this.plan.pages[seat.page];
            for (const field of ["section", "row", "note"])
            {
                if (form.elements[field].value !== "" || this.selected.size === 1)
                {
                    seat[field] = form.elements[field].value;
                }
            }
            if (this.selected.size === 1 && form.elements.label.value !== "")
            {
                seat.label = form.elements.label.value;
            }
            for (const [field, dimension, size] of [["width", "w", page.width], ["height", "h", page.height]])
            {
                if (form.elements[field].value !== "")
                {
                    seat[dimension] = Number(form.elements[field].value) / size;
                }
            }
            if (form.elements.angle.value !== "")
            {
                seat.angle = Number(form.elements.angle.value);
            }
            for (const field of ["blocked", "reviewed"])
            {
                if (form.elements[field].value !== "")
                {
                    seat[field] = form.elements[field].value === "true";
                }
            }
        }
        this.refresh();
    }

    bulk(properties)
    {
        if (this.selected.size === 0)
        {
            this.app.toast("Select one or more seats first.");
            return;
        }
        this.checkpoint();
        this.plan.seats.filter((seat) => this.selected.has(seat.id)).forEach((seat) => Object.assign(seat, properties));
        this.refresh();
    }

    deleteSelected()
    {
        if (this.selected.size === 0 || confirmTranslated(`Delete ${this.selected.size} selected shapes from this draft?`) === false)
        {
            return;
        }
        this.checkpoint();
        this.plan.seats = this.plan.seats.filter((seat) => this.selected.has(seat.id) === false);
        this.selected.clear();
        this.refresh();
    }

    undo()
    {
        if (this.undoStack.length === 0)
        {
            return;
        }
        Object.assign(this.plan, JSON.parse(this.undoStack.pop()));
        this.dirty = true;
        this.selected.clear();
        this.map.preview = [];
        this.refresh();
    }

    async save()
    {
        const payload = {revision: this.plan.revision, name: this.plan.name, notes: this.plan.notes, seats: this.plan.seats, zones: this.plan.zones};
        const response = await this.app.api(`/api/admin/plans/${this.plan.id}`, {method: "PUT", body: payload});
        this.plan.revision = response.revision;
        this.dirty = false;
        this.refresh();
        this.app.toast("Draft saved.");
    }

    async publish()
    {
        if (this.plan.seats.some((seat) => seat.reviewed === false))
        {
            this.app.toast("Review every candidate before publishing.", true);
            return;
        }
        if (confirmTranslated("Publish this reviewed plan? Its geometry will be frozen. You can duplicate it to make future changes.") === false)
        {
            return;
        }
        await this.save();
        await this.app.api(`/api/admin/plans/${this.plan.id}/publish`, {method: "POST", body: {revision: this.plan.revision}});
        this.dirty = false;
        await this.app.loadPlans(this.plan.id);
        this.app.toast("Plan published. Create an event to open reservations.");
    }

    async importOverlay(file)
    {
        if (file === undefined)
        {
            return;
        }
        if (file.size > 8 * 1024 * 1024)
        {
            throw new Error("Overlay file is too large.");
        }
        const data = JSON.parse(await file.text());
        if (data.format !== "seatplan-overlay-v1" || Array.isArray(data.plan?.seats) === false)
        {
            throw new Error("This is not a Seatplan overlay export.");
        }
        const source = data.plan;
        if (source.seats.length > 5000 || Array.isArray(source.pages) === false || Array.isArray(source.zones ?? []) === false || (source.zones ?? []).length > 250)
        {
            throw new Error("Invalid or oversized overlay.");
        }
        for (const seat of source.seats)
        {
            if (seat === null || ["x", "y", "w", "h", "angle", "score"].some((key) => typeof seat[key] !== "number" || Number.isFinite(seat[key]) === false) || Number.isInteger(seat.page) === false || seat.page < 0 || seat.page >= this.plan.pages.length || ["section", "row", "label", "source", "note"].some((key) => typeof seat[key] !== "string") || typeof seat.blocked !== "boolean")
            {
                throw new Error("Overlay contains an invalid seat. No changes were imported.");
            }
        }
        for (const zone of source.zones ?? [])
        {
            if (Number.isInteger(zone.page) === false || zone.page < 0 || zone.page >= this.plan.pages.length || typeof zone.name !== "string" || Array.isArray(zone.points) === false || zone.points.length < 3 || zone.points.length > 32 || zone.points.some((point) => Array.isArray(point) === false || point.length !== 2 || point.some((value) => typeof value !== "number" || Number.isFinite(value) === false || value < 0 || value > 1)))
            {
                throw new Error("Overlay contains an invalid exclusion area.");
            }
        }
        if (source.pages.length !== this.plan.pages.length)
        {
            throw new Error("The source overlay has a different page count.");
        }
        if (source.sha256 !== this.plan.sha256 && confirmTranslated("This overlay was created for a DIFFERENT PDF. Coordinates are normalized, but alignment may be wrong. Import as unreviewed shapes?") === false)
        {
            return;
        }
        if (confirmTranslated("Replace all shapes and exclusion areas in this draft with the imported overlay? This can be undone before saving.") === false)
        {
            return;
        }
        this.checkpoint();
        this.plan.seats = source.seats.map((seat) => ({...seat, id: uid(), reviewed: false}));
        this.plan.zones = (source.zones ?? []).map((zone) => ({...zone, id: uid()}));
        this.selected.clear();
        this.refresh();
    }

    quadGrid(points, page)
    {
        const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
        this.gridDialog({page, x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys), corners: points.map((p) => [p.x, p.y])});
    }

    editCorner(id, corner, point)
    {
        const seat = this.plan.seats.find((item) => item.id === id);
        if (seat === undefined || this.plan.state !== "draft")
        {
            return;
        }
        const page = this.plan.pages[seat.page];
        const points = seatVertices(seat, page).map(([x, y]) => [x / page.width, y / page.height]);
        points[corner] = [point.x, point.y];
        const valid = points.every((a, index) =>
        {
            const b = points[(index + 1) % 4], c = points[(index + 2) % 4];
            return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) > 1e-9;
        });
        if (valid === false)
        {
            this.app.toast("Corners must form a convex outline without crossing edges.", true);
            return;
        }
        const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
        const left = Math.min(...xs), top = Math.min(...ys), width = Math.max(...xs) - left, height = Math.max(...ys) - top;
        if (width > 0.5 || height > 0.5 || width < 0.0001 || height < 0.0001)
        {
            this.app.toast("The adjusted cell is too large or too small.", true);
            return;
        }
        this.checkpoint();
        Object.assign(seat, {x: left + width / 2, y: top + height / 2, w: width, h: height, angle: 0, outline: points.map(([x, y]) => [(x - left) / width - 0.5, (y - top) / height - 0.5]), reviewed: false, source: "manual-corners"});
        this.refresh();
    }

    gridDialog(bounds)
    {
        const gridHelp = `${translate(bounds.corners ? "Your four corners define a skewed grid. Shared boundaries are interpolated consistently; rotation is ignored. For curved banks use short strips or individual rows." : "The rectangle you drew defines the grid bounds.")} ${translate("Gaps are percentages of each cell's pitch. Use zero gaps to follow adjacent printed cell boundaries.")}`;
        const modal = dialog(`<h2>Construct a seat grid</h2><p class="muted">${escape(gridHelp)}</p><form id="grid-form"><div class="two"><label>Rows<input name="rows" type="number" min="1" max="100" value="5" required></label><label>Columns<input name="columns" type="number" min="1" max="150" value="10" required></label><label>Horizontal gap, %<input name="gap_x" type="number" min="0" max="90" value="15"></label><label>Vertical gap, %<input name="gap_y" type="number" min="0" max="90" value="15"></label><label>Section<input name="section" value="Main" maxlength="80" required></label><label>Rotation, °<input name="angle" type="number" min="-180" max="180" value="0" step="0.1"></label><label>Row prefix<input name="row_prefix" value="R" maxlength="20"></label><label>First row<input name="row_start" type="number" value="1" min="0"></label><label>First seat number<input name="start" type="number" value="1"></label><label>Numbering step<input name="step" type="number" value="1"></label></div><label>Numbering<select name="numbering"><option value="continuous">Continue across the grid</option><option value="per-row">Restart on each row</option></select></label><label class="check"><input type="checkbox" name="serpentine">Reverse alternate rows (serpentine)</label><p class="small muted">Use step −1, 2 or −2 for reversed or odd/even numbering. Draw irregular rows as separate grids; delete positions where aisles occur.</p><div class="actions"><button type="button" class="button secondary" data-close>Cancel</button><button class="button">Preview grid</button></div></form>`);
        modal.querySelector("#grid-form").onsubmit = (event) =>
        {
            event.preventDefault();
            this.app.run(async () =>
            {
                const form = event.target;
                const payload = {...bounds, rows: numeric(form, "rows"), columns: numeric(form, "columns"), gap_x: numeric(form, "gap_x") / 100, gap_y: numeric(form, "gap_y") / 100, section: form.elements.section.value, angle: bounds.corners ? 0 : numeric(form, "angle"), row_prefix: form.elements.row_prefix.value, row_start: numeric(form, "row_start"), start: numeric(form, "start"), step: numeric(form, "step"), numbering: form.elements.numbering.value, serpentine: form.elements.serpentine.checked};
                const result = await this.app.api(`/api/admin/plans/${this.plan.id}/grid`, {method: "POST", body: payload});
                modal.close();
                this.map.preview = result.seats;
                this.map.render();
                this.showPreview(result.seats, "Grid preview", false);
            });
        };
    }

    async detect(form)
    {
        if (this.dirty)
        {
            await this.save();
        }
        const roi = this.region?.page === this.map.page ? [this.region.x, this.region.y, this.region.w, this.region.h] : null;
        const payload = {page: this.map.page, strategy: form.elements.strategy.value, recover_groups: form.elements.recover_groups.checked, repair_symbols: form.elements.repair_symbols.checked, symbol_max_size: numeric(form, "symbol_max_size"), gap_size: numeric(form, "gap_size"), section: form.elements.section.value, min_size: numeric(form, "min_size"), max_size: numeric(form, "max_size"), rectangularity: numeric(form, "rectangularity"), close_size: numeric(form, "close_size"), neighbours: numeric(form, "neighbours"), detect_crosses: form.elements.detect_crosses.checked, roi};
        const resultBox = this.container.querySelector("#detection-result");
        resultBox.innerHTML = '<p class="notice">Detecting… The worker must be running.</p>';
        const response = await this.app.api(`/api/admin/plans/${this.plan.id}/detect`, {method: "POST", body: payload});
        const job = await this.app.waitJob(response.job_id);
        if (this.container.isConnected === false)
        {
            return;
        }
        this.map.preview = job.result.seats;
        this.map.render();
        this.showPreview(job.result.seats, `${job.result.report.candidates} candidates · ${job.result.report.blocked_candidates} flagged/excluded`, true);
    }

    showPreview(seats, title, detection)
    {
        const box = this.container.querySelector("#detection-result");
        box.innerHTML = `<div class="notice"><strong>${escape(title)}</strong><p class="small">Preview only. ${detection ? "D-labels are provisional. Detection can still miss or split seats; a shape count is not a completeness check. Nothing is approved automatically." : "New grid seats will require review. This is administrator-constrained reconstruction, not automatic seat detection."}</p><div class="actions wrap"><button id="accept-preview" class="button small">Add non-overlapping</button><button id="discard-preview" class="button secondary small">Discard</button>${detection ? '<button id="replace-preview" class="text-button">Replace current page shapes…</button>' : ""}</div></div>`;
        box.querySelector("#discard-preview").onclick = () => { this.map.preview = []; this.map.render(); box.innerHTML = ""; };
        const apply = (replace) =>
        {
            if (replace && confirmTranslated("Replace all current-page seat shapes with these unreviewed candidates? Exclusion areas are retained.") === false)
            {
                return;
            }
            this.checkpoint();
            if (replace)
            {
                this.plan.seats = this.plan.seats.filter((seat) => seat.page !== this.map.page);
            }
            let added = 0;
            const labels = new Set(this.plan.seats.map((seat) => `${seat.section}\u0000${seat.row}\u0000${seat.label}`));
            for (const candidate of seats)
            {
                const exists = this.plan.seats.some((seat) => seat.id === candidate.id || (seat.page === candidate.page && seatOverlap(seat, candidate, this.plan.pages[seat.page]) > 0.65));
                if (exists === false)
                {
                    const seat = {...candidate, id: uid(), reviewed: false};
                    if (/^D\d+$/.test(seat.label))
                    {
                        let number = Number(seat.label.slice(1));
                        while (labels.has(`${seat.section}\u0000${seat.row}\u0000${seat.label}`))
                        {
                            seat.label = `D${String(++number).padStart(4, "0")}`;
                        }
                    }
                    labels.add(`${seat.section}\u0000${seat.row}\u0000${seat.label}`);
                    this.plan.seats.push(seat);
                    added++;
                }
            }
            this.map.preview = [];
            this.selected.clear();
            this.refresh();
            box.innerHTML = `<p class="small muted">Added ${added} shapes. Save the draft when ready.</p>`;
        };
        box.querySelector("#accept-preview").onclick = () => apply(false);
        box.querySelector("#replace-preview")?.addEventListener("click", () => apply(true));
        box.scrollIntoView({block: "nearest", behavior: "smooth"});
    }

    renumber()
    {
        if (this.selected.size === 0)
        {
            this.app.toast("Select the row or shapes to number first.");
            return;
        }
        const modal = dialog(`<h2>Renumber ${this.selected.size} selected shapes</h2><p class="small muted">Use this on one row at a time, or choose a sorting direction explicitly.</p><form id="renumber-form"><div class="two"><label>First number<input name="start" type="number" value="1" required></label><label>Step<input name="step" type="number" value="1" required></label><label>Section<input name="section" placeholder="Keep existing"></label><label>Row<input name="row" placeholder="Keep existing"></label></div><label>Sort order<select name="sort"><option value="x">Left to right</option><option value="y">Top to bottom</option></select></label><div class="actions"><button type="button" class="button secondary" data-close>Cancel</button><button class="button">Apply numbering</button></div></form>`);
        modal.querySelector("form").onsubmit = (event) =>
        {
            event.preventDefault();
            const form = event.target;
            if (numeric(form, "step") === 0)
            {
                return;
            }
            this.checkpoint();
            const selection = this.plan.seats.filter((seat) => this.selected.has(seat.id));
            selection.sort((a, b) => a.page - b.page || a[form.elements.sort.value] - b[form.elements.sort.value] || a.id.localeCompare(b.id));
            selection.forEach((seat, index) =>
            {
                seat.label = String(numeric(form, "start") + index * numeric(form, "step"));
                if (form.elements.section.value)
                {
                    seat.section = form.elements.section.value;
                }
                if (form.elements.row.value)
                {
                    seat.row = form.elements.row.value;
                }
                seat.reviewed = false;
            });
            modal.close();
            this.refresh();
        };
    }
}
