"""Is a place open at the time the user wants to eat?

Decided here, in code, not by the LLM: it is arithmetic over weekly
opening hours, and arithmetic should be deterministic and testable. See
docs/DECISIONS.md § Open-at-that-time is decided in code, not by the LLM.

Computed on every request from regularOpeningHours, never read from
Google's openNow. openNow is evaluated at fetch time and then served from
the 24-hour cache, so it can say "open" for hours after closing.

Every answer is True, False, or None. None means "cannot tell" - no hours,
no time zone, or no time given - and is never treated as closed.
"""

from datetime import datetime, timedelta, timezone

MINUTES_PER_DAY = 24 * 60
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY

# extract.DAYS weekday names, Monday first, matching datetime.weekday().
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _now_utc() -> datetime:
    """A separate function so tests can stop the clock."""
    return datetime.now(timezone.utc)


def _google_day(moment: datetime) -> int:
    """Google numbers days Sunday = 0; Python numbers them Monday = 0."""
    return (moment.weekday() + 1) % 7


def target_time(
    when: str, day: str | None, time: str | None, utc_offset_minutes: int | None
) -> tuple[int, int] | None:
    """When the user wants to eat, in the place's own local time.

    Returns (Google day number, minute of the day), or None if it cannot
    be known. "Now" is the place's now, not the user's: someone in LA
    asking for Boston at 9pm their time is asking about midnight in Boston.
    A weekday name means its next occurrence, today included.
    """
    if utc_offset_minutes is None:
        return None

    local_now = _now_utc() + timedelta(minutes=utc_offset_minutes)

    if when == "now":
        return _google_day(local_now), local_now.hour * 60 + local_now.minute

    if when != "later" or time is None:
        return None

    if day in (None, "today"):
        ahead = 0
    elif day == "tomorrow":
        ahead = 1
    elif day in _WEEKDAYS:
        ahead = (_WEEKDAYS.index(day) - local_now.weekday()) % 7
    else:
        return None

    hour, minute = (int(part) for part in time.split(":"))
    return _google_day(local_now + timedelta(days=ahead)), hour * 60 + minute


def _minute_of_week(point: dict) -> int:
    return point["day"] * MINUTES_PER_DAY + point.get("hour", 0) * 60 + point.get("minute", 0)


def is_open(periods: list[dict] | None, day: int, minute: int) -> bool | None:
    """Whether weekly opening periods cover the given moment.

    Handles the two shapes that trip naive code: a period that runs past
    midnight (close is on the next day), and one that runs past Saturday
    night into Sunday (close day number is smaller than open). A single
    period with no close is Google's way of saying open 24 hours.
    """
    if not periods:
        return None

    moment = day * MINUTES_PER_DAY + minute
    understood_any = False

    for period in periods:
        try:
            opens = _minute_of_week(period["open"])
            if "close" not in period:
                return True
            closes = _minute_of_week(period["close"])
        except (KeyError, TypeError):
            continue
        understood_any = True

        if closes <= opens:
            closes += MINUTES_PER_WEEK
        # The moment may sit in the wrapped part of a period that started
        # late in the previous week, so it is tried in both weeks.
        if opens <= moment < closes or opens <= moment + MINUTES_PER_WEEK < closes:
            return True

    return False if understood_any else None


def annotate(places: list[dict], when: str, day: str | None, time: str | None) -> None:
    """Set place["open_then"] on every place, in place."""
    for place in places:
        target = target_time(when, day, time, place["utc_offset_minutes"])
        place["open_then"] = None if target is None else is_open(place["periods"], *target)


def describe_when(when: str, day: str | None, time: str | None) -> str:
    """The time being checked, as the reason sentence names it."""
    if when == "now":
        return "right now"
    if time is None:
        return "then"
    return f"{day or 'today'} at {time}"
