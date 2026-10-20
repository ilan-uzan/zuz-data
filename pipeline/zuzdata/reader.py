"""Read-only access to a built container, for tests and debugging. Mirrors the Swift reader."""

import lzma
from pathlib import Path

import numpy as np

from . import container

DTYPES = {
    "STRO": "<u4", "STRB": "u1",
    "AGEN": [("gtfsId", "<u4"), ("nameHe", "<u4"), ("nameEn", "<u4"), ("nameAr", "<u4")],
    "SLAT": "<i4", "SLON": "<i4",
    "SREC": [("gtfsId", "<u4"), ("code", "<u4"), ("nameHe", "<u4"), ("nameEn", "<u4"), ("nameAr", "<u4"),
             ("cityHe", "<u4"), ("cityEn", "<u4"), ("cityAr", "<u4"), ("parent", "<u4"), ("flags", "<u4")],
    "RREC": [("gtfsId", "<u4"), ("agency", "<u2"), ("mode", "u1"), ("flags", "u1"), ("color", "<u4"),
             ("shortName", "<u4"), ("longName", "<u4"), ("desc", "<u4")],
    "HSGN": [("he", "<u4"), ("en", "<u4"), ("ar", "<u4")],
    "PATT": [("firstSlot", "<u4"), ("count", "<u4")],
    "PSTO": "<u4", "PFLG": "u1",
    "PROF": [("pattern", "<u4"), ("route", "<u4"), ("headsign", "<u4"), ("timesStart", "<u4"),
             ("tripStart", "<u4"), ("tripCount", "<u4"), ("reserved0", "<u4"), ("reserved1", "<u4")],
    "PARR": "<u2", "PDEP": "<u2",
    "TSTA": "<u4", "TSVC": "<u2", "TFLG": "u1", "TGID": "<u4",
    "SVCD": "<u8",
    "SPOF": "<u4", "SPRT": [("profile", "<u4"), ("position", "<u4")],
    "RPOF": "<u4", "RPRO": "<u4",
    "FPOF": "<u4", "FPTO": "<u4", "FPDU": "<u2",
    "GRDH": [("minLat", "<i4"), ("minLon", "<i4"), ("cellLat", "<i4"), ("cellLon", "<i4"), ("rows", "<u4"), ("cols", "<u4")],
    "GROF": "<u4", "GRST": "<u4",
    "FARE": [("fareId", "<u4"), ("agorot", "<u4"), ("maxMeters", "<u4"), ("kind", "<u4")],
    "RAIL": [("origin", "<u4"), ("destination", "<u4"), ("fare", "<u4")],
    "STKN": "<u4", "STOF": "<u4", "STPO": "<u4",
    "LNKN": "<u4", "LNOF": "<u4", "LNPO": "<u4",
    "SCOD": "<u4",
    # shapes file
    "SHOF": "<u4", "SHPT": "<i4",
    "SHBX": [("minLat", "<i4"), ("minLon", "<i4"), ("maxLat", "<i4"), ("maxLon", "<i4")],
    "PSHP": "<u4", "PRSH": "<u4", "RSHP": "<u4",
}


class Dataset:
    def __init__(self, path):
        path = Path(path)
        data = path.read_bytes()
        if path.suffix == ".xz":
            data = lzma.decompress(data)
        self.data = data
        self.header, self.sections = container.read(data)
        self._cache = {}

    def __getitem__(self, tag):
        if tag not in self._cache:
            self._cache[tag] = container.view(self.data, self.sections, tag, DTYPES[tag])
        return self._cache[tag]

    def string(self, sid):
        off = self["STRO"]
        return bytes(self["STRB"][off[sid]:off[sid + 1]]).decode("utf-8")

    def active(self, service, day):
        return bool((int(self["SVCD"][service]) >> day) & 1)

    def trips_on_day(self, day):
        bits = (self["SVCD"] >> np.uint64(day)) & np.uint64(1)
        return int(bits[self["TSVC"]].sum())
