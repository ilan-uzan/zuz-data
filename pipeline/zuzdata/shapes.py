"""Shape simplification (Douglas-Peucker) and stop-to-shape snapping."""

import csv
import math

import numpy as np

# 4 m keeps streets recognisable at city zoom while cutting ~90% of the points.
TOLERANCE_M = 4.0


def read_shapes(f, wanted):
    """Streams an open shapes.txt and returns {shape_id: [[lat, lon], ...] float array} for `wanted` ids."""
    out = {}
    current, pts = None, []

    def flush():
        if current is not None and current in wanted and pts:
            pts.sort()
            arr = np.array([(p[1], p[2]) for p in pts], dtype=np.float64)
            out[current] = arr

    reader = csv.reader(f)
    header = next(reader)
    i_id, i_lat, i_lon, i_seq = (header.index(k) for k in ("shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"))
    for row in reader:
        sid = row[i_id]
        if sid != current:
            flush()
            if sid in out:
                raise ValueError(f"shapes.txt is not grouped by shape_id ({sid})")
            current, pts = sid, []
        if sid in wanted:
            pts.append((int(row[i_seq]), float(row[i_lat]), float(row[i_lon])))
    flush()
    return out


def _project(latlon):
    lat0 = math.radians(float(latlon[:, 0].mean()))
    y = latlon[:, 0] * 111_320.0
    x = latlon[:, 1] * 111_320.0 * math.cos(lat0)
    return np.stack([x, y], axis=1)


def simplify(latlon: np.ndarray, keep: np.ndarray, tolerance: float = TOLERANCE_M) -> np.ndarray:
    """Douglas-Peucker; returns the indices of kept points. `keep` marks points that must survive."""
    n = len(latlon)
    if n <= 2:
        return np.arange(n)
    xy = _project(latlon)
    mask = np.zeros(n, dtype=bool)
    mask[0] = mask[-1] = True
    mask[keep] = True
    anchors = np.flatnonzero(mask)
    stack = list(zip(anchors[:-1].tolist(), anchors[1:].tolist()))
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = xy[b] - xy[a]
        pts = xy[a + 1:b] - xy[a]
        length = math.hypot(seg[0], seg[1])
        if length == 0:
            d = np.hypot(pts[:, 0], pts[:, 1])
        else:
            d = np.abs(pts[:, 0] * seg[1] - pts[:, 1] * seg[0]) / length
        i = int(np.argmax(d))
        if d[i] > tolerance:
            m = a + 1 + i
            mask[m] = True
            stack.append((a, m))
            stack.append((m, b))
    return np.flatnonzero(mask)


def snap(latlon: np.ndarray, stops_latlon: np.ndarray) -> np.ndarray:
    """Index of the shape point nearest each stop, monotonically non-decreasing along the shape."""
    xy = _project(np.vstack([latlon, stops_latlon]))
    shape_xy, stop_xy = xy[: len(latlon)], xy[len(latlon):]
    out = np.zeros(len(stop_xy), dtype=np.int64)
    start = 0
    n = len(shape_xy)
    for k, p in enumerate(stop_xy):
        # Look ahead a bounded window so loops (a line returning along the same street) snap in order.
        window = shape_xy[start:]
        d = np.hypot(window[:, 0] - p[0], window[:, 1] - p[1])
        # Take the first approach that comes close to the best match, then slide to its local minimum,
        # so a loop that passes the same corner twice snaps to the pass that comes first.
        threshold = max(float(d.min()) + 25.0, 30.0)
        i = int(np.argmax(d <= threshold))
        while i + 1 < len(d) and d[i + 1] <= d[i]:
            i += 1
        out[k] = start + i
        start = min(start + i, n - 1)
    return out
