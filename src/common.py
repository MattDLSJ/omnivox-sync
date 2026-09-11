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
    #: Desktop notifications, on whichever desktop this is. Named `macos` until
    #: it turned out Windows had been running `osascript` for months and
    #: logging "macOS notification failed" to a file nobody reads.
    desktop: bool = True
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
    books_folder: str = "3_Books"
    # Teacher announcements, saved beside the documents. Named with a leading
    # digit for the same reason as the others: Finder compares runs of digits
    # numerically, so it sorts with them rather than into the middle of them.
    communiques_folder: str = "4_Announcements"
    # Messages your own teachers sent you in Omnivox's internal mail.
    mio_folder: str = "5_MIO"
    # The per-course schedule the calendar mirror writes.
    schedule_file: str = "_schedule.md"
    # Open each teacher message for its full text. OFF by default, because
    # opening one marks it read, and a background job silently emptying
    # somebody's unread list is not a thing to do without being asked.
    mio_full_bodies: bool = False
    # Install the scheduled job at the end of setup, so it runs by itself from
    # day one rather than after somebody reads a recommendation and acts on it.
    schedule_auto: bool = True
    # Look through Downloads, Desktop and Documents for course files that are
    # already on the machine, and file them. Never deletes.
    organize_scan: bool = True
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

    # Not _require: an empty semester means "work it out from the date", which
    # is how the example avoids shipping a term name that is right for a few
    # months and then quietly wrong.
    semester = raw.get("semester") or ""
    # Empty base_path means "work it out", for exactly the reason semester
    # does. The example shipped "~/Documents/School/Cegep Automne 2026", which
    # is two bugs in one line: the year is frozen, so an install in January
    # files a winter term into a folder named Automne 2026, and the word is
    # French while every folder the project creates around it, and the
    # notebooks it names, are English. A fresh install produced a folder
    # called "Automne" holding notebooks called "Fall".
    base_path_raw = raw.get("base_path") or ""
    if not str(base_path_raw).strip():
        base_path = (
            user_folder("Documents") / "School"
            / f"Cegep {semester or default_semester()}"
        )
    else:
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
        # `macos:` is what configs written before the rename carry, and it
        # meant "desktop notifications" even then, so it is read as such.
        desktop=bool(
            notify_raw.get("desktop", notify_raw.get("macos", True))
        ),
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
        # Blank means "work it out from the date", so a fresh install in
        # January does not inherit last autumn's folder name.
        semester=str(semester) if str(semester).strip() else default_semester(),
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
        books_folder=str(raw.get("books_folder", "3_Books") or "3_Books"),
        communiques_folder=str(
            raw.get("communiques_folder", "4_Announcements") or "4_Announcements"
        ),
        mio_folder=str(raw.get("mio_folder", "5_MIO") or "5_MIO"),
        schedule_file=str(raw.get("schedule_file", "_schedule.md") or "_schedule.md"),
        mio_full_bodies=bool((raw.get("mio") or {}).get("full_bodies", False)),
        schedule_auto=bool((raw.get("schedule_settings") or raw.get("scheduling") or {}).get("auto", True)),
        organize_scan=bool((raw.get("organize") or {}).get("scan", True)),
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


#: Quebec cégep terms. August through December is the autumn one, January
#: through May the winter one, and the short summer session sits between.
_TERMS = ((8, "Fall"), (6, "Summer"), (1, "Winter"))


#: The three folders that hold somebody's files, as Windows knows them rather
#: than as a path built by hand.
#:
#: `~/Documents` is not the Documents folder on a Windows machine with OneDrive
#: Known Folder Move turned on, which is the default on a lot of them and on
#: every school-managed one. KFM points the Documents, Desktop and Downloads
#: known folders at `%USERPROFILE%\OneDrive\...`, and `C:\Users\<name>\Documents`
#: stays behind as a near-empty leftover. So a project that writes to
#: `~/Documents` creates its folders somewhere the owner cannot find: not in
#: the Documents they see in Explorer, not in the one synced to their phone.
#: It was reported exactly that way, by somebody who opened Documents and found
#: nothing there.
_WINDOWS_FOLDER_IDS = {
    # FOLDERID_Documents, FOLDERID_Desktop, FOLDERID_Downloads
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}

#: Where the same three live in the registry when the API is unavailable.
_WINDOWS_SHELL_NAMES = {
    "Documents": "Personal",
    "Desktop": "Desktop",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}


def _windows_known_folder(name: str) -> Path | None:
    """Ask Windows where `name` really is. None if it will not say."""
    guid = _WINDOWS_FOLDER_IDS.get(name)
    if not guid:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        raw = guid.strip("{}").split("-")
        tail = bytes.fromhex(raw[3] + raw[4])
        folder_id = _GUID(
            int(raw[0], 16), int(raw[1], 16), int(raw[2], 16),
            (ctypes.c_ubyte * 8)(*tail),
        )
        out = ctypes.c_wchar_p()
        status = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(out)
        )
        if status != 0 or not out.value:
            raise OSError(f"SHGetKnownFolderPath returned {status}")
        try:
            return Path(out.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except Exception:  # noqa: BLE001 - fall through to the registry
        pass
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, _WINDOWS_SHELL_NAMES[name])
        expanded = os.path.expandvars(str(value))
        return Path(expanded) if expanded and "%" not in expanded else None
    except Exception:  # noqa: BLE001 - the caller has a sane default
        return None


