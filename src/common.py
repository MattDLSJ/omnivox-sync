"""Shared core: config, state, notifications, logging.

Knows nothing about Omnivox, PDFs, or NotebookLM. Shared by all four modules.
"""

from __future__ import annotations

import contextlib
import portalocker
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import dotenv_values

_RANKINGS = {"rules", "gemini"}
#: "auto" uploads into the notebook named for each course. "staging" copies
#: new files into <course>/_to_upload/ for a manual drag-in. "off" is for the
#: people who do not use NotebookLM at all, and it exists because staging was
#: being handed to them as the way to opt out, which still left a folder of
#: copies in every course they own.
_NB_MODES = {"auto", "staging", "off"}
_SCHOOL_CHECKS = {"off", "ip", "ssid", "location", "auto"}
_COURSE_KEYS = ("code", "omnivox_name", "folder", "notebook")

_LOGGERS: dict[str, logging.Logger] = {}
LOG_MAX_BYTES = 1_048_576
LOG_BACKUPS = 3


class ConfigError(Exception):
    """Raised for any unusable configuration. Message is shown to the user."""


class StateError(Exception):
    """Raised when a state file exists but cannot be trusted."""


@dataclass(frozen=True)
class Course:
    code: str
    omnivox_name: str
    folder: str
    notebook: str
    record: bool = False
    icon: str = ""  # folder emoji, or "sym:<sf-symbol>"
    short: str = ""  # what a notification calls it; falls back to the first word
    language: str = ""  # transcription language; "" = use the global default
    teacher: str = ""
    group: str = ""  # Omnivox section, e.g. "1010"; shown in calendar descriptions

    def label(self) -> str:
        """The name a human recognises at a glance. Course codes like
        '330-704-EM' are meaningless in a notification."""
        return self.short or (self.folder.split()[0] if self.folder else self.code)


@dataclass(frozen=True)
class SchoolConfig:
    """Which Omnivox this account lives on, and what its buttons say.

    Omnivox is one product across nearly every cégep in Quebec, so the only
    things that vary are the hostname and, for the English-language colleges
    on the same system, six pieces of visible text. Empty means the default
    the code was written against.
    """

    portal: str = ""
    home: str = ""
    lea: str = ""
    labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NotifyConfig:
    macos: bool = True
    ntfy_topic: str = ""


@dataclass(frozen=True)
class SchoolLocation:
    """Campus centre and radius for the location-based at-school gate."""

    lat: float = 0.0
    lon: float = 0.0
    radius_m: float = 400.0

    def configured(self) -> bool:
        return bool(self.lat or self.lon)


@dataclass(frozen=True)
class FinderConfig:
    """Cosmetic Finder presentation for course folders."""

    tag: str = ""
    color: str = "green"


