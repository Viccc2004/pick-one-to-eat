"""hours: is a place open when the user wants to eat."""

from datetime import datetime, timezone

import pytest

import hours

BOSTON = -300  # UTC-5 in November
LA = -480

# Lunch, every day 11:00-15:00; dinner Mon-Sat 17:00-22:00.
LUNCH_AND_DINNER = [{"open": {"day": d, "hour": 11}, "close": {"day": d, "hour": 15}} for d in range(7)] + [
    {"open": {"day": d, "hour": 17}, "close": {"day": d, "hour": 22}} for d in range(1, 7)
]

# Friday 18:00 to Saturday 02:00 - past midnight.
LATE_FRIDAY = [{"open": {"day": 5, "hour": 18}, "close": {"day": 6, "hour": 2}}]

# Saturday 20:00 to Sunday 04:00 - past the end of Google's week.
LATE_SATURDAY = [{"open": {"day": 6, "hour": 20}, "close": {"day": 0, "hour": 4}}]

ALWAYS = [{"open": {"day": 0, "hour": 0, "minute": 0}}]

SUNDAY, MONDAY, WEDNESDAY, FRIDAY, SATURDAY = 0, 1, 3, 5, 6


def at(h, m=0):
    return h * 60 + m


@pytest.fixture
def clock(monkeypatch):
    """Stop the clock at a given UTC moment."""

    def set_to(*args):
        moment = datetime(*args, tzinfo=timezone.utc)
        monkeypatch.setattr(hours, "_now_utc", lambda: moment)

    return set_to


# --- is_open -------------------------------------------------------------------


def test_inside_a_period_is_open():
    assert hours.is_open(LUNCH_AND_DINNER, WEDNESDAY, at(12, 30)) is True


def test_between_lunch_and_dinner_is_closed():
    assert hours.is_open(LUNCH_AND_DINNER, WEDNESDAY, at(16)) is False


def test_closing_minute_itself_is_closed():
    assert hours.is_open(LUNCH_AND_DINNER, WEDNESDAY, at(22)) is False


def test_no_dinner_on_sunday():
    assert hours.is_open(LUNCH_AND_DINNER, SUNDAY, at(19)) is False


def test_past_midnight_belongs_to_the_previous_night():
    assert hours.is_open(LATE_FRIDAY, SATURDAY, at(1)) is True
    assert hours.is_open(LATE_FRIDAY, SATURDAY, at(3)) is False


def test_saturday_night_into_sunday_morning():
    # Close day (0) is smaller than open day (6): the week wraps.
    assert hours.is_open(LATE_SATURDAY, SATURDAY, at(23)) is True
    assert hours.is_open(LATE_SATURDAY, SUNDAY, at(3)) is True
    assert hours.is_open(LATE_SATURDAY, SUNDAY, at(5)) is False


def test_a_period_without_a_close_is_open_24_hours():
    assert hours.is_open(ALWAYS, MONDAY, at(4)) is True


def test_no_hours_is_unknown_not_closed():
    assert hours.is_open(None, MONDAY, at(12)) is None
    assert hours.is_open([], MONDAY, at(12)) is None


def test_unreadable_periods_are_unknown_not_closed():
    assert hours.is_open([{"open": "noon"}, {"bogus": 1}], MONDAY, at(12)) is None


# --- target_time ---------------------------------------------------------------


def test_now_is_the_places_now(clock):
    clock(2026, 11, 11, 17, 30)  # Wed 12:30 in Boston, 09:30 in LA

    assert hours.target_time("now", None, None, BOSTON) == (WEDNESDAY, at(12, 30))
    assert hours.target_time("now", None, None, LA) == (WEDNESDAY, at(9, 30))


def test_now_can_be_tomorrow_somewhere_else(clock):
    clock(2026, 11, 12, 3, 0)  # Thu 03:00 UTC = Wed 22:00 in Boston

    assert hours.target_time("now", None, None, BOSTON) == (WEDNESDAY, at(22))


def test_tomorrow_at_seven(clock):
    clock(2026, 11, 11, 17, 30)  # Wednesday in Boston

    assert hours.target_time("later", "tomorrow", "19:00", BOSTON) == (WEDNESDAY + 1, at(19))


def test_today_at_eight(clock):
    clock(2026, 11, 11, 17, 30)

    assert hours.target_time("later", "today", "20:00", BOSTON) == (WEDNESDAY, at(20))


def test_a_weekday_name_means_its_next_occurrence(clock):
    clock(2026, 11, 11, 17, 30)  # Wednesday

    assert hours.target_time("later", "friday", "19:00", BOSTON) == (FRIDAY, at(19))
    assert hours.target_time("later", "monday", "12:00", BOSTON) == (MONDAY, at(12))
    # Today's own name is today, not a week away.
    assert hours.target_time("later", "wednesday", "19:00", BOSTON) == (WEDNESDAY, at(19))


def test_tomorrow_from_saturday_wraps_to_sunday(clock):
    clock(2026, 11, 14, 17, 0)  # Saturday in Boston

    assert hours.target_time("later", "tomorrow", "12:00", BOSTON) == (SUNDAY, at(12))


def test_cannot_tell_without_a_time_zone_or_a_time(clock):
    clock(2026, 11, 11, 17, 30)

    assert hours.target_time("now", None, None, None) is None
    assert hours.target_time("later", "tomorrow", None, BOSTON) is None
    assert hours.target_time("unknown", None, None, BOSTON) is None


# --- annotate ------------------------------------------------------------------


def _place(periods, offset=BOSTON):
    return {"name": "x", "periods": periods, "utc_offset_minutes": offset}


def test_annotate_marks_open_closed_and_unknown(clock):
    clock(2026, 11, 11, 21, 0)  # Wed 16:00 in Boston, between lunch and dinner
    places = [_place(ALWAYS), _place(LUNCH_AND_DINNER), _place(None)]

    hours.annotate(places, "now", None, None)

    assert [p["open_then"] for p in places] == [True, False, None]


def test_describe_when():
    assert hours.describe_when("now", None, None) == "right now"
    assert hours.describe_when("later", "tomorrow", "19:00") == "tomorrow at 19:00"
    assert hours.describe_when("later", "tomorrow", None) == "then"
