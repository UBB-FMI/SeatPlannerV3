"""Deterministic contour ensembles for faint, rotated seat outlines.

No OCR, learned model, reference seat positions, or hall-specific geometry is
used here. Explicit exclusion polygons are applied by detection.py afterwards.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from .domain import DetectSpec


def interior_ink(binary: np.ndarray, rect: tuple) -> float:
    """Ink fraction away from the outline; reject solid glyphs as chair symbols."""
    (cx, cy), (w, h), angle = rect
    polygon = cv2.boxPoints(((cx, cy), (w * 0.7, h * 0.7), angle)).astype(np.int32)
    x, y, rw, rh = cv2.boundingRect(polygon)
    if x < 0 or y < 0 or x + rw > binary.shape[1] or y + rh > binary.shape[0]:
        return 1.0
    mask = np.zeros((rh, rw), np.uint8)
    cv2.fillConvexPoly(mask, polygon - (x, y), 255)
    return cv2.mean(binary[y:y + rh, x:x + rw], mask)[0] / 255


def outline_support(dilated_ink: np.ndarray, rect: tuple) -> list[float]:
    """Require real ink along all four sides, not just a newly closed contour.

    Directional closing can also enclose the *space between* two chairs. Testing
    against the original threshold image rejects gaps with no end borders.
    """
    height, width = dilated_ink.shape
    points = cv2.boxPoints(rect)
    t = np.linspace(0.15, 0.85, 15)[:, None]
    support = []
    for start, end in zip(points, np.roll(points, -1, axis=0)):
        xy = start[None, :] * (1 - t) + end[None, :] * t
        xx = np.clip(np.round(xy[:, 0]).astype(int), 0, width - 1)
        yy = np.clip(np.round(xy[:, 1]).astype(int), 0, height - 1)
        support.append(float(np.mean(dilated_ink[yy, xx] > 0)))
    return support


def split_symbol_groups(raw: list[dict], symbol_max: float) -> list[dict]:
    """Discard a merged pair's enclosure when two separate symbol interiors exist.

    The test is deliberately limited to small symbols: a large numbered seat
    must not be replaced by the whitespace above/below its printed number.
    """
    bins: dict[tuple[int, int], list[dict]] = {}
    seen = set()
    for candidate in raw:
        rect = candidate["rect"]
        key = tuple(round(value / 2) for value in (*rect[0], *sorted(rect[1])))
        if key in seen:
            continue
        seen.add(key)
        x, y = rect[0]
        bins.setdefault((int(x // 64), int(y // 64)), []).append(candidate)
    kept = []
    for candidate in raw:
        (cx, cy), (w, h), _ = candidate["rect"]
        area = w * h
        if max(w, h) > symbol_max * 1.25 or min(w, h) > symbol_max * 0.8125 or area < 300:
            kept.append(candidate)
            continue
        children = []
        for bx in range(int(cx // 64) - 1, int(cx // 64) + 2):
            for by in range(int(cy // 64) - 1, int(cy // 64) + 2):
                for child in bins.get((bx, by), []):
                    (x, y), (cw, ch), _ = child["rect"]
                    child_area = cw * ch
                    if not (0.2 <= child_area / area <= 0.68 and child["score"] >= 0.72 and math.hypot(cx - x, cy - y) < max(w, h) / 2):
                        continue
                    _, polygon = cv2.rotatedRectangleIntersection(candidate["rect"], child["rect"])
                    overlap = cv2.contourArea(polygon) if polygon is not None else 0
                    if overlap / child_area > 0.9:
                        children.append(child)
        split = False
        for index, first in enumerate(children):
            first_area = first["rect"][1][0] * first["rect"][1][1]
            for second in children[index + 1:]:
                second_area = second["rect"][1][0] * second["rect"][1][1]
                if (first_area + second_area) / area < 0.58:
                    continue
                _, polygon = cv2.rotatedRectangleIntersection(first["rect"], second["rect"])
                overlap = cv2.contourArea(polygon) if polygon is not None else 0
                if overlap / min(first_area, second_area) < 0.12:
                    split = True
                    break
            if split:
                break
        if split is False:
            kept.append(candidate)
    return kept


def contour_candidates(gray: np.ndarray, spec: DetectSpec) -> list[dict]:
    height, width = gray.shape
    enhanced = spec.strategy == "multiscale"
    thresholds = [(31, 12), (61, 8)]
    if enhanced:
        thresholds += [(31, 4), (61, 3)]
    variants = [cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, constant) for block, constant in thresholds]
    variants.append(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1])
    sizes = sorted({1, spec.close_size, min(5, spec.close_size + 1)}) if enhanced else [spec.close_size]
    kernels = [(np.ones((size, size), np.uint8), False) for size in sizes]
    if enhanced and spec.repair_symbols and spec.min_size < spec.symbol_max_size:
        for length in sorted({max(3, spec.gap_size - 2), spec.gap_size}):
            for angle in range(0, 180, 30):
                kernel = np.zeros((length, length), np.uint8)
                center = (length - 1) / 2
                dx, dy = math.cos(math.radians(angle)) * center, math.sin(math.radians(angle)) * center
                cv2.line(kernel, (round(center - dx), round(center - dy)), (round(center + dx), round(center + dy)), 1, 1)
                kernels.append((kernel, True))
    raw = []
    for variant_index, original in enumerate(variants):
        support_image = cv2.dilate(original, np.ones((3, 3), np.uint8))
        for kernel_index, (kernel, repair) in enumerate(kernels):
            binary = cv2.morphologyEx(original, cv2.MORPH_CLOSE, kernel)
            contours, hierarchy = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            if hierarchy is None:
                continue
            for index, contour in enumerate(contours):
                rect = cv2.minAreaRect(contour)
                (cx, cy), (w, h), _ = rect
                if min(w, h) < spec.min_size or max(w, h) > spec.max_size or min(w, h) / max(w, h) < 0.28:
                    continue
                if spec.roi is not None:
                    rx, ry, rwidth, rheight = spec.roi
                    if not (rx <= cx / width <= rx + rwidth and ry <= cy / height <= ry + rheight):
                        continue
                score = cv2.contourArea(contour) / (w * h)
                if score < spec.rectangularity:
                    continue
                depth = 0
                parent = int(hierarchy[0][index][3])
                while parent >= 0:
                    depth += 1
                    parent = int(hierarchy[0][parent][3])
                if enhanced is False and depth % 2 == 0:
                    continue
                if enhanced and (depth % 2 == 0 or repair):
                    if interior_ink(original, rect) > 0.12:
                        continue
                if repair:
                    if max(w, h) > spec.symbol_max_size or score < 0.72:
                        continue
                    sides = outline_support(support_image, rect)
                    if min(sides) < 0.45 or sum(sides) / 4 < 0.75:
                        continue
                raw.append({"rect": rect, "score": score, "variant": variant_index, "kernel": kernel_index, "repair": repair})
    if enhanced:
        raw = split_symbol_groups(raw, spec.symbol_max_size)
        # Normal closed cells take precedence; contained text fragments do not
        # steal their place. Repair outlines only fill remaining gaps.
        raw.sort(key=lambda item: (item["repair"], -round(item["rect"][1][0] * item["rect"][1][1], 2), -round(item["score"], 4), item["variant"], item["kernel"]))
    else:
        raw.sort(key=lambda item: (-round(item["score"], 4), -round(item["rect"][1][0] * item["rect"][1][1], 2), item["variant"], item["rect"][0][1], item["rect"][0][0]))
    return raw
