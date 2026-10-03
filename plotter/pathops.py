"""Operaciones sobre trazos (listas de arreglos Nx2 en mm)."""
import cv2
import numpy as np


def length(p):
    return float(np.hypot(*np.diff(p, axis=0).T).sum()) if len(p) > 1 else 0.0


def simplify(p, eps):
    if len(p) < 3:
        return p
    out = cv2.approxPolyDP(p.astype(np.float32).reshape(-1, 1, 2), eps, False)
    return out.reshape(-1, 2).astype(np.float64)


def subdivide(p, max_seg):
    """Inserta puntos para que ningún segmento supere max_seg."""
    if len(p) < 2:
        return p
    seg = np.hypot(*np.diff(p, axis=0).T)
    n = np.maximum(1, np.ceil(seg / max_seg).astype(int))
    out = [p[:1]]
    for i, k in enumerate(n):
        t = np.arange(1, k + 1)[:, None] / k
        out.append(p[i] + (p[i + 1] - p[i]) * t)
    return np.vstack(out)


def nn_order(paths, start=(0.0, 0.0)):
    """Ordena por vecino más cercano (invirtiendo trazos) para acortar los viajes."""
    n = len(paths)
    if n < 2:
        return list(paths)
    S = np.array([p[0] for p in paths], dtype=np.float64)
    E = np.array([p[-1] for p in paths], dtype=np.float64)
    cur = np.array(start, dtype=np.float64)
    out = []
    for _ in range(n):
        ds = ((S - cur) ** 2).sum(1)
        de = ((E - cur) ** 2).sum(1)
        i, j = int(ds.argmin()), int(de.argmin())
        if ds[i] <= de[j]:
            out.append(paths[i])
            cur = E[i].copy()
            k = i
        else:
            out.append(paths[j][::-1])
            cur = S[j].copy()
            k = j
        S[k] = E[k] = 1e12
    return out


def merge_close(paths, tol):
    """Une trazos consecutivos cuando la pluma casi no tendría que moverse."""
    out = []
    for p in paths:
        if out and np.hypot(*(out[-1][-1] - p[0])) <= tol:
            out[-1] = np.vstack([out[-1], p])
        else:
            out.append(p)
    return out


def stats(paths):
    draw = sum(length(p) for p in paths)
    travel = 0.0
    for a, b in zip(paths, paths[1:]):
        travel += float(np.hypot(*(b[0] - a[-1])))
    return {"draw_mm": draw, "travel_mm": travel, "lifts": len(paths)}


def to_flat(paths):
    return [np.round(p, 2).ravel().tolist() for p in paths]
