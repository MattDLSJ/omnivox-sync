import re
from datetime import datetime
from pathlib import Path

from src.common import load_config
from src.calendar_export import (
    build_ics, class_events, html_description, stable_uid, _description,
)
from src.common import Course


def _cfg(write_config, tmp_repo, sample_config_dict):
    courses = [dict(c, icon="🦉", teacher="Camille Nadeau") for c in sample_config_dict["courses"]]
    return load_config(write_config({
        "courses": courses,
        "schedule": [
            {"course": "601-101-MQ", "weekday": "thursday", "start": "08:10",
             "end": "10:00", "room": "C078"},
            {"course": "601-101-MQ", "weekday": "friday", "start": "16:10",
             "end": "18:00", "room": "B035"},
        ],
        "semester_start": "2026-08-24", "semester_end": "2026-12-09",
        "no_class_days": ["2026-10-12", "2026-10-13", "2026-10-14",
                          "2026-10-15", "2026-10-16"],
    }), repo_root=tmp_repo)


def test_one_event_per_meeting(write_config, tmp_repo, sample_config_dict):
    events = class_events(_cfg(write_config, tmp_repo, sample_config_dict))
    assert len(events) == 28, "14 weeks x 2 meetings"


def test_uid_is_stable_across_regeneration(write_config, tmp_repo, sample_config_dict):
    """The whole point: re-importing must UPDATE, not duplicate."""
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    first = [e["uid"] for e in class_events(cfg)]
    second = [e["uid"] for e in class_events(cfg)]
    assert first == second
    assert len(set(first)) == len(first), "UIDs must be unique"


def test_uid_depends_only_on_course_and_start():
    a = stable_uid("340-101-MQ", datetime(2026, 8, 27, 8, 10))
    b = stable_uid("340-101-MQ", datetime(2026, 8, 27, 8, 10))
    c = stable_uid("340-101-MQ", datetime(2026, 9, 3, 8, 10))
    assert a == b and a != c


def test_summary_carries_emoji_then_class_number(write_config, tmp_repo, sample_config_dict):
    ics = build_ics(_cfg(write_config, tmp_repo, sample_config_dict))
    assert "SUMMARY:🦉 1a · " in ics
    assert "SUMMARY:🦉 1b · " in ics


def test_room_follows_the_session_not_the_course(write_config, tmp_repo, sample_config_dict):
    """Philo is C078 on Thursday and B035 on Friday."""
    ics = build_ics(_cfg(write_config, tmp_repo, sample_config_dict))
    assert "Local C078 · Camille Nadeau" in ics
    assert "Local B035 · Camille Nadeau" in ics


def test_ics_uses_crlf_and_is_well_formed(write_config, tmp_repo, sample_config_dict):
    ics = build_ics(_cfg(write_config, tmp_repo, sample_config_dict))
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.rstrip().endswith("END:VCALENDAR")
    assert ics.count("BEGIN:VEVENT") == ics.count("END:VEVENT")
    assert "\n" not in ics.replace("\r\n", "")


def test_no_line_exceeds_75_octets(write_config, tmp_repo, sample_config_dict):
    for line in build_ics(_cfg(write_config, tmp_repo, sample_config_dict)).split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, line[:60]


def test_no_events_on_the_empty_week(write_config, tmp_repo, sample_config_dict):
    events = class_events(_cfg(write_config, tmp_repo, sample_config_dict))
    october = {e["start"].date().isoformat() for e in events}
    assert not (october & {"2026-10-15", "2026-10-16"})


def test_every_event_has_a_ten_minute_alarm(write_config, tmp_repo, sample_config_dict):
    ics = build_ics(_cfg(write_config, tmp_repo, sample_config_dict))
    assert ics.count("TRIGGER:-PT10M") == ics.count("BEGIN:VEVENT")


# ---------------------------------------------------------------------------
# Description formatting. Google renders a small HTML subset, and a bare \n is
# whitespace to an HTML parser: it collapses. That bug shipped once already, so
# these assert on the markup and not merely on the text being present.
# ---------------------------------------------------------------------------

_COURSE = Course(
    code="340-101-MQ", omnivox_name="PHILO", folder="Philosophie et rationalité",
    notebook="nb", teacher="Camille Nadeau", group="1060", icon="🦉",
)


def test_html_description_separates_header_lines_with_br_not_newline():
    html = html_description(_COURSE, "C078", "1a", "T")
    assert "\n" not in html
    assert html.count("<br>") == 4


def test_html_description_matches_the_established_layout():
    html = html_description(_COURSE, "C078", "1a", "T")
    assert html.startswith("<b>Philosophie et rationalité</b><br>")
    assert "340-101-MQ gr.1060" in html          # course code carries the group
    assert "Local C078    T" in html             # room, then the block letter
    assert "<b><i>Présentiel</i></b>" in html    # delivery mode, bold + italic


def test_html_description_has_all_three_headings_and_leaves_them_empty():
    html = html_description(_COURSE, "C078", "1a", "T")
    for heading in ("1a À faire / Devoirs :", "Contenu du cours :", "Notes / Rappels :"):
        assert f"<p><strong>{heading}</strong></p>" in html
    assert "<li>" not in html  # nothing pre-filled; he types his own bullets


def test_html_description_escapes_markup_in_course_data():
    nasty = Course(code="X<1>", omnivox_name="N", folder="A & B", notebook="nb")
    html = html_description(nasty, "R", "1", "T")
    assert "A &amp; B" in html and "X&lt;1&gt;" in html


def test_plain_description_keeps_newlines_for_the_ics():
    plain = _description(_COURSE, "C078", "1a", "T")
    assert "<br>" not in plain and "<b>" not in plain
    assert plain.splitlines()[0] == "Philosophie et rationalité"
    assert "Local C078    T" in plain


def test_block_letter_is_omitted_cleanly_when_unknown():
    assert "Local C078" in html_description(_COURSE, "C078", "1a", "")
    assert "Local C078    " not in html_description(_COURSE, "C078", "1a", "")


def test_ics_carries_both_plain_and_html_descriptions(
    write_config, tmp_repo, sample_config_dict
):
    ics = build_ics(_cfg(write_config, tmp_repo, sample_config_dict))
    n = ics.count("BEGIN:VEVENT")
    assert ics.count("X-ALT-DESC;FMTTYPE=text/html:") == n
    unfolded = ics.replace("\r\n ", "")
    assert unfolded.count("<b><i>Présentiel</i></b>") == n
