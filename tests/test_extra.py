from __future__ import annotations

import io
import sqlite3
import tarfile

import pytest
from fastapi import HTTPException
from reportlab.pdfgen import canvas

from conftest import login, make_plan
from seatplan.cli import backup
from seatplan.domain import PlanSave, validate_rotated_bounds
from seatplan.services import save_plan
from seatplan.worker import process_job


def test_rotated_page_bounds_validate_full_rectangle(client, app, settings):
    pid, seats = make_plan(app.state.db, settings, published=False)
    seats[0].update(x=0.03, y=0.5, w=0.02, h=0.1, angle=90)
    payload = PlanSave(revision=1, name="Out of bounds", seats=seats)
    with pytest.raises(HTTPException) as error:
        save_plan(app.state.db, pid, payload, "admin@example.org")
    assert error.value.status_code == 422
    with app.state.db.read() as connection:
        assert connection.execute("SELECT revision FROM plans WHERE id=?", (pid,)).fetchone()[0] == 1
    seats[0]["x"] = 0.5
    validate_rotated_bounds(seats[0], 600, 800)


def test_backup_contains_consistent_database_and_pdf_assets(app, settings, inventory, tmp_path):
    destination = tmp_path / "snapshot.tar.gz"
    backup(app.state.db, settings.data_dir, destination)
    with tarfile.open(destination) as archive:
        content = archive.extractfile("seatplan.sqlite3").read()
        assert any(name.endswith("source.pdf") for name in archive.getnames())
    copy = tmp_path / "copy.sqlite3"
    copy.write_bytes(content)
    with sqlite3.connect(copy) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM seats").fetchone()[0] == 8


def test_multipage_pdf_page_count_limit(client, app, settings):
    headers = login(client, app, "admin@example.org")
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream)
    for index in range(11):
        pdf.drawString(30, 700, f"Page {index + 1}")
        pdf.showPage()
    pdf.save()
    response = client.post("/api/admin/plans/upload?name=Too%20many%20pages", headers={**headers, "Content-Type": "application/pdf"}, content=stream.getvalue())
    assert response.status_code == 200
    assert process_job(app.state.db, settings)
    job = client.get("/api/admin/jobs/" + response.json()["job_id"]).json()
    assert job["status"] == "failed" and "10 pages" in job["error"]


def test_print_labels_are_opt_in_and_private(client, app, inventory):
    _, _, eid = inventory
    login(client, app, "admin@example.org")
    response = client.get(f"/print/{eid}?labels=true")
    assert response.status_code == 200
    assert 'class="overlay-labels"' in response.text
    assert 'id="print-labels" checked' in response.text
    assert 'class="print-seat-label"' in response.text
