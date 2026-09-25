import {escape, isExcluded, seatName, seatVertices} from "./ui.js";

export class SeatMap
{
    constructor(target, options)
    {
        this.target = target;
        this.options = options;
        this.page = options.page ?? 0;
        this.zoom = 1;
        this.fitMode = "page";
        this.cornerPoints = [];
        this.tool = "select";
        this.selected = new Set();
        this.preview = [];
        this.showLabels = false;
        this.target.innerHTML = `<div class="map-controls"><div class="button-group"><button data-map="minus" title="Zoom out">−</button><button data-map="fit">Fit page</button><button data-map="width">Fit width</button><button data-map="plus" title="Zoom in">+</button></div><label>Page <select data-map="page"></select></label><label class="check"><input type="checkbox" data-map="labels"> Overlay labels</label><button data-map="focus" aria-pressed="false">Focus map</button><span class="map-tool-hint"></span></div><div class="map-viewport" tabindex="0"><svg xmlns="http://www.w3.org/2000/svg" class="seat-map" aria-label="Interactive seating plan"></svg></div>`;
        this.viewport = target.querySelector(".map-viewport");
        this.svg = target.querySelector("svg");
        target.querySelector('[data-map="minus"]').onclick = () => this.setZoom(this.zoom / 1.3);
        target.querySelector('[data-map="plus"]').onclick = () => this.setZoom(this.zoom * 1.3);
        target.querySelector('[data-map="fit"]').onclick = () => this.fit("page");
        target.querySelector('[data-map="width"]').onclick = () => this.fit("width");
        target.querySelector('[data-map="focus"]').onclick = () =>
        {
            const focused = this.target.classList.toggle("map-focus");
            target.querySelector('[data-map="focus"]').textContent = focused ? "Exit focus" : "Focus map";
            target.querySelector('[data-map="focus"]').setAttribute("aria-pressed", String(focused));
            this.fit("page");
        };
        this.escapeHandler = (event) =>
        {
            if (event.key === "Escape")
            {
                this.cornerPoints = [];
                if (this.target.classList.contains("map-focus"))
                {
                    target.querySelector('[data-map="focus"]').click();
                }
                this.render();
            }
        };
        document.addEventListener("keydown", this.escapeHandler);
        target.querySelector('[data-map="labels"]').onchange = (event) => { this.showLabels = event.target.checked; this.render(); };
        const pageSelect = target.querySelector('[data-map="page"]');
        pageSelect.innerHTML = options.pages.map((page, index) => `<option value="${index}">${index + 1}</option>`).join("");
        pageSelect.value = this.page;
        pageSelect.onchange = () =>
        {
            this.page = Number(pageSelect.value);
            this.preview = [];
            this.selected.clear();
            this.render();
            this.options.onPage?.(this.page);
        };
        this.svg.addEventListener("pointerdown", (event) => this.pointerDown(event));
        this.svg.addEventListener("pointermove", (event) => this.pointerMove(event));
        this.svg.addEventListener("pointerup", (event) => this.pointerUp(event));
        this.svg.addEventListener("pointercancel", () => { this.drag = null; this.render(); });
        this.svg.addEventListener("keydown", (event) =>
        {
            if (["Enter", " "].includes(event.key) && event.target.dataset.id)
            {
                event.preventDefault();
                this.options.onSeat?.(event.target.dataset.id, event);
            }
        });
        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(this.viewport);
        this.render();
    }

    destroy()
    {
        this.resizeObserver.disconnect();
        document.removeEventListener("keydown", this.escapeHandler);
    }

    setZoom(zoom)
    {
        this.zoom = Math.max(0.25, Math.min(16, zoom));
        this.resize();
    }

    fit(mode)
    {
        this.fitMode = mode;
        this.zoom = 1;
        this.resize();
        this.viewport.scrollTop = 0;
        this.viewport.scrollLeft = 0;
    }

    resize()
    {
        const page = this.options.pages[this.page];
        if (page === undefined)
        {
            return;
        }
        const style = getComputedStyle(this.viewport);
        const availableWidth = Math.max(1, this.viewport.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
        const availableHeight = Math.max(1, this.viewport.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom));
        const scale = this.fitMode === "width" ? availableWidth / page.width : Math.min(availableWidth / page.width, availableHeight / page.height);
        const width = page.width * scale * this.zoom;
        this.svg.style.width = `${width}px`;
        this.svg.style.height = `${width * page.height / page.width}px`;
    }

