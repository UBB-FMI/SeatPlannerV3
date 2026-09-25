from __future__ import annotations

import hashlib
import io
import json
import socketserver
import threading
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from reportlab.pdfgen import canvas

from seatplan.db import json_dump
from seatplan.detection import detect_seats, diagonal_mark, render_pdf
from seatplan.domain import DetectSpec, excluded, new_id
from seatplan.mail import enqueue, process_one
from seatplan.worker import housekeeping, process_job

from conftest import csrf, login


def vector_pdf() -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(500, 600))
    for row in range(4):
        for col in range(6):
            x, y = 60 + col * 26, 280 + row * 30
            pdf.rect(x, y, 20, 24)
            pdf.setFont("Helvetica", 8)
            pdf.drawCentredString(x + 10, y + 8, str(row * 6 + col + 1))
    pdf.showPage()
    pdf.drawString(30, 500, "Second page")
    pdf.save()
    return buffer.getvalue()


def test_upload_render_detect_worker_and_draft_permissions(client, app, settings):
    headers = login(client, app, "admin@example.org")
    response = client.post("/api/admin/plans/upload?name=Vector%20hall", content=vector_pdf(), headers={**headers, "Content-Type": "application/pdf"})
    assert response.status_code == 200, response.text
    pid, job = response.json()["id"], response.json()["job_id"]
    assert process_job(app.state.db, settings)
    assert client.get(f"/api/admin/jobs/{job}").json()["status"] == "done"
    plan = client.get(f"/api/admin/plans/{pid}").json()
    assert plan["state"] == "draft" and len(plan["pages"]) == 2
    assert client.get(f"/media/{pid}/1.png").status_code == 200
    assert client.get(f"/media/{pid}/2.png").status_code == 404
    detected = client.post(f"/api/admin/plans/{pid}/detect", json={"page": 0, "min_size": 50, "max_size": 120}, headers=headers)
    assert detected.status_code == 200
    assert process_job(app.state.db, settings)
    report = client.get(f"/api/admin/jobs/{detected.json()['job_id']}").json()
    assert report["status"] == "done", report
    assert report["result"]["report"]["native_text"] is True
    assert report["result"]["report"]["candidates"] == 24
    assert all(seat["reviewed"] is False for seat in report["result"]["seats"])
    assert {seat["label"] for seat in report["result"]["seats"]} == {str(index) for index in range(1, 25)}
    # Detection never modifies a plan behind the administrator's back.
    assert client.get(f"/api/admin/plans/{pid}").json()["seats"] == []


def test_bad_pdf_and_oversized_body_are_rejected(client, app, settings):
    headers = login(client, app, "admin@example.org")
    assert client.post("/api/admin/plans/upload?name=Bad", content=b"not a PDF", headers={**headers, "Content-Type": "application/pdf"}).status_code == 422
    assert client.post("/api/admin/plans/upload?name=Bad", content=b"%PDF- broken file", headers={**headers, "Content-Type": "text/plain"}).status_code == 415
    response = client.post("/api/admin/plans/upload?name=Corrupt", content=b"%PDF-1.7 broken file", headers={**headers, "Content-Type": "application/pdf"})
    assert response.status_code == 200
    assert process_job(app.state.db, settings)
    assert client.get(f"/api/admin/jobs/{response.json()['job_id']}").json()["status"] == "failed"
    assert client.post("/api/auth/request", content=b"x" * (8 * 1024**2 + 1), headers={**headers, "Content-Type": "application/json"}).status_code == 413


def test_supplied_pdf_repeatable_detection_and_explicit_masks(tmp_path):
    examples = Path(__file__).resolve().parent.parent / "examples"
    pdf = examples / "sample-hall.pdf"
    profile = json.loads((examples / "sample-exclusions.json").read_text())
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == profile["pdf_sha256"]
    pages = render_pdf(pdf, tmp_path)
    assert len(pages) == 1 and pages[0]["has_text"] is False
    first = detect_seats(tmp_path / pages[0]["filename"], DetectSpec(), profile["zones"], pdf)
    second = detect_seats(tmp_path / pages[0]["filename"], DetectSpec(), profile["zones"], pdf)
    assert json_dump(first) == json_dump(second)
    assert first["report"]["candidates"] > 500  # Regression floor, NOT a claim of complete inventory.
    assert all(seat["reviewed"] is False for seat in first["seats"])
    assert all(seat["label"].startswith("D") for seat in first["seats"])
    assert any(excluded(seat, profile["zones"]) for seat in first["seats"])
    assert all(seat["blocked"] for seat in first["seats"] if excluded(seat, profile["zones"]))
    assert len(profile["zones"]) == 12


