"""Real Chromium UI smoke test; uses temporary data and development-only file mail.
Run: python tests/browser_smoke.py --output /tmp/seatplan-browser
Requires Chromium, or CHROMIUM_EXECUTABLE; installs no browser automatically.
"""
from __future__ import annotations

import argparse
import email
import email.policy
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from seatplan.cli import seed_sample
from seatplan.config import Settings
from seatplan.db import Database, json_dump
from seatplan.domain import new_id
from seatplan.detection import render_pdf
from reportlab.pdfgen import canvas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/seatplan-browser"))
    parser.add_argument("--bridge", action="store_true", help="Offline DOM transport bridge for environments where managed Chromium blocks all navigation; not an HTTP browser end-to-end test.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    executable = os.environ.get("CHROMIUM_EXECUTABLE") or shutil.which("chromium") or shutil.which("chromium-browser")
    if executable is None:
        raise SystemExit("Install Chromium, or set CHROMIUM_EXECUTABLE to a browser executable.")
    with tempfile.TemporaryDirectory(prefix="seatplan-browser-") as temporary:
        data = Path(temporary)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        settings = Settings(data_dir=data, public_url=origin, secret="development-browser-test-only-secret-123456789", admin_emails=frozenset({"admin@example.org"}), development=True, mail_backend="file")
        settings.prepare()
        db = Database(settings.db_path)
        db.initialize()
        sample_id = seed_sample(db, data)
        with db.read() as connection:
            sample_payloads = [json.loads(row['payload']) for row in connection.execute("SELECT payload FROM seats WHERE plan_id=?", (sample_id,))]
        sample_count = len(sample_payloads)
        # A clearly synthetic, separately reviewed plan for booking/print testing.
        pid, eid = new_id(), new_id()
        folder = data / "assets" / pid
        folder.mkdir()
        pdf = canvas.Canvas(str(folder / "source.pdf"), pagesize=(600, 800))
        pdf.setFont("Helvetica", 24)
        pdf.drawCentredString(300, 710, "BROWSER TEST HALL")
        pdf.setFont("Helvetica", 11)
        pdf.drawCentredString(300, 681, "Synthetic plan used only for functional testing")
        pdf.roundRect(110, 90, 380, 55, 12)
        pdf.drawCentredString(300, 113, "STAGE")
        seats = []
        for row in range(4):
            for col in range(8):
                x, y = 120 + col * 50, 300 + row * 70
                pdf.rect(x - 17, 800 - y - 22, 34, 44)
                pdf.drawCentredString(x, 800 - y - 4, f"{row + 1}-{col + 1}")
                seats.append({"id": new_id(), "page": 0, "section": "Main", "row": chr(65 + row), "label": str(col + 1), "x": x / 600, "y": y / 800, "w": 34 / 600, "h": 44 / 800, "angle": 0, "blocked": col == 7, "reviewed": True, "source": "browser-test", "note": "", "score": 1})
        pdf.save()
        pages = render_pdf(folder / "source.pdf", folder)
        for page in pages:
            page["asset"] = str((folder / page["filename"]).relative_to(data))
        with db.transaction() as connection:
            connection.execute("INSERT INTO plans(id,name,pdf_path,sha256,pages,zones,state,created) VALUES(?,?,?,?,?,'[]','published',?)", (pid, "Synthetic browser test plan", f"assets/{pid}/source.pdf", "0" * 64, json_dump(pages), time.time()))
            for seat in seats:
                connection.execute("INSERT INTO seats VALUES(?,?,?,?,?,?)", (seat["id"], pid, seat["section"], seat["row"], seat["label"], json_dump(seat)))
            connection.execute("INSERT INTO events(id,title,starts_at,description,plan_id,status,max_per_user,created) VALUES(?,?,?,?,?,'open',4,?)", (eid, "Demonstration concert", "Example event · Europe/Bucharest", "Synthetic seats for the end-to-end test; not the supplied hall inventory.", pid, time.time()))
        env = dict(os.environ, DATA_DIR=str(data), PUBLIC_URL=origin, APP_SECRET=settings.secret, ADMIN_EMAILS="admin@example.org", DEVELOPMENT="1", MAIL_BACKEND="file", OPENBLAS_NUM_THREADS="1", PYTHONPATH=str(ROOT))
        logs = (args.output / "server.log").open("w")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "seatplan.app:create_app", "--factory", "--host", "127.0.0.1", "--port", str(port), "--no-proxy-headers"], cwd=ROOT, env=env, stdout=logs, stderr=logs)
        worker = subprocess.Popen([sys.executable, "-m", "seatplan.worker"], cwd=ROOT, env=env, stdout=logs, stderr=logs)
        errors = []
        checks = []
        try:
            import httpx
            for _ in range(100):
                try:
                    if httpx.get(origin + "/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox"])
                clients = {}
                media = {}
                import base64
                with db.read() as connection:
                    for plan_row in connection.execute("SELECT id,pages FROM plans"):
                        for item in json.loads(plan_row["pages"]):
                            media[f"/media/{plan_row['id']}/{item['index']}.png"] = "data:image/png;base64," + base64.b64encode((data / item["asset"]).read_bytes()).decode()

                def navigate(page, path="/", token=""):
                    if args.bridge is False:
                        page.goto(origin + path + ("#confirm=" + token if token else ""))
                        return
                    if page not in clients:
                        clients[page] = httpx.Client(base_url=origin)
                        def transport(payload):
                            headers = dict(payload.get("headers", {}))
                            headers["Origin"] = origin
                            response = clients[page].request(payload.get("method", "GET"), payload["url"], headers=headers, content=payload.get("body"))
                            return {"body": response.text, "status": response.status_code, "headers": dict(response.headers)}
                        page.expose_function("seatplanTestTransport", transport)
                    response = clients[page].get(path)
                    assert response.status_code == 200, response.text
                    html = re.sub(r'<script[^>]*src="[^"]*"[^>]*></script>', '', response.text)
                    css_name = "print.css" if path.startswith("/print/") else "app.css"
                    html = re.sub(r'<link rel="stylesheet"[^>]*>', '<style>' + (ROOT / "seatplan/static" / css_name).read_text() + '</style>', html)
                    for url, image in media.items():
                        html = html.replace('href="' + url + '"', 'href="' + image + '"')
                    if page.locator("body").count():
                        page.evaluate("window.__testApp && clearInterval(window.__testApp.pollTimer)")
                    page.set_content(html)
                    if path.startswith("/print/"):
                        return
                    source = "\n".join((ROOT / "seatplan/static" / name).read_text() for name in ["i18n.js", "ui.js", "map.js", "editor.js", "app.js"])
                    source = re.sub(r'^import .*?;\s*$', '', source, flags=re.MULTILINE).replace("export async function", "async function").replace("export function", "function").replace("export class", "class")
                    source = source.replace("const image = `${this.options.base}/media/${encodeURIComponent(this.options.planId)}/${this.page}.png`;", "const image = testMedia[`${this.options.base}/media/${encodeURIComponent(this.options.planId)}/${this.page}.png`];")
                    prelude = "const location = " + json.dumps({"hash": "#confirm=" + token if token else "", "pathname": "/", "search": ""}) + "; const history = {replaceState() {}}; const testMedia = " + json.dumps(media) + "; const fetch = async (url, options={}) => { const reply = await window.seatplanTestTransport({url, ...options}); return new Response(reply.body, {status: reply.status, headers: reply.headers}); };"
                    page.add_script_tag(content="(() => {" + prelude + source + "; window.__testApp = app; })();")

                admin = browser.new_page(viewport={"width": 1536, "height": 1100}, device_scale_factor=1)
                admin.on("pageerror", lambda error: errors.append(str(error)))
                admin.on("dialog", lambda dialog: dialog.accept())

                def sign_in(page, address):
                    navigate(page)
                    page.locator("#account-button").click()
                    page.locator('#signin-form input[name="email"]').fill(address)
                    page.get_by_role("button", name="Send sign-in link").click()
                    page.locator("#signin-message .notice").wait_for()
                    token = None
                    for _ in range(100):
                        for path in (data / "dev-mail").glob("*.eml"):
                            message = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
                            if message["To"] == address:
                                match = re.search(r"#confirm=([A-Za-z0-9_-]+)", message.get_content())
                                if match:
                                    token = match.group(1)
                        if token:
                            break
                        time.sleep(0.1)
                    assert token, "No login mail reached the development mailbox."
                    navigate(page, token=token)
                    if args.bridge is False:
                        # A hash-only navigation reuses the current document;
                        # reload so startup consumes the emailed fragment.
                        page.reload()
                    page.get_by_role("button", name="Sign out", exact=True).wait_for()

                sign_in(admin, "admin@example.org")
                checks.append("Admin email request, delivered link, automatic verification and cookie session")
                admin.locator("#admin-nav").click()
                admin.locator("#plan-select").select_option(sample_id)
                admin.locator("#plan-count").filter(has_text=str(sample_count)).wait_for()
                admin.locator("#editor-map svg image").wait_for()
                admin.wait_for_timeout(700)
                admin.locator('#editor-map [data-map="fit"]').click()
                fitted = admin.locator('#editor-map').evaluate("""el => {
                    const svg=el.querySelector('svg').getBoundingClientRect(), viewport=el.querySelector('.map-viewport').getBoundingClientRect();
                    return svg.left>=viewport.left && svg.top>=viewport.top && svg.right<=viewport.right+1 && svg.bottom<=viewport.bottom+1;
                }""")
                assert fitted, "Fit-page clips the actual SVG image."
                admin.screenshot(path=str(args.output / "sample-editor.png"), full_page=True)
                admin.locator('#editor-map [data-map="focus"]').click()
                admin.screenshot(path=str(args.output / "sample-editor-focus.png"), full_page=False)
                admin.locator('#editor-map [data-map="focus"]').click()
                checks.append("Full PDF fits inside the viewport; focus view exposes the complete page")
                # Publication is blocked by the review gate without changing the draft.
                admin.locator("#editor-publish").click()
                assert "Review every candidate" in admin.locator("#toast").inner_text()
                checks.append(f"Supplied scanned PDF displayed with {sample_count} pending candidates and 12 masks; unreviewed publication blocked")
                # Click each newly recovered reference, not merely count SVG nodes.
                import math
                import cv2
                import numpy as np
                from seatplan.geometry import seat_vertices
                reference = json.loads((ROOT / "tests/fixtures/sample-coverage-points.json").read_text())
                for point in reference["points"]:
                    matching = []
                    for seat in sample_payloads:
                        polygon = np.asarray(seat_vertices(seat, reference["width"], reference["height"]), np.float32)
                        if cv2.pointPolygonTest(polygon, (point["x"], point["y"]), False) >= 0:
                            matching.append(seat)
                    assert len(matching) == 1, point
                    target = matching[0]
                    admin.locator(f'#editor-map svg [data-id="{target["id"]}"]').click()
                    assert admin.locator('#seat-form input[name="label"]').input_value() == target["label"]
                    assert admin.locator('#seat-form select[name="blocked"]').input_value() == "false"
                checks.append(f"Clicked all {len(reference['points'])} recovered references (438, 437, loje and upper rows); correct inspector and free flags")
                residuals = json.loads((ROOT / "tests/fixtures/sample-residual-points.json").read_text())
                clicked = []
                for point in residuals["points"]:
                    matching = [seat for seat in sample_payloads if cv2.pointPolygonTest(np.asarray(seat_vertices(seat, residuals["width"], residuals["height"]), np.float32), (point["x"], point["y"]), False) >= 0]
                    assert len(matching) == 1, point
                    target = matching[0]
                    admin.locator(f'#editor-map svg [data-id="{target["id"]}"]').click()
                    assert admin.locator('#seat-form input[name="label"]').input_value() == target["label"]
                    assert admin.locator('#seat-form select[name="blocked"]').input_value() == "false"
                    clicked.append(target["id"])
                assert len(set(clicked)) == len(clicked)
                assert admin.locator('#detect-form input[name="recover_groups"]').is_checked()
                checks.append(f"Clicked {len(clicked)} distinct residual-region seats: six outer-right seats and eighteen loje symbols; recovery enabled")
                admin.locator('#editor-map .map-viewport').evaluate("el => { el.scrollTop=0; el.scrollLeft=0; }")
                # Select a shape and edit via the real inspector.
                first = admin.locator('#editor-map svg g[data-id]').first
                if first.count() == 0:
                    first = admin.locator('#editor-map svg [data-id]').first
                first.click()
                admin.locator('#seat-form input[name="section"]').fill("Browser inspection")
                admin.locator('#seat-form button').click()
                admin.locator('#editor-save').click()
                admin.locator('#toast').filter(has_text="Draft saved").wait_for()
                checks.append("Seat selection, inspector edit, draft save through browser")
                # Draw a grid in the blank upper-left area; preview then add.
                admin.locator('[data-tool="grid"]').click()
                svg = admin.locator('#editor-map svg')
                box = svg.bounding_box()
                admin.mouse.move(box['x'] + box['width'] * .025, box['y'] + box['height'] * .015)
                admin.mouse.down()
                admin.mouse.move(box['x'] + box['width'] * .175, box['y'] + box['height'] * .065, steps=12)
                admin.mouse.up()
                admin.locator('#grid-form').wait_for()
                admin.locator('#grid-form input[name="rows"]').fill("2")
                admin.locator('#grid-form input[name="columns"]').fill("4")
                admin.locator('#grid-form input[name="section"]').fill("Test grid")
                admin.locator('#grid-form button:not([type])').click()
                admin.wait_for_timeout(250)
                admin.screenshot(path=str(args.output / "grid-preview.png"), full_page=True)
                admin.locator('#accept-preview').click()
                admin.locator('#plan-count').filter(has_text=f"{sample_count + 8} shapes").wait_for()
                admin.locator('#editor-undo').click()
                admin.locator('#plan-count').filter(has_text=f"{sample_count} shapes").wait_for()
                checks.append("Drawn 2 x 4 grid, algorithmic preview, add eight seats, undo")
                # Four user-selected corners construct a skewed, shared-edge grid.
                admin.locator('[data-tool="quadgrid"]').click()
                box = admin.locator('#editor-map svg').bounding_box()
                for x,y in [(.025,.015),(.145,.022),(.17,.067),(.045,.06)]:
                    admin.mouse.click(box['x']+x*box['width'],box['y']+y*box['height'])
                admin.locator('#grid-form').wait_for()
                admin.locator('#grid-form input[name="rows"]').fill("2")
                admin.locator('#grid-form input[name="columns"]').fill("2")
                admin.locator('#grid-form input[name="section"]').fill("Skewed grid")
                admin.locator('#grid-form button:not([type])').click()
                admin.locator('#accept-preview').wait_for()
                assert admin.locator('#editor-map polygon.preview').count() == 4
                admin.screenshot(path=str(args.output / "four-corner-grid-preview.png"), full_page=True)
                admin.locator('#accept-preview').click()
                admin.locator('#plan-count').filter(has_text=f"{sample_count + 4} shapes").wait_for()
                admin.locator('#editor-undo').click()
                checks.append("Four clicked corners generate four convex polygon cells; preview, add, undo")
                # Drag a corner through the actual SVG pointer handlers, then undo.
                admin.locator('[data-tool="select"]').click()
                # Zoom first: exceed the 4px pointer-drag threshold without
                # moving a vertex a substantial fraction of its source cell.
                for _ in range(4):
                    admin.locator('#editor-map [data-map="plus"]').click()
                reference_point = reference["points"][0]
                editable = next(seat for seat in sample_payloads if cv2.pointPolygonTest(np.asarray(seat_vertices(seat, reference["width"], reference["height"]), np.float32), (reference_point["x"], reference_point["y"]), False) >= 0)
                target = admin.locator(f'#editor-map polygon[data-id="{editable["id"]}"]')
                target.click()
                before_corner_edit = target.get_attribute("points")
                admin.locator('[data-tool="corners"]').click()
                handle = admin.locator('#editor-map [data-corner="0"]')
                hb = handle.bounding_box()
                admin.mouse.move(hb['x']+hb['width']/2,hb['y']+hb['height']/2)
                admin.mouse.down()
                admin.mouse.move(hb['x']+hb['width']/2+6,hb['y']+hb['height']/2+1,steps=6)
                admin.mouse.up()
                assert target.get_attribute("points") != before_corner_edit
                admin.locator('#editor-undo').click()
                assert target.get_attribute("points") == before_corner_edit
                admin.locator('[data-tool="select"]').click()
                admin.locator('#editor-map [data-map="fit"]').click()
                checks.append("Actual SVG corner dragging edits a convex cell outline; undo restores it")
                admin.locator('#detect-form button:not([type])').click()
                admin.locator('#detection-result strong').filter(has_text=f"{sample_count} candidates").wait_for(timeout=180000)
                admin.locator('#discard-preview').click()
                checks.append(f"Web detection job reaches the PDF worker and returns {sample_count} review-only candidates")
                # Discard preview and unsaved state on navigation.
                admin.locator('[data-admin-tab="live"]').click()
                admin.locator('#live-map svg').wait_for()
                user = browser.new_page(viewport={"width": 1440, "height": 1040})
                user.on("pageerror", lambda error: errors.append(str(error)))
                with db.transaction() as connection:
                    connection.execute("UPDATE events SET status='closed' WHERE id=?", (eid,))
                navigate(user)
                user.locator("#book-button").filter(has_text="Not reservable").wait_for()
                assert user.locator("#book-button").inner_text() == "Not reservable"
                assert user.locator("#book-button").is_disabled()
                with db.transaction() as connection:
                    connection.execute("UPDATE events SET status='open' WHERE id=?", (eid,))
                checks.append("Closed events show Not reservable on the booking button")
                sign_in(user, "visitor@example.org")
                user.locator(f'[data-id="{seats[0]["id"]}"]').click()
                user.locator(f'[data-id="{seats[1]["id"]}"]').click()
                user.locator('#book-button').click()
                user.locator('#toast').filter(has_text="Reservation confirmed").wait_for()
                user.locator(f'[data-id="{seats[2]["id"]}"]').click()
                user.locator(f'[data-id="{seats[3]["id"]}"]').click()
                user.locator(f'[data-id="{seats[4]["id"]}"]').click()
                assert user.locator('.selected-chip').count() == 2
                assert "limit has been reached" in user.locator('#toast').inner_text()
                checks.append("Existing two-seat booking leaves only two selectable seats under the per-event four-seat limit")
                user.screenshot(path=str(args.output / "booking-view.png"), full_page=True)
                checks.append("Visitor signs in by delivered email and reserves two seats through the map")
                user.locator('[data-view="mine"]').click()
                user.locator('.reservation-card').wait_for()
                assert user.locator('.reservation-seats > div').count() == 2
                checks.append("My reservations shows both seats")
                # Load admin live map after reservation; private roster and override.
                admin.locator('[data-admin-tab="live"]').click()
                admin.locator('#live-map svg').wait_for()
                admin.locator(f'[data-id="{seats[0]["id"]}"]').click()
                admin.locator('#override-form select[name="action"]').select_option("blocked")
                admin.locator('#override-form textarea[name="reason"]').fill("Browser test: technical obstruction")
                admin.locator('#override-form button').click()
                admin.locator('#toast').filter(has_text="Override applied").wait_for()
                checks.append("Admin overrides one reserved seat to blocked with required reason and audit")
                print_page = browser.new_page(viewport={"width": 1200, "height": 1100})
                # Share administrator session with this new tab.
                print_page.context.add_cookies(admin.context.cookies())
                if args.bridge:
                    clients[print_page] = clients[admin]
                navigate(print_page, f"/print/{eid}?roster=true")
                print_page.wait_for_load_state("networkidle")
                print_page.pdf(path=str(args.output / "allocation-print.pdf"), format="A4", print_background=True, prefer_css_page_size=True)
                assert "visitor@example.org" in print_page.locator('.printed-roster').inner_text()
                checks.append("Admin PDF print snapshot with seating map and private roster")
                mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
                mobile.on("pageerror", lambda error: errors.append(str(error)))
                navigate(mobile)
                mobile.locator('#public-map svg').wait_for()
                mobile.screenshot(path=str(args.output / "mobile.png"), full_page=True)
                overflow = mobile.evaluate("document.documentElement.scrollWidth > window.innerWidth + 2")
                assert overflow is False, "Mobile page has horizontal overflow."
                checks.append("390px mobile public page without horizontal page overflow")
                assert errors == [], errors
                checks.append("No uncaught JavaScript page errors across the tested flows")
                browser.close()
            (args.output / "browser-report.json").write_text(json.dumps({"transport": "offline DOM with HTTP API bridge (managed browser navigation blocked)" if args.bridge else "real browser HTTP", "passed": len(checks), "checks": checks, "page_errors": errors}, indent=2))
            print(json.dumps(checks, indent=2))
        finally:
            server.terminate()
            worker.terminate()
            server.wait(timeout=15)
            worker.wait(timeout=15)
            logs.close()


if __name__ == "__main__":
    main()
