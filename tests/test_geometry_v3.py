"""Shape accuracy and compatibility regressions; not a complete venue audit."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from seatplan.detection import detect_seats, render_pdf
from seatplan.domain import DetectSpec, GridSpec, Seat, build_grid, validate_rotated_bounds
from seatplan.geometry import quad_geometry, seat_vertices
from conftest import login, make_plan, make_event

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = json.loads((ROOT / "tests/fixtures/sample-cell-polygons.json").read_text())
POINTS = json.loads((ROOT / "tests/fixtures/sample-coverage-points.json").read_text())["points"]


@pytest.fixture(scope="module")
def precise(current_sample):
    return current_sample


@pytest.mark.parametrize("reference", REFERENCES["cases"], ids=[r["name"] for r in REFERENCES["cases"]])
def test_printed_cell_overlap(precise, reference):
    output, polygons = precise
    expected = np.asarray(reference["points"], np.float32)
    scores = []
    for polygon in polygons:
        intersection, _ = cv2.intersectConvexConvex(expected, polygon)
        scores.append(intersection / max(1, cv2.contourArea(expected) + cv2.contourArea(polygon) - intersection))
    assert max(scores) >= 0.80, (reference["name"], max(scores))
    assert sum(score > 0.15 for score in scores) == 1, "An overlapping fragment still competes with the cell."
    assert all(seat["reviewed"] is False for seat in output["seats"])


@pytest.mark.parametrize("point", POINTS, ids=[p["name"] for p in POINTS])
def test_previous_locations_remain_distinct_and_free(precise, point):
    output, polygons = precise
    matching = [i for i, polygon in enumerate(polygons) if cv2.pointPolygonTest(polygon, (point["x"], point["y"]), False) >= 0]
    assert len(matching) == 1, (point, matching)
    assert output["seats"][matching[0]]["blocked"] is False


def test_outline_roundtrip_and_real_bounds():
    polygon = np.array([[100, 100], [145, 120], [128, 160], [87, 137]], np.float32)
    value = {"page": 0, "section": "A", "label": "1", **quad_geometry(polygon, 600, 800)}
    seat = Seat.model_validate(value).model_dump()
    actual = np.asarray(seat_vertices(seat, 600, 800))
    assert max(min(np.linalg.norm(p - q) for q in actual) for p in polygon) < 0.0001
    validate_rotated_bounds(seat, 600, 800)
    with pytest.raises(ValueError):
        validate_rotated_bounds({**seat, "x": 0}, 600, 800)


@pytest.mark.parametrize("outline", [
    [[-.5, -.5], [.5, .5], [.5, -.5], [-.5, .5]],
    [[-.5, -.5], [-.5, .5], [.5, .5], [.5, -.5]],
    [[-.5, -.5], [.5, -.5], [float("nan"), .5], [-.5, .5]],
    [[-.5, -.5], [.5, -.5], [.6, .5], [-.5, .5]],
])
def test_bad_outlines_rejected(outline):
    with pytest.raises(ValidationError):
        Seat(page=0, section="A", label="1", x=.5, y=.5, w=.03, h=.04, outline=outline)


def test_four_corner_grid_shared_edges_and_numbering():
    spec = GridSpec(page=0, x=.1, y=.1, w=.4, h=.3, rows=2, columns=3, gap_x=0, gap_y=0, section="Skew", start=2, step=2, corners=[(.1, .1), (.4, .16), (.5, .4), (.18, .32)])
    seats = build_grid(spec, 800, 1200)
    assert [seat["label"] for seat in seats] == ["2", "4", "6", "8", "10", "12"]
    assert all(seat["outline"] is not None and seat["reviewed"] is False for seat in seats)
    first, second = [np.asarray(seat_vertices(seat, 800, 1200)) for seat in seats[:2]]
    common = [point for point in first if min(np.linalg.norm(point - q) for q in second) < .001]
    assert len(common) == 2
    for seat in seats:
        Seat.model_validate(seat)
        validate_rotated_bounds(seat, 800, 1200)


def test_four_corner_grid_rejects_bow_tie():
    with pytest.raises(ValidationError):
        GridSpec(page=0, x=.1, y=.1, w=.4, h=.4, rows=2, columns=2, section="A", corners=[(.1, .1), (.5, .5), (.5, .1), (.1, .5)])


def test_legacy_rectangle_still_works():
    seat = Seat(page=0, section="A", label="1", x=.5, y=.5, w=.1, h=.1).model_dump()
    assert seat["outline"] is None
    assert seat_vertices(seat, 100, 200) == [(45, 90), (55, 90), (55, 110), (45, 110)]


def test_polygon_survives_save_public_booking_and_print(client, app, settings):
    pid, seats = make_plan(app.state.db, settings, published=False, count=1)
    headers = login(client, app, "admin@example.org")
    plan = client.get(f"/api/admin/plans/{pid}").json()
    original = seats[0]
    original["outline"] = [[-.5, -.5], [.5, -.25], [.4, .5], [-.5, .25]]
    original["reviewed"] = True
    payload = {"revision": plan["revision"], "name": plan["name"], "notes": "", "seats": [original], "zones": []}
    saved = client.put(f"/api/admin/plans/{pid}", json=payload, headers=headers)
    assert saved.status_code == 200, saved.text
    stored = client.get(f"/api/admin/plans/{pid}").json()
    assert stored["seats"][0]["outline"] == original["outline"]
    with app.state.db.transaction() as connection:
        connection.execute("UPDATE plans SET state='published' WHERE id=?", (pid,))
    eid = make_event(app.state.db, pid)
    page = client.get(f"/print/{eid}")
    assert page.status_code == 200 and '<polygon class="seat free"' in page.text
    public = client.get(f"/api/events/{eid}")
    assert public.status_code == 200
    assert public.json()["seats"][0]["outline"] == original["outline"]
    booked = client.post(f"/api/events/{eid}/book", json={"seats": [original["id"]], "request_key": "polygon-integration-booking"}, headers=headers)
    assert booked.status_code == 200, booked.text
    printed = client.get(f"/print/{eid}")
    assert '<polygon class="seat reserved"' in printed.text
