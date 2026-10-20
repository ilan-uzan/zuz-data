"""GTFS -> Zuz timetable and shapes containers."""

import collections
import csv
import datetime as dt
import io
import json
import os
import sys
import time
import zipfile

import numpy as np

from . import SCHEMA_VERSION, fares, geo, shapes, text
from .container import Container, StringPool

# Route modes stored in the dataset (RREC.mode). The app maps them to `TransitMode`.
MODE_BUS, MODE_LIGHT_RAIL, MODE_TRAIN, MODE_METRONIT, MODE_CABLE_CAR, MODE_FUNICULAR, MODE_SHARE_TAXI, MODE_ON_DEMAND = range(8)

# Stop flags (SREC.flags).
STOP_EILAT, STOP_STATION, STOP_RAIL, STOP_LIGHT_RAIL, STOP_SERVED = 1, 2, 4, 8, 16
# Pattern slot flags (PFLG).
SLOT_NO_PICKUP, SLOT_NO_DROP_OFF = 1, 2
# Trip flags (TFLG).
TRIP_WHEELCHAIR, TRIP_NOT_WHEELCHAIR = 1, 2
# Route flags (RREC.flags).
ROUTE_ROUTABLE = 1

NONE = 0xFFFFFFFF


def log(*args):
    print(*args, file=sys.stderr, flush=True)


class Source:
    """A GTFS directory or zip; members are streamed, never loaded whole."""

    def __init__(self, path):
        self.path = path
        self.zip = zipfile.ZipFile(path) if zipfile.is_zipfile(path) else None

    def open(self, name):
        if self.zip:
            member = next((n for n in self.zip.namelist() if n.rsplit("/", 1)[-1] == name), None)
            if member is None:
                raise FileNotFoundError(name)
            return io.TextIOWrapper(self.zip.open(member), encoding="utf-8-sig", newline="")
        return open(os.path.join(self.path, name), encoding="utf-8-sig", newline="")

    def rows(self, name):
        with self.open(name) as f:
            yield from csv.DictReader(f)

    def feed_date(self):
        """When the feed was published: calendar.txt's timestamp (zip entry or file mtime)."""
        if self.zip:
            member = next(n for n in self.zip.namelist() if n.rsplit("/", 1)[-1] == "calendar.txt")
            return dt.date(*self.zip.getinfo(member).date_time[:3])
        return dt.date.fromtimestamp(os.path.getmtime(os.path.join(self.path, "calendar.txt")))

    def local_dir(self, scratch):
        """fares.read_* want a directory; extract the two small fare files if we were given a zip."""
        if not self.zip:
            return self.path
        os.makedirs(scratch, exist_ok=True)
        for name in ("fare_attributes.txt", "fare_rules.txt"):
            with self.open(name) as src, open(os.path.join(scratch, name), "w", encoding="utf-8") as dst:
                dst.write(src.read())
        return scratch


def hhmmss(s):
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def ymd(s):
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def load_palette(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: int(v["light"].lstrip("#")[:6], 16) for k, v in raw.items() if isinstance(v, dict) and "light" in v}


def route_color(route, palette):
    """route_color when the feed sets it; otherwise a deterministic colour by agency and mode."""
    if route["route_color"]:
        return int(route["route_color"], 16)
    agency = int(route["agency_id"])
    rtype = route["route_type"]
    short = route["route_short_name"]
    if rtype == "2":
        return palette["mode.train"]
    if rtype == "0":
        if agency == 22:  # NTA Tel Aviv light rail: every current line is a Red Line variant.
            return palette["line.tlv.red"]
        if agency == 21 and short == "1":  # Jerusalem light rail Red Line.
            return palette["line.jlm.red"]
        return palette["mode.lightRail"]
    if rtype == "5":
        return palette["mode.lightRail"]
    if rtype == "8":
        return palette["mode.sherut"]
    if agency in (5, 31, 32):  # Dan, Dan BaDarom, Dan Be'er Sheva
        return palette["line.dan"]
    bus_family = ["mode.bus", "mode.brt", "mode.bike", "region.negevDune.rust", "region.galileeGreen",
                  "region.telAvivSea", "brand.flagBlue", "region.shabbatDusk"]
    return palette[bus_family[agency % len(bus_family)]]


