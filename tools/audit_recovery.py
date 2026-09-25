"""Reproduce v1.2/v1.3 residual-seat checks on the supplied PDF.

Run: python tools/audit_recovery.py --output /tmp/seatplan-recovery-audit
This executes both detectors. Manually marked test points are used only to
measure the outputs, never to place seats or guide the detector.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from seatplan.detection import detect_seats, render_pdf
from seatplan.domain import DetectSpec
from seatplan.geometry import seat_vertices
from tools.audit_sample import compare, crop_overlay, font


def measure(output: dict, references: dict) -> dict:
    polygons = [np.asarray(seat_vertices(seat, references['width'], references['height']), np.float32) for seat in output['seats']]
    cases = []
    for point in references['points']:
        matching = [seat for seat, polygon in zip(output['seats'], polygons) if cv2.pointPolygonTest(polygon, (point['x'], point['y']), False) >= 0]
        cases.append({'name': point['name'], 'group': point['region'], 'ids': [s['id'] for s in matching], 'free': len(matching) == 1 and matching[0]['blocked'] is False})
    counts = Counter(case['ids'][0] for case in cases if len(case['ids']) == 1)
    for case in cases:
        case['separate_free_target'] = case['free'] and counts[case['ids'][0]] == 1
    groups = {}
    for case in cases:
        row = groups.setdefault(case['group'], {'reference_positions': 0, 'separate_free_targets': 0})
        row['reference_positions'] += 1
        row['separate_free_targets'] += int(case['separate_free_target'])
    negative = []
    for point in references['negative_points']:
        matching = [seat['id'] for seat, polygon in zip(output['seats'], polygons) if cv2.pointPolygonTest(polygon, (point['x'], point['y']), False) >= 0]
        negative.append({'name': point['name'], 'ids': matching, 'empty': len(matching) == 0})
    return {'groups': groups, 'separate_free_targets': sum(c['separate_free_target'] for c in cases), 'cases': cases, 'negative_points': negative}


def geometry_key(seat: dict) -> tuple:
    points = seat_vertices(seat, 1698, 2400)
    return tuple(round(value, 5) for point in points for value in point)


def audit(old: dict, new: dict, references: dict, geometry_references: dict) -> dict:
    old_geometry = {geometry_key(s) for s in old['seats']}
    new_geometry = {geometry_key(s) for s in new['seats']}
    previous = compare(old['seats'], geometry_references)
    current = compare(new['seats'], geometry_references)
    return {
        'reference_method': references['method'],
        'reference_positions': len(references['points']),
        'versions': {'v1.2': measure(old, references), 'v1.3': measure(new, references)},
        'candidate_reports': {'v1.2': old['report'], 'v1.3': new['report']},
        'geometry_retention': {'old_candidates': len(old_geometry), 'unchanged_outlines': len(old_geometry & new_geometry), 'removed_or_replaced_outlines': len(old_geometry - new_geometry), 'new_outlines': len(new_geometry - old_geometry)},
        'earlier_24_polygon_audit': {'v1.2': previous, 'v1.3': current, 'all_per_cell_metrics_unchanged': previous == current},
        'limitations': ['Development-region checks, not a full-hall precision/recall measurement.', 'Point coverage and unique IDs do not independently certify all four border positions.', 'Some other faint symbols, exclusions and printed identities still need review.', 'All output candidates are unreviewed. No manually inserted seats.'],
    }


def visuals(image: Image.Image, old: list[dict], new: list[dict], folder: Path) -> None:
    regions = [
        ('Outer-right bank: 255, 257, 259, 261, 263, 265', (1460, 1430, 1610, 1700)),
        ('Left Loja 9 and the outer part of left Loja 10', (510, 690, 790, 850)),
    ]
    panel_width, margin, top = 760, 30, 170
    dimensions = [(min(3, panel_width / (b[2]-b[0])), b) for _, b in regions]
    heights = [round((b[3]-b[1])*scale) for scale, b in dimensions]
    canvas = Image.new('RGB', (panel_width*3 + margin*4, top + sum(heights) + 200), 'white')
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 20), 'Seatplan v1.3: residual-seat recovery', fill='#183343', font=font(34))
    draw.text((margin, 69), 'Original pixels and identical unfilled blue outlines. No manually inserted seats. These are review drafts.', fill='#41525a', font=font(23))
    for column, label in enumerate(('Original PDF', 'v1.2', 'v1.3')):
        draw.text((margin + column*(panel_width+margin), 118), label, fill='#183343', font=font(27))
    y = top
    for row, (label, box) in enumerate(regions):
        draw.text((margin, y), label, fill='#183343', font=font(26))
        y += 43
        for column, seats in enumerate(([], old, new)):
            patch = crop_overlay(image, seats, box, scale=3)
            if patch.width > panel_width:
                patch = patch.resize((panel_width, heights[row]), Image.Resampling.LANCZOS)
            x = margin + column*(panel_width+margin)
            canvas.paste(patch, (x, y))
            draw.rectangle((x, y, x+patch.width, y+patch.height), outline='#c6d1d8', width=1)
        y += heights[row] + 25
    draw.text((margin, y+8), 'Crossed-out inner Loja 10 remains excluded. Blue indicates geometry only, not booking availability.', fill='#41525a', font=font(23))
    canvas.save(folder / 'residual-comparison.png')
    for name, box in [('outer-right-recovered', regions[0][1]), ('loja-9-10-recovered', regions[1][1])]:
        crop_overlay(image, new, box, scale=4).save(folder / (name + '.png'))
    crop_overlay(image, new, (0, 0, image.width, image.height), scale=1).save(folder / 'full-plan-outline.png')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pdf = ROOT / 'examples/sample-hall.pdf'
    references = json.loads((ROOT / 'tests/fixtures/sample-residual-points.json').read_text())
    geometry_references = json.loads((ROOT / 'tests/fixtures/sample-cell-polygons.json').read_text())
    profile = json.loads((ROOT / 'examples/sample-exclusions.json').read_text())
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if sha != references['pdf_sha256'] or sha != profile['pdf_sha256']:
        raise ValueError('Source PDF and reference/exclusion checksums differ.')
    outputs, timings = {}, {}
    with tempfile.TemporaryDirectory(prefix='seatplan-recovery-audit-') as temporary:
        pages = render_pdf(pdf, Path(temporary))
        image_path = Path(temporary) / pages[0]['filename']
        for version, recovery in [('v1.2', False), ('v1.3', True)]:
            start = time.perf_counter()
            outputs[version] = detect_seats(image_path, DetectSpec(recover_groups=recovery), profile['zones'], pdf)
            timings[version] = time.perf_counter()-start
        report = audit(outputs['v1.2'], outputs['v1.3'], references, geometry_references)
        report.update(source_pdf_sha256=sha, detection_seconds=timings, opencv_version=cv2.__version__)
        (args.output / 'recovery-audit.json').write_text(json.dumps(report, indent=2))
        with Image.open(image_path) as image:
            visuals(image, outputs['v1.2']['seats'], outputs['v1.3']['seats'], args.output)
        overlay = {'format': 'seatplan-overlay-v1', 'plan': {'name': 'Supplied hall - v1.3 UNREVIEWED recovery draft', 'sha256': sha, 'pages': pages, 'zones': profile['zones'], 'seats': outputs['v1.3']['seats']}}
        (args.output / 'sample-detection-overlay.json').write_text(json.dumps(overlay, indent=2))
    print(json.dumps({version: report['versions'][version]['groups'] for version in outputs}, indent=2))
    print('Observed seconds:', timings)


if __name__ == '__main__':
    main()
