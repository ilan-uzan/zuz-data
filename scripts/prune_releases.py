#!/usr/bin/env python3
"""Chooses which releases to delete.

    gh release list --limit 200 --json tagName,createdAt,isDraft | python3 -I scripts/prune_releases.py [days] [keep]

Reads `gh release list` JSON on stdin and prints one tag per line to delete:
published releases older than `days` (default 7), always keeping the newest `keep` (default 3),
plus drafts older than one day (left behind by a failed run).
"""

import datetime as dt
import json
import sys


def to_delete(releases, now, days=7, keep=3):
    def created(r):
        return dt.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))

    published = sorted((r for r in releases if not r.get("isDraft")), key=created, reverse=True)
    drafts = [r for r in releases if r.get("isDraft")]
    old = now - dt.timedelta(days=days)
    tags = [r["tagName"] for r in published[keep:] if created(r) < old]
    tags += [r["tagName"] for r in drafts if created(r) < now - dt.timedelta(days=1)]
    return tags


def main(argv):
    days = int(argv[0]) if argv else 7
    keep = int(argv[1]) if len(argv) > 1 else 3
    releases = json.load(sys.stdin)
    for tag in to_delete(releases, dt.datetime.now(dt.timezone.utc), days, keep):
        print(tag)


if __name__ == "__main__":
    main(sys.argv[1:])
