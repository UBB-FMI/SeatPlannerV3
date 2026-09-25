"""Conservative residual recovery after the v1.2 boundary pass.

A disconnected bank may support itself through shared edges; it need not already
contain an accepted seed. Small, repeated empty chair symbols are searched at
pixel resolution around existing chair evidence. Coordinates and seat counts of
the example venue are deliberately absent from this module.
"""
from __future__ import annotations

import math
from collections import defaultdict

import cv2
import numpy as np

from .detection_boundaries import SpatialIndex, describe, interior_density, overlap
from .domain import DetectSpec


def edge_shape(item: dict) -> tuple[float, float, float]:
    edges = np.roll(item["quad"], -1, axis=0) - item["quad"]
    lengths = np.linalg.norm(edges, axis=1)
    pair = [(lengths[0] + lengths[2]) / 2, (lengths[1] + lengths[3]) / 2]
    index = int(pair[1] > pair[0])
    axis = edges[index] - edges[(index + 2) % 4]
    return max(pair), min(pair), math.degrees(math.atan2(float(axis[1]), float(axis[0]))) % 180


def angle_distance(first: float, second: float) -> float:
    return abs((first - second + 90) % 180 - 90)


def components(items: list[dict], predicate, radius: float) -> list[list[int]]:
    index = SpatialIndex(radius)
    for number, item in enumerate(items):
        index.add(dict(item, component_index=number))
    seen: set[int] = set()
    groups = []
    for start in range(len(items)):
        if start in seen:
            continue
        group, stack = [], [start]
        seen.add(start)
        while stack:
            current = stack.pop()
            group.append(current)
            for other in index.near(items[current]["center"], radius):
                number = other["component_index"]
                if number not in seen and predicate(items[current], items[number]):
                    seen.add(number)
                    stack.append(number)
        groups.append(group)
    return groups


def shared_edge(first: dict, second: dict) -> bool:
    if not (0.75 < first["area"] / second["area"] < 1.34) or overlap(first, second) > 0.12:
        return False
    for a, b in zip(first["quad"], np.roll(first["quad"], -1, axis=0)):
        for c, d in zip(second["quad"], np.roll(second["quad"], -1, axis=0)):
            distance = min(max(np.linalg.norm(a - c), np.linalg.norm(b - d)), max(np.linalg.norm(a - d), np.linalg.norm(b - c)))
            if distance < max(3.5, min(np.linalg.norm(b - a), np.linalg.norm(d - c)) * 0.12):
                return True
    return False


def recover_banks(items: list[dict], fits: list[dict], spec: DetectSpec) -> tuple[list[dict], dict]:
    index = SpatialIndex(spec.max_size * 1.2)
    for item in items:
        index.add(item)
    pool = []
    for item in sorted(fits, key=lambda c: -c["score"]):
        if item["size"] <= spec.symbol_max_size + 1 or item["size"] > spec.max_size or item["score"] < 0.60 or min(item["support"]) < 0.85 or not (0.05 < item["density"] < 0.65):
            continue
        if any(overlap(item, other) > 0.30 for other in index.near(item["center"])):
            continue
        pool.append(item)
        index.add(item)
    added, accepted_groups = [], 0
    for group in components(pool, shared_edge, spec.max_size * 1.5):
        if len(group) < 3:
            continue
        accepted_groups += 1
        for number in group:
            added.append(dict(pool[number], origin="shared-edge-group", recovery="shared-edge-group", warning="Recovered as part of a separate group with shared borders; verify seat identity."))
    return items + added, {"recovered_bank_groups": accepted_groups, "recovered_bank_cells": len(added)}


