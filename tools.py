"""
Google Calendar tools.

This module exposes an async function `check_gcal_availability` that queries the
Google Calendar FreeBusy API to summarize a user's availability for a given day
or an interval of days. It returns a short, human-readable text summary intended
for direct use by an LLM response.

Notes
- This implementation uses plain HTTP with the provided OAuth `access_token` and
  does not require additional Google SDKs. Ensure your runtime has network access
  and a valid OAuth 2.0 access token for the user with `https://www.googleapis.com/auth/calendar.readonly`
  (or broader) scope.
- Keep tokens secure; never log them.

Example
    text = await check_gcal_availability(
        access_token=os.environ["GOOGLE_OAUTH_ACCESS_TOKEN"],
        date="2025-09-20",
        calendar_ids=["primary"],
        timezone="Europe/Istanbul",
        work_start="09:00",
        work_end="18:00",
    )
    # -> "Sep 20 (Sat): Busy 10:00–11:30. Free 09:00–10:00, 11:30–18:00."
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone as dt_timezone
from typing import Iterable, List, Optional, Sequence, Tuple

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover - very old Pythons
    ZoneInfo = None  # type: ignore


GCAL_FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"


@dataclass
class Interval:
    start: datetime
    end: datetime

    def clip(self, start: datetime, end: datetime) -> Optional["Interval"]:
        s = max(self.start, start)
        e = min(self.end, end)
        if s >= e:
            return None
        return Interval(s, e)


def _ensure_tz(tz_name: Optional[str]) -> dt_timezone:
    if tz_name and ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)  # type: ignore[arg-type]
        except Exception:
            pass
    return dt_timezone.utc


def _parse_date(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


def _parse_hhmm(s: Optional[str]) -> Optional[time]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%H:%M").time()
    except Exception:
        return None


def _to_rfc3339(dt: datetime) -> str:
    # Ensure aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt.isoformat()


def _http_post_json(url: str, headers: dict, payload: dict, timeout: int = 20) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        **headers,
    })
    # Use default SSL context; rely on system certs
    context = ssl.create_default_context()
    with urllib.request.urlopen(req, context=context, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body.decode("utf-8"))


def _merge_intervals(intervals: Sequence[Interval]) -> List[Interval]:
    if not intervals:
        return []
    sorted_ints = sorted(intervals, key=lambda x: x.start)
    merged: List[Interval] = []
    cur = sorted_ints[0]
    for iv in sorted_ints[1:]:
        if iv.start <= cur.end:
            cur = Interval(cur.start, max(cur.end, iv.end))
        else:
            merged.append(cur)
            cur = iv
    merged.append(cur)
    return merged


def _format_time(dt_obj: datetime) -> str:
    return dt_obj.strftime("%H:%M")


def _format_day(dt_obj: date) -> str:
    return dt_obj.strftime("%b %d (%a)")


def _day_span(d: date, tz: dt_timezone) -> Tuple[datetime, datetime]:
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def _iterate_days(start_date: date, end_date: date) -> Iterable[date]:
    cur = start_date
    while cur <= end_date:
        yield cur
        cur = cur + timedelta(days=1)


async def check_gcal_availability(
    *,
    access_token: str,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    calendar_ids: Optional[Sequence[str]] = None,
    timezone: str = "UTC",
    work_start: Optional[str] = None,
    work_end: Optional[str] = None,
) -> str:
    """Check Google Calendar availability and return a readable summary.

    Parameters
    - access_token: OAuth 2.0 bearer token authorized for Calendar read.
    - date: Single day in YYYY-MM-DD. Mutually exclusive with start_date/end_date.
    - start_date, end_date: Range in YYYY-MM-DD (inclusive). If only start_date
      is provided, the range is that single day. If only `date` is provided, a
      single-day range is used.
    - calendar_ids: Sequence of calendar IDs (e.g., ["primary", "team@domain"]).
      Defaults to ["primary"].
    - timezone: IANA timezone name for interpreting the day range and formatting.
    - work_start, work_end: Optional local times (HH:MM). If provided, free slot
      windows will be constrained to this working-hours window per day.

    Returns
    - A concise human-readable text summary (one or multiple lines) describing
      busy and free windows for each day.
    """
    if not access_token:
        return "Missing Google access token. Provide a valid OAuth bearer token."

    tz = _ensure_tz(timezone)

    # Determine date range
    if date:
        sd = ed = _parse_date(date)
    else:
        if start_date and end_date:
            sd, ed = _parse_date(start_date), _parse_date(end_date)
        elif start_date and not end_date:
            sd = ed = _parse_date(start_date)
        else:
            return "Please provide either 'date' or 'start_date' (and optional 'end_date')."

    if sd > ed:
        sd, ed = ed, sd  # swap

    work_start_t = _parse_hhmm(work_start)
    work_end_t = _parse_hhmm(work_end)
    if (work_start and not work_start_t) or (work_end and not work_end_t):
        return "Invalid working hours format. Use HH:MM, e.g., 09:00 and 18:00."
    if work_start_t and work_end_t and (datetime.combine(sd, work_end_t) <= datetime.combine(sd, work_start_t)):
        return "Invalid working hours: 'work_end' must be after 'work_start'."

    # Build FreeBusy request payload
    time_min_dt = datetime(sd.year, sd.month, sd.day, 0, 0, 0, tzinfo=tz)
    time_max_dt = datetime(ed.year, ed.month, ed.day, 0, 0, 0, tzinfo=tz) + timedelta(days=1)
    payload = {
        "timeMin": _to_rfc3339(time_min_dt),
        "timeMax": _to_rfc3339(time_max_dt),
        "items": [{"id": cid} for cid in (calendar_ids or ["primary"])],
        "timeZone": timezone,
    }

    headers = {"Authorization": f"Bearer {access_token}"}

    # Perform network call in a thread to avoid blocking the event loop
    import asyncio

    try:
        resp = await asyncio.to_thread(_http_post_json, GCAL_FREEBUSY_URL, headers, payload)
    except Exception as e:
        # Hide token and return a short message to the LLM
        return f"Couldn't reach Google Calendar FreeBusy API: {type(e).__name__}: {e}"

    # Parse busy windows across calendars and merge overlaps
    calendars = (resp or {}).get("calendars", {})
    intervals: List[Interval] = []
    for cal_id, cal_data in calendars.items():
        for b in cal_data.get("busy", []) or []:
            try:
                bstart = datetime.fromisoformat(b["start"]).astimezone(tz)
                bend = datetime.fromisoformat(b["end"]).astimezone(tz)
            except Exception:
                # Fallback parsing accommodating trailing 'Z' and microseconds
                bstart = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(tz)
                bend = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(tz)
            intervals.append(Interval(bstart, bend))

    merged = _merge_intervals(intervals)

    # Construct per-day summary
    lines: List[str] = []
    for day in _iterate_days(sd, ed):
        day_start, day_end = _day_span(day, tz)
        if work_start_t:
            day_start = datetime.combine(day, work_start_t, tz)
        if work_end_t:
            day_end = datetime.combine(day, work_end_t, tz)
        if day_start >= day_end:
            # Skip nonsensical ranges within this day
            continue

        # Busy intervals that intersect this day
        day_busy: List[Interval] = []
        for iv in merged:
            clipped = iv.clip(day_start, day_end)
            if clipped:
                day_busy.append(clipped)

        # Compute free windows inside [day_start, day_end]
        free_windows: List[Tuple[datetime, datetime]] = []
        cursor = day_start
        for iv in day_busy:
            if iv.start > cursor:
                free_windows.append((cursor, iv.start))
            cursor = max(cursor, iv.end)
        if cursor < day_end:
            free_windows.append((cursor, day_end))

        # Format line
        if not day_busy and not free_windows:
            # Shouldn't happen due to logic, but safe-guard
            lines.append(f"{_format_day(day)}: No data.")
            continue

        busy_part = (
            "None"
            if not day_busy
            else ", ".join(f"{_format_time(iv.start)}–{_format_time(iv.end)}" for iv in day_busy)
        )
        if work_start_t or work_end_t:
            free_part = (
                "None"
                if not free_windows
                else ", ".join(f"{_format_time(s)}–{_format_time(e)}" for s, e in free_windows)
            )
            lines.append(f"{_format_day(day)}: Busy {busy_part}. Free {free_part}.")
        else:
            # Without working hours, stick to busy summary and implicit otherwise-free wording
            if busy_part == "None":
                lines.append(f"{_format_day(day)}: No events; free all day.")
            else:
                lines.append(f"{_format_day(day)}: Busy {busy_part}. Otherwise free.")

    if not lines:
        return "No availability information found for the requested period."
    return "\n".join(lines)


# Optional: tool schema that can be added to session tools
CHECK_GCAL_AVAILABILITY_TOOL = {
    "type": "function",
    "name": "check_gcal_availability",
    "description": (
        "Check Google Calendar busy/free windows for a single day or a date range "
        "using an OAuth access token. Returns a short, human-readable summary."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "access_token": {
                "type": "string",
                "description": "OAuth 2.0 bearer token with Calendar read scope.",
            },
            "date": {
                "type": "string",
                "description": "Single day in YYYY-MM-DD.",
            },
            "start_date": {
                "type": "string",
                "description": "Start of range in YYYY-MM-DD.",
            },
            "end_date": {
                "type": "string",
                "description": "End of range in YYYY-MM-DD (inclusive).",
            },
            "calendar_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Calendar IDs, e.g., ['primary', 'team@domain.com'].",
            },
            "timezone": {
                "type": "string",
                "description": "IANA timezone name, e.g., 'Europe/Istanbul'.",
                "default": "UTC",
            },
            "work_start": {
                "type": "string",
                "description": "Optional local start time (HH:MM) to bound free windows.",
            },
            "work_end": {
                "type": "string",
                "description": "Optional local end time (HH:MM) to bound free windows.",
            },
        },
        "required": ["access_token"],
    },
}
