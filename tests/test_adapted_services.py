"""Exam accommodations, and the arithmetic nobody does by hand.

The collision check is the reason this module exists. Extra time is a
percentage of the class block and the booking form starts your session when
the class starts, so an extended exam runs past the end of its period and into
whatever is next. Neither system involved can see it: the exam module does not
know the timetable, and the timetable does not know about the accommodation.
On the first real run against one student's schedule it found three, including
two exams booked on the same morning where the first overlapped the second by
26 minutes.
"""

from datetime import time as dtime
from pathlib import Path

from src.adapted_services import (
    Accommodations, collisions, extended_end, parse_accommodations, summary,
)
from src.recorder import ScheduleEntry

FIXTURE = Path(__file__).parent / "fixtures" / "srae-accommodements.html"


def test_it_reads_what_the_college_granted():
    acc = parse_accommodations(FIXTURE.read_text(encoding="utf-8"))
    assert acc.extra_time_percent == 33
    assert acc.separate_room
    assert any("Salle d'examen" in i for i in acc.items)


def test_a_label_and_its_value_are_rejoined():
    """The panel renders them as separate elements, so they arrive as two
    lines. Split apart, neither half states what was granted and the bare
    percentage reads as an accommodation of its own."""
    acc = parse_accommodations(FIXTURE.read_text(encoding="utf-8"))
    assert any("examen théorique : 33%" in i for i in acc.items)
    assert ": 33%" not in acc.items


def test_the_hidden_error_placeholder_is_not_an_accommodation():
    """The panel ships one whether or not anything failed."""
    acc = parse_accommodations(FIXTURE.read_text(encoding="utf-8"))
    assert not any("erreur" in i.lower() for i in acc.items)


def test_a_page_with_no_accommodations_says_so():
    assert not parse_accommodations("<html><body>rien</body></html>")
    assert parse_accommodations("").extra_time_percent == 0


def test_extra_time_is_a_lower_bound_on_the_real_end():
    """The college's form rounds the block up to whole periods before adding
    the percentage, so the true booking can run later than this, never
    earlier. That is the safe direction: a collision found here is real."""
    assert extended_end(dtime(8, 10), dtime(10, 0), 33) == dtime(10, 36)
    assert extended_end(dtime(8, 10), dtime(10, 0), 0) == dtime(10, 0)


def test_a_late_exam_does_not_wrap_past_midnight():
    """Otherwise the end sorts before its own start and every comparison after
    it is nonsense."""
    assert extended_end(dtime(22, 0), dtime(23, 50), 50) == dtime(23, 59)


def _slot(course, weekday, start, end):
    return ScheduleEntry(course=course, weekday=weekday, start=start, end=end)


def test_it_finds_the_exam_that_runs_into_the_next_class():
    entries = [
        _slot("340-101-MQ", "thursday", dtime(8, 10), dtime(10, 0)),
        _slot("601-102-MQ", "thursday", dtime(10, 10), dtime(12, 0)),
    ]
    found = collisions(entries, 33)
    assert len(found) == 1
    assert found[0].course == "340-101-MQ"
    assert found[0].clashes_with == "601-102-MQ"
    assert found[0].overlap_minutes == 26


def test_no_extra_time_means_nothing_to_report():
    entries = [
        _slot("340-101-MQ", "thursday", dtime(8, 10), dtime(10, 0)),
        _slot("601-102-MQ", "thursday", dtime(10, 10), dtime(12, 0)),
    ]
    assert collisions(entries, 0) == []


def test_a_gap_big_enough_is_not_a_collision():
    entries = [
        _slot("A", "monday", dtime(8, 0), dtime(9, 0)),
        _slot("B", "monday", dtime(13, 0), dtime(14, 0)),
    ]
    assert collisions(entries, 33) == []


def test_days_are_kept_apart():
    """A Thursday exam cannot run into a Friday class, however long it is."""
    entries = [
        _slot("A", "thursday", dtime(8, 0), dtime(10, 0)),
        _slot("B", "friday", dtime(8, 30), dtime(10, 0)),
    ]
    assert collisions(entries, 50) == []


def test_the_summary_is_empty_when_there_is_no_accommodation():
    """Most students have none and should never see this section."""
    assert summary(Accommodations(), []) == ""


def test_the_summary_names_both_courses_readably():
    entries = [
        _slot("340-101-MQ", "thursday", dtime(8, 10), dtime(10, 0)),
        _slot("601-102-MQ", "thursday", dtime(10, 10), dtime(12, 0)),
    ]
    acc = Accommodations(("Temps supplémentaire : 33%",), 33)
    text = summary(acc, collisions(entries, 33),
                   labels={"340-101-MQ": "Philosophie", "601-102-MQ": "Littérature"})
    assert "Philosophie" in text and "Littérature" in text
    assert "26 min over" in text
