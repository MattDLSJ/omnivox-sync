"""The lecture was recorded fine. whisper was handed near-silence.

On 2026-08-26, five real minutes of a Psycho lecture recorded from the room at
-47.9 dB mean produced " ..." from whisper.cpp and a full, accurate French
paragraph after normalisation. Nothing about the audio changed; only the level
did. whisper.cpp applies no gain control of its own, so a lecture hall recorded
on a laptop mic sits below what the model will decode.

These tests pin the two halves of the fix that can silently regress: that real
audio gets lifted, and that digital silence does NOT. Amplifying silence is the
input that makes whisper invent "Sous-titrage Société Radio-Canada" for a whole
class, so the guard matters as much as the gain.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.transcribe import (
    NORMALISE_FILTER, SILENT_PEAK_DB, _ffmpeg, _to_wav16k, peak_db,
)


def _ffmpeg_available() -> bool:
    try:
        _ffmpeg()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_ffmpeg = pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")


def _synth(path: Path, source: str, seconds: int = 3) -> Path:
    """Render a short wav from an ffmpeg source expression."""
    subprocess.run(
        [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"{source}:d={seconds}",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(path)],
        check=True, capture_output=True, timeout=120,
    )
    return path


@needs_ffmpeg
def test_peak_db_reads_digital_silence_as_minus_91(tmp_path):
    """The number the whole diagnosis rests on.

    A denied microphone does not error; it returns samples that are all zero.
    ffmpeg reports that as -91.0 dB, and it is the only reading that separates
    "the mic was never opened" from "the room was quiet".
    """
    silent = _synth(tmp_path / "silent.wav", "anullsrc=r=16000:cl=mono")
    assert peak_db(silent) <= SILENT_PEAK_DB


@needs_ffmpeg
def test_peak_db_reads_a_real_signal_well_above_the_threshold(tmp_path):
    tone = _synth(tmp_path / "tone.wav", "sine=frequency=440:sample_rate=16000")
    assert peak_db(tone) > SILENT_PEAK_DB


@needs_ffmpeg
def test_quiet_audio_is_lifted_before_whisper_sees_it(tmp_path):
    """The regression that cost a lecture: a faint recording passed through flat.

    A tone at -30 dB stands in for the professor at the front of the room. What
    matters is only that the output is dramatically louder than the input, not
    the exact target, so the assertion has room for loudnorm to be retuned.
    """
    quiet = tmp_path / "quiet.wav"
    subprocess.run(
        [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=16000:d=4",
         "-af", "volume=-30dB",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(quiet)],
        check=True, capture_output=True, timeout=120,
    )
    before = peak_db(quiet)
    assert -60 < before < -20, "the fixture must be quiet but not silent"

    out = tmp_path / "out.wav"
    _to_wav16k(quiet, out)
    assert peak_db(out) > before + 20


@needs_ffmpeg
def test_digital_silence_is_left_flat(tmp_path):
    """Normalising nothing produces amplified nothing, which is worse.

    loudnorm has no signal to work from here, so it would raise the dither to
    speaking volume and hand whisper a minute of hiss. Whisper answers hiss
    with invented filler, so a silent class would come back as a page of
    plausible French rather than as an obvious failure.
    """
    silent = _synth(tmp_path / "silent.wav", "anullsrc=r=16000:cl=mono", seconds=4)
    out = tmp_path / "out.wav"
    _to_wav16k(silent, out)
    assert peak_db(out) <= SILENT_PEAK_DB


@needs_ffmpeg
def test_normalise_false_skips_the_filter(tmp_path):
    """The caller can opt out; used by anything that needs untouched samples."""
    quiet = tmp_path / "quiet.wav"
    subprocess.run(
        [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=16000:d=3",
         "-af", "volume=-30dB",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(quiet)],
        check=True, capture_output=True, timeout=120,
    )
    out = tmp_path / "out.wav"
    _to_wav16k(quiet, out, normalise=False)
    assert peak_db(out) == pytest.approx(peak_db(quiet), abs=1.0)


def test_highpass_runs_before_loudnorm():
    """Order is not cosmetic.

    At -48 dB the ventilation is most of the energy in the file. Normalising
    first spends the headroom on the air conditioning and leaves the speech
    where it was.
    """
    assert NORMALISE_FILTER.index("highpass") < NORMALISE_FILTER.index("loudnorm")


@needs_ffmpeg
def test_the_chain_cuts_rumble_before_it_lifts_the_level(tmp_path):
    """Order still matters for the two stages that remain.

    Most of the energy in these recordings is below 400 Hz and none of it is
    speech. Normalising before cutting it spends the headroom on ventilation.
    """
    names = [s.split("=")[0] for s in NORMALISE_FILTER.split(",")]
    assert names.index("highpass") < names.index("loudnorm")


def test_there_is_no_denoiser_in_the_chain():
    """afftdn was here and it deleted whole passages.

    `nf` is an ABSOLUTE noise floor in dB. These lectures sit near -49 dBFS, so
    the -25 that won the original sweep classified the speech itself as noise:
    one measured passage went from 67 words to 1. The sweep liked it only
    because it was run before the VAD threshold was fixed, and denoising was
    standing in for a gate that was too tight. Nine passages at -vt 0.30:
    582 words with no denoiser, 492 with nf=-25.

    If a denoiser is ever added back, measure the WORST passage, not the mean.
    """
    assert "afftdn" not in NORMALISE_FILTER
    assert "anlmdn" not in NORMALISE_FILTER


@needs_ffmpeg
def test_the_chain_actually_runs(tmp_path):
    """A filter chain is a string until something executes it. A typo here is
    invisible in review and turns every transcript into a conversion error."""
    out = tmp_path / "out.wav"
    proc = subprocess.run(
        [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=16000:d=2",
         "-af", NORMALISE_FILTER,
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[:300]
    assert out.stat().st_size > 1000


def test_vad_threshold_is_looser_than_the_whisper_default():
    """0.50 is tuned for a microphone near the speaker.

    From the back of a lecture hall the professor arrives at the level of a
    quiet room and the default gate drops the audio before the decoder ever
    sees it. Blind judges scored 0.30 higher on content recovered AND lower on
    content invented, so this is not a recall-for-precision trade.
    """
    from src.transcribe import VAD_THRESHOLD

    assert 0.15 < float(VAD_THRESHOLD) < 0.50


def test_both_whisper_invocations_pass_the_same_vad_threshold():
    """The language probe and the real decode both run whisper with VAD.

    Leaving the probe on the default gate makes it detect language from a
    different slice of audio than the one that gets transcribed, which is how
    a French lecture ends up decoded as English.
    """
    import inspect

    from src import transcribe as t

    src = inspect.getsource(t)
    assert src.count('"-vt", VAD_THRESHOLD') == 2


@needs_ffmpeg
def test_peak_db_floors_at_minus_91_and_the_threshold_clears_it(tmp_path):
    """volumedetect quantises to 16-bit internally whatever it is fed.

    A deliberately generated -80 dB tone reads -91.0 through it, so -91.0 means
    "at or below one LSB", not "exactly this loud". The silence threshold has
    to sit well clear of that floor or it would be measuring quantisation
    rather than audio.
    """
    quiet = tmp_path / "q.wav"
    subprocess.run(
        [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=16000:d=2",
         "-af", "volume=-80dB",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_f32le", "-y", str(quiet)],
        check=True, capture_output=True, timeout=120,
    )
    assert peak_db(quiet) == pytest.approx(-91.0, abs=0.5)
    assert SILENT_PEAK_DB > -91.0 + 25, "the threshold must clear the measurement floor"