    setTool(tool)
    {
        this.tool = tool;
        this.cornerPoints = [];
        const hints = {corners: "Select one seat, then drag its four corner handles", quadgrid: "Click four corners clockwise, starting at row 1 / column 1; Escape cancels", select: "Click a seat · drag to select an area", move: "Drag a selected seat to move the selection", add: "Click to add a seat", grid: "Drag the outer bounds of a grid", exclude: "Drag a non-reservable area", region: "Drag the region to detect"};
        this.target.querySelector(".map-tool-hint").textContent = hints[tool] ?? "";
        this.svg.style.touchAction = tool === "select" && this.options.edit !== true ? "auto" : "none";
        this.render();
        this.svg.style.cursor = ["add", "grid", "quadgrid", "exclude", "region"].includes(tool) ? "crosshair" : "default";
    }

    update(seats, zones = [], selected = new Set())
    {
        this.options.seats = seats;
        this.options.zones = zones;
        this.selected = selected;
        this.render();
    }

    render()
    {
        const page = this.options.pages[this.page];
        if (page === undefined)
        {
            return;
        }
        const width = page.width, height = page.height;
        this.svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
        const image = `${this.options.base}/media/${encodeURIComponent(this.options.planId)}/${this.page}.png`;
        let html = `<image href="${escape(image)}" width="${width}" height="${height}" class="plan-background"/>`;
        const zones = this.options.zones ?? [];
        for (const zone of zones.filter((item) => item.page === this.page))
        {
            html += `<polygon class="exclusion-zone" points="${zone.points.map(([x, y]) => `${x * width},${y * height}`).join(" ")}"><title>${escape(zone.name)}</title></polygon>`;
        }
        for (const seat of [...this.options.seats, ...this.preview].filter((item) => item.page === this.page))
        {
            const state = seat.status ?? (isExcluded(seat, zones) ? "blocked" : seat.reviewed ? "free" : "review");
            const preview = this.preview.includes(seat);
            const selected = this.selected.has(seat.id);
            const x = seat.x * width, y = seat.y * height, w = seat.w * width, h = seat.h * height;
            const classNames = ["seat", state, selected ? "selected" : "", seat.mine ? "mine" : "", preview ? "preview" : ""].join(" ");
            const name = `${seatName(seat)} — ${state}${seat.mine ? " (yours)" : ""}`;
            const geometry = seat.outline ? `polygon points="${seat.outline.map(([px, py]) => `${px * w},${py * h}`).join(" ")}"` : `rect x="${-w / 2}" y="${-h / 2}" width="${w}" height="${h}" rx="1"`;
            const tag = seat.outline ? "polygon" : "rect";
            html += `<g transform="translate(${x} ${y}) rotate(${seat.angle})"><${geometry} class="${classNames}" data-id="${escape(seat.id)}" tabindex="${preview ? -1 : 0}" role="button" aria-label="${escape(name)}"><title>${escape(name)}</title></${tag}>`;
            if (this.showLabels)
            {
                html += `<text class="overlay-label" text-anchor="middle" dominant-baseline="central" font-size="${Math.max(5, Math.min(w / Math.max(1, seat.label.length) * 1.4, h * 0.6))}">${escape(seat.label)}</text>`;
            }
            html += "</g>";
        }
        if (this.options.edit === true && this.tool === "corners" && this.selected.size === 1)
        {
            const selected = this.options.seats.find((seat) => this.selected.has(seat.id));
            if (selected && selected.page === this.page)
            {
                seatVertices(selected, page).forEach(([x, y], index) =>
                {
                    html += `<circle class="corner-handle" data-id="${escape(selected.id)}" data-corner="${index}" cx="${x}" cy="${y}" r="6"><title>Drag corner ${index + 1}</title></circle>`;
                });
            }
        }
        if (this.cornerPoints.length > 0)
        {
            html += `<polyline class="grid-guide" points="${this.cornerPoints.map((p) => `${p.x * width},${p.y * height}`).join(" ")}"/>`;
            this.cornerPoints.forEach((point, index) => { html += `<circle class="grid-guide" cx="${point.x * width}" cy="${point.y * height}" r="5"/><text class="grid-guide-label" x="${point.x * width + 9}" y="${point.y * height}">${index + 1}</text>`; });
        }
        if (this.options.region && this.options.region.page === this.page)
        {
            const region = this.options.region;
            html += `<rect class="detection-region" x="${region.x * width}" y="${region.y * height}" width="${region.w * width}" height="${region.h * height}"/>`;
        }
        this.svg.innerHTML = html;
        this.resize();
    }

