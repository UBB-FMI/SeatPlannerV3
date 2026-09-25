"""Deterministic raster geometry detection. No OCR, model, network, or learned weights.

Outputs are *candidates*, never approved seats. Printed labels are read only when
an actual PDF text layer exists. A scanned seat number is NOT guessed.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from .domain import DetectSpec, excluded
from .detection_shapes import contour_candidates
from .geometry import quad_geometry


def render_pdf(path: Path, output_dir: Path, max_pages: int = 10, long_edge: int = 2400) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    with pdfium.PdfDocument(path) as document:
        if len(document) < 1 or len(document) > max_pages:
            raise ValueError(f"PDF must contain between 1 and {max_pages} pages.")
        for index in range(len(document)):
            page = document[index]
            try:
                pw, ph = page.get_size()
                if not (1 <= pw <= 14400 and 1 <= ph <= 14400):
                    raise ValueError("Unsupported PDF page dimensions.")
                bitmap = page.render(scale=long_edge / max(pw, ph), draw_annots=True)
                try:
                    image = bitmap.to_pil().convert("RGB")
                    filename = f"page-{index + 1:03d}.png"
                    image.save(output_dir / filename, optimize=True)
                finally:
                    bitmap.close()
                text = page.get_textpage()
                try:
                    has_text = text.count_chars() > 0
                finally:
                    text.close()
                pages.append({"index": index, "width": image.width, "height": image.height, "filename": filename, "has_text": has_text})
            finally:
                page.close()
    return pages


def diagonal_mark(gray: np.ndarray, rect: tuple) -> bool:
    """Conservative X-like mark heuristic in a deskewed candidate interior.

    This can miss faint strokes and can confuse glyphs with strokes. All output
    still requires administrator review; explicit exclusion masks take priority.
    """
    (cx, cy), (rw, rh), angle = rect
    width, height = max(6, round(rw)), max(6, round(rh))
    points = cv2.boxPoints(rect).astype(np.float32)
    destination = np.array([[0, height - 1], [0, 0], [width - 1, 0], [width - 1, height - 1]], dtype=np.float32)
    patch = cv2.warpPerspective(gray, cv2.getPerspectiveTransform(points, destination), (width, height), borderValue=255)
    # Ignore the seat outline itself.
    margin = max(1, round(min(width, height) * 0.08))
    patch = patch[margin:height - margin, margin:width - margin]
    if min(patch.shape) < 5:
        return False
    ink = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    h, w = ink.shape
    lines = cv2.HoughLinesP(ink, 1, math.pi / 180, threshold=max(5, int(min(w, h) * 0.45)), minLineLength=max(w, h) * 0.6, maxLineGap=max(1, int(max(w, h) * 0.15)))
    directions = set()
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            dx, dy = int(x2) - int(x1), int(y2) - int(y1)
            if abs(dx) >= w * 0.55 and abs(dy) >= h * 0.55:
                directions.add(1 if dx * dy > 0 else -1)
    return len(directions) == 2


def detect_seats(image_path: Path, spec: DetectSpec, zones: list[dict] | None = None, pdf_path: Path | None = None) -> dict:
    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)
    gray = np.array(Image.open(image_path).convert("L"))
    height, width = gray.shape
    if gray.size > 6_000_000:
        raise ValueError("Detection image is too large.")
    raw = contour_candidates(gray, spec.model_copy(update={"strategy": "multiscale"}) if spec.strategy == "boundaries" else spec)
    algorithm = "deterministic-contour-v2" if spec.strategy == "multiscale" else "deterministic-contour-v1"
    unique = []
    bins: dict[tuple[int, int], list] = {}
    bin_size = max(8, spec.max_size)
    for candidate in raw:
        (cx, cy), (rw, rh), angle = candidate["rect"]
        bx, by = int(cx // bin_size), int(cy // bin_size)
        duplicate = False
        for xx in range(bx - 1, bx + 2):
            for yy in range(by - 1, by + 2):
                for previous in bins.get((xx, yy), []):
                    (px, py), (pw, ph), _ = previous["rect"]
                    if abs(cx - px) > max(rw, rh, pw, ph) / 2 or abs(cy - py) > max(rw, rh, pw, ph) / 2:
                        continue
                    _, intersection = cv2.rotatedRectangleIntersection(candidate["rect"], previous["rect"])
                    intersection_area = cv2.contourArea(intersection) if intersection is not None else 0
                    if intersection_area / max(1, min(rw * rh, pw * ph)) > 0.65:
                        duplicate = True
                        break
                if duplicate:
                    break
            if duplicate:
                break
        if duplicate is False:
            unique.append(candidate)
            bins.setdefault((bx, by), []).append(candidate)
    filtered = []
    for candidate in unique:
        (cx, cy), (rw, rh), _ = candidate["rect"]
        support = 0
        for other in unique:
            if other is candidate:
                continue
            (ox, oy), (ow, oh), _ = other["rect"]
            if 0.50 <= min(rw, rh) / min(ow, oh) <= 2 and 0.50 <= max(rw, rh) / max(ow, oh) <= 2 and math.hypot(cx - ox, cy - oy) <= max(rw, rh) * 4:
                support += 1
                if support >= spec.neighbours:
                    break
        if support >= spec.neighbours:
            filtered.append(candidate)
    boundary_report = {}
    if spec.strategy == "boundaries":
        from .detection_boundaries import refine_candidates
        filtered, boundary_report = refine_candidates(gray, filtered, spec)
        algorithm = "deterministic-boundaries-v4" if spec.recover_groups else "deterministic-boundaries-v3"
    filtered.sort(key=lambda item: (round(item["rect"][0][1], 2), round(item["rect"][0][0], 2), -item["score"]))
    if len(filtered) > 5000:
        raise ValueError("More than 5,000 candidates. Restrict the region or increase minimum size.")
    document = pdfium.PdfDocument(pdf_path) if pdf_path is not None else None
    page = document[spec.page] if document is not None else None
    text = page.get_textpage() if page is not None else None
    has_text = text is not None and text.count_chars() > 0
    seats = []
    try:
        for index, candidate in enumerate(filtered):
            (cx, cy), (rw, rh), angle = candidate["rect"]
            # Canonicalize near-upright rectangles so manual dimensions are intuitive.
            if angle > 45:
                rw, rh, angle = rh, rw, angle - 90
            if spec.strategy == "multiscale" and angle < -45:
                rw, rh, angle = rh, rw, angle + 90
            geometry = [round(cx / width, 7), round(cy / height, 7), round(rw / width, 7), round(rh / height, 7), round(angle, 3)]
            label = f"D{index + 1:04d}"
            note = "Provisional identifier; verify the printed seat number."
            if has_text:
                import re
                pw, ph = page.get_size()
                native = text.get_text_bounded((cx - rw / 2) / width * pw, (1 - (cy + rh / 2) / height) * ph, (cx + rw / 2) / width * pw, (1 - (cy - rh / 2) / height) * ph).strip()
                if re.fullmatch(r"[A-Za-z]?\d{1,5}[A-Za-z]?", native):
                    label, note = native, "Label from PDF text layer; verify section and row."
            polygon_geometry = quad_geometry(candidate["quad"], width, height) if "quad" in candidate else None
            if polygon_geometry is not None:
                geometry = [round(polygon_geometry[key], 7) for key in ["x", "y", "w", "h", "angle"]]
                if candidate.get("warning"):
                    note = candidate["warning"] + " " + note
            geometry_id = hashlib.sha256(f"{spec.page}:{spec.section}:{geometry}".encode()).hexdigest()[:24]
            crossed = spec.detect_crosses and diagonal_mark(gray, candidate["rect"])
            seat = {"id": f"d-{geometry_id}", "page": spec.page, "section": spec.section, "row": "", "label": label, "x": geometry[0], "y": geometry[1], "w": geometry[2], "h": geometry[3], "angle": geometry[4], "reviewed": False, "blocked": bool(crossed), "source": algorithm + ("-repair" if candidate.get("repair") else ""), "score": round(min(1, candidate["score"]), 4), "note": "Possible cross-out. " + note if crossed else note}
            if candidate.get("recovery"):
                seat["source"] += "-" + candidate["recovery"]
            if polygon_geometry is not None:
                seat["outline"] = [[round(value, 7) for value in point] for point in polygon_geometry["outline"]]
            if excluded(seat, zones or []):
                seat["blocked"] = True
                seat["note"] = "Inside an explicit exclusion mask. " + note
            seats.append(seat)
    finally:
        if text is not None:
            text.close()
        if page is not None:
            page.close()
        if document is not None:
            document.close()
    return {"seats": seats, "report": {"algorithm": algorithm, "strategy": spec.strategy, **boundary_report, "repair_candidates": sum(seat["source"].endswith("-repair") for seat in seats), "candidates": len(seats), "blocked_candidates": sum(seat["blocked"] for seat in seats), "review_required": len(seats), "native_text": bool(has_text), "image_width": width, "image_height": height, "warnings": ["Candidates are not a verified seat inventory.", "Cross-out detection is heuristic: inspect exclusions and missing/merged/split boxes.", "Scanned labels use provisional D-numbers; no OCR or label guessing is performed."]}}
