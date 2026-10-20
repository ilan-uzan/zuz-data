"""The `ZUZT` container: a fixed header, a section table and 8-byte-aligned little-endian arrays.

Layout (all integers little-endian):

    0   magic        4 bytes  b"ZUZT"
    4   schema       u32      SCHEMA_VERSION
    8   kind         u32      0 = timetable, 1 = shapes
    12  sectionCount u32
    16  startDate    u32      first service day, YYYYMMDD
    20  dayCount     u32      days covered by the service bitsets
    24  createdAt    i64      Unix seconds when the file was built
    32  feedVersion  u32      YYYYMMDD of the GTFS build
    36  reserved     28 bytes zero
    64  sections     sectionCount x 24 bytes: tag (4 ASCII), elementSize u32, offset u64, count u64

Every section starts on an 8-byte boundary, so the app can bind typed pointers into the mmap.
"""

import struct

import numpy as np

HEADER_SIZE = 64
ENTRY_SIZE = 24
MAGIC = b"ZUZT"


class Container:
    def __init__(self, kind: int, schema: int, start_date: int, day_count: int, created_at: int, feed_version: int):
        self.kind = kind
        self.schema = schema
        self.start_date = start_date
        self.day_count = day_count
        self.created_at = created_at
        self.feed_version = feed_version
        self.sections = []  # (tag, element size, bytes, count)

    def add(self, tag: str, array) -> None:
        """Adds a numpy array (any fixed-width dtype, or a structured record dtype) as a section."""
        assert len(tag) == 4, tag
        assert tag not in {s[0] for s in self.sections}, f"duplicate section {tag}"
        arr = np.ascontiguousarray(array)
        if arr.dtype.byteorder == ">":
            raise ValueError(f"{tag}: big-endian dtype")
        size = arr.dtype.itemsize
        self.sections.append((tag, size, arr.tobytes(), len(arr)))

    def tobytes(self) -> bytes:
        table_end = HEADER_SIZE + ENTRY_SIZE * len(self.sections)
        offset = (table_end + 7) & ~7
        entries, blobs = [], []
        cursor = offset
        for tag, size, blob, count in self.sections:
            entries.append(struct.pack("<4sIQQ", tag.encode("ascii"), size, cursor, count))
            pad = (-len(blob)) % 8
            blobs.append(blob + b"\0" * pad)
            cursor += len(blob) + pad
        header = struct.pack("<4sIIIIIqI", MAGIC, self.schema, self.kind, len(self.sections),
                             self.start_date, self.day_count, self.created_at, self.feed_version)
        header += b"\0" * (HEADER_SIZE - len(header))
        body = header + b"".join(entries)
        body += b"\0" * (offset - len(body))
        return body + b"".join(blobs)


def read(data: bytes):
    """Parses a container back into a header dict and {tag: numpy view}; used by tests and tools."""
    magic, schema, kind, count, start, days, created, feed = struct.unpack_from("<4sIIIIIqI", data, 0)
    if magic != MAGIC:
        raise ValueError("not a ZUZT file")
    sections = {}
    for i in range(count):
        tag, size, off, n = struct.unpack_from("<4sIQQ", data, HEADER_SIZE + i * ENTRY_SIZE)
        sections[tag.decode("ascii")] = (size, off, n)
    header = dict(schema=schema, kind=kind, start_date=start, day_count=days, created_at=created, feed_version=feed)
    return header, sections


def view(data: bytes, sections, tag: str, dtype):
    size, off, n = sections[tag]
    dt = np.dtype(dtype)
    assert dt.itemsize == size, f"{tag}: element size {size} != {dt.itemsize}"
    return np.frombuffer(data, dtype=dt, count=n, offset=off)


class StringPool:
    """Interned UTF-8 strings. Id 0 is always the empty string."""

    def __init__(self):
        self.ids = {"": 0}
        self.items = [""]

    def add(self, s: str) -> int:
        s = s or ""
        found = self.ids.get(s)
        if found is None:
            found = len(self.items)
            self.ids[s] = found
            self.items.append(s)
        return found

    def arrays(self):
        encoded = [s.encode("utf-8") for s in self.items]
        offsets = np.zeros(len(encoded) + 1, dtype="<u4")
        np.cumsum([len(b) for b in encoded], out=offsets[1:])
        return offsets, np.frombuffer(b"".join(encoded), dtype="u1")
