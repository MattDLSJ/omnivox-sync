"""The timetable scrape, against a real Omnivox page with the names changed.

Why this matters more than most parsers here: without a `schedule:` block the
recorder is inert on every platform, so lecture recording could not work from
a fresh install no matter what the installer did. Nothing wrote that block and
nothing could; the design spec had put it out of scope for v1 and it stayed
there. This is the piece that makes the feature reachable.
"""

from pathlib import Path

import pytest

from src.recorder import parse_schedule
from src.schedule_scrape import parse_horaire, session_code

FIXTURE = Path(__file__).parent / "fixtures" / "horaire.html"


@pytest.fixture
def slots():
    return parse_horaire(FIXTURE.read_text(encoding="utf-8"))


def test_it_finds_every_slot(slots):
    assert len(slots) == 9


def test_a_span_does_not_expire_a_row_early(slots):
    """The bug this was written for, and it is the reason to read the page
    rather than the printed PDF at all.

    A cell with rowspan=4 occupies its column for four rows. Decrementing the
    counter on the row that opened it frees the column one row too soon, and
    the failure is silent and plausible: Thursday 13:10-17:00 released its
    column for the 16:10 row, so Friday's 16:10 class was reported on
    Thursday. Found by diffing a scrape against a timetable somebody had
    typed in by hand, which is the only reason it was found at all."""
    late = [s for s in slots if s["start"] == "16:10"]
    assert len(late) == 1
    assert late[0]["weekday"] == "friday", "Thursday is still occupied at 16:10"

    long_one = [s for s in slots if s["course"] == "387-201-EM" and s["start"] == "13:10"]
    assert long_one and long_one[0]["end"] == "17:00", "four periods, not one"


def test_it_reads_the_room_the_block_and_the_group(slots):
    philo = [s for s in slots if s["start"] == "08:10" and s["weekday"] == "thursday"][0]
    assert philo["room"] == "C078"
    assert philo["block"] == "T"          # T theorie, L labo, E encadrement
    assert philo["group"] == "1060"
    # A room can name two gyms at once; ";" is Omnivox's separator and "/"
    # is what the config has always used.
    gym = [s for s in slots if s["course"] == "109-999-EM"][0]
    assert gym["room"] == "GQA/GQB"


def test_the_same_course_can_sit_in_two_rooms_on_two_days(slots):
    """Which is why `room` is per slot and not per course."""
    philo = sorted(
        (s for s in slots if s["course"] == "201-103-RE"),
        key=lambda s: s["start"],
    )
    assert [s["weekday"] for s in philo] == ["thursday", "friday"]
    assert [s["room"] for s in philo] == ["C078", "B035"]


def test_what_it_produces_is_what_the_recorder_accepts(slots):
    """The whole point. parse_schedule is the validator the config goes
    through, so anything this emits has to survive it untouched."""
    entries = parse_schedule(
        [{k: v for k, v in s.items() if k not in ("teacher", "group")} for s in slots]
    )
    assert len(entries) == 9
    assert {e.weekday for e in entries} <= {"wednesday", "thursday", "friday"}


def test_the_session_is_worked_out_from_the_date():
    """The page offers all three sessions of a year and picking the wrong one
    silently returns a different term's timetable."""
    from datetime import date

    assert session_code(date(2026, 9, 11)) == "20263"   # Automne
    assert session_code(date(2026, 2, 3)) == "20261"    # Hiver
    assert session_code(date(2026, 6, 20)) == "20262"   # Ete


def test_a_page_with_no_timetable_returns_nothing_rather_than_guessing():
    assert parse_horaire("<html><body>Session over</body></html>") == []
    assert parse_horaire("") == []


def test_discovery_fills_in_the_teacher_that_mio_filing_depends_on(tmp_path, slots):
    """src/mio.py matches an Omnivox message's sender against `teacher` on the
    course. Discovery never emitted one, so MIO filing silently did nothing
    for everybody except the one person who had typed the names in by hand
    before the feature existed. The timetable page has them."""
    import shutil
    from types import SimpleNamespace

    import yaml

    from src.omnivox_sync import _by_course, write_courses

    config = tmp_path / "config.yaml"
    shutil.copy("config.example.yaml", config)
    courses = [
        SimpleNamespace(code="201-103-RE", name="PHILOSOPHIE ET RATIONALITE"),
        SimpleNamespace(code="109-999-EM", name="BASKETBALL"),
    ]
    write_courses(config, courses, "Fall 2026", _by_course(slots))

    written = {c["code"]: c for c in yaml.safe_load(config.read_text(encoding="utf-8"))["courses"]}
    assert written["201-103-RE"]["teacher"]
    assert written["201-103-RE"]["group"] == "1060"
    # And the gym is still not something to record.
    assert written["109-999-EM"]["record"] is False


def test_a_hand_set_per_slot_override_survives_a_rediscovery(tmp_path, slots):
    """The one thing here a person sets and the college cannot know: a slot of
    a course can be a different KIND of event. An encadrement is other
    students asking about their own work, not a lecture."""
    import yaml

    from src.omnivox_sync import write_schedule

    config = tmp_path / "config.yaml"
    config.write_text(
        "semester: Fall 2026\nbase_path: \"\"\ncourses: []\n"
        "schedule:\n"
        "  - course: \"387-201-EM\"\n    weekday: \"thursday\"\n"
        "    start: \"17:10\"\n    end: \"18:00\"\n    record: false\n",
        encoding="utf-8",
    )
    write_schedule(config, slots)

    after = yaml.safe_load(config.read_text(encoding="utf-8"))["schedule"]
    kept = [s for s in after if s["course"] == "387-201-EM" and s["start"] == "17:10"]
    assert kept and kept[0]["record"] is False
    assert len(after) == 9, "and the rest of the timetable still arrived"
