from datetime import datetime, timezone, timedelta
from typing import Optional


def now_utc() -> datetime:
    """Returns current UTC datetime with timezone awareness."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensures a datetime object is timezone-aware in UTC.
    
    Particularly useful when running against SQLite or serialization layers
    that drop tzinfo.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_display_tz(dt: datetime, offset_hours: int = 3) -> datetime:
    """Converts a UTC datetime to display timezone (default: East Africa Time UTC+3)."""
    tz = timezone(timedelta(hours=offset_hours))
    return dt.astimezone(tz)


def format_time_12h(dt: datetime) -> str:
    """Formats time as 12-hour AM/PM without leading zero on hour (e.g. '2:00 PM')."""
    formatted = dt.strftime("%I:%M %p")
    if formatted.startswith("0"):
        formatted = formatted[1:]
    return formatted


def format_friendly_datetime(
    dt: Optional[datetime],
    now: Optional[datetime] = None,
    offset_hours: int = 3,
) -> str:
    """Formats datetime relative to now (e.g. 'Today, 2:00 PM', 'Tomorrow, 2:00 PM', 'Oct 5, 2:00 PM')."""
    if not dt:
        return "TBA"

    dt_utc = ensure_utc(dt)
    now_utc = ensure_utc(now) if now else datetime.now(timezone.utc)

    dt_local = to_display_tz(dt_utc, offset_hours)
    now_local = to_display_tz(now_utc, offset_hours)

    time_str = format_time_12h(dt_local)
    days_diff = (dt_local.date() - now_local.date()).days

    if days_diff == 0:
        day_str = "Today"
    elif days_diff == 1:
        day_str = "Tomorrow"
    elif days_diff == -1:
        day_str = "Yesterday"
    elif dt_local.year == now_local.year:
        day_str = f"{dt_local.strftime('%b')} {dt_local.day}"
    else:
        day_str = f"{dt_local.strftime('%b')} {dt_local.day}, {dt_local.year}"

    return f"{day_str}, {time_str}"


def format_schedule_window(
    opens_at: Optional[datetime],
    closes_at: Optional[datetime],
    now: Optional[datetime] = None,
    offset_hours: int = 3,
) -> str:
    """Formats opens_at and closes_at into clean schedule window.
    
    Examples:
    - Same day: 'Today, 2:00 PM → 4:00 PM'
    - Multi-day: 'Today, 2:00 PM → Tomorrow, 2:00 PM'
    """
    if not opens_at and not closes_at:
        return "Schedule TBA"
    if opens_at and not closes_at:
        return f"Opens {format_friendly_datetime(opens_at, now, offset_hours)}"
    if not opens_at and closes_at:
        return f"Closes {format_friendly_datetime(closes_at, now, offset_hours)}"

    dt_opens = ensure_utc(opens_at)
    dt_closes = ensure_utc(closes_at)
    dt_now = ensure_utc(now) if now else datetime.now(timezone.utc)

    local_opens = to_display_tz(dt_opens, offset_hours)
    local_closes = to_display_tz(dt_closes, offset_hours)

    opens_str = format_friendly_datetime(dt_opens, dt_now, offset_hours)
    if local_opens.date() == local_closes.date():
        closes_time = format_time_12h(local_closes)
        return f"{opens_str} → {closes_time}"

    closes_str = format_friendly_datetime(dt_closes, dt_now, offset_hours)
    return f"{opens_str} → {closes_str}"


def format_friendly_duration(minutes: int) -> str:
    """Formats minutes into human-friendly duration (e.g. '2 hours', '30 minutes', '1 hr 30 mins')."""
    if not minutes or minutes <= 0:
        return "Untimed"
    if minutes < 60:
        return f"{minutes} minutes"
    hours, rem = divmod(minutes, 60)
    if rem == 0:
        unit = "hour" if hours == 1 else "hours"
        return f"{hours} {unit}"
    h_unit = "hr" if hours == 1 else "hrs"
    return f"{hours} {h_unit} {rem} mins"


def format_friendly_questions(count: int) -> str:
    """Formats question count (e.g. '20 questions', '1 question')."""
    unit = "question" if count == 1 else "questions"
    return f"{count} {unit}"


def format_meta_line(duration_minutes: int, question_count: int) -> str:
    """Combines duration and questions with interpunct dot: '2 hours · 20 questions'."""
    dur = format_friendly_duration(duration_minutes)
    q = format_friendly_questions(question_count)
    return f"{dur} · {q}"


def parse_window_string(raw: str) -> Optional[timedelta]:
    """Parses human-entered window string into a timedelta.
    
    Accepts:
    - Pure number: e.g. '6' -> 6 hours
    - Hours: '2h', '4 hours', '12 hrs'
    - Days: '2d', '3 days', '5 day'
    - Weeks: '1w', '2 weeks'
    - Minutes: '30m', '45 mins'
    - Mixed: '1d 12h', '2 days 4 hours'
    """
    import re

    raw = raw.strip().lower()
    if not raw:
        return None

    if raw.isdigit():
        val = int(raw)
        if 1 <= val <= 720:  # 1 hour to 30 days
            return timedelta(hours=val)
        return None

    pattern = (
        r"^(?:(\d+)\s*(?:w|week|weeks))?\s*"
        r"(?:(\d+)\s*(?:d|day|days))?\s*"
        r"(?:(\d+)\s*(?:h|hr|hrs|hour|hours))?\s*"
        r"(?:(\d+)\s*(?:m|min|mins|minute|minutes))?$"
    )
    match = re.match(pattern, raw)
    if match and any(match.groups()):
        w, d, h, m = match.groups()
        td = timedelta(
            weeks=int(w or 0),
            days=int(d or 0),
            hours=int(h or 0),
            minutes=int(m or 0),
        )
        # Minimum 5 minutes, maximum 365 days
        if 300 <= td.total_seconds() <= 86400 * 365:
            return td
    return None


def format_timedelta_friendly(td: timedelta) -> str:
    """Formats a timedelta into a concise human-friendly string (e.g. '4 hours', '2 days', '1d 12h')."""
    total_secs = int(td.total_seconds())
    hours, rem = divmod(total_secs, 3600)
    days, hours = divmod(hours, 24)
    if days > 0 and hours > 0:
        return f"{days}d {hours}h"
    elif days > 0:
        unit = "day" if days == 1 else "days"
        return f"{days} {unit}"
    elif hours > 0:
        unit = "hour" if hours == 1 else "hours"
        return f"{hours} {unit}"
    else:
        mins = max(1, rem // 60)
        unit = "minute" if mins == 1 else "minutes"
        return f"{mins} {unit}"
