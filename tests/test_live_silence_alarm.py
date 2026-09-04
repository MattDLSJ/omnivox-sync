"""A silent capture has to be caught during the class, not after it.

It has now happened twice, for two entirely unrelated reasons, and neither
produced an error anywhere:

  2026-08-26  macOS had not granted the microphone; ffmpeg received zeroes.
  2026-08-27  avfoundation renumbered its devices overnight and "-i :0" had
              become an output device; ffmpeg received zeroes.

Both were found by accident. finalize() checks at the end of the class, which
reports the lecture as gone rather than saving it. This check runs two minutes
in, while there is still something to rescue.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.live import SILENCE_ALARM_AFTER, warn_if_silent

LOG = logging.getLogger("test")
COURSE = SimpleNamespace(label=lambda: "Philo")


def _ffmpeg() -> str | None:
    import shutil

    return shutil.which("ffmpeg")


needs_ffmpeg = pytest.mark.skipif(not _ffmpeg(), reason="ffmpeg not installed")


def _chunk(path: Path, source: str) -> None:
    subprocess.run(
        [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"{source}:d=2",
         "-ac", "1", "-c:a", "aac", "-y", str(path)],
        check=True, capture_output=True, timeout=120,
    )


def _session(tmp_path: Path, source: str, count: int) -> Path:
    session = tmp_path / "sess"
    session.mkdir()
    # finished_chunks() never returns the newest file, so write one extra.
    for i in range(count + 1):
        _chunk(session / f"chunk_100000_{i:03d}.m4a", source)
    return session


@pytest.fixture()
def sent(monkeypatch):
    out = []
    monkeypatch.setattr("src.recorder.notify", lambda t, m, **k: out.append((t, m, k)))
    return out


@needs_ffmpeg
def test_a_silent_capture_raises_the_alarm(tmp_path, sent):
    session = _session(tmp_path, "anullsrc=r=44100:cl=mono", SILENCE_ALARM_AFTER)
    assert warn_if_silent(session, COURSE, LOG, None) is True
    assert len(sent) == 1 and sent[0][2]["critical"] is True
    assert "Philo" in sent[0][1]


@needs_ffmpeg
def test_a_quiet_room_does_not(tmp_path, sent):
    """The whole difficulty. A lecture from the back of the room is very quiet
    and must not trip this, or the alarm becomes noise and gets ignored."""
    session = _session(tmp_path, "sine=frequency=300:sample_rate=44100", SILENCE_ALARM_AFTER)
    assert warn_if_silent(session, COURSE, LOG, None) is False
    assert sent == []


@needs_ffmpeg
def test_it_waits_before_deciding(tmp_path, sent):
    """One chunk is not evidence: a capture can start during a genuine pause."""
    session = _session(tmp_path, "anullsrc=r=44100:cl=mono", SILENCE_ALARM_AFTER - 1)
    assert warn_if_silent(session, COURSE, LOG, None) is False
    assert sent == []


@needs_ffmpeg
def test_it_notifies_once_per_class_not_once_per_minute(tmp_path, sent):
    """The live job ticks every minute for two hours."""
    session = _session(tmp_path, "anullsrc=r=44100:cl=mono", SILENCE_ALARM_AFTER)
    for _ in range(5):
        warn_if_silent(session, COURSE, LOG, None)
    assert len(sent) == 1


@needs_ffmpeg
def test_a_probe_failure_never_stops_the_class(tmp_path, sent, monkeypatch):
    session = _session(tmp_path, "anullsrc=r=44100:cl=mono", SILENCE_ALARM_AFTER)

    def boom(path):
        raise OSError("ffmpeg gone")

    monkeypatch.setattr("src.transcribe.peak_db", boom)
    assert warn_if_silent(session, COURSE, LOG, None) is False
    assert sent == []
