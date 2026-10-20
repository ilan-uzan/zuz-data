"""Search normalisation shared by the pipeline and the app.

The Swift side (`SearchNormalizer` in zuz/Services/Transit/StopSearch.swift) must produce exactly
the same tokens, so the rules are deliberately explicit character maps rather than locale APIs.
Keep the two in sync; `pipeline/tests` and `ZuzTests/StopSearchTests` pin the same examples.
"""

# Hebrew final letters fold to their regular forms so "שלו" matches "שלום" as a prefix.
_FOLD = {
    "ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ",
    # Arabic letter variants riders type interchangeably.
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي",
}

# Characters dropped entirely (they join words rather than split them): apostrophes, geresh and
# gershayim, so "בי''ס", "בי״ס" and "ביס" all become "ביס", and "ג'ורג'" becomes "גורג".
_DROP = set("'’׳״\"`´")


def _is_mark(ch: str) -> bool:
    code = ord(ch)
    return (
        0x0591 <= code <= 0x05C7 and ch not in "־׀׃׆"  # Hebrew points and cantillation
        or 0x064B <= code <= 0x065F  # Arabic harakat
        or code in (0x0670, 0x0640)  # superscript alef, tatweel
        or 0x0300 <= code <= 0x036F  # Latin combining marks
    )


def normalize(text: str) -> str:
    """Lowercase, strip diacritics, fold letter variants; separators become single spaces."""
    out = []
    for ch in text:
        if ch in _DROP or _is_mark(ch):
            continue
        ch = _FOLD.get(ch, ch)
        if ch.isalnum():
            out.append(ch.lower() if "A" <= ch <= "Z" or "a" <= ch <= "z" else ch)
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def tokens(text: str):
    return normalize(text).split(" ") if text else []
