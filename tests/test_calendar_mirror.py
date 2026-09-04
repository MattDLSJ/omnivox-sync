"""The calendar is the only thing that is written; everything else derives.

The per-class content, the evaluations and their weightings, and your own
notes all live in Google Calendar and nowhere else. NotebookLM, which is where
he actually asks questions about a course, could not see any of it: it had the
plan de cours and not the schedule built from it.

So the mirror runs one way, calendar -> file, and there is deliberately no path
back. Two-way would mean deciding which copy wins when both changed, and the
answer would have to be "the one he touched last", which nothing here can know.

The parser is written against Google's secret-iCal export, which is not the
same bytes as our own `build_ics()`: Google folds at 75 octets mid-word, emits
the description as HTML rather than plain text, and returns UTC stamps. So the
tests cover both shapes, ours for the round trip and a faithful Google-shaped
fixture for everything that reads a description.
"""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.calendar_export import build_ics
from src.calendar_mirror import (
    MIRROR_NAME,
    CalEvent,
    events_for,
    ics_url,
    mirror,
    parse_description,
    parse_ics,
    render_course,
)
from src.common import load_config

TZ = ZoneInfo("America/Toronto")
WHEN = datetime(2026, 8, 25, 5, 47, tzinfo=TZ)

# What Google actually returns: folded at 75 octets wherever it lands, the
# description as HTML, the emoji as literal UTF-8. The shape is real, copied
# from a live feed; the teacher, the evaluation and the private note are
# invented. A calendar mirror is exactly the kind of test where somebody's
# actual timetable ends up committed by accident, so this one is synthetic.
GOOGLE_ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
    "BEGIN:VEVENT\r\nUID:a@x\r\n"
    "DTSTART;TZID=America/Toronto:20260911T081000\r\n"
    "DTEND;TZID=America/Toronto:20260911T120000\r\n"
    "SUMMARY:🏛️ 3 · Histoire du monde · 📝 Quiz de lecture 1 (5 %)\r\n"
    "LOCATION:Local Z016 · Camille Nadeau\r\n"
    "DESCRIPTION:<b>Histoire du monde</b><br>601-101-MQ gr.1100<br>Local Z016  <b\r\n"
    " r>Camille Nadeau<br><b><i>Présentiel</i></b><p><strong>3 À faire / Devoir\r\n"
    " s :</strong></p><ul><li><strong>📝 Quiz de lecture 1 (5 %)</strong></li><li>Li\r\n"
    " re le chapitre 4</li></ul><p><strong>Contenu du cours :</strong></p><ul><l\r\n"
    " i>Les grandes routes commerciales</li></ul><p><strong>Notes / Rappels :</s\r\n"
    " trong></p><ul><li>Réviser les cartes du chapitre</li></ul>\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)


def _cfg(write_config, tmp_repo, sample_config_dict, schedule=None):
    return load_config(
        write_config(
            {
                "schedule": schedule
                if schedule is not None
                else [
                    {"course": "601-101-MQ", "weekday": "thursday",
                     "start": "08:10", "end": "10:00", "room": "Z016", "block": "T"},
                ],
                "semester_start": "2026-08-24",
                "semester_end": "2026-12-09",
                "no_class_days": [],
            }
        ),
        repo_root=tmp_repo,
    )


# --- the parser --------------------------------------------------------------


def test_round_trips_our_own_ics(write_config, tmp_repo, sample_config_dict):
    """Our writer folds and escapes per RFC 5545; the reader must undo exactly
    that, or the mirror silently drops events it cannot parse."""
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    events = parse_ics(build_ics(cfg))

    assert len(events) == 15          # every Thursday, 27 Aug to 3 Dec
    assert all(isinstance(e.start, datetime) for e in events)
    assert events[0].start.hour == 8 and events[0].start.minute == 10
    assert events[0].start.tzinfo is not None
    # The VALARM inside each VEVENT carries DESCRIPTION:Rappel. Reading it as
    # the event's own is how every mirrored class ends up saying "Rappel".
    assert all(e.description != "Rappel" for e in events)
    assert "Écriture et littérature" in events[0].description


def test_a_line_folded_mid_word_is_reassembled():
    """Google folds at 75 octets with no regard for word boundaries, so a
    naive line-by-line read loses the tail of every long description."""
    event = parse_ics(GOOGLE_ICS)[0]
    assert "Les grandes routes commerciales" in event.description
    assert "Camille Nadeau" in event.description
    assert "\n " not in event.description


def test_rfc_escapes_are_undone():
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20260911T121000Z\r\n"
           "SUMMARY:Vian\\, Sagan\\; Louis\\nsuite\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert parse_ics(ics)[0].summary == "Vian, Sagan; Louis\nsuite"


def test_utc_stamps_come_back_in_local_time():
    """Google exports UTC. 12:10Z in September is 08:10 in Longueuil, and an
    off-by-four-hours mirror would be worse than no mirror."""
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20260911T121000Z\r\n"
           "SUMMARY:x\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    start = parse_ics(ics)[0].start
    assert (start.hour, start.minute) == (8, 10)


