from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from .config import load_settings
from .db import Database, json_dump
from .detection import detect_seats, render_pdf
from .domain import DetectSpec, new_id
from .services import audit


def seed_sample(db: Database, data_dir: Path, fresh: bool = False) -> str:
    examples = Path(__file__).resolve().parent.parent / "examples"
    source = examples / "sample-hall.pdf"
    profile = json.loads((examples / "sample-exclusions.json").read_text())
    if hashlib.sha256(source.read_bytes()).hexdigest() != profile["pdf_sha256"]:
        raise ValueError("Sample PDF checksum does not match its annotated masks.")
    with db.read() as connection:
        existing = connection.execute("SELECT id FROM plans WHERE sha256=?", (profile["pdf_sha256"],)).fetchone()
    if existing and fresh is False:
        print("The sample is already imported; leaving it unchanged.")
        return existing["id"]
    pid = new_id()
    folder = data_dir / "assets" / pid
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, folder / "source.pdf")
    pages = render_pdf(folder / "source.pdf", folder)
    for page in pages:
        page["asset"] = str((folder / page["filename"]).relative_to(data_dir))
    result = detect_seats(folder / pages[0]["filename"], DetectSpec(), profile["zones"], folder / "source.pdf")
    with db.transaction() as connection:
        connection.execute("INSERT INTO plans(id,name,pdf_path,sha256,pages,zones,state,notes,created) VALUES(?,?,?,?,?,?,'draft',?,?)", (pid, "Supplied hall · review draft", str((folder / "source.pdf").relative_to(data_dir)), profile["pdf_sha256"], json_dump(pages), json_dump(profile["zones"]), "Scanned PDF. Provisional D-labels; manual sample exclusion masks. Detection is incomplete. Verify every seat, section, row, number and crossed-out area before publication. No event has been opened.", time.time()))
        for seat in result["seats"]:
            seat["id"] = new_id()  # Seat IDs are global; separate drafts must not collide.
            connection.execute("INSERT INTO seats VALUES(?,?,?,?,?,?)", (seat["id"], pid, seat["section"], seat["row"], seat["label"], json_dump(seat)))
        audit(connection, "console", "sample.seed", pid, result["report"])
    print(json.dumps(result["report"], indent=2))
    print("Imported as DRAFT; no candidate is approved and no reservations are open.")
    return pid


def backup(db: Database, data_dir: Path, output: Path) -> None:
    """SQLite online backup; assets are immutable and copied after its snapshot."""
    output = output.resolve()
    with tempfile.TemporaryDirectory() as temporary:
        destination = Path(temporary) / "seatplan.sqlite3"
        with db.read() as connection, sqlite3.connect(destination) as copy:
            connection.backup(copy)
        with tarfile.open(output, "w:gz") as archive:
            archive.add(destination, arcname="seatplan.sqlite3")
            archive.add(data_dir / "assets", arcname="assets")
    output.chmod(0o600)
    print(f"Backup written to {output}. Keep .env / APP_SECRET separately and securely.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seatplan administration utilities")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sample = sub.add_parser("seed-sample")
    sample.add_argument("--new", action="store_true", help="Create a separate draft using the current detector; preserve existing plans and bookings.")
    command = sub.add_parser("backup")
    command.add_argument("output", type=Path)
    args = parser.parse_args()
    settings = load_settings()
    settings.prepare()
    db = Database(settings.db_path)
    db.initialize()
    if args.command == "seed-sample":
        print("Plan ID:", seed_sample(db, settings.data_dir, fresh=args.new))
    elif args.command == "backup":
        backup(db, settings.data_dir, args.output)
    else:
        print(f"Database ready: {settings.db_path}")


if __name__ == "__main__":
    main()