@dataclass(frozen=True)
class Config:
    semester: str
    base_path: Path
    courses: list[Course]
    schedule: list[dict]
    notify: NotifyConfig
    digest_ranking: str
    notebooklm_mode: str
    at_school_check: str
    school_ip_prefix: str
    repo_root: Path
    school_ssid: str = ""
    school_location: SchoolLocation = SchoolLocation()
    semester_start: str = ""   # ISO date; recorder is inert outside these bounds
    semester_end: str = ""     # ISO date of the LAST regular class day
    no_class_days: tuple[str, ...] = ()  # ISO dates with no regular classes
    finder: FinderConfig = FinderConfig()
    # Fast-forward to the latest release at the start of each scheduled sync.
    # On by default: the whole failure mode is somebody setting this up in
    # September, never thinking about it again, and therefore never seeing a
    # fix. It refuses to touch a tree with local changes, so it cannot eat
    # anybody's work. Set `update: auto: false` to turn it off.
    auto_update: bool = True
    # When the scheduled sync runs, as "HH:MM" separated by commas. Feeds two
    # things that must agree: the launchd schedule, and the deadline a failed
    # run uses to decide when it is superseded rather than retried.
    sync_times: tuple[tuple[int, int], ...] = ((7, 30), (12, 15), (18, 30))
    digest_folder: str = ""  # subfolder of base_path for _digest.md; "" = base_path
    # Relative: a subfolder of each course folder. Absolute (or ~-prefixed): a
    # root of its own, with the course folder under it. Use an absolute path to
    # keep lecture audio out of base_path when base_path is cloud-synced.
    recordings_folder: str = "Voice"
    # Where textbook extracts and set works go. Named with a leading digit for
    # the same reason as 1_NotebookLM and 2_Voice: Finder compares runs of
    # digits numerically, so it sorts above the course documents instead of
    # somewhere in the middle of them.
    books_folder: str = "3_Livres"
    # Which microphone to record from, matched on the name avfoundation reports
    # ("MacBook Pro Microphone"). Empty means "use the built-in". Never an
    # index: see MIC_PREFERENCE in src/recorder.py for what that cost.
    microphone: str = ""
    transcribe: bool = True           # local whisper.cpp transcription
    # "auto" probes the recording in short windows and decodes each contiguous
    # language run on its own. Pinning a code ("fr") skips probing and applies
    # that language to the whole file, which DELETES speech in any other one
    # rather than translating it. See src/transcribe.py for the measurements.
    transcribe_language: str = "auto"
    # The languages "auto" is allowed to pick. A detection outside this list is
    # treated as noise and inherits from its neighbours, so one bad window in a
    # loud room cannot chop the lecture into pieces.
    transcribe_languages: tuple[str, ...] = ("fr", "en")
    transcribe_timestamps: bool = False   # keep [hh:mm:ss] in the transcript body
    # Voice-activity detection. Measured on a real 10-minute classroom Q&A:
    # without it whisper produced 219 lines of "Sous-titrage Société
    # Radio-Canada" and nothing else, in 38 s. With it, zero invented lines,
    # the actual discussion transcribed, in 11 s. Leave this on.
    # Transcribe each segment while the class is still running, so a transcript
    # exists to ask questions about mid-class. The final pass at the end of the
    # class overwrites it with the better version.
    live_transcribe: bool = True
    whisper_vad: bool = True
    whisper_vad_model: str = ""       # "" = newest ggml-silero* in ~/.cache/whisper
    whisper_model: str = ""           # "" = newest model in ~/.cache/whisper
    # Gate, not a preference: true queues the transcript, false queues nothing.
    # The audio is never uploaded automatically under either setting.
    upload_transcript: bool = True

    #: Which Omnivox to talk to. Defaults to the one this was written
    #: against, so an existing config.yaml needs no change.
    school: SchoolConfig = SchoolConfig()

    def course_by_code(self, code: str) -> Course | None:
        return next((c for c in self.courses if c.code == code), None)

    def labels(self) -> dict[str, str]:
        """course code -> short human label, for notifications and the digest."""
        return {c.code: c.label() for c in self.courses}

    def folder_for(self, course: Course) -> Path:
        return self.base_path / course.folder

    def digest_dir(self) -> Path:
        """Where _digest.md lives. Keeps base_path tidy when set."""
        return self.base_path / self.digest_folder if self.digest_folder else self.base_path


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def _require(mapping: dict, key: str, where: str):
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"{where}: missing required key '{key}'")
    return mapping[key]


def _parse_course(raw: dict, index: int) -> Course:
    where = f"courses[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a mapping, got {type(raw).__name__}")
    for key in _COURSE_KEYS:
        _require(raw, key, where)
    folder = str(raw["folder"])
    if "/" in folder or folder in (".", "..") or folder.startswith("/"):
        raise ConfigError(
            f"{where}: folder {folder!r} must be a plain subfolder name "
            "(no '/', '.', or '..') so it stays inside base_path"
        )
    return Course(
        code=str(raw["code"]),
        omnivox_name=str(raw["omnivox_name"]),
        folder=folder,
        notebook=str(raw["notebook"]),
        record=bool(raw.get("record", False)),
        icon=str(raw.get("icon", "") or ""),
        short=str(raw.get("short", "") or ""),
        language=str(raw.get("language", "") or ""),
        teacher=str(raw.get("teacher", "") or ""),
        group=str(raw.get("group", "") or ""),
    )


