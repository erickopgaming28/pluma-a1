import { worldPoint } from './element-geometry.mjs';

const MODES = new Set(['free', 'horizontal', 'vertical']);
const MAX_COORD = 10000;
const isNumber = value => typeof value === 'number' && Number.isFinite(value);
const isPoint = value => Array.isArray(value) && value.length === 2 && value.every(v => isNumber(v) && Math.abs(v) <= MAX_COORD);
const copyPoint = value => {
  if (!isPoint(value)) throw new RangeError('La medida necesita dos coordenadas finitas en milímetros.');
  return [...value];
};

/** Raw picked points remain in mm from the paper's upper left corner (positive y down).
 * dx/dy and angle describe those points; length follows the requested mode.
 * lineEnd is the projected endpoint to draw for a horizontal/vertical dimension.
 */
export function measurePoints(start, end, mode = 'free') {
  if (!MODES.has(mode)) throw new RangeError('Elige una medida libre, horizontal o vertical.');
  const a = copyPoint(start), b = copyPoint(end);
  const dx = b[0] - a[0], dy = b[1] - a[1];
  return { start: a, end: b, mode, dx, dy,
    length: mode === 'horizontal' ? Math.abs(dx) : mode === 'vertical' ? Math.abs(dy) : Math.hypot(dx, dy),
    angle: dx === 0 && dy === 0 ? 0 : Math.atan2(dy, dx) * 180 / Math.PI,
    lineEnd: mode === 'horizontal' ? [b[0], a[1]] : mode === 'vertical' ? [a[0], b[1]] : [...b] };
}

/** Whitelisted project overlay data; deliberately contains no printable paths/layers.
 * Missing overlays are accepted for older projects. Other invalid input is rejected.
 */
export function validateMeasurements(value) {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 100)
    throw new RangeError('El diseño puede contener hasta 100 medidas.');
  return value.map(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item))
      throw new RangeError('El diseño contiene una medida inválida.');
    const result = measurePoints(item.start, item.end, item.mode === undefined ? 'free' : item.mode);
    return { start: result.start, end: result.end, mode: result.mode };
  });
}

/** Exact closest point on a finite segment, including zero-length segments. */
export function projectToSegment(point, start, end) {
  const p = copyPoint(point), a = copyPoint(start), b = copyPoint(end);
  const dx = b[0] - a[0], dy = b[1] - a[1], denominator = dx * dx + dy * dy;
  const t = denominator === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / denominator));
  const projected = [a[0] + t * dx, a[1] + t * dy];
  return { point: projected, t, distance: Math.hypot(projected[0] - p[0], projected[1] - p[1]) };
}

/** Snap in paper mm using a CSS-pixel radius: k is CSS pixels per mm, not device pixels.
 * scene: {paper:[w,h], boxes:[{id,x,y,w,h,rotation,selected}], strokes:[{paths,x,y,s,angle,cx,cy,id}]}.
 * Stroke transforms match app.js strokes(): the physical center cx/cy is applied AFTER s.
 * Paper/box corners, midpoints and continuous edges are always fully searched.
 * The nearest finite anchor inside the radius wins; continuous edges are a fallback.
 * Path scanning is bounded by maxSegments (default 30000); limited reports skipped work.
 * No pan offset is required: convert the pointer into world mm before calling this function.
 */
