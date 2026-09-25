export function escape(value)
{
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
}

export function seatName(seat)
{
    return [seat.section, seat.row, seat.label].filter(Boolean).join(" / ");
}

export function pointInPolygon(x, y, points)
{
    let inside = false;
    for (let i = 0, j = points.length - 1; i < points.length; j = i++)
    {
        const [xi, yi] = points[i], [xj, yj] = points[j];
        const cross = (x - xi) * (yj - yi) - (y - yi) * (xj - xi);
        if (Math.abs(cross) < 1e-10 && x >= Math.min(xi, xj) - 1e-10 && x <= Math.max(xi, xj) + 1e-10 && y >= Math.min(yi, yj) - 1e-10 && y <= Math.max(yi, yj) + 1e-10)
        {
            return true;
        }
        if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi)
        {
            inside = inside === false;
        }
    }
    return inside;
}

export function isExcluded(seat, zones)
{
    return seat.blocked === true || zones.some((zone) => zone.page === seat.page && pointInPolygon(seat.x, seat.y, zone.points));
}

export function uid()
{
    if (typeof crypto.randomUUID === "function")
    {
        return crypto.randomUUID();
    }
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

export function downloadJSON(value, filename)
{
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: "application/json"}));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function dialog(html)
{
    const modal = document.querySelector("#modal");
    document.querySelector("#modal-content").innerHTML = html;
    if (modal.open === false)
    {
        modal.showModal();
    }
    modal.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => modal.close()));
    return modal;
}

export function numeric(form, name, fallback = 0)
{
    const value = form.elements[name].value;
    return value === "" ? fallback : Number(value);
}

// Intersection of actual convex seat outlines in page pixels. An axis-aligned
// center-distance heuristic can duplicate or suppress tightly spaced curved seats.
export function seatOverlap(first, second, page)
{
    const polygon = (seat) => seatVertices(seat, page);
    const cross = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    let subject = polygon(first);
    const clip = polygon(second);
    for (let i = 0; i < clip.length && subject.length > 0; i++)
    {
        const a = clip[i], b = clip[(i + 1) % clip.length], output = [];
        let previous = subject[subject.length - 1], previousSide = cross(a, b, previous);
        for (const current of subject)
        {
            const side = cross(a, b, current);
            if ((side >= 0) !== (previousSide >= 0))
            {
                const t = previousSide / (previousSide - side);
                output.push([previous[0] + t * (current[0] - previous[0]), previous[1] + t * (current[1] - previous[1])]);
            }
            if (side >= 0)
            {
                output.push(current);
            }
            previous = current;
            previousSide = side;
        }
        subject = output;
    }
    let area = 0;
    subject.forEach((point, index) =>
    {
        const next = subject[(index + 1) % subject.length];
        area += point[0] * next[1] - next[0] * point[1];
    });
    const polygonArea = (points) => Math.abs(points.reduce((sum, p, index) =>
    {
        const q = points[(index + 1) % points.length];
        return sum + p[0] * q[1] - q[0] * p[1];
    }, 0)) / 2;
    const minimumArea = Math.min(polygonArea(polygon(first)), polygonArea(polygon(second)));
    return Math.min(1, Math.abs(area) / (2 * minimumArea));
}

export function seatVertices(seat, page)
{
    const angle = seat.angle * Math.PI / 180, cosine = Math.cos(angle), sine = Math.sin(angle);
    const outline = seat.outline ?? [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]];
    return outline.map(([x, y]) =>
    {
        const dx = x * seat.w * page.width, dy = y * seat.h * page.height;
        return [seat.x * page.width + cosine * dx - sine * dy, seat.y * page.height + sine * dx + cosine * dy];
    });
}