def load_config(config_path: Path, *, repo_root: Path | None = None) -> Config:
    config_path = Path(config_path)
    repo_root = Path(repo_root) if repo_root else config_path.parent

    if not config_path.exists():
        raise ConfigError(
            f"Config file not found: {config_path}. config.yaml is per-person "
            "and is not in the repo; start from the template with: "
            "cp config.example.yaml config.yaml"
        )

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path.name}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path.name}: top level must be a mapping")

    semester = _require(raw, "semester", config_path.name)
    base_path_raw = _require(raw, "base_path", config_path.name)
    base_path = Path(str(base_path_raw)).expanduser()
    if not base_path.is_absolute():
        base_path = (repo_root / base_path).resolve()

    raw_courses = raw.get("courses") or []
    if not isinstance(raw_courses, list):
        raise ConfigError("courses: must be a list (use [] when not yet discovered)")
    courses = [_parse_course(c, i) for i, c in enumerate(raw_courses)]

    seen: set[str] = set()
    for course in courses:
        if course.code in seen:
            raise ConfigError(f"Duplicate course code {course.code!r} in courses")
        seen.add(course.code)

    school_raw = raw.get("school") or {}
    if not isinstance(school_raw, dict):
        raise ConfigError("school: must be a mapping")
    labels_raw = school_raw.get("labels") or {}
    if not isinstance(labels_raw, dict):
        raise ConfigError("school.labels: must be a mapping")
    school_cfg = SchoolConfig(
        portal=str(school_raw.get("portal", "") or "").strip(),
        home=str(school_raw.get("home", "") or "").strip(),
        lea=str(school_raw.get("lea", "") or "").strip(),
        labels=tuple((str(k), str(v)) for k, v in labels_raw.items()),
    )

    notify_raw = raw.get("notify") or {}
    # An ntfy.sh topic is a bearer secret in disguise: anyone who knows the name
    # can read every notification and push their own. config.yaml is no longer
    # tracked, but it is still the file people paste into issues and hand to an
    # AI, so the topic belongs in .env and the key here stays empty.
    notify_cfg = NotifyConfig(
        macos=bool(notify_raw.get("macos", True)),
        ntfy_topic=(
            str(notify_raw.get("ntfy_topic", "") or "").strip()
            or env_value(repo_root, "NTFY_TOPIC")
        ),
    )

    digest_raw = raw.get("digest") or {}
    digest_folder = str(digest_raw.get("folder", "") or "")
    if "/" in digest_folder or digest_folder in (".", "..") or digest_folder.startswith("/"):
        raise ConfigError(
            f"digest.folder {digest_folder!r} must be a plain subfolder name so it "
            "stays inside base_path"
        )

    finder_raw = raw.get("finder") or {}
    finder_cfg = FinderConfig(
        tag=str(finder_raw.get("tag", "") or ""),
        color=str(finder_raw.get("color", "green") or "green"),
    )

    ranking = str(digest_raw.get("ranking", "rules"))
    if ranking not in _RANKINGS:
        raise ConfigError(
            f"digest.ranking must be one of {sorted(_RANKINGS)}, got {ranking!r}"
        )

    nb_mode = str((raw.get("notebooklm") or {}).get("mode", "auto"))
    if nb_mode not in _NB_MODES:
        raise ConfigError(
            f"notebooklm.mode must be one of {sorted(_NB_MODES)}, got {nb_mode!r}"
        )

    school_check = str(raw.get("at_school_check", "off"))
    if school_check not in _SCHOOL_CHECKS:
        raise ConfigError(
            f"at_school_check must be one of {sorted(_SCHOOL_CHECKS)}, got {school_check!r}"
        )

    return Config(
        semester=str(semester),
        base_path=base_path,
        courses=courses,
        schedule=list(raw.get("schedule") or []),
        notify=notify_cfg,
        school=school_cfg,
        digest_ranking=ranking,
        notebooklm_mode=nb_mode,
        at_school_check=school_check,
        school_ip_prefix=str(raw.get("school_ip_prefix", "") or ""),
        repo_root=repo_root,
        school_ssid=str(raw.get("school_ssid", "") or ""),
        school_location=SchoolLocation(
            lat=float((raw.get("school_location") or {}).get("lat", 0) or 0),
            lon=float((raw.get("school_location") or {}).get("lon", 0) or 0),
            radius_m=float((raw.get("school_location") or {}).get("radius_m", 400) or 400),
        ),
        semester_start=str(raw.get("semester_start", "") or ""),
        semester_end=str(raw.get("semester_end", "") or ""),
        no_class_days=tuple(str(d) for d in (raw.get("no_class_days") or [])),
        finder=finder_cfg,
        auto_update=bool((raw.get("update") or {}).get("auto", True)),
        sync_times=parse_sync_times(raw.get("sync_times")),
        digest_folder=digest_folder,
        recordings_folder=str(raw.get("recordings_folder", "Voice") or "Voice"),
        books_folder=str(raw.get("books_folder", "3_Livres") or "3_Livres"),
        microphone=str(raw.get("microphone", "") or ""),
        transcribe=bool(raw.get("transcribe", True)),
        transcribe_language=str(raw.get("transcribe_language", "auto") or "auto"),
        transcribe_languages=tuple(
            str(x).strip().lower()
            for x in (raw.get("transcribe_languages") or ["fr", "en"])
        ),
        transcribe_timestamps=bool(raw.get("transcribe_timestamps", False)),
        live_transcribe=bool(raw.get("live_transcribe", True)),
        whisper_vad=bool(raw.get("whisper_vad", True)),
        whisper_vad_model=str(raw.get("whisper_vad_model", "") or ""),
        whisper_model=str(raw.get("whisper_model", "") or ""),
        upload_transcript=bool(raw.get("upload_transcript", True)),
    )