def route_mode(route):
    rtype = route["route_type"]
    if rtype == "2":
        return MODE_TRAIN
    if rtype == "0":
        return MODE_LIGHT_RAIL
    if rtype == "5":
        return MODE_CABLE_CAR if route["agency_id"] == "33" else MODE_FUNICULAR
    if rtype == "8":
        return MODE_SHARE_TAXI
    if rtype == "715":
        return MODE_ON_DEMAND
    return MODE_BUS


def city_of(desc):
    # stop_desc looks like "רחוב: בן יהודה 74 עיר: כפר סבא רציף:  קומה: ".
    if "עיר:" not in desc:
        return ""
    rest = desc.split("עיר:", 1)[1]
    return rest.split("רציף:", 1)[0].strip()


def display_he(name):
    # The feed writes gershayim as two apostrophes (בי''ס); show the real character.
    return name.replace("''", "״")


MAX_DAYS = 64  # service bitsets are u64


def service_window(calendar_rows, feed_date):
    """First feed day and day count for the bitsets.

    Anchored at the day before the feed was published (so tonight's after-midnight trips of the
    previous service day are kept), never before the earliest service and never past the latest:
    a stray years-old calendar row must not drag the 64-day window into the past.
    """
    earliest = min(ymd(c["start_date"]) for c in calendar_rows)
    latest = max(ymd(c["end_date"]) for c in calendar_rows)
    start = max(earliest, feed_date - dt.timedelta(days=1))
    if start > latest:
        raise ValueError(f"no service on or after {start} (calendar ends {latest})")
    return start, min((latest - start).days + 1, MAX_DAYS)


