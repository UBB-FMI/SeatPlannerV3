# Boundary reconstruction: research and implementation notes

Seatplan v1.2 · 25 September 2026

**Historical geometry notes.** The specific outer-right and left Loja 9/10 failures described below are addressed in v1.3; see [RECOVERY-V1.3.md](RECOVERY-V1.3.md) for the measured scope and remaining limits. The original v1.2 measurements are preserved here.

## Diagnosis on the supplied scan

The previous pipeline found closed regions and represented them with a minimum-area rotated rectangle. That representation cannot follow a sheared quadrilateral: changing a rectangle's angle cannot change its right angles. The upper-right bank contains just such cells. Thresholding and closing also generated fragments at character strokes, including competing shapes around main seats 372 and 346. Some faint dividers were lost altogether.

The appropriate decomposition here is **propose boundaries → fit actual printed borders → check local cell structure → represent four corners → review seat semantics**. Shape recovery, printed seat identity, and crossed-out availability are separate tasks. More candidate shapes do not establish better seat recall.

The following are selected chapters, paper sections, and official technical documentation consulted, not a claim to have read entire books. Their application to this particular seating diagram is our engineering inference, not a published evaluation on this venue.

## Primary reading and what it contributed

| Source | Relevant contribution | Status in this release |
|---|---|---|
| Torralba, Isola & Freeman, *Foundations of Computer Vision*, chapter 18, [Image Derivatives](https://visionbook.mit.edu/derivatives.html) | Spatial intensity changes provide boundary evidence; sampling, localization, and noise matter. | Original grayscale samples, not a repaired binary mask, verify fitted borders. The specific ridge score is our heuristic, not the chapter's algorithm. |
| Same book, chapter 31, [Perceptual Grouping](https://visionbook.mit.edu/perceptual_organization.html) | Local primitives must be grouped into meaningful structures. | Local repeated cell sizes support candidate selection; isolated fragments should not defeat a complete neighboring cell. |
| Same book, chapter 41, [Homographies](https://visionbook.mit.edu/homography.html) | General planar transformations need not preserve angles or lengths. | Quadrilateral outlines replace the compulsory right-angle model. The manual grid uses bilinear interpolation between four corners, not a recovered global homography. |
| Grompone von Gioi, Jakubowicz, Morel & Randall, [*LSD: a Line Segment Detector*](https://www.ipol.im/pub/art/2012/gjmr-lsd/), IPOL, 2012, DOI 10.5201/ipol.2012.gjmr-lsd | Gradient-aligned regions yield local line segments with controlled false alarms under the algorithm's model. | OpenCV's LSD generates proposals. Its statistical guarantee is NOT a guarantee that a proposed region is a real seat. |
| OpenCV, [line extraction using morphology](https://docs.opencv.org/4.x/dd/dd7/tutorial_morph_lines_detection.html) | Structuring-element direction and length select line-like structures. | Directional opening/closing at 36 orientations generates additional proposals. Final boundaries must still match original pixels. |
| OpenCV, [structural analysis and shape descriptors](https://docs.opencv.org/4.x/d3/dc0/group__imgproc__shape.html) | `minAreaRect` is an enclosing rotated rectangle; polygon approximation and convex clipping support less restrictive geometry. | Convex quadrilateral validation, actual polygon overlap, and a rectangle used only as a storage frame. |
| *Introduction to Bioimage Analysis*, [Image transforms](https://bioimagebook.github.io/chapters/2-processing/6-transforms/transforms.html) | Distance transforms and marker/seed-based watershed can split regions. | Considered, not shipped as the default. Glyph interiors and broken dividers make reliable seat seeds a separate problem in this scan. |
| Felzenszwalb & Huttenlocher, [*Distance Transforms of Sampled Functions*](https://cs.brown.edu/people/pfelzens/papers/dt-final.pdf), Theory of Computing 8, 2012 | Exact distance transforms can be computed efficiently and used in matching. | Considered for distance-to-edge objectives and validation; the implemented fitter instead samples grayscale ridge contrast directly, and the audit uses polygon IoU. |

No reference implementation source was copied into the MIT application. The installed OpenCV implementation supplies LSD and morphology; dependencies retain their licenses. In particular, the IPOL reference-code license is not silently relabeled MIT.

## Implemented algorithm

1. Render the PDF deterministically at a longest side of 2,400 pixels. Obtain proposals from the previous multiscale detector, additional contour variants, directional morphology, and LSD line masks. Enforce size, convexity, region, and proposal-count bounds.
2. For each proposed quadrilateral, search small integer normal offsets of both endpoints of every edge. Sample along the edge and on both sides with bilinear image sampling. Prefer a dark ridge consistently supported along the edge, with a small penalty for a large displacement. Intersect adjacent fitted lines to obtain four actual corners.
3. Match fitted candidates to the prior cell proposals by overlap and border support. Avoid replacing complete numbered cells with high-contrast text fragments. Use size, interior ink, and small-chair evidence to distinguish some loje symbols from compartment outlines.
4. Compare candidate edge lengths and areas with neighboring cells. Propose one-cell translations only where a local repeating structure supports them; every addition must independently pass the original-pixel border test. Rank competing candidates using this local consistency and suppress substantial overlaps.
5. Preserve uncertain fallback candidates at low confidence, with a review warning. Apply the existing independent exclusion annotations and cross-out heuristic. Assign provisional labels and leave every candidate unreviewed.

This is bounded exhaustive **local** search and local consistency, not a global optimum, universal table parser, or mathematical proof of a seat inventory. It does not force the output to the printed capacity. Heuristic confidence scores are not calibrated probabilities. No learned model, OCR, random model fitting, or hall-coordinate list is used by the detector.

The scan was used during development. Several pixel-scale parameters are calibrated to the default 2,400-pixel rendering and the tested symbol scale. Other venues, line thicknesses, rotations, degraded scans, or substantially different symbols require testing and potentially different settings. Increasing the raster size alone cannot restore information absent from the source scan.

## What was actually measured

`tools/audit_sample.py` independently reruns the v1.1 and v1.2 methods and creates side-by-side original/old/new crops. `tests/fixtures/sample-cell-polygons.json` contains 24 approximate polygons visually placed on the original scan using pixel rulers. They were not generated from the detector, but they were used during development and are NOT a held-out benchmark. Corner placement uncertainty is approximately 1–3 rendered pixels.

Mean polygon intersection-over-union changed from **0.785 to 0.917**. At IoU ≥ 0.80, **21/24 became 24/24**; at IoU ≥ 0.90, **10/24 became 18/24**. Competing fragments with IoU > 0.15 against a reference changed from **2 to 0**. Some already good cells have slightly worse IoU after refinement; this is an aggregate improvement, not a universal one.

The audited execution observed approximately **7.7 seconds** for v1.1 and **23.0 seconds** for v1.2 on this environment. This is a single execution, not a production benchmark. The detector produced **919 candidate shapes**, with **59 marked unavailable** after the heuristic and 12 manual exclusion masks. These are not verified seat or unavailable-seat counts. The printed 928 is not used by the detector.

## Known failures and deployment decision

Faint lower outer-right positions (the visible 255–265 group) and some small loje symbols are still missed. An oversized blank-compartment candidate also survives with a low-confidence warning. Cross-out detection and printed identity are not solved by better geometry. A complete human-reviewed inventory remains necessary before publication.

Use **4-corner grid** for a short, well-defined skewed block or single curved-row segment, then adjust vertices with **Edit corners**. This is an explicit human constraint followed by deterministic construction, not automatic detection. Zero gaps follow contiguous printed boundaries; positive gaps shrink clickable cells. A single four-corner grid does not model an entire curved auditorium.

A more complete next algorithmic stage would build a shared line/intersection graph per bank, solve cell adjacency and missing-divider hypotheses jointly, and expose unresolved alternatives rather than silently selecting one. That graph reconstruction and certified global optimization are **not** implemented in v1.2. The current release provides a concrete, tested improvement without claiming those capabilities.
