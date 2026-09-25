# Residual-seat recovery — v1.3

## Two different failures

The six outer-right cells visibly labeled 255, 257, 259, 261, 263 and 265 had strong fitted border proposals, but the previous acceptance step required already accepted neighboring seats. The entire isolated group was consequently discarded. This was principally a bootstrap/filter problem, not a lack of border proposals.

In the reported left Loja 9 and outer Loja 10 region, faint small symbols were missed or represented by one larger enclosure spanning a pair. Merely increasing a global threshold or counting candidate shapes would not distinguish these errors.

## Implemented correction

`seatplan/detection_recovery.py` runs after the existing four-border/lattice pass. It does not contain venue coordinates, printed labels, an expected total, or a saved seat list.

**Separate banks.** Strong four-border proposals with compatible area and actually shared edges form a graph. A connected group of at least three cells can support its own acceptance without a previously accepted seed. Isolated shapes and non-shared near neighbors do not qualify. Existing overlapping seats are retained, not duplicated.

**Small repeated chairs.** Existing small, mostly empty, elongated symbols provide a robust scale and local orientation range. Around uncertain evidence, a bounded pixel-resolution search evaluates three sizes per dimension and nearby angle bins. Four signed ridge filters compare each possible border with the grayscale pixels to either side. Interior-ink tests are applied before local peak selection: an ink-filled false hypothesis must not suppress a valid neighboring empty chair.

Accepted hypotheses must form locally compatible pairs/rows and remain near actual existing chair evidence. This is geometric repetition, not knowledge of how many seats a named loja contains. A supported pair can replace an oversized merged enclosure. A small empty negative-space candidate bracketed by compatible chairs can be removed. A very strong empty-interior match can also tighten a slightly oversized, ink-filled single-chair enclosure. Every other existing outline is preserved. New shapes carry recovery provenance and explicit review notes.

The pass is enabled by **Recover separate groups and faint chair pairs (slower)**. Disabling it reproduces the previous precision detector. The small-chair search additionally follows the repair-symbols setting. It is bounded by tile/template and worker time limits, and may need a smaller region on other scans.

## Measured result on the supplied scan

| Development references | v1.2: separate free targets | v1.3: separate free targets |
|---|---:|---:|
| Six numbered outer-right cells | 0 / 6 | 6 / 6 |
| Six outer left Loja 9 symbols | 1 / 6 | 6 / 6 |
| Six inner left Loja 9 symbols | 2 / 6 | 6 / 6 |
| Six outer left Loja 10 symbols | 3 / 6 | 6 / 6 |

A target spanning two reference positions does not count as two recovered seats. All 24 checked positions now resolve to different IDs. The checked false gap between chair columns resolves to no target. The browser regression actually clicks each target and checks the selected inspector identity.

Across the full detection result, 897 of the 919 old outlines remain identical, 22 were removed/replaced, and 38 new outlines were added: six shared-edge cells and 32 small symbols. The resulting 935 shapes include existing uncertainties and are **not 935 verified seats**. All 24 earlier per-cell polygon audit results remain identical, including mean IoU 0.917; the 17 earlier point checks also remain in the test suite.

One uncontended recorded run took 80.225 seconds at a 2,400-pixel longest side. This is a single run on the development environment, not a hardware-independent benchmark. Region selection and disabling the optional pass reduce work. No extra runtime dependencies are added.

## Limits and exclusion safety

The 24 interior reference points were visually selected on the original scan and used during development. They do not measure full-hall recall/precision or independently certify all four corners. Positional names inside loje are test identifiers, not recovered printed numbering. Scanned labels remain provisional D-numbers.

The same 12 manual exclusion annotations and independent cross-out heuristic apply after recovery. There are still 59 unavailable flags in this run; that is not a verified count of crossed-out seats. The crossed-out inner part of left Loja 10 stays excluded. All candidates stay unreviewed, publication still requires review, and an upgrade does not mutate existing saved plans or reservations.

Some small outlines, ambiguous blank-compartment candidates and overlapping fragments can remain outside the audited positions. Neither successful local references nor a total near the printed capacity establishes a complete inventory. Review those using the existing corner/grid/exclusion tools before publishing.

## Reproduction

```sh
python tools/audit_recovery.py --output /tmp/seatplan-recovery-audit
```

This reruns v1.2 and v1.3 against the source PDF, not a cached overlay. The reference JSON is used only after detection for measurement. See `recovery-audit.json`, `sample-detection-report.json`, and `screenshots/residual-comparison.png` for the delivered results. `tools/audit_sample.py` retains the historical v1.1/v1.2 geometry comparison with recovery explicitly disabled.
