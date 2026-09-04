import os
from datetime import date, datetime, time as dtime
from pathlib import Path

import pytest

from src.common import ConfigError, load_config
from src.recorder import (
    GRACE_MINUTES,
    ScheduleEntry,
    active_entry,
    at_school,
    chunk_order,
    finished_entry,
    parse_schedule,
    parse_time,
    recordable,
    tick,
)

#: The autouse guard below replaces start_capture for every test. Hold on to
#: the real one here, at import time, for the one test whose subject IS the
#: capture command. That test fakes Popen, so no ffmpeg ever runs.
import src.recorder as _rec  # noqa: E402

_REAL_START_CAPTURE = _rec.start_capture

SCHED = [
    {"course": "601-101-MQ", "weekday": "tuesday", "start": "10:00", "end": "12:00"},
    {"course": "201-103-RE", "weekday": "thursday", "start": "08:30", "end": "10:00"},
]


# --- parsing -----------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [("10:00", dtime(10, 0)), ("8:05", dtime(8, 5)),
                                          (" 14:30 ", dtime(14, 30)), ("9h45", dtime(9, 45))])
def test_parse_time_accepts_reasonable_forms(raw, expected):
    assert parse_time(raw) == expected


@pytest.mark.parametrize("raw", ["25:00", "10:70", "noon", "", "1000"])
def test_parse_time_rejects_nonsense(raw):
    with pytest.raises(ConfigError):
        parse_time(raw)


def test_empty_schedule_is_valid():
    assert parse_schedule([]) == []
    assert parse_schedule(None) == []


def test_parse_schedule_builds_entries():
    entries = parse_schedule(SCHED)
    assert entries[0] == ScheduleEntry("601-101-MQ", "tuesday", dtime(10, 0), dtime(12, 0))


@pytest.mark.parametrize(
    "bad,needle",
    [
        ({"course": "X", "weekday": "mardi", "start": "10:00", "end": "12:00"}, "weekday"),
        ({"course": "X", "weekday": "tuesday", "start": "12:00", "end": "10:00"}, "after"),
        ({"weekday": "tuesday", "start": "10:00", "end": "12:00"}, "course"),
    ],
)
def test_bad_schedule_entries_are_rejected(bad, needle):
    with pytest.raises(ConfigError, match=needle):
        parse_schedule([bad])


# --- window logic ------------------------------------------------------------

TUE_1030 = datetime(2026, 9, 8, 10, 30)   # a Tuesday, mid-class
TUE_0959 = datetime(2026, 9, 8, 9, 59)
TUE_1200 = datetime(2026, 9, 8, 12, 0)    # exactly at end
TUE_1205 = datetime(2026, 9, 8, 12, 5)    # inside grace
TUE_1215 = datetime(2026, 9, 8, 12, 15)   # past grace
WED_1030 = datetime(2026, 9, 9, 10, 30)   # wrong day


def test_active_inside_the_window():
    assert active_entry(parse_schedule(SCHED), TUE_1030).course == "601-101-MQ"


def test_not_active_before_start():
    assert active_entry(parse_schedule(SCHED), TUE_0959) is None


def test_not_active_at_exact_end():
    assert active_entry(parse_schedule(SCHED), TUE_1200) is None


def test_not_active_on_another_weekday():
    assert active_entry(parse_schedule(SCHED), WED_1030) is None


def test_arriving_late_still_records():
    """Spec section 10: opening the Mac mid-class must start capture."""
    assert active_entry(parse_schedule(SCHED), datetime(2026, 9, 8, 11, 55)) is not None


def test_finished_within_grace():
    assert finished_entry(parse_schedule(SCHED), TUE_1205).course == "601-101-MQ"


def test_not_finished_after_grace():
    assert finished_entry(parse_schedule(SCHED), TUE_1215) is None


def test_grace_is_ten_minutes():
    assert GRACE_MINUTES == 10


def test_active_never_starts_during_the_grace_period():
    """Otherwise a tick at end+5 would record an empty room."""
    assert active_entry(parse_schedule(SCHED), TUE_1205) is None


# --- opt-in ------------------------------------------------------------------


def test_only_courses_with_record_true_are_recordable(write_config, tmp_repo, sample_config_dict):
    courses = [dict(c) for c in sample_config_dict["courses"]]
    courses[0]["record"] = True
    cfg = load_config(write_config({"courses": courses}), repo_root=tmp_repo)
    entries = recordable(cfg, parse_schedule(SCHED))
    assert [e.course for e in entries] == ["601-101-MQ"]


def test_no_opted_in_courses_means_nothing_recordable(loaded_config):
    assert recordable(loaded_config, parse_schedule(SCHED)) == []


# --- dormancy ----------------------------------------------------------------


def test_tick_is_inert_with_an_empty_schedule(loaded_config):
    """The whole point: installing the job early must be harmless."""
    assert tick(loaded_config, now=TUE_1030) == "inert"


def test_tick_is_inert_when_no_course_opted_in(write_config, tmp_repo):
    cfg = load_config(write_config({"schedule": SCHED}), repo_root=tmp_repo)
    assert tick(cfg, now=TUE_1030) == "inert"


def test_tick_is_idle_outside_any_window(write_config, tmp_repo, sample_config_dict):
    courses = [dict(c) for c in sample_config_dict["courses"]]
    courses[0]["record"] = True
    cfg = load_config(
        write_config({"courses": courses, "schedule": SCHED}), repo_root=tmp_repo
    )
    assert tick(cfg, now=WED_1030) == "idle"


def test_tick_starts_capture_inside_a_window(write_config, tmp_repo, sample_config_dict, monkeypatch):
    started = []
    courses = [dict(c) for c in sample_config_dict["courses"]]
    courses[0]["record"] = True
    cfg = load_config(
        write_config({"courses": courses, "schedule": SCHED}), repo_root=tmp_repo
    )
    monkeypatch.setattr("src.recorder.start_capture", lambda *a: started.append(a))
    monkeypatch.setattr("src.recorder.is_recording", lambda cfg: False)
    assert tick(cfg, now=TUE_1030) == "started"
    assert len(started) == 1


