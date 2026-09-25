from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from .config import Settings, load_settings
from .db import Database, json_dump
from .domain import BookRequest, CancelRequest, ConfirmRequest, DetectSpec, EmailRequest, EventEdit, GridSpec, OverrideRequest, PlanSave, StrictModel, build_grid, new_id
from .mail import enqueue
from .security import client_ip, create_session, digest, ensure_user, get_identity, rate_limit, require_identity
from .services import audit, book, booking_notice, cancel_booking, clone_plan, event_snapshot, get_event, get_plan, override, plan_seats, plan_summary, publish_plan, save_event, save_plan, seat_name


class RevisionRequest(StrictModel):
    revision: int = Field(ge=1)


class SafetyMiddleware:
    """Bound request bodies *before* FastAPI parses them; reject cross-origin writes."""
    def __init__(self, app: Callable, settings: Settings, db: Database) -> None:
        self.app, self.settings, self.db = app, settings, db

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
        mutating = scope["method"] not in {"GET", "HEAD", "OPTIONS"}
        if mutating and headers.get("origin") != self.settings.origin:
            await JSONResponse({"detail": "Invalid request origin. Check PUBLIC_URL."}, 403)(scope, receive, send)
            return
        body = bytearray()
        path = scope["path"].removeprefix(self.settings.base_path) if self.settings.base_path else scope["path"]
        admin_write = mutating and path.startswith("/api/admin/")
        if admin_write:
            # Reject unauthorized large uploads before reading or buffering them.
            try:
                await run_in_threadpool(require_identity, Request(scope), self.db, self.settings, admin=True)
            except HTTPException as exc:
                await JSONResponse({"detail": exc.detail}, exc.status_code, headers=exc.headers)(scope, receive, send)
                return
        if mutating:
            limit = (self.settings.max_upload_mb * 1024**2) if path == "/api/admin/plans/upload" else (8 * 1024**2 if admin_write else 64 * 1024)
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > limit:
                    await JSONResponse({"detail": "Request body exceeds the configured size limit."}, 413)(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
        delivered = False

        async def buffered_receive() -> dict:
            nonlocal delivered
            if mutating and delivered is False:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def secured_send(message: dict) -> None:
            if message["type"] == "http.response.start":
                security_headers = {
                    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                    "x-content-type-options": "nosniff", "x-frame-options": "DENY",
                    "referrer-policy": "no-referrer", "permissions-policy": "camera=(), microphone=(), geolocation=()",
                }
                if "/static/" not in scope["path"]:
                    security_headers["cache-control"] = "no-store, private"
                message["headers"] += [(key.encode(), value.encode()) for key, value in security_headers.items()]
            await send(message)

        await self.app(scope, buffered_receive, secured_send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.prepare()
    db = Database(settings.db_path)
    db.initialize()
    app = FastAPI(title="Seatplan", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None, root_path=settings.base_path, redirect_slashes=False)
    app.state.db, app.state.settings = db, settings
    app.add_middleware(SafetyMiddleware, settings=settings, db=db)
    package = Path(__file__).parent
    templates = Environment(loader=FileSystemLoader(package / "templates"), autoescape=select_autoescape(["html"]))
    app.mount("/static", StaticFiles(directory=package / "static"), name="static")

    @app.exception_handler(sqlite3.OperationalError)
    async def database_busy(request: Request, error: sqlite3.OperationalError) -> JSONResponse:
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            return JSONResponse({"detail": "The database is busy. Retry the same operation in a moment."}, 503, headers={"Retry-After": "2"})
        raise error

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(templates.get_template("index.html").render(base=settings.base_path))

    @app.get("/healthz")
    def health() -> dict:
        with db.read() as connection:
            connection.execute("SELECT 1")
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request) -> JSONResponse:
        identity = get_identity(request, db, settings)
        response = JSONResponse({})
        if identity is None:
            with db.transaction() as connection:
                rate_limit(connection, settings, "session", client_ip(request, settings), 500, 3600)
                identity = create_session(connection, settings, response)
        response.body = json.dumps({"email": identity.email, "admin": identity.admin, "csrf": identity.csrf, "development": settings.development}).encode()
        response.headers["content-length"] = str(len(response.body))
        return response

    @app.post("/api/auth/request")
    def request_login(payload: EmailRequest, request: Request) -> dict:
        require_identity(request, db, settings, signed_in=False)
        with db.transaction() as connection:
            rate_limit(connection, settings, "login-email", payload.email, 5, 3600)
            rate_limit(connection, settings, "login-ip", client_ip(request, settings), 60, 900)
            token = secrets.token_urlsafe(32)
            connection.execute("INSERT INTO login_tokens VALUES(?,?,?,NULL,?)", (digest(settings, token), payload.email, time.time() + 900, time.time()))
            # URL fragment avoids token leakage to HTTP logs. The browser requires an explicit POST.
            link = f"{settings.public_url}/#confirm={token}"
            enqueue(connection, payload.email, "Your Seatplan sign-in link", f"Open this link and press Confirm sign-in:\n\n{link}\n\nIt expires in 15 minutes and can be used once.\nDo not forward this email.\nIf you did not request it, ignore this message.\n")
        return {"message": "A sign-in email has been queued. Check your inbox and spam folder."}

    @app.post("/api/auth/confirm")
    def confirm_login(payload: ConfirmRequest, request: Request) -> JSONResponse:
        current = require_identity(request, db, settings, signed_in=False)
        response = JSONResponse({"ok": True})
        with db.transaction() as connection:
            rate_limit(connection, settings, "confirm", client_ip(request, settings), 80, 900)
            token = connection.execute("SELECT * FROM login_tokens WHERE token_hash=?", (digest(settings, payload.token),)).fetchone()
            if token is None or token["consumed"] is not None or token["expires"] <= time.time():
                raise HTTPException(400, "This sign-in link has expired or was already used. Request a new one.")
            connection.execute("UPDATE login_tokens SET consumed=? WHERE token_hash=?", (time.time(), token["token_hash"]))
            uid = ensure_user(connection, token["email"])
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (current.session_hash,))
            create_session(connection, settings, response, uid)
            audit(connection, token["email"], "auth.sign_in", uid, {})
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request) -> JSONResponse:
        identity = require_identity(request, db, settings, signed_in=False)
        response = JSONResponse({"ok": True})
        with db.transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (identity.session_hash,))
        response.delete_cookie("seatplan_session", path=settings.cookie_path, secure=settings.secure_cookies, httponly=True, samesite="lax")
        return response

    @app.get("/api/events")
    def events() -> dict:
        with db.read() as connection:
            return {"events": [dict(row) for row in connection.execute("SELECT * FROM events ORDER BY created DESC")]}

    @app.get("/api/events/{event_id}")
    def event(event_id: str, request: Request) -> dict:
        identity = get_identity(request, db, settings)
        with db.read() as connection:
            connection.execute("BEGIN")
            return event_snapshot(connection, event_id, identity)

    @app.post("/api/events/{event_id}/book")
    def book_seats(event_id: str, payload: BookRequest, request: Request) -> dict:
        identity = require_identity(request, db, settings)
        with db.transaction() as connection:
            rate_limit(connection, settings, "booking", identity.user_id or "", 60, 600)
        return book(db, settings, event_id, payload, identity)

    @app.get("/api/bookings")
    def my_bookings(request: Request) -> dict:
        identity = require_identity(request, db, settings)
        with db.read() as connection:
            connection.execute("BEGIN")
            bookings = []
            for row in connection.execute("SELECT b.id,b.event_id,b.created,e.title,e.starts_at FROM bookings b JOIN events e ON e.id=b.event_id WHERE b.user_id=? ORDER BY b.created DESC", (identity.user_id,)):
                item = dict(row)
                item["seats"] = [json.loads(seat[0]) for seat in connection.execute("SELECT s.payload FROM allocations a JOIN seats s ON s.id=a.seat_id WHERE a.booking_id=? ORDER BY s.section,s.row_name,s.label", (row["id"],))]
                # Do not expose internal admin notes even to the booking owner.
                for seat in item["seats"]:
                    seat.pop("note", None)
                bookings.append(item)
            return {"bookings": bookings}

    @app.post("/api/bookings/{booking_id}/cancel")
    def cancel(booking_id: str, payload: CancelRequest, request: Request) -> dict:
        identity = require_identity(request, db, settings)
        return cancel_booking(db, settings, booking_id, payload.seats, identity)

    @app.get("/media/{plan_id}/{page_index}.png")
    def page_image(plan_id: str, page_index: int, request: Request) -> FileResponse:
        with db.read() as connection:
            plan = get_plan(connection, plan_id)
        if plan["state"] != "published":
            require_identity(request, db, settings, admin=True)
        if page_index < 0 or page_index >= len(plan["pages"]):
            raise HTTPException(404, "PDF page not found.")
        return FileResponse(settings.data_dir / plan["pages"][page_index]["asset"], media_type="image/png")

    @app.get("/api/admin/plans")
    def admin_plans(request: Request) -> dict:
        require_identity(request, db, settings, admin=True)
        with db.read() as connection:
            plans = []
            for row in connection.execute("SELECT id FROM plans ORDER BY created DESC"):
                plan = get_plan(connection, row["id"])
                plan.pop("pdf_path", None)
                plan["summary"] = plan_summary(connection, plan)
                plans.append(plan)
        return {"plans": plans}

    @app.post("/api/admin/plans/upload")
    async def upload_plan(request: Request, name: str = Query(min_length=1, max_length=160)) -> dict:
        identity = require_identity(request, db, settings, admin=True)
        if request.headers.get("content-type", "").split(";")[0] != "application/pdf":
            raise HTTPException(415, "Upload the PDF bytes with Content-Type: application/pdf.")
        body = await request.body()
        if not body.startswith(b"%PDF-"):
            raise HTTPException(422, "This file does not have a PDF header.")
        if len(body) > settings.max_upload_mb * 1024**2:
            raise HTTPException(413, "The PDF exceeds the upload limit.")
        plan_id, job_id = new_id(), new_id()
        folder = settings.data_dir / "assets" / plan_id
        folder.mkdir(mode=0o700)
        pdf_path = folder / "source.pdf"
        pdf_path.write_bytes(body)
        sha = hashlib.sha256(body).hexdigest()
        zones, notes = [], ""
        example = package.parent / "examples" / "sample-exclusions.json"
        if example.exists():
            profile = json.loads(example.read_text())
            if profile["pdf_sha256"] == sha:
                zones = profile["zones"]
                notes = "Checksum-matched, manually annotated starter exclusions applied for the supplied sample. Review the entire scan, numbering, and exclusions before publication."
        params = {"kind": "render", "pdf_path": str(pdf_path), "output_dir": str(folder), "max_pages": settings.max_pages}
        with db.transaction() as connection:
            connection.execute("INSERT INTO plans(id,name,pdf_path,sha256,zones,notes,created) VALUES(?,?,?,?,?,?,?)", (plan_id, name.strip(), str(pdf_path.relative_to(settings.data_dir)), sha, json_dump(zones), notes, time.time()))
            connection.execute("INSERT INTO jobs(id,plan_id,kind,params,created) VALUES(?,?,'render',?,?)", (job_id, plan_id, json_dump(params), time.time()))
            audit(connection, identity.email or "", "plan.upload", plan_id, {"sha256": sha, "bytes": len(body)})
        return {"id": plan_id, "job_id": job_id}

    @app.get("/api/admin/plans/{plan_id}")
    def admin_plan(plan_id: str, request: Request) -> dict:
        require_identity(request, db, settings, admin=True)
        with db.read() as connection:
            connection.execute("BEGIN")
            plan = get_plan(connection, plan_id)
            plan.pop("pdf_path", None)
            plan["seats"] = plan_seats(connection, plan_id)
        return plan

    @app.put("/api/admin/plans/{plan_id}")
    def update_plan(plan_id: str, payload: PlanSave, request: Request) -> dict:
        identity = require_identity(request, db, settings, admin=True)
        return save_plan(db, plan_id, payload, identity.email or "")

    @app.post("/api/admin/plans/{plan_id}/publish")
    def publish(plan_id: str, payload: RevisionRequest, request: Request) -> dict:
        identity = require_identity(request, db, settings, admin=True)
        return publish_plan(db, plan_id, payload.revision, identity.email or "")

    @app.post("/api/admin/plans/{plan_id}/clone")
    def clone(plan_id: str, request: Request) -> dict:
        identity = require_identity(request, db, settings, admin=True)
        return clone_plan(db, plan_id, identity.email or "")

    @app.post("/api/admin/plans/{plan_id}/grid")
    def grid(plan_id: str, payload: GridSpec, request: Request) -> dict:
        require_identity(request, db, settings, admin=True)
        with db.read() as connection:
            plan = get_plan(connection, plan_id)
        if plan["state"] != "draft" or payload.page >= len(plan["pages"]):
            raise HTTPException(422, "Choose a page in a draft plan.")
        page = plan["pages"][payload.page]
        try:
            seats = build_grid(payload, page["width"], page["height"])
        except ValueError as exc:
            raise HTTPException(422, "The grid extends beyond the page. Reduce its size or rotation.") from exc
        return {"seats": seats}

    @app.post("/api/admin/plans/{plan_id}/detect")
    def detect(plan_id: str, payload: DetectSpec, request: Request) -> dict:
        identity = require_identity(request, db, settings, admin=True)
        with db.transaction() as connection:
            plan = get_plan(connection, plan_id)
            if plan["state"] != "draft" or payload.page >= len(plan["pages"]):
                raise HTTPException(422, "Detection requires a rendered draft page.")
            pending = connection.execute("SELECT COUNT(*) FROM jobs WHERE plan_id=? AND status IN ('queued','running')", (plan_id,)).fetchone()[0]
            if pending >= 3:
                raise HTTPException(429, "Wait for this plan's current PDF jobs to finish.")
            job_id = new_id()
            params = {"kind": "detect", "pdf_path": str(settings.data_dir / plan["pdf_path"]), "image_path": str(settings.data_dir / plan["pages"][payload.page]["asset"]), "spec": payload.model_dump(), "zones": plan["zones"]}
            connection.execute("INSERT INTO jobs(id,plan_id,kind,params,created) VALUES(?,?,'detect',?,?)", (job_id, plan_id, json_dump(params), time.time()))
            audit(connection, identity.email or "", "plan.detect", plan_id, payload.model_dump())
        return {"job_id": job_id}

    @app.get("/api/admin/jobs/{job_id}")
    def job_status(job_id: str, request: Request) -> dict:
        require_identity(request, db, settings, admin=True)
        with db.read() as connection:
            row = connection.execute("SELECT id,plan_id,kind,status,result,error,created FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Job not found or already expired.")
        result = dict(row)
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    @app.get("/api/admin/plans/{plan_id}/export")
    def export_plan(plan_id: str, request: Request) -> Response:
        require_identity(request, db, settings, admin=True)
        with db.read() as connection:
            connection.execute("BEGIN")
            plan = get_plan(connection, plan_id)
            plan["seats"] = plan_seats(connection, plan_id)
        plan.pop("pdf_path", None)
        for page in plan["pages"]:
            page.pop("asset", None)
        return Response(json_dump({"format": "seatplan-overlay-v1", "plan": plan}), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="seatplan-{plan_id}.json"'})

    @app.get("/api/admin/plans/{plan_id}/source")
    def source_pdf(plan_id: str, request: Request) -> FileResponse:
        require_identity(request, db, settings, admin=True)
        with db.read() as connection:
            plan = get_plan(connection, plan_id)
        return FileResponse(settings.data_dir / plan["pdf_path"], media_type="application/pdf", filename=f"seatplan-{plan_id}.pdf")

    @app.post("/api/admin/events")
    def create_event(payload: EventEdit, request: Request) -> dict:
        identity = require_identity(request, db, settings, admin=True)
        return save_event(db, settings, payload, identity.email or "")

    @app.put("/api/admin/events/{event_id}")
    def edit_event(event_id: str, payload: EventEdit, request: Request) -> dict:
        identity = require_identity(request, db, settings, admin=True)
        return save_event(db, settings, payload, identity.email or "", event_id)

    @app.get("/api/admin/events/{event_id}")
    def live_event(event_id: str, request: Request) -> dict:
        identity = require_identity(request, db, settings, admin=True)
        with db.read() as connection:
            connection.execute("BEGIN")
            return event_snapshot(connection, event_id, identity, admin=True)

    @app.post("/api/admin/events/{event_id}/override")
    def admin_override(event_id: str, payload: OverrideRequest, request: Request) -> dict:
        identity = require_identity(request, db, settings, admin=True)
        return override(db, settings, event_id, payload, identity)

    @app.get("/api/admin/events/{event_id}/csv")
    def export_csv(event_id: str, request: Request) -> Response:
        identity = require_identity(request, db, settings, admin=True)
        with db.read() as connection:
            connection.execute("BEGIN")
            snapshot = event_snapshot(connection, event_id, identity, admin=True)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["Section", "Row", "Seat", "State", "Email", "Reservation", "Admin note"])
        def safe(value: object) -> str:
            text = str(value or "")
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text
        for seat in snapshot["seats"]:
            allocation = seat.get("allocation", {})
            writer.writerow([safe(value) for value in [seat["section"], seat["row"], seat["label"], seat["status"], allocation.get("email", ""), allocation.get("booking_id", ""), allocation.get("note", "")]])
        return Response("\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="seating-{event_id}.csv"'})

    @app.get("/print/{event_id}", response_class=HTMLResponse)
    def print_event(event_id: str, request: Request, roster: bool = False, labels: bool = False) -> HTMLResponse:
        identity = require_identity(request, db, settings, admin=True)
        with db.read() as connection:
            connection.execute("BEGIN")
            snapshot = event_snapshot(connection, event_id, identity, admin=True)
        return HTMLResponse(templates.get_template("print.html").render(base=settings.base_path, snapshot=snapshot, roster=roster, labels=labels, generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")))

    @app.get("/api/admin/system")
    def system_status(request: Request) -> dict:
        require_identity(request, db, settings, admin=True)
        with db.read() as connection:
            counts = {row[0]: row[1] for row in connection.execute("SELECT status,COUNT(*) FROM outbox GROUP BY status")}
            heartbeat = connection.execute("SELECT updated FROM heartbeat WHERE name='worker'").fetchone()
            messages = [dict(row) for row in connection.execute("SELECT id,recipient,subject,status,attempts,last_error,created FROM outbox ORDER BY created DESC LIMIT 100")]
            jobs = [dict(row) for row in connection.execute("SELECT id,kind,status,error,created FROM jobs ORDER BY created DESC LIMIT 30")]
            log = [dict(row) for row in connection.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]
        return {"mail_counts": counts, "messages": messages, "jobs": jobs, "audit": log, "worker_age_seconds": round(time.time() - heartbeat[0], 1) if heartbeat else None, "mail_backend": settings.mail_backend}

    @app.post("/api/admin/mail/{mail_id}/retry")
    def retry_mail(mail_id: str, request: Request) -> dict:
        identity = require_identity(request, db, settings, admin=True)
        with db.transaction() as connection:
            row = connection.execute("SELECT status,body FROM outbox WHERE id=?", (mail_id,)).fetchone()
            if row is None or row["status"] not in {"failed", "queued"} or not row["body"]:
                raise HTTPException(409, "This email cannot be retried. Request a new sign-in link for expired messages.")
            connection.execute("UPDATE outbox SET status='queued',attempts=0,available=?,lease=NULL,last_error='' WHERE id=?", (time.time(), mail_id))
            audit(connection, identity.email or "", "mail.retry", mail_id, {})
        return {"queued": True}

    return app
