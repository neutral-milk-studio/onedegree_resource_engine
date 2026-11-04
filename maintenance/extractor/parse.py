"""Text helpers shared by the extractors and the comparator.

- :func:`html_to_text` turns a page into readable lines (one per block element).
- :func:`normalize` is the canonical form used to check that a quote really appears on
  the page: whitespace collapsed, dashes unified, case kept.
- Day/time/phone parsers turn free text like "Monday – Friday: 8:30 AM – 5:00 PM" into
  comparable values.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

DAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_DAY_NAMES = {
    "monday": "MO", "mon": "MO", "mondays": "MO",
    "tuesday": "TU", "tue": "TU", "tues": "TU", "tuesdays": "TU",
    "wednesday": "WE", "wed": "WE", "wednesdays": "WE",
    "thursday": "TH", "thu": "TH", "thur": "TH", "thurs": "TH", "thursdays": "TH",
    "friday": "FR", "fri": "FR", "fridays": "FR",
    "saturday": "SA", "sat": "SA", "saturdays": "SA",
    "sunday": "SU", "sun": "SU", "sundays": "SU",
}  # fmt: skip
_BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "tr", "div", "br", "title"}
_SKIP = {"script", "style", "noscript", "template"}
_DASHES = re.compile(r"[‐-―−]")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", _DASHES.sub("-", text or "")).strip()


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[tuple[str, str]] = []  # (tag, text)
        self._buf: list[str] = []
        self._tag = "p"
        self._skip = 0

    def _flush(self) -> None:
        # Collapse whitespace only: quotes must stay verbatim (dashes etc. kept as on the page).
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if text:
            self.lines.append((self._tag, text))
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self._flush()
            self._tag = tag

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def html_lines(html: str) -> list[tuple[str, str]]:
    parser = _TextParser()
    parser.feed(html or "")
    parser.close()
    return parser.lines


def html_to_text(html: str) -> str:
    """Readable page text, one block per line (what the model sees and quotes from)."""
    return "\n".join(text for _, text in html_lines(html))


def sections(html: str) -> dict[str, list[str]]:
    """Heading text (lower case) -> the lines under it, for h2-h4/dt headings."""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for tag, text in html_lines(html):
        if tag in {"h2", "h3", "h4", "dt"}:
            current = text.lower().rstrip(":")
            out.setdefault(current, [])
        elif current is not None:
            out[current].append(text)
    return out


def quote_on_page(quote: str, page_text: str) -> bool:
    return bool(quote and quote.strip()) and normalize(quote) in normalize(page_text)


# --- phones -------------------------------------------------------------------------------

_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})")


def phone_digits(text: str) -> str | None:
    match = _PHONE.search(text or "")
    return "".join(match.groups()) if match else None


def find_phone(text: str) -> str | None:
    """The first phone number in ``text``, formatted (650) 555-0100."""
    digits = phone_digits(text)
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}" if digits else None


# --- days and times -----------------------------------------------------------------------

_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?|(noon|midnight)", re.I)
_RANGE = re.compile(
    r"(?P<a>(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)|noon)\s*(?:-|to|until)\s*"
    r"(?P<b>(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)|noon|midnight)",
    re.I,
)


def parse_time(text: str, default_meridiem: str | None = None) -> str | None:
    match = _TIME.fullmatch(text.strip())
    if not match:
        return None
    if match.group(4):
        return "12:00" if match.group(4).lower() == "noon" else "00:00"
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    meridiem = (match.group(3) or default_meridiem or "").lower().replace(".", "")
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_days(text: str) -> list[str]:
    """'Monday - Friday', 'Mon, Wed and Fri', 'Fridays', 'Daily' -> day codes in order."""
    lowered = normalize(text).lower()
    if re.search(r"\b(daily|every day|7 days)\b", lowered):
        return list(DAY_CODES)
    if re.search(r"\bweekdays\b", lowered):
        return DAY_CODES[:5]
    days: list[str] = []
    for match in re.finditer(r"([a-z]+)\s*(?:-|to|through|thru)\s*([a-z]+)", lowered):
        a, b = _DAY_NAMES.get(match.group(1)), _DAY_NAMES.get(match.group(2))
        if a and b:
            i, j = DAY_CODES.index(a), DAY_CODES.index(b)
            days += DAY_CODES[i : j + 1] if i <= j else DAY_CODES[i:] + DAY_CODES[: j + 1]
    for word in re.findall(r"[a-z]+", lowered):
        code = _DAY_NAMES.get(word)
        if code and code not in days:
            days.append(code)
    return sorted(set(days), key=DAY_CODES.index)


def parse_hours_line(line: str) -> list[tuple[str, str, str]]:
    """'Monday - Friday: 8:30 AM - 5:00 PM' -> [('MO','08:30','17:00'), ...]."""
    text = normalize(line)
    match = _RANGE.search(text)
    if not match:
        return []
    days = parse_days(text[: match.start()])
    end_mer = re.search(r"(a\.?m\.?|p\.?m\.?)", match.group("b"), re.I)
    opens = parse_time(match.group("a"), end_mer.group(1) if end_mer else None)
    closes = parse_time(match.group("b"))
    if not (days and opens and closes):
        return []
    if closes <= opens and not re.search(r"(a\.?m|p\.?m|noon)", match.group("a"), re.I):
        opens = parse_time(match.group("a"), "am")  # "9 - 5pm" means 9am
    return [(day, opens, closes) for day in days]


def split_list(text: str) -> list[str]:
    """'English, Spanish and Cantonese' -> ['English', 'Spanish', 'Cantonese']."""
    parts = re.split(r",|;|\band\b|&|/", normalize(text).rstrip("."), flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def area_name(text: str) -> str:
    """'Santa Clara County' / 'Santa Clara Counties' -> 'Santa Clara'."""
    cleaned = re.sub(r"\b(county|counties|residents?|city of)\b", "", text, flags=re.I)
    return normalize(cleaned).strip(" .")