def user_folder(name: str, home: Path | None = None) -> Path:
    """Documents, Desktop or Downloads, as the person's file manager shows it."""
    if sys.platform == "win32" and home is None:
        found = _windows_known_folder(name)
        if found:
            return found
    return (home or Path.home()) / name


def user_folders(name: str, home: Path | None = None) -> list[Path]:
    """Every place `name` might be, most authoritative first.

    Both, when KFM is on: the redirected folder is where new files go, and the
    plain one usually still holds whatever was there before the redirect
    happened. Searching only one of them misses real coursework.
    """
    seen, out = set(), []
    for candidate in (user_folder(name, home), (home or Path.home()) / name):
        resolved = str(candidate)
        if resolved not in seen:
            seen.add(resolved)
            out.append(candidate)
    return out


def default_semester(today=None) -> str:
    """"Automne 2026", worked out from the date.

    The example config used to hardcode one, which meant it was correct for a
    few months and then quietly wrong: an install in January would have
    created a folder called "Cegep Automne 2026" and filed a winter semester
    into it. Nobody would notice until the folder names stopped matching what
    they were studying.

    English, to match the folder names the project creates around it. Mixing
    the two is what somebody noticed and could not unsee: "Cegep Automne 2026"
    holding 3_Books, 5_MIO and a _schedule.md. Either language would be fine;
    half of each is not. It is one line in config.yaml to write it any other
    way, and anyone who does keeps their own version: this only fills a blank.
    """
    from datetime import date

    today = today or date.today()
    name = next(label for month, label in _TERMS if today.month >= month)
    return f"{name} {today.year}"


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

    `required=False` returns empty strings rather than raising, because the
    two interactive entry points (`--login`, `--doctor`) have to work before
    anything is configured.

    They are needed for every SCHEDULED run, though, and the docstring here
    used to claim otherwise. The stored browser profile keeps Omnivox's
    trusted-device cookie, which is what stops the six-digit codes, and it
    does NOT keep the session cookie, which dies with the browser. Measured
    over 93 scheduled runs on the author's machine: 114 form logins, zero
    session reuses. `make login` therefore offers to save what was typed, and
    a copy with nothing here works by hand and stops when left alone.
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


#: Raising a toast on Windows is a WinRT call, and PowerShell is the only
#: interpreter guaranteed to be on the machine that can make one. The app id
#: has to be one Windows already trusts or the toast is accepted and never
#: drawn; PowerShell's own is the standard choice and needs no registration.
_WINDOWS_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).AppendChild($xml.CreateTextNode($env:SCHOOL_TOAST_TITLE)) | Out-Null
$texts.Item(1).AppendChild($xml.CreateTextNode($env:SCHOOL_TOAST_BODY)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(
    '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
).Show($toast)
"""


def _desktop_notification(title: str, message: str, *, alert: bool) -> None:
    """One notification on this desktop. Raises; the caller logs and carries on.

    Note what is NOT here: any attempt on Linux. Saying so is the point. The
    previous version ran `osascript` on every platform because the flag that
    guarded it was called `macos` and defaulted to true, so Windows spent every
    run shelling out to a program that does not exist there and writing "macOS
    notification failed" into a log file. An unsupported platform should do
    nothing quietly, not fail loudly in a place nobody looks.
    """
    if sys.platform == "darwin":
        sound = ' sound name "Basso"' if alert else ""
        script = (
            f'display notification "{_osascript_escape(message)}" '
            f'with title "{_osascript_escape(title)}"{sound}'
        )
        subprocess.run(["osascript", "-e", script], check=False, timeout=10)
        return

    if sys.platform == "win32":
        # The text goes through the environment rather than into the script.
        # A course name with an apostrophe would otherwise end the PowerShell
        # string, and one with an "&" would break the XML: CreateTextNode is
        # what makes the second problem somebody else's.
        env = {
            **os.environ,
            "SCHOOL_TOAST_TITLE": title,
            "SCHOOL_TOAST_BODY": message,
        }
        subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", _WINDOWS_TOAST,
            ],
            check=False,
            timeout=15,
            env=env,
            # Without this a console window flashes on screen at every
            # notification, which on a sync that reports six courses is worse
            # than having no notifications at all.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return


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

    if cfg.desktop:
        try:
            _desktop_notification(title, message, alert=level == "alert")
        except Exception as exc:  # noqa: BLE001 - must never abort a run
            log.warning("desktop notification failed: %s", exc)

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
