"""Live transcription: the transcript grows while the class is still running.

The recorder writes audio in short segments. This module transcribes each
segment as soon as ffmpeg finishes writing it and rewrites a Markdown file in
the course folder, so an assistant pointed at that folder mid-class can answer
"what did the prof say ten minutes ago".

It is deliberately a SEPARATE launchd job from the recorder tick. The recorder
tick asks CoreLocation where the Mac is, which can block for 25 seconds, and
that is far too heavy to run every minute. This job does nothing at all unless
a capture is already in progress, so ticking it often is cheap.

Quality note: a live pass sees one 60-second segment at a time, so it cannot
use the whole-file language runs or long-form context that `transcribe.py`
uses. It is the rough draft. When the class ends the recorder runs the full
pass over the joined audio and overwrites this file with the good version.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src.common import Config, ConfigError, load_config, setup_logging

REPO_ROOT = Path(__file__).resolve().parents[1]

CHUNK = re.compile(r"chunk_(\d{6})_(\d+)\.m4a$")


def chunk_index(path: Path) -> tuple[str, int]:
    match = CHUNK.search(path.name)
    return (match.group(1), int(match.group(2))) if match else (path.name, 0)


def finished_chunks(session: Path) -> list[Path]:
    """Segments ffmpeg has finished with.

    The newest file is still being written, so it is never included. ffmpeg
    only opens the next segment once the previous one is closed, which makes
    "a higher-numbered file exists" a reliable completion test and avoids
    transcribing a half-written chunk.
    """
    chunks = sorted(session.glob("chunk_*.m4a"), key=chunk_index)
    return chunks[:-1] if len(chunks) > 1 else []


def pending(session: Path) -> list[Path]:
    """Finished segments that have not been transcribed yet."""
    return [c for c in finished_chunks(session) if not c.with_suffix(".txt").exists()]


def duration(path: Path) -> float:
    from src.transcribe import _duration

    return _duration(path)


def transcribe_chunk(chunk: Path, offset: float, cfg: Config, log: logging.Logger) -> str:
    """Transcribe one segment. Returns timestamped lines, or "" on failure.

    Never raises: a failed segment must not stop the ones after it, and must
    never touch the recording, which is the artefact that matters.
    """
    from src.transcribe import (
        _decode, _to_wav16k, find_model, find_vad, find_whisper, _clock,
    )

    binary, model = find_whisper(), find_model(cfg.whisper_model)
    if binary is None or model is None:
        log.warning("whisper or model missing; skipping live pass")
        return ""

    wav = chunk.with_suffix(".16k.wav")
    try:
        _to_wav16k(chunk, wav)
        segments = _decode(
            wav, binary=binary, model=model,
            language=cfg.transcribe_language or "auto",
            prompt="", offset=offset, timeout=600, log=log,
            vad=find_vad(cfg.whisper_vad_model) if cfg.whisper_vad else None,
        )
    except Exception as exc:  # noqa: BLE001 - a bad segment is not fatal
        log.warning("Live pass failed on %s: %s", chunk.name, exc)
        return ""
    finally:
        wav.unlink(missing_ok=True)

    return "\n".join(f"[{_clock(when)}] {text}" for when, text in segments)


def live_path(cfg: Config, course, when: datetime) -> Path:
    from src.recorder import recording_dir, recording_stem

    folder = recording_dir(cfg, course)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{recording_stem(cfg, course, when)}.md"


def rebuild(cfg: Config, session: Path, course, entry, when: datetime) -> Path | None:
    """Rewrite the live transcript from every segment transcribed so far."""
    from src.recorder import class_label

    parts = sorted(session.glob("chunk_*.txt"), key=lambda p: chunk_index(p.with_suffix(".m4a")))
    if not parts:
        return None
    body = "\n".join(t for t in (p.read_text(encoding="utf-8").strip() for p in parts) if t)
    if not body:
        return None

    number = class_label(cfg, course.code, when)
    blocks = {"T": "théorie", "L": "laboratoire", "E": "encadrement"}
    minutes = int(sum(duration(p.with_suffix(".m4a")) for p in parts) // 60)
    head = [
        f"# Cours {number or '?'} : {course.label()} (EN COURS)",
        "",
        "- **Statut**: transcription EN DIRECT. Le cours n'est pas terminé.",
        "  Ce fichier est réécrit toutes les minutes et sera remplacé à la fin",
        "  du cours par une version complète et plus exacte.",
        f"- **Date**: {when:%A %d %B %Y}",
        f"- **Cours**: {course.label()}",
    ]
    if course.teacher:
        head.append(f"- **Enseignant**: {course.teacher}")
    if entry.room:
        head.append(f"- **Local**: {entry.room}")
    if entry.block:
        head.append(f"- **Type**: {blocks.get(entry.block, entry.block)}")
    head += [
        f"- **Transcrit jusqu'ici**: {minutes} min depuis le début du cours.",
        "- **Qualité**: passe rapide, une minute à la fois. La version finale",
        "  corrige les passages en anglais et les coupures entre les segments.",
        "",
    ]
    out = live_path(cfg, course, when)
    out.write_text("\n".join(head) + "\n" + body + "\n", encoding="utf-8")
    return out


#: Warn after this many finished minutes of a capture with no sound in it.
#: Two is enough to be sure and early enough to still rescue the class.
SILENCE_ALARM_AFTER = 2


def warn_if_silent(session: Path, course, log: logging.Logger, notify_cfg) -> bool:
    """Notify once, loudly, if the capture in progress is recording nothing.

    This exists because a silent capture has now happened twice for two entirely
    different reasons, and neither one produced an error anywhere:

      2026-08-26  macOS had not granted the microphone. ffmpeg received zeroes.
      2026-08-27  avfoundation renumbered its devices overnight and "-i :0" had
                  become an output device. ffmpeg received zeroes.

    Both were only visible in the audio itself, and both were found by accident.
    finalize() already checks at the end of the class, which tells you the
    lecture is gone rather than saving it. Two minutes in, there is still time
    to do something about it.
    """
    from src.recorder import notify
    from src.transcribe import SILENT_PEAK_DB, peak_db

    marker = session / "silence-warned"
    if marker.exists():
        return False
    chunks = finished_chunks(session)
    if len(chunks) < SILENCE_ALARM_AFTER:
        return False
    try:
        if any(peak_db(c) > SILENT_PEAK_DB for c in chunks[:SILENCE_ALARM_AFTER]):
            return False
    except Exception as exc:  # noqa: BLE001 - a probe must never stop the class
        log.warning("Could not check the level of the capture: %s", exc)
        return False

    marker.write_text("silent", encoding="utf-8")
    log.error("%s is recording digital silence", course.label())
    notify(
        "Micro muet",
        f"{course.label()} : {SILENCE_ALARM_AFTER} min enregistrées sans aucun son. "
        "Vérifie le micro maintenant, le cours est en train d'être perdu.",
        critical=True,
        cfg=notify_cfg,
    )
    return True


def tick(cfg: Config, *, now: datetime | None = None, logger=None) -> str:
    """One live beat. Cheap and silent unless a capture is running."""
    log = logger or logging.getLogger("school.live")
    now = now or datetime.now()
    if not (cfg.transcribe and cfg.live_transcribe):
        return "disabled"

    from src.recorder import (
        active_entry, in_session, is_recording, parse_schedule, recordable, session_dir,
    )

    if not is_recording(cfg):
        return "idle"
    if not in_session(cfg, now):
        return "idle"
    entry = active_entry(recordable(cfg, parse_schedule(cfg.schedule)), now)
    if entry is None:
        return "idle"
    course = cfg.course_by_code(entry.course)
    if course is None:
        return "idle"

    session = session_dir(cfg, entry, now)
    if not session.exists():
        return "idle"
    # Before spending whisper on it, check there is anything in there at all.
    if warn_if_silent(session, course, log, cfg.notify):
        return "silent"

    todo = pending(session)
    if not todo:
        return "nothing-new"

    # Offsets accumulate over every earlier segment, so a timestamp in the live
    # file means the same thing it will mean in the final one.
    done = sorted(session.glob("chunk_*.m4a"), key=chunk_index)
    offsets: dict[Path, float] = {}
    running = 0.0
    for chunk in done:
        offsets[chunk] = running
        running += duration(chunk)

    for chunk in todo:
        text = transcribe_chunk(chunk, offsets.get(chunk, 0.0), cfg, log)
        chunk.with_suffix(".txt").write_text(text, encoding="utf-8")
        log.info("Live: %s -> %d chars", chunk.name, len(text))

    out = rebuild(cfg, session, course, entry, now)
    return f"transcribed {len(todo)}" + (f" -> {out.name}" if out else "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.live", description="Transcribe the class while it runs."
    )
    parser.add_argument("--config", default=str(REPO_ROOT / "config.yaml"))
    parser.add_argument("--tick", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    repo_root = config_path.resolve().parent
    log = setup_logging("live", repo_root)
    try:
        cfg = load_config(config_path, repo_root=repo_root)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    try:
        status = tick(cfg, logger=log)
    except Exception as exc:  # noqa: BLE001 - never take the recorder down
        log.exception("Live tick failed")
        return 1
    if status not in ("idle", "disabled", "nothing-new"):
        log.info("live tick: %s", status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
