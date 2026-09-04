"""Local speech-to-text for class recordings.

Runs whisper.cpp on-device: no API, no cost, no audio leaving the Mac. On an
M4 Pro the large-v3-turbo model runs around 8x realtime, so a two-hour lecture
transcribes in roughly fifteen minutes.

The transcript matters more than the audio. It is what makes a semester of
lectures searchable, and what lets an assistant answer "what did the prof say
about X" without anyone re-listening to two hours of recording.

Two correctness rules are load-bearing here, both learned the hard way:

1. ALWAYS decode with timestamps, then strip them for display. `-nt` is not a
   formatting choice. whisper.cpp's long-form loop uses the last emitted
   timestamp token to decide where the next 30-second window starts. With
   `-nt` there are no timestamp tokens, so the loop advances a flat 30 seconds
   no matter how much it actually decoded, and anything after an early decode
   termination inside that window is skipped silently. Measured on a 3.6 min
   clip of clean synthetic French: 39/40 checkpoints survived with `-nt`,
   40/40 without. Early terminations cluster at pauses, noise and language
   switches, so real lecture audio loses more than that.

2. Detect the language per window, not per file. `-l auto` runs detection ONCE
   on the first 30 seconds and pins that language for the whole file. Speech in
   another language is then not translated, it is DELETED. Measured: a 42 s
   English passage inside a French clip produced zero words and corrupted the
   surrounding timestamps, while the same audio alone transcribed at p=0.9999.
   So we probe short windows first, group them into contiguous language runs,
   and decode each run with its own language pinned.

What this still cannot fix: code-switching INSIDE one sentence ("le working
class dont il parle"). The operative variable is whether a single 30-second
decode window is linguistically homogeneous, and a mixed window drops the
out-of-language speech no matter where the switch falls. That is why runs are
cut as real files rather than by seeking within one.

An initial prompt naming the expected English terms is threaded through here,
but do not oversell it: it rescued one lexicalized borrowing in a hand test and
failed on sustained passages in a broader one. Treat it as unproven.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = Path.home() / ".cache" / "whisper" / "ggml-large-v3-turbo.bin"
DEFAULT_VAD = Path.home() / ".cache" / "whisper" / "ggml-silero-v5.1.2.bin"
_BINARIES = ("whisper-cli", "whisper-cpp", "main")
_FALLBACK_PATHS = ("/opt/homebrew/bin/whisper-cli", "/usr/local/bin/whisper-cli")

# Language probing.
PROBE_SECONDS = 15        # measured reliable (p>0.99 on clean speech) and short
                          # enough that a switch lands within one window
MIN_LANG_PROB = 0.60      # below this a window inherits from its neighbours
MIN_RUN_SECONDS = 20.0    # a shorter run is noise, not a real language switch
SNAP_SECONDS = 6.0        # how far a cut may slide to land inside a silence
SILENCE_NOISE = "-30dB"
SILENCE_MIN = 0.35

# Reading the shape of the class, not just its words. A transcript that is only
# prose hides what an agent most needs: that nothing was said for twenty
# minutes, or that a stretch is whisper looping on a room full of noise.
# A decode that returns almost nothing for its duration did not find quiet
# audio, it found audio it could not read under the language it was given.
# Real lecture speech runs well over 100 words per minute; this floor is low
# enough that a genuinely sparse stretch is not second-guessed.
MIN_WORDS_PER_MINUTE = 20.0

MIN_GAP_SECONDS = 120.0        # a quiet stretch worth telling the reader about
UNRELIABLE_WINDOW = 120.0      # judged in two-minute blocks
UNRELIABLE_MIN_WORDS = 40      # too little text to judge repetition fairly
# Measured on real recordings: a healthy lecture window sits at 0.71 to 0.81
# unique bigrams and gzip 2.3 to 2.6. A window where whisper looped on group
# babble measured 0.23 and 6.26, so the gap between good and bad is wide.
# The bigram ratio is the primary signal because it does not care how large the
# speaker's vocabulary is. Compression does: a lecture that drills a short word
# list compresses like a hallucination without being one, so gzip only counts
# when the bigram ratio corroborates it.
UNRELIABLE_BIGRAM = 0.45
UNRELIABLE_GZIP = 5.0
UNRELIABLE_GZIP_BIGRAM = 0.60
# Whisper answers silence and unintelligible noise with a stock phrase rather
# than nothing: French audio famously produces "Sous-titrage Société
# Radio-Canada" over and over. That is sparse, a line every thirty seconds, so
# the word-count guard above never sees enough text to judge it. Catch it by
# looking at how much of a window is one repeated line.
REPEAT_DOMINANCE = 0.5     # share of a window taken by a single repeated line
REPEAT_MIN_SEGMENTS = 4    # below this, repetition is not yet evidence
REPEAT_RUN = 3             # consecutive identical lines folded into one marker

_PROCESSING = re.compile(r"processing\s+'([^']+)'")
_DETECTED = re.compile(r"auto-detected language:\s*([A-Za-z]{2,3})\s*\(\s*p\s*=\s*([0-9.]+)\s*\)")
_TS_LINE = re.compile(
    r"^\[(\d+):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*[^\]]*\]\s?(.*)$"
)
_SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")

# Marker language names, for the run headings inside a French transcript.
_LANG_FR = {
    "fr": "français", "en": "anglais", "es": "espagnol", "de": "allemand",
    "it": "italien", "pt": "portugais", "la": "latin", "ar": "arabe",
    "zh": "chinois", "ru": "russe", "nl": "néerlandais", "ht": "créole",
}


class TranscriptionError(Exception):
    """Transcription failed. Never fatal: the audio is already safe on disk."""


@dataclass(frozen=True)
class LanguageRun:
    """A contiguous stretch of audio decoded with one language pinned."""

    start: float
    end: float          # exclusive; the last run ends at the file duration
    lang: str
    confidence: float = 0.0
    # Foreign stretches too short to survive as their own run, swallowed by
    # this one. They are recorded rather than forgotten because decoding them
    # under this run's language deletes them, and a silent hole in a study
    # transcript is worse than an admitted one.
    absorbed: tuple[tuple[float, float, str], ...] = ()

    def seconds(self) -> float:
        return max(0.0, self.end - self.start)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def find_whisper() -> str | None:
    for name in _BINARIES:
        found = shutil.which(name)
        if found:
            return found
    for candidate in _FALLBACK_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def find_model(configured: str = "") -> Path | None:
    if configured:
        path = Path(configured).expanduser()
        return path if path.exists() else None
    if DEFAULT_MODEL.exists():
        return DEFAULT_MODEL
    cache = DEFAULT_MODEL.parent
    if cache.is_dir():
        models = sorted(cache.glob("ggml-*.bin"), key=lambda p: p.stat().st_size, reverse=True)
        return models[0] if models else None
    return None


#: How eagerly Silero calls a frame speech, 0..1. whisper.cpp defaults to 0.50,
#: which is tuned for a microphone near the speaker. At the back of a lecture
#: hall the professor's voice arrives at the level of a quiet room, and the
#: default gate simply drops it: the decoder never sees the audio, so no
#: decoder threshold can recover it. Two of them, no-speech-thold and
#: logprob-thold, produced BYTE-IDENTICAL output in the sweep for exactly this
#: reason.
#:
#: 0.30 was the best of the settings tried, and it is a strict improvement over
#: the default rather than a trade: blind judges scored it higher on real
#: content recovered (7.75 vs 7.12) AND lower on invented content (2.25 vs
#: 2.75), and the longest repeat loop fell from 7 lines to 2.
#:
#: Do not push it lower without re-measuring. 0.20 recovers more still (8.00)
#: but doubles what the model invents over silence, and turning VAD off
#: entirely was the worst result in the whole sweep: it scored 7.83 on invented
#: content over clips that contain no speech at all, against 1.83 here.
VAD_THRESHOLD = "0.30"


def find_vad(configured: str = "") -> Path | None:
    """The Silero voice-activity model, if it has been fetched.

    Optional on purpose: a missing model degrades to the old behaviour with a
    log line rather than failing a class recording.
    """
    if configured:
        path = Path(configured).expanduser()
        return path if path.exists() else None
    if DEFAULT_VAD.exists():
        return DEFAULT_VAD
    cache = DEFAULT_VAD.parent
    if cache.is_dir():
        found = sorted(cache.glob("ggml-silero*.bin"))
        return found[0] if found else None
    return None


def available(model: str = "") -> bool:
    return find_whisper() is not None and find_model(model) is not None


def _ffmpeg() -> str:
    import src.recorder as recorder  # local import: avoids a cycle at module load

    return recorder.find_ffmpeg()


# --------------------------------------------------------------------------
# Audio helpers
# --------------------------------------------------------------------------


#: A recording whose loudest sample is under this is not quiet, it is empty.
#: A real room with nobody speaking still peaks around -13 dB, and a microphone
#: macOS has denied reads -91.0. Nothing between the two exists in practice, so
#: this threshold separates "no signal" from "faint signal" without a
#: judgement call.
#:
#: -91.0 is the FLOOR of the measurement, not a reading: ffmpeg's volumedetect
#: quantises to 16-bit internally and reports -91.0 for anything at or below one
#: LSB, whatever the input format. A deliberately generated -80 dB tone also
#: reads -91.0 through it, where astats reports the true -98. That does not
#: affect this threshold, which sits 31 dB above the floor, but do NOT use
#: peak_db() to measure anything genuinely quiet: use astats for that.
SILENT_PEAK_DB = -60.0

#: Lift the level before whisper sees it, and do nothing else.
#:
#: There WAS an FFT denoiser here, afftdn=nf=-25, and it was a mistake. It won
#: a 19-chain sweep on total words and it is the worst of its alternatives once
#: the VAD threshold was fixed, because those two were tuned in separate stages
#: and the first was never re-run after the second changed. Denoising helped
#: only by making the signal clean enough to get past a VAD gate that was too
#: tight; open the gate properly and the denoiser has nothing left to buy.
#:
#: What it cost, over nine passages of a real lecture, decoded at -vt 0.30:
#:
#:     no denoise            582 words
#:     afftdn nf=-25         492 words   <- what shipped
#:     afftdn nf=-40         585 words
#:     afftdn nf=-50         583 words
#:
#: And the average understates it. On one passage the shipped chain returned
#: ONE word where no denoising returned sixty-seven: nf is an absolute noise
#: floor in dB, so on audio whose speech sits near -49 dBFS a threshold of -25
#: classifies the speech itself as noise and deletes it. A filter that can erase
#: a whole passage is not worth 0.5% on the mean, which is all -40 was worth.
#:
#:   highpass  first. Most of the energy in these recordings is below 400 Hz and
#:             none of it is speech, so cutting it stops every later stage
#:             spending its range on the ventilation. Dropping it is not
#:             neutral: loudnorm alone sent the decoder into a 27-line loop.
#:
#:   loudnorm  last, targeting perceived loudness rather than peak. That is what
#:             makes it adapt: far less gain when the mic is close, so one
#:             setting works from the front row and from the back. A fixed
#:             +20 dB instead scored 1184 against 1520.
NORMALISE_FILTER = "highpass=f=80,loudnorm=I=-18:TP=-2:LRA=11"


def peak_db(path: Path) -> float:
    """Loudest sample in the file, in dBFS. -91.0 means digital silence.

    Used to decide whether normalising is worth doing, and by the recorder to
    tell a class it failed to capture from a class that was merely quiet.
    """
    proc = subprocess.run(
        [_ffmpeg(), "-nostdin", "-hide_banner", "-nostats",
         "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=1800,
    )
    match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", proc.stderr or "")
    return float(match.group(1)) if match else 0.0


def _to_wav16k(src: Path, dest: Path, *, normalise: bool = True) -> None:
    """whisper.cpp wants 16 kHz mono PCM; recordings are AAC.

    It also wants them at a sane level. See NORMALISE_FILTER for why, and for
    the before/after that justifies doing this on every recording rather than
    only on the quiet ones.

    A file with no signal at all is converted flat. Normalising digital silence
    would raise the dither to speaking volume and hand whisper a minute of
    amplified nothing, which is exactly the input that makes it invent
    "Sous-titrage Société Radio-Canada".
    """
    filters: list[str] = []
    if normalise and peak_db(src) > SILENT_PEAK_DB:
        filters = ["-af", NORMALISE_FILTER]

    proc = subprocess.run(
        [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-i", str(src), *filters,
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(dest)],
        capture_output=True, text=True, timeout=1800,
    )
    if proc.returncode != 0 or not dest.exists():
        raise TranscriptionError(f"Could not convert {src.name} to wav: {proc.stderr[:200]}")


def audio_seconds(path: Path) -> float:
    """How much audio the file actually holds, not how long it says it is.

    These are different numbers and the gap between them is where a lecture
    goes missing. A capture that drops samples still advances its timestamps
    with the device clock, so the container ends up describing the wall-clock
    span of the class while holding only the audio that survived. Nothing
    downstream notices: ffmpeg exits clean, the file plays, the transcript
    reads normally, and the header quotes the container.

    Every Fall 2026 recording was between 9 and 17 percent short this way. The
    history class of 2026-08-28 filed itself as 201 minutes over a file holding
    172, and the 29 missing minutes were only found by counting frames.

    Read from the frame count in the header, which is free, rather than by
    decoding, which costs three seconds on a three-hour lecture. AAC carries
    1024 samples per frame. Returns 0.0 when it cannot tell, so callers can
    treat "unknown" and "fine" the same way and never block a recording.
    """
    probe = _ffmpeg().replace("ffmpeg", "ffprobe")
    try:
        proc = subprocess.run(
            [probe, "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=nb_frames,sample_rate", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        rate, frames = (proc.stdout or "").strip().split(",")[:2]
        return int(frames) * 1024 / int(rate)
    except Exception:  # noqa: BLE001 - a diagnostic must never cost a lecture
        return 0.0


def _duration(path: Path) -> float:
    probe = _ffmpeg().replace("ffmpeg", "ffprobe")
    try:
        proc = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        return float((proc.stdout or "0").strip())
    except Exception:  # noqa: BLE001 - duration is only used for the last run's end
        return 0.0


def _slice(src: Path, dest: Path, start: float, end: float | None) -> None:
    """Cut [start, end) out of a wav. `-ss` after `-i` so the cut is accurate;
    PCM decode is cheap enough that seeking from zero does not matter."""
    cmd = [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
           "-i", str(src), "-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(dest)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not dest.exists():
        raise TranscriptionError(f"Could not slice {start:.1f}-{end}: {proc.stderr[:200]}")


def silence_midpoints(wav: Path, *, logger: logging.Logger | None = None) -> list[float]:
    """Midpoint of every detected silence, for snapping run boundaries.

    Cutting mid-word costs a word on each side of the cut. Cutting in a pause
    costs nothing. Failure here is not fatal: we fall back to the probe grid.
    """
    log = logger or logging.getLogger("school.transcribe")
    try:
        proc = subprocess.run(
            [_ffmpeg(), "-nostdin", "-hide_banner", "-i", str(wav),
             "-af", f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_MIN}",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=1800,
        )
    except Exception as exc:  # noqa: BLE001
        log.info("silencedetect unavailable (%s); cutting on the probe grid", exc)
        return []

    points: list[float] = []
    pending: float | None = None
    for line in (proc.stderr or "").splitlines():
        start = _SILENCE_START.search(line)
        if start:
            pending = float(start.group(1))
            continue
        stop = _SILENCE_END.search(line)
        if stop and pending is not None:
            points.append((pending + float(stop.group(1))) / 2.0)
            pending = None
    return points


def snap(boundary: float, silences: list[float], *, window: float = SNAP_SECONDS) -> float:
    """Move `boundary` to the nearest silence within `window` seconds."""
    if not silences:
        return boundary
    nearest = min(silences, key=lambda s: abs(s - boundary))
    return nearest if abs(nearest - boundary) <= window else boundary


# --------------------------------------------------------------------------
# Language probing (pure parsing + grouping, so it is testable without whisper)
# --------------------------------------------------------------------------


def parse_detections(stderr: str) -> list[tuple[str, str, float]]:
    """(file, language, probability) for each probed window, in order.

    whisper.cpp prints a `processing '<path>'` line then an `auto-detected
    language: xx (p = 0.99)` line for each input file, both on stderr.
    """
    out: list[tuple[str, str, float]] = []
    current: str | None = None
    for line in stderr.splitlines():
        seen = _PROCESSING.search(line)
        if seen:
            current = seen.group(1)
            continue
        found = _DETECTED.search(line)
        if found and current is not None:
            out.append((current, found.group(1).lower(), float(found.group(2))))
            current = None
    return out


def build_runs(
    windows: list[tuple[float, float, str, float]],
    *,
    allowed: tuple[str, ...] = ("fr", "en"),
    min_prob: float = MIN_LANG_PROB,
    min_run: float = MIN_RUN_SECONDS,
) -> list[LanguageRun]:
    """Turn per-window detections into contiguous language runs.

    `windows` is (start, end, lang, prob), in order. Three cleanups, in order:
    a window that is unconfident or names an unexpected language inherits from
    its neighbours; contiguous windows of one language merge; and a run too
    short to be a real switch is absorbed by its longer neighbour. Without the
    last step a single noisy window would cut the lecture into three pieces
    and cost a word at each cut.
    """
    if not windows:
        return []

    allow = tuple(a.lower() for a in allowed) if allowed else ()
    labelled: list[str | None] = []
    for _, _, lang, prob in windows:
        ok = prob >= min_prob and (not allow or lang in allow)
        labelled.append(lang if ok else None)

    if all(x is None for x in labelled):
        # Nothing was confident. Fall back to the single most probable window.
        best = max(windows, key=lambda w: w[3])
        return [LanguageRun(windows[0][0], windows[-1][1], best[2], best[3])]

    # Inherit backwards, then forwards, so leading unknowns get filled too.
    for i in range(len(labelled)):
        if labelled[i] is None and i > 0:
            labelled[i] = labelled[i - 1]
    for i in range(len(labelled) - 1, -1, -1):
        if labelled[i] is None and i + 1 < len(labelled):
            labelled[i] = labelled[i + 1]

    runs: list[LanguageRun] = []
    for (start, end, _, prob), lang in zip(windows, labelled):
        if runs and runs[-1].lang == lang:
            last = runs[-1]
            runs[-1] = LanguageRun(last.start, end, lang, max(last.confidence, prob))
        else:
            runs.append(LanguageRun(start, end, lang or "", prob))

    return _absorb_short(runs, min_run)


def _absorb_short(runs: list[LanguageRun], min_run: float) -> list[LanguageRun]:
    """Drop runs shorter than `min_run`, then re-merge neighbours that match.

    A swallowed run whose language differs from its host is remembered on the
    host, because that audio will now be decoded under the wrong language and
    will therefore produce nothing at all.
    """
    while len(runs) > 1:
        shortest = min(range(len(runs)), key=lambda i: runs[i].seconds())
        if runs[shortest].seconds() >= min_run:
            break
        victim = runs.pop(shortest)

        def host(target: LanguageRun, start: float, end: float) -> LanguageRun:
            lost = target.absorbed + victim.absorbed
            if victim.lang and victim.lang != target.lang:
                lost = lost + ((victim.start, victim.end, victim.lang),)
            return LanguageRun(start, end, target.lang, target.confidence, lost)

        if shortest == 0:
            runs[0] = host(runs[0], victim.start, runs[0].end)
        elif shortest >= len(runs):
            runs[-1] = host(runs[-1], runs[-1].start, victim.end)
        else:
            before, after = runs[shortest - 1], runs[shortest]
            if before.seconds() >= after.seconds():
                runs[shortest - 1] = host(before, before.start, victim.end)
            else:
                runs[shortest] = host(after, victim.start, after.end)

        merged: list[LanguageRun] = []
        for run in runs:
            if merged and merged[-1].lang == run.lang:
                last = merged[-1]
                merged[-1] = LanguageRun(
                    last.start, run.end, run.lang,
                    max(last.confidence, run.confidence),
                    last.absorbed + run.absorbed,
                )
            else:
                merged.append(run)
        runs = merged
    return runs


def detect_runs(
    wav: Path,
    binary: str,
    model: Path,
    *,
    allowed: tuple[str, ...] = ("fr", "en"),
    window_seconds: int = PROBE_SECONDS,
    logger: logging.Logger | None = None,
    vad: Path | None = None,
) -> list[LanguageRun]:
    """Probe the file in short windows and return contiguous language runs.

    All windows go through ONE whisper-cli invocation, so the 1.5 GB model is
    loaded once. Measured: 8 windows of a 108 s clip probed in 4.4 s total.

    Deliberately NOT run through VAD, though that sounds like it should help.
    Measured on a noisy classroom recording: VAD-filtered probing invented two
    English runs covering 75 s that decoded to "- Well there." and "- Okay.",
    and cost 63 words of French. Whisper reports a confident language from a
    couple of seconds of speech, so removing silence concentrates the noise
    rather than suppressing it. The yield check in `transcribe` is what
    actually catches a wrong pin.
    """
    log = logger or logging.getLogger("school.transcribe")
    total = _duration(wav)

    with tempfile.TemporaryDirectory(prefix="school-probe-") as tmp:
        probe_dir = Path(tmp)
        proc = subprocess.run(
            [_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
             "-i", str(wav), "-f", "segment",
             "-segment_time", str(window_seconds),
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
             str(probe_dir / "w%05d.wav")],
            capture_output=True, text=True, timeout=1800,
        )
        chunks = sorted(probe_dir.glob("w*.wav"))
        if proc.returncode != 0 or not chunks:
            log.warning("Could not split for language probing; using auto on the whole file")
            return []

        probe_cmd = [binary, "-m", str(model), "-l", "auto", "-dl"]
        if vad is not None:
            probe_cmd += ["--vad", "-vm", str(vad), "-vt", VAD_THRESHOLD]
        probe_cmd += [str(c) for c in chunks]
        detect = subprocess.run(
            probe_cmd, capture_output=True, text=True, timeout=3600
        )
        found = parse_detections(detect.stderr or "")
        by_name = {Path(f).name: (lang, prob) for f, lang, prob in found}

        windows: list[tuple[float, float, str, float]] = []
        for index, chunk in enumerate(chunks):
            lang, prob = by_name.get(chunk.name, ("", 0.0))
            start = index * float(window_seconds)
            end = min(start + window_seconds, total) if total else start + window_seconds
            if index == len(chunks) - 1 and total:
                end = max(end, total)
            windows.append((start, end, lang, prob))

    if not windows:
        return []
    runs = build_runs(windows, allowed=allowed)
    for run in runs:
        log.info(
            "Language run: %6.1f-%6.1f s  %s  (p=%.2f)",
            run.start, run.end, run.lang or "?", run.confidence,
        )
    return runs


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------


def parse_segments(stdout: str, *, offset: float = 0.0) -> list[tuple[float, str]]:
    """(absolute start seconds, text) for each timestamped whisper segment.

    Lines that carry no timestamp are kept with the previous segment's time so
    nothing is ever silently dropped by the parser itself.
    """
    out: list[tuple[float, str]] = []
    for line in stdout.splitlines():
        match = _TS_LINE.match(line.strip())
        if match:
            hours, minutes, seconds, millis, text = match.groups()
            when = (
                int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0
            )
            text = text.strip()
            if text:
                out.append((offset + when, text))
            continue
        stray = line.strip()
        if stray and not stray.startswith("["):
            out.append((offset + (out[-1][0] if out else 0.0), stray))
    return out


#: Decode this many seconds at a time, and no more.
#:
#: whisper carries its own previous output forward as context. On clean audio
#: that is what makes it coherent. On a lecture recorded across a room it is
#: what makes it spiral: once it emits a wrong line, that line conditions the
#: next one, and the classic failure is a single sentence repeating until the
#: file ends.
#:
#: Handing it one minute at a time forces the context to reset every minute.
#: Measured over 27 minutes of real lecture audio, scored by blind judges who
#: could not see which method produced which transcript:
#:
#:                        recovered   invented   duplicated   longest loop
#:     whole file              7.75       2.80         1.40              9
#:     60 s windows            7.70       1.60         0.15              1
#:
#: Same content recovered, close to half the invention, and the loops stop. The
#: raw word count is slightly LOWER (1942 against 2018) and that is the point:
#: the missing words were the repeats.
#:
#: Do NOT add overlap between windows. It looks like the obvious fix for a word
#: cut in half at a boundary, and it measured worse than either option here:
#: 60 s windows overlapping by 10 s scored 6.60 for duplicated content against
#: 0.15, because whisper re-words the repeated seconds just differently enough
#: that no dedupe catches it. An automatic near-duplicate check cleared it and
#: the judges did not, which is why it is written down here.
WINDOW_SECONDS = 60.0


def _decode(
    wav: Path,
    *,
    binary: str,
    model: Path,
    language: str,
    prompt: str,
    offset: float,
    timeout: int,
    log: logging.Logger,
    vad: Path | None = None,
) -> list[tuple[float, str]]:
    """Transcribe a wav, one WINDOW_SECONDS slice at a time.

    See WINDOW_SECONDS for why this is not simply handed to whisper whole.
    """
    duration = _duration(wav)
    if duration <= WINDOW_SECONDS * 1.5:
        return _decode_once(
            wav, binary=binary, model=model, language=language, prompt=prompt,
            offset=offset, timeout=timeout, log=log, vad=vad,
        )

    out: list[tuple[float, str]] = []
    with tempfile.TemporaryDirectory(prefix="school-window-") as tmp:
        start = 0.0
        index = 0
        while start < duration - 0.05:
            end = min(start + WINDOW_SECONDS, duration)
            piece = Path(tmp) / f"w{index:04d}.wav"
            # The last window runs to the end: passing an explicit -to there can
            # clip the final syllable when the duration probe rounds down.
            _slice(wav, piece, start, None if end >= duration - 0.05 else end)
            out += _decode_once(
                piece, binary=binary, model=model, language=language, prompt=prompt,
                offset=offset + start, timeout=timeout, log=log, vad=vad,
            )
            start, index = end, index + 1
    return out


def _decode_once(
    wav: Path,
    *,
    binary: str,
    model: Path,
    language: str,
    prompt: str,
    offset: float,
    timeout: int,
    log: logging.Logger,
    vad: Path | None = None,
) -> list[tuple[float, str]]:
    """One whisper pass. Always WITH timestamps; see the module docstring."""
    cmd = [binary, "-m", str(model), "-l", language or "auto", "-f", str(wav)]
    if vad is not None:
        cmd += ["--vad", "-vm", str(vad), "-vt", VAD_THRESHOLD]
    if prompt:
        cmd += ["--prompt", prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise TranscriptionError(
            f"whisper failed on {wav.name}: {(proc.stderr or '')[-300:]}"
        )
    segments = parse_segments(proc.stdout or "", offset=offset)
    if not segments:
        log.warning("whisper produced no text for %s (%s)", wav.name, language)
    return segments


def yielded_enough(
    segments: list[tuple[float, str]], seconds: float,
    *, floor: float = MIN_WORDS_PER_MINUTE,
) -> bool:
    """Did this decode produce a plausible amount of text for its length?"""
    if seconds <= 0:
        return True
    words = sum(len(text.split()) for _, text in segments)
    return words >= floor * (seconds / 60.0)


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def repetition(text: str) -> tuple[float, float]:
    """(unique bigram ratio, gzip compression ratio) for a stretch of transcript.

    Both catch the same failure from opposite sides: when whisper is handed a
    room where twenty people talk at once it stops transcribing and starts
    looping a phrase. Unique bigrams collapse and the text becomes very
    compressible. Neither measure depends on microphone distance or room tone,
    which is why they work where a loudness threshold does not.
    """
    words = re.findall(r"\w+", text.lower())
    bigrams = list(zip(words, words[1:]))
    unique = len(set(bigrams)) / len(bigrams) if bigrams else 1.0
    raw = text.encode("utf-8")
    ratio = len(raw) / max(1, len(zlib.compress(raw, 6))) if raw else 1.0
    return unique, ratio


def _normalise(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


def collapse_repeats(
    segments: list[tuple[float, str]], *, min_run: int = REPEAT_RUN
) -> list[tuple[float, str, int]]:
    """Fold consecutive identical lines into (time, text, count).

    Forty identical lines of a hallucinated subtitle credit tell a reader one
    thing, and telling them forty times wastes the context window of whatever
    ends up reading the transcript.
    """
    folded: list[tuple[float, str, int]] = []
    index = 0
    while index < len(segments):
        when, text = segments[index]
        key = _normalise(text)
        ahead = index + 1
        while ahead < len(segments) and _normalise(segments[ahead][1]) == key:
            ahead += 1
        folded.append((when, text, ahead - index))
        index = ahead
    return folded


def find_gaps(
    segments: list[tuple[float, str]],
    total: float = 0.0,
    *,
    min_seconds: float = MIN_GAP_SECONDS,
) -> list[tuple[float, float]]:
    """Stretches where nothing was transcribed for a long time.

    This is deliberately "no speech transcribed" and not "silence": the cause
    might be a break, a film, an exam, or a room too loud to transcribe. The
    marker says what is known and leaves the interpretation open.
    """
    gaps: list[tuple[float, float]] = []
    previous = 0.0
    for when, _ in segments:
        if when - previous >= min_seconds:
            gaps.append((previous, when))
        previous = max(previous, when)
    if total and total - previous >= min_seconds:
        gaps.append((previous, total))
    return gaps


def find_unreliable(
    segments: list[tuple[float, str]],
    *,
    window: float = UNRELIABLE_WINDOW,
    min_words: int = UNRELIABLE_MIN_WORDS,
    max_bigram: float = UNRELIABLE_BIGRAM,
    max_gzip: float = UNRELIABLE_GZIP,
    gzip_bigram: float = UNRELIABLE_GZIP_BIGRAM,
) -> list[tuple[float, float, str]]:
    """Windows where the transcript looks like whisper looping, not speech.

    Returns (start, end, reason). "silence" means one stock line repeated, which
    is what whisper emits for silence or unintelligible noise. "brouhaha" means
    varied but degenerate text, which is what a room of people talking at once
    produces. They read differently to a human, so they are worded differently.
    """
    if not segments:
        return []
    first, last = segments[0][0], segments[-1][0]
    flagged: list[list] = []
    start = first
    while start <= last:
        stop = start + window
        chunk = [text for when, text in segments if start <= when < stop]
        if chunk:
            joined = " ".join(chunk)
            counts: dict[str, int] = {}
            for text in chunk:
                key = _normalise(text)
                if key:
                    counts[key] = counts.get(key, 0) + 1
            dominant = max(counts.values()) / len(chunk) if counts else 0.0
            stuck = (
                len(chunk) >= REPEAT_MIN_SEGMENTS and dominant >= REPEAT_DOMINANCE
            )
            reason = "silence" if stuck else ""
            if not reason and len(re.findall(r"\w+", joined)) >= min_words:
                unique, ratio = repetition(joined)
                if unique <= max_bigram or (ratio >= max_gzip and unique <= gzip_bigram):
                    reason = "brouhaha"
            if reason:
                if flagged and start - flagged[-1][1] < 1.0:
                    flagged[-1][1] = stop
                    if flagged[-1][2] != reason:
                        flagged[-1][2] = "mixte"
                else:
                    flagged.append([start, stop, reason])
        start = stop
    return [(a, b, why) for a, b, why in flagged]


def _minutes(seconds: float) -> str:
    total = int(round(seconds / 60.0))
    return "1 min" if total <= 1 else f"{total} min"


_WHY = {
    "silence": "aucune parole intelligible, le logiciel a répété une phrase toute faite",
    "brouhaha": "plusieurs personnes parlent en même temps",
    "mixte": "passage bruyant, par moments inintelligible",
}


def timeline(
    segments: list[tuple[float, str]],
    total: float,
    gaps: list[tuple[float, float]],
    unreliable: list[tuple[float, float, str]],
    skipped: list[tuple[float, float, str]] | None = None,
) -> str:
    """A short "what happened when" block, for a reader who cannot hear it."""
    if not total:
        return ""
    quiet = sum(b - a for a, b in gaps)
    doubtful = sum(b - a for a, b, _ in unreliable)
    usable = max(0.0, total - quiet - doubtful)
    lines = [
        "## Déroulement",
        "",
        f"- Enregistrement de {_minutes(total)}, dont environ "
        f"{_minutes(usable)} de parole transcrite de façon fiable.",
    ]
    for start, end in gaps:
        lines.append(
            f"- {_clock(start)} à {_clock(end)} : aucune parole transcrite "
            f"pendant {_minutes(end - start)}."
        )
    for start, end, why in unreliable:
        lines.append(
            f"- {_clock(start)} à {_clock(end)} : transcription peu fiable, "
            f"{_WHY.get(why, _WHY['mixte'])}."
        )
    for start, end, lang in skipped or []:
        name = _LANG_FR.get(lang, lang or "une autre langue")
        lines.append(
            f"- {_clock(start)} : environ {int(round(end - start))} s en {name} "
            "non transcrites, passage trop court pour être isolé."
        )
    return "\n".join(lines)


def render(
    runs: list[tuple[LanguageRun, list[tuple[float, str]]]],
    *,
    timestamps: bool = False,
    total: float = 0.0,
    annotate: bool = True,
    skipped: list[tuple[float, float, str]] | None = None,
) -> str:
    """Assemble decoded runs into the transcript body.

    Markers are woven in at the point they apply rather than collected in the
    header, because a retrieval system will hand a model one chunk from the
    middle of a three-hour transcript and the header will not be in it.
    """
    flat: list[tuple[float, str]] = [seg for _, segs in runs for seg in segs]
    gaps = find_gaps(flat, total) if annotate else []
    unreliable = find_unreliable(flat) if annotate else []
    lost = sorted(skipped or []) if annotate else []
    multi = len({run.lang for run, _ in runs}) > 1

    lines: list[str] = []
    if annotate and total:
        block = timeline(flat, total, gaps, unreliable, lost)
        if block:
            lines.extend([block, ""])

    pending_gaps = list(gaps)
    pending_lost = list(lost)
    pending_bad = list(unreliable)
    open_bad: tuple[float, float, str] | None = None

    for run, segments in runs:
        if multi:
            name = _LANG_FR.get(run.lang, run.lang or "inconnue")
            if lines:
                lines.append("")
            lines.extend([f"**[Passage en {name}]**", ""])
        for when, text, count in collapse_repeats(segments):
            while pending_lost and pending_lost[0][0] <= when:
                begin, stop, lang = pending_lost.pop(0)
                name = _LANG_FR.get(lang, lang or "une autre langue")
                lines.extend([
                    "",
                    f"**[Ici, environ {int(round(stop - begin))} s en {name} "
                    "n'ont pas été transcrites : le passage est trop court pour "
                    "être isolé de façon fiable.]**",
                    "",
                ])
            while pending_gaps and pending_gaps[0][1] <= when:
                start, end = pending_gaps.pop(0)
                lines.extend([
                    "",
                    f"**[Aucune parole transcrite pendant {_minutes(end - start)}, "
                    f"de {_clock(start)} à {_clock(end)}.]**",
                    "",
                ])
            if open_bad and when >= open_bad[1]:
                lines.extend(["", "**[Fin du passage peu fiable.]**", ""])
                open_bad = None
            if not open_bad and pending_bad and pending_bad[0][0] <= when < pending_bad[0][1]:
                open_bad = pending_bad.pop(0)
                lines.extend([
                    "",
                    f"**[Transcription peu fiable à partir d'ici : "
                    f"{_WHY.get(open_bad[2], _WHY['mixte'])}. Le texte qui suit ne "
                    "doit pas être cité comme les paroles de l'enseignant.]**",
                    "",
                ])
            if count >= REPEAT_RUN:
                lines.append(
                    f"**[La même ligne revient {count} fois ici : {text.strip()!r}. "
                    "C'est une invention du logiciel de transcription, qui répond "
                    "ainsi au silence ou à un bruit inintelligible.]**"
                )
            else:
                for _ in range(count):
                    lines.append(f"[{_clock(when)}] {text}" if timestamps else text)
    if open_bad:
        lines.extend(["", "**[Fin du passage peu fiable.]**"])
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def transcribe(
    audio: Path,
    *,
    language: str = "auto",
    model: str = "",
    header: str = "",
    logger: logging.Logger | None = None,
    timeout: int = 7200,
    languages: tuple[str, ...] = ("fr", "en"),
    timestamps: bool = False,
    prompt: str = "",
    use_vad: bool = True,
    vad_model: str = "",
) -> Path:
    """Transcribe `audio` and write a .md beside it. Returns the transcript path.

    `language="auto"` probes the file and decodes each language run separately.
    Any other value pins that language for the whole file and skips probing.
    """
    log = logger or logging.getLogger("school.transcribe")
    audio = Path(audio)
    if not audio.exists():
        raise TranscriptionError(f"No such audio file: {audio}")

    binary = find_whisper()
    if binary is None:
        raise TranscriptionError(
            "whisper.cpp not found. Install it with: brew install whisper-cpp"
        )
    model_path = find_model(model)
    if model_path is None:
        raise TranscriptionError(
            "No whisper model found. Download one into ~/.cache/whisper/, e.g.\n"
            "  curl -L -o ~/.cache/whisper/ggml-large-v3-turbo.bin \\\n"
            "    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
            "ggml-large-v3-turbo.bin"
        )

    vad = find_vad(vad_model) if use_vad else None
    if use_vad and vad is None:
        log.warning(
            "No Silero VAD model found; transcribing without it. Whisper will "
            "invent filler over silence. Fetch it with: make fetch-vad"
        )

    wav = audio.with_suffix(".16k.wav")
    decoded: list[tuple[LanguageRun, list[tuple[float, str]]]] = []
    try:
        _to_wav16k(audio, wav)
        total = _duration(wav)

        runs: list[LanguageRun] = []
        if (language or "auto").lower() == "auto":
            runs = detect_runs(
                wav, binary, model_path, allowed=languages, logger=log
            )
        if not runs:
            runs = [LanguageRun(0.0, total, language if language != "auto" else "auto", 0.0)]

        if len(runs) == 1:
            # Fast path. One decode over the whole file keeps whisper's
            # long-form context intact and avoids every cut artefact.
            log.info("Transcribing %s (%s) with %s...", audio.name, runs[0].lang, model_path.name)
            decoded.append((
                runs[0],
                _decode(wav, binary=binary, model=model_path, language=runs[0].lang,
                        prompt=prompt, offset=0.0, timeout=timeout, log=log, vad=vad),
            ))
        else:
            silences = silence_midpoints(wav, logger=log)
            cuts = [runs[0].start] + [snap(r.start, silences) for r in runs[1:]] + [total]
            log.info(
                "Transcribing %s in %d language runs (%s)...",
                audio.name, len(runs), ", ".join(r.lang for r in runs),
            )
            # The language the recording is mostly in. A run that disagrees with
            # it is the one worth double-checking, because a wrong pin deletes.
            tally: dict[str, float] = {}
            for run in runs:
                tally[run.lang] = tally.get(run.lang, 0.0) + run.seconds()
            dominant = max(tally, key=tally.get) if tally else ""

            with tempfile.TemporaryDirectory(prefix="school-runs-") as tmp:
                for index, run in enumerate(runs):
                    start, end = cuts[index], cuts[index + 1]
                    if end - start <= 0.05:
                        continue
                    piece = Path(tmp) / f"run{index:02d}.wav"
                    _slice(wav, piece, start, end if index < len(runs) - 1 else None)
                    segments = _decode(
                        piece, binary=binary, model=model_path, language=run.lang,
                        prompt=prompt, offset=start, timeout=timeout, log=log, vad=vad,
                    )
                    lang = run.lang
                    if (
                        run.lang != dominant
                        and dominant
                        and not yielded_enough(segments, end - start)
                    ):
                        # Pinning this language returned almost nothing, which is
                        # what a wrong guess looks like. Try the dominant language
                        # and keep whichever actually recovered speech.
                        retry = _decode(
                            piece, binary=binary, model=model_path, language=dominant,
                            prompt=prompt, offset=start, timeout=timeout, log=log,
                            vad=vad,
                        )
                        got = sum(len(t.split()) for _, t in segments)
                        again = sum(len(t.split()) for _, t in retry)
                        log.info(
                            "Run %.0f-%.0f s pinned to %s yielded %d words; %s "
                            "yielded %d. Keeping %s.",
                            start, end, run.lang, got, dominant, again,
                            dominant if again > got else run.lang,
                        )
                        if again > got:
                            segments, lang = retry, dominant
                    decoded.append((
                        LanguageRun(start, end, lang, run.confidence, run.absorbed),
                        segments,
                    ))
    finally:
        wav.unlink(missing_ok=True)

    lost = [span for run, _ in decoded for span in run.absorbed]
    text = render(decoded, timestamps=timestamps, total=total, skipped=lost)
    if not text:
        raise TranscriptionError(f"whisper produced no text for {audio.name}")

    out = audio.with_suffix(".md")
    body = f"{header.rstrip()}\n\n{text}\n" if header else f"{text}\n"
    out.write_text(body, encoding="utf-8")
    log.info("Transcript written: %s (%d chars)", out.name, len(text))
    return out


def transcript_header(
    course_label: str,
    class_no: str | None,
    when,
    source: str,
    *,
    room: str = "",
    block: str = "",
    teacher: str = "",
    languages: str = "",
    minutes: int = 0,
) -> str:
    """A front-matter block so the file is self-describing to a human or an
    assistant reading the folder cold."""
    number = f"Cours {class_no}" if class_no else "Cours ?"
    blocks = {"T": "théorie", "L": "laboratoire", "E": "encadrement"}
    lines = [f"# {number} : {course_label}", "", f"- **Date**: {when:%A %d %B %Y}",
             f"- **Classe**: {number}", f"- **Cours**: {course_label}"]
    if teacher:
        lines.append(f"- **Enseignant**: {teacher}")
    if room:
        lines.append(f"- **Local**: {room}")
    if block:
        lines.append(f"- **Type**: {blocks.get(block, block)}")
    if minutes:
        lines.append(f"- **Durée**: {minutes} min")
    if languages:
        lines.append(f"- **Langues**: {languages}")
    lines.append(f"- **Source**: {source}")
    lines.append("- **Transcription**: whisper.cpp large-v3-turbo, local")
    return "\n".join(lines) + "\n"