def test_all_day_events_stay_dates():
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20260907\r\n"
           "SUMMARY:Congé férié\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert parse_ics(ics)[0].start == date(2026, 9, 7)


def test_a_cancelled_event_is_not_mirrored():
    """Google keeps cancelled events in the feed. Mirroring one would put a
    class in the file that he was told is not happening."""
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20260911T121000Z\r\n"
           "SUMMARY:annulé\r\nSTATUS:CANCELLED\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert parse_ics(ics) == []


# --- the description ---------------------------------------------------------


def test_the_three_headings_come_back_as_sections():
    described = parse_description(parse_ics(GOOGLE_ICS)[0].description)
    titles = [t for t, _ in described.sections]
    assert "À faire" in titles[0] and "Contenu" in titles[1] and "Notes" in titles[2]
    assert described.section("Contenu") == ["Les grandes routes commerciales"]


def test_his_own_notes_survive():
    """The whole point. Notes / Rappels is the half he writes himself, and it is
    the half NotebookLM could not see."""
    described = parse_description(parse_ics(GOOGLE_ICS)[0].description)
    assert described.section("Notes") == ["Réviser les cartes du chapitre"]


def test_an_unrecognised_description_is_kept_not_dropped():
    """These get hand-edited. Anything the parser does not understand has to
    come through as a plain line rather than vanish."""
    described = parse_description("juste une note écrite à la main")
    assert described.header == ["juste une note écrite à la main"]


# --- rendering ---------------------------------------------------------------


def test_evaluations_get_their_own_table(write_config, tmp_repo, sample_config_dict):
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    course = cfg.course_by_code("601-101-MQ")
    body = render_course(course, parse_ics(GOOGLE_ICS), generated=WHEN)

    assert "## Évaluations" in body
    assert "| 3 | vendredi 11 septembre | Quiz de lecture 1 (5 %) |" in body


def test_the_class_number_is_not_repeated_inside_its_own_section(
    write_config, tmp_repo, sample_config_dict
):
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    body = render_course(cfg.course_by_code("601-101-MQ"), parse_ics(GOOGLE_ICS),
                         generated=WHEN)
    assert "**À faire / Devoirs**" in body
    assert "**3 À faire" not in body


def test_the_file_says_it_is_a_copy(write_config, tmp_repo, sample_config_dict):
    """Someone reading it, human or agent, has to know editing it is pointless."""
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    body = render_course(cfg.course_by_code("601-101-MQ"), parse_ics(GOOGLE_ICS),
                         generated=WHEN)
    assert "lecture seule" in body and "calendrier est la source" in body


def test_events_are_matched_by_course_code_not_title():
    """Titles get hand-edited and two courses can share a room or a teacher.
    The code in the description is the only unambiguous field."""
    mine = CalEvent(summary="n'importe quoi", description="<b>x</b><br>601-101-MQ gr.1")
    other = CalEvent(summary="📖 1a · Écriture et littérature", description="<b>x</b><br>999-999-XX")

    class C:
        code = "601-101-MQ"

    assert events_for(C(), [mine, other]) == [mine]


# --- the writer --------------------------------------------------------------


def test_inert_without_the_secret_url(write_config, tmp_repo, sample_config_dict):
    """No .env key means no mirror, no crash, and nothing written, the same way
    the recorder is inert without a schedule."""
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    assert ics_url(tmp_repo) is None

    result = mirror(cfg)
    assert result.written == [] and result.events == 0


def test_an_unchanged_semester_is_not_rewritten(
    write_config, tmp_repo, sample_config_dict
):
    """The timestamp changes every run. If that counted as a change, six files
    would re-upload to NotebookLM every single morning for nothing."""
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)

    first = mirror(cfg, ics_text=GOOGLE_ICS)
    assert len(first.written) == 1
    assert first.written[0].name == MIRROR_NAME

    second = mirror(cfg, ics_text=GOOGLE_ICS)
    assert second.written == []
    assert second.unchanged == first.written


def test_a_changed_calendar_does_rewrite(write_config, tmp_repo, sample_config_dict):
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    mirror(cfg, ics_text=GOOGLE_ICS)

    edited = GOOGLE_ICS.replace("Réviser les cartes du chapitre", "Réviser les dates clés")
    again = mirror(cfg, ics_text=edited)

    assert len(again.written) == 1
    assert "Réviser les dates clés" in again.written[0].read_text(encoding="utf-8")


def test_the_mirror_lands_beside_the_course_material(
    write_config, tmp_repo, sample_config_dict
):
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    written = mirror(cfg, ics_text=GOOGLE_ICS).written[0]
    assert written.parent == cfg.folder_for(cfg.course_by_code("601-101-MQ"))


def test_dry_run_reports_without_writing(write_config, tmp_repo, sample_config_dict):
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    result = mirror(cfg, ics_text=GOOGLE_ICS, dry_run=True)
    assert len(result.written) == 1
    assert not result.written[0].exists()


# --- the wrong link ----------------------------------------------------------


