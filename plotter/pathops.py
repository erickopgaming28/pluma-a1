"""Operaciones sobre trazos (listas de arreglos Nx2 en mm)."""
import cv2
import heapq
from concurrent.futures import CancelledError
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


def nn_order(paths, start=(0.0, 0.0), cancelled=None):
    """Ordena por vecino más cercano (invirtiendo trazos) para acortar los viajes."""
    n = len(paths)
    if n < 2:
        return list(paths)
    if n >= 4096:
        return _indexed_order(paths, start, cancelled)
    S = np.array([p[0] for p in paths], dtype=np.float64)
    E = np.array([p[-1] for p in paths], dtype=np.float64)
    cur = np.array(start, dtype=np.float64)
    out = []
    for step in range(n):
        if step % 128 == 0 and cancelled and cancelled():
            raise CancelledError()
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


def _indexed_order(paths, start, cancelled=None):
    """Vecino más cercano exacto con índice espacial y borrado de extremos.

    Evita comparar todos los extremos restantes en cada paso. No elimina,
    aproxima ni une trazos; conserva los mismos desempates del recorrido lento.
    """
    points = np.asarray([end for p in paths for end in (p[0], p[-1])], dtype=np.float64)
    nodes, leaves = [], np.empty(len(points), dtype=np.int32)

    def build(ids, parent=-1):
        box = np.array([points[ids].min(0), points[ids].max(0)])
        index = len(nodes)
        node = {'box': box, 'parent': parent, 'count': len(ids)}
        nodes.append(node)
        if len(ids) <= 16:
            node['ids'] = ids
            leaves[ids] = index
        else:
            axis = int(np.argmax(box[1] - box[0]))
            middle = len(ids) // 2
            order = np.argpartition(points[ids, axis], middle)
            node['children'] = (build(ids[order[:middle]], index), build(ids[order[middle:]], index))
        return index

    build(np.arange(len(points)))
    used = np.zeros(len(paths), dtype=bool)
    current, out = np.asarray(start, dtype=np.float64), []

    def distance(box):
        delta = np.maximum(np.maximum(box[0] - current, current - box[1]), 0)
        return float(delta @ delta)

    for step in range(len(paths)):
        if step % 128 == 0 and cancelled and cancelled():
            raise CancelledError()
        best, endpoint = (float('inf'), 2, len(paths)), None
        queue = [(distance(nodes[0]['box']), 0)]
        while queue:
            lower, index = heapq.heappop(queue)
            if lower > best[0]:
                break
            node = nodes[index]
            if not node['count']:
                continue
            if 'children' in node:
                for child in node['children']:
                    if nodes[child]['count']:
                        lower = distance(nodes[child]['box'])
                        if lower <= best[0]:
                            heapq.heappush(queue, (lower, child))
            else:
                ids = node['ids'][~used[node['ids'] // 2]]
                deltas = points[ids] - current
                squared = np.einsum('ij,ij->i', deltas, deltas)
                for point_id, value in zip(ids, squared):
                    # Igual distancia: primero inicio, luego índice original.
                    candidate = (float(value), int(point_id % 2), int(point_id // 2))
                    if candidate < best:
                        best, endpoint = candidate, int(point_id)
        k = endpoint // 2
        used[k] = True
        p = paths[k] if endpoint % 2 == 0 else paths[k][::-1]
        out.append(p)
        current = p[-1]
        for point_id in (2 * k, 2 * k + 1):
            index = int(leaves[point_id])
            while index >= 0:
                nodes[index]['count'] -= 1
                index = nodes[index]['parent']
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
