> Historical v1.1 report. The current detector and results are documented in RESEARCH.md, TESTING.md and CHANGELOG.md.

# Detection v2: recovery of missing sample overlays

## Observed defect

The original 691-candidate editor screenshot did not contain click targets for main seats 438 and 437 under the two Loja 6 labels. Numerous small rectangles in the loje and seats in the curved upper block were also absent. These were detection omissions, not deliberate exclusions and not a booking/permission fault.

## Changes

- Multi-pass adaptive/Otsu thresholding, including weaker-line thresholds, with several closure sizes rather than one fixed closure.
- Six-angle directional gap closing for small empty rectangular symbols. Checks against the original ink limit false detections in gaps between chairs.
- Both suitable outer and inner contours can contribute. Small merged-pair enclosures are suppressed when distinct symbol interiors are present.
- Rotated-rectangle overlap, rather than an axis-aligned center-distance test, is used when adding non-overlapping preview candidates.
- Web controls expose the method, small-symbol repair, maximum symbol size and gap length. The original detector remains selectable as `Legacy detector (comparison)`.
- A safe `seed-sample --new` option creates a separate draft without rewriting existing plans or bookings. Global seat IDs remain unique between drafts.

The detector has no hard-coded seat positions, OCR, learned weights or manual seat insertions. The pre-existing sample exclusion polygons are manual annotations and remain separate from detection.

## Results on the supplied PDF

| Measurement | Previous | Revised |
|---|---:|---:|
| Candidate shapes | 691 | 920 |
| Selected regression locations with a click target | 0 / 17 | 17 / 17 |
| Manually annotated exclusion masks | 12 | 12 |
| Candidates inside those masks | 47 | 86 |

The 17 reference points are a selected, visually inspected subset of old failures: seats 438 and 437, seven positions in left Loja 5, and eight positions in the curved upper block. This is **not** an unbiased recall/precision benchmark or an exhaustive hall audit. Increased masked-shape counts do not mean that additional real seats have been closed; more shapes and fragments are found inside the same polygons.

All 920 candidates remain unreviewed. Sixty-six candidates originate from the directional repair passes. One local run took approximately 7.1 seconds; timing depends on the machine and PDF. Repeated canonical output was identical in the test environment.

**The loje problem is improved, not completely solved.** A faint position in left Loja 5 is still missing in the comparison image, and imperfect/misaligned or split rectangles remain. The scan's printed numbers are still provisional D-labels. Do not open reservations until the inventory, numbering and exclusions have been reviewed and corrected. The total 920 is not a verified number of seats and is not validated against the printed 928.

See `screenshots/detection-comparison.png` for tightly cropped before/after examples, including the remaining miss. This comparison is rendered from the actual detector geometries over the PDF raster. `screenshots/sample-editor.png` is the revised real editor rendered in Chromium via the documented HTTP/DOM test bridge.

## Updating an existing installation

Keep your `.env` and persistent data volume. Back up the installation first. Replace the application source (including the new `seatplan/detection_shapes.py`), then rebuild both services:

```sh
docker compose up -d --build
docker compose exec web python -m seatplan.cli seed-sample --new
```

The last command is optional and creates a **new review draft** for the bundled sample. It does not change old drafts, published plans, event assignments or reservations. Without `--new`, an already imported sample is deliberately left unchanged. No database schema migration is required.

Hard-refresh the browser after deploying the updated static files. For other PDFs, choose **Multi-pass: faint and rotated seats** in Automatic detection, optionally restrict a region, run detection and inspect the preview. Size settings are pixels in the rendered page, not CSS pixels.

`Add non-overlapping` preserves existing shapes and identities; it cannot correct a malformed existing shape it overlaps. `Replace current page shapes` removes the page's existing shapes and identities, so export/duplicate the draft first and do not use it blindly after manual edits. Existing exclusion areas are retained. Published plans are immutable and must use the normal replacement/version workflow.
