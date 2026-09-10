"""When the scheduled sync runs, and the two places that have to agree.

launchd decides when the job fires. next_window() decides when a failed run
has been superseded rather than retried. If those two disagree, a retry either
fires forever or never fires at all, and nothing says so. They now read the
same config key, and these tests are what keeps that true.
"""

from datetime import datetime

import pytest

from src.common import DEFAULT_SYNC_TIMES, ConfigError, parse_sync_times
from src.omnivox_sync import next_window


def test_a_comma_separated_string_is_the_written_form():
    """What `make settings` writes, because a text editor that only handles
    scalars cannot safely write a YAML list."""
    assert parse_sync_times("07:30, 12:15, 18:30") == ((7, 30), (12, 15), (18, 30))


def test_a_yaml_list_works_too():
    """What somebody editing config.yaml by hand will naturally write."""
    assert parse_sync_times(["07:30", "18:30"]) == ((7, 30), (18, 30))


def test_nothing_configured_means_the_shipped_schedule():
    for empty in (None, "", []):
        assert parse_sync_times(empty) == DEFAULT_SYNC_TIMES


def test_times_are_sorted_and_deduplicated():
    """next_window walks them in order and returns the first one still ahead.
    Out of order, it would return a window that has already passed, and the
    retry armed against it would be due immediately, forever."""
    assert parse_sync_times("18:30, 07:30, 07:30") == ((7, 30), (18, 30))


@pytest.mark.parametrize("bad", ["7h30", "0730", "07:30:00", "morning", "07-30"])
def test_something_that_is_not_a_time_is_refused_loudly(bad):
    """Silently falling back to the default would leave somebody certain they
    had changed the schedule when they had not."""
    with pytest.raises(ConfigError, match="HH:MM"):
        parse_sync_times(bad)


@pytest.mark.parametrize("bad", ["25:00", "12:60", "99:99"])
def test_a_time_that_does_not_exist_is_refused(bad):
    with pytest.raises(ConfigError, match="real time"):
        parse_sync_times(bad)


def test_next_window_uses_the_configured_times():
    got = next_window(datetime(2026, 9, 9, 8, 0), ((7, 30), (18, 30)))
    assert (got.hour, got.minute) == (18, 30)


def test_next_window_rolls_over_to_tomorrow_after_the_last_one():
    got = next_window(datetime(2026, 9, 9, 23, 0), ((7, 30), (18, 30)))
    assert (got.day, got.hour, got.minute) == (10, 7, 30)


def test_next_window_still_defaults_when_given_nothing():
    got = next_window(datetime(2026, 9, 9, 8, 0))
    assert (got.hour, got.minute) == (12, 15)


def test_a_single_daily_time_still_rolls_over():
    """The quietest option on the settings page is one time a day, and a
    one-element schedule is where an off-by-one in the rollover would hide."""
    got = next_window(datetime(2026, 9, 9, 19, 0), ((18, 30),))
    assert (got.day, got.hour) == (10, 18)
