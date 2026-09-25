"""Reproduce geometry checks and visual comparisons on the supplied PDF.

Run from the project root: python tools/audit_sample.py --output /tmp/seatplan-audit
No OCR, external services or additional runtime dependencies are used.
Reference polygons are development checks, never detector inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from seatplan.detection import detect_seats, render_pdf
from seatplan.domain import DetectSpec
from seatplan.geometry import seat_vertices


def compare(seats: list[dict], references: dict) -> dict:
    """Match independent, separated references; reject duplicate positive matches."""
    width, height = references["width"], references["height"]
    polygons = [np.asarray(seat_vertices(seat, width, height), np.float32) for seat in seats]
    areas = [cv2.contourArea(p) for p in polygons]
    centers = np.asarray([p.mean(axis=0) for p in polygons])
    used: set[int] = set()
    cases = []
    for reference in references["cases"]:
        expected = np.asarray(reference["points"], np.float32)
        area = cv2.contourArea(expected)
        scores = np.zeros(len(polygons))
        nearby = np.flatnonzero(np.linalg.norm(centers - expected.mean(axis=0), axis=1) < 100)
        for index in nearby:
            intersection, _ = cv2.intersectConvexConvex(expected, polygons[index])
            scores[index] = intersection / max(1, area + areas[index] - intersection)
        best = int(np.argmax(scores))
        # The references do not overlap. This also guards against a merged box
        # being credited as two successful matches. Missing cases have IoU 0.
        if scores[best] > 0:
            if best in used:
                raise ValueError("One detected shape matches multiple references; audit needs explicit assignment.")
            used.add(best)
        cases.append({**reference, "iou": float(scores[best]), "seat_id": seats[best]["id"] if scores[best] > 0 else None, "extra_overlaps": int(np.count_nonzero(scores > .15)) - int(scores[best] > .15)})
    return {"mean_iou": float(np.mean([case["iou"] for case in cases])), "at_least_0.8": sum(case["iou"] >= .8 for case in cases), "at_least_0.9": sum(case["iou"] >= .9 for case in cases), "extra_overlaps": sum(case["extra_overlaps"] for case in cases), "cases": cases}


def font(size: int) -> ImageFont.ImageFont:
    # Use a system font when available; no font file is bundled or copied.
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def crop_overlay(image: Image.Image, seats: list[dict], box: tuple[int, int, int, int], scale: int = 3) -> Image.Image:
    left, top, right, bottom = box
    output = image.crop(box).convert("RGB").resize(((right-left)*scale, (bottom-top)*scale))
    draw = ImageDraw.Draw(output)
    for seat in seats:
        points = seat_vertices(seat, image.width, image.height)
        if max(x for x, y in points) < left or min(x for x, y in points) > right or max(y for x, y in points) < top or min(y for x, y in points) > bottom:
            continue
        projected = [((x-left)*scale, (y-top)*scale) for x, y in points]
        draw.line(projected + projected[:1], fill=(9, 110, 170), width=2, joint="curve")
    return output


def visuals(image: Image.Image, old: list[dict], new: list[dict], folder: Path) -> None:
    regions = [
        ("Central seats: 194 / 192 / 166 / 164 / 142", (650, 1375, 810, 1532)),
        ("Left outer bank and adjacent loje", (95, 1000, 335, 1350)),
        ("Upper-left angled cells", (265, 680, 445, 840)),
        ("Upper-right skewed cells", (1230, 665, 1415, 875)),
        ("Main 374 / 372 / 370 and 348 / 346 / 344", (468, 1130, 591, 1245)),
    ]
    panel_width = 720
    header, row_gap, margin = 155, 75, 28
    heights = [int((box[3]-box[1]) * min(3, panel_width/(box[2]-box[0]))) for _, box in regions]
    canvas = Image.new("RGB", (panel_width*3 + margin*4, header + sum(h + row_gap for h in heights) + 95), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 18), "Seatplan: original scan / v1.1 / v1.2 boundary fitting", fill="#183343", font=font(32))
    draw.text((margin, 65), "Same blue outline in both outputs. Geometry only; colors do not indicate availability. No manually inserted seats.", fill="#41525a", font=font(22))
    for column, name in enumerate(("Original PDF pixels", "Previous rotated rectangles", "New fitted quadrilaterals")):
        draw.text((margin + column*(panel_width+margin), 110), name, fill="#183343", font=font(25))
    y = header
    for index, (name, box) in enumerate(regions):
        draw.text((margin, y), name, fill="#183343", font=font(26))
        y += 44
        for column, seats in enumerate(([], old, new)):
            patch = crop_overlay(image, seats, box)
            if patch.width > panel_width:
                patch = patch.resize((panel_width, heights[index]), Image.Resampling.LANCZOS)
            x = margin + column*(panel_width+margin)
            canvas.paste(patch, (x, y))
            draw.rectangle((x, y, x+patch.width, y+patch.height), outline="#c6d1d8", width=1)
        y += heights[index] + row_gap - 44
    draw.text((margin, y+8), "Remaining misses are visible, including faint loje details. These regions do not establish full-hall detection accuracy.", fill="#41525a", font=font(22))
    canvas.save(folder / "geometry-comparison.png")
    crop_overlay(image, new, (0, 0, image.width, image.height), scale=1).save(folder / "full-plan-outline.png")
    # Explicitly expose a known failure region, not just selected successes.
    crop_overlay(image, new, (1320, 1390, 1660, 1775)).save(folder / "remaining-faint-right.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    pdf = root / "examples/sample-hall.pdf"
    references = json.loads((root / "tests/fixtures/sample-cell-polygons.json").read_text())
    profile = json.loads((root / "examples/sample-exclusions.json").read_text())
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if sha != references["pdf_sha256"] or sha != profile["pdf_sha256"]:
        raise ValueError("Sample/reference/exclusion checksums do not match.")
    outputs, durations = {}, {}
    with tempfile.TemporaryDirectory() as temporary:
        pages = render_pdf(pdf, Path(temporary))
        image_path = Path(temporary) / pages[0]["filename"]
        for name, strategy in (("v1.1", "multiscale"), ("v1.2", "boundaries")):
            start = time.perf_counter()
            outputs[name] = detect_seats(image_path, DetectSpec(strategy=strategy, recover_groups=False), profile["zones"], pdf)
            durations[name] = round(time.perf_counter() - start, 3)
        report = {"source_pdf_sha256": sha, "reference_method": references["method"], "reference_cells": len(references["cases"]), "opencv_version": cv2.__version__, "detection_seconds": durations, "versions": {name: {**compare(value["seats"], references), "candidate_report": value["report"]} for name, value in outputs.items()}}
        (args.output / "geometry-audit.json").write_text(json.dumps(report, indent=2))
        new = outputs["v1.2"]
        canonical = json.dumps(new, sort_keys=True, separators=(",", ":")).encode()
        detection_report = {"source_pdf_sha256": sha, "canonical_masked_result_sha256": hashlib.sha256(canonical).hexdigest(), "single_detection_seconds_observed": durations["v1.2"], "manually_annotated_exclusion_areas": len(profile["zones"]), **new["report"], "limitations": ["Candidate count is not measured seat recall.", "24 reference cells were used during development; not a held-out full-hall audit.", "Some faint outer-right positions and loje symbols are still missed; false positives remain.", "All identities and exclusions still require review."]}
        (args.output / "sample-detection-report.json").write_text(json.dumps(detection_report, indent=2))
        overlay = {"format": "seatplan-overlay-v1", "plan": {"name": "Supplied hall - v1.2 UNREVIEWED geometry draft", "sha256": sha, "pages": pages, "zones": profile["zones"], "seats": new["seats"]}}
        (args.output / "sample-detection-overlay.json").write_text(json.dumps(overlay, indent=2))
        with Image.open(image_path) as image:
            visuals(image, outputs["v1.1"]["seats"], new["seats"], args.output)
    print(json.dumps({name: {key: value for key, value in metrics.items() if key not in ("cases", "candidate_report")} for name, metrics in report["versions"].items()}, indent=2))
    print("Detection seconds:", durations)


if __name__ == "__main__":
    main()