def test_tick_does_not_restart_an_existing_capture(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    courses = [dict(c) for c in sample_config_dict["courses"]]
    courses[0]["record"] = True
    cfg = load_config(write_config({"courses": courses, "schedule": SCHED}), repo_root=tmp_repo)
    monkeypatch.setattr("src.recorder.is_recording", lambda cfg: True)
    monkeypatch.setattr(
        "src.recorder.start_capture", lambda *a: pytest.fail("must not double-start")
    )
    assert tick(cfg, now=TUE_1030) == "recording"


# --- at-school gate ----------------------------------------------------------


def test_gate_off_always_allows(loaded_config):
    assert at_school(loaded_config) is True


def test_ip_gate_matches_prefix(write_config, tmp_repo, monkeypatch):
    cfg = load_config(
        write_config({"at_school_check": "ip", "school_ip_prefix": "132.213."}),
        repo_root=tmp_repo,
    )
    monkeypatch.setattr("src.recorder.current_ip", lambda **k: "132.213.44.9")
    assert at_school(cfg) is True
    monkeypatch.setattr("src.recorder.current_ip", lambda **k: "24.201.5.5")
    assert at_school(cfg) is False


def test_ip_gate_without_a_prefix_refuses_to_record(write_config, tmp_repo):
    cfg = load_config(write_config({"at_school_check": "ip"}), repo_root=tmp_repo)
    assert at_school(cfg) is False


def test_ip_lookup_failure_refuses_to_record(write_config, tmp_repo, monkeypatch):
    """Never record off-campus just because the network check broke."""
    cfg = load_config(
        write_config({"at_school_check": "ip", "school_ip_prefix": "132."}), repo_root=tmp_repo
    )
    monkeypatch.setattr(
        "src.recorder.current_ip", lambda **k: (_ for _ in ()).throw(OSError("offline"))
    )
    assert at_school(cfg) is False


def test_gated_tick_does_not_start_capture(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    courses = [dict(c) for c in sample_config_dict["courses"]]
    courses[0]["record"] = True
    cfg = load_config(
        write_config({"courses": courses, "schedule": SCHED,
                      "at_school_check": "ip", "school_ip_prefix": "132."}),
        repo_root=tmp_repo,
    )
    monkeypatch.setattr("src.recorder.current_ip", lambda **k: "24.1.1.1")
    monkeypatch.setattr("src.recorder.is_recording", lambda cfg: False)
    monkeypatch.setattr(
        "src.recorder.start_capture", lambda *a: pytest.fail("must not record off campus")
    )
    assert tick(cfg, now=TUE_1030) == "gated"


# --- concat ordering ---------------------------------------------------------


def test_chunks_sort_numerically_not_lexically(tmp_path):
    names = ["chunk_100000_010.m4a", "chunk_100000_002.m4a", "chunk_100000_001.m4a"]
    paths = [tmp_path / n for n in names]
    assert [p.name for p in chunk_order(paths)] == [
        "chunk_100000_001.m4a", "chunk_100000_002.m4a", "chunk_100000_010.m4a"
    ]


def test_chunks_from_a_resumed_session_sort_after_the_first(tmp_path):
    """A lid-close means a second ffmpeg run with a later start stamp."""
    names = ["chunk_103000_000.m4a", "chunk_100000_001.m4a", "chunk_100000_000.m4a"]
    paths = [tmp_path / n for n in names]
    assert [p.name for p in chunk_order(paths)] == [
        "chunk_100000_000.m4a", "chunk_100000_001.m4a", "chunk_103000_000.m4a"
    ]


# --- semester bounds ---------------------------------------------------------
# Without date bounds the weekly schedule alone would record over the holidays,
# through exams, and on next semester's Tuesdays.


@pytest.fixture(autouse=True)
def _never_spawn_ffmpeg(monkeypatch):
    """Hard guard: no unit test may start a real capture."""
    monkeypatch.setattr(
        "src.recorder.start_capture",
        lambda *a: pytest.fail("a test tried to spawn a real ffmpeg capture"),
    )


def _bounded(write_config, tmp_repo, sample_config_dict, **over):
    courses = [dict(c) for c in sample_config_dict["courses"]]
    courses[0]["record"] = True
    base = {
        "courses": courses,
        "schedule": SCHED,
        "semester_start": "2026-08-24",
        "semester_end": "2026-12-09",
        "no_class_days": ["2026-10-13"],
    }
    base.update(over)
    return load_config(write_config(base), repo_root=tmp_repo)


def test_records_inside_the_session(write_config, tmp_repo, sample_config_dict, monkeypatch):
    from src.recorder import in_session

    cfg = _bounded(write_config, tmp_repo, sample_config_dict)
    assert in_session(cfg, datetime(2026, 9, 8, 10, 30)) is True


def test_does_not_record_before_the_session_starts(write_config, tmp_repo, sample_config_dict):
    """Regression: on 2026-08-20 the recorder would have recorded a Thursday
    class four days before the semester began."""
    cfg = _bounded(write_config, tmp_repo, sample_config_dict)
    assert tick(cfg, now=datetime(2026, 8, 20, 10, 30)) == "out-of-session"


def test_does_not_record_after_classes_end(write_config, tmp_repo, sample_config_dict):
    cfg = _bounded(write_config, tmp_repo, sample_config_dict)
    assert tick(cfg, now=datetime(2026, 12, 15, 10, 30)) == "out-of-session"


def test_does_not_record_next_semester(write_config, tmp_repo, sample_config_dict):
    cfg = _bounded(write_config, tmp_repo, sample_config_dict)
    assert tick(cfg, now=datetime(2027, 2, 9, 10, 30)) == "out-of-session"


def test_does_not_record_on_a_no_class_day(write_config, tmp_repo, sample_config_dict):
    cfg = _bounded(write_config, tmp_repo, sample_config_dict)
    assert tick(cfg, now=datetime(2026, 10, 13, 10, 30)) == "out-of-session"


def test_missing_bounds_stay_permissive(write_config, tmp_repo, sample_config_dict):
    """An unconfigured setup must behave as before, not silently stop."""
    from src.recorder import in_session

    cfg = _bounded(write_config, tmp_repo, sample_config_dict,
                   semester_start="", semester_end="", no_class_days=[])
    assert in_session(cfg, datetime(2030, 1, 1, 10, 0)) is True


def test_out_of_session_stops_a_running_capture(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    stopped = []
    cfg = _bounded(write_config, tmp_repo, sample_config_dict)
    monkeypatch.setattr("src.recorder.is_recording", lambda cfg: True)
    monkeypatch.setattr("src.recorder.stop_capture", lambda cfg, log: stopped.append(1))
    assert tick(cfg, now=datetime(2026, 10, 13, 10, 30)) == "out-of-session"
    assert stopped == [1]


def test_the_real_fall_2026_no_class_days_are_all_excluded(write_config, tmp_repo, sample_config_dict):
    """Every day the calendrier scolaire marks as having no regular classes."""
    from src.recorder import in_session

    days = ["2026-09-07", "2026-10-05", "2026-10-06", "2026-10-12",
            "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-11-11"]
    cfg = _bounded(write_config, tmp_repo, sample_config_dict, no_class_days=days)
    for day in days:
        when = datetime.fromisoformat(day + "T10:30:00")
        assert in_session(cfg, when) is False, day


# --- location-based at-school gate -------------------------------------------
# IP and SSID both break on a tethered connection or a campus Wi-Fi outage,
# which is exactly when you still want the class recorded.

CAMPUS = {"lat": 45.5365, "lon": -73.4943, "radius_m": 400}


def _gated(write_config, tmp_repo, mode, **over):
    base = {"at_school_check": mode, "school_location": CAMPUS}
    base.update(over)
    return load_config(write_config(base), repo_root=tmp_repo)


def test_distance_is_metres():
    from src.recorder import distance_m

    # one degree of latitude is ~111 km
    assert 110_000 < distance_m(45.0, -73.0, 46.0, -73.0) < 112_000
    assert distance_m(45.5365, -73.4943, 45.5365, -73.4943) == 0


def test_on_campus_records(write_config, tmp_repo, monkeypatch):
    cfg = _gated(write_config, tmp_repo, "location")
    monkeypatch.setattr("src.recorder.current_location", lambda *a, **k: (45.5366, -73.4934))
    assert at_school(cfg) is True


def test_at_home_does_not_record(write_config, tmp_repo, monkeypatch):
    # Any point outside the radius does. Never paste a real reading of
    # somewhere you actually sleep into a test: four decimals is an
    # eleven-metre square, and tests are the part of a repo people read.
    cfg = _gated(write_config, tmp_repo, "location")
    monkeypatch.setattr("src.recorder.current_location", lambda *a, **k: (45.0, -73.0))
    assert at_school(cfg) is False


def test_just_outside_the_radius_does_not_record(write_config, tmp_repo, monkeypatch):
    cfg = _gated(write_config, tmp_repo, "location")
    monkeypatch.setattr("src.recorder.current_location", lambda *a, **k: (45.5410, -73.4943))
    assert at_school(cfg) is False


def test_location_unavailable_does_not_record(write_config, tmp_repo, monkeypatch):
    """Permission denied must fail closed, not default to recording."""
    cfg = _gated(write_config, tmp_repo, "location")
    monkeypatch.setattr("src.recorder.current_location", lambda *a, **k: None)
    assert at_school(cfg) is False


def test_auto_records_when_tethered_if_location_says_campus(write_config, tmp_repo, monkeypatch):
    """The tethering case: IP is the carrier's, SSID is the phone's, but the
    Mac is physically at the cégep."""
    cfg = _gated(write_config, tmp_repo, "auto",
                 school_ssid="CAMPUS", school_ip_prefix="132.213.")
    monkeypatch.setattr("src.recorder.current_location", lambda *a, **k: (45.5366, -73.4934))
    monkeypatch.setattr("src.recorder.current_ssid", lambda: "Matt's Phone")
    monkeypatch.setattr("src.recorder.current_ip", lambda **k: "24.201.9.9")
    assert at_school(cfg) is True


def test_auto_records_on_campus_wifi_when_location_is_denied(write_config, tmp_repo, monkeypatch):
    cfg = _gated(write_config, tmp_repo, "auto", school_ssid="CAMPUS")
    monkeypatch.setattr("src.recorder.current_location", lambda *a, **k: None)
    monkeypatch.setattr("src.recorder.current_ssid", lambda: "CAMPUS")
    assert at_school(cfg) is True


def test_auto_at_home_records_nothing(write_config, tmp_repo, monkeypatch):
    cfg = _gated(write_config, tmp_repo, "auto",
                 school_ssid="CAMPUS", school_ip_prefix="132.213.")
    monkeypatch.setattr("src.recorder.current_location", lambda *a, **k: (45.0, -73.0))
    monkeypatch.setattr("src.recorder.current_ssid", lambda: "HOME-WIFI")
    monkeypatch.setattr("src.recorder.current_ip", lambda **k: "24.201.9.9")
    assert at_school(cfg) is False


def test_auto_with_nothing_evaluable_does_not_record(write_config, tmp_repo, monkeypatch):
    cfg = _gated(write_config, tmp_repo, "auto", school_location={"lat": 0, "lon": 0})
    monkeypatch.setattr("src.recorder.current_location", lambda *a, **k: None)
    monkeypatch.setattr("src.recorder.current_ssid", lambda: "")
    assert at_school(cfg) is False


def test_off_still_records_anywhere(write_config, tmp_repo):
    assert at_school(_gated(write_config, tmp_repo, "off")) is True


# --- class numbering: teachers count WEEKS, with a/b ------------------------


def _numbered(write_config, tmp_repo, sample_config_dict):
    courses = [dict(c) for c in sample_config_dict["courses"]]
    sched = [
        {"course": "601-101-MQ", "weekday": "thursday", "start": "08:10", "end": "10:00"},
        {"course": "601-101-MQ", "weekday": "friday", "start": "16:10", "end": "18:00"},
        {"course": "201-103-RE", "weekday": "friday", "start": "08:10", "end": "12:00"},
    ]
    return load_config(write_config({
        "courses": courses, "schedule": sched,
        "semester_start": "2026-08-24", "semester_end": "2026-12-09",
        "no_class_days": ["2026-10-16"],
    }), repo_root=tmp_repo)


def test_twice_weekly_course_gets_a_and_b(write_config, tmp_repo, sample_config_dict):
    from src.recorder import class_label

    cfg = _numbered(write_config, tmp_repo, sample_config_dict)
    assert class_label(cfg, "601-101-MQ", datetime(2026, 8, 27, 8, 10)) == "1a"
    assert class_label(cfg, "601-101-MQ", datetime(2026, 8, 28, 16, 10)) == "1b"
    assert class_label(cfg, "601-101-MQ", datetime(2026, 9, 3, 8, 10)) == "2a"


def test_once_weekly_course_gets_a_plain_number(write_config, tmp_repo, sample_config_dict):
    from src.recorder import class_label

    cfg = _numbered(write_config, tmp_repo, sample_config_dict)
    assert class_label(cfg, "201-103-RE", datetime(2026, 8, 28, 8, 10)) == "1"
    assert class_label(cfg, "201-103-RE", datetime(2026, 9, 4, 8, 10)) == "2"


def test_a_fully_empty_week_does_not_consume_a_number(
    write_config, tmp_repo, sample_config_dict
):
    """The real 12-16 Oct week has no classes at all, so it must not count.
    Verified live: Histoire runs 7 (9 Oct) -> 8 (23 Oct)."""
    from src.recorder import class_label

    courses = [dict(c) for c in sample_config_dict["courses"]]
    cfg = load_config(write_config({
        "courses": courses,
        "schedule": [
            {"course": "601-101-MQ", "weekday": "thursday", "start": "08:10", "end": "10:00"},
            {"course": "201-103-RE", "weekday": "friday", "start": "08:10", "end": "12:00"},
        ],
        "semester_start": "2026-08-24", "semester_end": "2026-12-09",
        "no_class_days": ["2026-10-12", "2026-10-13", "2026-10-14",
                          "2026-10-15", "2026-10-16"],
    }), repo_root=tmp_repo)
    before = class_label(cfg, "201-103-RE", datetime(2026, 10, 9, 8, 10))
    after = class_label(cfg, "201-103-RE", datetime(2026, 10, 23, 8, 10))
    assert int(after) == int(before) + 1, f"{before} -> {after} skipped a number"


def test_a_week_this_course_missed_does_not_consume_its_number(
    write_config, tmp_repo, sample_config_dict
):
    """A week where OTHER courses ran but this one did not must not take a
    number from this course.

    This asserted the opposite until 2026-08-21, when Latreille's plan de cours
    arrived and settled it: he calls 20 October "semaine 7", not 8, even though
    the 5-9 October week taught plenty of other courses. Recherche is
    Tuesday-only and Tuesday 6 October ran the Monday schedule, so his course
    simply did not meet. Teachers number their own sessions, and the number is
    only useful if it matches what he hears in class.
    """
    from src.recorder import class_label

    cfg = _numbered(write_config, tmp_repo, sample_config_dict)  # only 16 Oct excluded
    before = class_label(cfg, "201-103-RE", datetime(2026, 10, 9, 8, 10))
    after = class_label(cfg, "201-103-RE", datetime(2026, 10, 23, 8, 10))
    assert int(after) == int(before) + 1, "the skipped Friday must not cost a number"


def test_the_other_courses_that_week_keep_their_own_count(
    write_config, tmp_repo, sample_config_dict
):
    """The paired assertion: numbering is per course, so the Thursday course is
    unaffected by the Friday course missing a week."""
    from src.recorder import class_label

    cfg = _numbered(write_config, tmp_repo, sample_config_dict)
    thursday_before = class_label(cfg, "601-101-MQ", datetime(2026, 10, 8, 8, 10))
    thursday_after = class_label(cfg, "601-101-MQ", datetime(2026, 10, 15, 8, 10))
    assert int(thursday_after.rstrip("ab")) == int(thursday_before.rstrip("ab")) + 1


def test_label_is_none_outside_the_semester(write_config, tmp_repo, sample_config_dict):
    from src.recorder import class_label

    cfg = _numbered(write_config, tmp_repo, sample_config_dict)
    assert class_label(cfg, "601-101-MQ", datetime(2027, 2, 4, 8, 10)) is None


def test_recording_filename_carries_number_date_and_course(
    write_config, tmp_repo, sample_config_dict
):
    from src.recorder import recording_stem

    cfg = _numbered(write_config, tmp_repo, sample_config_dict)
    stem = recording_stem(cfg, cfg.course_by_code("601-101-MQ"), datetime(2026, 8, 27, 8, 10))
    assert stem.startswith("Cours 1a - 2026-08-27 - ")


def test_recordings_live_in_their_own_subfolder(write_config, tmp_repo, sample_config_dict):
    from src.recorder import recording_dir

    cfg = _numbered(write_config, tmp_repo, sample_config_dict)
    d = recording_dir(cfg, cfg.course_by_code("601-101-MQ"))
    assert d.name == "Voice"
    assert d.parent == cfg.folder_for(cfg.course_by_code("601-101-MQ"))


# --- what leaves the machine after a recording -------------------------------
#
# `upload_transcript` is a gate, not a preference. The audio is a recording of
# other people and is never queued for NotebookLM under any setting, including
# when transcription fails. These tests exist because that fallback used to ship
# the .m4a whenever there was no transcript.


def _finalize_faking_ffmpeg(monkeypatch, cfg, entry, now, *, transcript_body=None):
    """Run finalize() without ffmpeg, ffprobe or whisper.

    `transcript_body=None` makes transcription raise, which is the case that
    previously fell back to uploading the audio.
    """
    import logging
    import subprocess as sp

    import src.transcribe as transcribe_mod
    from src.recorder import finalize, session_dir

    target = session_dir(cfg, entry, now)
    target.mkdir(parents=True, exist_ok=True)
    (target / "chunk_100000_000.m4a").write_bytes(b"audio")

    def fake_run(cmd, *a, **k):
        if "concat" in cmd:                       # the ffmpeg concat call
            Path(cmd[-1]).write_bytes(b"joined audio")
            return sp.CompletedProcess(cmd, 0, "", "")
        return sp.CompletedProcess(cmd, 0, "3600.0\n", "")   # ffprobe duration

    monkeypatch.setattr("src.recorder.subprocess.run", fake_run)
    monkeypatch.setattr("src.recorder.find_ffmpeg", lambda: "/usr/bin/ffmpeg")

    def fake_transcribe(audio, **kwargs):
        if transcript_body is None:
            raise RuntimeError("whisper failed")
        md = audio.with_suffix(".md")
        md.write_text(transcript_body, encoding="utf-8")
        return md

    monkeypatch.setattr(transcribe_mod, "transcribe", fake_transcribe)
    monkeypatch.setattr(transcribe_mod, "transcript_header", lambda *a, **k: "")

    return finalize(cfg, entry, now, logging.getLogger("test"))


def _recorded_cfg(write_config, tmp_repo, sample_config_dict, **overrides):
    courses = [dict(c) for c in sample_config_dict["courses"]]
    courses[0]["record"] = True
    data = {
        "courses": courses,
        "schedule": [
            {"course": "601-101-MQ", "weekday": "thursday", "start": "08:10", "end": "10:00"}
        ],
        "semester_start": "2026-08-24",
        "semester_end": "2026-12-09",
        "transcribe": True,
    }
    data.update(overrides)
    return load_config(write_config(data), repo_root=tmp_repo)


def _queue(cfg):
    import json

    path = cfg.repo_root / "state" / "upload_queue.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


ENTRY = ScheduleEntry("601-101-MQ", "thursday", dtime(8, 10), dtime(10, 0))
WHEN = datetime(2026, 8, 27, 10, 5)


def test_upload_transcript_queues_the_text_never_the_audio(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict, upload_transcript=True)
    out = _finalize_faking_ffmpeg(monkeypatch, cfg, ENTRY, WHEN, transcript_body="bonjour")

    queued = _queue(cfg)
    assert len(queued) == 1
    assert queued[0]["path"].endswith(".md")
    assert not queued[0]["path"].endswith(".m4a")
    assert out.suffix == ".m4a" and out.exists()   # audio still kept, just locally


def test_upload_transcript_false_queues_nothing_rather_than_the_audio(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict, upload_transcript=False)
    out = _finalize_faking_ffmpeg(monkeypatch, cfg, ENTRY, WHEN, transcript_body="bonjour")

    assert _queue(cfg) == []
    assert out.exists()


def test_failed_transcription_queues_nothing_rather_than_the_audio(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict, upload_transcript=True)
    out = _finalize_faking_ffmpeg(monkeypatch, cfg, ENTRY, WHEN, transcript_body=None)

    assert _queue(cfg) == []
    assert out.exists()


def test_transcription_disabled_queues_nothing(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    cfg = _recorded_cfg(
        write_config, tmp_repo, sample_config_dict, transcribe=False, upload_transcript=True
    )
    _finalize_faking_ffmpeg(monkeypatch, cfg, ENTRY, WHEN, transcript_body="unused")

    assert _queue(cfg) == []


# --- where recordings are written --------------------------------------------


def test_absolute_recordings_folder_escapes_base_path(
    write_config, tmp_repo, sample_config_dict, tmp_path
):
    """base_path can sit inside a cloud-synced tree; recordings must not have to."""
    from src.recorder import recording_dir

    elsewhere = tmp_path / "Recordings" / "Cegep"
    cfg = load_config(
        write_config({"recordings_folder": str(elsewhere)}), repo_root=tmp_repo
    )
    course = cfg.course_by_code("601-101-MQ")
    d = recording_dir(cfg, course)

    assert d == elsewhere / course.folder
    assert cfg.base_path not in d.parents


def test_tilde_recordings_folder_is_treated_as_absolute(
    write_config, tmp_repo, sample_config_dict
):
    from src.recorder import recording_dir

    cfg = load_config(
        write_config({"recordings_folder": "~/Recordings/Cegep"}), repo_root=tmp_repo
    )
    course = cfg.course_by_code("601-101-MQ")
    d = recording_dir(cfg, course)

    assert d.is_absolute()
    assert "~" not in str(d)
    assert d == Path.home() / "Recordings" / "Cegep" / course.folder


# The autouse _never_spawn_ffmpeg guard replaces src.recorder.start_capture, so
# grab the real one at import time (fixtures run later, per test). These tests
# still spawn nothing: subprocess.Popen is mocked in each of them.
import src.recorder as _rec_mod

_REAL_START_CAPTURE = _rec_mod.start_capture


# --- a class that captured nothing must not look like a class that went fine ---
#
# This is the recorder's worst failure mode: silence in every channel. The
# launchd agent is a different TCC subject from the Terminal that `make
# mic-test` grants, so a denied microphone is the DEFAULT state of a fresh
# install, and it used to produce an empty folder and a "finalized" log line.


def test_empty_session_notifies_and_keeps_the_evidence(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    import logging

    from src.recorder import finalize, session_dir

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    target = session_dir(cfg, ENTRY, WHEN)
    target.mkdir(parents=True, exist_ok=True)
    (target / "chunk_100000_000.m4a").write_bytes(b"")          # zero bytes
    (target / "ffmpeg.log").write_text(
        "[avfoundation @ 0x1] Failed to open device: Operation not permitted\n",
        encoding="utf-8",
    )

    sent = []
    monkeypatch.setattr("src.recorder.notify", lambda t, m, **k: sent.append((t, m, k)))

    assert finalize(cfg, ENTRY, WHEN, logging.getLogger("test")) is None
    assert target.exists(), "session dir holds the only evidence; must survive"

    assert len(sent) == 1
    title, message, kwargs = sent[0]
    assert kwargs["critical"] is True
    assert kwargs.get("cfg") is not None, "a mid-class failure must reach the phone"
    assert "Operation not permitted" in message


def test_tick_does_not_report_finalized_when_nothing_was_recorded(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    from src.recorder import session_dir, tick

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    session_dir(cfg, ENTRY, WHEN).mkdir(parents=True, exist_ok=True)   # no chunks

    monkeypatch.setattr("src.recorder.is_recording", lambda cfg: False)
    monkeypatch.setattr("src.recorder.at_school", lambda cfg, **k: True)
    monkeypatch.setattr("src.recorder.notify", lambda *a, **k: None)

    # 10:05 is past the 10:00 end, inside the grace window: the finalize branch.
    assert tick(cfg, now=WHEN, logger=None) == "finalize-empty"


def test_capture_that_dies_at_once_writes_no_pid_and_says_why(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """A dead ffmpeg used to leave a pid file, so is_recording() said 'yes' and
    the next tick returned 'recording' for the rest of the class."""
    import logging

    from src.recorder import pid_file, session_dir

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)

    def instant_death(argv, err_path, plist_path, **k):
        """launchd accepted the job and it was gone before it reported a pid."""
        err_path.parent.mkdir(parents=True, exist_ok=True)
        err_path.write_text(
            "[avfoundation] Failed to open device: Operation not permitted\n",
            encoding="utf-8",
        )
        return None

    monkeypatch.setattr("src.micapp.start_capture_job", instant_death)
    monkeypatch.setattr("src.micapp.stop_capture_job", lambda *a, **k: None)
    monkeypatch.setattr("src.micapp.capture_binary", lambda root, fallback: fallback)
    monkeypatch.setattr("src.recorder.find_ffmpeg", lambda: "/usr/bin/ffmpeg")

    sent = []
    monkeypatch.setattr("src.recorder.notify", lambda t, m, **k: sent.append((t, m, k)))

    _REAL_START_CAPTURE(cfg, ENTRY, WHEN, logging.getLogger("test"))

    assert not pid_file(cfg).exists(), "a dead capture must not look live"
    assert (session_dir(cfg, ENTRY, WHEN) / "capture-failed").exists()
    assert len(sent) == 1 and sent[0][2]["critical"] is True
    assert "Operation not permitted" in sent[0][1]


def test_repeated_ticks_notify_once_per_class_not_once_per_tick(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """Retrying is deliberate: granting the mic mid-class then just works.
    Re-notifying every 5 minutes for two hours is not."""
    import logging

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)

    monkeypatch.setattr("src.micapp.start_capture_job", lambda *a, **k: None)
    monkeypatch.setattr("src.micapp.stop_capture_job", lambda *a, **k: None)
    monkeypatch.setattr("src.micapp.capture_binary", lambda root, fallback: fallback)
    monkeypatch.setattr("src.recorder.find_ffmpeg", lambda: "/usr/bin/ffmpeg")

    sent = []
    monkeypatch.setattr("src.recorder.notify", lambda t, m, **k: sent.append(t))

    log = logging.getLogger("test")
    for _ in range(5):
        _REAL_START_CAPTURE(cfg, ENTRY, WHEN, log)

    assert len(sent) == 1


# --------------------------------------------------------------------------
# What lands in Voice/ has to be listenable, and a silent class has to be loud
# --------------------------------------------------------------------------


def _real_ffmpeg() -> str | None:
    import shutil as sh

    return sh.which("ffmpeg")


def _write_chunk(path: Path, source: str, gain: str = "", seconds: int = 3) -> None:
    """Render a real AAC chunk from an ffmpeg source, optionally attenuated."""
    import subprocess as sp

    cmd = [_real_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"{source}:d={seconds}"]
    if gain:
        cmd += ["-af", f"volume={gain}"]
    cmd += ["-ac", "1", "-c:a", "aac", "-b:a", "64k", "-y", str(path)]
    sp.run(cmd, check=True, capture_output=True, timeout=120)


def _finalize_for_real(monkeypatch, cfg, entry, now, chunk_source, gain=""):
    """finalize() with real ffmpeg, faking only whisper and the notifications."""
    import logging

    import src.transcribe as transcribe_mod
    from src.recorder import finalize, session_dir

    target = session_dir(cfg, entry, now)
    target.mkdir(parents=True, exist_ok=True)
    for index in range(2):
        _write_chunk(target / f"chunk_100000_{index:03d}.m4a", chunk_source, gain)

    monkeypatch.setattr(transcribe_mod, "transcribe", lambda audio, **k: None)
    monkeypatch.setattr(transcribe_mod, "transcript_header", lambda *a, **k: "")

    sent = []
    monkeypatch.setattr("src.recorder.notify", lambda t, m, **k: sent.append((t, m, k)))
    out = finalize(cfg, entry, now, logging.getLogger("test"))
    return out, sent


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_the_saved_lecture_is_loud_enough_to_play_back(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """A lecture hall from the third row lands near -47 dBFS mean. That is
    transcribable once lifted and far too quiet to LISTEN to, and the audio is
    what gets checked when the transcript reads oddly. Measured on the real
    2026-08-26 recording: -35.8 dB mean as captured, -19.8 dB as saved.
    """
    from src.transcribe import peak_db

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    out, _ = _finalize_for_real(
        monkeypatch, cfg, ENTRY, WHEN, "sine=frequency=300:sample_rate=44100", gain="-32dB",
    )
    assert out is not None and out.exists()

    # Compared against the input rather than an absolute target, so retuning
    # loudnorm does not break the test. What must not regress is that a quiet
    # capture comes out of finalize dramatically louder than it went in.
    lift = peak_db(out) - (-32.0)
    assert lift > 15, f"the saved file was only lifted by {lift:.1f} dB"


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_a_silent_class_is_reported_and_not_amplified(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """The 2026-08-26 failure, from the other side.

    A denied microphone gives ffmpeg zeroes, so the file is the right size and
    the right length with no sound in it, and every layer reported success.
    Two things have to happen: say so loudly, and do NOT normalise, because
    raising an empty file to speaking volume hands whisper a lecture of hiss and
    whisper answers hiss with fluent invented French.
    """
    from src.transcribe import SILENT_PEAK_DB, peak_db

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    out, sent = _finalize_for_real(
        monkeypatch, cfg, ENTRY, WHEN, "anullsrc=r=44100:cl=mono",
    )
    assert out is not None and out.exists()
    assert peak_db(out) <= SILENT_PEAK_DB, "digital silence must not be amplified"
    assert any(k.get("critical") for _, _, k in sent), "a silent class must notify loudly"


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_a_shorter_final_pass_does_not_destroy_the_live_transcript(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """The live pass is not reliably the worse of the two.

    Measured on ten minutes of the 2026-08-26 lecture, recorded from the
    furthest seat in the room: the live pass recovered about 400 real words and
    the final pass about 250. The final slices by detected language run and by
    silence, and on distant, gappy speech those cuts land badly. Both write to
    the same path, so the better transcript used to be overwritten with no
    trace it had existed.
    """
    import logging

    import src.transcribe as transcribe_mod
    from src.recorder import finalize, live_path_for, session_dir

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    course = cfg.course_by_code(ENTRY.course)

    target = session_dir(cfg, ENTRY, WHEN)
    target.mkdir(parents=True, exist_ok=True)
    _write_chunk(target / "chunk_100000_000.m4a", "sine=frequency=300:sample_rate=44100")

    live = live_path_for(cfg, course, WHEN)
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("# En cours\n\n" + " ".join(["mot"] * 400), encoding="utf-8")

    def thin_final(audio, **kwargs):
        md = audio.with_suffix(".md")
        md.write_text("# Cours\n\n" + " ".join(["mot"] * 250), encoding="utf-8")
        return md

    monkeypatch.setattr(transcribe_mod, "transcribe", thin_final)
    monkeypatch.setattr(transcribe_mod, "transcript_header", lambda *a, **k: "")
    monkeypatch.setattr("src.recorder.notify", lambda *a, **k: None)

    finalize(cfg, ENTRY, WHEN, logging.getLogger("test"))

    kept = list(live.parent.glob("*(en direct).md"))
    assert kept, "the longer live transcript was destroyed"
    assert len(kept[0].read_text(encoding="utf-8").split()) > 300


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_a_better_final_pass_leaves_no_clutter_behind(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """In the ordinary case the final pass wins and a second .md in Voice/ is
    noise. Only keep the live one when it is the record that survived."""
    import logging

    import src.transcribe as transcribe_mod
    from src.recorder import finalize, live_path_for, session_dir

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    course = cfg.course_by_code(ENTRY.course)

    target = session_dir(cfg, ENTRY, WHEN)
    target.mkdir(parents=True, exist_ok=True)
    _write_chunk(target / "chunk_100000_000.m4a", "sine=frequency=300:sample_rate=44100")

    live = live_path_for(cfg, course, WHEN)
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("# En cours\n\n" + " ".join(["mot"] * 100), encoding="utf-8")

    def full_final(audio, **kwargs):
        md = audio.with_suffix(".md")
        md.write_text("# Cours\n\n" + " ".join(["mot"] * 900), encoding="utf-8")
        return md

    monkeypatch.setattr(transcribe_mod, "transcribe", full_final)
    monkeypatch.setattr(transcribe_mod, "transcript_header", lambda *a, **k: "")
    monkeypatch.setattr("src.recorder.notify", lambda *a, **k: None)

    finalize(cfg, ENTRY, WHEN, logging.getLogger("test"))
    assert not list(live.parent.glob("*(en direct).md"))


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_a_failed_final_pass_promotes_the_live_transcript(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """Transcription failing used to leave the class with no text at all, even
    though a live transcript had been sitting there the whole time."""
    import logging

    import src.transcribe as transcribe_mod
    from src.recorder import finalize, live_path_for, session_dir

    cfg = _recorded_cfg(
        write_config, tmp_repo, sample_config_dict, upload_transcript=True
    )
    course = cfg.course_by_code(ENTRY.course)

    target = session_dir(cfg, ENTRY, WHEN)
    target.mkdir(parents=True, exist_ok=True)
    _write_chunk(target / "chunk_100000_000.m4a", "sine=frequency=300:sample_rate=44100")

    live = live_path_for(cfg, course, WHEN)
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("# En cours\n\nvraiment dit en classe", encoding="utf-8")

    def boom(audio, **kwargs):
        raise RuntimeError("whisper failed")

    monkeypatch.setattr(transcribe_mod, "transcribe", boom)
    monkeypatch.setattr(transcribe_mod, "transcript_header", lambda *a, **k: "")
    monkeypatch.setattr("src.recorder.notify", lambda *a, **k: None)

    finalize(cfg, ENTRY, WHEN, logging.getLogger("test"))

    queued = _queue(cfg)
    assert len(queued) == 1 and queued[0]["path"].endswith("(en direct).md")


# --------------------------------------------------------------------------
# Orphaned captures: the lid-closing failure
# --------------------------------------------------------------------------


def _orphan(cfg, course_code: str, day: str, chunks: int = 3) -> Path:
    """A session directory nothing is writing to, with real chunks in it."""
    folder = cfg.repo_root / "state" / "rec_tmp" / f"{day}_{course_code}"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(chunks):
        (folder / f"chunk_080000_{i:03d}.m4a").write_bytes(b"x" * 5000)
    return folder


def test_an_orphaned_capture_is_filed_on_the_next_tick(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """The 2026-08-27 failure. A capture started at 08:34 was still running at
    13:06, through the end of one class and the whole of the next, because every
    tick that would have closed it happened while the Mac was asleep. Both
    course folders were empty and 181 chunks sat in state/rec_tmp with nothing
    looking for them.
    """
    import logging

    from src.recorder import sweep_orphans

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    folder = _orphan(cfg, "601-101-MQ", "2026-08-27")

    filed = []
    monkeypatch.setattr(
        "src.recorder.finalize",
        lambda c, e, w, l: filed.append((e.course, w)) or Path("out.m4a"),
    )
    out = sweep_orphans(cfg, datetime(2026, 8, 27, 15, 0), logging.getLogger("test"))

    assert len(filed) == 1, "the orphaned capture was not filed"
    assert filed[0][0] == "601-101-MQ"
    assert filed[0][1].date() == date(2026, 8, 27), "filed under the wrong day"
    assert len(out) == 1


def test_the_sweep_never_touches_the_capture_in_progress(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """Finalising a live capture would concatenate a class that is still being
    recorded and delete the directory out from under ffmpeg."""
    import logging

    from src.recorder import pid_file, session_dir, sweep_orphans

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    now = datetime(2026, 8, 27, 9, 0)          # inside the configured class
    live = session_dir(cfg, ENTRY, now)
    live.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (live / f"chunk_080000_{i:03d}.m4a").write_bytes(b"x" * 5000)
    pid_file(cfg).write_text(str(os.getpid()), encoding="utf-8")

    filed = []
    monkeypatch.setattr("src.recorder.finalize", lambda c, e, w, l: filed.append(e) or None)
    sweep_orphans(cfg, now, logging.getLogger("test"))
    assert filed == [], "the sweep finalised the capture that was still running"


def test_the_sweep_ignores_empty_and_unparseable_directories(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """A session directory is kept after a failed capture precisely so its
    ffmpeg.log survives. Filing it would be filing nothing."""
    import logging

    from src.recorder import sweep_orphans

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    root = cfg.repo_root / "state" / "rec_tmp"
    (root / "2026-08-27_601-101-MQ").mkdir(parents=True)      # no chunks
    (root / "2026-08-27_601-101-MQ" / "ffmpeg.log").write_text("failed")
    (root / "salvage_whatever").mkdir(parents=True)           # not a session name
    (root / "not-a-date_601-101-MQ").mkdir(parents=True)

    filed = []
    monkeypatch.setattr("src.recorder.finalize", lambda c, e, w, l: filed.append(e) or None)
    assert sweep_orphans(cfg, datetime(2026, 8, 27, 15, 0), logging.getLogger("test")) == []
    assert filed == []


def test_one_bad_session_does_not_block_the_others(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    import logging

    from src.recorder import sweep_orphans

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    _orphan(cfg, "601-101-MQ", "2026-08-25")
    _orphan(cfg, "601-101-MQ", "2026-08-27")

    seen = []

    def flaky(c, e, w, l):
        seen.append(w.date())
        if w.date() == date(2026, 8, 25):
            raise RuntimeError("concat exploded")
        return Path("out.m4a")

    monkeypatch.setattr("src.recorder.finalize", flaky)
    out = sweep_orphans(cfg, datetime(2026, 8, 27, 15, 0), logging.getLogger("test"))
    assert len(seen) == 2, "the sweep stopped at the first failure"
    assert len(out) == 1


# --------------------------------------------------------------------------
# The at-school gate: cost, order, and what it cost on 2026-08-27
# --------------------------------------------------------------------------


def _gate_cfg(write_config, tmp_repo, sample_config_dict, **over):
    return _recorded_cfg(
        write_config, tmp_repo, sample_config_dict,
        at_school_check="auto", school_ip_prefix="203.0.113.",
        school_ssid="", **over,
    )


def test_the_cheap_signal_is_asked_first_and_stops_the_rest(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """CoreLocationCLI can block for 25 seconds. If the IP already said yes,
    nothing should ever wait on it."""
    from src import recorder

    cfg = _gate_cfg(write_config, tmp_repo, sample_config_dict)
    asked = []
    monkeypatch.setattr(recorder, "_by_ip", lambda c, l: asked.append("ip") or True)
    monkeypatch.setattr(recorder, "_by_ssid", lambda c, l: asked.append("ssid") or None)
    monkeypatch.setattr(recorder, "_by_location", lambda c, l: asked.append("location") or True)

    assert recorder.at_school(cfg) is True
    assert asked == ["ip"], f"asked more than it needed to: {asked}"


def test_location_still_answers_when_the_network_cannot(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """Tethering, or the campus Wi-Fi being down. Location is slow, not useless."""
    from src import recorder

    cfg = _gate_cfg(write_config, tmp_repo, sample_config_dict)
    monkeypatch.setattr(recorder, "_by_ip", lambda c, l: None)
    monkeypatch.setattr(recorder, "_by_ssid", lambda c, l: None)
    monkeypatch.setattr(recorder, "_by_location", lambda c, l: True)
    assert recorder.at_school(cfg) is True


def test_no_signal_at_all_still_refuses(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """The 2026-08-27 morning: every signal unavailable. Recording a private
    conversation on a guess is worse than missing a lecture, so it refuses."""
    from src import recorder

    cfg = _gate_cfg(write_config, tmp_repo, sample_config_dict)
    for name in ("_by_ip", "_by_ssid", "_by_location"):
        monkeypatch.setattr(recorder, name, lambda c, l: None)
    assert recorder.at_school(cfg) is False


def test_a_definite_no_is_not_overridden_by_an_unavailable_signal(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    from src import recorder

    cfg = _gate_cfg(write_config, tmp_repo, sample_config_dict)
    monkeypatch.setattr(recorder, "_by_ip", lambda c, l: False)
    monkeypatch.setattr(recorder, "_by_ssid", lambda c, l: None)
    monkeypatch.setattr(recorder, "_by_location", lambda c, l: False)
    assert recorder.at_school(cfg) is False


def test_the_public_ip_is_cached_between_ticks(monkeypatch):
    """The gate now runs once a minute during class hours. Without a cache that
    is an HTTP request every minute for the length of every lecture."""
    from src import recorder

    monkeypatch.setattr(recorder, "_IP_CACHE", (0.0, ""))
    calls = []

    class FakeResponse:
        def read(self):
            calls.append(1)
            return b"203.0.113.221"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(recorder.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    assert recorder.current_ip() == "203.0.113.221"
    assert recorder.current_ip() == "203.0.113.221"
    assert len(calls) == 1, "the second lookup went to the network"


def test_the_tick_runs_every_minute():
    """Five minutes was chosen when the gate could block for 25 s. It now costs
    about 0.02 s, and five minutes is the difference between catching a class
    you walked into late and losing the start of it for good."""
    import plistlib
    from pathlib import Path as P

    tpl = P(__file__).resolve().parents[1] / "launchd" / "com.school.recorder.plist.template"
    data = plistlib.loads(tpl.read_bytes())
    assert data["StartInterval"] <= 60


def test_no_alarm_for_a_class_that_is_already_recorded(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """This used to fire "Recherche is NOT being recorded" minutes after that
    lecture had been finalised and transcribed, because by then the gate had
    correctly noticed the Mac was no longer on campus. An alarm that fires
    after the fact teaches you to ignore the alarm."""
    import logging

    from src.recorder import _warn_gated, recording_dir, recording_stem

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    course = cfg.course_by_code(ENTRY.course)
    folder = recording_dir(cfg, course)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{recording_stem(cfg, course, WHEN)}.m4a").write_bytes(b"a recording")

    sent = []
    monkeypatch.setattr("src.recorder.notify", lambda t, m, **k: sent.append(t))
    _warn_gated(cfg, ENTRY, WHEN, logging.getLogger("test"))
    assert sent == []


def test_the_alarm_still_fires_when_nothing_was_recorded(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    import logging

    from src.recorder import _warn_gated

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    sent = []
    monkeypatch.setattr("src.recorder.notify", lambda t, m, **k: sent.append((t, k)))
    _warn_gated(cfg, ENTRY, WHEN, logging.getLogger("test"))
    # What this test guards is that it speaks AT ALL: it used to fail closed in
    # silence, which is how the Recherche lecture of 2026-08-27 was lost. How
    # loudly is a separate question, settled quietly in the test below.
    assert len(sent) == 1


# --------------------------------------------------------------------------
# Keeping the evidence
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_a_minute_of_untreated_audio_survives_finalize(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """finalize() normalises in place and then deletes the session directory.

    Asked on 2026-08-28 what the processing had done to that morning's lecture,
    the only honest answer was that the evidence had been deleted at noon. The
    treated file is not enough: you cannot un-normalise it.
    """
    import logging

    import src.transcribe as transcribe_mod
    from src.recorder import finalize, session_dir

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    target = session_dir(cfg, ENTRY, WHEN)
    target.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        _write_chunk(target / f"chunk_100000_{i:03d}.m4a",
                     "sine=frequency=300:sample_rate=44100", gain="-30dB")

    monkeypatch.setattr(transcribe_mod, "transcribe", lambda a, **k: None)
    monkeypatch.setattr(transcribe_mod, "transcript_header", lambda *a, **k: "")
    monkeypatch.setattr("src.recorder.notify", lambda *a, **k: None)
    finalize(cfg, ENTRY, WHEN, logging.getLogger("test"))

    samples = list((cfg.repo_root / "state" / "raw-samples").glob("*.m4a"))
    assert samples, "no untreated sample was kept"
    assert samples[0].stat().st_size > 0


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_the_sample_is_untreated(write_config, tmp_repo, sample_config_dict, monkeypatch):
    """It has to be the raw audio. A normalised sample answers no question at
    all, since the finished recording is already normalised."""
    import logging

    import src.transcribe as transcribe_mod
    from src.recorder import finalize, session_dir
    from src.transcribe import peak_db

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    target = session_dir(cfg, ENTRY, WHEN)
    target.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        _write_chunk(target / f"chunk_100000_{i:03d}.m4a",
                     "sine=frequency=300:sample_rate=44100", gain="-30dB")

    monkeypatch.setattr(transcribe_mod, "transcribe", lambda a, **k: None)
    monkeypatch.setattr(transcribe_mod, "transcript_header", lambda *a, **k: "")
    monkeypatch.setattr("src.recorder.notify", lambda *a, **k: None)
    out = finalize(cfg, ENTRY, WHEN, logging.getLogger("test"))

    sample = next((cfg.repo_root / "state" / "raw-samples").glob("*.m4a"))
    assert peak_db(sample) < peak_db(out) - 10, "the sample looks normalised"


def test_only_the_last_few_classes_are_kept(tmp_path, monkeypatch):
    """One minute per class is nothing; a semester of them is still pointless
    to hoard, and this lives in state/ where nobody prunes by hand."""
    from src.recorder import RAW_SAMPLE_KEEP

    assert 3 <= RAW_SAMPLE_KEEP <= 20


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_a_short_class_still_gets_a_sample(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """The sample is taken ten minutes in. Seeking past the end of a shorter
    recording does NOT fail: ffmpeg returns 0 and writes a valid container with
    nothing in it, about a kilobyte of headers, so neither the return code nor
    a zero-size check notices. Only the duration does.
    """
    import logging

    import src.transcribe as transcribe_mod
    from src.recorder import finalize, session_dir
    from src.transcribe import _duration

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    target = session_dir(cfg, ENTRY, WHEN)
    target.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        _write_chunk(target / f"chunk_100000_{i:03d}.m4a",
                     "sine=frequency=300:sample_rate=44100", gain="-30dB")

    monkeypatch.setattr(transcribe_mod, "transcribe", lambda a, **k: None)
    monkeypatch.setattr(transcribe_mod, "transcript_header", lambda *a, **k: "")
    monkeypatch.setattr("src.recorder.notify", lambda *a, **k: None)
    finalize(cfg, ENTRY, WHEN, logging.getLogger("test"))

    sample = next((cfg.repo_root / "state" / "raw-samples").glob("*.m4a"))
    assert _duration(sample) > 1.0, "the sample is an empty container"


# --------------------------------------------------------------------------
# Hearing the treatment on your own microphone
# --------------------------------------------------------------------------


def _fake_mic(monkeypatch, *, gain="-30dB", freq=440):
    """Swap the avfoundation capture for a synthesised tone of the same length.

    Only the capture call is faked. Everything after it, the two re-encodes and
    the level probes, runs for real, which is the part worth testing.
    """
    import subprocess

    import src.recorder as rec

    real_run = subprocess.run
    seen: list[list[str]] = []

    def fake_run(cmd, *a, **kw):
        if isinstance(cmd, list):
            seen.append(list(cmd))
        if isinstance(cmd, list) and "avfoundation" in cmd:
            i = cmd.index("-i")
            secs = cmd[cmd.index("-t") + 1] if "-t" in cmd else "5"
            swapped = cmd[: i - 2] + [
                "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:d={secs}",
                "-af", f"volume={gain}",
            ] + cmd[i + 2 :]
            return real_run(swapped, *a, **kw)
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(rec.subprocess, "run", fake_run)
    monkeypatch.setattr(rec, "resolve_microphone", lambda: ":0")
    return seen


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_sound_check_keeps_both_versions(tmp_path, monkeypatch):
    """The whole point is that you end up holding the before AND the after.

    The finished lecture cannot tell you what the processing did, because
    loudnorm normalises to a target: a class that started at -49 dB and one that
    started at -20 dB both land at -18. Only a kept 'before' answers it.
    """
    import src.recorder as rec

    monkeypatch.setattr(rec, "REPO_ROOT", tmp_path)
    _fake_mic(monkeypatch)

    assert rec.sound_check(seconds=2) == 0
    made = sorted((tmp_path / "state" / "sound-check").glob("*.m4a"))
    names = " ".join(f.name for f in made)
    assert "avant" in names and "apres" in names and "capture" in names


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_the_two_clips_differ_only_by_the_filter(tmp_path, monkeypatch):
    """Both listening clips are re-encoded from the same capture at the same
    bitrate. If the 'before' were left at the capture bitrate instead, half of
    what you heard would be the codec, not the treatment."""
    import src.recorder as rec
    from src.transcribe import peak_db

    monkeypatch.setattr(rec, "REPO_ROOT", tmp_path)
    seen = _fake_mic(monkeypatch, gain="-30dB")

    assert rec.sound_check(seconds=2) == 0
    folder = tmp_path / "state" / "sound-check"
    before = next(folder.glob("*-1-avant.m4a"))
    after = next(folder.glob("*-2-apres.m4a"))

    assert peak_db(after) > peak_db(before) + 10, "the treatment did nothing audible"

    # Not the bitrate of the OUTPUT: AAC spends fewer bits on quiet material, so
    # the untreated clip lands well under its own setting and always would. What
    # has to match is the setting the two encodes were asked for.
    encodes = [c for c in seen if "-b:a" in c and "avfoundation" not in c]
    assert len(encodes) == 2
    assert {c[c.index("-b:a") + 1] for c in encodes} == {rec.LISTENING_BITRATE}
    assert sum("-af" in c for c in encodes) == 1, "the filter is not the only difference"


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_a_denied_microphone_does_not_produce_a_fake_comparison(tmp_path, monkeypatch):
    """A microphone macOS has denied writes a file of the right length holding
    nothing. Treating that silence and playing it back would look like a working
    sound check and teach him the wrong thing about his setup."""
    import src.recorder as rec

    monkeypatch.setattr(rec, "REPO_ROOT", tmp_path)
    _fake_mic(monkeypatch, gain="-120dB")

    assert rec.sound_check(seconds=2) == 2
    folder = tmp_path / "state" / "sound-check"
    assert not list(folder.glob("*avant*")), "it built a comparison out of silence"


# --------------------------------------------------------------------------
# Audio that never arrives
# --------------------------------------------------------------------------


def test_the_capture_does_not_throw_away_late_audio(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """avfoundation discards buffers that arrive later than expected, by
    default, and calls it correct behaviour. On a laptop also running a whisper
    pass every seventy seconds it fires constantly: the history class of
    2026-08-28 lost 28.9 minutes in 78,931 separate drops, and every class this
    semester lost between 9 and 17 percent the same way, silently."""
    import logging

    from src.recorder import CAPTURE_QUEUE

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    seen: list[list[str]] = []

    # The capture is handed to launchd through the signed mic app, not spawned
    # directly, so this is the seam that carries the command.
    monkeypatch.setattr("src.micapp.start_capture_job",
                        lambda cmd, *a, **k: seen.append(list(cmd)) or 4242)
    monkeypatch.setattr("src.recorder.resolve_microphone", lambda *a, **k: ":0")
    monkeypatch.setattr("src.recorder.subprocess.Popen", lambda *a, **k: _FakeProc())
    _REAL_START_CAPTURE(cfg, ENTRY, WHEN, logging.getLogger("test"))

    cmd = next((c for c in seen if "avfoundation" in c), None)
    assert cmd is not None, "no capture was launched"
    assert cmd[cmd.index("-drop_late_frames") + 1] == "false"
    assert int(cmd[cmd.index("-thread_queue_size") + 1]) == CAPTURE_QUEUE
    # Both belong to the INPUT. After -i they configure nothing at all.
    assert cmd.index("-drop_late_frames") < cmd.index("-i")
    assert cmd.index("-thread_queue_size") < cmd.index("-i")


class _FakeProc:
    pid = 4242

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_audio_seconds_counts_what_is_there_not_what_is_claimed(tmp_path):
    """The two numbers agree on a healthy file. They are only allowed to
    disagree when audio is genuinely missing, which is the whole point."""
    import subprocess

    from src.transcribe import _duration, audio_seconds

    src = tmp_path / "ok.m4a"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=300:sample_rate=48000:d=12", "-ac", "1",
         "-c:a", "aac", "-b:a", "64k", "-y", str(src)],
        check=True, capture_output=True,
    )
    assert abs(audio_seconds(src) - 12.0) < 0.3
    assert abs(audio_seconds(src) - _duration(src)) < 0.3


def test_audio_seconds_says_nothing_rather_than_something_wrong(tmp_path):
    """It runs inside finalize, next to a recording that cannot be redone. A
    file it cannot read must return 0.0 and be ignored, never raise."""
    from src.transcribe import audio_seconds

    junk = tmp_path / "not-audio.m4a"
    junk.write_bytes(b"this is not a media file")
    assert audio_seconds(junk) == 0.0
    assert audio_seconds(tmp_path / "missing.m4a") == 0.0


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_a_short_recording_says_so_instead_of_filing_quietly(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """Losing a fifth of a lecture used to be completely silent.

    ffmpeg exits clean, the file plays, the transcript reads normally, and the
    header quotes the container, which is stamped from the device clock and so
    describes the length of the CLASS rather than the length of the recording.
    The history class of 2026-08-28 filed itself as 201 minutes over a file
    holding 172 and nothing anywhere said a word.
    """
    import logging

    import src.transcribe as transcribe_mod
    from src.recorder import finalize, session_dir

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    target = session_dir(cfg, ENTRY, WHEN)
    target.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        _write_chunk(target / f"chunk_100000_{i:03d}.m4a",
                     "sine=frequency=300:sample_rate=44100")

    told: list[tuple] = []
    monkeypatch.setattr(transcribe_mod, "transcribe", lambda a, **k: None)
    monkeypatch.setattr(transcribe_mod, "transcript_header", lambda *a, **k: "")
    monkeypatch.setattr("src.recorder.notify",
                        lambda title, body, **k: told.append((title, body)))
    # A recording that claims 100 minutes and holds 80.
    monkeypatch.setattr(transcribe_mod, "_duration", lambda p: 6000.0)
    monkeypatch.setattr(transcribe_mod, "audio_seconds", lambda p: 4800.0)

    finalize(cfg, ENTRY, WHEN, logging.getLogger("test"))
    assert any("incomplet" in t.lower() for t, _ in told), f"nobody was told: {told}"
    assert any("20 min" in b for _, b in told), told


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_a_healthy_recording_is_not_nagged_about(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """The two numbers never agree to the last sample. A warning on every class
    would be worth exactly as much as no warning at all."""
    import logging

    import src.transcribe as transcribe_mod
    from src.recorder import finalize, session_dir

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    target = session_dir(cfg, ENTRY, WHEN)
    target.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        _write_chunk(target / f"chunk_100000_{i:03d}.m4a",
                     "sine=frequency=300:sample_rate=44100")

    told: list[tuple] = []
    monkeypatch.setattr(transcribe_mod, "transcribe", lambda a, **k: None)
    monkeypatch.setattr(transcribe_mod, "transcript_header", lambda *a, **k: "")
    monkeypatch.setattr("src.recorder.notify",
                        lambda title, body, **k: told.append((title, body)))
    monkeypatch.setattr(transcribe_mod, "_duration", lambda p: 6000.0)
    monkeypatch.setattr(transcribe_mod, "audio_seconds", lambda p: 5994.0)

    finalize(cfg, ENTRY, WHEN, logging.getLogger("test"))
    assert not any("incomplet" in t.lower() for t, _ in told), told


# --------------------------------------------------------------------------
# Alarms that cried wolf
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_a_gated_class_is_not_reported_as_a_failure(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """A class the gate blocked is not a capture failure.

    When the gate sees the Mac off campus it never starts a capture, and
    finalize used to fire a CRITICAL "aucun son capté" on every tick of the
    class hour: ten alarms in ten minutes about a class nobody was trying to
    record.
    """
    import logging

    from src.recorder import finalize, session_dir

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    target = session_dir(cfg, ENTRY, WHEN)
    target.mkdir(parents=True, exist_ok=True)
    (target / "gated").write_text("no at-school signal", encoding="utf-8")

    told: list[tuple] = []
    monkeypatch.setattr("src.recorder.notify",
                        lambda title, body, **k: told.append((title, body)))

    assert finalize(cfg, ENTRY, WHEN, logging.getLogger("test")) is None
    assert told == [], f"it cried about a class the gate had deliberately blocked: {told}"


@pytest.mark.skipif(not _real_ffmpeg(), reason="ffmpeg not installed")
def test_a_real_capture_failure_alarms_once_not_every_tick(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """finished_entry keeps returning the class for the whole grace window, so
    every tick re-ran finalize. A critical alarm repeated ten times is one he
    learns to swipe away, which is exactly what it must not become."""
    import logging

    from src.recorder import finalize, session_dir

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    target = session_dir(cfg, ENTRY, WHEN)
    target.mkdir(parents=True, exist_ok=True)
    (target / "ffmpeg.log").write_text("Operation not permitted", encoding="utf-8")

    told: list[tuple] = []
    monkeypatch.setattr("src.recorder.notify",
                        lambda title, body, **k: told.append((title, body)))

    log = logging.getLogger("test")
    for _ in range(10):
        assert finalize(cfg, ENTRY, WHEN, log) is None

    assert len(told) == 1, f"{len(told)} alarms for one lost class"
    assert "NON enregistré" in told[0][0]


# --------------------------------------------------------------------------
# How loudly to speak
# --------------------------------------------------------------------------


def test_max_priority_is_never_used():
    """ntfy priority 5 bypasses do-not-disturb and vibrates in repeated bursts.

    Every alarm in this project used to send it, including "your class started
    and the Mac does not think it is at school". That rang like crazy. An alert
    that seizes the phone for a class you are not attending is an alert you
    learn to swipe away.
    """
    import inspect

    import src.common as common

    source = inspect.getsource(common.notify)
    assert '"priority": 5' not in source
    assert '{"quiet": 2, "normal": 3, "alert": 4}' in source


def test_a_missed_class_does_not_buzz_the_phone(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """The gate fires on every class that was never being recorded."""
    import logging

    from src.recorder import _warn_gated

    cfg = _recorded_cfg(write_config, tmp_repo, sample_config_dict)
    seen: list[dict] = []
    monkeypatch.setattr("src.recorder.notify",
                        lambda t, m, **k: seen.append(dict(k, title=t)))

    _warn_gated(cfg, ENTRY, WHEN, logging.getLogger("test"))
    assert seen, "it said nothing at all, which was the older bug"
    assert seen[0].get("level") == "quiet", seen
    assert not seen[0].get("critical"), seen


def test_a_silent_lecture_still_speaks_up(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """The one case worth a sound: audio was captured and contains nothing.
    That is unrecoverable, and he has to hear about it while he can still act."""
    import inspect

    import src.recorder as rec

    source = inspect.getsource(rec.finalize)
    assert "Enregistrement muet" in source
    i = source.index("Enregistrement muet")
    assert "critical=True" in source[i:i + 400], "the silent-lecture alarm went quiet too"
