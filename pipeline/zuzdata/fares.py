"""Fares from fare_attributes / fare_rules, checked against the 2022 "Derekh Shava" distance bands.

The research (data-sources §5) found that zone-to-zone rules are a pure function of the
straight-line distance between boarding and alighting stops:

    201  8.00   0–15 km      202 14.50  15–40 km     204 19.00  40–120 km
    205 30.50   120–225 km   206 87.32  > 225 km
    221–226     the same bands for the VAT-free Eilat region
    211–215     Israel Railways, priced per station pair

So the dataset ships the band table (prices read from fare_attributes every night), an Eilat
flag per stop, and the rail station-pair table. The app applies the 90-minute transfer window.
"""

import collections
import csv

import numpy as np

# Upper distance bound (metres) for each band, in fare_id order. The last band is open-ended.
BAND_LIMITS_M = {201: 15_000, 202: 40_000, 204: 120_000, 205: 225_000, 206: 0xFFFFFFFF}
EILAT_BANDS = {221: 201, 222: 202, 224: 204, 225: 205, 226: 206}
RAIL_FARES = (211, 212, 213, 214, 215)

KIND_BAND, KIND_EILAT, KIND_RAIL = 0, 1, 2


def read_attributes(gtfs):
    out = {}
    with open(f"{gtfs}/fare_attributes.txt", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            out[int(row["fare_id"])] = int(round(float(row["price"]) * 100))
    return out


def build(gtfs, stop_index, stop_zone, stop_code_index):
    """Returns (fare records, rail OD records, eilat stop flags, stats).

    stop_index: gtfs stop_id -> index. stop_zone: index -> zone id string.
    stop_code_index: stop_code string -> index (rail station-pair rules use station codes as zones).
    """
    prices = read_attributes(gtfs)
    zone_stops = collections.defaultdict(list)
    for i, z in enumerate(stop_zone):
        if z:
            zone_stops[z].append(i)

    eilat_zones, normal_zones = set(), set()
    rail_pairs = {}
    with open(f"{gtfs}/fare_rules.txt", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if not row["origin_id"]:
                continue
            fid = int(row["fare_id"])
            o, d = row["origin_id"], row["destination_id"]
            if fid in EILAT_BANDS:
                eilat_zones.add(o)
                eilat_zones.add(d)
            elif fid in BAND_LIMITS_M:
                normal_zones.add(o)
                normal_zones.add(d)
            elif fid in RAIL_FARES:
                oi, di = stop_code_index.get(o), stop_code_index.get(d)
                if oi is not None and di is not None:
                    rail_pairs[(oi, di)] = fid

    # Eilat-region zones are the ones that only ever appear in VAT-free (22x) rules.
    eilat_only = eilat_zones - normal_zones
    eilat = np.zeros(len(stop_zone), dtype=bool)
    for z in eilat_only:
        for i in zone_stops.get(z, ()):
            eilat[i] = True

    fare_ids = sorted(prices)
    fare_dtype = np.dtype([("fareId", "<u4"), ("agorot", "<u4"), ("maxMeters", "<u4"), ("kind", "<u4")])
    fares = np.zeros(len(fare_ids), dtype=fare_dtype)
    for k, fid in enumerate(fare_ids):
        if fid in BAND_LIMITS_M:
            kind, limit = KIND_BAND, BAND_LIMITS_M[fid]
        elif fid in EILAT_BANDS:
            kind, limit = KIND_EILAT, BAND_LIMITS_M[EILAT_BANDS[fid]]
        elif fid in RAIL_FARES:
            kind, limit = KIND_RAIL, 0
        else:
            kind, limit = 3, 0
        fares[k] = (fid, prices[fid], limit, kind)
    fare_pos = {fid: k for k, fid in enumerate(fare_ids)}

    rail_dtype = np.dtype([("origin", "<u4"), ("destination", "<u4"), ("fare", "<u4")])
    rail = np.array(sorted((o, d, fare_pos[f]) for (o, d), f in rail_pairs.items()), dtype=rail_dtype)
    stats = dict(fares=len(fares), rail_pairs=len(rail), eilat_stops=int(eilat.sum()))
    return fares, rail, eilat, stats
