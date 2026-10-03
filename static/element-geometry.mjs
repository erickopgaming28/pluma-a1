export function normalizeAngle(value) {
  if (!Number.isFinite(value)) throw new RangeError('El ángulo debe ser un número finito.');
  return ((value + 180) % 360 + 360) % 360 - 180;
}

export function worldPoint(b, angle, x, y) {
  const a = angle * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
  const u = x - b.w / 2, v = y - b.h / 2;
  return [b.x + b.w / 2 + c * u - s * v, b.y + b.h / 2 + s * u + c * v];
}

export function localPoint(b, angle, x, y) {
  const a = angle * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
  const u = x - b.x - b.w / 2, v = y - b.y - b.h / 2;
  return [b.w / 2 + c * u + s * v, b.h / 2 - s * u + c * v];
}

export function rotatedBounds(b, angle) {
  const points = [[0, 0], [b.w, 0], [b.w, b.h], [0, b.h]].map(([x, y]) => worldPoint(b, angle, x, y));
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const x = Math.min(...xs), y = Math.min(...ys);
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y, points };
}
