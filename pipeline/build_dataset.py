#!/usr/bin/env python3
"""Build Zuz's on-device timetable from the Ministry of Transport GTFS feed.

    python3 -I pipeline/build_dataset.py <gtfs dir or zip> <out dir> [--no-shapes] [--palette PATH]

Writes to <out dir>:
    zuz-tt.bin          raw timetable container (mmap-able; see docs/DATASET.md)
    zuz-tt.bin.xz       the distributable (xz/LZMA2, decoded by Apple's Compression framework)
    zuz-shapes.bin(.xz) simplified route shapes (optional)
    manifest.json       version, validity, sizes, sha256 and schema

Input files are untrusted data: they are only parsed, never executed.
"""

import argparse
import datetime as dt
import hashlib
import json
import lzma
import os
import sys
from pathlib import Path

# -I drops the script's directory from sys.path; add our own package explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from zuzdata import SCHEMA_VERSION  # noqa: E402
from zuzdata.build import build, log  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


# xz with CRC32 and the LZMA2 filter is the container Apple's COMPRESSION_LZMA decoder reads.
# The dictionary size is also the decoder's working memory on the phone: 4 MiB for the timetable (no gain
# beyond it), 16 MiB for shapes (absolute coordinates repeat across shapes far apart in the file).
def xz_filters(dict_mib):
    return [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME, "dict_size": dict_mib << 20}]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(out: Path, name: str, raw: bytes, dict_mib: int):
    (out / name).write_bytes(raw)
    packed = lzma.compress(raw, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32, filters=xz_filters(dict_mib))
    (out / f"{name}.xz").write_bytes(packed)
    log(f"{name}: {len(raw):,} B raw, {len(packed):,} B xz")
    return {
        "file": f"{name}.xz",
        "compression": "xz-lzma2",
        "bytes": len(packed),
        "sha256": sha256(packed),
        "rawBytes": len(raw),
        "rawSha256": sha256(raw),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gtfs", help="GTFS directory or zip")
    ap.add_argument("out", help="output directory")
    ap.add_argument("--no-shapes", action="store_true", help="skip the shapes file")
    ap.add_argument("--palette", default=str(REPO / "palette/route-colors.json"))
    ap.add_argument("--feed-date", type=dt.date.fromisoformat,
                    help="publication date (YYYY-MM-DD); default: calendar.txt's timestamp. Day 0 is the day before")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    timetable, shape_container, stats, meta = build(args.gtfs, str(out), args.palette, shapes_enabled=not args.no_shapes,
                                                    feed_date=args.feed_date)

    files = {"timetable": write(out, "zuz-tt.bin", timetable.tobytes(), 4)}
    if shape_container is not None:
        files["shapes"] = write(out, "zuz-shapes.bin", shape_container.tobytes(), 16)

    start, end = meta["start"], meta["end"]
    created = dt.datetime.fromtimestamp(meta["created"], dt.timezone.utc)
    manifest = {
        "schema": SCHEMA_VERSION,
        "version": f"{start:%Y%m%d}-{created:%Y%m%d%H%M}",
        "createdAt": created.isoformat().replace("+00:00", "Z"),
        "validFrom": start.isoformat(),
        "validTo": end.isoformat(),
        "timeZone": "Asia/Jerusalem",
        "source": "Israel Ministry of Transport GTFS (gtfs.mot.gov.il)",
        "files": files,
        "stats": stats,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"done in {stats['seconds']} s -> {out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
