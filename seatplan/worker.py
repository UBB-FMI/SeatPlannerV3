from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import Settings, load_settings
from .db import Database, json_dump
from .mail import process_one as process_mail


def process_job(db: Database, settings: Settings) -> bool:
    now = time.time()
    with db.transaction() as connection:
        connection.execute("UPDATE jobs SET status='queued',lease=NULL WHERE status='running' AND lease<?", (now,))
        row = connection.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        if row is None:
            return False
        connection.execute("UPDATE jobs SET status='running',lease=? WHERE id=?", (now + 120, row["id"]))
    job_id = row["id"]
    input_path = settings.data_dir / "jobs" / f"{job_id}-input.json"
    output_path = settings.data_dir / "jobs" / f"{job_id}-output.json"
    input_path.write_text(row["params"])
    result = None
    error = ""
    try:
        environment = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
        process = subprocess.run([sys.executable, "-m", "seatplan.pdf_job", str(input_path), str(output_path)], timeout=210, capture_output=True, env=environment, check=False)
        if process.returncode != 0 or not output_path.exists():
            error = "PDF worker failed or exceeded its resource limit. Try a smaller PDF / detection region."
        else:
            output = json.loads(output_path.read_text())
            if output["ok"]:
                result = output["result"]
            else:
                error = output["error"]
    except subprocess.TimeoutExpired:
        error = "PDF operation timed out after 65 seconds. Try a smaller file or region."
    except Exception as exc:
        error = f"Worker error: {type(exc).__name__}"
    with db.transaction() as connection:
        connection.execute("UPDATE jobs SET status=?,result=?,error=?,lease=NULL WHERE id=?", ("failed" if error else "done", json_dump(result) if result is not None else None, error, job_id))
        if row["kind"] == "render":
            params = json.loads(row["params"])
            if error:
                connection.execute("UPDATE plans SET state='failed',notes=? WHERE id=?", (error, row["plan_id"]))
            else:
                for page in result["pages"]:
                    page["asset"] = str((Path(params["output_dir"]) / page["filename"]).relative_to(settings.data_dir))
                connection.execute("UPDATE plans SET state='draft',pages=?,revision=revision+1 WHERE id=?", (json_dump(result["pages"]), row["plan_id"]))
    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)
    return True


def housekeeping(db: Database) -> None:
    now = time.time()
    with db.transaction() as connection:
        connection.execute("INSERT INTO heartbeat VALUES('worker',?) ON CONFLICT(name) DO UPDATE SET updated=excluded.updated", (now,))
        connection.execute("DELETE FROM sessions WHERE expires<?", (now,))
        connection.execute("DELETE FROM login_tokens WHERE expires<?", (now - 3600,))
        connection.execute("DELETE FROM rate_limits WHERE expires<?", (now,))
        # Expired login emails must not be delivered much later as misleading links.
        connection.execute("UPDATE outbox SET status='failed',body='',last_error='Login link expired' WHERE status IN ('queued','failed') AND subject='Your Seatplan sign-in link' AND created<?", (now - 900,))
        connection.execute("DELETE FROM outbox WHERE status='sent' AND sent<?", (now - 30 * 86400,))
        connection.execute("DELETE FROM jobs WHERE created<? AND status IN ('done','failed')", (now - 7 * 86400,))


def main() -> None:
    parser = argparse.ArgumentParser(description="Process Seatplan emails and PDF jobs.")
    parser.add_argument("--once", action="store_true", help="Process available work and exit.")
    args = parser.parse_args()
    settings = load_settings()
    settings.prepare()
    db = Database(settings.db_path)
    db.initialize()
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            housekeeping(db)
            did_work = process_mail(db, settings)
            did_work = process_job(db, settings) or did_work
            if args.once and did_work is False:
                break
            if did_work is False:
                time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception:
            logging.exception("Worker iteration failed; retrying in five seconds.")
            if args.once:
                raise
            time.sleep(5)


if __name__ == "__main__":
    main()
