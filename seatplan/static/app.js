import {PlanEditor} from "./editor.js";
import {SeatMap} from "./map.js";
import {dialog, escape, seatName, uid} from "./ui.js";

class SeatplanApp
{
    constructor()
    {
        this.base = document.body.dataset.base;
        this.root = document.querySelector("#app");
        this.selected = new Set();
        this.view = "book";
        this.eventId = new URLSearchParams(location.search).get("event");
        document.querySelectorAll("[data-view]").forEach((button) => button.onclick = () => this.run(() => this.navigate(button.dataset.view)));
        document.querySelector("#account-button").onclick = () => this.run(async () =>
        {
            if (this.me.email)
            {
                await this.api("/api/auth/logout", {method: "POST"});
                this.me = await this.api("/api/me");
                this.account();
                this.selected.clear();
                await this.navigate("book");
            }
            else
            {
                this.signIn();
            }
        });
        window.addEventListener("beforeunload", (event) =>
        {
            if (this.editor?.dirty)
            {
                event.preventDefault();
                event.returnValue = "";
            }
        });
        document.addEventListener("keydown", (event) =>
        {
            if (this.editor && this.editor.plan.state === "draft" && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName) === false && document.querySelector("#modal").open === false)
            {
                if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z")
                {
                    event.preventDefault();
                    this.editor.undo();
                }
                else if (event.key === "Delete")
                {
                    this.editor.deleteSelected();
                }
            }
        });
    }

    async api(path, options = {})
    {
        const headers = {...(options.headers ?? {})};
        const method = options.method ?? "GET";
        if (method !== "GET")
        {
            headers["X-CSRF-Token"] = this.me?.csrf ?? "";
        }
        let body;
        if (options.raw)
        {
            body = options.raw;
            headers["Content-Type"] = "application/pdf";
        }
        else if (options.body !== undefined)
        {
            body = JSON.stringify(options.body);
            headers["Content-Type"] = "application/json";
        }
        const response = await fetch(this.base + path, {method, headers, body, credentials: "same-origin", cache: "no-store"});
        let data;
        try
        {
            data = await response.json();
        }
        catch
        {
            throw new Error(`Server returned ${response.status}. Check the reverse proxy and application logs.`);
        }
        if (response.ok === false)
        {
            let detail = data.detail ?? `Request failed (${response.status}).`;
            if (Array.isArray(detail))
            {
                detail = detail.slice(0, 5).map((item) => `${item.loc?.join(" / ")}: ${item.msg}`).join("; ");
            }
            const error = new Error(String(detail));
            error.status = response.status;
            throw error;
        }
        return data;
    }

    async run(callback)
    {
        try
        {
            await callback();
        }
        catch (error)
        {
            this.toast(error.message || "Operation failed.", true);
            console.error(error);
        }
    }

    toast(message, error = false)
    {
        const toast = document.querySelector("#toast");
        toast.textContent = message;
        toast.hidden = false;
        toast.classList.toggle("error", error);
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(() => toast.hidden = true, error ? 14000 : 6000);
    }

    account()
    {
        document.querySelector("#account-email").textContent = this.me.email ?? "";
        document.querySelector("#account-button").textContent = this.me.email ? "Sign out" : "Sign in";
        document.querySelector("#admin-nav").hidden = this.me.admin !== true;
        document.querySelector("#development").hidden = this.me.development !== true;
    }

    async start()
    {
        this.me = await this.api("/api/me");
        this.account();
        await this.navigate("book");
        if (location.hash.startsWith("#confirm="))
        {
            const token = location.hash.slice("#confirm=".length);
            history.replaceState(null, "", location.pathname + location.search);
            const modal = dialog('<p class="eyebrow">EMAIL VERIFICATION</p><h2>Confirm your sign-in</h2><p>This signs you in to Seatplan using the address that received the email. Only continue if you requested this link.</p><div class="actions"><button class="button secondary" data-close>Cancel</button><button id="confirm-signin" class="button">Confirm sign-in</button></div>');
            modal.querySelector("#confirm-signin").onclick = () => this.run(async () =>
            {
                const button = modal.querySelector("#confirm-signin");
                button.disabled = true;
                try
                {
                    await this.api("/api/auth/confirm", {method: "POST", body: {token}});
                    this.me = await this.api("/api/me");
                    this.account();
                    modal.close();
                    await this.navigate("book");
                    this.toast("Email confirmed. You can now book seats.");
                }
                finally
                {
                    button.disabled = false;
                }
            });
        }
    }

    signIn()
    {
        const modal = dialog('<p class="eyebrow">NO PASSWORD NEEDED</p><h2>Sign in with your email</h2><p>We will email a one-time confirmation link. Once confirmed, you can book seats and manage your reservations.</p><form id="signin-form"><label>Email address<input name="email" type="email" autocomplete="email" maxlength="254" required placeholder="you@example.com"></label><div id="signin-message"></div><div class="actions"><button type="button" class="button secondary" data-close>Close</button><button class="button">Send sign-in link</button></div></form>');
        modal.querySelector("form").onsubmit = (event) =>
        {
            event.preventDefault();
            this.run(async () =>
            {
                const form = event.target, button = form.querySelector('button[type="submit"], button:not([type])');
                button.disabled = true;
                try
                {
                    const result = await this.api("/api/auth/request", {method: "POST", body: {email: form.elements.email.value}});
                    modal.querySelector("#signin-message").innerHTML = `<p class="notice">${escape(result.message)} The link expires in 15 minutes.</p>`;
                }
                finally
                {
                    button.disabled = false;
                }
            });
        };
    }

    async navigate(view)
    {
        if (this.editor?.dirty && confirm("Leave this editor and discard unsaved changes? Export or save the draft first to keep them.") === false)
        {
            return;
        }
        clearInterval(this.pollTimer);
        this.editor?.destroy();
        this.map?.destroy();
        this.editor = null;
        this.map = null;
        this.view = view;
        document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
        if (view === "admin")
        {
            if (this.me.admin !== true)
            {
                this.signIn();
                return;
            }
            await this.showAdmin("plans");
        }
        else if (view === "mine")
        {
            await this.showMine();
        }
        else
        {
            await this.showBook();
        }
    }

    async showBook()
    {
        const data = await this.api("/api/events");
        if (data.events.length === 0)
        {
            this.root.innerHTML = `<div class="page-heading"><p class="eyebrow">RESERVATIONS</p><h1>A place for everyone.</h1><p class="muted">Event seating, without the paperwork.</p></div><div class="empty"><h2>No events have been created yet.</h2><p>${this.me.admin ? "Upload and review a seating plan in Administration, then create an event." : "Please check back when the organizer opens an event."}</p></div>`;
            return;
        }
        if (data.events.some((event) => event.id === this.eventId) === false)
        {
            this.eventId = data.events[0].id;
        }
        this.root.innerHTML = `<div class="page-heading split"><div><p class="eyebrow">MAKE YOURSELF AT HOME</p><h1 id="event-title">Choose your seats</h1><p id="event-date" class="muted"></p></div><label class="event-picker">Event<select id="event-select">${data.events.map((event) => `<option value="${event.id}">${escape(event.title)}${event.status === "closed" ? " · closed" : ""}</option>`).join("")}</select></label></div><p id="event-description" class="event-description"></p><div class="workspace booking-workspace"><section class="map-panel"><div id="public-map"></div><div class="legend"><span class="dot free"></span>Available <span class="dot reserved"></span>Reserved <span class="dot blocked"></span>Unavailable <span class="dot selected"></span>Your selection</div></section><aside class="inspector booking-inspector"><h2>Your selection</h2><p id="booking-rule" class="small muted"></p><div id="chosen-seats" class="chosen-seats"></div><button id="book-button" class="button full">Reserve selected seats</button><p class="small muted">Seats are assigned when you press Reserve, not while browsing. A confirmation email follows a successful reservation.</p><hr><h3>Find a seat</h3><label class="sr-only" for="seat-search">Search seat, row or section</label><input id="seat-search" placeholder="Search seat, row or section"><label class="sr-only" for="section-filter">Section</label><select id="section-filter"><option value="">All sections</option></select><label class="check"><input id="available-filter" type="checkbox" checked>Available seats only</label><div id="seat-results" class="seat-results"></div></aside></div>`;
        const eventSelect = this.root.querySelector("#event-select");
        eventSelect.value = this.eventId;
        eventSelect.onchange = () => this.run(async () =>
        {
            this.eventId = eventSelect.value;
            this.selected.clear();
            this.map?.destroy();
            this.map = null;
            await this.refreshPublic();
        });
        this.root.querySelector("#seat-search").oninput = () => this.seatResults();
        this.root.querySelector("#section-filter").onchange = () => this.seatResults();
        this.root.querySelector("#available-filter").onchange = () => this.seatResults();
        this.root.querySelector("#book-button").onclick = () => this.run(() => this.makeBooking());
        await this.refreshPublic();
        this.pollTimer = setInterval(() =>
        {
            if (document.hidden === false && this.view === "book")
            {
                this.refreshPublic().catch(() => {});
            }
        }, 7000);
    }

    async refreshPublic()
    {
        const expectedId = this.eventId;
        const snapshot = await this.api(`/api/events/${expectedId}`);
        if (this.view !== "book" || expectedId !== this.eventId || this.root.querySelector("#public-map") === null)
        {
            return;
        }
        const oldPlan = this.snapshot?.plan.id;
        this.snapshot = snapshot;
        document.querySelector("#event-title").textContent = snapshot.event.title;
        document.querySelector("#event-date").textContent = `${snapshot.event.starts_at}${snapshot.event.status === "closed" ? " · Reservations closed" : ""}`;
        document.querySelector("#event-description").textContent = snapshot.event.description;
        const valid = new Set(snapshot.seats.filter((seat) => seat.status === "free").map((seat) => seat.id));
        const previous = this.selected.size;
        this.selected = new Set([...this.selected].filter((id) => valid.has(id)));
        if (this.selected.size < previous)
        {
            this.toast("Some selected seats are no longer available. Your selection was updated.", true);
        }
        if (this.map === null || oldPlan !== snapshot.plan.id)
        {
            this.map?.destroy();
            this.map = new SeatMap(this.root.querySelector("#public-map"), {base: this.base, planId: snapshot.plan.id, pages: snapshot.plan.pages, seats: snapshot.seats, onSeat: (id) => this.togglePublicSeat(id)});
        }
        this.map.update(snapshot.seats, [], this.selected);
        const sectionFilter = document.querySelector("#section-filter"), previousSection = sectionFilter.value;
        sectionFilter.innerHTML = '<option value="">All sections</option>' + [...new Set(snapshot.seats.map((seat) => seat.section))].sort().map((section) => `<option value="${escape(section)}">${escape(section)}</option>`).join("");
        sectionFilter.value = previousSection;
        this.chosenSeats();
        this.seatResults();
    }

    togglePublicSeat(id)
    {
        const seat = this.snapshot.seats.find((item) => item.id === id);
        if (seat === undefined || seat.status !== "free")
        {
            return;
        }
        if (this.selected.has(id))
        {
            this.selected.delete(id);
        }
        else
        {
            this.selected.add(id);
        }
        this.map.update(this.snapshot.seats, [], this.selected);
        this.chosenSeats();
        this.seatResults();
    }

    chosenSeats()
    {
        const event = this.snapshot.event;
        const seats = this.snapshot.seats.filter((seat) => this.selected.has(seat.id));
        const container = this.root.querySelector("#chosen-seats");
        container.innerHTML = seats.length ? seats.map((seat) => `<button class="selected-chip" data-remove="${seat.id}">${escape(seatName(seat))}<span aria-hidden="true">×</span></button>`).join("") : '<p class="muted">Click an available seat on the plan, or use the search below.</p>';
        container.querySelectorAll("[data-remove]").forEach((button) => button.onclick = () => this.togglePublicSeat(button.dataset.remove));
        this.root.querySelector("#booking-rule").textContent = event.max_per_user > 0 ? `Up to ${event.max_per_user} seats per email for this event.` : "Multiple seats can be reserved with one email address.";
        const button = this.root.querySelector("#book-button");
        button.textContent = this.me.email ? `Reserve ${seats.length || "selected"} seat${seats.length === 1 ? "" : "s"}` : "Sign in to reserve";
        button.disabled = event.status !== "open" || (this.me.email && seats.length === 0);
    }

    seatResults()
    {
        const search = this.root.querySelector("#seat-search").value.trim().toLowerCase();
        const section = this.root.querySelector("#section-filter").value;
        const onlyFree = this.root.querySelector("#available-filter").checked;
        const seats = this.snapshot.seats.filter((seat) => (section === "" || seat.section === section) && (onlyFree === false || seat.status === "free") && seatName(seat).toLowerCase().includes(search));
        const container = this.root.querySelector("#seat-results");
        container.innerHTML = seats.slice(0, 60).map((seat) => `<button class="seat-result ${this.selected.has(seat.id) ? "chosen" : ""}" data-seat="${seat.id}" ${seat.status !== "free" ? "disabled" : ""}><span>${escape(seatName(seat))}</span><small>${seat.mine ? "Yours" : escape(seat.status)}</small></button>`).join("") + `<p class="small muted">${seats.length > 60 ? `Showing 60 of ${seats.length}. Refine your search.` : `${seats.length} matching seats.`}</p>`;
        container.querySelectorAll("[data-seat]").forEach((button) => button.onclick = () => this.togglePublicSeat(button.dataset.seat));
    }

    async makeBooking()
    {
        if (this.me.email === null)
        {
            this.signIn();
            return;
        }
        if (this.selected.size === 0)
        {
            return;
        }
        const seats = [...this.selected].sort();
        if (seats.length > 100)
        {
            throw new Error("Reserve at most 100 seats per request.");
        }
        const fingerprint = `${this.eventId}:${seats.join(",")}`;
        if (this.pendingBooking?.fingerprint !== fingerprint)
        {
            this.pendingBooking = {fingerprint, key: uid()};
        }
        const button = this.root.querySelector("#book-button");
        button.disabled = true;
        try
        {
            const response = await this.api(`/api/events/${this.eventId}/book`, {method: "POST", body: {seats, request_key: this.pendingBooking.key}});
            this.selected.clear();
            this.pendingBooking = null;
            await this.refreshPublic();
            this.toast(`Reservation confirmed. Reference ${response.id}. Your email is queued for delivery.`);
        }
        catch (error)
        {
            await this.refreshPublic();
            throw error;
        }
        finally
        {
            this.chosenSeats();
        }
    }

    async showMine()
    {
        if (this.me.email === null)
        {
            this.root.innerHTML = '<div class="page-heading"><h1>My reservations</h1><p class="muted">Sign in with the same email address you used to reserve.</p></div><button id="mine-login" class="button">Sign in</button>';
            this.root.querySelector("#mine-login").onclick = () => this.signIn();
            return;
        }
        const data = await this.api("/api/bookings");
        this.root.innerHTML = `<div class="page-heading"><p class="eyebrow">YOUR PLACES</p><h1>My reservations</h1><p class="muted">${escape(this.me.email)}</p></div><div class="reservation-grid">${data.bookings.map((booking) => `<article class="reservation-card"><p class="eyebrow">${booking.seats.length ? `${booking.seats.length} RESERVED` : "NO ACTIVE SEATS"}</p><h2>${escape(booking.title)}</h2><p class="muted">${escape(booking.starts_at)}</p><div class="reservation-seats">${booking.seats.map((seat) => `<div><span>${escape(seatName(seat))}</span><button class="text-button danger-text" data-cancel="${booking.id}" data-seat="${seat.id}">Cancel seat</button></div>`).join("")}</div><p class="reference">Reference: ${escape(booking.id)}</p>${booking.seats.length ? `<button class="button secondary" data-cancel="${booking.id}">Cancel all these seats</button>` : ""}</article>`).join("") || '<div class="empty">You have no reservations yet.</div>'}</div>`;
        this.root.querySelectorAll("[data-cancel]").forEach((button) => button.onclick = () => this.run(async () =>
        {
            if (confirm("Cancel the selected reservation seats? They will immediately become available to others.") === false)
            {
                return;
            }
            await this.api(`/api/bookings/${button.dataset.cancel}/cancel`, {method: "POST", body: {seats: button.dataset.seat ? [button.dataset.seat] : []}});
            await this.showMine();
            this.toast("Reservation updated. An email notification has been queued.");
        }));
    }

    async showAdmin(tab)
    {
        if (this.editor?.dirty && confirm("Discard unsaved map changes?") === false)
        {
            return;
        }
        this.editor?.destroy();
        this.map?.destroy();
        this.editor = null;
        this.map = null;
        this.root.innerHTML = `<div class="admin-heading"><div><p class="eyebrow">ORGANIZER WORKSPACE</p><h1>Administration</h1></div><div class="tabs">${[["plans", "Seat plans"], ["live", "Events & seating"], ["system", "Delivery & audit"]].map(([key, title]) => `<button class="${tab === key ? "active" : ""}" data-admin-tab="${key}">${title}</button>`).join("")}</div></div><div id="admin-content"></div>`;
        this.root.querySelectorAll("[data-admin-tab]").forEach((button) => button.onclick = () => this.run(() => this.showAdmin(button.dataset.adminTab)));
        if (tab === "plans")
        {
            await this.loadPlans();
        }
        else if (tab === "live")
        {
            await this.showLive();
        }
        else
        {
            await this.showSystem();
        }
    }

    async loadPlans(selectedId)
    {
        if (this.editor?.dirty && confirm("Discard unsaved map changes before refreshing the plan list?") === false)
        {
            return;
        }
        const data = await this.api("/api/admin/plans");
        this.editor?.destroy();
        this.editor = null;
        const content = this.root.querySelector("#admin-content");
        content.innerHTML = `<div class="admin-toolbar"><label>Seat plan<select id="plan-select"><option value="">Select a plan</option>${data.plans.map((plan) => `<option value="${plan.id}">${escape(plan.name)} · ${escape(plan.state)} · ${plan.summary.total} shapes</option>`).join("")}</select></label><button id="upload-pdf" class="button">Upload a PDF</button><button id="refresh-plans" class="button secondary">Refresh</button></div><div id="plan-editor"></div>`;
        content.querySelector("#upload-pdf").onclick = () => this.uploadDialog();
        content.querySelector("#refresh-plans").onclick = () => this.run(() => this.loadPlans(content.querySelector("#plan-select").value));
        const selector = content.querySelector("#plan-select");
        selector.value = selectedId ?? data.plans[0]?.id ?? "";
        selector.onchange = () => this.run(async () =>
        {
            if (this.editor?.dirty && confirm("Discard unsaved changes to this plan?") === false)
            {
                selector.value = this.editor.plan.id;
                return;
            }
            await this.openPlan(selector.value);
        });
        await this.openPlan(selector.value);
    }

    async openPlan(id)
    {
        this.editor?.destroy();
        this.editor = null;
        const target = this.root.querySelector("#plan-editor");
        if (id === "")
        {
            target.innerHTML = '<div class="empty"><h2>Start with your PDF.</h2><p>Upload a plan, detect seat shapes or draw grids, review the exclusions, then publish.</p></div>';
            return;
        }
        const plan = await this.api(`/api/admin/plans/${id}`);
        if (["draft", "published"].includes(plan.state) === false)
        {
            target.innerHTML = `<div class="empty"><h2>${plan.state === "failed" ? "PDF processing failed" : "PDF processing is queued"}</h2><p>${escape(plan.notes || "Keep the worker running, then press Refresh. The PDF is rendered in a resource-limited subprocess.")}</p></div>`;
            return;
        }
        this.editor = new PlanEditor(this, target, plan);
    }

    uploadDialog()
    {
        if (this.editor?.dirty && confirm("Save or export first to keep your edits. Continue and discard unsaved map changes?") === false)
        {
            return;
        }
        const modal = dialog('<h2>Upload a new seating plan</h2><p class="muted">This creates a new draft. It will not change any existing event or reservation.</p><form id="upload-form"><label>Plan name<input name="name" required maxlength="160" placeholder="Main hall · September layout"></label><label>PDF file<input name="pdf" type="file" accept="application/pdf,.pdf" required></label><p class="small muted">Default limit: 20 MB, 10 pages. PDFs are rasterized on the server; the browser receives only page images.</p><div id="upload-status"></div><div class="actions"><button type="button" class="button secondary" data-close>Cancel</button><button class="button">Upload and render</button></div></form>');
        modal.querySelector("form").onsubmit = (event) =>
        {
            event.preventDefault();
            this.run(async () =>
            {
                const form = event.target, button = form.querySelector('button:not([type])');
                button.disabled = true;
                try
                {
                    const response = await this.api(`/api/admin/plans/upload?name=${encodeURIComponent(form.elements.name.value)}`, {method: "POST", raw: form.elements.pdf.files[0]});
                    modal.querySelector("#upload-status").innerHTML = '<p class="notice">PDF uploaded. Waiting for the rendering worker…</p>';
                    await this.waitJob(response.job_id);
                    modal.close();
                    await this.loadPlans(response.id);
                    this.toast("PDF rendered. Add or detect seats in the draft editor.");
                }
                finally
                {
                    button.disabled = false;
                }
            });
        };
    }

    async waitJob(id)
    {
        for (let attempt = 0; attempt < 180; attempt++)
        {
            const job = await this.api(`/api/admin/jobs/${id}`);
            if (job.status === "failed")
            {
                throw new Error(job.error || "PDF processing failed.");
            }
            if (job.status === "done")
            {
                return job;
            }
            await new Promise((resolve) => setTimeout(resolve, 1000));
        }
        throw new Error("The job is still queued or running. Check Delivery & audit and make sure the worker is running, then refresh.");
    }

    async showLive()
    {
        const data = await this.api("/api/events");
        const content = this.root.querySelector("#admin-content");
        content.innerHTML = `<div class="admin-toolbar"><label>Event<select id="live-event"><option value="">Select an event</option>${data.events.map((event) => `<option value="${event.id}">${escape(event.title)} · ${event.status}</option>`).join("")}</select></label><button id="new-event" class="button">Create event</button><button id="edit-event" class="button secondary">Event settings</button><button id="refresh-live" class="button secondary">Refresh live view</button></div><div id="live-content"></div>`;
        const selector = content.querySelector("#live-event");
        selector.value = data.events.some((event) => event.id === this.liveId) ? this.liveId : data.events[0]?.id ?? "";
        selector.onchange = () => this.run(() => this.loadLive(selector.value));
        content.querySelector("#new-event").onclick = () => this.run(() => this.eventDialog());
        content.querySelector("#edit-event").onclick = () => this.run(() => this.eventDialog(this.liveSnapshot?.event));
        content.querySelector("#refresh-live").onclick = () => this.run(() => this.loadLive(selector.value));
        await this.loadLive(selector.value);
    }

    async loadLive(id)
    {
        this.liveId = id;
        this.map?.destroy();
        this.map = null;
        this.liveSelected = new Set();
        const content = this.root.querySelector("#live-content");
        if (id === "")
        {
            this.liveSnapshot = null;
            content.innerHTML = '<div class="empty"><h2>Create your first event.</h2><p>Publish a reviewed plan, then select it in the event settings. Each event has its own reservations.</p></div>';
            return;
        }
        this.liveSnapshot = await this.api(`/api/admin/events/${id}`);
        const snapshot = this.liveSnapshot;
        content.innerHTML = `<div class="editor-header"><div><h2>${escape(snapshot.event.title)}</h2><p class="muted">${escape(snapshot.event.starts_at)} · ${snapshot.event.status} · revision ${snapshot.event.revision}</p></div><div class="actions wrap"><a class="button secondary" target="_blank" rel="noopener" href="${this.base}/print/${id}">Print plan</a><a class="button secondary" target="_blank" rel="noopener" href="${this.base}/print/${id}?roster=true">Print plan + attendee list</a><a class="button secondary" href="${this.base}/api/admin/events/${id}/csv">Export CSV</a></div></div><div class="workspace"><section class="map-panel"><div id="live-map"></div><div class="legend"><span class="dot free"></span>Available <span class="dot reserved"></span>Reserved <span class="dot blocked"></span>Unavailable <span class="dot selected"></span>Selected</div></section><aside class="inspector"><h2>Administrator override</h2><p class="small muted">Choose seats on the map or in the roster. An override may cancel another person's reservation; changes are logged and emailed.</p><div id="override-selection" class="selection-list"></div><form id="override-form"><label>Set selected seats to<select name="action"><option value="reserved">Reserved</option><option value="free">Free (remove event assignment)</option><option value="blocked">Blocked for this event</option></select></label><label>Attendee email <small>(optional for house reservations)</small><input name="email" type="email" maxlength="254"></label><label>Reason <small>(required)</small><textarea name="reason" minlength="3" maxlength="500" rows="3" required></textarea></label><label class="check"><input name="allow_excluded" type="checkbox">Explicitly allow reserving plan-excluded seats</label><p class="small muted">“Free” removes an event assignment. A seat excluded by the underlying plan remains non-reservable; revise the plan to change that rule.</p><button class="button danger full">Apply administrator override</button></form><hr><div id="live-counts"></div></aside></div><section class="roster-panel"><div class="split"><h2>Seat roster</h2><input id="roster-search" placeholder="Search email, seat or reference" aria-label="Search roster"></div><div class="table-scroll"><table><thead><tr><th>Select</th><th>Seat</th><th>State</th><th>Email</th><th>Reservation</th><th>Note</th></tr></thead><tbody id="roster-body"></tbody></table></div></section>`;
        this.map = new SeatMap(content.querySelector("#live-map"), {base: this.base, planId: snapshot.plan.id, pages: snapshot.plan.pages, seats: snapshot.seats, onSeat: (sid) => this.toggleLiveSeat(sid)});
        content.querySelector("#roster-search").oninput = () => this.liveRoster();
        content.querySelector("#override-form").onsubmit = (event) =>
        {
            event.preventDefault();
            this.run(async () =>
            {
                if (this.liveSelected.size === 0)
                {
                    throw new Error("Select at least one seat.");
                }
                const form = event.target, count = this.liveSelected.size;
                const displaced = this.liveSnapshot.seats.filter((seat) => this.liveSelected.has(seat.id) && seat.status === "reserved").length;
                if (confirm(`Set ${count} seats to ${form.elements.action.value}? This affects ${displaced} currently reserved seats. Attendees with email addresses will be notified.`) === false)
                {
                    return;
                }
                await this.api(`/api/admin/events/${id}/override`, {method: "POST", body: {revision: this.liveSnapshot.event.revision, seats: [...this.liveSelected], action: form.elements.action.value, email: form.elements.email.value, reason: form.elements.reason.value, allow_excluded: form.elements.allow_excluded.checked}});
                await this.loadLive(id);
                this.toast("Override applied and recorded in the audit log.");
            });
        };
        this.updateLiveSelection();
        this.liveRoster();
    }

    toggleLiveSeat(id)
    {
        if (this.liveSelected.has(id))
        {
            this.liveSelected.delete(id);
        }
        else
        {
            this.liveSelected.add(id);
        }
        this.map.update(this.liveSnapshot.seats, [], this.liveSelected);
        this.updateLiveSelection();
        this.liveRoster();
    }

    updateLiveSelection()
    {
        const snapshot = this.liveSnapshot;
        const seats = snapshot.seats.filter((seat) => this.liveSelected.has(seat.id));
        this.root.querySelector("#override-selection").innerHTML = `<strong>${seats.length} selected</strong>` + seats.slice(0, 20).map((seat) => `<div>${escape(seatName(seat))}<small>${escape(seat.allocation?.email || seat.status)}</small></div>`).join("") + (seats.length > 20 ? `<p>+ ${seats.length - 20} more</p>` : "");
        this.root.querySelector("#live-counts").innerHTML = ["free", "reserved", "blocked"].map((state) => `<div class="stat-line"><span>${state}</span><strong>${snapshot.seats.filter((seat) => seat.status === state).length}</strong></div>`).join("");
    }

    liveRoster()
    {
        const term = this.root.querySelector("#roster-search").value.toLowerCase();
        const seats = this.liveSnapshot.seats.filter((seat) => `${seatName(seat)} ${seat.allocation?.email ?? ""} ${seat.allocation?.booking_id ?? ""}`.toLowerCase().includes(term));
        const body = this.root.querySelector("#roster-body");
        body.innerHTML = seats.map((seat) => `<tr><td><input type="checkbox" data-roster="${seat.id}" ${this.liveSelected.has(seat.id) ? "checked" : ""} aria-label="Select ${escape(seatName(seat))}"></td><td>${escape(seatName(seat))}</td><td><span class="state-label ${seat.status}">${seat.status}</span></td><td>${escape(seat.allocation?.email ?? "")}</td><td class="reference">${escape(seat.allocation?.booking_id ?? "")}</td><td>${escape(seat.allocation?.note ?? "")}</td></tr>`).join("");
        body.querySelectorAll("[data-roster]").forEach((input) => input.onchange = () => this.toggleLiveSeat(input.dataset.roster));
    }

    async eventDialog(event)
    {
        const data = await this.api("/api/admin/plans");
        const plans = data.plans.filter((plan) => plan.state === "published");
        if (plans.length === 0)
        {
            throw new Error("Publish a reviewed seating plan before creating an event.");
        }
        const modal = dialog(`<h2>${event ? "Event settings" : "Create an event"}</h2><form id="event-form"><label>Event title<input name="title" maxlength="160" required value="${escape(event?.title ?? "")}"></label><label>Date and time <small>(display text, include timezone)</small><input name="starts_at" maxlength="80" placeholder="25 September 2026, 19:00 · Europe/Bucharest" value="${escape(event?.starts_at ?? "")}"></label><label>Description<textarea name="description" maxlength="2000" rows="3">${escape(event?.description ?? "")}</textarea></label><label>Published seating plan<select name="plan_id">${plans.map((plan) => `<option value="${plan.id}" ${event?.plan_id === plan.id ? "selected" : ""}>${escape(plan.name)}</option>`).join("")}</select></label><div class="two"><label>Reservations<select name="status"><option value="closed" ${event?.status !== "open" ? "selected" : ""}>Closed</option><option value="open" ${event?.status === "open" ? "selected" : ""}>Open</option></select></label><label>Seats per email <small>(0 = unlimited)</small><input name="max_per_user" type="number" min="0" max="5000" value="${event?.max_per_user ?? 4}"></label></div><p class="notice small">Changing the plan preserves occupied seats only when their exact section / row / label exists in the new version. Missing or newly excluded reserved seats prevent the change. Resolve them explicitly first.</p><div class="actions"><button type="button" class="button secondary" data-close>Cancel</button><button class="button">Save event</button></div></form>`);
        modal.querySelector("form").onsubmit = (submit) =>
        {
            submit.preventDefault();
            this.run(async () =>
            {
                const form = submit.target;
                const payload = Object.fromEntries(new FormData(form));
                payload.max_per_user = Number(payload.max_per_user);
                payload.revision = event?.revision ?? null;
                const response = await this.api(event ? `/api/admin/events/${event.id}` : "/api/admin/events", {method: event ? "PUT" : "POST", body: payload});
                this.liveId = response.id;
                modal.close();
                await this.showLive();
                this.toast("Event saved.");
            });
        };
    }

    async showSystem()
    {
        const data = await this.api("/api/admin/system");
        const target = this.root.querySelector("#admin-content");
        const worker = data.worker_age_seconds === null ? "No worker heartbeat yet" : `Worker heartbeat: ${data.worker_age_seconds}s ago`;
        target.innerHTML = `<div class="system-summary"><div><h2>Delivery & operations</h2><p class="${data.worker_age_seconds === null || data.worker_age_seconds > 150 ? "danger-text" : "muted"}">${escape(worker)} · ${escape(data.mail_backend)} mail backend</p><p class="muted">${Object.entries(data.mail_counts).map(([key, value]) => `${escape(key)}: ${value}`).join(" · ") || "No messages queued yet."}</p></div><button id="refresh-system" class="button secondary">Refresh</button></div><section class="roster-panel"><h2>Email delivery</h2><div class="table-scroll"><table><thead><tr><th>Recipient</th><th>Subject</th><th>State</th><th>Attempts</th><th>Error</th><th></th></tr></thead><tbody>${data.messages.map((message) => `<tr><td>${escape(message.recipient)}</td><td>${escape(message.subject)}</td><td>${escape(message.status)}</td><td>${message.attempts}</td><td>${escape(message.last_error)}</td><td>${["queued", "failed"].includes(message.status) ? `<button class="text-button" data-retry="${message.id}">Retry</button>` : ""}</td></tr>`).join("")}</tbody></table></div></section><section class="roster-panel"><h2>PDF jobs</h2><div class="table-scroll"><table><thead><tr><th>Job</th><th>Type</th><th>State</th><th>Error</th></tr></thead><tbody>${data.jobs.map((job) => `<tr><td class="reference">${job.id}</td><td>${job.kind}</td><td>${job.status}</td><td>${escape(job.error)}</td></tr>`).join("")}</tbody></table></div></section><section class="roster-panel"><h2>Latest audit entries</h2><p class="small muted">Showing the latest 100 entries. The database retains the complete audit trail; the web interface cannot edit or delete it.</p><div class="table-scroll"><table><thead><tr><th>Time (local)</th><th>Actor</th><th>Action</th><th>Details</th></tr></thead><tbody>${data.audit.map((entry) => `<tr><td>${escape(new Date(entry.created * 1000).toLocaleString())}</td><td>${escape(entry.actor)}</td><td>${escape(entry.action)}</td><td class="audit-detail">${escape(entry.details)}</td></tr>`).join("")}</tbody></table></div></section>`;
        target.querySelector("#refresh-system").onclick = () => this.run(() => this.showSystem());
        target.querySelectorAll("[data-retry]").forEach((button) => button.onclick = () => this.run(async () =>
        {
            await this.api(`/api/admin/mail/${button.dataset.retry}/retry`, {method: "POST"});
            await this.showSystem();
        }));
    }
}

const app = new SeatplanApp();
app.run(() => app.start());
