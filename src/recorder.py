"""Milestone 4: class recorder.

Dormant until `schedule:` is filled in config.yaml. Every tick exits
immediately unless the wall clock is inside a scheduled window for a course
with `record: true`, so installing the launchd job early is harmless.

Design note (spec section 10): a single 5-minute tick handles on-time starts,
arriving late, opening the Mac mid-class, and resuming after a lid-close break,
because the next tick after wake simply restarts capture. Segmented output
bounds the loss from a sleep to one 5-minute chunk.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from src.common import (
    Config,
    ConfigError,
    Course,
    StateStore,
    load_config,
    notify,
    setup_logging,
    RunBusy,
    run_lock,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# 60 s, not 300. Two reasons: it bounds what a lid-close can destroy, and it
# sets how far behind the live transcript runs. A segment cannot be
# transcribed until ffmpeg closes it, so this IS the live latency floor.
SEGMENT_SECONDS = 60

#: How many packets the capture may buffer before the device starts losing
#: them, and how much loss is worth waking him up over. The queue is deep on
#: purpose: audio packets are tiny, and the cost of one being dropped is a
#: piece of a lecture that cannot be recorded again.
CAPTURE_QUEUE = 4096
CAPTURE_LOSS_WARN = 0.03
GRACE_MINUTES = 10  # spec section 10: keep recording this long past `end`
AUDIO_BITRATE = "64k"
#: The saved lecture is re-encoded once, normalised, so it can be played back.
#: Higher than the capture so that second generation costs nothing audible.
LISTENING_BITRATE = "96k"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class RecorderError(Exception):
    """Any recorder failure worth notifying about."""


@dataclass(frozen=True)
class ScheduleEntry:
    course: str
    weekday: str
    start: dtime
    end: dtime
    room: str = ""   # varies per session: Philo is C078 Thu, B035 Fri
    block: str = ""  # Omnivox activity type: T theorie, L labo, E encadrement
    # Per-slot override of the course's `record:`. None means "inherit".
    # It exists because one slot of a course can be a different KIND of event:
    # 300-204-EM's Thursday 17:10 E block is encadrement, where the talking is
    # other students asking about their own work, not a lecture.
    record: bool | None = None


# --------------------------------------------------------------------------
# Schedule logic (pure)
# --------------------------------------------------------------------------


def parse_time(value: str) -> dtime:
    match = re.fullmatch(r"\s*(\d{1,2})\s*[:hH]\s*(\d{2})\s*", str(value))
    if not match:
        raise ConfigError(f"Bad time {value!r}; expected HH:MM")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ConfigError(f"Time out of range: {value!r}")
    return dtime(hour, minute)


def parse_schedule(raw: list[dict]) -> list[ScheduleEntry]:
    """Validate config's `schedule:` block. An empty list is valid: M4 is inert."""
    entries: list[ScheduleEntry] = []
    for index, item in enumerate(raw or []):
        where = f"schedule[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{where}: expected a mapping")
        for key in ("course", "weekday", "start", "end"):
            if not item.get(key):
                raise ConfigError(f"{where}: missing required key {key!r}")
        weekday = str(item["weekday"]).strip().lower()
        if weekday not in WEEKDAYS:
            raise ConfigError(f"{where}: weekday must be one of {list(WEEKDAYS)}, got {weekday!r}")
        start, end = parse_time(item["start"]), parse_time(item["end"])
        if end <= start:
            raise ConfigError(f"{where}: end {item['end']} must be after start {item['start']}")
        entries.append(
            ScheduleEntry(
                course=str(item["course"]), weekday=weekday, start=start, end=end,
                room=str(item.get("room", "") or ""),
                block=str(item.get("block", "") or "").strip().upper(),
                record=None if item.get("record") is None else bool(item["record"]),
            )
        )
    return entries


def active_entry(
    entries: list[ScheduleEntry], now: datetime, *, grace_minutes: int = GRACE_MINUTES
) -> ScheduleEntry | None:
    """The entry whose window contains `now`, ignoring the grace period.

    Grace applies to *finalizing*, not to starting: we do not want a tick at
    end+8min to start a fresh recording of an empty room.
    """
    weekday = WEEKDAYS[now.weekday()]
    current = now.time()
    return next(
        (e for e in entries if e.weekday == weekday and e.start <= current < e.end),
        None,
    )


def finished_entry(
    entries: list[ScheduleEntry], now: datetime, *, grace_minutes: int = GRACE_MINUTES
) -> ScheduleEntry | None:
    """An entry that ended within the grace window — time to finalize."""
    weekday = WEEKDAYS[now.weekday()]
    current = now.time()
    for entry in entries:
        if entry.weekday != weekday:
            continue
        end_dt = datetime.combine(now.date(), entry.end)
        if entry.end <= current <= (end_dt + timedelta(minutes=grace_minutes)).time():
            return entry
    return None


def in_session(cfg: Config, now: datetime) -> bool:
    """True when `now` falls on a day that actually has regular classes.

    The weekly schedule alone is not enough: without date bounds the recorder
    would happily record over the holidays, through the exam period, and on
    next semester's Tuesdays. Missing bounds mean "no bounds configured", which
    stays permissive so an unconfigured setup behaves as before.
    """
    today = now.date().isoformat()
    if cfg.semester_start and today < cfg.semester_start:
        return False
    if cfg.semester_end and today > cfg.semester_end:
        return False
    return today not in cfg.no_class_days


def class_dates(cfg: Config, course_code: str) -> list[datetime]:
    """Every date this course actually meets, in order.

    Derived from the weekly schedule, bounded by the semester, minus the days
    the calendrier scolaire marks as having no regular classes. A course with
    two weekly sessions meets twice a week, and each meeting counts.
    """
    if not (cfg.semester_start and cfg.semester_end):
        return []
    entries = [e for e in parse_schedule(cfg.schedule) if e.course == course_code]
    if not entries:
        return []
    by_weekday: dict[str, list[ScheduleEntry]] = {}
    for entry in entries:
        by_weekday.setdefault(entry.weekday, []).append(entry)

    start = date.fromisoformat(cfg.semester_start)
    end = date.fromisoformat(cfg.semester_end)
    out: list[datetime] = []
    day = start
    while day <= end:
        if day.isoformat() not in cfg.no_class_days:
            for entry in sorted(by_weekday.get(WEEKDAYS[day.weekday()], []),
                                key=lambda e: e.start):
                out.append(datetime.combine(day, entry.start))
        day += timedelta(days=1)
    return out


def teaching_weeks(cfg: Config) -> list[date]:
    """Monday of each teaching week, in order.

    A week counts only if at least one class actually happens in it, so the
    12-16 October week (férié + JP + JM + JM + JE) does not consume a number.
    """
    if not (cfg.semester_start and cfg.semester_end):
        return []
    entries = parse_schedule(cfg.schedule)
    taught = {e.weekday for e in entries}
    start = date.fromisoformat(cfg.semester_start)
    end = date.fromisoformat(cfg.semester_end)

    weeks: list[date] = []
    day = start
    while day <= end:
        if (
            WEEKDAYS[day.weekday()] in taught
            and day.isoformat() not in cfg.no_class_days
        ):
            monday = day - timedelta(days=day.weekday())
            if monday not in weeks:
                weeks.append(monday)
        day += timedelta(days=1)
    return sorted(weeks)


