"""The Thursday E block, and the three things it broke.

300-204-EM meets twice on one Thursday in Automne 2026: a 13:10-17:00 lecture
(block L) and a 17:10-18:00 block the horaire marks "E". Every other course in
the timetable meets once a day, so the E block was the first case of one course
having two slots on one date, and it found three separate wrong answers:

  * class_label() suffixed by MEETING, so both slots claimed "1a"
  * the calendar title was identical for both, differing only by start time
  * recordable() is all-or-nothing per course, so recording the lecture meant
    recording the encadrement too

The E block sits outside the pondération (Th 1 + Lab 3 = 4 h, which the L block
already fills on its own) and is not the teacher's posted office hours, so what
it actually is stays unconfirmed. That is exactly why it must not record by
default: an encadrement period is other students talking about their own work.
"""

from datetime import datetime, time as dtime

import pytest

from src.calendar_export import event_title
from src.common import Course, load_config
from src.recorder import ScheduleEntry, class_label, parse_schedule, recordable

TWO_SLOTS_ONE_DAY = [
    {"course": "601-101-MQ", "weekday": "thursday", "start": "13:10", "end": "17:00",
     "room": "C142", "block": "L"},
    {"course": "601-101-MQ", "weekday": "thursday", "start": "17:10", "end": "18:00",
     "room": "C142", "block": "E", "record": False},
]

TWO_DAYS = [
    {"course": "601-101-MQ", "weekday": "thursday", "start": "08:10", "end": "10:00"},
    {"course": "601-101-MQ", "weekday": "friday", "start": "16:10", "end": "18:00"},
]


def _cfg(write_config, tmp_repo, sample_config_dict, schedule, *, record=True):
    courses = [dict(c) for c in sample_config_dict["courses"]]
    courses[0]["record"] = record
    return load_config(write_config({
        "courses": courses,
        "schedule": schedule,
        "semester_start": "2026-08-24",
        "semester_end": "2026-12-09",
        "no_class_days": [],
    }), repo_root=tmp_repo)


# --- the per-slot record override -------------------------------------------


def test_a_slot_can_opt_out_of_a_recorded_course(
    write_config, tmp_repo, sample_config_dict
):
    """The point of the whole override. Without it, recording the Thursday
    lecture would also record the encadrement that follows it."""
    cfg = _cfg(write_config, tmp_repo, sample_config_dict, TWO_SLOTS_ONE_DAY)
    kept = recordable(cfg, parse_schedule(cfg.schedule))
    assert [e.start for e in kept] == [dtime(13, 10)]


def test_a_slot_can_opt_in_to_a_course_that_does_not_record(
    write_config, tmp_repo, sample_config_dict
):
    """It has to work both ways, or `record: true` on a slot would be a lie."""
    schedule = [dict(TWO_SLOTS_ONE_DAY[1], record=True)]
    cfg = _cfg(write_config, tmp_repo, sample_config_dict, schedule, record=False)
    assert len(recordable(cfg, parse_schedule(cfg.schedule))) == 1


def test_a_slot_without_the_key_still_follows_its_course(
    write_config, tmp_repo, sample_config_dict
):
    """Every other entry in config.yaml omits `record:`, so the inherited path
    is the one that runs 99% of the time."""
    cfg = _cfg(write_config, tmp_repo, sample_config_dict, TWO_DAYS)
    assert len(recordable(cfg, parse_schedule(cfg.schedule))) == 2

    off = _cfg(write_config, tmp_repo, sample_config_dict, TWO_DAYS, record=False)
    assert recordable(off, parse_schedule(off.schedule)) == []


def test_the_override_is_absent_by_default_not_false():
    """None and False are different: None inherits, False refuses. Collapsing
    them would silence every course that never mentions `record:`."""
    assert ScheduleEntry("x", "monday", dtime(9), dtime(10)).record is None
    assert parse_schedule([TWO_SLOTS_ONE_DAY[0]])[0].record is None
    assert parse_schedule([TWO_SLOTS_ONE_DAY[1]])[0].record is False


# --- numbering ---------------------------------------------------------------


def test_two_slots_on_one_day_share_one_class_number(
    write_config, tmp_repo, sample_config_dict
):
    """The bug. The old loop matched on date and returned at the first hit, so
    the 17:10 slot silently inherited the 13:10 slot's "1a"."""
    cfg = _cfg(write_config, tmp_repo, sample_config_dict, TWO_SLOTS_ONE_DAY)
    assert class_label(cfg, "601-101-MQ", datetime(2026, 8, 27, 13, 10)) == "1"
    assert class_label(cfg, "601-101-MQ", datetime(2026, 8, 27, 17, 10)) == "1"
    assert class_label(cfg, "601-101-MQ", datetime(2026, 9, 3, 13, 10)) == "2"


def test_two_slots_on_two_days_still_get_a_and_b(
    write_config, tmp_repo, sample_config_dict
):
    """The fix counts distinct days, so it must not disturb Philo or
    Littérature, which really do meet Thursday and Friday."""
    cfg = _cfg(write_config, tmp_repo, sample_config_dict, TWO_DAYS)
    assert class_label(cfg, "601-101-MQ", datetime(2026, 8, 27, 8, 10)) == "1a"
    assert class_label(cfg, "601-101-MQ", datetime(2026, 8, 28, 16, 10)) == "1b"


# --- the calendar title ------------------------------------------------------


@pytest.fixture
def course():
    return Course(
        code="300-204-EM", omnivox_name="RECHERCHE", folder="Recherche qualitative",
        notebook="nb", record=True, icon="🔬", teacher="Léa Fournier", group="1040",
    )


def test_the_encadrement_block_says_so_in_the_title(course):
    """Both slots carry the same number, so the title is the only thing in a
    month view that separates them."""
    assert event_title(course, "7", "E") == "🔬 7 · Recherche qualitative (encadrement)"


@pytest.mark.parametrize("block", ["T", "L", ""])
def test_a_normal_block_gets_no_suffix(course, block):
    assert event_title(course, "7", block) == "🔬 7 · Recherche qualitative"


def test_an_unnumbered_meeting_drops_the_number_not_the_course(course):
    assert event_title(course, None, "T") == "🔬 Recherche qualitative"
