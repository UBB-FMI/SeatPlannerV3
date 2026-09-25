from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from seatplan.app import create_app
from seatplan.db import json_dump
from seatplan.domain import BookRequest, EventEdit, GridSpec, PlanSave, Seat, Zone, build_grid, new_id
from seatplan.security import Identity, digest, ensure_user
from seatplan.services import book, clone_plan, publish_plan, save_event, save_plan

from conftest import csrf, login, make_event, make_plan


def booking(client, eid, seats, headers, key=None):
    return client.post(f"/api/events/{eid}/book", json={"seats": seats, "request_key": key or new_id()}, headers=headers)


def test_login_confirmation_expiry_and_replay(client, app):
    headers = csrf(client)
    assert client.get("/api/me").json()["email"] is None
    assert client.post("/api/auth/request", json={"email": "PERSON@example.org"}, headers=headers).status_code == 200
    with app.state.db.read() as connection:
        mail = connection.execute("SELECT body FROM outbox").fetchone()[0]
        stored_hash = connection.execute("SELECT token_hash FROM login_tokens").fetchone()[0]
    token = re.search(r"#confirm=([A-Za-z0-9_-]+)", mail).group(1)
    assert token != stored_hash
    assert stored_hash == digest(app.state.settings, token)
    assert client.get("/#confirm=" + token).status_code == 200
    assert client.get("/api/me").json()["email"] is None  # mail scanners / GET do not log in
    response = client.post("/api/auth/confirm", json={"token": token}, headers=headers)
    assert response.status_code == 200
    assert client.get("/api/me").json()["email"] == "person@example.org"
    assert client.post("/api/auth/confirm", json={"token": token}, headers=csrf(client)).status_code == 400
    assert client.post("/api/auth/request", json={"email": "late@example.org"}, headers=csrf(client)).status_code == 200
    with app.state.db.transaction() as connection:
        text = connection.execute("SELECT body FROM outbox WHERE recipient='late@example.org'").fetchone()[0]
        connection.execute("UPDATE login_tokens SET expires=0 WHERE email='late@example.org'")
    expired = re.search(r"#confirm=([A-Za-z0-9_-]+)", text).group(1)
    assert client.post("/api/auth/confirm", json={"token": expired}, headers=csrf(client)).status_code == 400


def test_csrf_origin_admin_access_and_logout(client, app):
    assert client.get("/api/admin/plans").status_code == 401
    headers = csrf(client)
    assert client.post("/api/auth/request", json={"email": "x@example.org"}, headers={"Origin": "http://testserver"}).status_code == 403
    assert client.post("/api/auth/request", json={"email": "x@example.org"}, headers={**headers, "Origin": "https://other.example"}).status_code == 403
    headers = login(client, app)
    assert client.get("/api/admin/plans").status_code == 403
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/me").json()["email"] is None
    assert client.get("/api/bookings").status_code == 401


def test_email_validation_and_rate_limit(client):
    headers = csrf(client)
    for invalid in ["bad", "x@example.org\nBcc:x@evil.org", ".x@example.org", "x..y@example.org"]:
        assert client.post("/api/auth/request", json={"email": invalid}, headers=headers).status_code == 422
    for _ in range(5):
        assert client.post("/api/auth/request", json={"email": "limited@example.org"}, headers=headers).status_code == 200
    assert client.post("/api/auth/request", json={"email": "limited@example.org"}, headers=headers).status_code == 429


def test_headers_and_secure_cookies(settings):
    production = replace(settings, public_url="https://seats.example.org", development=False, mail_backend="smtp", smtp_host="smtp.example.org", mail_from="seats@example.org")
    with TestClient(create_app(production), base_url="https://seats.example.org") as client:
        response = client.get("/api/me")
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "no-store" in response.headers["cache-control"]


def test_multi_seat_booking_and_idempotency(client, app, inventory):
    pid, seats, eid = inventory
    headers = login(client, app)
    key = new_id()
    first = booking(client, eid, [seats[0]["id"], seats[1]["id"]], headers, key)
    assert first.status_code == 200, first.text
    again = booking(client, eid, [seats[1]["id"], seats[0]["id"]], headers, key)
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"] and again.json()["replayed"]
    assert booking(client, eid, [seats[2]["id"]], headers, key).status_code == 409
    mine = client.get("/api/bookings").json()["bookings"]
    assert len(mine) == 1 and len(mine[0]["seats"]) == 2
    with app.state.db.read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM outbox WHERE subject LIKE 'Reservation confirmed:%'").fetchone()[0] == 1