def class_label(cfg: Config, course_code: str, when: datetime) -> str | None:
    """What the teacher calls this class: "13", or "13a"/"13b" when the course
    meets twice in the same week.

    Teachers number by WEEK, not by meeting, so a course meeting Thursday and
    Friday of week 13 gives "13a" and "13b" in chronological order.
    """
    target = when.date()
    monday = target - timedelta(days=target.weekday())

    # Number the weeks THIS course meets, not the weeks the semester has one.
    # A week the course misses does not consume a number, because the teacher
    # numbers his own sessions: Latreille's plan de cours calls 20 October
    # "semaine 7", not 8, even though the 5-9 October week taught other
    # courses. Recherche is Tuesday-only and Tuesday 6 October ran the Monday
    # schedule, so his course simply did not meet that week.
    own_weeks: list[date] = []
    for meeting in class_dates(cfg, course_code):
        week = meeting.date() - timedelta(days=meeting.date().weekday())
        if week not in own_weeks:
            own_weeks.append(week)
    if monday not in own_weeks:
        return None
    number = own_weeks.index(monday) + 1

    # The DAYS this course meets in that same week, chronologically. Days, not
    # meetings: the suffix separates "the Thursday class" from "the Friday
    # class", so two slots on one Thursday are still one class day and share
    # one number. Without that, Recherche's 13:10 lecture and its 17:10
    # encadrement would both come out "7a" -- the loop below matched on date
    # and returned the first hit, so the second slot silently took the first
    # one's letter.
    same_week = sorted({
        d.date() for d in class_dates(cfg, course_code)
        if d.date() - timedelta(days=d.date().weekday()) == monday
    })
    if len(same_week) <= 1:
        return str(number)
    for index, meeting in enumerate(same_week):
        if meeting == target:
            return f"{number}{'abcdef'[index]}"
    return str(number)


def recording_dir(cfg: Config, course: Course) -> Path:
    """Recordings and transcripts live in their own subfolder: a semester of
    two-hour lectures would otherwise bury the course material.

    An absolute path (or one starting with ~) is treated as a root of its own,
    with the course folder hanging off it. That is how recordings are kept out
    of base_path when base_path sits inside a cloud-synced tree: a class
    recording is a recording of other people, and it should not be uploaded
    anywhere as a side effect of where the course documents happen to live.
    """
    sub = cfg.recordings_folder or "Voice"
    root = Path(sub).expanduser()
    if root.is_absolute():
        return root / course.folder
    return cfg.folder_for(course) / sub


def recording_stem(cfg: Config, course: Course, when: datetime) -> str:
    """e.g. "Cours 09 - 2026-10-16 - Histoire" — number first, because that is
    how teachers refer to a class."""
    label = class_label(cfg, course.code, when)
    prefix = f"Cours {label}" if label else "Cours ?"
    return f"{prefix} - {when:%Y-%m-%d} - {course.label()}"


def recordable(cfg: Config, entries: list[ScheduleEntry]) -> list[ScheduleEntry]:
    """Only entries that opt in.

    The course's `record:` is the default; a single schedule slot can override
    it either way. The override is what keeps the Thursday encadrement block
    out of the recorder without also silencing the lecture that shares its
    course code.
    """
    opted_in = {c.code for c in cfg.courses if c.record}
    return [
        e for e in entries
        if ((e.course in opted_in) if e.record is None else e.record)
    ]


# --------------------------------------------------------------------------
# At-school gate
# --------------------------------------------------------------------------


#: (monotonic seconds, address). The gate now runs once a minute during class
#: hours, and the public address does not change between two ticks.
_IP_CACHE: tuple[float, str] = (0.0, "")
_IP_CACHE_TTL = 120.0


def current_ip(timeout: int = 10) -> str:
    global _IP_CACHE
    seen_at, cached = _IP_CACHE
    if cached and (time.monotonic() - seen_at) < _IP_CACHE_TTL:
        return cached
    with urllib.request.urlopen("https://api.ipify.org", timeout=timeout) as response:
        address = response.read().decode("utf-8").strip()
    _IP_CACHE = (time.monotonic(), address)
    return address


