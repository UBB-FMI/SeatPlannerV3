"""Regression checks for the two residual regions; not full-hall ground truth."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from seatplan.detection_recovery import components, describe, edge_shape, paired_symbols, recover_banks, ring_kernels, shared_edge
from seatplan.domain import DetectSpec, excluded
from seatplan.geometry import seat_vertices

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = json.loads((ROOT / 'tests/fixtures/sample-residual-points.json').read_text())


def hits(output, point):
    return [seat for seat in output['seats'] if cv2.pointPolygonTest(np.asarray(seat_vertices(seat, REFERENCES['width'], REFERENCES['height']), np.float32), (point['x'], point['y']), False) >= 0]


@pytest.mark.parametrize('point', REFERENCES['points'], ids=[p['name'] for p in REFERENCES['points']])
def test_reported_residual_seat_has_one_free_target(current_sample, point):
    output, _ = current_sample
    found = hits(output, point)
    assert len(found) == 1, (point, [s['id'] for s in found])
    assert found[0]['blocked'] is False
    assert found[0]['reviewed'] is False


def test_residual_locations_are_distinct_and_gap_is_removed(current_sample):
    output, _ = current_sample
    matched = [hits(output, point)[0]['id'] for point in REFERENCES['points']]
    assert len(set(matched)) == len(matched), 'One merged shape cannot count as two recovered seats.'
    assert hits(output, REFERENCES['negative_points'][0]) == []


def test_recovery_reports_and_exclusions(current_sample):
    output, _ = current_sample
    assert output['report']['algorithm'] == 'deterministic-boundaries-v4'
    assert output['report']['recovered_bank_cells'] == 6
    assert output['report']['recovered_symbol_cells'] >= 10
    assert output['report']['replaced_symbol_enclosures'] >= 4
    assert any(s['source'].endswith('-shared-edge-group') for s in output['seats'])
    assert any(s['source'].endswith('-paired-symbol') for s in output['seats'])
    zones = json.loads((ROOT / 'examples/sample-exclusions.json').read_text())['zones']
    assert len(zones) == 12
    assert all(s['blocked'] for s in output['seats'] if excluded(s, zones))


def cell(x, y, width=40, height=36, angle=0):
    points = cv2.boxPoints(((float(x), float(y)), (float(width), float(height)), float(angle)))
    item = describe(points, score=.9, support=[1.0] * 4, density=.25)
    item['symbol_shape'] = edge_shape(item)
    return item


def test_unseeded_bank_can_support_itself():
    group = [cell(230, 200 + 36 * row) for row in range(4)]
    result, report = recover_banks([], group, DetectSpec())
    assert len(result) == 4
    assert report['recovered_bank_groups'] == 1
    assert all(c['recovery'] == 'shared-edge-group' for c in result)


def test_isolated_or_nonshared_shapes_do_not_form_bank():
    group = [cell(100, 100 + 70 * row) for row in range(4)]
    assert recover_banks([], group, DetectSpec())[0] == []
    assert recover_banks([], [cell(20, 20)], DetectSpec())[0] == []


def test_existing_bank_shapes_never_duplicated():
    group = [cell(230, 200 + 36 * row) for row in range(4)]
    result, report = recover_banks(group, group, DetectSpec())
    assert len(result) == 4
    assert report['recovered_bank_cells'] == 0


def test_small_symbol_pair_relation_is_symmetric():
    a, b = cell(753, 712, 21, 11, 175), cell(720, 720, 21, 11, 165)
    assert paired_symbols(a, b)
    assert paired_symbols(b, a)
    assert components([a, b], paired_symbols, 70) == [[0, 1]]


def test_ridge_filters_ignore_uniform_background():
    background = np.full((96, 96), 217, np.float32)
    for kernel in ring_kernels(21, 11, 155):
        assert abs(float(kernel.sum())) < 1e-6
        response = cv2.filter2D(background, -1, kernel)
        assert float(np.abs(response).max()) < .001


def test_new_setting_can_disable_recovery():
    assert DetectSpec().recover_groups is True
    assert DetectSpec(recover_groups=False).recover_groups is False
