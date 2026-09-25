from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import sqlite3
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response

from .config import Settings
from .db import Database
from .domain import new_id


@dataclass(frozen=True)
class Identity:
    session_hash: str
    csrf: str
    user_id: str | None
    email: str | None
    admin: bool


def digest(settings: Settings, value: str) -> str:
    return hmac.new(settings.secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def client_ip(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        networks = [ipaddress.ip_network(item) for item in settings.trusted_proxy_cidrs]
        trusted = any(ipaddress.ip_address(peer) in network for network in networks)
        if trusted:
            # The proxy MUST overwrite, not append to, this single-valued header.
            forwarded = request.headers.get("x-real-ip", "")
            if forwarded:
                return str(ipaddress.ip_address(forwarded))
    except ValueError:
        pass
    return peer


def get_identity(request: Request, db: Database, settings: Settings) -> Identity | None:
    cookie = request.cookies.get("seatplan_session", "")
    if len(cookie) < 30 or len(cookie) > 150:
        return None
    session_hash = digest(settings, cookie)
    with db.read() as connection:
        row = connection.execute(
            "SELECT s.*, u.email FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?",
            (session_hash, time.time()),
        ).fetchone()
    if row is None:
        return None
    return Identity(session_hash, row["csrf"], row["user_id"], row["email"], row["email"] in settings.admin_emails)


def require_identity(request: Request, db: Database, settings: Settings, admin: bool = False, signed_in: bool = True) -> Identity:
    identity = get_identity(request, db, settings)
    if identity is None or (signed_in and identity.user_id is None):
        raise HTTPException(401, "Please sign in with your email first.")
    if admin and identity.admin is False:
        raise HTTPException(403, "Administrator access is required.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get("origin") != settings.origin:
            raise HTTPException(403, "Invalid request origin. Check PUBLIC_URL and your reverse proxy.")
        csrf = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(identity.csrf, csrf):
            raise HTTPException(403, "Your session has changed. Reload the page and try again.")
    return identity


def create_session(connection: sqlite3.Connection, settings: Settings, response: Response, user_id: str | None = None) -> Identity:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    expires = time.time() + (settings.session_hours * 3600 if user_id else 3600)
    connection.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (digest(settings, token), user_id, csrf, expires, time.time()))
    response.set_cookie("seatplan_session", token, max_age=int(expires - time.time()), path=settings.cookie_path, secure=settings.secure_cookies, httponly=True, samesite="lax")
    email = None
    if user_id is not None:
        email = connection.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()[0]
    return Identity(digest(settings, token), csrf, user_id, email, email in settings.admin_emails)


def rate_limit(connection: sqlite3.Connection, settings: Settings, scope: str, value: str, maximum: int, seconds: int) -> None:
    now = time.time()
    bucket = int(now // seconds)
    key = digest(settings, f"{scope}:{value}:{bucket}")
    row = connection.execute("SELECT count FROM rate_limits WHERE key=?", (key,)).fetchone()
    if row is not None and row["count"] >= maximum:
        raise HTTPException(429, "Too many requests. Please wait before trying again.", headers={"Retry-After": str(seconds)})
    connection.execute("INSERT INTO rate_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1", (key, now + seconds * 2))


def ensure_user(connection: sqlite3.Connection, email: str) -> str:
    row = connection.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if row is not None:
        return row["id"]
    uid = new_id()
    connection.execute("INSERT INTO users VALUES(?,?,?)", (uid, email, time.time()))
    return uid
