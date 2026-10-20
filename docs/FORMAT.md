# Dataset format

The Zuz app routes on the device using a compact binary built from the Ministry of Transport GTFS feed by [`pipeline/build_dataset.py`](../pipeline/build_dataset.py). The app memory-maps the file and reads it in place.

## Files

Each release carries three assets:

| File | Contents | Oct 2026 feed |
|---|---|---|
| `zuz-tt.bin.xz` | Timetable, stops, routes, footpaths, fares, search index | 19.9 MB raw → **3.63 MB** xz |
| `zuz-shapes.bin.xz` | Simplified route shapes (optional) | 14.6 MB raw → **1.45 MB** xz |
| `manifest.json` | Version, validity, sizes, sha256s, download URLs, schema, build stats | a few KB |

**Compression: xz (LZMA2, CRC32, 4 MiB dictionary; 16 MiB for shapes).** Apple's Compression framework decodes it natively (`NSData.decompressed(using: .lzma)`), and the dictionary size limits the decoder's memory on the phone.

## Manifest

```json
{
  "schema": 1,
  "version": "20261015-202610152041",
  "createdAt": "2026-10-15T20:41:07Z",
  "validFrom": "2026-10-15", "validTo": "2026-11-14",
  "timeZone": "Asia/Jerusalem",
  "source": "Israel Ministry of Transport GTFS (gtfs.mot.gov.il)",
  "feedSha256": "…",
  "files": {
    "timetable": {"file": "zuz-tt.bin.xz", "compression": "xz-lzma2", "bytes": 3631528, "sha256": "…",
                  "rawBytes": 19921944, "rawSha256": "…",
                  "url": "https://github.com/ilan-uzan/zuz-data/releases/download/20261015-202610152041/zuz-tt.bin.xz"},
    "shapes": {…}
  },
  "stats": {"trips_per_day": {"2026-10-18": 122960, …}, …}
}
```

- `version` is `<feed start>-<build time>` and sorts correctly as a string. It is also the release tag. The app only installs a strictly newer version.
- `url` is the absolute, tag-pinned download URL of each file (added by [`scripts/finalize_manifest.py`](../scripts/finalize_manifest.py)). Clients should use it rather than a URL relative to `releases/latest`, which can move to a newer release between two requests.
- Clients check `bytes` and the compressed `sha256` before decompressing, and `rawBytes` and `rawSha256` after.
- `feedSha256` is the sha256 of the GTFS zip the release was built from. The nightly job skips publishing when the feed has not changed.

## Container format (`ZUZT`, schema 1)

Every integer is little-endian.

| Offset | Field | Type |
|---|---|---|
| 0 | magic `"ZUZT"` | 4 bytes |
| 4 | schema | u32 (1) |
| 8 | kind | u32 (0 = timetable, 1 = shapes) |
| 12 | sectionCount | u32 |
| 16 | startDate (feed day 0, `YYYYMMDD`) | u32 |
| 20 | dayCount (≤ 64) | u32 |
| 24 | createdAt (Unix seconds) | i64 |
| 32 | feedVersion | u32 |
| 36 | reserved | 28 bytes |
| 64 | section table: `sectionCount` × {tag[4], elementSize u32, offset u64, count u64} | 24 bytes each |

- Each section is a fixed-width array that starts on an 8-byte boundary.
- At open, the Swift reader checks that each section's `elementSize` equals `MemoryLayout<T>.stride` of its record struct, and that it fits inside the file.
- Indices are 0-based. `0xFFFFFFFF` means "none".

### Timetable sections

| Tag | Element | Meaning |
|---|---|---|
| `STRO` / `STRB` | u32 / u8 | String pool: offsets (n+1) and UTF-8 bytes. String 0 is `""`. |
| `AGEN` | {gtfsId, nameHe, nameEn, nameAr} u32 | Agencies |
| `SLAT` / `SLON` | i32 | Stop coordinates in microdegrees. Stops are ordered by grid cell, so neighbours sit together in memory. |
| `SREC` | {gtfsId, code, nameHe, nameEn, nameAr, cityHe, cityEn, cityAr, parent, flags} u32 | Stops. The city is parsed from `stop_desc`. Flags: 1 Eilat fare region, 2 station, 4 rail, 8 light rail, 16 served. |
| `RREC` | {gtfsId u32, agency u16, mode u8, flags u8, color u32, shortName, longName, desc u32} | Routes. Modes: 0 bus, 1 light rail, 2 train, 3 Metronit, 4 cable car, 5 funicular, 6 share taxi, 7 demand-responsive. Flag 1 means routable. |
| `HSGN` | {he, en, ar} u32 | Headsigns, shown as "place, city". For trains (whose feed headsign is the train number), the headsign is the terminus stop. |
| `PATT` | {firstSlot, count} u32 | Distinct stop sequences (6,994) |
| `PSTO` / `PFLG` | u32 / u8 | Pattern slots: stop index; flags (1 = no pickup, 2 = no drop-off) |
| `PROF` | {pattern, route, headsign, timesStart, tripStart, tripCount, 0, 0} u32 | **Profiles**: the RAPTOR routes (7,956). Each is one pattern with one set of offsets, so its trips never overtake each other. |
| `PARR` / `PDEP` | u16 | Arrival and departure offsets per profile slot, in seconds from trip start (max 21,239 s) |
| `TSTA` / `TSVC` / `TFLG` / `TGID` | u32 / u16 / u8 / u32 | Trips (399,451), sorted by (profile, start). Start is seconds after the service day's 00:00 and may exceed 24 h. The service is an index into `SVCD`. Flags: 1 wheelchair accessible, 2 not accessible. `TGID` is the numeric part of the GTFS `trip_id`. Real-time matching uses it only as a tie-breaker: the SIRI TripId equals it for ~20% of vehicles (see below). |
| `SVCD` | u64 | Distinct day bitsets (361). Bit *d* means the service runs on feed day *d*. |
| `SPOF` / `SPRT` | u32 / {profile, position} u32 | Stop → profiles serving it (CSR) |
| `RPOF` / `RPRO` | u32 | Route → profiles (CSR) |
| `FPOF` / `FPTO` / `FPDU` | u32 / u32 / u16 | Footpaths (CSR): stops within 400 m, walking seconds = ⌈metres × 1.25 / 1.3⌉ (414,484 pairs) |
| `GRDH` / `GROF` / `GRST` | header / u32 / u32 | Uniform 0.0045° stop grid, used for nearby stops and access/egress |
| `FARE` | {fareId, agorot, maxMeters, kind} u32 | `fare_attributes` prices. Kind: 0 distance band, 1 Eilat band, 2 rail pair |
| `RAIL` | {origin, destination, fare} u32 | Rail station-pair fares (fares 211–215, 4,555 pairs), sorted |
| `STKN` / `STOF` / `STPO` | u32 | Search tokens (string ids, sorted by UTF-8 bytes) → stops (CSR) |
| `LNKN` / `LNOF` / `LNPO` | u32 | Normalised public line numbers → routes (CSR) |
| `SCOD` | u32 | Stops sorted by stop code (binary search) |

