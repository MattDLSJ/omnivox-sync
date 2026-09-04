"""Live transcription: the transcript exists before the class ends.

The point of this module is to be useful mid-lecture, so the risks are all
about reading a file ffmpeg has not finished writing, and about doing
expensive work on a tick that fires every sixty seconds.
"""

from __future__ import annotations

from datetime import datetime, time as dtime
from pathlib import Path

from src.common import load_config
from src.live import chunk_index, finished_chunks, pending, tick


def _session(tmp_path, names):
    d = tmp_path / "sess"
    d.mkdir(exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"audio")
    return d


def test_only_closed_segments_are_offered(tmp_path):
    d = _session(tmp_path, ["chunk_093000_000.m4a", "chunk_093000_001.m4a",
                            "chunk_093000_002.m4a"])
    got = [p.name for p in finished_chunks(d)]
    assert got == ["chunk_093000_000.m4a", "chunk_093000_001.m4a"]
    assert "chunk_093000_002.m4a" not in got   # still open


def test_a_lone_segment_is_not_ready_yet(tmp_path):
    d = _session(tmp_path, ["chunk_093000_000.m4a"])
    assert finished_chunks(d) == []


def test_an_empty_session_is_fine(tmp_path):
    d = _session(tmp_path, [])
    assert finished_chunks(d) == [] and pending(d) == []


def test_segments_sort_numerically_not_alphabetically(tmp_path):
    """chunk_9 must come before chunk_10, or the transcript is scrambled."""
    d = _session(tmp_path, [f"chunk_093000_{i:03d}.m4a" for i in (0, 9, 10, 11, 2)])
    order = [chunk_index(p)[1] for p in finished_chunks(d)]
    assert order == sorted(order)


def test_already_transcribed_segments_are_not_redone(tmp_path):
    d = _session(tmp_path, ["chunk_093000_000.m4a", "chunk_093000_001.m4a",
                            "chunk_093000_002.m4a"])
    (d / "chunk_093000_000.txt").write_text("déjà fait", encoding="utf-8")
    assert [p.name for p in pending(d)] == ["chunk_093000_001.m4a"]


def _cfg(write_config, tmp_repo, sample_config_dict, **over):
    courses = [dict(c) for c in sample_config_dict["courses"]]
    courses[0]["record"] = True
    data = {
        "courses": courses,
        "schedule": [{"course": "601-101-MQ", "weekday": "friday",
                      "start": "08:10", "end": "12:00"}],
        "semester_start": "2026-08-24",
        "semester_end": "2026-12-09",
        "transcribe": True,
    }
    data.update(over)
    return load_config(write_config(data), repo_root=tmp_repo)


def test_the_tick_does_nothing_when_the_feature_is_off(
    write_config, tmp_repo, sample_config_dict
):
    cfg = _cfg(write_config, tmp_repo, sample_config_dict, live_transcribe=False)
    assert tick(cfg, now=datetime(2026, 10, 23, 9, 30)) == "disabled"


def test_the_tick_does_nothing_when_transcription_is_off(
    write_config, tmp_repo, sample_config_dict
):
    cfg = _cfg(write_config, tmp_repo, sample_config_dict, transcribe=False)
    assert tick(cfg, now=datetime(2026, 10, 23, 9, 30)) == "disabled"


def test_the_tick_is_cheap_when_nothing_is_recording(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """This fires every 60 s all semester. Idle must cost nothing."""
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    calls = []
    monkeypatch.setattr("src.live.transcribe_chunk",
                        lambda *a, **k: calls.append(1) or "")
    assert tick(cfg, now=datetime(2026, 10, 23, 9, 30)) == "idle"
    assert calls == []


def test_the_tick_stays_idle_outside_the_class_window(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    monkeypatch.setattr("src.recorder.is_recording", lambda c: True)
    # Friday 20:00: the schedule entry ends at 12:00.
    assert tick(cfg, now=datetime(2026, 10, 23, 20, 0)) == "idle"


def test_the_tick_stays_idle_on_a_day_with_no_class(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    cfg = _cfg(write_config, tmp_repo, sample_config_dict,
               no_class_days=["2026-10-23"])
    monkeypatch.setattr("src.recorder.is_recording", lambda c: True)
    assert tick(cfg, now=datetime(2026, 10, 23, 9, 30)) == "idle"


def test_a_failing_segment_still_records_that_it_was_attempted(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """A segment whisper chokes on must not be retried forever on every tick."""
    from src.recorder import session_dir, ScheduleEntry

    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    entry = ScheduleEntry("601-101-MQ", "friday", dtime(8, 10), dtime(12, 0))
    now = datetime(2026, 10, 23, 9, 30)
    sess = session_dir(cfg, entry, now)
    sess.mkdir(parents=True)
    for i in range(2):
        (sess / f"chunk_093000_{i:03d}.m4a").write_bytes(b"audio")

    monkeypatch.setattr("src.recorder.is_recording", lambda c: True)
    monkeypatch.setattr("src.live.duration", lambda p: 60.0)
    monkeypatch.setattr("src.live.transcribe_chunk", lambda *a, **k: "")

    tick(cfg, now=now)
    assert (sess / "chunk_093000_000.txt").exists()
    assert pending(sess) == []


def test_the_live_file_warns_that_it_is_provisional(
    write_config, tmp_repo, sample_config_dict, monkeypatch
):
    """It will be overwritten by the final pass; a reader must know that."""
    from src.recorder import session_dir, ScheduleEntry

    cfg = _cfg(write_config, tmp_repo, sample_config_dict)
    entry = ScheduleEntry("601-101-MQ", "friday", dtime(8, 10), dtime(12, 0))
    now = datetime(2026, 10, 23, 9, 30)
    sess = session_dir(cfg, entry, now)
    sess.mkdir(parents=True)
    for i in range(2):
        (sess / f"chunk_093000_{i:03d}.m4a").write_bytes(b"audio")

    monkeypatch.setattr("src.recorder.is_recording", lambda c: True)
    monkeypatch.setattr("src.live.duration", lambda p: 60.0)
    monkeypatch.setattr("src.live.transcribe_chunk",
                        lambda *a, **k: "[00:00:00] Bonjour tout le monde.")

    status = tick(cfg, now=now)
    assert status.startswith("transcribed")
    from src.live import live_path
    body = live_path(cfg, cfg.course_by_code("601-101-MQ"), now).read_text(encoding="utf-8")
    assert "EN COURS" in body
    assert "EN DIRECT" in body
    assert "Bonjour tout le monde." in body
