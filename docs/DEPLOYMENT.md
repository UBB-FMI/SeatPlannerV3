# Deployment

## Components

Run one or more HTTP web processes and at least one worker, all with the same configuration and local data directory. The web process accepts requests and records jobs/outbox messages; the worker sends email and runs PDF subprocesses. A stopped worker does not automatically stop reservations, so monitor its heartbeat and delivery queue. Do not launch public bookings while email delivery is unhealthy.

The default Compose file uses two web workers in one container, one background worker, one named persistent volume, loopback host port 8000, read-only root filesystems, non-root UID/GID 10001, temporary `/tmp`, dropped capabilities and no-new-privileges. The container runs HTTP only. Check ownership when replacing the named volume with a host bind mount (`10001:10001`). Do not expose `/data` as a web directory.

Both services may initialize the database; schema initialization is idempotent for version 1. A database with a newer schema version is rejected. Upgrade by backing up, stopping services, updating the source/image, running tests in staging and restarting. There is no general migration framework for future arbitrary schema changes yet.

## Native Debian/Linux installation

Adjust paths and Python interpreter to your environment:

```sh
sudo useradd --system --home /opt/seatplan --shell /usr/sbin/nologin seatplan
sudo mkdir -p /opt/seatplan /var/lib/seatplan
# Copy the extracted source into /opt/seatplan, then:
sudo chown -R seatplan:seatplan /opt/seatplan /var/lib/seatplan
cd /opt/seatplan
sudo -u seatplan python3 -m venv .venv
sudo -u seatplan .venv/bin/pip install -r requirements.txt
sudo -u seatplan cp .env.example .env
# Edit .env with production settings before starting either service.
sudo chmod 600 .env
sudo cp deploy/seatplan-web.service deploy/seatplan-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seatplan-web seatplan-worker
sudo journalctl -u seatplan-web -u seatplan-worker -n 100 --no-pager
```

The examples use `/var/lib/seatplan` for data and `/opt/seatplan/.env` as the shared environment file. Systemd parses that file directly; the Python app does not load dotenv files itself. For console commands in a shell, export the same variables. The example file's values are shell-compatible when correctly quoted; do not `source` an untrusted file. With SMTP passwords containing shell-special characters, use appropriate quotes or a controlled environment loader rather than an unsafe substitution.

## Reverse proxy

Use `deploy/apache.conf` or `deploy/nginx.conf`. Add these inside the virtual host that already terminates TLS. Do not create a second TLS terminator inside the app. Uvicorn must remain `--no-proxy-headers`; the configured `PUBLIC_URL` is authoritative. The app does not depend on trusting `X-Forwarded-Proto` for links or cookies.

The `/seats/` examples preserve the prefix when forwarding. This is required for static files under the configured FastAPI root path. Static files, API paths, mail URLs and cookies are generated using that prefix. Preserve query strings, forward the browser Origin header, and do not cache authenticated content. No WebSocket support is required; the visitor view polls availability every seven seconds. Admin snapshots refresh on demand and reject stale overrides.

For IP rate limiting, overwrite `X-Real-IP` in the **last trusted proxy** and set only its actual peer CIDR in `TRUSTED_PROXY_CIDRS`. The app deliberately ignores that header from untrusted peers. A shared default bucket behind the proxy can otherwise throttle multiple visitors together. Do not use `0.0.0.0/0`, and do not make the upstream publicly reachable merely to simplify proxying.

## SMTP

Set `SMTP_SECURITY=starttls` for an explicit TLS upgrade, `ssl` for an implicit TLS connection, or `plain` only for a trusted unauthenticated relay. TLS uses the standard certificate-verifying trust store; no insecure skip-verification flag is provided. Authenticated plaintext is rejected. For an internal CA, install the CA into the OS/container trust store rather than disabling verification.

`MAIL_FROM` must be a sender accepted by your relay. The app has no access to your credentials until you configure them. Validate a real sign-in and reservation message through your SMTP system, including spam-folder delivery and the correctness of the external link.

The durable outbox uses an at-least-once delivery design. A crash after SMTP acceptance but before recording success can result in a duplicate; Message-ID stays stable for retries, but that does not guarantee deduplication by mail clients. Retry backoff has up to eight attempts and a one-hour cap. Admins can retry queued/failed messages that still have bodies. Expired sign-in messages require a fresh sign-in request.

## Resource bounds and storage

Defaults are intended for a single hall/small set of events, not high-traffic ticket-sale bursts. SQLite writers are serialized before availability is checked. All web processes must share the same local file and assets. Do not use network filesystems or copy the database for active-active deployment. A relational server database can be added later, but that requires transaction and deployment changes.

PDFs are uploaded by administrators only. The browser receives raster images, not embedded active PDFs. Rendering/detection use a separate process with a 65-second wall timeout; Linux limits include 45/50 seconds CPU, 2 GiB address space and 200 MiB output file size. This limits resource consumption but does not replace OS isolation or a security-reviewed document sandbox. Apply host/container memory/CPU limits appropriate to the server and keep dependencies updated.

The exact pins are versions exercised locally, not a claim that no newer security patches exist. The Dockerfile was supplied but not built in the offline test environment. Validate the build and dependency installation on the deployment host before enabling traffic.
