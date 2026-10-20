"""Tests for the release scripts (always run; no dataset needed)."""

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import finalize_manifest  # noqa: E402
import prune_releases  # noqa: E402


class FinalizeManifestTests(unittest.TestCase):
    def manifest(self, version="20261015-202610152041"):
        return {"version": version, "files": {"timetable": {"file": "zuz-tt.bin.xz"},
                                              "shapes": {"file": "zuz-shapes.bin.xz"}}}

    def test_urls_are_pinned_to_the_release_tag(self):
        m = finalize_manifest.finalize(self.manifest(), "ilan-uzan/zuz-data", "ab" * 32)
        self.assertEqual(m["files"]["timetable"]["url"],
                         "https://github.com/ilan-uzan/zuz-data/releases/download/20261015-202610152041/zuz-tt.bin.xz")
        self.assertEqual(m["files"]["shapes"]["url"],
                         "https://github.com/ilan-uzan/zuz-data/releases/download/20261015-202610152041/zuz-shapes.bin.xz")
        self.assertEqual(m["feedSha256"], "ab" * 32)

    def test_rejects_odd_versions_and_repos(self):
        with self.assertRaises(ValueError):
            finalize_manifest.finalize(self.manifest("../latest"), "ilan-uzan/zuz-data", "")
        with self.assertRaises(ValueError):
            finalize_manifest.finalize(self.manifest(), "ilan-uzan/zuz-data/../x", "")


class PruneTests(unittest.TestCase):
    now = dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc)

    def release(self, tag, days_ago, draft=False):
        created = (self.now - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
        return {"tagName": tag, "createdAt": created, "isDraft": draft}

    def test_deletes_releases_older_than_a_week(self):
        releases = [self.release(f"r{d}", d) for d in range(12)]
        self.assertEqual(sorted(prune_releases.to_delete(releases, self.now)), ["r10", "r11", "r8", "r9"])

    def test_always_keeps_the_newest_three(self):
        releases = [self.release("a", 20), self.release("b", 30), self.release("c", 40), self.release("d", 50)]
        self.assertEqual(prune_releases.to_delete(releases, self.now), ["d"])

    def test_stale_drafts_go_and_fresh_drafts_stay(self):
        releases = [self.release("live", 0), self.release("stale", 2, draft=True), self.release("running", 0, True)]
        self.assertEqual(prune_releases.to_delete(releases, self.now), ["stale"])


if __name__ == "__main__":
    unittest.main()
