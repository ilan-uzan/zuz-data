#!/usr/bin/env python3
"""Adds release download URLs and the feed checksum to a built manifest.json.

    python3 -I scripts/finalize_manifest.py <manifest.json> <owner/repo> <feed.zip>

Each file gets an absolute, tag-pinned `url`
(https://github.com/<owner/repo>/releases/download/<version>/<file>), so a client that fetched
the manifest from `releases/latest` always downloads the files of that same release.
Prints the version (the release tag).
"""

import hashlib
import json
import re
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finalize(manifest: dict, repo: str, feed_sha256: str) -> dict:
    version = manifest["version"]
    if not re.fullmatch(r"\d{8}-\d{12}", version):
        raise ValueError(f"unexpected version {version!r}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError(f"unexpected repository {repo!r}")
    base = f"https://github.com/{repo}/releases/download/{version}/"
    for entry in manifest["files"].values():
        entry["url"] = base + entry["file"]
    manifest["feedSha256"] = feed_sha256
    return manifest


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    path, repo, feed = Path(argv[0]), argv[1], Path(argv[2])
    manifest = finalize(json.loads(path.read_text(encoding="utf-8")), repo, sha256_file(feed))
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(manifest["version"])


if __name__ == "__main__":
    main(sys.argv[1:])
