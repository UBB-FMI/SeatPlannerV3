"""Location-based regressions for the omissions reported in the sample editor.

The checked points are a small, visually inspected subset, not ground truth for
all 928 printed seats. Do not interpret passing tests or a count as full recall.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from seatplan.cli import seed_sample
from seatplan.detection import detect_seats, render_pdf
from seatplan.domain import DetectSpec, excluded

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = json.loads((ROOT / 'tests/fixtures/sample-coverage-points.json').read_text())


def covering(seats, point):
    width, height = REFERENCES['width'], REFERENCES['height']
    matches = []
    for seat in seats:
        angle = math.radians(seat['angle'])
        dx, dy = point['x'] - seat['x'] * width, point['y'] - seat['y'] * height
        if abs(dx * math.cos(angle) + dy * math.sin(angle)) < seat['w'] * width / 2 and abs(-dx * math.sin(angle) + dy * math.cos(angle)) < seat['h'] * height / 2:
            matches.append(seat)
    return matches


@pytest.fixture(scope='module')
def sample_results(tmp_path_factory):
    folder = tmp_path_factory.mktemp('sample-v2')
    pdf = ROOT / 'examples/sample-hall.pdf'
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == REFERENCES['pdf_sha256']
    pages = render_pdf(pdf, folder)
    assert (pages[0]['width'], pages[0]['height']) == (REFERENCES['width'], REFERENCES['height'])
    zones = json.loads((ROOT / 'examples/sample-exclusions.json').read_text())['zones']
    image = folder / pages[0]['filename']
    return (detect_seats(image, DetectSpec(strategy="multiscale"), zones, pdf), detect_seats(image, DetectSpec(strategy='legacy'), zones, pdf), zones)


@pytest.mark.parametrize('point', REFERENCES['points'], ids=[point['name'] for point in REFERENCES['points']])
def test_reported_locations_have_one_free_click_target(sample_results, point):
    enhanced, legacy, zones = sample_results
    matches = covering(enhanced['seats'], point)
    assert len(matches) == 1, (point, matches)
    assert matches[0]['reviewed'] is False  # Selectable in the editor, not auto-published.
    assert matches[0]['blocked'] is False
    assert excluded(matches[0], zones) is False
    assert covering(legacy['seats'], point) == []  # A regression of an actual old miss.


def test_regression_points_are_distinct_seats(sample_results):
    enhanced, _, _ = sample_results
    ids = [covering(enhanced['seats'], point)[0]['id'] for point in REFERENCES['points']]
    assert len(set(ids)) == len(ids)  # A single oversized box cannot satisfy two points.


def test_detector_provenance_and_exclusion_priority(sample_results):
    enhanced, legacy, zones = sample_results
    assert legacy['report']['algorithm'] == 'deterministic-contour-v1'
    assert legacy['report']['candidates'] == 691
    assert enhanced['report']['algorithm'] == 'deterministic-contour-v2'
    assert enhanced['report']['repair_candidates'] > 0
    assert any(seat['source'] == 'deterministic-contour-v2-repair' for seat in enhanced['seats'])
    assert all(seat['reviewed'] is False for seat in enhanced['seats'])
    assert all(seat['blocked'] for seat in enhanced['seats'] if excluded(seat, zones))


def test_rotated_overlap_used_by_browser():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is needed only for the pure frontend geometry unit test.')
    source = (ROOT / 'seatplan/static/ui.js').read_text().replace('export function', 'function')
    checks = """
const page = {width: 1000, height: 1400};
const a = {x:.5,y:.5,w:.020,h:.010,angle:45};
const b = {...a, angle:-135};
const c = {...a,x:.517,y:.512142857};
const d = {...a,x:.501,y:.5001};
if (Math.abs(seatOverlap(a,b,page)-1)>1e-8) throw Error('Equivalent rotation mismatch');
if (seatOverlap(a,c,page)>.05) throw Error('Adjacent rotated seats suppressed');
if (seatOverlap(a,d,page)<.8) throw Error('Duplicate missed');
if (Math.abs(seatOverlap(a,d,page)-seatOverlap(d,a,page))>1e-8) throw Error('Asymmetric');
"""
    subprocess.run([node, '--input-type=module', '-e', source + checks], check=True, capture_output=True, text=True)


def test_seed_new_keeps_existing_draft_and_uses_unique_seat_ids(app, settings, monkeypatch, sample_results):
    sample_seat = sample_results[0]['seats'][0]
    monkeypatch.setattr('seatplan.cli.detect_seats', lambda *args: {'seats': [dict(sample_seat)], 'report': {'test': True}})
    old = seed_sample(app.state.db, settings.data_dir)
    assert seed_sample(app.state.db, settings.data_dir) == old
    fresh = seed_sample(app.state.db, settings.data_dir, fresh=True)
    assert old != fresh
    with app.state.db.read() as connection:
        plans = connection.execute('SELECT id,state FROM plans WHERE id IN (?,?)', (old, fresh)).fetchall()
        seats = connection.execute('SELECT id,plan_id FROM seats WHERE plan_id IN (?,?)', (old, fresh)).fetchall()
    assert len(plans) == 2 and all(plan['state'] == 'draft' for plan in plans)
    assert len(seats) == 2 and seats[0]['id'] != seats[1]['id']
