# Verification report: detector v2, 25 September 2026

## Executed automated suite

Command:

```sh
OPENBLAS_NUM_THREADS=1 python -m pytest -q --disable-warnings --junitxml=docs/test-results.xml
```

**50 tests passed**, including the original authentication, SMTP capture, booking-race, reservation, administration, PDF worker, migration and export tests. New coverage checks 17 specific old omissions against freshly rendered/detected sample data, one distinct free/unreviewed click target per reference, provenance, exclusion-mask priority, rotated frontend deduplication, and safe creation of a new sample draft without overwriting the old one. The JavaScript geometry unit test uses Node when installed; this does not introduce a frontend build/runtime dependency on Node.

The original suite still includes two repeated full detections and compares their canonical JSON. The legacy strategy reproduces the original 691 candidates. The revised default produces 920 candidates with 86 inside the unchanged 12 manual masks. None is automatically approved. No blocked candidate was found outside the sample masks in the revised masked run.

These numbers are candidate counts, not physical seat counts. The regression subset was selected from known omissions and is not a random-sample accuracy estimate. One faint left Loja 5 position and imperfect shapes remain. See `DETECTION-V2.md` and `sample-detection-report.json`.

## Executed browser checks

Normal HTTP browser navigation was attempted again and failed with `ERR_BLOCKED_BY_ADMINISTRATOR` under the environment's managed browser policy. That is an environment restriction, not evidence that production browser navigation works or fails.

The existing offline DOM/HTTP API bridge was then run:

```sh
python tests/browser_smoke.py --bridge --output /tmp/seatplan-browser
```

**12 checks passed** with no uncaught JavaScript errors. These include loading the revised editor, **actually clicking all 17 recovered reference rectangles and checking the selected inspector identity and Free state**, review-gate refusal, edits/saving, grid preview/add/undo, a web-triggered detection job, visitor sign-in and multi-seat booking, My reservations, administrator override, print generation, and mobile layout.

The bridge renders the actual frontend in Chromium and talks to the actual local HTTP app/worker, but supplies asset and request transport. It does **not** validate normal-browser module loading, real navigation, CSP, Secure/SameSite transport behavior or the real HTTPS reverse proxy. Pure JavaScript syntax checks and a frontend geometry unit test also passed. Run the normal browser runner and test your staging origin before launch.

The revised editor screenshot and algorithm-overlay comparison were visually inspected. The v2 browser run generated a synthetic allocation PDF, but that new PDF was not separately visually inspected during this revision. The supplied `allocation-print-example.pdf` remains the original previously reviewed synthetic example, not a verified sample-hall allocation.

## Not executed

Docker image building, a fresh dependency installation, your production SMTP authentication/TLS and delivery, and your external proxy were not tested here. There was no independent security audit or complete manual reconstruction of the sample's seat inventory. No production data, mailbox credentials or bookings were used.

Historical v1 verification records and screenshot are retained in `docs/archive/` and are explicitly not current v2 results.
