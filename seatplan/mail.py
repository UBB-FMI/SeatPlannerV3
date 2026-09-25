from __future__ import annotations

import os
import smtplib
import sqlite3
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate

from .config import Settings
from .db import Database
from .domain import new_id


def enqueue(connection: sqlite3.Connection, recipient: str, subject: str, body: str) -> str:
    mail_id = new_id()
    connection.execute("INSERT INTO outbox(id,recipient,subject,body,available,created) VALUES(?,?,?,?,?,?)", (mail_id, recipient, subject, body, time.time(), time.time()))
    return mail_id


def deliver(settings: Settings, message: sqlite3.Row) -> None:
    mail = EmailMessage()
    mail["From"] = settings.mail_from or "Seatplan <noreply@example.test>"
    mail["To"] = message["recipient"]
    mail["Subject"] = message["subject"]
    mail["Date"] = formatdate(message["created"], usegmt=True)
    # Stable across retries, including a crash after SMTP accepted a message.
    mail["Message-ID"] = f"<{message['id']}@seatplan.local>"
    mail.set_content(message["body"])
    if settings.mail_backend == "file":
        path = settings.data_dir / "dev-mail" / f"{message['id']}.eml"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(mail.as_bytes())
        return
    context = ssl.create_default_context()
    if settings.smtp_security == "ssl":
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=context)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
    with server:
        server.ehlo()
        if settings.smtp_security == "starttls":
            server.starttls(context=context)
            server.ehlo()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(mail)


def process_one(db: Database, settings: Settings) -> bool:
    now = time.time()
    with db.transaction() as connection:
        connection.execute("UPDATE outbox SET status='queued', lease=NULL WHERE status='sending' AND lease<?", (now,))
        row = connection.execute("SELECT * FROM outbox WHERE status='queued' AND available<=? ORDER BY created LIMIT 1", (now,)).fetchone()
        if row is None:
            return False
        connection.execute("UPDATE outbox SET status='sending', attempts=attempts+1, lease=? WHERE id=?", (now + 120, row["id"]))
    try:
        deliver(settings, row)
    except Exception as exc:
        attempts = row["attempts"] + 1
        # Do not retain server strings that may contain recipient details or credentials.
        error = type(exc).__name__
        with db.transaction() as connection:
            connection.execute(
                "UPDATE outbox SET status=?, available=?, lease=NULL, last_error=? WHERE id=?",
                ("failed" if attempts >= 8 else "queued", time.time() + min(3600, 15 * 2 ** attempts), error, row["id"]),
            )
    else:
        with db.transaction() as connection:
            # Purge email bodies after successful send, especially login tokens.
            connection.execute("UPDATE outbox SET status='sent', sent=?, lease=NULL, body='', last_error='' WHERE id=?", (time.time(), row["id"]))
    return True