### Shapes sections (`zuz-shapes.bin`)

| Tag | Element | Meaning |
|---|---|---|
| `SHOF` / `SHPT` | u32 / i32 pairs | Points of each shape (lat, lon in microdegrees). Douglas-Peucker at 4 m, always keeping the point nearest each stop: 7.0 M → 1.68 M points. |
| `SHBX` | {minLat, minLon, maxLat, maxLon} i32 | Bounding boxes, for viewport culling |
| `PSHP` | u32 | Per profile slot (same index space as `PARR`): the stop's point index in its shape |
| `PRSH` | u32 | Profile → shape |
| `RSHP` | u32 | Route → its most-used shape (for the idle map) |

## Semantics

- **The window.** Feed day 0 is the day before the feed's publication date (calendar.txt's timestamp, or `--feed-date`), but never earlier than the first service. The window then runs for up to 64 days, never past the last service. An old calendar row can't drag the window into the past. The manifest's `validFrom`/`validTo` are exactly this window.
- **Service days.** Times follow GTFS: seconds after "noon minus 12 h" in Asia/Jerusalem.
  - For a query day D, the app builds a `DayTimetable` covering D−1, D and D+1 as absolute seconds after 00:00 of D−1. This way a 00:30 query sees D−1's 24:30 trips.
  - Building it costs about 5 ms, and it is cached.
- **Outside the feed's validity,** a day borrows the timetable of the same weekday inside it and is marked `isProjected`. An old bundled dataset therefore keeps answering.
- **Demand-responsive lines** (`route_type 715`, 315 trips) are left out of routing because they need a booking. Their route metadata is kept.
- **Colours:** `route_color` is used where the feed sets it (1,390 routes). Otherwise the colour comes from [`palette/route-colors.json`](../palette/route-colors.json) (the route colours of the app's palette):
  - rail → `mode.train`;
  - the TLV light rail → `line.tlv.red`;
  - JLM line 1 → `line.jlm.red`;
  - other light rail and the Carmelit → `mode.lightRail`;
  - share taxis → `mode.sherut`;
  - Dan companies → `line.dan`;
  - other bus agencies → a fixed agency-indexed palette.
- **Search normalisation** is defined in [`pipeline/zuzdata/text.py`](../pipeline/zuzdata/text.py) and mirrored in the app's `SearchNormalizer`. Both test suites pin the same examples. The rules:
  - lowercase ASCII;
  - drop niqqud, harakat and apostrophes/geresh/gershayim;
  - fold Hebrew final letters and Arabic letter variants;
  - every other non-alphanumeric character separates words.

## Known gaps in the source data

- No `calendar_dates`, `transfers`, `frequencies` or `feed_info`. Footpaths are therefore generated, and holidays rely on per-date `calendar` rows.
- 2,713 of 35,383 stop names (feed of 15 Oct 2026) have no English or Arabic translation. English and Russian show them in Latin letters (`HebrewTransliteration`: common stop words by meaning, the rest letter by letter); Arabic shows the Hebrew. There is no Russian at all, so Russian shows English.
- Israel Railways routes have no `route_short_name`; the app stores them as "IR" and shows "Train" in the rider's language. 347 bus routes end in a Hebrew letter ("62א"); outside Hebrew it reads as its Latin letter ("62A").
- Some translations are wrong in the feed itself. For example, the Arabic name of Jerusalem Yitzhak Navon station reads "Yitzhak Rabin".
- `stops.wheelchair_boarding` is absent. Only per-trip `wheelchair_accessible` is kept.
- Israel Railways trips have no `shape_id`, so train legs are drawn through their stations rather than along the track.
- No start time is past 24:00. Night trips still cross midnight through their offsets (7,405 trips end after 24:00), and the day tables handle both cases.
- Israel Railways has no public line numbers. Trains show "IR" plus the terminus.
- The Tariff.zip passes (daily, weekly, monthly) are not in the dataset yet.