def test_all_or_nothing_conflict_quota_and_wrong_plan(client, app, settings, inventory):
    pid, seats, eid = inventory
    headers = login(client, app)
    assert booking(client, eid, [seats[0]["id"]], headers).status_code == 200
    assert booking(client, eid, [seats[0]["id"], seats[1]["id"]], headers).status_code == 409
    snapshot = client.get(f"/api/events/{eid}").json()
    states = {seat["id"]: seat["status"] for seat in snapshot["seats"]}
    assert states[seats[1]["id"]] == "free"
    assert booking(client, eid, [seat["id"] for seat in seats[1:5]], headers).status_code == 409
    other_pid, other_seats = make_plan(app.state.db, settings)
    assert booking(client, eid, [other_seats[0]["id"]], headers).status_code == 422
    assert booking(client, eid, [seats[1]["id"], seats[1]["id"]], headers).status_code == 422


def test_crossed_out_masks_and_individual_blocking(client, app, inventory):
    pid, seats, eid = inventory
    seat = seats[2]
    zone = {"id": new_id(), "page": 0, "name": "Crossed-out", "points": [[seat["x"]-.02, .38], [seat["x"]+.02, .38], [seat["x"]+.02, .42], [seat["x"]-.02, .42]]}
    with app.state.db.transaction() as connection:
        connection.execute("UPDATE plans SET zones=? WHERE id=?", (json_dump([zone]), pid))
    headers = login(client, app)
    assert booking(client, eid, [seats[7]["id"]], headers).status_code == 409
    assert booking(client, eid, [seats[2]["id"]], headers).status_code == 409
    assert booking(client, eid, [seats[1]["id"], seats[2]["id"]], headers).status_code == 409
    assert client.get("/api/bookings").json()["bookings"] == []


def test_booking_requires_confirmed_session_and_event_open(client, app, inventory):
    pid, seats, eid = inventory
    headers = csrf(client)
    assert booking(client, eid, [seats[0]["id"]], headers).status_code == 401
    client.post("/api/auth/request", json={"email": "pending@example.org"}, headers=headers)
    assert booking(client, eid, [seats[0]["id"]], headers).status_code == 401
    headers = login(client, app)
    with app.state.db.transaction() as connection:
        connection.execute("UPDATE events SET status='closed' WHERE id=?", (eid,))
    assert booking(client, eid, [seats[0]["id"]], headers).status_code == 409


def test_public_data_contains_no_email_or_internal_notes(client, app, inventory):
    pid, seats, eid = inventory
    headers = login(client, app, "privateperson@example.org")
    booking(client, eid, [seats[0]["id"]], headers)
    public = TestClient(app)
    response = public.get(f"/api/events/{eid}")
    assert response.status_code == 200
    assert "privateperson@example.org" not in response.text
    assert "private organizer note" not in response.text
    assert "booking_id" not in response.text
    assert public.get(f"/print/{eid}?roster=true").status_code == 401
    assert public.get(f"/api/admin/events/{eid}/csv").status_code == 401


def test_cancellation_and_ownership(client, app, inventory):
    pid, seats, eid = inventory
    headers = login(client, app)
    bid = booking(client, eid, [seats[0]["id"], seats[1]["id"]], headers).json()["id"]
    other = TestClient(app)
    other_headers = login(other, app, "other@example.org")
    assert other.post(f"/api/bookings/{bid}/cancel", json={"seats": []}, headers=other_headers).status_code == 404
    assert client.post(f"/api/bookings/{bid}/cancel", json={"seats": [seats[0]["id"]]}, headers=headers).json()["cancelled"] == 1
    assert len(client.get("/api/bookings").json()["bookings"][0]["seats"]) == 1
    assert booking(other, eid, [seats[0]["id"]], other_headers).status_code == 200
    assert client.post(f"/api/bookings/{bid}/cancel", json={"seats": [seats[0]["id"]]}, headers=headers).status_code == 409