def ring_kernels(width: float, height: float, angle: float) -> list[np.ndarray]:
    """Four signed grayscale ridge filters, with bilinear kernel deposition."""
    half = math.ceil(math.hypot(width, height) / 2 + 5)
    size = half * 2 + 1
    points = cv2.boxPoints(((0.0, 0.0), (float(width), float(height)), float(angle)))
    kernels = []

    def deposit(kernel: np.ndarray, point: np.ndarray, weight: float) -> None:
        x, y = point + half
        ix, iy = math.floor(x), math.floor(y)
        fx, fy = x - ix, y - iy
        for dx, dy, factor in [(0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)]:
            kernel[iy + dy, ix + dx] += weight * factor

    for a, b in zip(points, np.roll(points, -1, axis=0)):
        direction = b - a
        normal = np.array([-direction[1], direction[0]]) / np.linalg.norm(direction)
        count = max(10, round(float(np.linalg.norm(direction))))
        kernel = np.zeros((size, size), np.float32)
        distance = min(2.5, height * 0.22)
        for t in np.linspace(0.12, 0.88, count):
            point = a * (1 - t) + b * t
            deposit(kernel, point, -1)
            deposit(kernel, point + distance * normal, 0.5)
            deposit(kernel, point - distance * normal, 0.5)
        kernels.append(kernel / count)
    return kernels


def paired_symbols(first: dict, second: dict) -> bool:
    """Alignment and pitch of a local pair/row; no assumed venue row count."""
    fw, fh, fa = first["symbol_shape"]
    sw, sh, sa = second["symbol_shape"]
    if angle_distance(fa, sa) > 12 or not (0.82 < fw / sw < 1.22 and 0.72 < fh / sh < 1.4):
        return False
    theta = math.radians(fa + ((sa - fa + 90) % 180 - 90) / 2)
    fw, fh = (fw + sw) / 2, (fh + sh) / 2
    delta = second["center"] - first["center"]
    along = abs(float(delta @ np.array([math.cos(theta), math.sin(theta)])))
    across = abs(float(delta @ np.array([-math.sin(theta), math.cos(theta)])))
    # Chairs in a short pair, or adjacent columns with a modest aisle/gap.
    paired = along < fw * 0.30 and 0.90 * fh < across < 1.75 * fh
    row = across < fh * 0.40 and fw * 1.25 < along < fw * 2.0
    return bool((paired or row) and overlap(first, second) < 0.13)


