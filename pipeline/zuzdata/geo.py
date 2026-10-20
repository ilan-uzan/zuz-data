"""Distances, footpaths and the stop grid."""

import math

import numpy as np

EARTH_RADIUS_M = 6_371_000.0
# Research (data-sources §8): 1.3 m/s walking pace, straight-line distance x 1.25 detour factor.
WALK_SPEED_MPS = 1.3
DETOUR_FACTOR = 1.25
FOOTPATH_MAX_M = 400.0
# Grid cells are ~0.0045° tall (500 m); the column width is scaled by cos(latitude) in the app.
GRID_CELL_E6 = 4_500


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


def walk_seconds(meters):
    return np.ceil(np.asarray(meters) * DETOUR_FACTOR / WALK_SPEED_MPS)


def footpaths(lat_e6: np.ndarray, lon_e6: np.ndarray, max_m: float = FOOTPATH_MAX_M):
    """All ordered stop pairs within `max_m`, as CSR (offsets, targets, seconds), sorted by duration."""
    n = len(lat_e6)
    lat = lat_e6 / 1e6
    lon = lon_e6 / 1e6
    # Bucket into cells about max_m wide, then compare each cell with its 3x3 neighbourhood.
    cell_lat = max_m / 111_320.0
    cell_lon = max_m / (111_320.0 * math.cos(math.radians(31.5)))
    cy = np.floor(lat / cell_lat).astype(np.int64)
    cx = np.floor(lon / cell_lon).astype(np.int64)
    buckets = {}
    for i, key in enumerate(zip(cy.tolist(), cx.tolist())):
        buckets.setdefault(key, []).append(i)
    buckets = {k: np.array(v, dtype=np.int64) for k, v in buckets.items()}
    per_stop = [None] * n
    for (y, x), members in buckets.items():
        near = [buckets[(y + dy, x + dx)] for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (y + dy, x + dx) in buckets]
        cand = np.concatenate(near)
        d = haversine_m(lat[members][:, None], lon[members][:, None], lat[cand][None, :], lon[cand][None, :])
        for row, stop in enumerate(members):
            mask = (d[row] <= max_m) & (cand != stop)
            targets = cand[mask]
            dist = d[row][mask]
            order = np.argsort(dist, kind="stable")
            per_stop[stop] = (targets[order], walk_seconds(dist[order]))
    offsets = np.zeros(n + 1, dtype="<u4")
    np.cumsum([len(p[0]) for p in per_stop], out=offsets[1:])
    targets = np.concatenate([p[0] for p in per_stop]).astype("<u4")
    seconds = np.concatenate([p[1] for p in per_stop]).astype("<u2")
    return offsets, targets, seconds


def grid(lat_e6: np.ndarray, lon_e6: np.ndarray):
    """A uniform grid over the stops' bounding box: (header record values, cell offsets, stop ids)."""
    min_lat = int(lat_e6.min()) - 1
    min_lon = int(lon_e6.min()) - 1
    rows = int((lat_e6.max() - min_lat) // GRID_CELL_E6) + 1
    cols = int((lon_e6.max() - min_lon) // GRID_CELL_E6) + 1
    r = (lat_e6 - min_lat) // GRID_CELL_E6
    c = (lon_e6 - min_lon) // GRID_CELL_E6
    cell = (r * cols + c).astype(np.int64)
    order = np.argsort(cell, kind="stable")
    counts = np.bincount(cell, minlength=rows * cols)
    offsets = np.zeros(rows * cols + 1, dtype="<u4")
    np.cumsum(counts, out=offsets[1:])
    header = (min_lat, min_lon, GRID_CELL_E6, GRID_CELL_E6, rows, cols)
    return header, offsets, order.astype("<u4")
