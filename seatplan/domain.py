from __future__ import annotations

import math
import re
import uuid
from typing import Literal

from .geometry import quad_geometry, seat_vertices

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def new_id() -> str:
    return str(uuid.uuid4())


def email_address(value: str) -> str:
    value = value.strip().lower()
    # Intentionally accepts common ASCII mailbox addresses, not quoted SMTP literals.
    if len(value) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", value):
        raise ValueError("Enter a valid email address.")
    local, domain = value.rsplit("@", 1)
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in value:
        raise ValueError("Enter a valid email address.")
    if any(len(part) > 63 or part.startswith("-") or part.endswith("-") for part in domain.split(".")):
        raise ValueError("Enter a valid email address.")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class EmailRequest(StrictModel):
    email: str

    _email = field_validator("email")(email_address)


class ConfirmRequest(StrictModel):
    token: str = Field(min_length=30, max_length=150)


class Seat(StrictModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    page: int = Field(ge=0, le=99)
    section: str = Field(min_length=1, max_length=80)
    row: str = Field(default="", max_length=40)
    label: str = Field(min_length=1, max_length=40)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=0.5)
    h: float = Field(gt=0, le=0.5)
    angle: float = Field(default=0, ge=-180, le=180)
    outline: list[tuple[float, float]] | None = Field(default=None, min_length=4, max_length=4)
    blocked: bool = False
    reviewed: bool = False
    source: str = Field(default="manual", max_length=60)
    note: str = Field(default="", max_length=300)
    score: float = Field(default=0, ge=0, le=1)

    @field_validator("outline")
    @classmethod
    def valid_outline(cls, value: list[tuple[float, float]] | None) -> list[tuple[float, float]] | None:
        if value is None:
            return None
        if any(not math.isfinite(v) or abs(v) > 0.50001 for point in value for v in point):
            raise ValueError("Outline corners must be finite local coordinates between -0.5 and 0.5.")
        crosses = []
        for i in range(4):
            a, b, c = value[i], value[(i + 1) % 4], value[(i + 2) % 4]
            crosses.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
        if min(crosses) <= 0.00001:
            raise ValueError("Outline corners must be a convex, positive-winding quadrilateral.")
        return value

    @model_validator(mode="after")
    def center_bounds(self) -> "Seat":
        if self.x - self.w / 2 < -0.001 or self.x + self.w / 2 > 1.001 or self.y - self.h / 2 < -0.001 or self.y + self.h / 2 > 1.001:
            raise ValueError("Seat rectangle extends beyond the page.")
        return self


class Zone(StrictModel):
    id: str = Field(default_factory=new_id, max_length=80)
    page: int = Field(ge=0, le=99)
    name: str = Field(default="Excluded area", max_length=120)
    points: list[tuple[float, float]] = Field(min_length=3, max_length=32)

    @field_validator("points")
    @classmethod
    def valid_points(cls, value: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if any(not math.isfinite(v) or v < 0 or v > 1 for point in value for v in point):
            raise ValueError("Exclusion coordinates must be finite and within the page.")
        area = abs(sum(value[i][0] * value[(i + 1) % len(value)][1] - value[(i + 1) % len(value)][0] * value[i][1] for i in range(len(value)))) / 2
        if area < 0.0000001:
            raise ValueError("Exclusion area is empty.")
        return value


def point_in_polygon(x: float, y: float, points: list | tuple) -> bool:
    inside = False
    j = len(points) - 1
    for i, (xi, yi) in enumerate(points):
        xj, yj = points[j]
        # Include the boundary, so a seat exactly on a mask edge remains excluded.
        cross = (x - xi) * (yj - yi) - (y - yi) * (xj - xi)
        if abs(cross) < 1e-10 and min(xi, xj) - 1e-10 <= x <= max(xi, xj) + 1e-10 and min(yi, yj) - 1e-10 <= y <= max(yi, yj) + 1e-10:
            return True
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def excluded(seat: dict, zones: list[dict]) -> bool:
    return any(zone["page"] == seat["page"] and point_in_polygon(seat["x"], seat["y"], zone["points"]) for zone in zones)


class PlanSave(StrictModel):
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    notes: str = Field(default="", max_length=2000)
    seats: list[Seat] = Field(max_length=5000)
    zones: list[Zone] = Field(default_factory=list, max_length=250)


class GridSpec(StrictModel):
    page: int = Field(ge=0, le=99)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    rows: int = Field(ge=1, le=100)
    columns: int = Field(ge=1, le=150)
    gap_x: float = Field(default=0.15, ge=0, lt=0.95)
    gap_y: float = Field(default=0.15, ge=0, lt=0.95)
    angle: float = Field(default=0, ge=-180, le=180)
    section: str = Field(min_length=1, max_length=80)
    row_prefix: str = Field(default="R", max_length=20)
    row_start: int = Field(default=1, ge=0, le=10000)
    start: int = Field(default=1, ge=-100000, le=100000)
    step: int = Field(default=1, ge=-10000, le=10000)
    numbering: Literal["continuous", "per-row"] = "continuous"
    serpentine: bool = False
    corners: list[tuple[float, float]] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode="after")
    def limits(self) -> "GridSpec":
        if self.corners is not None:
            if any(not math.isfinite(v) or v < 0 or v > 1 for point in self.corners for v in point):
                raise ValueError("Grid corners must be within the page.")
            signs = []
            for i in range(4):
                a, b, c = self.corners[i], self.corners[(i + 1) % 4], self.corners[(i + 2) % 4]
                signs.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
            if min(signs) <= 0.00000001:
                raise ValueError("Click four grid corners clockwise around the page, starting at the first row/column corner.")
        if self.rows * self.columns > 5000 or self.step == 0:
            raise ValueError("Grid is too large, or numbering step is zero.")
        return self


