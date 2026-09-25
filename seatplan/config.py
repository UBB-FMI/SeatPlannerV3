from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    public_url: str
    secret: str
    admin_emails: frozenset[str]
    development: bool = False
    mail_backend: str = "smtp"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"
    mail_from: str = ""
    trusted_proxy_cidrs: tuple[str, ...] = ()
    session_hours: int = 24
    max_upload_mb: int = 20
    max_pages: int = 10
    max_seats: int = 5000

    def __post_init__(self) -> None:
        parsed = urlsplit(self.public_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("PUBLIC_URL must be an absolute http(s) URL, with no query, credentials, or fragment.")
        if parsed.path and not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", parsed.path):
            raise ValueError("PUBLIC_URL's optional path must contain only simple path components, with no trailing slash.")
        if self.development is False and parsed.scheme != "https":
            raise ValueError("Production PUBLIC_URL must use HTTPS. The backend still receives HTTP.")
        if len(self.secret) < 32:
            raise ValueError("APP_SECRET must contain at least 32 random characters.")
        if self.mail_backend not in {"smtp", "file"}:
            raise ValueError("MAIL_BACKEND must be smtp or file.")
        if self.mail_backend == "file" and self.development is False:
            raise ValueError("File email delivery is permitted only in DEVELOPMENT=1.")
        if self.smtp_security not in {"starttls", "ssl", "plain"}:
            raise ValueError("SMTP_SECURITY must be starttls, ssl, or plain.")
        if self.mail_backend == "smtp" and (not self.smtp_host or not self.mail_from):
            raise ValueError("SMTP_HOST and MAIL_FROM are required for SMTP delivery.")
        if self.smtp_security == "plain" and self.smtp_password:
            raise ValueError("SMTP authentication over plaintext is disabled. Use TLS, or an unauthenticated trusted relay.")
        if not self.admin_emails:
            raise ValueError("Set ADMIN_EMAILS to at least one administrator email address.")
        if any("\n" in item or "\r" in item for item in [self.mail_from, self.smtp_host]):
            raise ValueError("Invalid mail configuration.")

    @property
    def origin(self) -> str:
        parsed = urlsplit(self.public_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def base_path(self) -> str:
        return urlsplit(self.public_url).path.rstrip("/")

    @property
    def cookie_path(self) -> str:
        return self.base_path + "/"

    @property
    def secure_cookies(self) -> bool:
        return urlsplit(self.public_url).scheme == "https"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "seatplan.sqlite3"

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("assets", "jobs", "dev-mail", "backups"):
            (self.data_dir / name).mkdir(exist_ok=True, mode=0o700)


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "./data")).resolve(),
        public_url=os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/"),
        secret=os.environ.get("APP_SECRET", ""),
        admin_emails=frozenset(x.strip().lower() for x in os.environ.get("ADMIN_EMAILS", "").split(",") if x.strip()),
        development=env_bool("DEVELOPMENT"),
        mail_backend=os.environ.get("MAIL_BACKEND", "smtp"),
        smtp_host=os.environ.get("SMTP_HOST", ""),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        smtp_security=os.environ.get("SMTP_SECURITY", "starttls"),
        mail_from=os.environ.get("MAIL_FROM", ""),
        trusted_proxy_cidrs=tuple(x.strip() for x in os.environ.get("TRUSTED_PROXY_CIDRS", "").split(",") if x.strip()),
        session_hours=int(os.environ.get("SESSION_HOURS", "24")),
        max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", "20")),
        max_pages=int(os.environ.get("MAX_PDF_PAGES", "10")),
    )