    position(event)
    {
        const point = this.svg.createSVGPoint();
        point.x = event.clientX;
        point.y = event.clientY;
        const result = point.matrixTransform(this.svg.getScreenCTM().inverse());
        const page = this.options.pages[this.page];
        return {x: Math.max(0, Math.min(1, result.x / page.width)), y: Math.max(0, Math.min(1, result.y / page.height))};
    }

    pointerDown(event)
    {
        if (event.button !== 0)
        {
            return;
        }
        const start = this.position(event);
        const id = event.target.closest("[data-id]")?.dataset.id;
        this.drag = {start, current: start, id, clientX: event.clientX, clientY: event.clientY, moved: false, event, corner: event.target.dataset.corner};
        if (this.options.edit === true)
        {
            this.svg.setPointerCapture(event.pointerId);
        }
    }

    pointerMove(event)
    {
        if (this.drag === undefined || this.drag === null)
        {
            return;
        }
        this.drag.current = this.position(event);
        this.drag.moved = Math.hypot(event.clientX - this.drag.clientX, event.clientY - this.drag.clientY) > 4;
        if (this.drag.moved === false || this.options.edit !== true || this.tool === "add")
        {
            return;
        }
        if (this.tool === "quadgrid")
        {
            return;
        }
        if (this.tool === "corners" && this.drag.corner !== undefined)
        {
            const handle = this.svg.querySelector(`[data-corner="${this.drag.corner}"]`);
            handle?.setAttribute("cx", this.drag.current.x * this.options.pages[this.page].width);
            handle?.setAttribute("cy", this.drag.current.y * this.options.pages[this.page].height);
            return;
        }
        this.svg.querySelector(".rubber-band")?.remove();
        const page = this.options.pages[this.page];
        const box = this.bounds();
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("class", "rubber-band");
        rect.setAttribute("x", box.x * page.width);
        rect.setAttribute("y", box.y * page.height);
        rect.setAttribute("width", box.w * page.width);
        rect.setAttribute("height", box.h * page.height);
        this.svg.append(rect);
    }

    bounds()
    {
        const a = this.drag.start, b = this.drag.current;
        return {page: this.page, x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y)};
    }

    pointerUp(event)
    {
        if (this.drag === undefined || this.drag === null)
        {
            return;
        }
        const drag = this.drag, box = this.bounds();
        this.drag = null;
        this.svg.querySelector(".rubber-band")?.remove();
        if (this.options.edit === true && this.tool === "quadgrid" && drag.moved === false)
        {
            this.cornerPoints.push(drag.current);
            if (this.cornerPoints.length === 4)
            {
                const points = this.cornerPoints;
                this.cornerPoints = [];
                this.options.onQuad?.(points, this.page);
            }
            this.render();
        }
        else if (this.options.edit === true && this.tool === "corners" && drag.corner !== undefined && drag.moved)
        {
            this.options.onCorner?.(drag.id, Number(drag.corner), drag.current);
            this.render();
        }
        else if (this.options.edit === true && this.tool === "move" && drag.id && drag.moved)
        {
            this.options.onMove?.(drag.id, drag.current.x - drag.start.x, drag.current.y - drag.start.y);
        }
        else if (this.options.edit === true && drag.moved && box.w > 0.001 && box.h > 0.001)
        {
            this.options.onDraw?.(this.tool, box, event);
        }
        else if (this.tool === "add" && this.options.edit === true)
        {
            this.options.onAdd?.({...drag.current, page: this.page});
        }
        else if (drag.id && drag.moved === false)
        {
            this.options.onSeat?.(drag.id, event);
        }
    }
}
