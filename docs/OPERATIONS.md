# Operations and launch checklist

## Before opening reservations

Verify the real public HTTPS origin and subpath, secure session cookies, proxy access restrictions and correct trusted-proxy IP handling. Perform sign-in as an administrator and as an ordinary mailbox through the actual SMTP relay. Confirm that an ordinary user cannot access admin, draft plan, CSV or print routes. Test from a second browser profile.

Review every seat against the original PDF: correct section/row/number, no duplicate physical positions, no missing required seats, no false contours, all crossed-out positions excluded, mask boundaries correct. The sample draft is deliberately not published. Count the final overlay and actual available seats independently; neither the printed 928 nor the candidate count validates the inventory.

Book several seats, repeat a booking submission, try an already reserved seat from another account, cancel a seat, block/unblock a seat as admin and verify the resulting notifications. Print a full allocation and roster; check page boundaries and numbering. Change a plan on a closed test event and verify exact identity mapping. Configure limits and the event Open/Closed state deliberately.

Monitor worker heartbeat and mail queues, test a backup restoration, protect SMTP/application secrets, review dependency updates, and apply your institution's data-access/retention and incident procedures before Internet exposure. The delivered tests are not an independent penetration test or a guarantee of production readiness.

## Backup and restore

Create a consistent database snapshot plus PDF/page assets:

```sh
# Container backup is created within the persistent volume.
docker compose exec web python -m seatplan.cli backup /data/backups/seatplan-backup.tar.gz
docker compose cp web:/data/backups/seatplan-backup.tar.gz ./seatplan-backup.tar.gz
chmod 600 ./seatplan-backup.tar.gz
```

For a native installation, run the same CLI under the service account and correct environment. The SQLite backup API is used, not a bare copy of the live `.sqlite3` file that might omit WAL changes. Assets are immutable once a plan is ready. For the cleanest operational restore point, let PDF jobs finish before taking the backup.

Keep `.env` / `APP_SECRET` separately and securely. The backup contains email addresses, reservation information, audit data and potentially pending email bodies/tokens; treat it as sensitive. Backups are not automatically encrypted or uploaded off-site.

Restore into an empty data directory with the services stopped. Extract only a trusted backup, restore the correct service ownership/permissions, then restart. The archive contains `seatplan.sqlite3` and `assets/`; `prepare()` recreates the other working folders. Restore to the same absolute DATA_DIR path where possible, because queued PDF job parameters contain absolute paths. If moving to a different path, do not resume those jobs unchanged: mark stale queued/running jobs failed and re-upload processing PDFs or restart detection on existing rendered drafts. Published plan assets use relative paths and remain portable.

Use the same secret to retain outstanding sessions and verification links, or generate a new secret to intentionally invalidate them; users can request fresh sign-in links. After a restore, review pending outbox messages carefully before starting the worker: delivery recorded after the backup may otherwise repeat. Confirm `PRAGMA integrity_check`, plan media access, a sample reservation and worker heartbeat after restoration.

## Health and troubleshooting

`GET /healthz` checks database availability. It does not mean email delivery is working or a plan is reviewed. Administration → Delivery & audit shows worker heartbeat, latest PDF jobs, mail state and the latest 100 audit records. The database retains the complete audit trail; the app exposes no audit-deletion API.

- **Login mail never arrives:** check the worker process first, then its queue/error class, SMTP sender permissions, transport mode, DNS/connectivity and recipient spam folder. Verify PUBLIC_URL before resending. No live SMTP credentials were embedded in the source.
- **403 origin/session error:** verify the external PUBLIC_URL scheme/host/port/path, that the browser is using that exact URL, and that the proxy has not replaced Origin. Reload after sign-in/out to obtain the current CSRF token.
- **Everyone hits an IP rate limit:** configure the last proxy's exact peer CIDR and overwritten X-Real-IP header; otherwise all users can share the proxy's bucket.
- **PDF stays processing:** start the worker and inspect PDF jobs. Default page-count/upload/resource limits may reject the file. A corrupt/encrypted/incompatible PDF requires a clean replacement.
- **No or poor candidates:** tune sizes in rendered pixels; draw a smaller region; adjust closure/rectangularity; fall back to grids/manual seats. More candidates does not necessarily mean better detection.
- **Publish is refused:** review every shape and keep at least one reservable seat. Saving does not approve shapes.
- **Plan migration is refused:** resolve occupied identities missing in the new plan or newly excluded reservations. Do not relabel a different physical seat to force the migration through.
- **Override is stale:** refresh the live snapshot, inspect current owners and reselect the intended seats. Another reservation changed the revision; the stale operation was not applied.
- **Database busy:** reduce excessive concurrent writes, check slow storage and avoid network filesystems. A busy response may be retried; booking request keys provide idempotency.

## Data, access and retention

Only email addresses are required from visitors. No attendee emails/internal admin notes appear in the public map payload. Admin email allowlists are environment configuration, not a user-editable role field. Add or remove entries and restart web processes to change privileges. Keep administrator mailboxes well protected.

Successful email bodies are purged after delivery; sent-message metadata is removed after 30 days. Expired queued sign-in bodies are cleared after 15 minutes. Expired sessions and throttling records are cleaned by the worker; old completed/failed PDF jobs are removed after seven days. Failed non-login email bodies remain available for operator retry. Development `.eml` files are not automatically purged; do not use the development backend for real attendee data.

User records, bookings and audit history have no automatic retention purge or account-deletion UI in this release. Set an institutional retention policy and perform controlled database archival/anonymization as required. Logs can contain endpoint identifiers and IPs; configure logging retention too. These implementation notes are not a legal-compliance certification.

The app does not guarantee that one email equals one person. Multiple mailboxes, compromised mailboxes, mail forwarding, spam delivery and external SMTP outages are outside the reservation database's uniqueness guarantees. Public seat browsing creates no holds; allocation occurs atomically at booking time.