def validate_rotated_bounds(seat: dict, width: int, height: int) -> None:
    """Check the full rotated rectangle using the page's actual aspect ratio."""
    points = seat_vertices(seat, width, height)
    if any(x < -1 or y < -1 or x > width + 1 or y > height + 1 for x, y in points):
        raise ValueError("Seat outline extends beyond the page.")


def build_grid(spec: GridSpec, width: int, height: int) -> list[dict]:
    """Rotation is in page-pixel space, not stretched normalized space."""
    angle = math.radians(spec.angle)
    cx = (spec.x + spec.w / 2) * width
    cy = (spec.y + spec.h / 2) * height
    result = []
    for row in range(spec.rows):
        for col in range(spec.columns):
            px = (spec.x + (col + 0.5) * spec.w / spec.columns) * width - cx
            py = (spec.y + (row + 0.5) * spec.h / spec.rows) * height - cy
            actual_col = spec.columns - col - 1 if spec.serpentine and row % 2 == 1 else col
            number_index = actual_col + (row * spec.columns if spec.numbering == "continuous" else 0)
            seat = Seat(
                page=spec.page, section=spec.section, row=f"{spec.row_prefix}{spec.row_start + row}",
                label=str(spec.start + spec.step * number_index),
                x=(cx + math.cos(angle) * px - math.sin(angle) * py) / width,
                y=(cy + math.sin(angle) * px + math.cos(angle) * py) / height,
                w=spec.w / spec.columns * (1 - spec.gap_x), h=spec.h / spec.rows * (1 - spec.gap_y),
                angle=spec.angle, source="grid", reviewed=False,
            )
            if spec.corners is not None:
                a, b, c, d = spec.corners
                def interpolate(u: float, v: float) -> list[float]:
                    return [((1 - u) * (1 - v) * a[0] + u * (1 - v) * b[0] + u * v * c[0] + (1 - u) * v * d[0]) * width,
                            ((1 - u) * (1 - v) * a[1] + u * (1 - v) * b[1] + u * v * c[1] + (1 - u) * v * d[1]) * height]
                left, right = (col + spec.gap_x / 2) / spec.columns, (col + 1 - spec.gap_x / 2) / spec.columns
                top, bottom = (row + spec.gap_y / 2) / spec.rows, (row + 1 - spec.gap_y / 2) / spec.rows
                polygon = [interpolate(left, top), interpolate(right, top), interpolate(right, bottom), interpolate(left, bottom)]
                value = seat.model_dump()
                value.update(quad_geometry(polygon, width, height))
                value.update(source="four-corner-grid", reviewed=False, note="Administrator-constrained grid; review alignment and numbering.")
                seat = Seat.model_validate(value)
            validate_rotated_bounds(seat.model_dump(), width, height)
            result.append(seat.model_dump())
    return result


class DetectSpec(StrictModel):
    recover_groups: bool = True
    strategy: Literal["boundaries", "multiscale", "legacy"] = "boundaries"
    repair_symbols: bool = True
    symbol_max_size: float = Field(default=32, ge=8, le=100)
    gap_size: int = Field(default=7, ge=3, le=9)
    page: int = Field(default=0, ge=0, le=99)
    min_size: float = Field(default=9, ge=4, le=500)
    max_size: float = Field(default=70, ge=8, le=800)
    rectangularity: float = Field(default=0.63, ge=0.4, le=0.99)
    close_size: int = Field(default=2, ge=1, le=5)
    neighbours: int = Field(default=2, ge=0, le=8)
    detect_crosses: bool = True
    section: str = Field(default="Detected", min_length=1, max_length=80)
    roi: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def limits(self) -> "DetectSpec":
        if self.min_size >= self.max_size:
            raise ValueError("Minimum size must be smaller than maximum size.")
        if self.roi is not None:
            x, y, w, h = self.roi
            if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.001 or y + h > 1.001:
                raise ValueError("Invalid detection region.")
        return self


class EventEdit(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    starts_at: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=2000)
    plan_id: str
    status: Literal["open", "closed"] = "closed"
    max_per_user: int = Field(default=4, ge=0, le=5000)
    revision: int | None = Field(default=None, ge=1)


class BookRequest(StrictModel):
    seats: list[str] = Field(min_length=1, max_length=100)
    request_key: str = Field(min_length=16, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class CancelRequest(StrictModel):
    seats: list[str] = Field(default_factory=list, max_length=100)


class OverrideRequest(StrictModel):
    revision: int = Field(ge=1)
    seats: list[str] = Field(min_length=1, max_length=1000)
    action: Literal["free", "blocked", "reserved"]
    email: str = ""
    reason: str = Field(min_length=3, max_length=500)
    allow_excluded: bool = False

    @field_validator("email")
    @classmethod
    def optional_email(cls, value: str) -> str:
        return email_address(value) if value else ""
