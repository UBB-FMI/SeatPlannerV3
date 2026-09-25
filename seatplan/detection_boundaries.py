"""Pixel-supported quadrilateral refinement, not a seat-count oracle.

Contour and line masks propose cells. Four independent printed border ridges are
then fitted in the ORIGINAL grayscale image by bounded exhaustive endpoint search.
Existing multi-pass candidates are assignment priors: isolated glyph fragments
must not displace an entire numbered cell simply because their contrast is higher.
There is no OCR, trained model, hall coordinate list, or random sampling here.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from .domain import DetectSpec


class SpatialIndex:
    def __init__(self, pitch: float) -> None:
        self.pitch = max(8, pitch)
        self.bins: dict[tuple[int, int], list[dict]] = {}

    def add(self, candidate: dict) -> None:
        x, y = candidate["center"]
        self.bins.setdefault((int(x // self.pitch), int(y // self.pitch)), []).append(candidate)

    def near(self, center: np.ndarray, radius: float | None = None) -> list[dict]:
        bx, by = (center // self.pitch).astype(int)
        reach = max(1, math.ceil((radius or self.pitch) / self.pitch))
        return [item for xx in range(bx - reach, bx + reach + 1) for yy in range(by - reach, by + reach + 1) for item in self.bins.get((xx, yy), [])]


def describe(points: np.ndarray, **properties) -> dict:
    points = np.asarray(points, np.float32)
    rect = cv2.minAreaRect(points)
    return {"quad": points, "rect": rect, "center": points.mean(0), "area": cv2.contourArea(points), "size": max(rect[1]), **properties}


def overlap(first: dict, second: dict) -> float:
    # Cheap AABB rejection before exact convex clipping.
    if np.any(np.abs(first["center"] - second["center"]) > (first["size"] + second["size"])):
        return 0.0
    area, _ = cv2.intersectConvexConvex(first["quad"], second["quad"])
    return area / max(1, min(first["area"], second["area"]))


def valid_quad(points: np.ndarray | None, minimum: float, maximum: float) -> bool:
    if points is None or cv2.isContourConvex(points) is False:
        return False
    edges = np.roll(points, -1, axis=0) - points
    lengths = np.linalg.norm(edges, axis=1)
    if lengths.min() < minimum or lengths.max() > maximum:
        return False
    cosines = np.sum(edges * np.roll(edges, -1, axis=0), axis=1) / (lengths * np.roll(lengths, -1))
    return bool(abs(cosines).max() < 0.72 and min(lengths[0], lengths[2]) / max(lengths[0], lengths[2]) > 0.5 and min(lengths[1], lengths[3]) / max(lengths[1], lengths[3]) > 0.5)


def interior_density(binary: np.ndarray, points: np.ndarray) -> float:
    center = points.mean(0)
    polygon = center + (points - center) * 0.62
    x, y, width, height = cv2.boundingRect(polygon)
    if x < 0 or y < 0 or x + width > binary.shape[1] or y + height > binary.shape[0]:
        return 1.0
    mask = np.zeros((height, width), np.uint8)
    cv2.fillConvexPoly(mask, np.round(polygon - [x, y]).astype(np.int32), 255)
    return cv2.mean(binary[y:y + height, x:x + width], mask)[0] / 255


def directional_lines(gray: np.ndarray, length: int, corrected: bool) -> np.ndarray:
    ink = cv2.subtract(cv2.GaussianBlur(gray, (0, 0), 17), gray) if corrected else 255 - gray
    response = np.zeros_like(gray)
    for angle in range(0, 180, 5):
        radians = math.radians(angle)
        center = (length - 1) / 2
        dx, dy = math.cos(radians) * center, math.sin(radians) * center
        kernel = np.zeros((length, length), np.uint8)
        cv2.line(kernel, (round(center - dx), round(center - dy)), (round(center + dx), round(center + dy)), 1, 1)
        closing = np.zeros((5, 5), np.uint8)
        cx, cy = math.cos(radians) * 2, math.sin(radians) * 2
        cv2.line(closing, (round(2 - cx), round(2 - cy)), (round(2 + cx), round(2 + cy)), 1, 1)
        response = cv2.max(response, cv2.morphologyEx(cv2.morphologyEx(ink, cv2.MORPH_CLOSE, closing), cv2.MORPH_OPEN, kernel))
    return response


def proposals(gray: np.ndarray, spec: DetectSpec, binary: np.ndarray) -> list[dict]:
    raw = []
    height, width = gray.shape
    maximum = spec.max_size * 1.15

    def add(mask: np.ndarray, origin: str) -> None:
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            return
        for i, contour in enumerate(contours):
            (cx, cy), (w, h), _ = cv2.minAreaRect(contour)
            area = cv2.contourArea(contour)
            if min(w, h) < spec.min_size or max(w, h) > maximum or area < spec.min_size ** 2 or area / (w * h) < 0.60:
                continue
            if spec.roi is not None:
                x, y, rw, rh = spec.roi
                if not (x <= cx / width <= x + rw and y <= cy / height <= y + rh):
                    continue
            hull = cv2.convexHull(contour)
            if len(hull) < 4:
                continue
            polygon = cv2.approxPolyDP(hull, 0.025 * cv2.arcLength(hull, True), True)
            if len(polygon) != 4:
                try:
                    polygon = cv2.approxPolyN(hull, 4)
                except cv2.error:
                    continue
            polygon = polygon.reshape(4, 2).astype(np.float32)
            if valid_quad(polygon, spec.min_size, maximum) is False:
                continue
            depth, parent = 0, int(hierarchy[0, i, 3])
            while parent >= 0:
                depth, parent = depth + 1, int(hierarchy[0, parent, 3])
            if depth % 2 == 0 and interior_density(binary, polygon) > 0.15:
                continue
            raw.append(describe(polygon, origin=origin))
            if len(raw) > 50000:
                raise ValueError("Too many boundary proposals. Select a smaller detection region.")

    for block, constant in [(31, 12), (61, 8), (31, 4), (61, 3), (101, 2)]:
        mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, constant)
        for size in [1, 2, 3]:
            add(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8)), "contour")
    # Multi-angle line openings provide proposals only; they never become proof
    # of a border without the later original-pixel test.
    for length, corrected in [(17, True), (23, False)]:
        response = directional_lines(gray, length, corrected)
        for threshold in [8, 15, 25]:
            add(cv2.morphologyEx((response > threshold).astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)), "directional-lines")
    lines = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(gray)[0]
    if lines is not None:
        lines = lines[:, 0, :]
        lengths = np.linalg.norm(lines[:, 2:] - lines[:, :2], axis=1)
        for limit, extension in [(16, 2), (16, 5), (21, 3)]:
            mask = np.zeros_like(gray)
            for x1, y1, x2, y2 in lines[lengths > limit]:
                direction = np.array([x2 - x1, y2 - y1])
                direction = direction / np.linalg.norm(direction) * extension
                cv2.line(mask, tuple(np.round([x1 - direction[0], y1 - direction[1]]).astype(int)), tuple(np.round([x2 + direction[0], y2 + direction[1]]).astype(int)), 255, 2)
            add(mask, "line-segments")
    index, unique = SpatialIndex(maximum), []
    for item in sorted(raw, key=lambda c: -c["area"]):
        if any(other["area"] < item["area"] * 1.25 and overlap(item, other) > 0.8 for other in index.near(item["center"])):
            continue
        unique.append(item)
        index.add(item)
    if len(unique) > 6000:
        raise ValueError("Too many distinct boundaries. Restrict the detection region.")
    return unique


def fit_quad(gray: np.ndarray, polygon: np.ndarray, minimum: float = 9, maximum: float = 80) -> dict | None:
    lines, scores, supports = [], [], []
    for start, end in zip(polygon, np.roll(polygon, -1, axis=0)):
        direction = end - start
        length = np.linalg.norm(direction)
        if length < 2:
            return None
        normal = np.array([-direction[1], direction[0]]) / length
        t = np.linspace(0.10, 0.90, min(160, max(24, int(length))), dtype=np.float32)
        span = min(7, max(3, length * 0.14))
        values = np.arange(-math.ceil(span), math.ceil(span) + 0.1, 1, dtype=np.float32)
        offset_a, offset_b = np.meshgrid(values, values)
        offset_a, offset_b = offset_a.ravel(), offset_b.ravel()
        offsets = offset_a[:, None] * (1 - t) + offset_b[:, None] * t
        points = start[None, None, :] + t[None, :, None] * direction + offsets[:, :, None] * normal

        def sample(xy: np.ndarray) -> np.ndarray:
            return cv2.remap(gray, xy[:, :, 0].astype(np.float32), xy[:, :, 1].astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)

        response = np.clip(((sample(points + 3 * normal) + sample(points - 3 * normal)) / 2 - sample(points)) / 60, 0, 1)
        objective = response.mean(1) * 0.7 + np.quantile(response, 0.2, axis=1) * 0.3 - 0.004 * (np.abs(offset_a) + np.abs(offset_b))
        best = int(np.argmax(objective))
        a, b = start + normal * offset_a[best], end + normal * offset_b[best]
        delta = b - a
        n = np.array([-delta[1], delta[0]]) / np.linalg.norm(delta)
        lines.append((n, float(n @ a)))
        scores.append(float(objective[best]))
        supports.append(float(np.mean(response[best] > 0.12)))
    corners = []
    for i in range(4):
        first, second = lines[i - 1], lines[i]
        try:
            corners.append(np.linalg.solve(np.array([first[0], second[0]]), np.array([first[1], second[1]])))
        except np.linalg.LinAlgError:
            return None
    fitted = np.array(corners, np.float32)
    if valid_quad(fitted, minimum, maximum) is False or min(scores) < 0.12 or min(supports) < 0.5 or np.mean(scores) < 0.28:
        return None
    return describe(fitted, score=float(0.6 * np.mean(scores) + 0.4 * min(scores)), sides=scores, support=supports)


def refine_candidates(gray: np.ndarray, seeds: list[dict], spec: DetectSpec) -> tuple[list[dict], dict]:
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 61, 5)
    maximum = spec.max_size * 1.15
    raw = proposals(gray, spec, binary)
    fits, fit_index = [], SpatialIndex(maximum)
    for candidate in raw:
        fitted = fit_quad(gray, candidate["quad"], spec.min_size, maximum)
        if fitted is None:
            continue
        fitted["origin"] = candidate["origin"]
        fitted["density"] = interior_density(binary, fitted["quad"])
        fits.append(fitted)
        fit_index.add(fitted)
    result, unfitted = [], 0
    for candidate in seeds:
        original = describe(cv2.boxPoints(candidate["rect"]))
        nearby = [c for c in fit_index.near(original["center"]) if np.linalg.norm(c["center"] - original["center"]) < original["size"] * 0.6 and 0.55 < c["area"] / original["area"] < 1.35 and overlap(original, c) > 0.75]

        def ranking(c: dict) -> float:
            intersection, _ = cv2.intersectConvexConvex(original["quad"], c["quad"])
            return 0.8 * intersection / max(1, original["area"] + c["area"] - intersection) + 0.2 * c["score"]

        selected = max(nearby, key=ranking) if nearby else fit_quad(gray, original["quad"], spec.min_size, maximum)
        if selected is not None and 0.55 < selected["area"] / original["area"] < 1.4:
            item = dict(selected, repair=candidate.get("repair", False), warning="")
        else:
            item = dict(original, score=0.1, repair=candidate.get("repair", False), warning="No reliable four-border fit; inspect this shape.")
            unfitted += 1
        item["density"] = interior_density(binary, item["quad"])
        result.append(item)
    # Tiny chair evidence may replace an oversized empty compartment. It must
    # not split normal numbered cells at their glyph baselines.
    chairs = [c for c in fits if c["size"] <= min(28, spec.symbol_max_size) and c["area"] >= 170 and c["density"] < 0.23 and min(c["support"]) >= 0.7 and c["score"] >= 0.35]
    chair_index = SpatialIndex(maximum)
    for chair in chairs:
        chair_index.add(chair)
    cleaned, removed = [], 0
    for item in result:
        children = []
        if item["size"] > 34 and item["density"] < 0.18:
            children = [c for c in chair_index.near(item["center"]) if item["area"] > 1.65 * c["area"] and overlap(item, c) > 0.85]
        if children:
            removed += 1
        else:
            cleaned.append(item)
    result, index = cleaned, SpatialIndex(maximum)
    for item in result:
        index.add(item)
    added = 0
    for item in sorted(fits, key=lambda c: -c["score"]):
        small = item["size"] <= 34
        if min(item["support"]) < 0.8 or (small and item["density"] > 0.23) or (small is False and item["density"] < 0.035):
            continue
        near = index.near(item["center"])
        if any(overlap(item, other) > 0.38 for other in near):
            continue
        peers = [other for other in index.near(item["center"], item["size"] * 3) if np.linalg.norm(other["center"] - item["center"]) < item["size"] * 3 and 0.7 < other["area"] / item["area"] < 1.4]
        if len(peers) < 2:
            continue
        item = dict(item, warning="Additional boundary candidate; verify that it is a seat.")
        result.append(item)
        index.add(item)
        added += 1
    result, lattice_report = reconcile_lattice(gray, result, spec, binary)
    recovery_report = {}
    if spec.recover_groups:
        from .detection_recovery import recover_residuals
        result, recovery_report = recover_residuals(gray, result, fits, spec, binary)
    ambiguous = 0
    for item in result:
        if item["size"] > 34 and item["density"] < 0.025:
            item["warning"] = "Possible blank compartment rather than a seat; inspect or delete."
            item["score"] = min(0.15, item["score"])
            ambiguous += 1
    result.sort(key=lambda c: (round(float(c["center"][1]), 2), round(float(c["center"][0]), 2), -c["score"]))
    return result, {**lattice_report, **recovery_report, "boundary_proposals": len(raw), "fitted_proposals": len(fits), "unfitted_seeds": unfitted, "removed_compartment_candidates": removed, "additional_candidates": added, "ambiguous_empty_candidates": ambiguous}


def reconcile_lattice(gray: np.ndarray, items: list[dict], spec: DetectSpec, binary: np.ndarray) -> tuple[list[dict], dict]:
    """Use repeated local cell dimensions to reject fragments and recover gaps.

    A proposed neighboring cell still needs four original-pixel-supported edges;
    no divider is invented purely to make a regular grid or a desired seat count.
    This is a local consistency pass, not global optimal graph reconstruction.
    """
    maximum = spec.max_size * 1.15
    index = SpatialIndex(maximum)

    def characterize(item: dict) -> None:
        edges = np.linalg.norm(np.roll(item["quad"], -1, axis=0) - item["quad"], axis=1)
        item["shape"] = np.sort([(edges[0] + edges[2]) / 2, (edges[1] + edges[3]) / 2])
        item["density"] = interior_density(binary, item["quad"])

    def peer_count(item: dict) -> int:
        return sum(bool(np.linalg.norm(item["center"] - other["center"]) < item["size"] * 2.6 and np.all(other["shape"] / item["shape"] > 0.85) and np.all(other["shape"] / item["shape"] < 1.18) and 0.8 < other["area"] / item["area"] < 1.2 and overlap(item, other) < 0.1) for other in index.near(item["center"], item["size"] * 2.6))

    for item in items:
        characterize(item)
        index.add(item)
    for item in items:
        item["peers"] = peer_count(item)
    strong = [item for item in items if item["peers"] >= 3 and item["size"] > spec.symbol_max_size + 1 and item["density"] > 0.04]
    added = []
    height, width = gray.shape
    for item in strong:
        polygon = item["quad"]
        along = ((polygon[1] - polygon[0]) + (polygon[2] - polygon[3])) / 2
        down = ((polygon[3] - polygon[0]) + (polygon[2] - polygon[1])) / 2
        for delta in [along, -along, down, -down]:
            prediction = describe(polygon + delta)
            if np.any(prediction["quad"].min(0) < 0) or np.any(prediction["quad"].max(0) > [width, height]):
                continue
            if spec.roi is not None:
                x, y, rw, rh = spec.roi
                cx, cy = prediction["center"]
                if not (x <= cx / width <= x + rw and y <= cy / height <= y + rh):
                    continue
            if any(other.get("peers", 0) >= 3 and overlap(prediction, other) > 0.6 for other in index.near(prediction["center"])):
                continue
            fitted = fit_quad(gray, prediction["quad"], spec.min_size, maximum)
            if fitted is None or fitted["score"] < 0.4 or min(fitted["support"]) < 0.7 or not (0.8 < fitted["area"] / item["area"] < 1.2):
                continue
            characterize(fitted)
            fitted["peers"] = peer_count(fitted)
            if fitted["peers"] < 3 or fitted["density"] < 0.04:
                continue
            fitted["warning"] = "Local-grid proposal fitted to visible borders; verify the seat identity."
            fitted["origin"] = "local-grid"
            added.append(fitted)
            index.add(fitted)
    combined, kept, index = items + added, [], SpatialIndex(maximum)
    ranked = sorted(combined, key=lambda item: (item["peers"] >= 3, min(item["peers"], 6), item["score"]), reverse=True)
    for item in ranked:
        if any(overlap(item, other) > 0.3 for other in index.near(item["center"])):
            continue
        kept.append(item)
        index.add(item)
    return kept, {"local_grid_proposals": len(added), "overlapping_alternatives_removed": len(combined) - len(kept)}