def test_long_diagonal_x_heuristic_on_clean_synthetic_patch():
    image = np.full((100, 100), 255, dtype=np.uint8)
    cv2.line(image, (25, 25), (75, 75), 0, 2)
    cv2.line(image, (25, 75), (75, 25), 0, 2)
    assert diagonal_mark(image, ((50.0, 50.0), (60.0, 60.0), 0.0))
    blank = np.full((100, 100), 255, dtype=np.uint8)
    assert diagonal_mark(blank, ((50.0, 50.0), (60.0, 60.0), 0.0)) is False


def test_file_mail_delivery_and_body_purge(app, settings):
    with app.state.db.transaction() as connection:
        mid = enqueue(connection, "guest@example.org", "Confirmation", "Your seat is A / 1.")
    assert process_one(app.state.db, settings)
    content = (settings.data_dir / "dev-mail" / f"{mid}.eml").read_text()
    assert "guest@example.org" in content and "Your seat is A / 1." in content
    with app.state.db.read() as connection:
        record = connection.execute("SELECT * FROM outbox WHERE id=?", (mid,)).fetchone()
    assert record["status"] == "sent" and record["body"] == ""
    assert record["attempts"] == 1


def test_smtp_delivery_to_local_capture_server(app, settings):
    messages = []
    class SMTPHandler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b"220 local test SMTP\r\n")
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                command = line.upper()
                if command.startswith((b"EHLO", b"HELO")):
                    self.wfile.write(b"250-local test\r\n250 SIZE 1000000\r\n")
                elif command.startswith(b"DATA"):
                    self.wfile.write(b"354 End with dot\r\n")
                    data = bytearray()
                    while True:
                        part = self.rfile.readline()
                        if part == b".\r\n" or not part:
                            break
                        data.extend(part)
                    messages.append(bytes(data))
                    self.wfile.write(b"250 accepted\r\n")
                elif command.startswith(b"QUIT"):
                    self.wfile.write(b"221 goodbye\r\n")
                    break
                else:
                    self.wfile.write(b"250 OK\r\n")
    with socketserver.TCPServer(("127.0.0.1", 0), SMTPHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        smtp = replace(settings, mail_backend="smtp", smtp_host="127.0.0.1", smtp_port=server.server_address[1], smtp_security="plain", mail_from="Seatplan <seats@example.org>")
        with app.state.db.transaction() as connection:
            enqueue(connection, "recipient@example.org", "SMTP test", "Reserved: Main / A / 1")
        assert process_one(app.state.db, smtp)
        server.shutdown()
        thread.join(timeout=2)
    assert len(messages) == 1 and b"Reserved: Main / A / 1" in messages[0]


def test_failed_mail_retry_and_expired_signin_cleanup(app, settings, monkeypatch):
    with app.state.db.transaction() as connection:
        mid = enqueue(connection, "guest@example.org", "Confirmation", "Body")
    def fail(*args):
        raise ConnectionError("Server unavailable")
    monkeypatch.setattr("seatplan.mail.deliver", fail)
    assert process_one(app.state.db, settings)
    with app.state.db.read() as connection:
        row = connection.execute("SELECT * FROM outbox WHERE id=?", (mid,)).fetchone()
    assert row["status"] == "queued" and row["attempts"] == 1 and row["available"] > time.time()
    assert row["last_error"] == "ConnectionError"
    with app.state.db.transaction() as connection:
        expired = enqueue(connection, "guest@example.org", "Your Seatplan sign-in link", "secret token")
        connection.execute("UPDATE outbox SET created=0 WHERE id=?", (expired,))
    housekeeping(app.state.db)
    with app.state.db.read() as connection:
        row = connection.execute("SELECT * FROM outbox WHERE id=?", (expired,)).fetchone()
    assert row["status"] == "failed" and row["body"] == ""