def build_portal(cfg) -> "object":
    """Turn the `school:` config into the Portal the scraper drives.

    Kept here rather than in omnivox.py so that config stays the only place
    anyone has to look to point this at a different cégep.
    """
    from src.omnivox import DEFAULT_PORTAL, Portal

    school = getattr(cfg, "school", None) or SchoolConfig()
    if not (school.portal or school.home or school.lea or school.labels):
        return DEFAULT_PORTAL
    fields = {}
    if school.portal:
        fields["school"] = school.portal
    if school.home:
        fields["home"] = school.home
    if school.lea:
        fields["lea"] = school.lea
    for key, value in school.labels:
        if key == "docs_nav":
            fields[key] = tuple(v.strip() for v in value.split("|") if v.strip())
        elif key in {"docs_link", "travaux_link", "lea_link_name", "logged_in_text"}:
            fields[key] = value
        else:
            raise ConfigError(
                f"school.labels: unknown label {key!r}. Valid: docs_link, "
                "travaux_link, docs_nav, lea_link_name, logged_in_text"
            )
    return Portal(**fields)


#: The schedule this ships with. Weekday mornings before class, lunchtime, and
#: after supper: the three moments a document is likely to have been posted
#: since the last look.
DEFAULT_SYNC_TIMES = ((7, 30), (12, 15), (18, 30))