export function snapMeasurementPoint(point, scene = {}, { k = 1, radiusPx = 10, maxSegments = 30000 } = {}) {
  const raw = copyPoint(point);
  if (!isNumber(k) || k <= 0 || !isNumber(radiusPx) || radiusPx < 0 || radiusPx > 1000 || !Number.isFinite(radiusPx / k))
    throw new RangeError('La escala y el radio de ajuste deben ser válidos.');
  if (!Number.isInteger(maxSegments) || maxSegments < 0 || maxSegments > 1000000)
    throw new RangeError('El límite de segmentos debe estar entre 0 y 1,000,000.');
  if (!scene || typeof scene !== 'object' || Array.isArray(scene))
    throw new RangeError('La geometría de la hoja no es válida.');
  const radius = radiusPx / k;
  let best = null, bestDistance = radius, bestRank = Infinity, scannedSegments = 0, limited = false;
  const offer = (candidate, distance, kind, label, sourceId = null, rank = 3) => {
    if (distance > radius + 1e-10 || !isNumber(distance) || !candidate.every(isNumber)) return;
    const anchor = rank < 2.5, bestAnchor = bestRank < 2.5;
    if (best && ((!anchor && bestAnchor) || (anchor === bestAnchor &&
        (distance > bestDistance + 1e-10 || (Math.abs(distance - bestDistance) <= 1e-10 && rank >= bestRank))))) return;
    best = { point: candidate, snapped: true, kind, label, distance, sourceId };
    bestDistance = distance; bestRank = rank;
  };
  const offerPoint = (p, kind, label, id, rank) => offer(p, Math.hypot(p[0] - raw[0], p[1] - raw[1]), kind, label, id, rank);
  // Internal projection avoids allocating/copying validated points in the pointer hot path.
  const segment = (query, a, b) => {
    const dx = b[0] - a[0], dy = b[1] - a[1], denominator = dx * dx + dy * dy;
    const t = denominator === 0 ? 0 : Math.max(0, Math.min(1, ((query[0] - a[0]) * dx + (query[1] - a[1]) * dy) / denominator));
    const p = [a[0] + t * dx, a[1] + t * dy];
    return { point: p, t, distance: Math.hypot(p[0] - query[0], p[1] - query[1]) };
  };
  const offerRectangle = (points, prefix, id, selected = false) => {
    const labels = prefix === 'paper' ? ['Esquina de la hoja', 'Centro del borde de la hoja', 'Borde de la hoja']
      : ['Esquina del elemento', 'Centro del lado del elemento', 'Lado del elemento'];
    const rankOffset = selected ? -0.1 : 0;
    points.forEach(p => offerPoint(p, `${prefix}-corner`, labels[0], id, rankOffset));
    points.forEach((a, i) => {
      const b = points[(i + 1) % 4];
      offerPoint([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], `${prefix}-midpoint`, labels[1], id, 1 + rankOffset);
      const candidate = segment(raw, a, b);
      offer(candidate.point, candidate.distance, `${prefix}-edge`, labels[2], id, 3 + rankOffset);
    });
  };
  const paper = scene.paper;
  if (Array.isArray(paper) && paper.length === 2 && paper.every(v => isNumber(v) && v > 0 && v <= MAX_COORD))
    offerRectangle([[0, 0], [paper[0], 0], [paper[0], paper[1]], [0, paper[1]]], 'paper', null);
  for (const box of Array.isArray(scene.boxes) ? scene.boxes : []) {
    if (!box || ![box.x, box.y, box.w, box.h, box.rotation ?? 0].every(isNumber) || box.w <= 0 || box.h <= 0) continue;
    const angle = box.rotation ?? 0;
    const points = [[0, 0], [box.w, 0], [box.w, box.h], [0, box.h]].map(([x, y]) => worldPoint(box, angle, x, y));
    if (!points.every(isPoint)) continue;
    offerRectangle(points, 'box', box.id ?? null, Boolean(box.selected));
  }
  const groups = Array.isArray(scene.strokes) ? scene.strokes : [];
  outer: for (const group of groups) {
    if (!group || !Array.isArray(group.paths)) continue;
    const { x = 0, y = 0, s = 1, angle = 0, cx = 0, cy = 0 } = group;
    if (![x, y, s, angle, cx, cy].every(isNumber) || s <= 0) continue;
    const radians = angle * Math.PI / 180, c = Math.cos(radians), sn = Math.sin(radians);
    const u = raw[0] - x - cx, v = raw[1] - y - cy;
    const query = [(c * u + sn * v + cx) / s, (-sn * u + c * v + cy) / s];
    const transform = p => [x + cx + c * (p[0] * s - cx) - sn * (p[1] * s - cy),
      y + cy + sn * (p[0] * s - cx) + c * (p[1] * s - cy)];
    for (const path of group.paths) {
      if (!Array.isArray(path) || path.length < 4 || path.length % 2) continue;
      for (let i = 2; i < path.length; i += 2) {
        if (scannedSegments >= maxSegments) { limited = true; break outer; }
        ++scannedSegments;
        const a = [path[i - 2], path[i - 1]], b = [path[i], path[i + 1]];
        if (!isPoint(a) || !isPoint(b)) continue;
        for (const endpoint of [i === 2 ? a : null, i === path.length - 2 ? b : null]) {
          if (!endpoint) continue;
          const distance = Math.hypot(endpoint[0] - query[0], endpoint[1] - query[1]) * s;
          if (distance <= radius + 1e-10)
            offer(transform(endpoint), distance, 'path-endpoint', 'Extremo del trazo', group.id ?? null, 2);
        }
        const candidate = segment(query, a, b), distance = candidate.distance * s;
        if (distance > bestDistance + 1e-10) continue;
        const endpoint = (i === 2 && candidate.t === 0) || (i === path.length - 2 && candidate.t === 1);
        offer(transform(candidate.point), distance, endpoint ? 'path-endpoint' : 'path-segment',
          endpoint ? 'Extremo del trazo' : 'Trazo', group.id ?? null, endpoint ? 2 : 4);
      }
    }
  }
  return { ...(best || { point: raw, snapped: false, kind: null, label: '', distance: null, sourceId: null }), limited, scannedSegments };
}
