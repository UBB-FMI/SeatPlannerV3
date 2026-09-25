# Verification report — v1.3, 25 September 2026

## Automated suite

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OPENBLAS_NUM_THREADS=1 python -m pytest -q --disable-warnings --junitxml=docs/test-results.xml
```

**132 passed in 280.49 seconds**, with zero failures/errors/skips, on Python 3.13.5 using installed dependencies. The new tests include 24 parametrized residual-location checks, distinct-ID/no-gap checks, recovery provenance and exclusion checks, synthetic disconnected-bank/duplicate rejection, pair-symmetry, constant-image ridge-filter behavior, and the optional switch. The earlier 100 tests remain in the suite. Parametrized locations are counted separately; these are not 132 independent end-to-end scenarios.

Coverage retains email verification/expiry/replay, Origin/CSRF/access controls, secure cookie flags, a local capture-server SMTP dialogue, durable delivery, atomic multi-seat bookings, a 20-way race with one successful allocation, cancellations, quotas, administrator overrides/audit, publication gates, plan migrations, PDF processing, legacy and quadrilateral grids, polygon persistence in public booking/printing, exports, backups, 17 earlier coverage references and 24 earlier polygon references.

The sample is detected independently in the session fixture and twice in the repeatability test. Canonical outputs of the two repeated runs must match. This is determinism in the exercised environment, not guaranteed bitwise equality across OpenCV versions and platforms. Disabling unrelated ambient pytest plugins does not skip project tests.

An additional actual detector execution with `recover_groups=False` was **identical to the saved, freshly executed v1.2 baseline**, including the 919 candidate result. See `compatibility-check.json`. Python compilation and all local JavaScript module syntax checks also completed successfully.

## Newly reported regions

`tests/fixtures/sample-residual-points.json` contains 24 interior points visually selected on the original rendered scan. No such coordinates are used by the runtime detector.

| Reference region | v1.2 separate free targets | v1.3 separate free targets |
|---|---:|---:|
| Outer-right numbered cells 255–265 | 0/6 | 6/6 |
| Left Loja 9, outer six symbols | 1/6 | 6/6 |
| Left Loja 9, inner six symbols | 2/6 | 6/6 |
| Left Loja 10, outer six symbols | 3/6 | 6/6 |

A merged outline covering two positions is not counted as two correct targets. All 24 locations now have exactly one free target with a unique ID. The checked gap between columns has no target. All candidates remain unreviewed. The unchanged 12 sample exclusion annotations still exclude covered positions, including the crossed-out inner part of left Loja 10.

The earlier 24 polygon comparisons are unchanged per cell: mean IoU **0.917263**, 24/24 at IoU >= 0.80, 18/24 at IoU >= 0.90, no competing reference overlaps. Across the whole result, 897 of 919 old outlines remain identical, 22 were removed/replaced, and 38 new outlines were added, giving **935 candidate shapes**. The final unavailable flag count remains 59. None of these totals is a verified inventory or full-hall recall score.

**Limitations:** these are development references, not held-out validation; the point checks do not independently certify every corner. Some loje fragments/overlaps/ambiguous candidates can remain elsewhere. Printed labels are not OCR'd. All physical seat identities and handwritten exclusions require review before publishing.

One separate full-page execution observed **80.225 seconds** at a 2,400-pixel longest side. The optional recovery search scanned 46 tiles with 2,691 template evaluations. This is a single development execution, not a production throughput benchmark. The test suite ran repeated full detections, and some browser/verification work ran concurrently; its total time should not be interpreted as single-PDF performance.

Reproduction:

```sh
python tools/audit_recovery.py --output /tmp/seatplan-recovery-audit
```

The tool reruns both v1.2 and v1.3 from the PDF and generates the comparison and measured JSON. `tools/audit_sample.py` preserves the historical v1.1/v1.2 comparison with recovery explicitly disabled. Delivered results are in `recovery-audit.json`, `sample-detection-report.json`, and `screenshots/residual-comparison.png`.

## Chromium UI checks

```sh
OPENBLAS_NUM_THREADS=1 python tests/browser_smoke.py --bridge --output /tmp/seatplan-browser
```

**16 checks passed**, with zero uncaught JavaScript page errors. The new check actually clicks each of the 24 residual-region SVG targets, verifies the selected label and free flag in the inspector, verifies distinct IDs, and checks that recovery is enabled. The old 17 actual sample clicks still pass.

Other checks cover delivered-email administrator and visitor login, full-page fit/focus, publication refusal on the unreviewed sample, inspector edit/save, rectangle-grid preview/add/undo, four-click polygon-grid construction, actual corner dragging/undo, a web-triggered worker detection returning 935 candidates, two-seat visitor booking, My reservations, administrator override, printing/roster output, and mobile width. See `browser-report.json` for the actual check list.

**Transport limitation:** this environment restricts normal Chromium navigation. The executed runner used the existing offline DOM/HTTP API bridge. The real application and worker handled API operations, but the bridge supplies origins/cookies/assets; it does not validate real navigation, normal module loading/CSP, browser SameSite/Secure behavior, public DNS/TLS or the production reverse proxy. It does not modify browser policy. Run without `--bridge` and test your external HTTPS origin in staging.

The current sample-editor/focus screenshots show the actual supplied PDF and automatic unreviewed result. Both pages of the newly generated allocation/roster PDF were rasterized and visually inspected. Booking and printing use a separate synthetic hall, not a silently published version of the user's venue.

## Not executed or certified

No clean Docker image build, fresh dependency resolution, real SMTP authentication/TLS/deliverability, actual reverse proxy or public-origin browser test was performed. This is not a security audit, hostile-PDF corpus evaluation, accessibility certification, high-load benchmark or exhaustive human seat audit. Existing deployment and review requirements still apply.