def test_admin_override_stale_revision_and_notifications(client, app, inventory):
    pid, seats, eid = inventory
    headers = login(client, app)
    bid = booking(client, eid, [seats[0]["id"]], headers).json()["id"]
    admin = TestClient(app)
    admin_headers = login(admin, app, "admin@example.org")
    snapshot = admin.get(f"/api/admin/events/{eid}").json()
    payload = {"revision": snapshot["event"]["revision"], "seats": [seats[0]["id"]], "action": "reserved", "email": "new@example.org", "reason": "Move to new guest", "allow_excluded": False}
    assert admin.post(f"/api/admin/events/{eid}/override", json={**payload, "revision": 1}, headers=admin_headers).status_code == 409
    response = admin.post(f"/api/admin/events/{eid}/override", json=payload, headers=admin_headers)
    assert response.status_code == 200, response.text
    assert client.get("/api/bookings").json()["bookings"][0]["seats"] == []
    with app.state.db.read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit WHERE action='seats.override'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM outbox WHERE recipient='person@example.org' AND subject LIKE 'Reservation changed%'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM outbox WHERE recipient='new@example.org'").fetchone()[0] == 1
    rev = response.json()["revision"]
    excluded_payload = {**payload, "revision": rev, "seats": [seats[7]["id"]]}
    assert admin.post(f"/api/admin/events/{eid}/override", json=excluded_payload, headers=admin_headers).status_code == 422
    result = admin.post(f"/api/admin/events/{eid}/override", json={**excluded_payload, "allow_excluded": True}, headers=admin_headers)
    assert result.status_code == 200
    clear = {**excluded_payload, "revision": result.json()["revision"], "action": "free", "email": ""}
    assert admin.post(f"/api/admin/events/{eid}/override", json=clear, headers=admin_headers).status_code == 200
    state = {seat["id"]: seat["status"] for seat in admin.get(f"/api/admin/events/{eid}").json()["seats"]}
    assert state[seats[7]["id"]] == "blocked"


def test_concurrent_reservations_only_one_winner(app, settings, inventory):
    pid, seats, eid = inventory
    identities = []
    with app.state.db.transaction() as connection:
        for index in range(20):
            email = f"guest{index}@example.org"
            identities.append(Identity("", "", ensure_user(connection, email), email, False))
    def attempt(identity):
        try:
            book(app.state.db, settings, eid, BookRequest(seats=[seats[0]["id"]], request_key=new_id()), identity)
            return 200
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=20) as executor:
        statuses = list(executor.map(attempt, identities))
    assert statuses.count(200) == 1
    assert statuses.count(409) == 19
    with app.state.db.read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM allocations WHERE event_id=?", (eid,)).fetchone()[0] == 1


