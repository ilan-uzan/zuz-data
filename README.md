# zuz-data

Nightly timetable dataset for **Zuz**, an iPhone and iPad transit app for Israel.

Every night a GitHub Actions job downloads the Israel Ministry of Transport GTFS feed, turns it into a compact binary that the app memory-maps for on-device routing, tests it, and publishes it as a [release](https://github.com/ilan-uzan/zuz-data/releases).

## Using the data

The latest manifest is always at:

```
https://github.com/ilan-uzan/zuz-data/releases/latest/download/manifest.json
```

It lists each file with its size, sha256 (compressed and raw) and a download URL pinned to that release's tag. Download the files from those URLs, check the sizes and hashes, then decompress (xz).

| Asset | What it is |
|---|---|
| `zuz-tt.bin.xz` | Timetable, stops, routes, footpaths, fares and search index (~3.6 MB) |
| `zuz-shapes.bin.xz` | Simplified route shapes (~1.5 MB) |
| `manifest.json` | Version, validity window, sizes, sha256s, URLs, build stats |

The binary layout, the manifest fields and the known gaps in the source data are described in [docs/FORMAT.md](docs/FORMAT.md).

## Schedule

- [`.github/workflows/nightly.yml`](.github/workflows/nightly.yml) runs at **22:30 UTC** (about 01:30 in Israel), after the Ministry publishes the day's feed. It can also be started by hand (Actions → Nightly dataset → Run workflow).
- A release is published only when the feed has changed since the latest release. Its tag is the dataset version, `<first service day YYYYMMDD>-<build time YYYYMMDDHHMM>` (UTC).
- Releases older than 7 days are deleted, but the newest 3 are always kept.
- Pull requests build and test the dataset without publishing.

## Building locally

Python 3.9+ with numpy; about 2 minutes and 1.5 GB of RAM.

```sh
curl -fL -o /tmp/gtfs.zip https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip   # GET, never HEAD
python3 -m venv /tmp/zuz-venv && /tmp/zuz-venv/bin/pip install numpy
/tmp/zuz-venv/bin/python -I pipeline/build_dataset.py /tmp/gtfs.zip /tmp/zuz-out
mkdir -p /tmp/gtfs && unzip -o /tmp/gtfs.zip fare_attributes.txt fare_rules.txt -d /tmp/gtfs
ZUZ_DATASET=/tmp/zuz-out ZUZ_GTFS=/tmp/gtfs /tmp/zuz-venv/bin/python -I -m unittest discover -s pipeline/tests -v
```

Run Python with `-I`: the feed is untrusted data and is only parsed, never executed.

## Data source and attribution

- **Source:** Israel Ministry of Transport and Road Safety (משרד התחבורה והבטיחות בדרכים), public transport GTFS feed, `https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip`, listed on [data.gov.il](https://data.gov.il). If that server is unreachable, the job falls back to the [Mobility Database](https://mobilitydatabase.org) mirror of the same feed (`mdb-2519`).
- **Attribution:** "Timetable data: Israel Ministry of Transport." The published files are a derived, reformatted version of that feed. They are not endorsed by the Ministry, and they may contain errors or be out of date. Always check official sources before you travel.
- **Terms:** data.gov.il lists the Ministry's datasets as open data. The Ministry's own terms-of-use page for the GTFS feed could not be read when this repository was set up, so its exact wording is not reproduced here. The owner of this repository should review the Ministry of Transport's terms of use. Anyone reusing these files must follow the source's terms.
- **Route colours** that the feed does not set come from the Zuz palette ([`palette/route-colors.json`](palette/route-colors.json)).

## Licence

The code in this repository (the pipeline, scripts and workflow) is under the [MIT licence](LICENSE). The data in the releases is derived from the Israel Ministry of Transport GTFS feed and remains under the source's terms; the MIT licence does not apply to it.