def build(gtfs_path, out_dir, palette_path, shapes_enabled=True, feed_date=None):
    t0 = time.time()
    src = Source(gtfs_path)
    pool = StringPool()
    stats = {}

    # Translations are keyed by the Hebrew string (legacy Google format).
    tr = collections.defaultdict(dict)
    for row in src.rows("translations.txt"):
        tr[row["trans_id"]][row["lang"].upper()] = row["translation"]

    def names(he):
        t = tr.get(he, {})
        return pool.add(display_he(he)), pool.add(t.get("EN", "")), pool.add(t.get("AR", ""))

    # --- Agencies
    agencies = list(src.rows("agency.txt"))
    agency_index = {a["agency_id"]: i for i, a in enumerate(agencies)}
    agency_rec = np.zeros(len(agencies), dtype=[("gtfsId", "<u4"), ("nameHe", "<u4"), ("nameEn", "<u4"), ("nameAr", "<u4")])
    for i, a in enumerate(agencies):
        he, en, ar = names(a["agency_name"])
        agency_rec[i] = (int(a["agency_id"]), he, en, ar)

    # --- Stops, ordered spatially so neighbours sit close in memory during routing.
    raw_stops = list(src.rows("stops.txt"))
    lat = np.array([round(float(s["stop_lat"]) * 1e6) for s in raw_stops], dtype=np.int64)
    lon = np.array([round(float(s["stop_lon"]) * 1e6) for s in raw_stops], dtype=np.int64)
    cell = ((lat - lat.min()) // geo.GRID_CELL_E6) * 10_000 + (lon - lon.min()) // geo.GRID_CELL_E6
    order = np.lexsort((lon, cell))
    raw_stops = [raw_stops[i] for i in order]
    lat, lon = lat[order], lon[order]
    stop_index = {s["stop_id"]: i for i, s in enumerate(raw_stops)}
    n_stops = len(raw_stops)
    stop_dtype = np.dtype([("gtfsId", "<u4"), ("code", "<u4"), ("nameHe", "<u4"), ("nameEn", "<u4"), ("nameAr", "<u4"),
                           ("cityHe", "<u4"), ("cityEn", "<u4"), ("cityAr", "<u4"), ("parent", "<u4"), ("flags", "<u4")])
    stop_rec = np.zeros(n_stops, dtype=stop_dtype)
    stop_zone, code_index = [], {}
    for i, s in enumerate(raw_stops):
        he, en, ar = names(s["stop_name"])
        city = city_of(s["stop_desc"])
        che, cen, car = names(city) if city else (0, 0, 0)
        code = int(s["stop_code"]) if s["stop_code"].isdigit() else 0
        flags = STOP_STATION if s["location_type"] == "1" else 0
        stop_rec[i] = (int(s["stop_id"]), code, he, en, ar, che, cen, car, NONE, flags)
        stop_zone.append(s["zone_id"] or s["stop_code"])
        if s["stop_code"]:
            code_index.setdefault(s["stop_code"], i)
    for i, s in enumerate(raw_stops):
        if s["parent_station"] in stop_index:
            stop_rec[i]["parent"] = stop_index[s["parent_station"]]
    log(f"stops {n_stops}")

    # --- Routes
    palette = load_palette(palette_path)
    raw_routes = list(src.rows("routes.txt"))
    route_index = {r["route_id"]: i for i, r in enumerate(raw_routes)}
    route_dtype = np.dtype([("gtfsId", "<u4"), ("agency", "<u2"), ("mode", "u1"), ("flags", "u1"), ("color", "<u4"),
                            ("shortName", "<u4"), ("longName", "<u4"), ("desc", "<u4")])
    route_rec = np.zeros(len(raw_routes), dtype=route_dtype)
    for i, r in enumerate(raw_routes):
        route_rec[i] = (int(r["route_id"]), agency_index[r["agency_id"]], route_mode(r), 0, route_color(r, palette),
                        pool.add(r["route_short_name"]), pool.add(display_he(r["route_long_name"])), pool.add(r["route_desc"]))

    # --- Services: calendar rows -> distinct day bitsets over the feed's validity.
    cal = list(src.rows("calendar.txt"))
    start, day_count = service_window(cal, feed_date or src.feed_date())
    end = start + dt.timedelta(days=day_count - 1)
    weekday_cols = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    bits_index, bitsets, service_of = {}, [], {}
    for c in cal:
        a, b = ymd(c["start_date"]), ymd(c["end_date"])
        bits = 0
        for d in range(day_count):
            day = start + dt.timedelta(days=d)
            if a <= day <= b and c[weekday_cols[day.weekday()]] == "1":
                bits |= 1 << d
        if bits == 0:
            continue
        if bits not in bits_index:
            bits_index[bits] = len(bitsets)
            bitsets.append(bits)
        service_of[c["service_id"]] = bits_index[bits]
    assert len(bitsets) < 0xFFFF
    stats["services"] = len(bitsets)
    log(f"calendar {start}..{end} ({day_count} days), {len(bitsets)} distinct service bitsets")

    # --- Trips
    headsign_ids = {}
    headsign_rec = []

    def headsign(raw):
        found = headsign_ids.get(raw)
        if found is None:
            parts = [p.strip() for p in raw.split("_") if p.strip()]
            # "יבנה_תחנה מרכזית" is city_place; show "place, city" in every language.
            parts = parts[::-1]
            he = ", ".join(display_he(p) for p in parts)
            en = ", ".join(tr.get(p, {}).get("EN") or display_he(p) for p in parts)
            ar = ", ".join(tr.get(p, {}).get("AR") or display_he(p) for p in parts)
            found = len(headsign_rec)
            headsign_ids[raw] = found
            headsign_rec.append((pool.add(he), pool.add(en), pool.add(ar)))
        return found

    def headsign_for_stop(s):
        # Israel Railways puts the train number in trip_headsign; riders want the terminus.
        key = ("stop", s)
        found = headsign_ids.get(key)
        if found is None:
            found = len(headsign_rec)
            headsign_ids[key] = found
            r = stop_rec[s]
            headsign_rec.append((int(r["nameHe"]), int(r["nameEn"] or r["nameHe"]), int(r["nameAr"] or r["nameHe"])))
        return found

    def has_words(raw):
        return any(ch.isalpha() for ch in raw)

    trip_meta = {}
    skipped_on_demand = 0
    for t in src.rows("trips.txt"):
        r = route_index.get(t["route_id"])
        svc = service_of.get(t["service_id"])
        if r is None or svc is None:
            continue
        if route_rec[r]["mode"] == MODE_ON_DEMAND:
            skipped_on_demand += 1
            continue
        wc = t.get("wheelchair_accessible", "")
        flags = TRIP_WHEELCHAIR if wc == "1" else TRIP_NOT_WHEELCHAIR if wc == "2" else 0
        prefix = t["trip_id"].split("_", 1)[0]
        gtfs_num = int(prefix) if prefix.isdigit() and int(prefix) < NONE else NONE
        hs = headsign(t["trip_headsign"]) if has_words(t["trip_headsign"]) else None
        trip_meta[t["trip_id"]] = (r, svc, hs, t["shape_id"], flags, gtfs_num)
    stats["trips_skipped_on_demand"] = skipped_on_demand
    log(f"trips {len(trip_meta)} (skipped {skipped_on_demand} demand-responsive)")

    # --- stop_times: stream, one trip at a time.
    pattern_ids, pattern_list = {}, []          # (stops, flags) -> id
    profile_ids, profile_list = {}, []          # key -> id
    trips = []                                  # (profile, start, service, flags, gtfs id)
    seen = set()
    rows_read = 0

    def finish(trip_id, rows):
        meta = trip_meta.get(trip_id)
        if meta is None or len(rows) < 2:
            return
        rows.sort()
        stops_seq = tuple(r[1] for r in rows)
        slot_flags = tuple(r[4] for r in rows)
        t_start = min(rows[0][2], rows[0][3])
        arr = tuple(r[2] - t_start for r in rows)
        dep = tuple(r[3] - t_start for r in rows)
        if max(dep) > 0xFFFF or max(arr) > 0xFFFF:
            raise ValueError(f"trip {trip_id} longer than 18 h")
        pkey = (stops_seq, slot_flags)
        pid = pattern_ids.get(pkey)
        if pid is None:
            pid = len(pattern_list)
            pattern_ids[pkey] = pid
            pattern_list.append(pkey)
        route, svc, hs, shape, tflags, gtfs_num = meta
        if hs is None:
            hs = headsign_for_stop(stops_seq[-1])
        key =(route, hs, pid, shape, arr, dep)
        prof = profile_ids.get(key)
        if prof is None:
            prof = len(profile_list)
            profile_ids[key] = prof
            profile_list.append(key)
        trips.append((prof, t_start, svc, tflags, gtfs_num))

    with src.open("stop_times.txt") as f:
        reader = csv.reader(f)
        header = next(reader)
        i_trip, i_arr, i_dep, i_stop, i_seq = (header.index(k) for k in ("trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"))
        i_pick = header.index("pickup_type") if "pickup_type" in header else None
        i_drop = header.index("drop_off_type") if "drop_off_type" in header else None
        current, rows = None, []
        for row in reader:
            rows_read += 1
            tid = row[i_trip]
            if tid != current:
                if current is not None:
                    finish(current, rows)
                if tid in seen:
                    raise ValueError(f"stop_times.txt is not grouped by trip_id ({tid})")
                seen.add(tid)
                current, rows = tid, []
            if tid not in trip_meta:
                continue
            s = stop_index.get(row[i_stop])
            if s is None:
                continue
            fl = 0
            if i_pick is not None and row[i_pick] == "1":
                fl |= SLOT_NO_PICKUP
            if i_drop is not None and row[i_drop] == "1":
                fl |= SLOT_NO_DROP_OFF
            rows.append((int(row[i_seq]), s, hhmmss(row[i_arr]), hhmmss(row[i_dep]), fl))
            if rows_read % 2_000_000 == 0:
                log(f"  stop_times {rows_read:,} rows, {len(profile_list)} profiles, {time.time() - t0:.0f}s")
        if current is not None:
            finish(current, rows)
    stats.update(stop_times=rows_read, patterns=len(pattern_list), profiles=len(profile_list), trips=len(trips))
    log(f"stop_times {rows_read:,} rows -> {len(pattern_list)} patterns, {len(profile_list)} profiles, {len(trips)} trips")

    # --- Patterns
    pat_rec = np.zeros(len(pattern_list), dtype=[("firstSlot", "<u4"), ("count", "<u4")])
    pat_stops, pat_flags = [], []
    for i, (seq, fl) in enumerate(pattern_list):
        pat_rec[i] = (len(pat_stops), len(seq))
        pat_stops.extend(seq)
        pat_flags.extend(fl)
    pat_stops = np.array(pat_stops, dtype="<u4")
    pat_flags = np.array(pat_flags, dtype="u1")

    # --- Trips sorted by (profile, start); profiles keep [tripStart, tripCount).
    trip_arr = np.array(trips, dtype=[("profile", "<u4"), ("start", "<u4"), ("service", "<u2"), ("flags", "u1"), ("gtfs", "<u4")])
    trip_arr = trip_arr[np.lexsort((trip_arr["start"], trip_arr["profile"]))]
    counts = np.bincount(trip_arr["profile"], minlength=len(profile_list))
    trip_first = np.zeros(len(profile_list) + 1, dtype=np.int64)
    np.cumsum(counts, out=trip_first[1:])

    shape_ids = sorted({key[3] for key in profile_list if key[3]})
    prof_rec = np.zeros(len(profile_list), dtype=[("pattern", "<u4"), ("route", "<u4"), ("headsign", "<u4"), ("timesStart", "<u4"),
                                                  ("tripStart", "<u4"), ("tripCount", "<u4"), ("reserved0", "<u4"), ("reserved1", "<u4")])
    arr_off, dep_off = [], []
    for i, (route, hs, pid, shape, arr, dep) in enumerate(profile_list):
        prof_rec[i] = (pid, route, hs, len(arr_off), trip_first[i], counts[i], 0, 0)
        arr_off.extend(arr)
        dep_off.extend(dep)
        route_rec[route]["flags"] |= ROUTE_ROUTABLE
    arr_off = np.array(arr_off, dtype="<u2")
    dep_off = np.array(dep_off, dtype="<u2")

    # Stop flags from what serves them.
    for i, (route, hs, pid, shape, arr, dep) in enumerate(profile_list):
        mode = route_rec[route]["mode"]
        bit = STOP_RAIL if mode == MODE_TRAIN else STOP_LIGHT_RAIL if mode == MODE_LIGHT_RAIL else 0
        for s in pattern_list[pid][0]:
            stop_rec[s]["flags"] |= STOP_SERVED | bit

    # --- Stop -> (profile, position) index for RAPTOR route collection and departure boards.
    sp = [[] for _ in range(n_stops)]
    for i, (route, hs, pid, shape, arr, dep) in enumerate(profile_list):
        for pos, s in enumerate(pattern_list[pid][0]):
            sp[s].append((i, pos))
    sp_off = np.zeros(n_stops + 1, dtype="<u4")
    np.cumsum([len(x) for x in sp], out=sp_off[1:])
    sp_rec = np.array([e for x in sp for e in x], dtype=[("profile", "<u4"), ("position", "<u4")])

    # --- Route -> profiles (line search, map).
    rp = [[] for _ in range(len(raw_routes))]
    for i, key in enumerate(profile_list):
        rp[key[0]].append(i)
    rp_off = np.zeros(len(raw_routes) + 1, dtype="<u4")
    np.cumsum([len(x) for x in rp], out=rp_off[1:])
    rp_ids = np.array([p for x in rp for p in x], dtype="<u4")

    # --- Footpaths and grid
    fp_off, fp_to, fp_sec = geo.footpaths(lat, lon)
    stats["footpaths"] = int(len(fp_to))
    log(f"footpaths {len(fp_to):,}")
    grid_header, grid_off, grid_stops = geo.grid(lat, lon)
    grid_rec = np.array([grid_header], dtype=[("minLat", "<i4"), ("minLon", "<i4"), ("cellLat", "<i4"), ("cellLon", "<i4"),
                                              ("rows", "<u4"), ("cols", "<u4")])

    # --- Fares
    fare_rec, rail_rec, eilat, fare_stats = fares.build(src.local_dir(os.path.join(out_dir, ".fares")), stop_index, stop_zone, code_index)
    stats.update(fare_stats)
    for i in np.flatnonzero(eilat):
        stop_rec[i]["flags"] |= STOP_EILAT

    # --- Search: normalised name tokens (he/en/ar) -> stops; line numbers -> routes; codes -> stops.
    token_stops = collections.defaultdict(set)
    for i, s in enumerate(raw_stops):
        if not stop_rec[i]["flags"] & STOP_SERVED:
            continue
        for sid in (stop_rec[i]["nameHe"], stop_rec[i]["nameEn"], stop_rec[i]["nameAr"]):
            for tok in text.tokens(pool.items[sid]):
                if tok:
                    token_stops[tok].add(i)
    tokens_sorted = sorted(token_stops)
    tok_ids = np.array([pool.add(t) for t in tokens_sorted], dtype="<u4")
    tok_off = np.zeros(len(tokens_sorted) + 1, dtype="<u4")
    np.cumsum([len(token_stops[t]) for t in tokens_sorted], out=tok_off[1:])
    tok_post = np.array([s for t in tokens_sorted for s in sorted(token_stops[t])], dtype="<u4")

    line_routes = collections.defaultdict(list)
    for i, r in enumerate(raw_routes):
        if route_rec[i]["flags"] & ROUTE_ROUTABLE and r["route_short_name"]:
            line_routes[text.normalize(r["route_short_name"])].append(i)
    lines_sorted = sorted(k for k in line_routes if k)
    line_ids = np.array([pool.add(k) for k in lines_sorted], dtype="<u4")
    line_off = np.zeros(len(lines_sorted) + 1, dtype="<u4")
    np.cumsum([len(line_routes[k]) for k in lines_sorted], out=line_off[1:])
    line_post = np.array([r for k in lines_sorted for r in line_routes[k]], dtype="<u4")

    served_with_code = [i for i in range(n_stops) if stop_rec[i]["code"]]
    codes_sorted = np.array(sorted(served_with_code, key=lambda i: int(stop_rec[i]["code"])), dtype="<u4")
    stats.update(search_tokens=len(tokens_sorted), line_names=len(lines_sorted))

    # --- Shapes (separate optional file)
    shape_container = None
    if shapes_enabled:
        shape_container = build_shapes(src, shape_ids, profile_list, pattern_list, prof_rec, len(arr_off), lat, lon, route_rec,
                                       trip_first, start, day_count, stats)

    # --- Assemble the timetable container.
    created = int(time.time())
    feed_version = int(start.strftime("%Y%m%d"))
    c = Container(0, SCHEMA_VERSION, int(start.strftime("%Y%m%d")), day_count, created, feed_version)
    str_off, str_bytes = pool.arrays()
    c.add("STRO", str_off)
    c.add("STRB", str_bytes)
    c.add("AGEN", agency_rec)
    c.add("SLAT", lat.astype("<i4"))
    c.add("SLON", lon.astype("<i4"))
    c.add("SREC", stop_rec)
    c.add("RREC", route_rec)
    c.add("HSGN", np.array(headsign_rec, dtype=[("he", "<u4"), ("en", "<u4"), ("ar", "<u4")]))
    c.add("PATT", pat_rec)
    c.add("PSTO", pat_stops)
    c.add("PFLG", pat_flags)
    c.add("PROF", prof_rec)
    c.add("PARR", arr_off)
    c.add("PDEP", dep_off)
    c.add("TSTA", trip_arr["start"].astype("<u4"))
    c.add("TSVC", trip_arr["service"].astype("<u2"))
    c.add("TFLG", trip_arr["flags"].astype("u1"))
    c.add("TGID", trip_arr["gtfs"].astype("<u4"))
    c.add("SVCD", np.array(bitsets, dtype="<u8"))
    c.add("SPOF", sp_off)
    c.add("SPRT", sp_rec)
    c.add("RPOF", rp_off)
    c.add("RPRO", rp_ids)
    c.add("FPOF", fp_off)
    c.add("FPTO", fp_to)
    c.add("FPDU", fp_sec)
    c.add("GRDH", grid_rec)
    c.add("GROF", grid_off)
    c.add("GRST", grid_stops)
    c.add("FARE", fare_rec)
    c.add("RAIL", rail_rec)
    c.add("STKN", tok_ids)
    c.add("STOF", tok_off)
    c.add("STPO", tok_post)
    c.add("LNKN", line_ids)
    c.add("LNOF", line_off)
    c.add("LNPO", line_post)
    c.add("SCOD", codes_sorted)

    # Weekday sanity numbers for the manifest and tests.
    svc_bits = np.array(bitsets, dtype=np.uint64)
    per_day = []
    for d in range(day_count):
        active = (svc_bits >> np.uint64(d)) & np.uint64(1)
        per_day.append(int(active[trip_arr["service"]].sum()))
    stats["trips_per_day"] = {(start + dt.timedelta(days=d)).isoformat(): n for d, n in enumerate(per_day)}
    stats["stops"] = n_stops
    stats["seconds"] = round(time.time() - t0, 1)
    meta = dict(start=start, end=end, day_count=day_count, created=created)
    return c, shape_container, stats, meta


def build_shapes(src, shape_ids, profile_list, pattern_list, prof_rec, n_slots, lat, lon, route_rec, trip_first, start, day_count, stats):
    wanted = set(shape_ids)
    log(f"shapes: reading {len(wanted)} shapes")
    with src.open("shapes.txt") as f:
        raw = shapes.read_shapes(f, wanted)
    # Snap every (shape, pattern) pair once; profiles sharing both reuse the result.
    pairs = collections.defaultdict(set)
    for i, key in enumerate(profile_list):
        if key[3] in raw:
            pairs[key[3]].add(key[2])
    simplified, slot_index = {}, {}
    for sid, pids in pairs.items():
        pts = raw[sid]
        snapped = {}
        for pid in pids:
            seq = pattern_list[pid][0]
            stop_ll = np.stack([lat[list(seq)] / 1e6, lon[list(seq)] / 1e6], axis=1)
            snapped[pid] = shapes.snap(pts, stop_ll)
        keep = np.unique(np.concatenate(list(snapped.values())))
        kept = shapes.simplify(pts, keep)
        simplified[sid] = pts[kept]
        for pid, idx in snapped.items():
            slot_index[(sid, pid)] = np.searchsorted(kept, idx)
    order = [s for s in shape_ids if s in simplified]
    pos = {s: i for i, s in enumerate(order)}
    sh_off = np.zeros(len(order) + 1, dtype="<u4")
    np.cumsum([len(simplified[s]) for s in order], out=sh_off[1:])
    pts_all = np.concatenate([simplified[s] for s in order]) if order else np.zeros((0, 2))
    sh_pts = np.round(pts_all * 1e6).astype("<i4").reshape(-1)
    bbox = np.zeros(len(order), dtype=[("minLat", "<i4"), ("minLon", "<i4"), ("maxLat", "<i4"), ("maxLon", "<i4")])
    for i, s in enumerate(order):
        p = np.round(simplified[s] * 1e6).astype(np.int64)
        bbox[i] = (p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max())
    # Per profile slot: index of the stop's point within the profile's shape (NONE without a shape).
    slots = np.full(n_slots, NONE, dtype="<u4")
    prof_shape = np.full(len(profile_list), NONE, dtype="<u4")
    for i, key in enumerate(profile_list):
        sid, pid = key[3], key[2]
        if (sid, pid) in slot_index:
            first = int(prof_rec["timesStart"][i])
            idx = slot_index[(sid, pid)]
            slots[first:first + len(idx)] = idx
            prof_shape[i] = pos[sid]
    # Representative shape per route: the one used by the most trips.
    rshp = np.full(len(route_rec), NONE, dtype="<u4")
    best = {}
    for i, key in enumerate(profile_list):
        if prof_shape[i] == NONE:
            continue
        n = int(trip_first[i + 1] - trip_first[i])
        r = key[0]
        if r not in best or n > best[r][0] or (n == best[r][0] and len(simplified[key[3]]) > best[r][2]):
            best[r] = (n, prof_shape[i], len(simplified[key[3]]))
    for r, (_, s, _) in best.items():
        rshp[r] = s
    stats["shape_points_raw"] = int(sum(len(v) for v in raw.values()))
    stats["shape_points"] = int(len(pts_all))
    log(f"shapes: {stats['shape_points_raw']:,} -> {stats['shape_points']:,} points")
    c = Container(1, SCHEMA_VERSION, int(start.strftime("%Y%m%d")), day_count, int(time.time()), int(start.strftime("%Y%m%d")))
    c.add("SHOF", sh_off)
    c.add("SHPT", sh_pts)
    c.add("SHBX", bbox)
    c.add("PSHP", slots)
    c.add("PRSH", prof_shape)
    c.add("RSHP", rshp)
    return c
