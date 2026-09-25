# Changes

## v1.3 — separate banks and faint chair pairs

- Recover complete disconnected groups through independently supported shared borders, without requiring an existing accepted seat in that group.
- Bounded, deterministic grayscale template search for small chair symbols near uncertain evidence, followed by local pair/row consistency. Reject ink-filled hypotheses before selecting local maxima.
- Replace supported merged chair enclosures and remove a supported negative-space gap; preserve the other existing outlines. No sample coordinates or expected seat count are detector inputs.
- Optional recovery checkbox; disable it for the v1.2 precision path. No schema migration or automatic changes to saved plans.
- 24 new development-reference locations: six outer-right numbered cells and 18 left Loja 9/outer Loja 10 chairs now have individual free click targets. All earlier 24 polygon metrics remain identical.
- Reproducible v1.2/v1.3 audit and honest original/old/new visual comparison. Current candidate count is 935, not a verified seat count.
- 132 automated tests and 16 offline browser-bridge checks in the delivered environment. See docs/TESTING.md for scope and limits.

## Safe upgrade from v1.2

Preserve `.env`, `APP_SECRET`, database and assets; take a backup. Replace the application source, run `docker compose up -d --build`, then `docker compose exec web python -m seatplan.cli seed-sample --new` for a separate current sample draft. Hard-refresh the browser. Existing plans, edits, events and reservations remain untouched. Do not replace a published event plan without the existing explicit reviewed migration workflow.


## v1.2 — boundary geometry and complete-page editor

- Optional convex four-corner outlines throughout storage, public map, editor, overlap handling and print output. Legacy rectangles remain valid.
- Default deterministic four-border pixel fitting: multi-angle morphology/LSD proposals, local bounded edge fitting, repeated-cell consistency and overlap suppression. No OCR or hall-coordinate seat insertions.
- Manual four-click quadrilateral grids; draggable individual seat corners; low-confidence candidate selection.
- True Fit page, separate Fit width, viewport-sized Focus map with Escape exit.
- Increased PDF worker processing limits for the slower detector.
- 24 approximate development-reference cell polygons and a reproducible old/new geometry audit; 17 earlier coverage references retained.
- 100 passing automated tests and 15 passing offline browser-bridge checks in the delivered environment. Full normal-browser/proxy deployment still requires staging verification.

Sample geometry is improved, not complete. It remains an unpublished review draft. Upgrading does not rerun detection on or mutate existing plans.

## Safe upgrade from v1.1

Back up the database/assets and preserve `.env` / `APP_SECRET`. Replace application source, then:

```sh
docker compose up -d --build
docker compose exec web python -m seatplan.cli seed-sample --new
```

`--new` creates a separate review draft using the current detector and keeps existing plans, events and bookings. Hard-refresh the browser. Do not load a new quadrilateral overlay into an old v1.1 deployment.


---

## Historical v1.1 release notes

# 1.1.0

Deterministic detector ensemble for faint and rotated seats; directional small-symbol repair; corrected rotated overlap checks in the editor; legacy comparison method; safe fresh sample draft import; sample-based location/click regressions. See docs/DETECTION-V2.md.

This is an improved detection draft, not a complete hall inventory or fully resolved detection problem.
