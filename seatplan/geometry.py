"""Convex cell geometry shared by detection and the grid builder.

Outlines use local coordinates in the existing centre/size/rotation frame. This
keeps moves, resizing, old rectangular plans and JSON storage backward compatible.
"""
from __future__ import annotations

import math

import cv2
import numpy as np


def quad_geometry(points: list | np.ndarray, width: int, height: int) -> dict:
    polygon = np.asarray(points, dtype=np.float32).reshape(4, 2)
    if cv2.isContourConvex(polygon) is False or abs(cv2.contourArea(polygon)) < 1:
        raise ValueError("Seat corners must form a non-degenerate convex quadrilateral.")
    if cv2.contourArea(polygon, oriented=True) < 0:
        polygon = polygon[::-1]
    (cx, cy), (w, h), angle = cv2.minAreaRect(polygon)
    if angle > 45:
        w, h, angle = h, w, angle - 90
    if angle < -45:
        w, h, angle = h, w, angle + 90
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    local = []
    for x, y in polygon:
        dx, dy = float(x) - cx, float(y) - cy
        local.append([max(-0.5, min(0.5, (cosine * dx + sine * dy) / w)), max(-0.5, min(0.5, (-sine * dx + cosine * dy) / h))])
    return {"x": cx / width, "y": cy / height, "w": w / width, "h": h / height, "angle": angle, "outline": local}


def seat_vertices(seat: dict, width: int, height: int) -> list[tuple[float, float]]:
    angle = math.radians(seat.get("angle", 0))
    cosine, sine = math.cos(angle), math.sin(angle)
    outline = seat.get("outline") or [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]
    result = []
    for x, y in outline:
        dx, dy = x * seat["w"] * width, y * seat["h"] * height
        result.append((seat["x"] * width + cosine * dx - sine * dy, seat["y"] * height + sine * dx + cosine * dy))
    return result