def current_ssid() -> str:
    try:
        out = subprocess.run(
            ["ipconfig", "getsummary", "en0"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:  # noqa: BLE001
        return ""
    match = re.search(r"\bSSID\s*:\s*(.+)", out)
    return match.group(1).strip() if match else ""


def find_corelocation() -> str | None:
    import shutil

    found = shutil.which("CoreLocationCLI")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/CoreLocationCLI", "/usr/local/bin/CoreLocationCLI"):
        if Path(candidate).exists():
            return candidate
    return None


def current_location(timeout: int = 25) -> tuple[float, float] | None:
    """(lat, lon) from CoreLocation, or None if unavailable.

    Unlike IP or SSID this keeps working on a tethered connection and when the
    campus Wi-Fi is down, which is exactly when the other two fail.
    """
    binary = find_corelocation()
    if binary is None:
        return None
    try:
        proc = subprocess.run(
            [binary, "-once", "yes", "-format", "%latitude %longitude"],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception:  # noqa: BLE001
        return None
    parts = (proc.stdout or "").split()
    if proc.returncode != 0 or len(parts) < 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (haversine)."""
    import math

    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _by_location(cfg: Config, log) -> bool | None:
    """True/False if location could be determined, None if unavailable."""
    if not cfg.school_location.configured():
        return None
    here = current_location()
    if here is None:
        log.info("Location unavailable (CoreLocationCLI missing or permission denied)")
        return None
    metres = distance_m(here[0], here[1], cfg.school_location.lat, cfg.school_location.lon)
    log.info("Location: %.0f m from campus (radius %.0f m)", metres, cfg.school_location.radius_m)
    return metres <= cfg.school_location.radius_m


def _by_ssid(cfg: Config, log) -> bool | None:
    if not cfg.school_ssid:
        return None
    ssid = current_ssid()
    if not ssid:
        return None
    return cfg.school_ssid.casefold() in ssid.casefold()


def _by_ip(cfg: Config, log) -> bool | None:
    if not cfg.school_ip_prefix:
        return None
    try:
        return current_ip().startswith(cfg.school_ip_prefix)
    except Exception:  # noqa: BLE001
        return None


def at_school(cfg: Config, *, logger: logging.Logger | None = None) -> bool:
    """Is the Mac on campus? Fails CLOSED: never record when unsure.

    "auto" asks every configured signal and records if ANY says yes. That is
    deliberate: location survives tethering and a Wi-Fi outage, while SSID and
    IP survive location permission being denied. A false positive would need
    the Mac to be within the campus radius, which is precise enough to trust.
    """
    log = logger or logging.getLogger("school.recorder")
    mode = cfg.at_school_check

    if mode == "off":
        return True

    checks = {"location": _by_location, "ssid": _by_ssid, "ip": _by_ip}
    if mode in checks:
        verdict = checks[mode](cfg, log)
        if verdict is None:
            log.warning("at_school_check=%r could not be evaluated; not recording", mode)
            return False
        return verdict

    if mode == "auto":
        # Cheapest first, and stop at the first yes. The IP check is one cached
        # HTTP call; CoreLocationCLI can block for 25 seconds and, on a Mac that
        # has just woken up, answers "unavailable" for a quarter of an hour.
        #
        # On 2026-08-27 that cost the first 24 minutes of a Philo lecture. The
        # gate was asked at 08:13, 08:19, 08:24 and 08:29 and every one of those
        # ticks failed on location alone, because location was the only signal
        # configured. It answered at 08:34 and recording started then. Asking
        # more often would not have helped: each retry failed the same way. What
        # helps is a signal that does not need a location fix, asked first.
        order = ("ip", "ssid", "location")
        verdicts = []
        for name in order:
            verdict = checks[name](cfg, log)
            verdicts.append(verdict)
            if verdict is True:
                return True
        if all(v is None for v in verdicts):
            log.warning("No at-school signal could be evaluated; not recording")
        return False

    return False


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------


#: Microphones to record from, best first, matched on the name avfoundation
#: reports. The first one present wins.
#:
#: NOT an index. avfoundation numbers devices positionally and renumbers them
#: whenever anything registers or disappears, including virtual devices that
#: install themselves without being asked. On 2026-08-26 ":0" was the built-in
#: microphone; six hours later, with four classes about to be recorded, the
#: same ":0" was "Steam Streaming Speakers", an OUTPUT device, and the capture
#: was a clean stream of zeroes with no error anywhere. Same silence as a
#: denied microphone, entirely different cause.
MIC_PREFERENCE = ("MacBook Pro Microphone", "MacBook Air Microphone", "Built-in Microphone")

#: Names that are never a lecture microphone even when they can be opened as
#: an input. Virtual loopback devices happily return silence forever.
MIC_BLOCKLIST = ("steam streaming", "loopback", "blackhole", "soundflower", "aggregate")

_AUDIO_HEADER = re.compile(r"AVFoundation audio devices:")
_DEVICE_LINE = re.compile(r"\[(\d+)\]\s+(.+?)\s*$")


def audio_devices(binary: str | None = None) -> list[tuple[int, str]]:
    """Every avfoundation audio input, as (index, name).

    ffmpeg prints the list to stderr and exits non-zero on purpose, so the
    return code is meaningless here.
    """
    proc = subprocess.run(
        [binary or find_ffmpeg(), "-hide_banner", "-f", "avfoundation",
         "-list_devices", "true", "-i", ""],
        capture_output=True, text=True, timeout=120,
    )
    devices: list[tuple[int, str]] = []
    in_audio = False
    for line in (proc.stderr or "").splitlines():
        body = line.split("] ", 1)[-1] if "] " in line else line
        if _AUDIO_HEADER.search(line):
            in_audio = True
            continue
        if not in_audio:
            continue
        if "video devices" in line.lower():
            break
        match = _DEVICE_LINE.match(body.strip())
        if match:
            devices.append((int(match.group(1)), match.group(2).strip()))
    return devices


def resolve_microphone(
    preferred: str = "", *, binary: str | None = None, log: logging.Logger | None = None
) -> str:
    """The avfoundation input spec to record from, e.g. ":3".

    Resolved fresh for every capture, because the numbering is only valid for
    as long as the device list is unchanged. Falls back to ":0" so a capture
    still happens if the list cannot be read at all; a wrong device is
    recoverable from the audio, no capture is not.
    """
    log = log or logging.getLogger("school.recorder")
    try:
        devices = audio_devices(binary)
    except Exception as exc:  # noqa: BLE001 - never lose a class to a probe
        log.warning("Could not list audio devices (%s); falling back to :0", exc)
        return ":0"
    if not devices:
        log.warning("ffmpeg listed no audio devices; falling back to :0")
        return ":0"

    wanted = [preferred] if preferred else []
    wanted += [m for m in MIC_PREFERENCE if m != preferred]
    for name in wanted:
        for index, device in devices:
            if device.casefold() == name.casefold():
                log.info("Recording from [%d] %s", index, device)
                return f":{index}"

    for index, device in devices:
        if not any(bad in device.casefold() for bad in MIC_BLOCKLIST):
            log.warning(
                "None of the preferred microphones are present; using [%d] %s",
                index, device,
            )
            return f":{index}"

    log.error("Every audio device looks like a virtual one: %s", devices)
    return f":{devices[0][0]}"


def find_ffmpeg() -> str:
    import shutil

    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(candidate).exists():
            return candidate
    raise RecorderError("ffmpeg not found. Install it with: brew install ffmpeg")


def session_dir(cfg: Config, entry: ScheduleEntry, now: datetime) -> Path:
    return cfg.repo_root / "state" / "rec_tmp" / f"{now:%Y-%m-%d}_{entry.course}"


def pid_file(cfg: Config) -> Path:
    return cfg.repo_root / "state" / "recorder.pid"


def _ffmpeg_reason(err_path: Path, limit: int = 200) -> str:
    """The last meaningful line of an ffmpeg stderr log, for a notification.

    A microphone denied by TCC reads as "Input/output error" or "Operation not
    permitted" on the avfoundation input, which is enough to tell the difference
    between "macOS refused" and "no such device".
    """
    try:
        lines = [
            ln.strip()
            for ln in err_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip()
        ]
    except OSError:
        return "ffmpeg a quitté sans rien écrire"
    return lines[-1][:limit] if lines else "ffmpeg a quitté immédiatement"


def is_recording(cfg: Config) -> bool:
    path = pid_file(cfg)
    if not path.exists():
        return False
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        path.unlink(missing_ok=True)
        return False


def start_capture(cfg: Config, entry: ScheduleEntry, now: datetime, log: logging.Logger) -> None:
    """Launch segmented ffmpeg capture plus a caffeinate guard.

    The capture is handed to launchd rather than spawned here. That is not
    tidiness: python cannot be in the process chain or macOS denies the
    microphone outright. See src/micapp.py for the log lines that show it.
    """
    from src import micapp

    target = session_dir(cfg, entry, now)
    target.mkdir(parents=True, exist_ok=True)
    pattern = str(target / f"chunk_{now:%H%M%S}_%03d.m4a")

    cmd = [
        micapp.capture_binary(cfg.repo_root, find_ffmpeg()),
        "-nostdin",
        "-hide_banner",
        "-loglevel", "warning",
        "-f", "avfoundation",
        # avfoundation throws away buffers that arrive later than it expected,
        # and it does that BY DEFAULT. On a laptop also running a whisper pass
        # every seventy seconds, "later than expected" happens constantly: the
        # history class of 2026-08-28 lost 28.9 minutes in 78,931 separate
        # drops averaging 22 ms each, and every other class this semester lost
        # between 9 and 17 percent the same way. Nothing reported it, because
        # from ffmpeg's point of view discarding a late buffer is the correct
        # behaviour it was asked for.
        "-drop_late_frames", "false",
        # And give the input somewhere to wait. The default queue is eight
        # packets, which is nothing when the segment muxer stops to close one
        # file and open the next, 201 times over a three-hour lecture.
        "-thread_queue_size", str(CAPTURE_QUEUE),
        # Resolved by NAME every time. See MIC_PREFERENCE for what went wrong
        # when this was a hardcoded ":0".
        "-i", resolve_microphone(cfg.microphone, log=log),
        "-ac", "1",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-f", "segment",
        "-segment_time", str(SEGMENT_SECONDS),
        "-reset_timestamps", "1",
        pattern,
    ]
    log.info("Starting capture for %s -> %s", entry.course, target)

    # ffmpeg's stderr goes to a file in the session dir, never to DEVNULL. It is
    # the only place the real reason ever appears when ffmpeg does fail loudly.
    # It is worth knowing that the microphone failure does NOT appear here: a
    # denied mic gives ffmpeg a clean stream of zeroes and this file stays
    # empty. finalize() measures the audio for that reason.
    err_path = target / "ffmpeg.log"

    def fail(reason: str) -> None:
        log.error("Capture for %s failed to start: %s", entry.course, reason)
        # One notification per class, not one per 5-minute tick. Ticks keep
        # retrying on purpose: granting the mic mid-class then just works.
        marker = target / "capture-failed"
        if not marker.exists():
            marker.write_text(reason, encoding="utf-8")
            notify(
                "Enregistrement impossible",
                f"{entry.course}: {reason}",
                critical=True,
                cfg=cfg.notify,
            )

    try:
        pid = micapp.start_capture_job(cmd, err_path, cfg.repo_root / "state" / "capture.plist")
    except micapp.MicAppError as exc:
        fail(str(exc))
        return

    # No pid means ffmpeg died on startup. Writing the pid file unconditionally
    # used to make a dead capture look live for the whole class: is_recording()
    # only checks that SOME pid is alive, so the next tick returned "recording"
    # and the failure surfaced as an empty folder after the lecture, if anyone
    # went looking.
    if pid is None:
        micapp.stop_capture_job()
        fail(_ffmpeg_reason(err_path))
        return

    pid_file(cfg).write_text(str(pid), encoding="utf-8")

    # Hold the machine awake while the lid is open (spec section 10).
    subprocess.Popen(
        ["caffeinate", "-i", "-w", str(pid)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def stop_capture(cfg: Config, log: logging.Logger) -> None:
    path = pid_file(cfg)
    if not path.exists():
        return
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGINT)  # let ffmpeg close the current segment cleanly
        log.info("Stopped capture (pid %s)", pid)
    except (ValueError, ProcessLookupError, PermissionError) as exc:
        log.info("No live capture to stop (%s)", exc)
    finally:
        path.unlink(missing_ok=True)
        # The job is registered with launchd, not just running. Leaving the
        # registration behind makes the next bootstrap fail with "service
        # already loaded", which would cost tomorrow's first class.
        from src import micapp

        micapp.stop_capture_job()


def chunk_order(paths: list[Path]) -> list[Path]:
    """Sort chunks by (start time, segment index) as encoded in the filename."""

    def key(path: Path):
        match = re.search(r"chunk_(\d{6})_(\d+)", path.name)
        return (match.group(1), int(match.group(2))) if match else (path.name, 0)

    return sorted(paths, key=key)


def live_path_for(cfg: Config, course, when: datetime) -> Path:
    """Where the live pass writes. Imported lazily: src.live imports this
    module, so a module-level import would be circular."""
    from src.live import live_path

    return live_path(cfg, course, when)


def _spoken_words(path: Path) -> int:
    """Words of actual transcript, ignoring timestamps and the header.

    Used only to compare two transcripts of the SAME recording, so it does not
    need to be clever, only consistent.
    """
    body = path.read_text(encoding="utf-8")
    body = re.sub(r"\[\d\d:\d\d:\d\d\]", " ", body)
    body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith(("#", "-", "*")))
    return len(body.split())


#: How much untouched audio to keep per class, and how many classes to keep it
#: for. Sixty seconds is enough to hear and to measure; ten classes is a couple
#: of weeks, which is as far back as anyone asks.
RAW_SAMPLE_SECONDS = 60
RAW_SAMPLE_KEEP = 10


def _keep_raw_sample(cfg: Config, joined: Path, stem: str, log: logging.Logger) -> Path | None:
    """Stash a minute of the untreated recording under state/, then prune.

    Not in the course folder: this is diagnostic material, and a second audio
    file next to every lecture would be clutter he never asked for. Taken from
    ten minutes in rather than the start, because the first minutes of a class
    are shuffling and hellos, not the microphone doing its normal job.
    """
    try:
        folder = cfg.repo_root / "state" / "raw-samples"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{stem}.m4a"
        proc = subprocess.run(
            [find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
             "-ss", "600", "-t", str(RAW_SAMPLE_SECONDS), "-i", str(joined),
             "-c", "copy", "-y", str(dest)],
            capture_output=True, text=True, timeout=300,
        )
        # Seeking past the end does NOT fail. ffmpeg returns 0 and writes a
        # valid container holding nothing, about a kilobyte of headers, so the
        # return code and a zero-size check both pass and the sample is silently
        # empty. Ask how long it actually is.
        from src.transcribe import _duration

        if proc.returncode != 0 or not dest.exists() or _duration(dest) < 1.0:
            # A class shorter than ten minutes has nothing at that offset.
            subprocess.run(
                [find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-t", str(RAW_SAMPLE_SECONDS), "-i", str(joined),
                 "-c", "copy", "-y", str(dest)],
                capture_output=True, text=True, timeout=300,
            )
        old = sorted(folder.glob("*.m4a"), key=lambda f: f.stat().st_mtime)
        for stale in old[:-RAW_SAMPLE_KEEP]:
            stale.unlink(missing_ok=True)
        return dest if dest.exists() else None
    except Exception as exc:  # noqa: BLE001 - a keepsake must never cost the class
        log.warning("Could not keep a raw sample: %s", exc)
        return None


def finalize(
    cfg: Config, entry: ScheduleEntry, now: datetime, log: logging.Logger
) -> Path | None:
    """Concatenate chunks into the course folder and clean up."""
    target = session_dir(cfg, entry, now)
    if not target.exists():
        return None
    chunks = chunk_order(list(target.glob("chunk_*.m4a")))
    chunks = [c for c in chunks if c.stat().st_size > 0]
    if not chunks:
        # A class that captured nothing is the recorder's worst failure, because
        # it is indistinguishable from a class that went fine until you go
        # looking for the file weeks later. Say so, and KEEP the session dir:
        # it holds ffmpeg.log and the capture-failed marker, which are the only
        # evidence of why. Deleting it here used to destroy the diagnosis.
        reason = _ffmpeg_reason(target / "ffmpeg.log")

        # A class the gate deliberately blocked did not FAIL, and must not be
        # reported as if it had. When the gate sees you off campus it never
        # starts a capture, and this branch used to fire a critical "aucun son
        # capté" on every tick of the class hour: ten alarms in ten minutes for
        # a class nobody was trying to record.
        # _warn_gated leaves the marker that tells the two cases apart, and it
        # has already spoken once for this class.
        if (target / "gated").exists():
            log.info(
                "%s was never recorded: the at-school gate blocked it, nothing failed",
                entry.course,
            )
            return None

        # And when it IS a real failure, say so ONCE. finished_entry keeps
        # returning the class for the whole grace window, so every tick used to
        # re-run finalize and re-notify. An alarm that repeats ten times is one
        # he learns to swipe away, which defeats the point of making it critical.
        told = target / "empty-reported"
        log.error("No audio captured for %s. ffmpeg said: %s", entry.course, reason)
        if not told.exists():
            told.write_text(reason, encoding="utf-8")
            notify(
                "Cours NON enregistré",
                f"{entry.course}: aucun son capté. {reason}",
                critical=True,
                cfg=cfg.notify,
            )
        return None

    course = cfg.course_by_code(entry.course)
    if course is None:
        raise RecorderError(f"{entry.course} is scheduled but missing from config.courses")
    folder = recording_dir(cfg, course)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{recording_stem(cfg, course, now)}.m4a"

    listing = target / "concat.txt"
    listing.write_text(
        "\n".join(f"file '{c.as_posix()}'" for c in chunks) + "\n", encoding="utf-8"
    )
    joined = target / "joined.m4a"
    proc = subprocess.run(
        [find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-y", str(joined)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0 or not joined.exists() or joined.stat().st_size == 0:
        raise RecorderError(
            f"Concat failed for {entry.course}: {(proc.stderr or '').strip()[:300]}"
        )

    import shutil

    # A lecture hall picked up from the third row lands near -47 dBFS mean.
    # That is loud enough to transcribe once it is lifted, and far too quiet to
    # LISTEN to: at normal volume the recording of 2026-08-26 is barely audible
    # over a room. The transcript is the artefact that gets used, but the audio
    # is the one that gets checked when the transcript reads oddly, so it has to
    # be playable.
    #
    # This is the only place the saved file is touched. The cost is a second
    # AAC generation, which is bought at a higher bitrate than the capture and
    # is nothing next to the noise floor of a laptop mic across a classroom.
    try:
        from src.transcribe import NORMALISE_FILTER, SILENT_PEAK_DB, peak_db

        peak = peak_db(joined)
    except Exception as exc:  # noqa: BLE001 - never lose the recording over a probe
        log.warning("Could not measure the level of %s: %s", joined.name, exc)
        peak, SILENT_PEAK_DB = 0.0, -60.0
        NORMALISE_FILTER = ""

    # Keep a short piece of the audio EXACTLY as the microphone heard it, and
    # write down the level of the whole thing. Both are gone a few lines below,
    # when the session directory is removed, and once they are gone there is no
    # way to answer "what did the processing actually do to this lecture".
    # Asked that on 2026-08-28 about a class recorded that morning, the only
    # honest answer was that the evidence had been deleted at noon.
    log.info("%s: raw peak %.1f dBFS before processing", out.name, peak)
    _keep_raw_sample(cfg, joined, out.stem, log)

    normalised = False
    if NORMALISE_FILTER and peak > SILENT_PEAK_DB:
        proc = subprocess.run(
            [find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
             "-i", str(joined), "-af", NORMALISE_FILTER,
             "-c:a", "aac", "-b:a", LISTENING_BITRATE, "-y", str(out)],
            capture_output=True, text=True, timeout=3600,
        )
        normalised = proc.returncode == 0 and out.exists() and out.stat().st_size > 0
        if not normalised:
            # Keeping the quiet original beats losing the class to a filter.
            log.warning(
                "Could not normalise %s, keeping it as captured: %s",
                out.name, (proc.stderr or "").strip()[:200],
            )
    if not normalised:
        shutil.move(str(joined), str(out))

    shutil.rmtree(target, ignore_errors=True)

    # How long the file SAYS it is, and how much audio is actually in it.
    #
    # Nothing used to compare these, so a class could lose a fifth of itself in
    # silence. The container is stamped from the device clock and keeps running
    # while dropped buffers leave holes, so it describes the length of the
    # class rather than the length of the recording. Reporting the container
    # figure is how "Durée: 201 min" ended up on a file holding 172.
    declared, real = 0.0, 0.0
    try:
        from src.transcribe import _duration, audio_seconds

        declared, real = _duration(out), audio_seconds(out)
    except Exception as exc:  # noqa: BLE001 - never lose the recording over a probe
        log.warning("Could not measure the length of %s: %s", out.name, exc)

    minutes = int((real or declared) // 60)
    lost = (declared - real) / declared if declared > 0 and real > 0 else 0.0
    if lost > 0:
        log.info(
            "%s: %d min of audio in a %d min recording, %.1f%% lost at capture",
            out.name, minutes, int(declared // 60), lost * 100,
        )
    if lost > CAPTURE_LOSS_WARN:
        notify(
            "Enregistrement incomplet",
            f"{course.folder} : {int((declared - real) / 60)} min manquantes "
            f"sur {int(declared // 60)} ({lost * 100:.0f}%). "
            "Le micro a perdu des morceaux pendant le cours.",
            cfg=cfg.notify,
        )

    log.info("Finalized %s (%d min) -> %s", entry.course, minutes, out)

    # A file of the right size, the right length, and no sound in it.
    #
    # This is what a denied microphone looks like on macOS. It is NOT an error:
    # ffmpeg opens the avfoundation device, writes segments on schedule, exits
    # cleanly, and leaves ffmpeg.log empty. Everything downstream reports
    # success. On 2026-08-26 the whole Psycho lecture was captured this way and
    # the only visible symptom was an empty Voice folder, because whisper
    # returned no text and the live pass had nothing to write.
    #
    # So the check is on the audio itself, and it is loud, because the recording
    # cannot be redone.
    if peak <= SILENT_PEAK_DB:
        log.error("%s is digitally silent (%.1f dBFS peak)", out.name, peak)
        notify(
            "Enregistrement muet",
            f"{course.folder} : {minutes} min sans aucun son ({peak:.0f} dBFS). "
            "Le micro n'a probablement pas été autorisé. "
            "Réglages > Confidentialité > Microphone.",
            critical=True,
            cfg=cfg.notify,
        )

    # Transcribe locally. The transcript is the useful artefact: searchable,
    # readable by an assistant, and far cheaper for NotebookLM than audio.
    # A failure here must never lose the recording, which is already on disk.
    transcript = None
    if cfg.transcribe:
        # The live pass writes to the same path the final pass is about to
        # claim, so keep a copy of it first. It is NOT reliably the worse of
        # the two: measured on ten minutes of the 2026-08-26 lecture, recorded
        # from the furthest seat in the room, the live pass recovered ~400 real
        # words and the final pass ~250. The final slices the audio by detected
        # language runs and by silence, and on distant, gappy speech those cuts
        # land badly. Overwriting used to destroy the better transcript with no
        # trace that it had ever existed.
        live_copy = None
        live_before = live_path_for(cfg, course, now)
        if live_before.exists():
            live_copy = live_before.with_name(f"{live_before.stem} (en direct).md")
            import shutil as _sh

            _sh.copy2(live_before, live_copy)

        try:
            from src.transcribe import transcribe as run_transcribe, transcript_header

            header = transcript_header(
                course.label(), class_label(cfg, course.code, now), now, out.name,
                room=entry.room, block=entry.block, teacher=course.teacher,
                minutes=minutes,
            )
            transcript = run_transcribe(
                out, language=course.language or cfg.transcribe_language,
                model=cfg.whisper_model,
                header=header, logger=log,
                languages=cfg.transcribe_languages,
                timestamps=cfg.transcribe_timestamps,
                use_vad=cfg.whisper_vad,
                vad_model=cfg.whisper_vad_model,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Transcription failed for %s: %s", out.name, exc)
            notify("Transcription échouée", f"{out.name}: {exc}", cfg=cfg.notify)

        # Keep the live copy only when the final pass came back with less. In
        # the ordinary case the final is better and a second file in Voice/ is
        # just clutter; in the case above it is the only surviving record of
        # what was said.
        if live_copy is not None:
            keep = False
            if transcript is None:
                keep = True   # the final pass failed outright
            else:
                try:
                    keep = _spoken_words(live_copy) > _spoken_words(transcript) * 1.15
                except OSError:
                    keep = True
            if keep:
                log.warning(
                    "The live transcript is longer than the final one; keeping %s",
                    live_copy.name,
                )
                if transcript is None:
                    transcript = live_copy
            else:
                live_copy.unlink(missing_ok=True)

    # Queue for M2 exactly like a downloaded document, but ONLY ever the
    # transcript. The audio is a recording of other people and never leaves on
    # its own: no transcript, because transcription is off or because it failed,
    # means nothing is queued rather than falling back to shipping their voice.
    # `upload_transcript` is a gate, not a preference.
    to_upload = transcript if cfg.upload_transcript else None

    if to_upload is not None:
        queue = StateStore(cfg.repo_root / "state" / "upload_queue.json")
        record = {
            "course_code": course.code,
            "notebook": course.notebook,
            "path": str(to_upload),
            "filename": to_upload.name,
            "queued_at": now.isoformat(timespec="seconds"),
        }
        # The sync appends to this same file, and a class ending at 12:00
        # finalises near the 12:15 sync. A lost race here is silent: the
        # transcript stays on disk, nothing re-queues it, and it simply never
        # reaches NotebookLM. Waiting is safe because the lock is only ever
        # held for milliseconds; the uploader drops it before uploading.
        try:
            with run_lock(cfg.repo_root / "state" / "queue.lock", wait=30):
                queue.write(queue.read() + [record])
            detail = f"{course.folder}, {minutes} min, transcription en file pour NotebookLM"
        except RunBusy:
            log.error("Could not take state/queue.lock in 30s; %s not queued", to_upload.name)
            notify(
                "Transcription non mise en file",
                f"{to_upload.name} est sur le disque mais pas en file pour NotebookLM.",
                critical=True,
                cfg=cfg.notify,
            )
            detail = f"{course.folder}, {minutes} min, transcription NON mise en file"
    else:
        detail = f"{course.folder}, {minutes} min, gardé en local"

    notify("Cours enregistré", detail, cfg=cfg.notify)
    return out


# --------------------------------------------------------------------------
# Tick
# --------------------------------------------------------------------------


def _warn_gated(cfg: Config, entry: ScheduleEntry, now: datetime, log: logging.Logger) -> None:
    """Say out loud that a scheduled class is NOT being recorded.

    The gate fails closed, which is right: recording a private conversation
    because a signal was ambiguous is worse than missing a lecture. What was
    wrong is that it failed closed in silence. On 2026-08-27 the 13:10
    Recherche class went unrecorded because location was the only at-school
    signal configured and CoreLocationCLI answered "unavailable" that minute.
    The log said so; nothing else did, and from a seat in the classroom a gated
    tick and a recording tick look exactly the same.

    Once per class, not once per tick: the tick runs every five minutes for as
    long as four hours.
    """
    marker = session_dir(cfg, entry, now) / "gated"
    if marker.exists():
        return

    # Never cry about a class that is already on disk. This used to notify
    # "Recherche is NOT being recorded" minutes after that very lecture had
    # been finalised and transcribed, because by then the gate had correctly
    # noticed the Mac had left campus. The alarm was right about the gate and
    # wrong about what it meant. An alarm
    # that fires after the fact is one you learn to ignore, and this one exists
    # precisely so that it is not ignored.
    course = cfg.course_by_code(entry.course)
    if course is not None:
        done = recording_dir(cfg, course) / f"{recording_stem(cfg, course, now)}.m4a"
        if done.exists():
            log.info("%s is already recorded today; staying quiet", course.label())
            return

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(now.isoformat(timespec="seconds"), encoding="utf-8")
    label = course.label() if course else entry.course
    log.error("%s is NOT being recorded: no at-school signal", label)
    # Quiet on purpose. This fires whenever a scheduled class starts and the
    # gate says the Mac is not on campus, which includes every class nobody
    # was trying to record. That is worth a line on the lock screen, not a
    # phone. The loud tier is kept for a class that was recorded and came out
    # silent, which is the case he cannot fix afterwards.
    notify(
        "Cours non enregistré",
        f"{label} a commencé et le Mac ne se croit pas au cégep, donc "
        "il n'enregistre pas. Vérifie le Wi-Fi, ou lance : make record-now",
        level="quiet",
        cfg=cfg.notify,
    )


#: A session directory is named {YYYY-MM-DD}_{course code}. See session_dir().
_SESSION_DIR = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)$")


def sweep_orphans(cfg: Config, now: datetime, log: logging.Logger) -> list[Path]:
    """File any capture that was stopped without being filed. Returns what it filed.

    Three separate paths in tick() stop a capture: out-of-session, gated, and
    the trailing catch-all. None of them finalised, because finalising was the
    job of the ONE tick that catches a class in the act of ending. Miss that
    tick and the chunks sit in state/rec_tmp forever while the course folder
    shows nothing.

    Missing that tick is not exotic. Closing the lid is enough. On 2026-08-27 a
    capture started at 08:34 for Philo and was still running at 13:06, straight
    through the end of Philo, the whole of Littérature, and the start of the
    afternoon, because every tick that would have closed it happened while the
    Mac was asleep. Two classes, 181 chunks, 79 MB, and both course folders
    empty. The audio was all there; nothing was looking for it.

    So this does not try to catch the right moment. It runs every tick, asks
    only "is there a session directory that nothing is writing to", and files
    whatever it finds. Cheap when there is nothing, which is almost always.
    """
    root = cfg.repo_root / "state" / "rec_tmp"
    if not root.is_dir():
        return []

    live = None
    if is_recording(cfg):
        active = active_entry(recordable(cfg, parse_schedule(cfg.schedule)), now)
        if active is not None:
            live = session_dir(cfg, active, now)

    by_code = {e.course: e for e in parse_schedule(cfg.schedule)}
    filed: list[Path] = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder == live:
            continue
        match = _SESSION_DIR.match(folder.name)
        if not match:
            continue
        if not any(c.stat().st_size > 1000 for c in folder.glob("chunk_*.m4a")):
            continue
        day, code = match.group(1), match.group(2)
        entry = by_code.get(code)
        if entry is None:
            log.warning("Orphaned capture for unknown course %s in %s", code, folder.name)
            continue
        try:
            when = datetime.combine(date.fromisoformat(day), entry.start)
        except ValueError:
            log.warning("Cannot parse the date in %s", folder.name)
            continue
        log.warning("Filing an orphaned capture: %s", folder.name)
        try:
            out = finalize(cfg, entry, when, log)
        except Exception as exc:  # noqa: BLE001 - one bad session must not block the rest
            log.error("Could not file %s: %s", folder.name, exc)
            continue
        if out:
            filed.append(out)
    return filed


def tick(cfg: Config, *, now: datetime | None = None, logger=None) -> str:
    """One scheduler beat. Returns a short status string for logging/tests."""
    log = logger or logging.getLogger("school.recorder")
    now = now or datetime.now()

    # Capture is `ffmpeg -f avfoundation`, and the process that owns it is a
    # launchd job, because macOS blames the responsible process for microphone
    # access. Neither exists elsewhere. Nothing in this file used to check,
    # and the setup page's claim that recording "stays inert everywhere else"
    # was enforced by nothing at all: on Windows, launchctl raises
    # FileNotFoundError, which is not MicAppError, so it escaped into the
    # generic handler and reported "Recorder failed" once a minute, forever.
    #
    # That was invisible while Windows notifications were themselves broken.
    # They work now, so the same bug would put a toast on somebody's screen
    # every sixty seconds. Refusing quietly, once, is the whole fix.
    if sys.platform != "darwin":
        log.info("Recorder is macOS only for now; nothing to do on %s.", sys.platform)
        return "inert"

    entries = recordable(cfg, parse_schedule(cfg.schedule))
    if not entries:
        return "inert"  # schedule empty or no course opted in

    # Before deciding anything, file whatever a previous run left behind.
    sweep_orphans(cfg, now, log)

    if not in_session(cfg, now):
        if is_recording(cfg):
            stop_capture(cfg, log)
        return "out-of-session"

    active = active_entry(entries, now)
    if active:
        if not at_school(cfg, logger=log):
            if is_recording(cfg):
                stop_capture(cfg, log)
            _warn_gated(cfg, active, now, log)
            return "gated"
        if is_recording(cfg):
            return "recording"
        start_capture(cfg, active, now, log)
        return "started"

    done = finished_entry(entries, now)
    if done:
        if is_recording(cfg):
            stop_capture(cfg, log)
        # "finalized" used to be returned even when finalize() produced nothing,
        # so the log line for a lost lecture and a good one were identical.
        return "finalized" if finalize(cfg, done, now, log) else "finalize-empty"

    if is_recording(cfg):
        stop_capture(cfg, log)
        return "stopped"
    return "idle"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def mic_test(seconds: int = 5) -> int:
    out = Path("/tmp/school-mic-test.m4a")
    print(f"Recording {seconds}s from the default mic...")
    proc = subprocess.run(
        [find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "warning",
         "-f", "avfoundation", "-i", resolve_microphone(), "-t", str(seconds),
         "-ac", "1", "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-y", str(out)],
        capture_output=True, text=True, timeout=seconds + 60,
    )
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        print("Microphone capture FAILED.", file=sys.stderr)
        print((proc.stderr or "").strip()[:500], file=sys.stderr)
        print(
            "\nGrant microphone access to your terminal in:\n"
            "  System Settings > Privacy & Security > Microphone",
            file=sys.stderr,
        )
        return 2

    # Size is not proof. A microphone denied by TCC returns zeroes rather than
    # an error, so ffmpeg writes a file of exactly the right length containing
    # nothing, and this check used to call that OK. It is the whole reason the
    # Psycho lecture of 2026-08-26 was lost.
    from src.transcribe import SILENT_PEAK_DB, peak_db

    peak = peak_db(out)
    if peak <= SILENT_PEAK_DB:
        print(f"Microphone captured DIGITAL SILENCE ({peak:.0f} dBFS peak).", file=sys.stderr)
        print(
            "\nThe file is the right size and contains no sound at all, which is\n"
            "what a microphone macOS has not granted looks like.\n"
            "Run:  make setup-mic",
            file=sys.stderr,
        )
        return 2

    print(f"OK: {out} ({out.stat().st_size} bytes, {peak:.0f} dBFS peak). Playing it back...")
    subprocess.run(["afplay", str(out)], check=False)
    return 0


#: How long a sound check records. Long enough to hear a room settle and to
#: give the level meters something stable to read, short enough that nobody
#: minds running it from their seat before a class starts.
SOUND_CHECK_SECONDS = 30


def sound_check(seconds: int = SOUND_CHECK_SECONDS) -> int:
    """Record from the real microphone and keep BOTH versions, untreated and treated.

    `mic_test` answers "is the microphone working". This answers the other
    question, the one that could not be answered on 2026-08-28 about a class
    recorded that morning: what does the processing actually do to the sound of
    THIS room, from THIS seat, on THIS laptop.

    The finished lecture cannot answer it. loudnorm normalises *to* a target, so
    every processed class lands at about the same level whether it started at
    -20 dB or at -49 dB. The only way to know how far a recording was lifted is
    to hold on to a piece of it from before the lift.

    Both clips he listens to are re-encoded at the same bitrate from the same
    capture, so the only difference between them is the filter. The capture
    itself is kept too, at the bitrate the recorder really uses.
    """
    from src.transcribe import NORMALISE_FILTER, SILENT_PEAK_DB, peak_db

    folder = REPO_ROOT / "state" / "sound-check"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    capture = folder / f"{stamp}-capture.m4a"
    before = folder / f"{stamp}-1-avant.m4a"
    after = folder / f"{stamp}-2-apres.m4a"

    print(f"Recording {seconds}s from {resolve_microphone()}. Talk, or let the room talk.")
    proc = subprocess.run(
        [find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "warning",
         "-f", "avfoundation", "-i", resolve_microphone(), "-t", str(seconds),
         "-ac", "1", "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-y", str(capture)],
        capture_output=True, text=True, timeout=seconds + 60,
    )
    if proc.returncode != 0 or not capture.exists() or capture.stat().st_size == 0:
        print("Microphone capture FAILED.", file=sys.stderr)
        print((proc.stderr or "").strip()[:500], file=sys.stderr)
        return 2

    # The same trap as mic_test: a microphone macOS has denied writes a file of
    # exactly the right length containing nothing at all.
    peak = peak_db(capture)
    if peak <= SILENT_PEAK_DB:
        print(f"Captured DIGITAL SILENCE ({peak:.0f} dBFS peak). Run: make setup-mic", file=sys.stderr)
        return 2

    for dest, filt in ((before, None), (after, NORMALISE_FILTER)):
        cmd = [find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(capture)]
        if filt:
            cmd += ["-af", filt]
        cmd += ["-c:a", "aac", "-b:a", LISTENING_BITRATE, "-y", str(dest)]
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    from src.transcribe import audio_seconds

    got = audio_seconds(capture)
    lost = (seconds - got) / seconds if got else 0.0
    print()
    print(f"  asked for {seconds}s, captured {got:.1f}s"
          + (f"  -> {lost * 100:.1f}% LOST" if lost > 0.01 else "  -> nothing lost"))
    print(f"  avant  {peak_db(before):6.1f} dBFS peak   {before}")
    print(f"  apres  {peak_db(after):6.1f} dBFS peak   {after}")
    print(f"  filter {NORMALISE_FILTER}")
    print()
    # Only play when a person is actually watching. Under a test runner or a
    # launchd job, stdout is a pipe and blasting audio at nobody is rude.
    if sys.stdout.isatty():
        for label, path in (("AVANT (brut)", before), ("APRES (traite)", after)):
            print(f"  playing {label}...")
            subprocess.run(["afplay", str(path)], check=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.recorder", description="Record scheduled classes."
    )
    parser.add_argument("--config", default=str(REPO_ROOT / "config.yaml"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tick", action="store_true", help="one scheduler beat (launchd calls this)")
    group.add_argument("--mic-test", action="store_true", help="record 5s and play it back")
    group.add_argument(
        "--sound-check", type=int, nargs="?", const=SOUND_CHECK_SECONDS, default=None,
        metavar="SECONDS",
        help="record from the real mic and keep both the untreated and the treated version",
    )
    group.add_argument("--capture-ip", action="store_true", help="print your public IP prefix")
    group.add_argument(
        "--capture-location", action="store_true",
        help="print your current coordinates, to paste into school_location",
    )
    group.add_argument("--status", action="store_true", help="show schedule and recording state")
    group.add_argument(
        "--record-now", action="store_true",
        help="record the class happening right now, ignoring the at-school gate",
    )
    return parser


def record_now(config_path: Path, repo_root: Path, log: logging.Logger) -> int:
    """Start recording the class in progress, gate or no gate.

    The escape hatch for the case that actually happened twice: a class is
    running, the at-school check cannot get a signal, and the answer is not to
    debug CoreLocation from a seat in the lecture hall. `repo_root` is resolved
    absolute before anything else, because launchd runs the capture with no
    working directory and a relative output path dies with a header error that
    names nothing useful.
    """
    cfg = load_config(config_path, repo_root=repo_root.resolve())
    now = datetime.now()
    entries = recordable(cfg, parse_schedule(cfg.schedule))
    entry = active_entry(entries, now)
    if entry is None:
        upcoming = [e for e in entries if e.weekday == now.strftime("%A").lower()]
        print("No class is scheduled right now.", file=sys.stderr)
        for e in upcoming:
            print(f"  today: {e.start}-{e.end}  {e.course}", file=sys.stderr)
        return 1

    course = cfg.course_by_code(entry.course)
    label = course.label() if course else entry.course
    if is_recording(cfg):
        print(f"Already recording {label}.")
        return 0

    start_capture(cfg, entry, now, log)
    if not is_recording(cfg):
        print(f"Could not start recording {label}; see logs/recorder.log", file=sys.stderr)
        return 2
    print(f"Recording {label} ({entry.start}-{entry.end}). It will stop and file itself normally.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.mic_test:
        return mic_test()

    if args.sound_check is not None:
        return sound_check(args.sound_check or SOUND_CHECK_SECONDS)

    config_path = Path(args.config)
    repo_root = config_path.resolve().parent
    log = setup_logging("recorder", repo_root)

    if args.capture_location:
        here = current_location()
        if here is None:
            print(
                "Could not get a location fix.\n"
                "  1. Install it:  brew install --cask corelocationcli\n"
                "  2. Enable System Settings > Privacy & Security > Location Services\n"
                "  3. Allow CoreLocationCLI in that same list",
                file=sys.stderr,
            )
            return 2
        print(f"Current position: {here[0]:.6f}, {here[1]:.6f}")
        print("Paste into config.yaml:")
        print(f"  school_location:\n    lat: {here[0]:.6f}\n    lon: {here[1]:.6f}\n    radius_m: 400")
        return 0

    if args.capture_ip:
        try:
            ip = current_ip()
        except Exception as exc:  # noqa: BLE001
            print(f"Could not reach api.ipify.org: {exc}", file=sys.stderr)
            return 2
        prefix = ".".join(ip.split(".")[:3])
        print(f"Public IP: {ip}\nPut this in config.yaml:\n  school_ip_prefix: \"{prefix}.\"")
        return 0

    if args.record_now:
        return record_now(config_path, repo_root, log)

    try:
        cfg = load_config(config_path, repo_root=repo_root)
        entries = parse_schedule(cfg.schedule)
    except ConfigError as exc:
        log.error("%s", exc)
        # No cfg=, deliberately: the config is the thing that failed, so the
        # ntfy topic is unreadable and this lands on the Mac only. Acceptable
        # here because a ConfigError follows someone editing config.yaml, so
        # they are at the keyboard. It would NOT be acceptable for a mid-class
        # failure, which is why those paths all pass cfg.notify.
        notify("Recorder config error", str(exc), critical=True)
        return 2

    if args.status:
        opted = [c.code for c in cfg.courses if c.record]
        print(f"schedule entries : {len(entries)}")
        print(f"courses opted in : {', '.join(opted) if opted else '(none)'}")
        print(f"at_school_check  : {cfg.at_school_check}")
        print(f"currently recording: {is_recording(cfg)}")
        if not entries:
            print("\nRecorder is INERT: config.yaml has an empty `schedule:`.")
        return 0

    try:
        status = tick(cfg, logger=log)
    except RecorderError as exc:
        log.error("%s", exc)
        notify("Recorder failed", str(exc), critical=True, cfg=cfg.notify)
        return 1
    except Exception as exc:  # noqa: BLE001
        log.exception("Recorder tick failed")
        notify("Recorder failed", f"{type(exc).__name__}: {exc}", critical=True, cfg=cfg.notify)
        return 1

    if status != "inert":
        log.info("tick: %s", status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
