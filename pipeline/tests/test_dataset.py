"""Sanity checks for a built dataset.

    ZUZ_DATASET=<out dir> ZUZ_GTFS=<gtfs dir> python3 -I -m unittest discover -s pipeline/tests -v

Dataset tests are skipped when ZUZ_DATASET is not set; the normalisation tests always run.
"""

import csv
import datetime as dt
import math
import os
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zuzdata import text  # noqa: E402

DATASET = os.environ.get("ZUZ_DATASET")
GTFS = os.environ.get("ZUZ_GTFS")


class NormalizeTests(unittest.TestCase):
    # The same examples are pinned in ZuzTests/StopSearchTests.swift.
    def test_hebrew(self):
        self.assertEqual(text.normalize("בי''ס בר לב/בן יהודה"), "ביס בר לב בנ יהודה")
        self.assertEqual(text.normalize("ת. מרכזית"), "ת מרכזית")
        self.assertEqual(text.normalize("שָׁלוֹם"), "שלומ")

    def test_english(self):
        self.assertEqual(text.normalize("Tel Aviv-Yafo  Savidor"), "tel aviv yafo savidor")
        self.assertEqual(text.normalize("Ben Gurion's"), "ben gurions")

    def test_arabic(self):
        self.assertEqual(text.normalize("المحطة المركزية"), "المحطه المركزيه")
        self.assertEqual(text.normalize("أُمّ"), "ام")


class ServiceWindowTests(unittest.TestCase):
    """Day 0 follows the feed date, not the oldest calendar row."""

    @staticmethod
    def rows(*ranges):
        return [{"start_date": a, "end_date": b} for a, b in ranges]

    def test_anchored_the_day_before_the_feed(self):
        from zuzdata.build import service_window
        start, days = service_window(self.rows(("20261015", "20261114")), dt.date(2026, 10, 14))
        self.assertEqual((start, days), (dt.date(2026, 10, 15), 31))

    def test_old_services_do_not_drag_the_window_back(self):
        from zuzdata.build import service_window
        start, days = service_window(self.rows(("20200101", "20261231"), ("20261015", "20261114")), dt.date(2026, 10, 14))
        self.assertEqual(start, dt.date(2026, 10, 13))
        self.assertEqual(days, 64)

    def test_feed_with_nothing_ahead_fails(self):
        from zuzdata.build import service_window
        with self.assertRaises(ValueError):
            service_window(self.rows(("20200101", "20200201")), dt.date(2026, 10, 14))


@unittest.skipUnless(DATASET, "set ZUZ_DATASET to a built dataset directory")
class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from zuzdata.reader import Dataset
        cls.ds = Dataset(Path(DATASET) / "zuz-tt.bin")

    def first_weekday(self, weekday):
        start = dt.datetime.strptime(str(self.ds.header["start_date"]), "%Y%m%d").date()
        for d in range(self.ds.header["day_count"]):
            if (start + dt.timedelta(days=d)).weekday() == weekday:
                return d
        self.fail("no such weekday in the feed")

    def test_stop_count(self):
        self.assertGreater(len(self.ds["SREC"]), 30_000)

    def test_weekday_trips(self):
        sunday = self.first_weekday(6)
        self.assertGreater(self.ds.trips_on_day(sunday), 100_000)
        # Saturday service is a small fraction of a weekday.
        self.assertLess(self.ds.trips_on_day(self.first_weekday(5)), self.ds.trips_on_day(sunday) / 3)

    def test_structure(self):
        prof, patt = self.ds["PROF"], self.ds["PATT"]
        starts = self.ds["TSTA"]
        for i in random.Random(1).sample(range(len(prof)), 300):
            p = prof[i]
            trips = starts[p["tripStart"]:p["tripStart"] + p["tripCount"]]
            self.assertTrue((trips[1:] >= trips[:-1]).all(), "trips sorted by start")
            n = patt[p["pattern"]]["count"]
            arr = self.ds["PARR"][p["timesStart"]:p["timesStart"] + n]
            dep = self.ds["PDEP"][p["timesStart"]:p["timesStart"] + n]
            self.assertTrue((arr[1:] >= dep[:-1]).all(), "offsets never go back in time")
            self.assertTrue((dep >= arr).all())
        self.assertLess(int(self.ds["PSTO"].max()), len(self.ds["SREC"]))
        self.assertEqual(int(self.ds["SPOF"][-1]), len(self.ds["SPRT"]))
        self.assertEqual(int(self.ds["FPOF"][-1]), len(self.ds["FPTO"]))
        self.assertLessEqual(int(self.ds["FPDU"].max()), math.ceil(400 * 1.25 / 1.3))

    def test_rail_between_tel_aviv_and_jerusalem(self):
        """A train calls at Tel Aviv Savidor Center and later at Jerusalem Yitzhak Navon."""
        # Station codes: 17038 "תל אביב מרכז" (Savidor Center), 17118 "ירושלים/יצחק נבון".
        codes = self.ds["SREC"]["code"]
        savidor = {i for i in range(len(codes)) if codes[i] == 17038 and self.ds["SREC"][i]["flags"] & 4}
        navon = {i for i in range(len(codes)) if codes[i] == 17118 and self.ds["SREC"][i]["flags"] & 4}
        self.assertTrue(savidor and navon)
        found = False
        for p in self.ds["PROF"]:
            pat = self.ds["PATT"][p["pattern"]]
            seq = list(self.ds["PSTO"][pat["firstSlot"]:pat["firstSlot"] + pat["count"]])
            a = next((k for k, s in enumerate(seq) if s in savidor), None)
            b = next((k for k, s in enumerate(seq) if s in navon), None)
            if a is not None and b is not None and a < b:
                found = True
                break
        self.assertTrue(found)

    @unittest.skipUnless(GTFS, "set ZUZ_GTFS to check fares against the feed")
    def test_fares_match_feed(self):
        with open(Path(GTFS) / "fare_attributes.txt", encoding="utf-8-sig", newline="") as f:
            prices = {int(r["fare_id"]): round(float(r["price"]) * 100) for r in csv.DictReader(f)}
        table = {int(r["fareId"]): int(r["agorot"]) for r in self.ds["FARE"]}
        self.assertEqual(table, prices)

        # The bands reproduce the feed's zone-to-zone rules from straight-line distance.
        rec, lat, lon = self.ds["SREC"], self.ds["SLAT"], self.ds["SLON"]
        by_code = {int(r["code"]): i for i, r in enumerate(rec)}
        bands = sorted((int(r["maxMeters"]), int(r["fareId"])) for r in self.ds["FARE"] if r["kind"] == 0)
        rng = random.Random(7)
        checked = agree = 0
        with open(Path(GTFS) / "fare_rules.txt", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row["route_id"] or not row["fare_id"].startswith("20") or rng.random() > 0.02:
                    continue
                a, b = by_code.get(int(row["origin_id"])), by_code.get(int(row["destination_id"]))
                if a is None or b is None:
                    continue
                d = haversine(lat[a] / 1e6, lon[a] / 1e6, lat[b] / 1e6, lon[b] / 1e6)
                fare = next(fid for limit, fid in bands if d <= limit)
                checked += 1
                agree += fare == int(row["fare_id"])
        self.assertGreater(checked, 5_000)
        # Zones are represented by one stop each; stops near a band edge can fall either side.
        self.assertGreater(agree / checked, 0.97, f"{agree}/{checked}")


def haversine(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


if __name__ == "__main__":
    unittest.main()
