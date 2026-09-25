# Verification report — 25 September 2026

## Executed backend tests

Environment: Python 3.13.5, Linux, locally installed dependency versions pinned in requirements.txt. The command `OPENBLAS_NUM_THREADS=1 python -m pytest -q --disable-warnings --junitxml=docs/test-results.xml` completed with **29 passed**. Tests use temporary databases and test-only addresses/secrets; no production data or SMTP credentials are included.

Coverage exercised:

| Area | Executed checks |
|---|---|
| Authentication | Request/deliver/confirm flow; link GET does not authenticate; token hashing, expiration, replay rejection; sign-out/session rotation |
| Request protection | CSRF token, external Origin validation, admin-only access, secure production cookie flags, email validation, throttling |
| Reservations | Multi-seat allocation; all-or-nothing conflict handling; allowance; wrong-plan IDs; repeated request key; changed-payload replay rejection |
| Concurrency | 20 concurrent booking operations aimed at one seat; exactly one succeeded, 19 conflicted; one active allocation |
| Exclusions | Individual blocks and center-in-mask rules; administrator's explicit excluded-seat override; base exclusion retained after freeing an allocation |
| Cancellation/admin | Owner authorization; partial/full cancellations; private roster visibility; stale override rejection; change notifications and audit |
| Plans | Draft/published access controls; review gate; duplicate identities; optimistic revision; immutable publication; exact-identity event migration preserving bookings; rejection without data loss when identities are missing |
| Geometry | Rotated grids, continuous/per-row/serpentine/odd-even numbering, input bounds, full rotated seat-page bounds, invalid masks |
| PDF worker | Actual isolated-subprocess rendering of a synthetic multipage vector PDF; native text and rectangle detection; job status; corrupt/oversized/too-many-pages rejection |
| Supplied PDF | Exact checksum; raster-only content; 691 deterministic candidates; 12 explicit masks; repeated masked results identical; every candidate unreviewed |
| SMTP/outbox | Actual SMTP dialogue with a loopback capture server; development .eml delivery; retry/backoff; failure recording; expired login cleanup; body purge after send |
| Exports/operations | CSV formula protection; admin-only print/roster; overlay-label option; subpath URLs and cookie scope; consistent SQLite backup and integrity check |

The concurrency test exercises the actual reservation service and database in concurrent threads, not a simulated random result or a high-load HTTP benchmark. The loopback SMTP test uses an unauthenticated local test server; it does not validate the customer's authenticated TLS relay or deliverability.

## Chromium UI verification

Chromium 144.0.7559.96 and Playwright 1.57.0 were used. This environment applies a managed policy that blocks all browser URL navigation. The first normal browser attempt failed with `ERR_BLOCKED_BY_ADMINISTRATOR`; browser policy was not changed.

A separate **offline DOM transport bridge** rendered the actual templates/styles/JavaScript while routing API calls through a Python HTTP client to the real local app and worker. Source modules were concatenated for offline injection, image assets were inlined, location/history were test stand-ins, and the bridge supplied Origin/cookie transport. This validates DOM behavior and backend integration but **does not validate the normal browser's module loading, CSP enforcement, SameSite/Secure behavior, real navigation, or the production proxy**. Those have API/header tests where applicable, not a substituted claim of full-browser end-to-end coverage.

Command: `python tests/browser_smoke.py --bridge --output /tmp/seatplan-browser`.

**11 checks passed**, recorded in `browser-report.json`: admin email verification, sample draft display and review-gate refusal, inspector edits/saving, grid preview/add/undo, web detection job, visitor email verification and two-seat booking, My reservations, admin override, print/roster generation, 390px mobile layout without horizontal overflow, and no uncaught JavaScript page errors in the exercised flows.

The print snapshot contained two A4 pages: the synthetic allocation map, followed by its private roster. The PDF was rendered again and visually inspected. The booking/print example is explicitly a synthetic test hall; it is not a published version of the supplied venue. The screenshot `screenshots/sample-editor.png` uses the actual supplied PDF and its unreviewed candidates.

The ordinary browser runner is included without the bridge flag. Run it in an environment with an installed unrestricted local Chromium, and also test the actual staging URL manually before public deployment. The runner does not modify the browser's policy or download browser binaries.

## Detection result and boundaries

`sample-detection-report.json` records the source and canonical-result hashes, dimensions and measured candidate counts. Default raw detection: **691 candidates, zero automatically recognized cross-outs**. Adding the 12 manually annotated sample polygons/rectangles excludes **47 candidate shapes**. All 691 remain unreviewed. Repeated masked output was byte-identical after canonical JSON serialization in the exercised environment.

These are shape counts, not ground-truth seats, actual unavailable-seat totals, or measured recall against the printed 928. Some real seats are missed; some chair/box details can be split or mistaken for seats. Scanned numbers have no text layer and are not OCR'd. The manually defined masks are distinguishable from algorithmic detection and require organizer review.

## Not executed or certified

The Docker image was not built in the network-restricted environment. A fresh pip resolution/download was not tested; runtime execution used installed packages. The delivered Dockerfile targets Python 3.12 whereas execution here used Python 3.13.5. Your real HTTPS reverse proxy, remote SMTP credentials/TLS/CA chain, incoming Internet traffic and domain mail deliverability were not available to test.

This is not an independent security audit, an exhaustive concurrency/stress test, a broad PDF parser adversarial corpus, accessibility certification, or a completed human audit of the sample seat inventory. TLS proxy/cookie behavior must be verified on the actual deployment origin. Dependency pins are the exercised versions, not a declaration that they are the latest available patches.
