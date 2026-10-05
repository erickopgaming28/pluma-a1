import assert from 'node:assert/strict';
import { measurePoints, validateMeasurements, projectToSegment, snapMeasurementPoint } from '../static/measure-tools.mjs';
import { worldPoint } from '../static/element-geometry.mjs';

const close = (a, b, epsilon = 1e-8) => assert.ok(Math.abs(a - b) <= epsilon, `${a} ≈ ${b}`);
const closePoint = (a, b) => a.forEach((value, i) => close(value, b[i]));

const free = measurePoints([2, 3], [5, 7]);
assert.equal(free.length, 5); assert.equal(free.dx, 3); assert.equal(free.dy, 4);
close(free.angle, Math.atan2(4, 3) * 180 / Math.PI);
const horizontal = measurePoints([5, 7], [2, 3], 'horizontal');
assert.equal(horizontal.length, 3); assert.equal(horizontal.dy, -4);
assert.deepEqual(horizontal.end, [2, 3]); assert.deepEqual(horizontal.lineEnd, [2, 7]);
const vertical = measurePoints([2, 3], [5, 7], 'vertical');
assert.equal(vertical.length, 4); assert.deepEqual(vertical.lineEnd, [2, 7]);
assert.equal(measurePoints([1, 1], [1, 1]).angle, 0);

closePoint(projectToSegment([5, 2], [0, 0], [10, 0]).point, [5, 0]);
closePoint(projectToSegment([15, 2], [0, 0], [10, 0]).point, [10, 0]);
closePoint(projectToSegment([-2, 2], [0, 0], [10, 0]).point, [0, 0]);
assert.deepEqual(projectToSegment([3, 4], [0, 0], [0, 0]), { point: [0, 0], t: 0, distance: 5 });

const paper = { paper: [210, 297] };
let snapped = snapMeasurementPoint([0.5, 0.5], paper, { k: 10 });
assert.equal(snapped.kind, 'paper-corner'); closePoint(snapped.point, [0, 0]);
snapped = snapMeasurementPoint([105, 0.5], paper, { k: 10 });
assert.equal(snapped.kind, 'paper-midpoint'); closePoint(snapped.point, [105, 0]);
snapped = snapMeasurementPoint([45, 0.5], paper, { k: 10 });
assert.equal(snapped.kind, 'paper-edge'); closePoint(snapped.point, [45, 0]);
// A constant 10 CSS-pixel catchment is 5 mm at k=2 but 1 mm at k=10.
assert.equal(snapMeasurementPoint([45, 3], paper, { k: 2 }).snapped, true);
assert.equal(snapMeasurementPoint([45, 3], paper, { k: 10 }).snapped, false);
assert.deepEqual(snapMeasurementPoint([45, 3], paper, { k: 10 }).point, [45, 3]);

for (const rotation of [0, 90, -37, 179]) {
  const box = { id: 7, x: 10, y: 20, w: 40, h: 20, rotation };
  const corner = worldPoint(box, rotation, 0, 0);
  snapped = snapMeasurementPoint(corner, { boxes: [box] }, { k: 8 });
  assert.equal(snapped.kind, 'box-corner'); assert.equal(snapped.sourceId, 7); closePoint(snapped.point, corner);
  const midpoint = worldPoint(box, rotation, box.w / 2, 0);
  snapped = snapMeasurementPoint(midpoint, { boxes: [box] }, { k: 8 });
  assert.equal(snapped.kind, 'box-midpoint'); closePoint(snapped.point, midpoint);
}

// This has the same scaled/rotated physical-center transform used by app.js.
const stroke = { id: 9, x: 10, y: 20, s: 2, angle: 90, cx: 10, cy: 5, paths: [[0, 0, 10, 0]] };
snapped = snapMeasurementPoint([26, 25], { strokes: [stroke] }, { k: 5 });
assert.equal(snapped.kind, 'path-segment'); closePoint(snapped.point, [25, 25]); assert.equal(snapped.sourceId, 9);
snapped = snapMeasurementPoint([25, 35], { strokes: [stroke] }, { k: 5 });
assert.equal(snapped.kind, 'path-endpoint'); closePoint(snapped.point, [25, 35]);
snapped = snapMeasurementPoint([6, 4], { strokes: [{ paths: [[0, 0, 10, 10]] }] }, { k: 2 });
closePoint(snapped.point, [5, 5]);
snapped = snapMeasurementPoint([3, 4], { strokes: [{ paths: [[3, 4, 3, 4]] }] });
assert.equal(snapped.kind, 'path-endpoint'); closePoint(snapped.point, [3, 4]);
for (const angle of [0, 37, -140]) for (const s of [.25, 2, 3]) {
  const b = { x: 30, y: 40, w: 10 * s, h: 6 * s };
  const query = worldPoint(b, angle, 4 * s, .1 * s);
  const expected = worldPoint(b, angle, 4 * s, 0);
  const group = { ...b, s, angle, cx: b.w / 2, cy: b.h / 2, paths: [[0, 0, 10, 0]] };
  snapped = snapMeasurementPoint(query, { strokes: [group] }, { k: 1 / s, radiusPx: .2 });
  assert.equal(snapped.kind, 'path-segment'); closePoint(snapped.point, expected); close(snapped.distance, .1 * s);
}

// Work is visibly bounded; complete paper/box snapping survives an exhausted path budget.
snapped = snapMeasurementPoint([50, 0], { ...paper, strokes: [{ paths: [[0, 80, 100, 80, 100, 90]] }] }, { maxSegments: 1 });
assert.equal(snapped.kind, 'paper-edge'); assert.equal(snapped.scannedSegments, 1); assert.equal(snapped.limited, true);
assert.equal(snapMeasurementPoint([10, 10], { strokes: [{ paths: [[0, 0, 20, 0]] }] }, { maxSegments: 1 }).limited, false);
assert.equal(snapMeasurementPoint([10, 10], { strokes: [{ paths: [[0, 0, 20, 0]] }] }, { maxSegments: 0 }).limited, true);

const overlays = [{ start: [1, 2], end: [4, 6], mode: 'vertical', layers: ['never printable'] }];
const clean = validateMeasurements(overlays);
assert.deepEqual(clean, [{ start: [1, 2], end: [4, 6], mode: 'vertical' }]);
clean[0].start[0] = 999; assert.equal(overlays[0].start[0], 1);
assert.deepEqual(validateMeasurements(undefined), []);
for (const bad of [null, {}, Array.from({ length: 101 }, () => overlays[0]), [{ start: [1, 2], end: [3, Infinity] }],
  [{ start: [1, 2], end: [3, 4], mode: 'unsupported' }], [{ start: [1, 2], end: [3, 4], mode: null }],
  [{ start: [1, 2], end: ['3', 4] }]])
  assert.throws(() => validateMeasurements(bad));
for (const bad of [[NaN, 0], [Infinity, 0], ['1', 2], [0], [0, 1, 2], { x: 0, y: 0 }, [10001, 0]])
  assert.throws(() => measurePoints(bad, [0, 0]));
for (const options of [{ k: 0 }, { k: NaN }, { k: Infinity }, { radiusPx: -1 }, { maxSegments: -1 }])
  assert.throws(() => snapMeasurementPoint([0, 0], paper, options));
assert.equal(snapMeasurementPoint([10, 10], { boxes: [{ x: NaN }], strokes: [{ paths: [[0, 0, Infinity, 1]] }] }).snapped, false);

console.log('Medidas en mm, ejes, giros, ajuste por zoom y validación de cotas: correctos.');