def test_the_shareable_link_is_rejected_by_name():
    """The settings page offers two links centimetres apart, and the wrong one
    gets picked first. "Get shareable link" is the web UI: it needs a login
    and returns HTML, so parsing it would find no events and report an empty
    semester, which is indistinguishable from a real one."""
    from src.calendar_mirror import MirrorError, fetch_ics, looks_like_ics_feed

    shareable = "https://calendar.google.com/calendar/u/0?cid=MjZiOTM4MzQ5NGI2"
    assert not looks_like_ics_feed(shareable)

    with pytest.raises(MirrorError) as exc:
        fetch_ics(shareable)
    assert "?cid=" in str(exc.value)
    assert "Adresse secrète au format iCal" in str(exc.value)


def test_the_real_feed_shape_is_accepted():
    from src.calendar_mirror import looks_like_ics_feed

    assert looks_like_ics_feed(
        "https://calendar.google.com/calendar/ical/abc%40group.calendar."
        "google.com/private-0123456789abcdef/basic.ics"
    )


def test_html_answered_with_200_is_not_read_as_an_empty_semester(monkeypatch):
    """The nastier failure: a feed that redirects to sign-in answers 200 with a
    login page. Zero events must be an error, not a quiet 'no classes'."""
    import io

    from src.calendar_mirror import MirrorError, fetch_ics

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "src.calendar_mirror.urllib.request.urlopen",
        lambda *a, **k: FakeResponse(b"<!doctype html><title>Sign in</title>"),
    )
    with pytest.raises(MirrorError, match="pas en iCal"):
        fetch_ics("https://calendar.google.com/calendar/ical/x/private-y/basic.ics")


# --- prose is not a heading --------------------------------------------------

ENCADREMENT = (
    "<b>Recherche qualitative</b><br>601-101-MQ gr.1040<br>Local C142    E<br>"
    "<b><i>Présentiel</i></b>"
    "<p><strong>❓ Bloc « E » de l'horaire, après le cours de 13:10. Hors "
    "pondération. Ne s'enregistre pas.</strong></p>"
    "<p><strong>1 À faire / Devoirs :</strong></p>"
    "<p><strong>Contenu du cours :</strong></p>"
    "<p><strong>Notes / Rappels :</strong></p>"
)


def test_a_bold_paragraph_without_a_colon_is_prose_not_a_heading():
    """The encadrement note is a whole bold paragraph, so "is it bold" made it
    a heading with no items, and empty headings render as nothing. The only
    explanation of what that 17:10 block even is disappeared from the file."""
    described = parse_description(ENCADREMENT)
    titles = [t for t, _ in described.sections]

    assert not any("Bloc « E »" in t for t in titles)
    assert any("Bloc « E »" in n for n in described.notes)


def test_that_note_actually_reaches_the_file(write_config, tmp_repo, sample_config_dict):
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    ics = GOOGLE_ICS.replace(
        GOOGLE_ICS[GOOGLE_ICS.index("DESCRIPTION:"):GOOGLE_ICS.index("END:VEVENT")],
        "DESCRIPTION:" + ENCADREMENT + "\r\n",
    )
    body = render_course(cfg.course_by_code("601-101-MQ"), parse_ics(ics), generated=WHEN)
    assert "Bloc « E »" in body


def test_bold_mid_prose_does_not_split_a_note_in_half():
    """The Littérature week-11 note bolds one sentence in the middle. Treating
    that as a heading hung the rest of the note under a title that was really
    the middle of a sentence."""
    described = parse_description(
        "<b>x</b><br>601-101-MQ<p><strong>Contenu du cours :</strong></p>"
        "<p><em>Le plan ne prévoit qu'une séance.</em></p>"
        "<p><em>Mais la journée JR est le <b>mercredi 11 novembre</b>, "
        "et le groupe a cours le jeudi.</em></p>"
    )
    titles = [t for t, _ in described.sections]
    assert titles == ["Contenu du cours :"]
    assert len(described.prose_for("Contenu du cours :")) == 2


def test_a_mirror_that_never_landed_is_queued_again(
    write_config, tmp_repo, fake_driver, course_factory, monkeypatch
):
    """"Written" is not "in the notebook". If the upload failed, the file sits
    on disk unchanged and nothing about it will ever change again until the
    calendar does, so queueing only on change strands it forever."""
    import src.calendar_mirror as mirror_mod
    from src.omnivox_sync import SyncResult, _run_mirror
    from src.common import load_config
    import logging

    cfg = _cfg(write_config, tmp_repo, None)
    monkeypatch.setattr(
        mirror_mod, "ics_url", lambda root: "https://x/calendar/ical/a/private-b/basic.ics"
    )
    monkeypatch.setattr(mirror_mod, "fetch_ics", lambda url, **k: GOOGLE_ICS)

    first = SyncResult()
    _run_mirror(cfg, first, False, logging.getLogger("t"))
    assert [q["filename"] for q in first.upload_queue] == [MIRROR_NAME]
    assert first.upload_queue[0]["replace"] is True

    # Nothing recorded as landed, and the file is now unchanged: queue it anyway.
    second = SyncResult()
    _run_mirror(cfg, second, False, logging.getLogger("t"))
    assert [q["filename"] for q in second.upload_queue] == [MIRROR_NAME]