def test_editor_review_revision_validation_and_immutable_publication(client, app, settings):
    headers = login(client, app, "admin@example.org")
    pid, seats = make_plan(app.state.db, settings, published=False, reviewed=False)
    assert client.get(f"/media/{pid}/0.png").status_code == 200
    assert TestClient(app).get(f"/media/{pid}/0.png").status_code == 401
    payload = {"revision": 1, "name": "Reviewed hall", "notes": "", "seats": seats, "zones": []}
    assert client.post(f"/api/admin/plans/{pid}/publish", json={"revision": 1}, headers=headers).status_code == 422
    seats[0]["label"] = seats[1]["label"]
    assert client.put(f"/api/admin/plans/{pid}", json=payload, headers=headers).status_code == 422
    seats[0]["label"] = "1"
    for seat in seats:
        seat["reviewed"] = True
    response = client.put(f"/api/admin/plans/{pid}", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert client.put(f"/api/admin/plans/{pid}", json=payload, headers=headers).status_code == 409
    assert client.post(f"/api/admin/plans/{pid}/publish", json={"revision": 2}, headers=headers).status_code == 200
    assert client.put(f"/api/admin/plans/{pid}", json={**payload, "revision": 3}, headers=headers).status_code == 409
    assert TestClient(app).get(f"/media/{pid}/0.png").status_code == 200
    exported = client.get(f"/api/admin/plans/{pid}/export").json()
    assert exported["format"] == "seatplan-overlay-v1"
    assert "pdf_path" not in exported["plan"]


def test_grid_rotation_numbering_and_bounds(app, settings):
    spec = GridSpec(page=0, x=.2, y=.2, w=.3, h=.2, rows=2, columns=3, section="Left", start=12, step=-2, angle=15, gap_x=.2, gap_y=.1)
    seats = build_grid(spec, 600, 800)
    assert [seat["label"] for seat in seats] == ["12", "10", "8", "6", "4", "2"]
    assert all(seat["reviewed"] is False for seat in seats)
    assert all(seat["angle"] == 15 for seat in seats)
    per_row = build_grid(spec.model_copy(update={"numbering": "per-row", "serpentine": True}), 600, 800)
    assert [seat["label"] for seat in per_row] == ["12", "10", "8", "8", "10", "12"]
    with pytest.raises(ValueError):
        Seat(page=0, section="X", label="1", x=.001, y=.5, w=.1, h=.1)
    with pytest.raises(ValueError):
        Seat(page=0, section="X", label="1", x=float("nan"), y=.5, w=.1, h=.1)
    with pytest.raises(ValueError):
        Zone(page=0, points=[(.1,.1),(.1,.1),(.1,.1)])


def test_safe_plan_migration_preserves_reservations(client, app, settings, inventory):
    pid, seats, eid = inventory
    headers = login(client, app)
    bid = booking(client, eid, [seats[0]["id"]], headers).json()["id"]
    clone = clone_plan(app.state.db, pid, "admin@example.org")["id"]
    publish_plan(app.state.db, clone, 1, "admin@example.org")
    with app.state.db.read() as connection:
        rev = connection.execute("SELECT revision FROM events WHERE id=?", (eid,)).fetchone()[0]
    save_event(app.state.db, settings, EventEdit(title="Concert", plan_id=clone, status="open", revision=rev), "admin@example.org", eid)
    booking_data = client.get("/api/bookings").json()["bookings"][0]
    assert booking_data["id"] == bid and booking_data["seats"][0]["label"] == "1"
    assert booking_data["seats"][0]["id"] != seats[0]["id"]
    incompatible, _ = make_plan(app.state.db, settings, prefix="Different")
    with app.state.db.read() as connection:
        rev = connection.execute("SELECT revision FROM events WHERE id=?", (eid,)).fetchone()[0]
    with pytest.raises(HTTPException) as error:
        save_event(app.state.db, settings, EventEdit(title="Concert", plan_id=incompatible, revision=rev), "admin@example.org", eid)
    assert error.value.status_code == 409
    with app.state.db.read() as connection:
        assert connection.execute("SELECT plan_id FROM events WHERE id=?", (eid,)).fetchone()[0] == clone
        assert connection.execute("SELECT COUNT(*) FROM allocations WHERE booking_id=?", (bid,)).fetchone()[0] == 1


def test_csv_formula_safety_and_print_output(client, app, inventory):
    pid, seats, eid = inventory
    headers = login(client, app, "admin@example.org")
    snapshot = client.get(f"/api/admin/events/{eid}").json()
    response = client.post(f"/api/admin/events/{eid}/override", json={"revision": snapshot["event"]["revision"], "seats": [seats[0]["id"]], "action": "reserved", "email": "guest@example.org", "reason": "=HYPERLINK(test)", "allow_excluded": False}, headers=headers)
    assert response.status_code == 200
    csv = client.get(f"/api/admin/events/{eid}/csv")
    assert "'=HYPERLINK(test)" in csv.text
    page = client.get(f"/print/{eid}?roster=true")
    assert page.status_code == 200 and "guest@example.org" in page.text
    assert '<svg' in page.text and "Print / save as PDF" in page.text
    assert "guest@example.org" not in client.get(f"/print/{eid}").text


def test_subpath_urls_and_cookie_scope(settings):
    prefixed = replace(settings, public_url="http://testserver/seats")
    app = create_app(prefixed)
    with TestClient(app) as client:
        response = client.get("/seats/api/me")
        assert response.status_code == 200
        assert "Path=/seats/" in response.headers["set-cookie"]
        headers = {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf"]}
        assert client.post("/seats/api/auth/request", json={"email": "test@example.org"}, headers=headers).status_code == 200
        with app.state.db.read() as connection:
            mail = connection.execute("SELECT body FROM outbox").fetchone()[0]
        assert "http://testserver/seats/#confirm=" in mail
        assert 'src="/seats/static/app.js"' in client.get("/seats/").text


def test_settings_fail_closed(settings):
    with pytest.raises(ValueError):
        replace(settings, secret="weak")
    with pytest.raises(ValueError):
        replace(settings, development=False)
    with pytest.raises(ValueError):
        replace(settings, smtp_security="plain", smtp_password="password")
    with pytest.raises(ValueError):
        replace(settings, public_url="http://example.org/path?next=other")
