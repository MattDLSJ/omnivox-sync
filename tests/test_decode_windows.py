"""whisper is handed one minute at a time, and never overlapping minutes.

whisper carries its previous output forward as context. On a lecture recorded
across a room that is what makes it spiral: one wrong line conditions the next,
and a single sentence repeats until the file ends. Resetting the context every
minute stops it.

Measured over 27 minutes of real lecture audio, blind-judged:

                    recovered   invented   duplicated   longest loop
    whole file           7.75       2.80         1.40              9
    60 s windows         7.70       1.60         0.15              1

The tests below pin the two things a later refactor could plausibly undo: that
long audio really is split, and that the windows do not overlap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import transcribe as t


@pytest.fixture()
def spy(monkeypatch):
    """Record every window handed to whisper, without running whisper."""
    calls: list[tuple[float, float | None]] = []
    cuts: list[tuple[float, float | None]] = []

    def fake_slice(src, dest, start, end):
        cuts.append((start, end))
        dest.write_bytes(b"wav")

    def fake_once(wav, **kwargs):
        calls.append(kwargs["offset"])
        return [(kwargs["offset"], "mot")]

    monkeypatch.setattr(t, "_slice", fake_slice)
    monkeypatch.setattr(t, "_decode_once", fake_once)
    return calls, cuts


def _run(tmp_path):
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"wav")
    return t._decode(
        wav, binary="whisper", model=Path("m"), language="fr", prompt="",
        offset=0.0, timeout=60, log=__import__("logging").getLogger("t"),
    )


def test_short_audio_is_not_split(tmp_path, monkeypatch, spy):
    """A live chunk is already a minute. Slicing it again would only add a
    temp-file round trip and a chance to lose the last syllable."""
    calls, cuts = spy
    monkeypatch.setattr(t, "_duration", lambda p: 60.0)
    _run(tmp_path)
    assert cuts == [], "short audio must go straight to whisper"
    assert calls == [0.0]


def test_long_audio_is_split_into_windows(tmp_path, monkeypatch, spy):
    calls, cuts = spy
    monkeypatch.setattr(t, "_duration", lambda p: 200.0)
    _run(tmp_path)
    assert len(cuts) == 4, f"200 s should be four windows, got {cuts}"
    assert calls == [0.0, 60.0, 120.0, 180.0]


def test_windows_do_not_overlap(tmp_path, monkeypatch, spy):
    """Overlap is the obvious fix for a word cut at a boundary and it measured
    WORSE: 6.60 for duplicated content against 0.15. whisper re-words the
    repeated seconds just differently enough that no dedupe catches it."""
    calls, cuts = spy
    monkeypatch.setattr(t, "_duration", lambda p: 300.0)
    _run(tmp_path)
    starts = [c[0] for c in cuts]
    assert starts == sorted(starts)
    for (start, end), (next_start, _) in zip(cuts, cuts[1:]):
        assert end is not None
        assert next_start >= end, f"window at {next_start} overlaps one ending at {end}"


def test_the_last_window_runs_to_the_end(tmp_path, monkeypatch, spy):
    """Passing an explicit end on the final slice clips the last syllable when
    the duration probe rounds down."""
    calls, cuts = spy
    monkeypatch.setattr(t, "_duration", lambda p: 150.0)
    _run(tmp_path)
    assert cuts[-1][1] is None


def test_offsets_stay_absolute(tmp_path, monkeypatch, spy):
    """A timestamp in the transcript has to mean the same thing whether the
    file was split or not, or every reference into the recording is wrong."""
    calls, cuts = spy
    monkeypatch.setattr(t, "_duration", lambda p: 200.0)
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"wav")
    out = t._decode(
        wav, binary="w", model=Path("m"), language="fr", prompt="", offset=500.0,
        timeout=60, log=__import__("logging").getLogger("t"),
    )
    assert [when for when, _ in out] == [500.0, 560.0, 620.0, 680.0]


def test_window_length_is_a_minute():
    """Not a magic number: 30 s windows scored worse on recovery (7.20 against
    7.70) and 90 s starts letting the loops back in."""
    assert 45.0 <= t.WINDOW_SECONDS <= 75.0
