# PDF editor and seat geometry

## Viewing the complete plan

**Fit page** fits both the width and height inside the map viewport, including a portrait page's bottom edge. **Fit width** enlarges to the available width and may require vertical scrolling. **Focus map** expands the map into a viewport-sized panel; Escape exits it. Plus/minus zoom beyond the fitted scale. Full-page viewing is an overview: zoom in to review individual borders and tiny loje seats.

The editor's complete-page screenshot is `screenshots/sample-editor.png`; the focused viewport is `screenshots/sample-editor-focus.png`.

## Coordinates and compatible shape storage

Page coordinates are normalized: `(0,0)` top-left, `(1,1)` bottom-right. Page indices start at zero. A seat retains `x,y,w,h,angle` as its local frame; angle is clockwise in actual rendered pixel space. The optional `outline` contains exactly four convex clockwise vertices in local frame coordinates, each within `[-0.5,0.5]`. When omitted, the old rotated rectangle is used. The frame is not the clickable boundary when an outline exists.

The same transformed polygon is used in the editor, public map, overlap handling and print output. Moving, resizing and rotating a seat transform its local polygon. Existing rectangle plans remain valid; existing plans and reservations are not automatically modified by an application upgrade. New polygon overlays require this version or later, not the old frontend/backend.

## Tools and review

**Select** toggles a seat or drag-selects a region. **Move** drags the selected set. **Add seat** creates a provisional rectangle. **Edit corners** exposes four handles when exactly one seat is selected; zoom in and drag a handle to the printed boundary. Invalid/self-intersecting edits are rejected. Successful corner edits reset that seat's review flag and can be undone.

**Select low-confidence shapes** selects detected shapes whose geometric confidence is below 0.35 on the current page. It is a review aid, not a calibrated error classifier. High scores do not prove seat identity or availability.

The inspector edits section, row, label, dimensions, angle, availability, review state and notes. Blank inputs generally leave values unchanged. Labels must be unique within their section and row. **Exclude area** creates an independent non-reservable mask; **Exclude selected** changes an individual flag. A masked seat remains unavailable even after its individual flag is cleared. Masks use seat-center containment, including the boundary.

Save deliberately. Starting detection saves the current draft before queuing the job; other edits are not autosaved. Undo retains 30 in-browser geometry/mask states, not a permanent audit history or event-allocation rollback. Export an overlay before large changes.

## Rectangle and four-corner grids

**Draw grid** uses a dragged bounding rectangle, row/column counts, gap percentages and overall rotation. **4-corner grid** takes four clicks clockwise, starting at the corner corresponding to row 1 / column 1. Click the other three outer corners in order, then configure rows, columns, gaps and numbering in the dialog. Escape cancels incomplete point selection.

The four-corner grid uses bilinear interpolation between those points. Adjacent cells have consistent shared boundaries when gaps are zero. Its rotation field is ignored because the clicked corners define the orientation. It is not automatic perspective estimation or pixel snapping. For a curved bank, define one row or short strip at a time rather than one grid around the entire arc.

For printed touching cells, set horizontal and vertical gaps to **0%**. A gap of 20% means the clickable extent occupies 80% of each cell's pitch. Numbering supports first value, positive/negative step, continuous or restart-per-row numbering, row prefix and serpentine order. Step 2/-2 handles even/odd numbering. Always confirm direction against the paper.

Preview before adding. Existing geometrically overlapping seats are skipped based on actual polygon overlap, not just center distance. Remove or replace old incorrect candidates deliberately; a new grid should not be layered on top of a wrong old overlay. Inspect endpoints and boundary alignment after adding, and correct individual corners as necessary.

## Deterministic detection

The new default is **Precision: four-border pixel fitting**. The previous multi-pass and legacy methods remain selectable. The precision method generates contour, directional-line and LSD proposals; fits four borders to original grayscale pixels; and checks local repeated cell dimensions to reject fragments and recover supported gaps. It stores convex quadrilaterals instead of forcing every seat into a rotated rectangle.

**Recover separate groups and faint chair pairs (slower)** is enabled by default for the precision strategy. It adds a conservative pass for disconnected numbered cells with shared borders and for small repeated chair symbols near existing evidence. It can split supported merged chair enclosures and remove some empty spaces mistaken for chairs. Uncheck it to reproduce the v1.2 geometry pass. This setting does not affect the multi-pass or legacy strategies. The small-symbol portion additionally requires **Repair faint small outlines** to be enabled; shared-edge bank recovery does not. A recorded full-page v1.3 run took about 80 seconds in the test environment; performance varies with the scan and hardware. It has work caps and may require a smaller detection region.

Settings are in rendered pixels, not browser pixels or PDF points. PDF pages use a 2,400-pixel longest side. Restrict **Detection region** when a local bank needs different settings. Increasing closure can recover gaps but can also merge cells. The precision pass has fixed extra threshold/line-proposal variants; the original contour controls also affect its seed detector.

All results remain unreviewed. A failed four-border fit can survive as a low-confidence fallback. The method is not a verified seat inventory. The v1.3 sample produces 935 candidates; the 12 explicit manual masks plus cross-out heuristic flag 59. These flags have not been classified as correct or incorrect. No false-negative/false-positive rate has been established. A lack of a flag does not mean a crossed-out seat is safe to reserve.

Native PDF text can supply a simple candidate label; it does not infer all section/row semantics. This supplied scan has no usable native text layer, so provisional `D0001`-style labels require correction. No OCR or learned model is included. See [RECOVERY-V1.3.md](RECOVERY-V1.3.md) for current changes and limits, and [RESEARCH.md](RESEARCH.md) for the historical v1.2 geometry approach.

Detection runs in the worker, not the browser UI thread. Its subprocess has a 210-second wall limit and a 180-second CPU soft limit. The browser polls for up to three minutes, so a busy queue or a particularly slow job may finish after that UI wait; check Delivery & audit. Work on smaller regions rather than indiscriminately weakening all filters.

## Exclusions, publication and replacement PDFs

The supplied checksum-matched exclusions are editable manual annotations, not an exhaustive independent audit. Review crossed-out positions, especially faint marks and curved compartments. Exclusions and shape detection remain separate.

Publication requires every candidate reviewed. The printed capacity does not determine the published inventory. Do not bulk-approve merely because a detection job finished.

Published overlays are immutable. Duplicate for changes on the same background, or upload a new PDF and import/draw its overlay. Import assigns fresh internal IDs and resets review flags; a different PDF checksum triggers an alignment warning. Page counts must match.

Events remain linked to their existing published plan until explicitly switched. Migration uses exact case-sensitive section, row and label strings. Missing occupied seats or seats newly excluded block the switch until resolved. Review physical identity as well as matching strings; geometry alone cannot establish that a reservation was moved appropriately.