def scan_symbols(gray: np.ndarray, items: list[dict], spec: DetectSpec, binary: np.ndarray) -> tuple[list[dict], dict]:
    anchors = []
    for item in items:
        w, h, a = edge_shape(item)
        if w <= min(34, spec.symbol_max_size) and w >= spec.min_size * 1.6 and h >= spec.min_size and 1.35 < w / h < 2.5 and item["density"] < 0.045 and item["score"] >= 0.35:
            anchors.append(dict(item, symbol_shape=(w, h, a), anchor=True))
    if len(anchors) < 2:
        return items, {"symbol_scan_tiles": 0, "recovered_symbol_cells": 0, "replaced_symbol_enclosures": 0}
    # A faint compartment can have only one surviving chair. Use the robust
    # symbol size distribution across the page, then require a connected local
    # group AFTER searching. Requiring local peers before search repeats the
    # disconnected-bank failure and discards that last useful anchor.
    median_long, median_short = np.median([a["symbol_shape"][:2] for a in anchors], axis=0)
    anchors = [a for a in anchors if 0.78 < a["symbol_shape"][0] / median_long < 1.23 and 0.75 < a["symbol_shape"][1] / median_short < 1.30]
    index = SpatialIndex(100)
    for anchor in anchors:
        index.add(anchor)
    if len(anchors) < 2:
        return items, {"symbol_scan_tiles": 0, "recovered_symbol_cells": 0, "replaced_symbol_enclosures": 0}
    tile = 128
    height, width = gray.shape
    regions: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for anchor in anchors:
        radius = min(85, anchor["size"] * 3.7)
        x, y = anchor["center"]
        for tx in range(max(0, int((x - radius) // tile)), min((width - 1) // tile, int((x + radius) // tile)) + 1):
            for ty in range(max(0, int((y - radius) // tile)), min((height - 1) // tile, int((y + radius) // tile)) + 1):
                regions[tx, ty].append(anchor)
    if len(regions) > 180:
        raise ValueError("Too many symbol-search regions. Select a smaller detection region.")
    raw = []
    scanned_tiles, evaluations = 0, 0
    cache: dict[tuple, list[np.ndarray]] = {}
    interior_cache: dict[tuple, np.ndarray] = {}
    for (tx, ty), local in sorted(regions.items()):
        if spec.roi is not None:
            rx, ry, rw, rh = spec.roi
            if (tx + 1) * tile < rx * width or tx * tile > (rx + rw) * width or (ty + 1) * tile < ry * height or ty * tile > (ry + rh) * height:
                continue
        if all(a["score"] >= 0.65 for a in local):
            continue
        scanned_tiles += 1
        long_side, short_side = np.median([a["symbol_shape"][:2] for a in local], axis=0)
        lengths = sorted({round(float(long_side * scale)) for scale in [0.92, 1.0, 1.08]})
        widths = sorted({max(round(spec.min_size), round(float(short_side * scale))) for scale in [0.88, 1.0, 1.12]})
        angles = sorted({(round(a["symbol_shape"][2] / 5) * 5 + delta) % 180 for a in local for delta in [-5, 0, 5]})
        margin = math.ceil(long_side + 8)
        x0, y0 = max(0, tx * tile - margin), max(0, ty * tile - margin)
        x1, y1 = min(width, (tx + 1) * tile + margin), min(height, (ty + 1) * tile + margin)
        image = gray[y0:y1, x0:x1].astype(np.float32)
        ink_image = binary[y0:y1, x0:x1].astype(np.float32) / 255
        best = np.zeros(image.shape, np.float32)
        geometry = np.zeros((*image.shape, 3), np.float32)
        for w in lengths:
            for h in widths:
                for angle in angles:
                    evaluations += 1
                    if evaluations > 10000:
                        raise ValueError("Symbol search exceeded its work limit. Select a smaller detection region.")
                    key = (w, h, angle)
                    if key not in cache:
                        cache[key] = ring_kernels(w, h, angle)
                        kernel = np.zeros_like(cache[key][0])
                        half = (kernel.shape[0] - 1) // 2
                        inner = cv2.boxPoints(((float(half), float(half)), (float(w) * 0.62, float(h) * 0.62), float(angle)))
                        cv2.fillConvexPoly(kernel, np.round(inner).astype(np.int32), 1)
                        interior_cache[key] = kernel / kernel.sum()
                    responses = np.stack([np.clip(cv2.filter2D(image, -1, kernel) / 60, 0, 1) for kernel in cache[key]])
                    ordered = np.sort(responses, axis=0)
                    score = 0.55 * responses.mean(0) + 0.45 * ordered[1]
                    score[(ordered[0] < 0.035) | (ordered[1] < 0.17)] = 0
                    # Reject ink-filled proposals BEFORE maximum selection:
                    # otherwise a slightly stronger invalid peak hides a valid
                    # empty-chair match just one pixel away.
                    score[cv2.filter2D(ink_image, -1, interior_cache[key]) > 0.045] = 0
                    improved = score > best
                    best[improved] = score[improved]
                    geometry[improved] = [w, h, angle]
        peaks = (best >= cv2.dilate(best, np.ones((7, 7), np.uint8))) & (best > 0.34)
        for cy, cx in zip(*np.nonzero(peaks)):
            x, y = cx + x0, cy + y0
            if not (tx * tile <= x < (tx + 1) * tile and ty * tile <= y < (ty + 1) * tile):
                continue
            if spec.roi is not None:
                rx, ry, rw, rh = spec.roi
                if not (rx <= x / width <= rx + rw and ry <= y / height <= ry + rh):
                    continue
            w, h, angle = geometry[cy, cx]
            polygon = cv2.boxPoints(((float(x), float(y)), (float(w), float(h)), float(angle)))
            if np.any(polygon.min(0) < 0) or np.any(polygon.max(0) > [width, height]):
                continue
            density = interior_density(binary, polygon)
            if density > 0.04:
                continue
            if not any(np.linalg.norm([x - a["center"][0], y - a["center"][1]]) < a["size"] * 3.7 and angle_distance(float(angle), a["symbol_shape"][2]) <= 20 for a in local):
                continue
            raw.append(describe(polygon, score=float(best[cy, cx]), density=density, symbol_shape=(float(w), float(h), float(angle)), recovery="paired-symbol", warning="Recovered by a local repeated-chair template; inspect the faint borders before approving."))
    # Prefer existing correctly sized shapes, but do not allow a merged enclosure
    # to suppress every child before the repeated-symbol graph is constructed.
    candidates, candidate_index = [], SpatialIndex(60)
    for item in sorted(raw, key=lambda c: -c["score"]):
        if any(overlap(item, other) > 0.30 for other in candidate_index.near(item["center"])):
            continue
        candidates.append(item)
        candidate_index.add(item)
    new = []
    for group in components(candidates, paired_symbols, 70):
        members = [candidates[i] for i in group]
        if len(members) < 3 or max(c["score"] for c in members) < 0.5:
            continue
        if not any(np.linalg.norm(c["center"] - anchor["center"]) < anchor["size"] * 3.7 and angle_distance(c["symbol_shape"][2], anchor["symbol_shape"][2]) < 20 and 0.65 < c["area"] / anchor["area"] < 1.4 for c in members for anchor in index.near(c["center"], 85)):
            continue
        for member in members:
            member["symbol_group_size"] = len(members)
        new.extend(members)
    replaced, gaps, refined, keep = [], [], [], []
    for item in items:
        children = [c for c in new if item["area"] > c["area"] * 1.45 and overlap(item, c) > 0.65]
        compact_group_child = any(c["symbol_group_size"] >= 4 and item["area"] < c["area"] * 2.5 for c in children)
        if (len(children) >= 2 or compact_group_child) and item["density"] < 0.20 and item["size"] < spec.symbol_max_size * 2.2:
            replaced.append(item)
            continue
        # The narrow whitespace BETWEEN two chair columns also has an outline.
        # It is a gap, not a rotated chair: bracketed by actual same-row chairs,
        # larger than them, and with its long axis nearly perpendicular to theirs.
        gap = False
        iw, ih, ia = edge_shape(item)
        if item["density"] < 0.035 and iw < spec.symbol_max_size * 1.5:
            neighbors = [c for c in new if c["symbol_group_size"] >= 4 and 1.3 < item["area"] / c["area"] < 4 and angle_distance(ia, c["symbol_shape"][2]) > 65 and np.linalg.norm(item["center"] - c["center"]) < c["size"] * 1.6]
            for first in neighbors:
                w, h, a = first["symbol_shape"]
                theta = math.radians(a)
                axis, normal = np.array([math.cos(theta), math.sin(theta)]), np.array([-math.sin(theta), math.cos(theta)])
                offsets = [(float((c["center"] - item["center"]) @ axis), abs(float((c["center"] - item["center"]) @ normal))) for c in neighbors if angle_distance(c["symbol_shape"][2], a) < 12]
                left = any(-w * 1.3 < along < -w * 0.5 and across < h * 0.6 for along, across in offsets)
                right = any(w * 0.5 < along < w * 1.3 and across < h * 0.6 for along, across in offsets)
                if left and right:
                    gap = True
                    break
        if gap:
            gaps.append(item)
            continue
        # A very strong blank-interior match can trim an ink-filled enclosure
        # around one chair. Stable correctly sized/empty existing seats stay as-is.
        precise = [c for c in new if c["symbol_group_size"] >= 4 and c["score"] > 0.85 and c["density"] < 0.01 and 1.12 < item["area"] / c["area"] < 1.45 and overlap(item, c) > 0.8]
        if precise and item["score"] < 0.7 and 0.05 < item["density"] < 0.20 and iw <= spec.symbol_max_size:
            refined.append(item)
            continue
        keep.append(item)
    result_index = SpatialIndex(spec.max_size * 1.2)
    for item in keep:
        result_index.add(item)
    added = []
    for item in sorted(new, key=lambda c: -c["score"]):
        if any(overlap(item, other) > 0.25 for other in result_index.near(item["center"])):
            continue
        added.append(item)
        result_index.add(item)
    return keep + added, {"symbol_scan_tiles": scanned_tiles, "symbol_template_evaluations": evaluations, "symbol_template_candidates": len(candidates), "recovered_symbol_cells": len(added), "replaced_symbol_enclosures": len(replaced), "removed_symbol_gaps": len(gaps), "refined_symbol_enclosures": len(refined)}


def recover_residuals(gray: np.ndarray, items: list[dict], fits: list[dict], spec: DetectSpec, binary: np.ndarray) -> tuple[list[dict], dict]:
    items, bank_report = recover_banks(items, fits, spec)
    symbol_report = {}
    if spec.repair_symbols:
        items, symbol_report = scan_symbols(gray, items, spec, binary)
    return items, {**bank_report, **symbol_report}
