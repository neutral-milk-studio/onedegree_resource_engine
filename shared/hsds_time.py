"""Convert local opening hours to the HSDS time format."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo


def to_hsds_time(local_time: str, tz_name: str, on: date) -> str:
    """Return ``HH:MM`` plus the UTC offset in effect in ``tz_name`` on ``on``.

    >>> to_hsds_time("08:30", "America/Los_Angeles", date(2026, 7, 1))
    '08:30-07:00'
    """
    moment = datetime.combine(on, time.fromisoformat(local_time), ZoneInfo(tz_name))
    offset = moment.strftime("%z")
    return f"{moment:%H:%M}{offset[:3]}:{offset[3:5]}"