def parse_sync_times(raw) -> tuple[tuple[int, int], ...]:
    """Accept "07:30, 12:15" or ["07:30", "12:15"], reject nonsense loudly.

    A string, because the settings page writes this and writing a YAML list
    from a text editor that only handles scalars is a way to corrupt a config
    file. A list is still accepted, because that is what somebody editing by
    hand will naturally write.
    """
    if raw is None or raw == "":
        return DEFAULT_SYNC_TIMES
    items = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    out = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        parts = text.split(":")
        if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
            raise ConfigError(
                f"sync_times: {text!r} is not a time. Write them as HH:MM, "
                'separated by commas: "07:30, 12:15, 18:30"'
            )
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ConfigError(f"sync_times: {text!r} is not a real time of day.")
        out.append((hour, minute))
    if not out:
        return DEFAULT_SYNC_TIMES
    # Sorted, because next_window walks them in order and an out-of-order list
    # would silently return a window that has already passed.
    return tuple(sorted(set(out)))


def env_value(repo_root: Path, key: str) -> str:
    """One optional value from repo_root/.env, empty when absent.

    Unlike load_credentials this never raises: it is for settings that are
    secret but not required, so a missing .env just disables the feature.
    """
    env_path = Path(repo_root) / ".env"
    if not env_path.exists():
        return ""
    return (dotenv_values(env_path).get(key) or "").strip()


def load_credentials(repo_root: Path, *, required: bool = True) -> tuple[str, str]:
    """Read OMNIVOX_USER / OMNIVOX_PASS from repo_root/.env.

    They are OPTIONAL, and `required=False` returns empty strings rather than
    raising. Signing in by hand once, in the browser `make login` opens, stores
    a session that every later run reuses, and that path never touches a
    password. Which is the setup worth having: it is one screen instead of a
    file to edit, and the six-digit identity check happens in the same sitting.

    What the password buys, and the only thing it buys, is UNATTENDED
    re-login. Session cookies expire long before the trusted-device cookie
    does, and with a password on disk the scheduled run signs back in by
    itself instead of going quiet until somebody notices.
    """
    env_path = Path(repo_root) / ".env"
    if not env_path.exists():
        if not required:
            return "", ""
        raise ConfigError(
            f"No .env at {env_path}. Copy .env.example to .env and fill in "
            "OMNIVOX_USER and OMNIVOX_PASS."
        )
    values = dotenv_values(env_path)
    creds = []
    for key in ("OMNIVOX_USER", "OMNIVOX_PASS"):
        value = (values.get(key) or "").strip()
        if not value and required:
            raise ConfigError(f"{key} is missing or empty in {env_path}")
        creds.append(value)
    return creds[0], creds[1]


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


