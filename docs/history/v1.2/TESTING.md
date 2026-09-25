# Verification report — v1.2, 25 September 2026

## Executed automated tests

Command in the project root:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OPENBLAS_NUM_THREADS=1 python -m pytest -q --disable-warnings --junitxml=docs/test-results.xml
```

**100 passed in 88.33 seconds**, Python 3.13.5. Disabling unrelated installed pytest plugins avoids instrumentation overhead in this environment; it does not disable any tests in this project. Runtime dependencies are pinned to the versions exercised. This run used installed packages, not a fresh installation in a clean Docker image.

The 50 earlier tests still exercise email sign-in/expiry/replay, origin/CSRF/access controls, SMTP dialogue against a loopback capture server, durable outbox handling, all-or-nothing bookings, a 20-thread race with exactly one successful booking, quotas, cancellations, administrator overrides, audit, plan review/version migration, PDF worker processing, legacy grids and exclusions, exports, and SQLite backup.

The 50 added tests comprise 24 development-reference polygon overlap cases, 17 earlier point-coverage regressions, and geometry/API cases for polygon validation, actual page bounds, coordinate round-tripping, legacy rectangles, four-corner grid shared edges and numbering, invalid grids, and polygon save/public API/actual booking/print persistence. Parametrized cases are counted as separate tests; these are not 100 independent full-application scenarios.

The deterministic sample test executes the detector repeatedly against the same source and exclusions and compares canonical results. It does not promise cross-platform or cross-library-version bitwise identity.

## Geometry audit on the user's PDF

Run:

```sh
python tools/audit_sample.py --output /tmp/seatplan-audit
```

This executes both the earlier `multiscale` strategy and the new `boundaries` strategy. It does not read a cached new result or inject the expected reference corners into detection. The source PDF checksum and rendered dimensions are recorded in `sample-detection-report.json` and `geometry-audit.json`.

| Development-region measurement | v1.1 | v1.2 |
|---|---:|---:|
| Reference cells | 24 | 24 |
| Mean polygon intersection-over-union | 0.785 | 0.917 |
| Cells with IoU ≥ 0.80 | 21 | 24 |
| Cells with IoU ≥ 0.90 | 10 | 18 |
| Extra competing fragments at reference IoU > 0.15 | 2 | 0 |
| Observed detection duration, one execution | 7.689 s | 22.985 s |

**These are not full-hall accuracy, precision or recall measurements.** The 24 approximate references were visually placed on the source scan with pixel rulers (roughly 1–3 px uncertainty), used during development, and selected around reported problems. They are not an independent held-out benchmark. Some previously well-aligned individual cells regress slightly despite the mean improvement. Complete per-cell results are in the JSON, not hidden by the average.

The new default produces **919 candidate shapes**, all unreviewed, and **59 unavailable flags** after the heuristic and 12 unchanged manual exclusion annotations. The printed capacity of 928 is not an algorithm input. Do not divide 919 by 928 and call it recall. The cross-out heuristic and manual masks are not independently audited ground truth.

The comparison PNG has identical blue strokes for old and new outputs, with an untouched source column. It represents geometry, not reservation status. No missing seats were hand-inserted into the detector output. Full-page and remaining-failure views are also included. Remaining failures include faint outer-right seats, some loje symbols, and an ambiguous blank-compartment candidate flagged for review.

## Chromium UI checks

The browser suite uses Chromium/Playwright and a real local application plus worker. This environment has managed navigation restrictions. The executed mode is an **offline DOM / HTTP API bridge**, not normal browser navigation through a production proxy. Assets are injected; the bridge supplies request origins/cookies and routes API requests to the real local server. It does not validate normal module loading, full CSP enforcement, browser SameSite/Secure behavior, public DNS/TLS or the production proxy.

Command:

```sh
OPENBLAS_NUM_THREADS=1 python tests/browser_smoke.py --bridge --output /tmp/seatplan-browser
```

**15 checks passed** in the executed run, recorded in `browser-report.json`: administrator delivered-email login; full-page fit and focus view; sample review gate; 17 actual candidate clicks; inspector edits/save; drawn rectangular grid preview/add/undo; four-click quadrilateral grid preview/add/undo; actual SVG corner dragging/undo; web-triggered worker detection; visitor email login and two-seat booking; My reservations; administrator override; printable roster/map generation; 390px mobile layout; and no uncaught JavaScript errors in those flows.

The corner test zooms before dragging, exceeds the deliberate 4px drag threshold, compares actual SVG polygon points, and verifies that Undo restores them. Fit-page checks the SVG's actual bounding box against the map viewport. The full-page and focus screenshots use the supplied PDF, not a synthetic substitute. Booking/print scenarios use a separate synthetic hall, not a falsely published sample inventory. Both pages of the newly generated print PDF were rasterized and visually inspected.

For a normal environment, run the browser script without `--bridge` and manually verify the deployment's external HTTPS origin. The runner does not change browser policy or download browser binaries.

## Not verified or certified

Docker image building, fresh dependency installation/resolution, your authenticated/TLS SMTP relay and deliverability, actual Apache/Nginx proxy, production cookie behavior, public exposure and long-term load were not tested. This is not an independent security audit, hostile-PDF corpus test, accessibility certification, or complete human verification of every seat.

The application includes a deterministic improvement, not a guarantee of perfect automatic reconstruction. Review and correct the full inventory before publishing an event.
