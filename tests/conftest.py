from __future__ import annotations

import io
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from reportlab.pdfgen import canvas

from seatplan.app import create_app
from seatplan.config import Settings
from seatplan.db import json_dump
from seatplan.domain import new_id


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, public_url="http://testserver", secret="a-unit-test-secret-with-more-than-32-characters", admin_emails=frozenset({"admin@example.org"}), development=True, mail_backend="file")


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def csrf(client: TestClient, origin="http://testserver") -> dict:
    return {"X-CSRF-Token": client.get("/api/me").json()["csrf"], "Origin": origin}


def login(client: TestClient, app, email="person@example.org") -> dict:
    headers = csrf(client, app.state.settings.origin)
    response = client.post("/api/auth/request", json={"email": email}, headers=headers)
    assert response.status_code == 200, response.text
    with app.state.db.read() as connection:
        message = connection.execute("SELECT body FROM outbox WHERE recipient=? ORDER BY created DESC LIMIT 1", (email,)).fetchone()[0]
    token = re.search(r"#confirm=([A-Za-z0-9_-]+)", message).group(1)
    response = client.post("/api/auth/confirm", json={"token": token}, headers=headers)
    assert response.status_code == 200, response.text
    return csrf(client, app.state.settings.origin)


def make_plan(db, settings, published=True, count=8, prefix="Main", blocked=(), reviewed=True):
    plan_id = new_id()
    folder = settings.data_dir / "assets" / plan_id
    folder.mkdir(parents=True)
    Image.new("RGB", (600, 800), "white").save(folder / "page-001.png")
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(600, 800))
    pdf.drawString(30, 760, "TEST SEATING PLAN")
    pdf.save()
    (folder / "source.pdf").write_bytes(stream.getvalue())
    pages = [{"index": 0, "width": 600, "height": 800, "filename": "page-001.png", "has_text": True, "asset": f"assets/{plan_id}/page-001.png"}]
    seats = []
    with db.transaction() as connection:
        connection.execute("INSERT INTO plans(id,name,pdf_path,sha256,pages,zones,state,created) VALUES(?,?,?,?,?,'[]',?,?)", (plan_id, "Test plan", f"assets/{plan_id}/source.pdf", "0" * 64, json_dump(pages), "published" if published else "draft", time.time()))
        for index in range(count):
            seat = {"id": new_id(), "page": 0, "section": prefix, "row": "A", "label": str(index + 1), "x": 0.15 + index * 0.08, "y": 0.4, "w": 0.05, "h": 0.04, "angle": 0, "blocked": index in blocked, "reviewed": reviewed, "source": "test", "note": "private organizer note", "score": 1}
            seats.append(seat)
            connection.execute("INSERT INTO seats VALUES(?,?,?,?,?,?)", (seat["id"], plan_id, seat["section"], seat["row"], seat["label"], json_dump(seat)))
    return plan_id, seats


def make_event(db, plan_id, maximum=4):
    event_id = new_id()
    with db.transaction() as connection:
        connection.execute("INSERT INTO events(id,title,plan_id,status,max_per_user,created) VALUES(?, 'Concert',?,'open',?,?)", (event_id, plan_id, maximum, time.time()))
    return event_id


@pytest.fixture
def inventory(app, settings):
    pid, seats = make_plan(app.state.db, settings, blocked=(7,))
    eid = make_event(app.state.db, pid)
    return pid, seats, eid


@pytest.fixture(scope="session")
def current_sample(tmp_path_factory):
    """One actual default-detector execution shared by geometry/location tests."""
    import json
    import cv2
    import numpy as np
    from seatplan.detection import detect_seats, render_pdf
    from seatplan.domain import DetectSpec
    from seatplan.geometry import seat_vertices
    root = Path(__file__).resolve().parents[1]
    folder = tmp_path_factory.mktemp("current-sample")
    pdf = root / "examples/sample-hall.pdf"
    pages = render_pdf(pdf, folder)
    zones = json.loads((root / "examples/sample-exclusions.json").read_text())["zones"]
    output = detect_seats(folder / pages[0]["filename"], DetectSpec(), zones, pdf)
    return output, [np.asarray(seat_vertices(s, pages[0]["width"], pages[0]["height"]), np.float32) for s in output["seats"]]