class StateStore:
    """A JSON list of records, written atomically."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError(
                f"State file {self.path.name} is corrupt ({exc}). "
                "Move it aside to rebuild; downloads already on disk are protected "
                "by the on-disk secondary guard."
            ) from exc
        if not isinstance(data, list):
            raise StateError(f"State file {self.path.name} must contain a JSON list")
        return data

    def write(self, records: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Serialize first: a failure here must leave the existing file untouched.
        payload = json.dumps(records, ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def append(self, record: dict) -> None:
        records = self.read()
        records.append(record)
        self.write(records)


# --------------------------------------------------------------------------
# Notifications and logging
# --------------------------------------------------------------------------


def _osascript_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


class RunBusy(Exception):
    """Another process already holds the run lock."""


@contextlib.contextmanager
def run_lock(path: Path, *, wait: float = 0.0, poll: float = 0.5):
    """An exclusive lock across processes, for the length of a `with` block.

    Nothing in this repo was serialised before. Three launchd jobs and a button
    can all drive the same Playwright profile in state/omnivox-profile/, and a
    headless Chromium creates no lock of its own: two of them open the same
    profile with no error at all. The worst case is a scheduled sync joining
    `make login` while the MFA code is being typed.

    An OS-level file lock is the right primitive because the kernel drops it
    when the fd closes, which includes a crash or a SIGKILL. No stale lock can
    ever be left behind, so there is no PID-liveness check to get wrong. That
    holds on Windows too, which is why portalocker rather than fcntl: fcntl
    exists only on Unix, and importing it here took down every module that
    imports this one.

    wait=0 raises RunBusy immediately; wait>0 polls up to that many seconds.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Deliberately not a `with`: the fd has to outlive this line. Closing it
    # releases the lock, so the handle must live as long as the critical section.
    handle = open(path, "a+", encoding="utf-8")
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                portalocker.lock(
                    handle, portalocker.LOCK_EX | portalocker.LOCK_NB
                )
                break
            except (portalocker.LockException, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise RunBusy(f"another run holds {path.name}") from None
                time.sleep(poll)
        # Record the holder so a human can see who has it. Never unlink this
        # file: a fresh inode would let a second process lock a different one.
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        yield path
    finally:
        handle.close()


def human_scope(cfg, scope: str) -> str:
    """'330-704-EM/notes.pdf' -> 'Histoire/notes.pdf'.

    A course code means nothing on a phone screen at 07:30. Errors keep the
    code in the log; only the notification gets the human name.
    """
    names = cfg.labels()
    code, sep, rest = scope.partition("/")
    return f"{names.get(code, code)}{sep}{rest}"


def human_courses(cfg, codes) -> str:
    names = cfg.labels()
    return ", ".join(names.get(c, c) for c in sorted(codes))


def notify(
    title: str,
    message: str,
    *,
    critical: bool = False,
    level: str | None = None,
    cfg: NotifyConfig | None = None,
) -> None:
    """Best-effort user notification. Never raises: a broken notifier must not
    abort a sync run. Failures are logged instead.

    `level` is "quiet", "normal" or "alert". `critical=True` is the old spelling
    of "alert" and still works, so the eighteen existing call sites did not all
    have to change at once.
    """
    cfg = cfg or NotifyConfig()
    log = logging.getLogger("school.notify")

    # ntfy priority 5 is the "max" level, and it earns its name: on a phone it
    # bypasses do-not-disturb and vibrates in repeated bursts with a pop-over.
    # Every alarm in this project used it, including "your class started and the
    # Mac does not think it is at school", which is information, not an
    # emergency. The result rang like crazy, and rightly so: an alert that
    # shakes the phone for a class you are not attending trains you to ignore
    # the one that means a lecture was lost.
    #
    # So three levels instead of a boolean. 4 still sounds and still stands out,
    # but it does not seize the phone. Nothing here uses 5 any more.
    level = "alert" if critical else level or "normal"
    priority = {"quiet": 2, "normal": 3, "alert": 4}.get(level, 3)

    if cfg.macos:
        sound = ' sound name "Basso"' if level == "alert" else ""
        script = (
            f'display notification "{_osascript_escape(message)}" '
            f'with title "{_osascript_escape(title)}"{sound}'
        )
        try:
            subprocess.run(["osascript", "-e", script], check=False, timeout=10)
        except Exception as exc:  # noqa: BLE001 - must never abort a run
            log.warning("macOS notification failed: %s", exc)

    if cfg.ntfy_topic:
        try:
            # POST JSON rather than using headers: ntfy headers must be ASCII,
            # and the digest titles are full of "⚠" and "·", which would arrive
            # as "?" if squeezed through an ASCII header.
            payload = json.dumps(
                {
                    "topic": cfg.ntfy_topic,
                    "title": title,
                    "message": message,
                    "priority": priority,
                    "tags": ["warning"] if level == "alert" else [],
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                "https://ntfy.sh/",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10):
                pass
        except Exception as exc:  # noqa: BLE001
            log.warning("ntfy notification failed: %s", exc)


def setup_logging(module: str, repo_root: Path) -> logging.Logger:
    """Return a logger writing to logs/{module}.log, rotated at 1 MB x3."""
    if module in _LOGGERS:
        return _LOGGERS[module]

    log_dir = Path(repo_root) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"school.{module}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / f"{module}.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUPS,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # stdout, not the default stderr: under launchd, stderr is StandardErrorPath,
    # and routine INFO lines there would make logs/launchd.err.log useless for
    # spotting actual failures.
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    _LOGGERS[module] = logger
    return logger
