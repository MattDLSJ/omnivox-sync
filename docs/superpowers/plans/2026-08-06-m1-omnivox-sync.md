# Milestone 1: Omnivox Sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a scheduled, dry-run-capable Python job that logs into Omnivox LEA, downloads every not-yet-seen distributed document into the right local course folder, converts non-NotebookLM formats to PDF, and records state durably — failing loudly on every error.

**Architecture:** Three layers with a hard seam between them. `src/omnivox.py` is the only module that touches Playwright: it exposes a small, typed site-driver interface (`list_courses`, `list_documents`, `download`). `src/omnivox_sync.py` holds all orchestration and decision logic and talks only to that interface, so every decision (what is new, what to convert, what to queue) is unit-testable against a fake driver with no browser. `src/common.py` holds config loading, atomic JSON state, notifications, and logging, shared by all four future modules. Milestone 1 ends at a well-defined seam where M2 (NotebookLM upload) and M3 (digest) will attach.

**Tech Stack:** Python 3.14 (spec floor 3.12+), Playwright (Chromium, persistent context), PyYAML, python-dotenv, pytest, LibreOffice `soffice` CLI, `launchd`.

---

## Global Constraints

Copied from `docs/superpowers/specs/2026-08-06-school-automation-design.md`. Every task's requirements implicitly include this section.

- **Repo root:** `~/Documents/Programming/school-automation`. Verified this Mac does **not** sync Documents to iCloud, so the spec's default location stands (spec §4 location note resolved).
- **Python 3.12+**; this machine has 3.14.2. All commands run through the repo venv at `.venv/bin/python`.
- **Recurring cost must be $0.** No paid APIs anywhere in M1.
- **The pipeline must never reduce information visibility.** Anything downloaded or read on the user's behalf must end up in a record M3 can surface. In M1 that means: everything observed is written to state and to the run summary, even in dry-run.
- **Fail loudly.** Every failure produces a macOS notification and a log entry. Never silently skip. A parse failure also saves a Playwright screenshot into `logs/`.
- **Config-driven semesters.** Nothing semester-specific is hardcoded in `src/`. Course codes, folders, notebook names, and paths live only in `config.yaml`.
- **YAGNI.** M1 implements spec §4, §5, §6, §7, §11, §12 only. No M2/M3/M4 behavior beyond the named seam in Task 15.
- **`.env` is never committed**, even to a private repo. It is gitignored from the first commit, before any credential exists on disk.
- **New-file definition is the tuple `(course_code, filename, publish_date)`** checked against `state/downloads.json`. Omnivox red badges and stars are never used as truth, because this pipeline clears them.
- **Downloads never involve a native Save dialog.** Playwright's `expect_download` API only. This is the exact blocker that killed the browser-extension approach in March 2026.
- **Always navigate from the Omnivox homepage.** Never reuse a deep link across runs; Omnivox session URLs expire.
- **NotebookLM-supported extensions** (do not convert): `pdf, docx, txt, md, images, mp3, wav, epub`. **Convert to PDF:** `pptx, ppt, xlsx, xls, odp` and unknown office types.
- **Atomic state writes only:** write temp file in the same directory, then `os.replace`.
- **State/log rotation:** `RotatingFileHandler`, 1 MB, 3 backups, per module, at `logs/{module}.log`.
- **launchd:** absolute paths everywhere; no shell environment is available to launchd jobs.

---

## Scope: what M1 does and does not build

**In scope (spec §4, §5, §6, §7, §11, §12):** repo scaffold, `common.py`, config schema + validation, `.env` handling, `--discover` semester bootstrap, LEA login with session reuse, course + document-table parsing, new-file diffing, downloads, PDF conversion, folder creation, state files, failure notifications with screenshots, `launchd` install/uninstall, pytest suite, dry-run and headed modes.

**Explicitly deferred (named seam, no implementation):**
- **M2 upload.** Task 15 writes `state/upload_queue.json` and calls a `chain_downstream()` function that logs `"M2 not implemented (Milestone 2)"`. No NotebookLM code.
- **M3 digest and announcement collection.** Spec §7 lists "Quoi de neuf" / MIO / due-date collection under M1's crawl, but spec §16 assigns that work to **Milestone 3** ("Announcement collection in M1's crawl"). M1 therefore builds the crawl and the seam; M3 adds the collectors. Task 15 leaves a documented extension point. **This is the one place the plan reads §7 through §16 rather than literally — flagged here so it is a deliberate decision, not an omission.**
- **M4 recorder.** Nothing. `schedule:` stays `[]` in config and `com.school.recorder.plist` is not created.

**Deviations from the spec's file layout (§4), and why:**
1. **`src/omnivox.py` added** (spec lists only `omnivox_sync.py`). Splitting the Playwright driver from orchestration is what makes spec §12's "pure logic under pytest" achievable — the diffing, routing, and dry-run logic get real tests against a fake driver instead of needing a live browser. `omnivox_sync.py` remains the entry point exactly as specified.
2. **`config.example.yaml`, `.env.example`, `Makefile`, `scripts/install_launchd.sh`, `tests/` added.** Support files; `Makefile` is required by spec §11 ("a small `make install-launchd` target"), `tests/` by §12.
3. **`launchd/com.school.sync.plist` is generated from a `.template`** because absolute paths (§11) cannot be committed portably.

---

## File Structure

```
school-automation/
├── .env                          # gitignored; user fills OMNIVOX_USER / OMNIVOX_PASS
├── .env.example                  # committed template, no values
├── .gitignore                    # .env, logs/, state/, .venv/, __pycache__, .DS_Store
├── config.yaml                   # live semester config (committed; contains no secrets)
├── config.example.yaml           # committed reference copy with comments
├── requirements.txt
├── Makefile                      # venv, test, install-launchd, uninstall-launchd
├── pyproject.toml                # pytest config only
├── README.md                     # setup + operations, written in Task 17
├── src/
│   ├── __init__.py
│   ├── common.py                 # Config dataclasses, load_config, StateStore, notify, setup_logging
│   ├── omnivox.py                # Playwright site driver: OmnivoxSession + typed records
│   ├── omnivox_sync.py           # M1 orchestration, SyncResult, CLI entry point
│   └── convert.py                # extension routing + soffice invocation
├── launchd/
│   └── com.school.sync.plist.template
├── scripts/
│   └── install_launchd.sh        # renders template with absolute paths, bootstraps
├── state/                        # gitignored (dir kept via .gitkeep)
│   ├── downloads.json
│   ├── upload_queue.json
│   └── omnivox-profile/          # Playwright persistent context
├── logs/                         # gitignored (dir kept via .gitkeep)
└── tests/
    ├── conftest.py               # tmp repo fixture, fake driver, sample config
    ├── test_config.py
    ├── test_state.py
    ├── test_notify.py
    ├── test_convert_routing.py
    ├── test_convert_soffice.py
    ├── test_diffing.py
    ├── test_discover.py
    ├── test_sync_orchestration.py
    └── test_launchd_render.py
```

**Responsibility boundaries:**
- `common.py` knows nothing about Omnivox or PDFs — it is the shared core for M1–M4.
- `omnivox.py` knows nothing about config, state, or conversion — it takes credentials and returns typed records.
- `convert.py` knows nothing about courses or state — it takes a path and returns a path.
- `omnivox_sync.py` is the only module that knows how all of the above compose.

---

## Interface Reference

Every task below refers to these. Names and types are fixed here so tasks implemented out of order stay consistent.

```python
# src/common.py
@dataclass(frozen=True)
class Course:
    code: str; omnivox_name: str; folder: str; notebook: str; record: bool

@dataclass(frozen=True)
class NotifyConfig:
    macos: bool; ntfy_topic: str

@dataclass(frozen=True)
class Config:
    semester: str
    base_path: Path            # expanded, absolute
    courses: list[Course]
    schedule: list[dict]       # opaque in M1; M4 parses it
    notify: NotifyConfig
    digest_ranking: str        # "rules" | "gemini"
    notebooklm_mode: str       # "auto" | "staging"
    at_school_check: str       # "off" | "ip" | "ssid"
    school_ip_prefix: str
    repo_root: Path

class ConfigError(Exception): ...

def load_config(config_path: Path, *, repo_root: Path | None = None) -> Config
def load_credentials(repo_root: Path) -> tuple[str, str]      # (user, password); raises ConfigError
def setup_logging(module: str, repo_root: Path) -> logging.Logger
def notify(title: str, message: str, *, critical: bool = False,
           cfg: NotifyConfig | None = None) -> None

class StateStore:
    def __init__(self, path: Path) -> None
    def read(self) -> list[dict]
    def write(self, records: list[dict]) -> None      # atomic
    def append(self, record: dict) -> None

# src/convert.py
class ConversionError(Exception): ...
NOTEBOOKLM_SUPPORTED: frozenset[str]
CONVERTIBLE: frozenset[str]
def needs_conversion(path: Path) -> bool
def find_soffice() -> str                              # raises ConversionError if absent
def convert_to_pdf(src: Path, outdir: Path, *, timeout: int = 180) -> Path

# src/omnivox.py
@dataclass(frozen=True)
class OmnivoxCourse:
    code: str; name: str; href: str

@dataclass(frozen=True)
class OmnivoxDocument:
    course_code: str; filename: str; publish_date: str; ref: str   # publish_date is ISO YYYY-MM-DD

class OmnivoxError(Exception): ...
class LoginError(OmnivoxError): ...
class ParseError(OmnivoxError): ...

class OmnivoxSession:                                   # context manager
    def __init__(self, profile_dir: Path, *, headed: bool = False,
                 screenshot_dir: Path | None = None, logger=None) -> None
    def login(self, user: str, password: str) -> None
    def list_courses(self) -> list[OmnivoxCourse]
    def list_documents(self, course: OmnivoxCourse) -> list[OmnivoxDocument]
    def download(self, doc: OmnivoxDocument, dest: Path) -> Path

# src/omnivox_sync.py
@dataclass
class SyncResult:
    downloaded: list[dict]            # download records
    converted: list[dict]             # {"source":…, "pdf":…}
    conversion_failures: list[dict]   # {"source":…, "error":…, "uploadable": bool}
    upload_queue: list[dict]          # {"course_code":…, "path":…, "filename":…}
    skipped_existing: list[dict]      # secondary-guard hits
    errors: list[dict]                # {"scope":…, "error":…, "screenshot":… | None}
```

**Download record shape** written to `state/downloads.json`:

```json
{"course_code": "601-101-MQ", "filename": "ch01.pptx", "publish_date": "2026-09-03",
 "downloaded_at": "2026-09-03T07:30:12", "path": "/abs/path/ch01.pptx",
 "converted_pdf": "/abs/path/ch01.pdf"}
```
`converted_pdf` is `null` when no conversion was needed or conversion failed.

---

## Task 1: Repo scaffold, gitignore-before-secrets, first commit

**Files:**
- Create: `.gitignore`, `.env.example`, `requirements.txt`, `pyproject.toml`, `config.example.yaml`, `config.yaml`, `src/__init__.py`, `tests/conftest.py`, `state/.gitkeep`, `logs/.gitkeep`, `Makefile`

**Interfaces:**
- Consumes: nothing.
- Produces: the repo layout every later task writes into; `tests/conftest.py` fixtures `tmp_repo`, `sample_config_dict`, `write_config` used by Tasks 2–16.

**Ordering note:** `.gitignore` is created and committed **before** `.env` ever exists. Never create `.env` first.

- [ ] **Step 1: Write `.gitignore`**

```gitignore
.env
.venv/
logs/
state/
__pycache__/
*.pyc
.DS_Store
.pytest_cache/
launchd/com.school.sync.plist
```

Note the last line: only the `.template` is committed; the rendered plist holds machine-absolute paths.

- [ ] **Step 2: Keep the gitignored dirs in the tree**

```bash
mkdir -p src tests launchd scripts state logs docs/superpowers/plans
printf '!.gitkeep\n' > state/.gitkeep
printf '!.gitkeep\n' > logs/.gitkeep
```

Then add negations to `.gitignore` so the dirs survive a clone:

```gitignore
!state/.gitkeep
!logs/.gitkeep
```

- [ ] **Step 3: Write `requirements.txt`**

```
playwright>=1.47
PyYAML>=6.0
python-dotenv>=1.0
pytest>=8.0
```

- [ ] **Step 4: Write `.env.example`**

```
# Copy to .env and fill in. .env is gitignored and must never be committed.
OMNIVOX_USER=
OMNIVOX_PASS=
# Optional, only if digest.ranking = "gemini" (Milestone 3)
GEMINI_API_KEY=
```

- [ ] **Step 5: Write `config.example.yaml`**

```yaml
semester: "Automne 2026"

# NOTE: existing folders on this Mac use English season names
# ("Cegep Fall 2025", "Cegep Winter 2026", "Cegep Summer 2026").
# The spec specifies "Cegep Automne 2026". The fall folder does not exist yet,
# so either works — omnivox_sync creates it. Change this one line if you
# prefer to match the existing English convention.
base_path: "~/Documents/School/Cegep Automne 2026"

# Filled by: .venv/bin/python -m src.omnivox_sync --discover
courses: []

# Filled once the timetable is published (Milestone 4). M4 is inert while empty.
schedule: []

notify:
  macos: true
  ntfy_topic: ""        # optional ntfy.sh topic for phone push; empty = disabled

digest:
  ranking: "rules"      # "rules" | "gemini"

notebooklm:
  mode: "auto"          # "auto" | "staging"

at_school_check: "off"  # "off" | "ip" | "ssid"
school_ip_prefix: ""
```

- [ ] **Step 6: Copy it to the live config**

```bash
cp config.example.yaml config.yaml
```

`config.yaml` **is** committed — it holds no secrets, and committing it means a lost machine can be rebuilt from the repo.

- [ ] **Step 7: Write `pyproject.toml`**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"
markers = [
    "live: hits the real Omnivox site; requires .env credentials",
    "soffice: requires a working LibreOffice install",
]
```

- [ ] **Step 8: Write `Makefile`**

Tabs, not spaces, for recipe lines.

```makefile
PY := $(CURDIR)/.venv/bin/python

.PHONY: venv test test-unit sync dry-run discover install-launchd uninstall-launchd

venv:
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt
	$(PY) -m playwright install chromium

test:
	$(PY) -m pytest

test-unit:
	$(PY) -m pytest -m "not live"

dry-run:
	$(PY) -m src.omnivox_sync --dry-run

discover:
	$(PY) -m src.omnivox_sync --discover

sync:
	$(PY) -m src.omnivox_sync

install-launchd:
	./scripts/install_launchd.sh install

uninstall-launchd:
	./scripts/install_launchd.sh uninstall
```

- [ ] **Step 9: Write `src/__init__.py`**

```python
"""School automation: Omnivox sync, conversion, upload, digest, recorder."""
```

- [ ] **Step 10: Write `tests/conftest.py`**

```python
import json
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def sample_config_dict(tmp_path):
    """Minimal valid config with two courses."""
    return {
        "semester": "Automne 2026",
        "base_path": str(tmp_path / "School" / "Cegep Automne 2026"),
        "courses": [
            {
                "code": "601-101-MQ",
                "omnivox_name": "Écriture et littérature",
                "folder": "Écriture et littérature",
                "notebook": "Écriture et littérature - Cegep Automne 2026",
                "record": False,
            },
            {
                "code": "201-103-RE",
                "omnivox_name": "Calcul différentiel",
                "folder": "Calcul différentiel",
                "notebook": "Calcul différentiel - Cegep Automne 2026",
                "record": False,
            },
        ],
        "schedule": [],
        "notify": {"macos": False, "ntfy_topic": ""},
        "digest": {"ranking": "rules"},
        "notebooklm": {"mode": "auto"},
        "at_school_check": "off",
        "school_ip_prefix": "",
    }


@pytest.fixture
def tmp_repo(tmp_path):
    """A repo-shaped temp dir with state/ and logs/."""
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def write_config(tmp_repo, sample_config_dict):
    """Write a config dict to tmp_repo/config.yaml and return its path."""

    def _write(overrides=None):
        data = dict(sample_config_dict)
        if overrides:
            data.update(overrides)
        path = tmp_repo / "config.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def write_env(tmp_repo):
    """Write a .env into tmp_repo."""

    def _write(user="student", password="secret", extra=""):
        path = tmp_repo / ".env"
        path.write_text(
            f"OMNIVOX_USER={user}\nOMNIVOX_PASS={password}\n{extra}", encoding="utf-8"
        )
        return path

    return _write


@pytest.fixture
def read_json():
    def _read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    return _read
```

- [ ] **Step 11: Verify the scaffold imports and pytest runs clean**

Run: `.venv/bin/python -m pytest`
Expected: `no tests ran` (exit code 5) — collection succeeds with zero tests. Not an error at this stage.

- [ ] **Step 12: Commit — gitignore first, so no secret can ever be staged**

```bash
git add .gitignore
git commit -m "chore: gitignore secrets and generated dirs before anything else exists"
git add .env.example requirements.txt pyproject.toml Makefile config.yaml config.example.yaml src/__init__.py tests/conftest.py state/.gitkeep logs/.gitkeep docs/
git commit -m "chore: scaffold school-automation repo for Milestone 1"
```

- [ ] **Step 13: Confirm `.env` is untrackable**

```bash
touch .env && git status --porcelain --untracked-files=all | grep -c '\.env$'
```
Expected: `0`. Then `rm .env` — the real one is created in Task 9.

---

## Task 2: `common.py` — config loading and validation

**Files:**
- Create: `src/common.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `tests/conftest.py` fixtures from Task 1.
- Produces: `Course`, `NotifyConfig`, `Config`, `ConfigError`, `load_config(config_path, *, repo_root=None) -> Config`. Every later task loads config through this function only.

Validation rules (spec §6: "exits with a clear error and notification on invalid YAML"):
- YAML parse failure → `ConfigError` naming the file and the parser message.
- Missing `semester` or `base_path` → `ConfigError`.
- `base_path` is `expanduser()`-ed and resolved to absolute. It is **not** required to exist — `omnivox_sync` creates it.
- Each course requires `code`, `omnivox_name`, `folder`, `notebook`. `record` defaults `False`.
- Duplicate course `code` → `ConfigError` (would corrupt state keying).
- A `folder` containing `/` or equal to `.`/`..` → `ConfigError` (path traversal out of `base_path`).
- `digest.ranking` ∈ {rules, gemini}; `notebooklm.mode` ∈ {auto, staging}; `at_school_check` ∈ {off, ip, ssid}. Anything else → `ConfigError`.
- `courses: []` is **valid** — that is the pre-`--discover` state.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
from pathlib import Path

import pytest

from src.common import Config, ConfigError, load_config


def test_loads_valid_config(write_config, tmp_repo):
    cfg = load_config(write_config(), repo_root=tmp_repo)
    assert isinstance(cfg, Config)
    assert cfg.semester == "Automne 2026"
    assert len(cfg.courses) == 2
    assert cfg.courses[0].code == "601-101-MQ"
    assert cfg.courses[0].record is False
    assert cfg.notebooklm_mode == "auto"


def test_base_path_is_absolute_and_expanded(write_config, tmp_repo):
    cfg = load_config(write_config({"base_path": "~/Documents/School/X"}), repo_root=tmp_repo)
    assert cfg.base_path.is_absolute()
    assert "~" not in str(cfg.base_path)
    assert str(cfg.base_path).startswith(str(Path.home()))


def test_empty_courses_is_valid(write_config, tmp_repo):
    cfg = load_config(write_config({"courses": []}), repo_root=tmp_repo)
    assert cfg.courses == []


def test_missing_base_path_raises(write_config, tmp_repo):
    path = write_config()
    path.write_text("semester: X\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="base_path"):
        load_config(path, repo_root=tmp_repo)


def test_invalid_yaml_raises_with_filename(write_config, tmp_repo):
    path = write_config()
    path.write_text("courses: [oops\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(path, repo_root=tmp_repo)
    assert "config.yaml" in str(exc.value)


def test_missing_config_file_raises(tmp_repo):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_repo / "nope.yaml", repo_root=tmp_repo)


def test_course_missing_required_key_raises(write_config, tmp_repo):
    bad = [{"code": "X", "omnivox_name": "Y", "folder": "Z"}]  # no notebook
    with pytest.raises(ConfigError, match="notebook"):
        load_config(write_config({"courses": bad}), repo_root=tmp_repo)


def test_duplicate_course_code_raises(write_config, tmp_repo, sample_config_dict):
    dup = sample_config_dict["courses"] + [dict(sample_config_dict["courses"][0])]
    with pytest.raises(ConfigError, match="[Dd]uplicate"):
        load_config(write_config({"courses": dup}), repo_root=tmp_repo)


@pytest.mark.parametrize("folder", ["a/b", "..", ".", "/abs"])
def test_folder_traversal_raises(write_config, tmp_repo, sample_config_dict, folder):
    bad = [dict(sample_config_dict["courses"][0], folder=folder)]
    with pytest.raises(ConfigError, match="folder"):
        load_config(write_config({"courses": bad}), repo_root=tmp_repo)


@pytest.mark.parametrize(
    "key,value,needle",
    [
        ("digest", {"ranking": "magic"}, "ranking"),
        ("notebooklm", {"mode": "turbo"}, "mode"),
        ("at_school_check", "maybe", "at_school_check"),
    ],
)
def test_enum_values_validated(write_config, tmp_repo, key, value, needle):
    with pytest.raises(ConfigError, match=needle):
        load_config(write_config({key: value}), repo_root=tmp_repo)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.common'`

- [ ] **Step 3: Implement the config half of `src/common.py`**

```python
"""Shared core: config, state, notifications, logging."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_RANKINGS = {"rules", "gemini"}
_NB_MODES = {"auto", "staging"}
_SCHOOL_CHECKS = {"off", "ip", "ssid"}
_COURSE_KEYS = ("code", "omnivox_name", "folder", "notebook")


class ConfigError(Exception):
    """Raised for any unusable configuration. Message is shown to the user."""


@dataclass(frozen=True)
class Course:
    code: str
    omnivox_name: str
    folder: str
    notebook: str
    record: bool = False


@dataclass(frozen=True)
class NotifyConfig:
    macos: bool = True
    ntfy_topic: str = ""


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

    def course_by_code(self, code: str) -> Course | None:
        return next((c for c in self.courses if c.code == code), None)

    def folder_for(self, course: Course) -> Path:
        return self.base_path / course.folder


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
    )


def load_config(config_path: Path, *, repo_root: Path | None = None) -> Config:
    config_path = Path(config_path)
    repo_root = Path(repo_root) if repo_root else config_path.parent

    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

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

    notify_raw = raw.get("notify") or {}
    notify_cfg = NotifyConfig(
        macos=bool(notify_raw.get("macos", True)),
        ntfy_topic=str(notify_raw.get("ntfy_topic", "") or ""),
    )

    ranking = str((raw.get("digest") or {}).get("ranking", "rules"))
    if ranking not in _RANKINGS:
        raise ConfigError(f"digest.ranking must be one of {sorted(_RANKINGS)}, got {ranking!r}")

    nb_mode = str((raw.get("notebooklm") or {}).get("mode", "auto"))
    if nb_mode not in _NB_MODES:
        raise ConfigError(f"notebooklm.mode must be one of {sorted(_NB_MODES)}, got {nb_mode!r}")

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
        digest_ranking=ranking,
        notebooklm_mode=nb_mode,
        at_school_check=school_check,
        school_ip_prefix=str(raw.get("school_ip_prefix", "") or ""),
        repo_root=repo_root,
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS, 14 tests.

- [ ] **Step 5: Commit**

```bash
git add src/common.py tests/test_config.py
git commit -m "feat(common): config loading with strict validation"
```

---

## Task 3: `common.py` — atomic JSON state store

**Files:**
- Modify: `src/common.py` (append)
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing from Task 2.
- Produces: `StateStore` with `read() -> list[dict]`, `write(records)`, `append(record)`. Tasks 7, 13, 15 use it for `downloads.json` and `upload_queue.json`.

Behavior (spec §6, §14): missing file reads as `[]`; writes go to a temp file **in the same directory** then `os.replace` (atomic on the same filesystem); a corrupt file raises rather than silently resetting, because silently discarding state would re-download the semester.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_state.py
import json

import pytest

from src.common import StateError, StateStore


def test_missing_file_reads_as_empty_list(tmp_repo):
    assert StateStore(tmp_repo / "state" / "downloads.json").read() == []


def test_write_then_read_roundtrip(tmp_repo):
    store = StateStore(tmp_repo / "state" / "downloads.json")
    records = [{"course_code": "A", "filename": "f.pdf", "publish_date": "2026-09-01"}]
    store.write(records)
    assert store.read() == records


def test_append_preserves_existing(tmp_repo):
    store = StateStore(tmp_repo / "state" / "downloads.json")
    store.append({"n": 1})
    store.append({"n": 2})
    assert [r["n"] for r in store.read()] == [1, 2]


def test_write_creates_parent_directory(tmp_repo):
    store = StateStore(tmp_repo / "state" / "nested" / "deep.json")
    store.write([{"ok": True}])
    assert store.path.exists()


def test_write_is_atomic_no_temp_file_left(tmp_repo):
    store = StateStore(tmp_repo / "state" / "downloads.json")
    store.write([{"a": 1}])
    leftovers = [p.name for p in (tmp_repo / "state").iterdir() if p.name != "downloads.json"]
    assert leftovers == []


def test_corrupt_file_raises_rather_than_resetting(tmp_repo):
    path = tmp_repo / "state" / "downloads.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError, match="downloads.json"):
        StateStore(path).read()


def test_non_list_content_raises(tmp_repo):
    path = tmp_repo / "state" / "downloads.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    with pytest.raises(StateError, match="list"):
        StateStore(path).read()


def test_unicode_survives_roundtrip(tmp_repo):
    store = StateStore(tmp_repo / "state" / "downloads.json")
    store.write([{"filename": "Écriture — résumé.pdf"}])
    assert store.read()[0]["filename"] == "Écriture — résumé.pdf"


def test_original_file_intact_if_serialization_fails(tmp_repo):
    store = StateStore(tmp_repo / "state" / "downloads.json")
    store.write([{"good": 1}])
    with pytest.raises(TypeError):
        store.write([{"bad": object()}])
    assert store.read() == [{"good": 1}]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_state.py -v`
Expected: FAIL — `ImportError: cannot import name 'StateError'`

- [ ] **Step 3: Append the implementation to `src/common.py`**

Add `import json`, `import os`, and `import tempfile` to the imports at the top of the file.

```python
class StateError(Exception):
    """Raised when a state file exists but cannot be trusted."""


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
```

Note the ordering in `write`: `json.dumps` runs **before** the temp file is created, so a serialization failure leaves the existing file untouched — that is what `test_original_file_intact_if_serialization_fails` pins.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_state.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add src/common.py tests/test_state.py
git commit -m "feat(common): atomic JSON state store"
```

---

## Task 4: `common.py` — credentials, notifications, logging

**Files:**
- Modify: `src/common.py` (append)
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `NotifyConfig`, `ConfigError` from Task 2.
- Produces: `load_credentials(repo_root) -> tuple[str, str]`, `notify(title, message, *, critical=False, cfg=None)`, `setup_logging(module, repo_root) -> logging.Logger`. Used by Tasks 13, 16, 17.

Behavior (spec §6): `notify` posts a macOS notification via `osascript` when `cfg.macos`, and additionally POSTs to `https://ntfy.sh/{topic}` when `ntfy_topic` is non-empty. **`notify` must never raise** — a broken notifier must not take down a sync run; it logs and returns. `load_credentials` reads `.env` via `python-dotenv` and raises `ConfigError` naming the missing key.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notify.py
import logging

import pytest

from src.common import ConfigError, NotifyConfig, load_credentials, notify, setup_logging


def test_load_credentials_reads_env(tmp_repo, write_env):
    write_env(user="1234567", password="hunter2")
    assert load_credentials(tmp_repo) == ("1234567", "hunter2")


def test_missing_env_file_raises(tmp_repo):
    with pytest.raises(ConfigError, match=r"\.env"):
        load_credentials(tmp_repo)


def test_blank_password_raises_naming_the_key(tmp_repo, write_env):
    write_env(user="1234567", password="")
    with pytest.raises(ConfigError, match="OMNIVOX_PASS"):
        load_credentials(tmp_repo)


def test_credentials_are_stripped(tmp_repo, write_env):
    write_env(user="  1234567  ", password=" hunter2 ")
    assert load_credentials(tmp_repo) == ("1234567", "hunter2")


def test_notify_invokes_osascript(monkeypatch):
    calls = []
    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: calls.append(a[0]))
    notify("Title", "Body", cfg=NotifyConfig(macos=True, ntfy_topic=""))
    assert calls and calls[0][0] == "osascript"
    assert "Body" in calls[0][-1] and "Title" in calls[0][-1]


def test_notify_escapes_double_quotes(monkeypatch):
    calls = []
    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: calls.append(a[0]))
    notify('He said "hi"', 'a "quoted" body', cfg=NotifyConfig(macos=True, ntfy_topic=""))
    script = calls[0][-1]
    assert '\\"' in script


def test_notify_skips_osascript_when_macos_false(monkeypatch):
    calls = []
    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: calls.append(a))
    notify("T", "B", cfg=NotifyConfig(macos=False, ntfy_topic=""))
    assert calls == []


def test_notify_posts_to_ntfy_when_topic_set(monkeypatch):
    posted = {}

    def fake_urlopen(request, timeout=None):
        posted["url"] = request.full_url
        posted["body"] = request.data
        posted["headers"] = request.headers

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp()

    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr("src.common.urllib.request.urlopen", fake_urlopen)
    notify("T", "B", cfg=NotifyConfig(macos=False, ntfy_topic="my-topic"))
    assert posted["url"] == "https://ntfy.sh/my-topic"
    assert posted["body"] == b"B"


def test_notify_never_raises_when_osascript_explodes(monkeypatch):
    def boom(*a, **k):
        raise OSError("no osascript here")

    monkeypatch.setattr("src.common.subprocess.run", boom)
    notify("T", "B", cfg=NotifyConfig(macos=True, ntfy_topic=""))  # must not raise


def test_notify_never_raises_when_ntfy_explodes(monkeypatch):
    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr(
        "src.common.urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")),
    )
    notify("T", "B", cfg=NotifyConfig(macos=False, ntfy_topic="t"))  # must not raise


def test_setup_logging_writes_to_logs_dir(tmp_repo):
    logger = setup_logging("omnivox_sync", tmp_repo)
    logger.info("hello from the test")
    for handler in logger.handlers:
        handler.flush()
    log_file = tmp_repo / "logs" / "omnivox_sync.log"
    assert log_file.exists()
    assert "hello from the test" in log_file.read_text(encoding="utf-8")


def test_setup_logging_is_idempotent(tmp_repo):
    a = setup_logging("omnivox_sync", tmp_repo)
    b = setup_logging("omnivox_sync", tmp_repo)
    assert a is b
    assert len(a.handlers) == len([h for h in b.handlers])
    assert len(a.handlers) == 2  # file + stream, not four


def test_setup_logging_rotates_at_1mb(tmp_repo):
    logger = setup_logging("rotate_check", tmp_repo)
    file_handler = next(
        h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    )
    assert file_handler.maxBytes == 1_048_576
    assert file_handler.backupCount == 3
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_notify.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_credentials'`

- [ ] **Step 3: Append the implementation to `src/common.py`**

Add to the imports at the top: `import logging`, `import logging.handlers`, `import subprocess`, `import urllib.request`, and `from dotenv import dotenv_values`.

```python
_LOGGERS: dict[str, logging.Logger] = {}
LOG_MAX_BYTES = 1_048_576
LOG_BACKUPS = 3


def load_credentials(repo_root: Path) -> tuple[str, str]:
    """Read OMNIVOX_USER / OMNIVOX_PASS from repo_root/.env."""
    env_path = Path(repo_root) / ".env"
    if not env_path.exists():
        raise ConfigError(
            f"No .env at {env_path}. Copy .env.example to .env and fill in "
            "OMNIVOX_USER and OMNIVOX_PASS."
        )
    values = dotenv_values(env_path)
    creds = []
    for key in ("OMNIVOX_USER", "OMNIVOX_PASS"):
        value = (values.get(key) or "").strip()
        if not value:
            raise ConfigError(f"{key} is missing or empty in {env_path}")
        creds.append(value)
    return creds[0], creds[1]


def _osascript_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify(
    title: str,
    message: str,
    *,
    critical: bool = False,
    cfg: NotifyConfig | None = None,
) -> None:
    """Best-effort user notification. Never raises: a broken notifier must not
    abort a sync run. Failures are logged instead."""
    cfg = cfg or NotifyConfig()
    log = logging.getLogger("school.notify")

    if cfg.macos:
        sound = ' sound name "Basso"' if critical else ""
        script = (
            f'display notification "{_osascript_escape(message)}" '
            f'with title "{_osascript_escape(title)}"{sound}'
        )
        try:
            subprocess.run(["osascript", "-e", script], check=False, timeout=10)
        except Exception as exc:  # noqa: BLE001 - notification must never abort a run
            log.warning("macOS notification failed: %s", exc)

    if cfg.ntfy_topic:
        try:
            request = urllib.request.Request(
                f"https://ntfy.sh/{cfg.ntfy_topic}",
                data=message.encode("utf-8"),
                headers={
                    "Title": title.encode("ascii", "replace").decode(),
                    "Priority": "high" if critical else "default",
                },
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

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    _LOGGERS[module] = logger
    return logger
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_notify.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Confirm a real notification actually appears**

Run:
```bash
.venv/bin/python -c "from src.common import notify, NotifyConfig; notify('School automation', 'Task 4 wiring works', cfg=NotifyConfig(macos=True, ntfy_topic=''))"
```
Expected: a macOS banner appears. If macOS silently suppresses it, open System Settings → Notifications and allow notifications for the terminal app. Do not proceed until a banner is visible — spec §2's "fail loudly" depends entirely on this channel working.

- [ ] **Step 6: Run the whole suite and commit**

```bash
.venv/bin/python -m pytest
git add src/common.py tests/test_notify.py
git commit -m "feat(common): credentials, notifications, rotating logs"
```

---

## Task 5: `convert.py` — extension routing (pure logic)

**Files:**
- Create: `src/convert.py`
- Test: `tests/test_convert_routing.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `NOTEBOOKLM_SUPPORTED`, `CONVERTIBLE`, `ConversionError`, `needs_conversion(path) -> bool`, `is_uploadable(path) -> bool`. Task 6 adds `convert_to_pdf`; Task 13 calls both.

Routing rule (spec §7 step 7): supported set is `pdf, docx, txt, md, images, mp3/wav, epub` — never converted. `pptx, ppt, xlsx, xls, odp` and **unknown office types** are converted. An unknown, non-office extension (e.g. `.zip`) is neither uploadable nor convertible: it is downloaded and filed, then flagged in the digest and skipped for upload.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_convert_routing.py
from pathlib import Path

import pytest

from src.convert import (
    CONVERTIBLE,
    NOTEBOOKLM_SUPPORTED,
    is_uploadable,
    needs_conversion,
)


@pytest.mark.parametrize(
    "name",
    ["a.pdf", "a.docx", "a.txt", "a.md", "a.png", "a.jpg", "a.jpeg",
     "a.mp3", "a.wav", "a.epub", "A.PDF", "A.DocX"],
)
def test_supported_formats_are_not_converted(name):
    assert needs_conversion(Path(name)) is False
    assert is_uploadable(Path(name)) is True


@pytest.mark.parametrize("name", ["a.pptx", "a.ppt", "a.xlsx", "a.xls", "a.odp", "A.PPTX"])
def test_office_formats_are_converted(name):
    assert needs_conversion(Path(name)) is True


@pytest.mark.parametrize("name", ["a.odt", "a.ods", "a.rtf", "a.doc"])
def test_other_office_types_are_converted(name):
    """Spec: 'and unknown office types'."""
    assert needs_conversion(Path(name)) is True


@pytest.mark.parametrize("name", ["a.zip", "a.exe", "a.mov", "noextension"])
def test_unknown_non_office_is_neither_converted_nor_uploadable(name):
    assert needs_conversion(Path(name)) is False
    assert is_uploadable(Path(name)) is False


def test_supported_and_convertible_sets_do_not_overlap():
    assert NOTEBOOKLM_SUPPORTED.isdisjoint(CONVERTIBLE)


def test_routing_is_case_insensitive_and_handles_full_paths():
    assert needs_conversion(Path("/a/b/Chapitre 3 — résumé.PPTX")) is True
    assert is_uploadable(Path("/a/b/Chapitre 3 — résumé.PDF")) is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_convert_routing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.convert'`

- [ ] **Step 3: Write `src/convert.py`**

```python
"""Format routing and LibreOffice conversion.

Knows nothing about courses, config, or state: paths in, paths out.
"""

from __future__ import annotations

from pathlib import Path

# NotebookLM accepts these directly (spec section 7 step 7).
NOTEBOOKLM_SUPPORTED = frozenset(
    {
        ".pdf", ".docx", ".txt", ".md",
        ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".mp3", ".wav",
        ".epub",
    }
)

# Office formats LibreOffice can render to PDF.
CONVERTIBLE = frozenset(
    {".pptx", ".ppt", ".xlsx", ".xls", ".odp", ".odt", ".ods", ".doc", ".rtf", ".csv"}
)


class ConversionError(Exception):
    """Raised when a file could not be converted to PDF."""


def _ext(path: Path) -> str:
    return path.suffix.lower()


def needs_conversion(path: Path) -> bool:
    """True when the file must be rendered to PDF before upload."""
    return _ext(path) in CONVERTIBLE


def is_uploadable(path: Path) -> bool:
    """True when NotebookLM accepts this file as-is."""
    return _ext(path) in NOTEBOOKLM_SUPPORTED
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_convert_routing.py -v`
Expected: PASS, 28 tests.

- [ ] **Step 5: Commit**

```bash
git add src/convert.py tests/test_convert_routing.py
git commit -m "feat(convert): format routing for NotebookLM support"
```

---

## Task 6: `convert.py` — real LibreOffice conversion

**Files:**
- Modify: `src/convert.py` (append)
- Test: `tests/test_convert_soffice.py`

**Interfaces:**
- Consumes: `ConversionError` from Task 5.
- Produces: `find_soffice() -> str`, `convert_to_pdf(src, outdir, *, timeout=180) -> Path`. Task 13 calls `convert_to_pdf` and catches `ConversionError`.

Implementation notes that matter:
- `soffice` is at `/opt/homebrew/bin/soffice` on this machine (verified). Search PATH first, then the two known app-bundle locations, so a `launchd` job with no PATH still works (spec §11).
- LibreOffice **refuses to run two instances against one user profile**. A `launchd` job firing while the user has LibreOffice open would fail. Pass `-env:UserInstallation=file://<tmpdir>` to give the headless run a private profile. This is the single most common cause of silent `soffice` failure and is worth the extra flag.
- `soffice` frequently exits **0 while producing nothing**. Never trust the return code — assert the output file exists and is non-empty.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_convert_soffice.py
import shutil
import subprocess
from pathlib import Path

import pytest

from src.convert import ConversionError, convert_to_pdf, find_soffice

pytestmark = pytest.mark.soffice

soffice_missing = pytest.mark.skipif(
    shutil.which("soffice") is None
    and not Path("/Applications/LibreOffice.app/Contents/MacOS/soffice").exists(),
    reason="LibreOffice not installed",
)


@soffice_missing
def test_find_soffice_returns_an_executable_path():
    path = Path(find_soffice())
    assert path.exists()
    out = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=120)
    assert "LibreOffice" in out.stdout


@soffice_missing
def test_converts_a_real_pptx_to_pdf(tmp_path):
    src = tmp_path / "deck.fodp"          # flat ODP: valid input, no binary fixture needed
    src.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
        ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
        ' office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.presentation">'
        "<office:body><office:presentation><draw:page draw:name=\"p1\">"
        "<draw:frame><draw:text-box><text:p>Chapitre 1</text:p></draw:text-box></draw:frame>"
        "</draw:page></office:presentation></office:body></office:document>",
        encoding="utf-8",
    )
    outdir = tmp_path / "out"
    outdir.mkdir()
    pdf = convert_to_pdf(src, outdir)
    assert pdf.exists() and pdf.suffix == ".pdf"
    assert pdf.stat().st_size > 0
    assert pdf.read_bytes()[:5] == b"%PDF-"


@soffice_missing
def test_original_is_kept_beside_the_pdf(tmp_path):
    src = tmp_path / "notes.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    outdir = tmp_path / "out"
    outdir.mkdir()
    pdf = convert_to_pdf(src, outdir)
    assert src.exists(), "spec section 7: keep the original beside the PDF"
    assert pdf.exists()


@soffice_missing
def test_unicode_filename_survives(tmp_path):
    src = tmp_path / "Écriture — résumé n°3.csv"
    src.write_text("é,à\n1,2\n", encoding="utf-8")
    outdir = tmp_path / "out"
    outdir.mkdir()
    pdf = convert_to_pdf(src, outdir)
    assert pdf.exists()
    assert pdf.stem == src.stem


def test_missing_source_raises(tmp_path):
    with pytest.raises(ConversionError, match="not found"):
        convert_to_pdf(tmp_path / "ghost.pptx", tmp_path)


def test_zero_exit_but_no_output_still_raises(tmp_path, monkeypatch):
    """soffice often exits 0 while writing nothing. Never trust the return code."""
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"not really a pptx")
    outdir = tmp_path / "out"
    outdir.mkdir()
    monkeypatch.setattr("src.convert.find_soffice", lambda: "/bin/true")
    with pytest.raises(ConversionError, match="produced no output"):
        convert_to_pdf(src, outdir)


def test_timeout_raises_conversion_error(tmp_path, monkeypatch):
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"x")
    monkeypatch.setattr("src.convert.find_soffice", lambda: "/bin/sleep")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr("src.convert.subprocess.run", fake_run)
    with pytest.raises(ConversionError, match="timed out"):
        convert_to_pdf(src, tmp_path, timeout=1)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_convert_soffice.py -v`
Expected: FAIL — `ImportError: cannot import name 'convert_to_pdf'`

- [ ] **Step 3: Append to `src/convert.py`**

Add imports at the top: `import shutil`, `import subprocess`, `import tempfile`.

```python
_SOFFICE_FALLBACKS = (
    "/opt/homebrew/bin/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/local/bin/soffice",
)


def find_soffice() -> str:
    """Locate the soffice binary. Checks PATH, then known install locations,
    because launchd jobs run with no shell PATH (spec section 11)."""
    found = shutil.which("soffice")
    if found:
        return found
    for candidate in _SOFFICE_FALLBACKS:
        if Path(candidate).exists():
            return candidate
    raise ConversionError(
        "LibreOffice (soffice) not found. Install it with: brew install --cask libreoffice"
    )


def convert_to_pdf(src: Path, outdir: Path, *, timeout: int = 180) -> Path:
    """Render `src` to PDF inside `outdir`. The original is left untouched.

    Returns the path to the produced PDF. Raises ConversionError on any failure.
    """
    src = Path(src)
    outdir = Path(outdir)
    if not src.exists():
        raise ConversionError(f"Source file not found: {src}")
    outdir.mkdir(parents=True, exist_ok=True)

    soffice = find_soffice()
    expected = outdir / f"{src.stem}.pdf"

    # A private UserInstallation lets this run even while the user has
    # LibreOffice open; otherwise soffice refuses to start a second instance.
    with tempfile.TemporaryDirectory(prefix="soffice-profile-") as profile:
        cmd = [
            soffice,
            f"-env:UserInstallation=file://{profile}",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(src),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(f"Conversion of {src.name} timed out after {timeout}s") from exc
        except OSError as exc:
            raise ConversionError(f"Could not run soffice for {src.name}: {exc}") from exc

    if not expected.exists() or expected.stat().st_size == 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:400]
        raise ConversionError(
            f"soffice produced no output for {src.name} "
            f"(exit {proc.returncode}){': ' + detail if detail else ''}"
        )
    return expected
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_convert_soffice.py -v`
Expected: PASS, 7 tests. The first LibreOffice run can take 30–60 s while it builds its profile; that is normal.

- [ ] **Step 5: Sanity-check against a real deck from last semester**

```bash
.venv/bin/python -c "
from pathlib import Path
from src.convert import convert_to_pdf
src = Path.home()/'Documents/School/Cegep Winter 2026/L\\'entreprise/ch08_presentationVECondense.pptx'
print(convert_to_pdf(src, Path('/tmp/convtest')))
"
```
Expected: a path is printed and the PDF opens correctly. This is real course material in the exact format the sync will hit most often.

- [ ] **Step 6: Commit**

```bash
git add src/convert.py tests/test_convert_soffice.py
git commit -m "feat(convert): headless LibreOffice PDF conversion with private profile"
```

---

## Task 7: New-file diffing and the on-disk secondary guard

**Files:**
- Create: `src/omnivox_sync.py` (diffing section only; CLI comes in Task 13)
- Test: `tests/test_diffing.py`

**Interfaces:**
- Consumes: `StateStore` (Task 3).
- Produces: `Decision` enum-like constants, `download_key(...)`, `classify_document(doc, state_index, target_dir) -> tuple[str, Path]`, `build_state_index(records) -> dict`. Task 13 calls `classify_document` for every document.

This is the single most correctness-critical piece of M1 and it is pure — no browser, no filesystem writes, fully tested.

**The three-way decision** (spec §7 step 4, plus one gap the spec leaves open):

| Situation | Decision | Rationale |
|---|---|---|
| Tuple `(course, filename, publish_date)` **is** in state | `SKIP_KNOWN` | Already handled on a previous run. |
| Tuple absent **and** no file of that name on disk | `DOWNLOAD` | Genuinely new. |
| Tuple absent, file on disk, **and no state record for `(course, filename)` at any date** | `SKIP_EXISTING` — log a warning, record the tuple | Spec's stated case: "state file was lost". Do not re-download. |
| Tuple absent, file on disk, **but a record exists for `(course, filename)` at a *different* date** | `DOWNLOAD_REVISION` — save as `name (YYYY-MM-DD).ext` | **Gap the spec does not cover.** The professor re-published the file. Under a literal reading of the spec this would hit `SKIP_EXISTING` and the revision would never be fetched — a silent information loss, which spec §2 forbids. A prior record at a different date is unambiguous evidence of a revision, not of state loss, so it is safe to distinguish. Flagged here as a deliberate, documented addition. |

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diffing.py
from pathlib import Path

import pytest

from src.omnivox_sync import (
    DOWNLOAD,
    DOWNLOAD_REVISION,
    SKIP_EXISTING,
    SKIP_KNOWN,
    build_state_index,
    classify_document,
    download_key,
)


def _doc(course="601-101-MQ", filename="ch01.pptx", publish_date="2026-09-03"):
    from src.omnivox import OmnivoxDocument

    return OmnivoxDocument(
        course_code=course, filename=filename, publish_date=publish_date, ref="r1"
    )


def _record(course="601-101-MQ", filename="ch01.pptx", publish_date="2026-09-03"):
    return {"course_code": course, "filename": filename, "publish_date": publish_date}


def test_download_key_is_the_spec_tuple():
    assert download_key("A", "f.pdf", "2026-09-03") == ("A", "f.pdf", "2026-09-03")


def test_unseen_document_with_empty_folder_is_downloaded(tmp_path):
    decision, dest = classify_document(_doc(), build_state_index([]), tmp_path)
    assert decision == DOWNLOAD
    assert dest == tmp_path / "ch01.pptx"


def test_known_tuple_is_skipped(tmp_path):
    index = build_state_index([_record()])
    decision, _ = classify_document(_doc(), index, tmp_path)
    assert decision == SKIP_KNOWN


def test_known_tuple_is_skipped_even_if_file_was_deleted_from_disk(tmp_path):
    """State is the source of truth; a deleted local file is the user's choice."""
    index = build_state_index([_record()])
    decision, _ = classify_document(_doc(), index, tmp_path)
    assert decision == SKIP_KNOWN


def test_file_on_disk_with_no_state_record_is_skipped_as_state_loss(tmp_path):
    (tmp_path / "ch01.pptx").write_text("existing", encoding="utf-8")
    decision, dest = classify_document(_doc(), build_state_index([]), tmp_path)
    assert decision == SKIP_EXISTING
    assert dest == tmp_path / "ch01.pptx"
    assert dest.read_text(encoding="utf-8") == "existing", "must not be overwritten"


def test_republished_file_is_downloaded_with_a_dated_suffix(tmp_path):
    (tmp_path / "ch01.pptx").write_text("old version", encoding="utf-8")
    index = build_state_index([_record(publish_date="2026-09-03")])
    decision, dest = classify_document(_doc(publish_date="2026-09-17"), index, tmp_path)
    assert decision == DOWNLOAD_REVISION
    assert dest == tmp_path / "ch01 (2026-09-17).pptx"


def test_revision_suffix_preserves_multi_dot_names(tmp_path):
    (tmp_path / "notes.v2.docx").write_text("old", encoding="utf-8")
    index = build_state_index([_record(filename="notes.v2.docx", publish_date="2026-09-03")])
    _, dest = classify_document(
        _doc(filename="notes.v2.docx", publish_date="2026-09-17"), index, tmp_path
    )
    assert dest.name == "notes.v2 (2026-09-17).docx"


def test_same_filename_in_two_courses_is_independent(tmp_path):
    index = build_state_index([_record(course="601-101-MQ")])
    other = _doc(course="201-103-RE")
    decision, _ = classify_document(other, index, tmp_path)
    assert decision == DOWNLOAD


def test_missing_target_directory_is_treated_as_empty(tmp_path):
    decision, _ = classify_document(_doc(), build_state_index([]), tmp_path / "nope")
    assert decision == DOWNLOAD


def test_build_state_index_handles_legacy_records_missing_keys():
    index = build_state_index([{"course_code": "A"}, {}, _record()])
    assert ("601-101-MQ", "ch01.pptx", "2026-09-03") in index["tuples"]


def test_unicode_filenames_match_exactly(tmp_path):
    doc = _doc(filename="Écriture — résumé.pdf")
    index = build_state_index([_record(filename="Écriture — résumé.pdf")])
    decision, _ = classify_document(doc, index, tmp_path)
    assert decision == SKIP_KNOWN


@pytest.mark.parametrize("evil", ["../escape.pdf", "a/b.pdf", "/etc/passwd"])
def test_filenames_from_the_site_cannot_escape_the_course_folder(tmp_path, evil):
    decision, dest = classify_document(_doc(filename=evil), build_state_index([]), tmp_path)
    assert dest.parent == tmp_path
    assert ".." not in dest.parts
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diffing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.omnivox_sync'`

- [ ] **Step 3: Create `src/omnivox_sync.py` with the diffing section**

```python
"""Milestone 1: Omnivox LEA sync.

Orchestration only. All Playwright work lives in src/omnivox.py, so every
decision in this module is testable without a browser.
"""

from __future__ import annotations

from pathlib import Path

from src.omnivox import OmnivoxDocument

DOWNLOAD = "download"
DOWNLOAD_REVISION = "download_revision"
SKIP_KNOWN = "skip_known"
SKIP_EXISTING = "skip_existing"


def download_key(course_code: str, filename: str, publish_date: str) -> tuple[str, str, str]:
    """The spec's new-file identity tuple (section 7 step 4)."""
    return (course_code, filename, publish_date)


def build_state_index(records: list[dict]) -> dict:
    """Index download records for O(1) lookup.

    Returns {"tuples": set[(course, filename, date)], "names": set[(course, filename)]}.
    Records missing keys are ignored rather than crashing the run.
    """
    tuples: set[tuple[str, str, str]] = set()
    names: set[tuple[str, str]] = set()
    for record in records:
        course = record.get("course_code")
        filename = record.get("filename")
        if not course or not filename:
            continue
        names.add((course, filename))
        tuples.add(download_key(course, filename, record.get("publish_date", "")))
    return {"tuples": tuples, "names": names}


def safe_filename(filename: str) -> str:
    """Reduce a site-supplied filename to a single path component.

    Filenames come from a remote page and are never trusted to stay inside
    the course folder.
    """
    name = Path(filename.replace("\\", "/")).name.strip()
    if name in ("", ".", ".."):
        return "unnamed_document"
    return name


def _revision_name(filename: str, publish_date: str) -> str:
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        return f"{filename} ({publish_date})"
    return f"{stem} ({publish_date}).{ext}"


def classify_document(
    doc: OmnivoxDocument, state_index: dict, target_dir: Path
) -> tuple[str, Path]:
    """Decide what to do with one document row, and where it would land.

    Returns (decision, destination_path). Performs no I/O beyond an existence
    check and never writes.
    """
    filename = safe_filename(doc.filename)
    target_dir = Path(target_dir)
    dest = target_dir / filename

    if download_key(doc.course_code, doc.filename, doc.publish_date) in state_index["tuples"]:
        return SKIP_KNOWN, dest

    if not dest.exists():
        return DOWNLOAD, dest

    # A file with this name is already on disk but the tuple is unknown.
    if (doc.course_code, doc.filename) in state_index["names"]:
        # We have seen this name before at a different publish date: a revision.
        return DOWNLOAD_REVISION, target_dir / _revision_name(filename, doc.publish_date)

    # No record at all: the state file was lost. Do not re-download (spec section 7 step 4).
    return SKIP_EXISTING, dest
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_diffing.py -v`
Expected: PASS, 14 tests. `src.omnivox` does not exist yet — create a placeholder `src/omnivox.py` containing only the two dataclasses and three exception classes from the Interface Reference so the import resolves; Task 8 fills in the rest.

- [ ] **Step 5: Commit**

```bash
git add src/omnivox_sync.py src/omnivox.py tests/test_diffing.py
git commit -m "feat(sync): new-file diffing with on-disk secondary guard and revision handling"
```

---

## Task 8: `omnivox.py` — Playwright session scaffolding

**Files:**
- Modify: `src/omnivox.py` (replace the Task 7 placeholder with the full driver skeleton)
- Test: `tests/test_omnivox_session.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `OmnivoxSession` as a context manager with `screenshot(label) -> Path`. Tasks 9–12 add `login`, `list_courses`, `list_documents`, `download`.

Design points:
- **Persistent context** at `state/omnivox-profile/` keeps the Omnivox session cookie between runs, so most runs skip the login form entirely (spec §7 step 1).
- `accept_downloads=True` at context creation — required for `expect_download` in Task 12.
- `screenshot(label)` writes `logs/{label}-{timestamp}.png`. Every failure path in Tasks 9–12 calls it (spec §7 failure behavior).
- Locale `fr-CA`: LEA renders French labels ("Documents distribués"), and pinning the locale keeps selectors stable regardless of the machine's language.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_omnivox_session.py
import pytest

from src.omnivox import LoginError, OmnivoxError, OmnivoxSession, ParseError


def test_exception_hierarchy():
    assert issubclass(LoginError, OmnivoxError)
    assert issubclass(ParseError, OmnivoxError)


def test_session_creates_profile_and_screenshot_dirs(tmp_repo):
    profile = tmp_repo / "state" / "omnivox-profile"
    shots = tmp_repo / "logs"
    session = OmnivoxSession(profile, screenshot_dir=shots)
    assert session.profile_dir == profile
    assert session.screenshot_dir == shots


@pytest.mark.live
def test_session_opens_and_closes_a_browser(tmp_repo):
    profile = tmp_repo / "state" / "omnivox-profile"
    with OmnivoxSession(profile, screenshot_dir=tmp_repo / "logs") as session:
        session.page.goto("about:blank")
        assert session.page.title() == ""
    assert profile.exists(), "persistent context must materialise the profile dir"


@pytest.mark.live
def test_screenshot_writes_a_png(tmp_repo):
    with OmnivoxSession(
        tmp_repo / "state" / "omnivox-profile", screenshot_dir=tmp_repo / "logs"
    ) as session:
        session.page.goto("about:blank")
        shot = session.screenshot("unit-test")
        assert shot.exists()
        assert shot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert shot.parent == tmp_repo / "logs"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_omnivox_session.py -v`
Expected: FAIL — `ImportError: cannot import name 'OmnivoxSession'`

- [ ] **Step 3: Write `src/omnivox.py`**

```python
"""Playwright driver for Omnivox / LEA.

The only module that touches a browser. Returns plain typed records so
orchestration stays testable without one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OMNIVOX_HOME = "https://cegepmontpetit.omnivox.ca"
LEA_HOME = "https://cegepmontpetit-lea.omnivox.ca"
DEFAULT_TIMEOUT_MS = 30_000


class OmnivoxError(Exception):
    """Base class for every Omnivox site failure."""


class LoginError(OmnivoxError):
    """Credentials rejected, or the login form could not be completed."""


class ParseError(OmnivoxError):
    """The page loaded but did not have the structure we expect."""


@dataclass(frozen=True)
class OmnivoxCourse:
    code: str
    name: str
    href: str


@dataclass(frozen=True)
class OmnivoxDocument:
    course_code: str
    filename: str
    publish_date: str  # ISO YYYY-MM-DD; "" when the page shows no parseable date
    ref: str           # opaque handle used by download(): an href or a row selector


class OmnivoxSession:
    """A logged-in browser session against Omnivox, reused across runs.

    Use as a context manager:

        with OmnivoxSession(profile_dir) as session:
            session.login(user, password)
            courses = session.list_courses()
    """

    def __init__(
        self,
        profile_dir: Path,
        *,
        headed: bool = False,
        screenshot_dir: Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.headed = headed
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self.log = logger or logging.getLogger("school.omnivox")
        self._playwright = None
        self._context = None
        self.page: Page | None = None

    def __enter__(self) -> "OmnivoxSession":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=not self.headed,
            accept_downloads=True,
            locale="fr-CA",
            timezone_id="America/Toronto",
            viewport={"width": 1440, "height": 900},
        )
        self._context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
        return False

    def screenshot(self, label: str) -> Path | None:
        """Save a full-page screenshot for debugging a failure. Never raises."""
        if self.screenshot_dir is None or self.page is None:
            return None
        try:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.screenshot_dir / f"{safe}-{stamp}.png"
            self.page.screenshot(path=str(path), full_page=True)
            self.log.info("Saved failure screenshot: %s", path)
            return path
        except Exception as exc:  # noqa: BLE001 - diagnostics must never mask the real error
            self.log.warning("Could not save screenshot %s: %s", label, exc)
            return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_omnivox_session.py -v`
Expected: PASS, 4 tests (2 marked `live` still run — they only hit `about:blank`, no credentials needed).

- [ ] **Step 5: Commit**

```bash
git add src/omnivox.py tests/test_omnivox_session.py
git commit -m "feat(omnivox): persistent Playwright session with failure screenshots"
```

---

## Task 9: `omnivox.py` — login with session reuse (live site)

**Files:**
- Modify: `src/omnivox.py` (append `is_logged_in`, `login`)
- Create: `.env` (by the user, not by the implementer)
- Test: `tests/test_omnivox_login.py`

**Interfaces:**
- Consumes: `OmnivoxSession`, `LoginError` (Task 8).
- Produces: `OmnivoxSession.is_logged_in() -> bool` and `OmnivoxSession.login(user, password) -> None`. Tasks 10–12 assume `login` has run.

**Credential prerequisite — this is the task that blocks on the user.** Before starting:

```bash
cp .env.example .env
open -a TextEdit .env    # fill OMNIVOX_USER and OMNIVOX_PASS, save, close
```

The credentials are typed by the user directly into `.env` on their own machine. They are never pasted into a chat transcript, never echoed to a terminal, and never committed (`.gitignore` from Task 1 Step 1 covers this — verified in Task 1 Step 13).

**Selector honesty.** The exact DOM of Cégep Édouard-Montpetit's Omnivox is not known at planning time and Omnivox skins vary per college. The constants below are **starting hypotheses based on the standard Omnivox login page**, and Step 1 of this task is a live discovery run that confirms or replaces them. Do not treat them as verified. Every selector is written as an ordered fallback list so a skin difference degrades to the next candidate rather than crashing.

- [ ] **Step 1: Discover the real login DOM before writing any selector**

```bash
.venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=False)
    pg = b.new_page(locale="fr-CA")
    pg.goto("https://cegepmontpetit.omnivox.ca", wait_until="domcontentloaded")
    pg.wait_for_timeout(3000)
    print("URL:", pg.url)
    for f in pg.query_selector_all("input"):
        print("INPUT", {k: f.get_attribute(k) for k in ("id","name","type","placeholder")})
    for f in pg.query_selector_all("button, input[type=submit], a[href*=ogin]"):
        print("SUBMIT", f.get_attribute("id"), f.get_attribute("name"), (f.text_content() or "").strip()[:40])
    pg.screenshot(path="logs/login-discovery.png", full_page=True)
    b.close()
PY
```

Record the real `id`/`name` of the student-number field, the password field, and the submit control. Put them **first** in the candidate lists in Step 3, keeping the hypotheses as fallbacks.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_omnivox_login.py
import pytest

from src.common import load_credentials
from src.omnivox import LoginError, OmnivoxSession

REPO = __import__("pathlib").Path(__file__).resolve().parents[1]


def test_login_error_is_raised_for_blank_credentials(tmp_repo):
    with OmnivoxSession(tmp_repo / "state" / "p", screenshot_dir=tmp_repo / "logs") as s:
        with pytest.raises(LoginError, match="empty"):
            s.login("", "")


@pytest.mark.live
def test_real_login_succeeds_and_is_reused(tmp_repo):
    """First call logs in via the form; second call reuses the session cookie."""
    user, password = load_credentials(REPO)
    profile = tmp_repo / "state" / "omnivox-profile"

    with OmnivoxSession(profile, screenshot_dir=tmp_repo / "logs") as s:
        s.login(user, password)
        assert s.is_logged_in()

    with OmnivoxSession(profile, screenshot_dir=tmp_repo / "logs") as s:
        s.login(user, password)          # must be a no-op via the stored cookie
        assert s.is_logged_in()


@pytest.mark.live
def test_bad_password_raises_login_error_and_screenshots(tmp_repo):
    user, _ = load_credentials(REPO)
    shots = tmp_repo / "logs"
    with OmnivoxSession(tmp_repo / "state" / "fresh", screenshot_dir=shots) as s:
        with pytest.raises(LoginError):
            s.login(user, "definitely-not-the-password")
    assert list(shots.glob("login-failed-*.png")), "a failure screenshot must be saved"
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_omnivox_login.py -v`
Expected: FAIL — `AttributeError: 'OmnivoxSession' object has no attribute 'login'`

- [ ] **Step 4: Append `login` and `is_logged_in` to `src/omnivox.py`**

Replace the candidate lists with whatever Step 1 actually found, keeping these as fallbacks.

```python
# Ordered candidates: first match wins. Update the leading entry from the
# Task 9 Step 1 discovery run; later entries are fallbacks across Omnivox skins.
_USER_FIELDS = ("input#NoDA", "input[name='NoDA']", "input[name='UserID']",
                "input[type='text'][name*='No']", "form input[type='text']")
_PASS_FIELDS = ("input#PasswordEtu", "input[name='PasswordEtu']",
                "input[name='Password']", "input[type='password']")
_SUBMIT = ("input[type='submit']", "button[type='submit']",
           "a#btnValider", "button:has-text('Valider')", "button:has-text('Connexion')")
# Presence of any of these means we are past the login wall.
_LOGGED_IN_MARKERS = ("a[href*='Deconnexion']", "a[href*='Logout']",
                      "text=Quoi de neuf", "#entete_nom_usager", "a[href*='/lea/']")


def _first_visible(page: Page, selectors: tuple[str, ...], timeout_ms: int = 4000):
    """Return the first selector in `selectors` that resolves to a visible element."""
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except Exception:  # noqa: BLE001 - try the next candidate
            continue
    return None
```

Then add these methods to `OmnivoxSession`:

```python
    def is_logged_in(self) -> bool:
        """True when the current page shows post-login chrome."""
        if self.page is None:
            return False
        for marker in _LOGGED_IN_MARKERS:
            try:
                if self.page.locator(marker).first.is_visible(timeout=1500):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def login(self, user: str, password: str) -> None:
        """Log in if needed. A no-op when the stored session cookie is still valid.

        Always starts from the Omnivox homepage: session deep links expire
        (spec section 7 step 2).
        """
        if not user or not password:
            raise LoginError("OMNIVOX_USER / OMNIVOX_PASS are empty; fill in .env")

        self.page.goto(OMNIVOX_HOME, wait_until="domcontentloaded")

        if self.is_logged_in():
            self.log.info("Existing Omnivox session reused; skipping login form")
            return

        user_field = _first_visible(self.page, _USER_FIELDS)
        pass_field = _first_visible(self.page, _PASS_FIELDS)
        if user_field is None or pass_field is None:
            self.screenshot("login-failed-noform")
            raise LoginError(
                f"Login form not found at {self.page.url}. The page layout probably "
                "changed; see the screenshot in logs/ and update _USER_FIELDS/_PASS_FIELDS."
            )

        user_field.fill(user)
        pass_field.fill(password)

        submit = _first_visible(self.page, _SUBMIT)
        if submit is None:
            self.screenshot("login-failed-nosubmit")
            raise LoginError(f"No submit control found on the login form at {self.page.url}")

        submit.click()
        try:
            self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - some skins keep a socket open; verify by marker instead
            pass

        if not self.is_logged_in():
            self.screenshot("login-failed-rejected")
            raise LoginError(
                "Login did not complete. Most likely a wrong password, a changed "
                f"password, or a new interstitial. Landed on {self.page.url}; "
                "see the screenshot in logs/."
            )
        self.log.info("Logged in to Omnivox as %s", user)
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_omnivox_login.py -v`
Expected: PASS, 3 tests.

If `test_real_login_succeeds_and_is_reused` fails, re-run headed to watch it:
```bash
.venv/bin/python -m pytest tests/test_omnivox_login.py -k real_login -v -s
```
and inspect `logs/login-failed-*.png`. **If Omnivox presents a CAPTCHA or a 2FA challenge, stop and report it** — do not attempt to solve or bypass it. That is a design change (the session would have to be established by the user by hand once, then reused from the persistent profile), not a bug to code around.

- [ ] **Step 6: Confirm the credential file never entered git**

```bash
git log --all --numstat --pretty=format: | grep -c '\.env$' ; git status --porcelain
```
Expected: `0`, and `.env` absent from `git status`.

- [ ] **Step 7: Commit**

```bash
git add src/omnivox.py tests/test_omnivox_login.py
git commit -m "feat(omnivox): login with persistent session reuse and failure screenshots"
```

---

## Task 10: Course listing and `--discover` semester bootstrap

**Files:**
- Modify: `src/omnivox.py` (append `list_courses`)
- Modify: `src/omnivox_sync.py` (append `render_discovery_yaml`)
- Test: `tests/test_discover.py`

**Interfaces:**
- Consumes: `OmnivoxCourse` (Task 8), `login` (Task 9).
- Produces: `OmnivoxSession.list_courses() -> list[OmnivoxCourse]` and `render_discovery_yaml(courses, semester) -> str`. Task 13 wires `--discover` to both.

This implements spec §5.3: `--discover` prints a paste-ready `courses:` block with `folder` and `notebook` pre-filled from the display name, so the user never hand-types the semester config.

- [ ] **Step 1: Discover the real LEA course-list DOM**

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from src.common import load_credentials
from src.omnivox import OmnivoxSession, LEA_HOME
user, pw = load_credentials(Path.cwd())
with OmnivoxSession(Path("state/omnivox-profile"), headed=True,
                    screenshot_dir=Path("logs")) as s:
    s.login(user, pw)
    s.page.goto(LEA_HOME, wait_until="domcontentloaded")
    s.page.wait_for_timeout(3000)
    print("URL:", s.page.url)
    print(s.page.content()[:6000])
    s.page.screenshot(path="logs/lea-discovery.png", full_page=True)
    input("Inspect the page, then press Enter...")
PY
```

Note how each course row is marked up: the element carrying the course **code** (format `NNN-NNN-XX`), the element carrying the **display name**, and the `href` into that course. Record them for Step 3.

- [ ] **Step 2: Write the failing tests**

The pure part — YAML rendering — is fully testable without a browser.

```python
# tests/test_discover.py
import yaml

from src.omnivox import OmnivoxCourse
from src.omnivox_sync import folder_name_from, render_discovery_yaml

COURSES = [
    OmnivoxCourse(code="601-101-MQ", name="Écriture et littérature", href="/lea/a"),
    OmnivoxCourse(code="201-103-RE", name="Calcul différentiel", href="/lea/b"),
]


def test_render_produces_parseable_yaml():
    parsed = yaml.safe_load(render_discovery_yaml(COURSES, "Automne 2026"))
    assert [c["code"] for c in parsed["courses"]] == ["601-101-MQ", "201-103-RE"]


def test_every_required_course_key_is_present():
    parsed = yaml.safe_load(render_discovery_yaml(COURSES, "Automne 2026"))
    for course in parsed["courses"]:
        assert set(course) == {"code", "omnivox_name", "folder", "notebook", "record"}
        assert course["record"] is False


def test_notebook_name_includes_the_semester():
    parsed = yaml.safe_load(render_discovery_yaml(COURSES, "Automne 2026"))
    assert parsed["courses"][0]["notebook"] == "Écriture et littérature - Cegep Automne 2026"


def test_folder_defaults_to_the_display_name():
    parsed = yaml.safe_load(render_discovery_yaml(COURSES, "Automne 2026"))
    assert parsed["courses"][0]["folder"] == "Écriture et littérature"


def test_accents_survive_yaml_round_trip():
    text = render_discovery_yaml(COURSES, "Automne 2026")
    assert "Écriture et littérature" in text, "must not be escaped to \\uXXXX"


def test_rendered_block_passes_load_config(tmp_repo, sample_config_dict):
    """The whole point of --discover: the output is paste-ready and valid."""
    from src.common import load_config

    parsed = yaml.safe_load(render_discovery_yaml(COURSES, "Automne 2026"))
    merged = dict(sample_config_dict, courses=parsed["courses"])
    path = tmp_repo / "config.yaml"
    path.write_text(yaml.safe_dump(merged, allow_unicode=True), encoding="utf-8")
    cfg = load_config(path, repo_root=tmp_repo)
    assert [c.code for c in cfg.courses] == ["601-101-MQ", "201-103-RE"]


def test_folder_name_strips_slashes_and_whitespace():
    assert folder_name_from("  Calcul / Algèbre  ") == "Calcul - Algèbre"
    assert folder_name_from("Économie") == "Économie"


def test_folder_name_never_produces_a_traversal():
    assert "/" not in folder_name_from("../etc")
    assert folder_name_from("..") == "unnamed course"


def test_empty_course_list_renders_valid_empty_yaml():
    parsed = yaml.safe_load(render_discovery_yaml([], "Automne 2026"))
    assert parsed["courses"] == []
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discover.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_discovery_yaml'`

- [ ] **Step 4: Append `list_courses` to `src/omnivox.py`**

Add `import re` at the top. Replace the candidate selectors with what Step 1 found.

```python
_COURSE_CODE_RE = re.compile(r"\b(\d{3}-\w{3}-\w{2})\b")
_COURSE_ROW_CANDIDATES = (
    "a[href*='SommaireCours']",
    "a[href*='/lea/'][href*='Cours']",
    "table.tableauFormat tr a",
    "#tblListeCours tr",
)


    def list_courses(self) -> list[OmnivoxCourse]:
        """Every course visible on the LEA dashboard.

        Always navigates from LEA_HOME: session deep links expire.
        """
        self.page.goto(LEA_HOME, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
        except Exception:  # noqa: BLE001
            pass

        courses: list[OmnivoxCourse] = []
        seen: set[str] = set()
        for selector in _COURSE_ROW_CANDIDATES:
            for element in self.page.locator(selector).all():
                try:
                    text = (element.text_content() or "").strip()
                    href = element.get_attribute("href") or ""
                except Exception:  # noqa: BLE001
                    continue
                match = _COURSE_CODE_RE.search(text) or _COURSE_CODE_RE.search(href)
                if not match:
                    continue
                code = match.group(1)
                if code in seen:
                    continue
                name = _COURSE_CODE_RE.sub("", text).strip(" -–— \t\n")
                seen.add(code)
                courses.append(OmnivoxCourse(code=code, name=name or code, href=href))
            if courses:
                break

        if not courses:
            self.screenshot("courses-parse-failed")
            raise ParseError(
                f"No courses found on the LEA dashboard at {self.page.url}. "
                "Either the semester has no courses yet, or the page layout changed; "
                "see the screenshot in logs/ and update _COURSE_ROW_CANDIDATES."
            )
        self.log.info("Found %d courses on LEA", len(courses))
        return courses
```

- [ ] **Step 5: Append the renderer to `src/omnivox_sync.py`**

Add `import yaml` and `from src.omnivox import OmnivoxCourse` at the top.

```python
def folder_name_from(display_name: str) -> str:
    """Turn an Omnivox display name into a safe single-component folder name."""
    cleaned = display_name.replace("/", "-").replace("\\", "-").strip()
    cleaned = " ".join(cleaned.split())
    if cleaned in ("", ".", ".."):
        return "unnamed course"
    return cleaned


def render_discovery_yaml(courses: list[OmnivoxCourse], semester: str) -> str:
    """A paste-ready `courses:` block for config.yaml (spec section 5.3)."""
    block = {
        "courses": [
            {
                "code": course.code,
                "omnivox_name": course.name,
                "folder": folder_name_from(course.name),
                "notebook": f"{course.name} - Cegep {semester}",
                "record": False,
            }
            for course in courses
        ]
    }
    return yaml.safe_dump(block, allow_unicode=True, sort_keys=False, width=1000)
```

- [ ] **Step 6: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discover.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 7: Commit**

```bash
git add src/omnivox.py src/omnivox_sync.py tests/test_discover.py
git commit -m "feat(discover): LEA course listing and paste-ready config block"
```

---

## Task 11: Document table parsing

**Files:**
- Modify: `src/omnivox.py` (append `list_documents`, `parse_publish_date`)
- Test: `tests/test_document_parsing.py`

**Interfaces:**
- Consumes: `OmnivoxCourse`, `OmnivoxDocument`, `ParseError` (Task 8).
- Produces: `OmnivoxSession.list_documents(course) -> list[OmnivoxDocument]` and the pure helper `parse_publish_date(text) -> str`. Task 13 feeds the result into `classify_document`.

**Build-time reality (spec §5.3).** This plan is written on 2026-08-06; the fall course list may not exist on LEA yet. Development therefore targets whatever session **is** browsable — Winter 2026 or Summer 2026, via LEA's session selector — using `--dry-run` so nothing is written. `list_documents` must work against any session; the session selector itself is only used manually during development and is not part of the scheduled run.

**Date parsing matters more than it looks.** The tuple `(course, filename, publish_date)` is the identity of a document. If date formatting is unstable across runs — `2026-09-03` one day, `3 septembre 2026` the next — every document looks new forever and the sync re-downloads the semester. `parse_publish_date` normalises to ISO and is the most heavily tested pure function after `classify_document`.

- [ ] **Step 1: Discover the real "Documents distribués" DOM**

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from src.common import load_credentials
from src.omnivox import OmnivoxSession, LEA_HOME
user, pw = load_credentials(Path.cwd())
with OmnivoxSession(Path("state/omnivox-profile"), headed=True, screenshot_dir=Path("logs")) as s:
    s.login(user, pw)
    for c in s.list_courses():
        print(c)
    print("\nNavigate by hand to one course > 'Documents et vidéos' > 'Documents distribués'.")
    input("Press Enter once the document table is on screen...")
    print("URL:", s.page.url)
    print(s.page.content()[:12000])
    s.page.screenshot(path="logs/documents-discovery.png", full_page=True)
PY
```

Record: the row selector, which cell holds the filename, which holds the date, the date's literal format, and whether the download is a direct `href` or a JavaScript handler. This determines the shape of `ref`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_document_parsing.py
import pytest

from src.omnivox import parse_publish_date


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-09-03", "2026-09-03"),
        ("03-09-2026", "2026-09-03"),
        ("03/09/2026", "2026-09-03"),
        ("2026/09/03", "2026-09-03"),
        ("3 septembre 2026", "2026-09-03"),
        ("13 décembre 2026", "2026-12-13"),
        ("1 janvier 2027", "2027-01-01"),
        ("Publié le 3 septembre 2026", "2026-09-03"),
        ("2026-09-03 14:22", "2026-09-03"),
        ("  03-09-2026  ", "2026-09-03"),
    ],
)
def test_dates_normalise_to_iso(raw, expected):
    assert parse_publish_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "not a date", "Nouveau"])
def test_unparseable_dates_return_empty_string(raw):
    assert parse_publish_date(raw) == ""


def test_all_twelve_french_months_parse():
    months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
              "août", "septembre", "octobre", "novembre", "décembre"]
    for index, month in enumerate(months, start=1):
        assert parse_publish_date(f"5 {month} 2026") == f"2026-{index:02d}-05"


def test_french_months_parse_without_accents():
    """Some Omnivox skins strip accents."""
    assert parse_publish_date("5 fevrier 2026") == "2026-02-05"
    assert parse_publish_date("5 decembre 2026") == "2026-12-05"


def test_parsing_is_idempotent():
    once = parse_publish_date("3 septembre 2026")
    assert parse_publish_date(once) == once, "re-parsing must be stable or state breaks"


def test_ambiguous_ddmm_prefers_day_first():
    """Quebec convention is DD-MM-YYYY; 05-09 is 5 September, not 9 May."""
    assert parse_publish_date("05-09-2026") == "2026-09-05"
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_document_parsing.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_publish_date'`

- [ ] **Step 4: Append to `src/omnivox.py`**

Add `import unicodedata` at the top. Update the selector candidates from Step 1.

```python
_FRENCH_MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "decembre": 12,
}
_ISO_RE = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
_DMY_RE = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")
_TEXT_DATE_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})\b")


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    ).lower()


def parse_publish_date(raw: str) -> str:
    """Normalise an Omnivox date cell to ISO YYYY-MM-DD, or "" if unparseable.

    Stability matters: this value is part of the identity tuple that decides
    whether a document is new (spec section 7 step 4).
    """
    if not raw:
        return ""
    text = raw.strip()

    match = _ISO_RE.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"

    match = _DMY_RE.search(text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    match = _TEXT_DATE_RE.search(text)
    if match:
        day_s, month_word, year_s = match.groups()
        month = _FRENCH_MONTHS.get(_strip_accents(month_word))
        if month:
            return f"{int(year_s):04d}-{month:02d}-{int(day_s):02d}"

    return ""


_DOC_LINK_CANDIDATES = (
    "a[href*='DepotDocument']",
    "a[href*='Telecharger']",
    "a[href*='Fichier']",
    "table tr a[href]",
)
_DOCS_NAV_TEXTS = ("Documents distribués", "Documents et vidéos", "Documents")
```

Then add the method to `OmnivoxSession`:

```python
    def open_documents_page(self, course: OmnivoxCourse) -> None:
        """Navigate from LEA_HOME into this course's 'Documents distribués' page."""
        self.page.goto(LEA_HOME, wait_until="domcontentloaded")
        if course.href:
            self.page.goto(
                course.href if course.href.startswith("http") else LEA_HOME + course.href,
                wait_until="domcontentloaded",
            )
        for label in _DOCS_NAV_TEXTS:
            link = self.page.get_by_role("link", name=re.compile(label, re.I)).first
            try:
                if link.is_visible(timeout=2500):
                    link.click()
                    self.page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - try the next label
                continue

    def list_documents(self, course: OmnivoxCourse) -> list[OmnivoxDocument]:
        """Every distributed document row for one course.

        An empty list is a valid result (a course may have posted nothing yet)
        and is NOT an error. A missing table is an error.
        """
        self.open_documents_page(course)

        rows = self.page.locator("table tr")
        if rows.count() == 0:
            self.screenshot(f"documents-no-table-{course.code}")
            raise ParseError(
                f"No document table found for {course.code} at {self.page.url}. "
                "See the screenshot in logs/ and update _DOC_LINK_CANDIDATES."
            )

        documents: list[OmnivoxDocument] = []
        seen: set[tuple[str, str]] = set()
        for index in range(rows.count()):
            row = rows.nth(index)
            try:
                row_text = (row.text_content() or "").strip()
                link = None
                for selector in _DOC_LINK_CANDIDATES:
                    candidate = row.locator(selector).first
                    if candidate.count() > 0:
                        link = candidate
                        break
                if link is None or link.count() == 0:
                    continue
                filename = (link.text_content() or "").strip()
                href = link.get_attribute("href") or ""
            except Exception:  # noqa: BLE001 - a malformed row must not kill the course
                continue

            if not filename:
                continue
            publish_date = parse_publish_date(row_text)
            key = (filename, publish_date)
            if key in seen:
                continue
            seen.add(key)
            documents.append(
                OmnivoxDocument(
                    course_code=course.code,
                    filename=filename,
                    publish_date=publish_date,
                    ref=href,
                )
            )

        self.log.info("%s: %d documents listed", course.code, len(documents))
        return documents
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_document_parsing.py -v`
Expected: PASS, 22 tests.

- [ ] **Step 6: Verify against the live site, read-only**

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from src.common import load_credentials
from src.omnivox import OmnivoxSession
user, pw = load_credentials(Path.cwd())
with OmnivoxSession(Path("state/omnivox-profile"), screenshot_dir=Path("logs")) as s:
    s.login(user, pw)
    for course in s.list_courses():
        docs = s.list_documents(course)
        print(f"{course.code} {course.name}: {len(docs)} documents")
        for d in docs[:3]:
            print("   ", d.publish_date or "(no date)", "|", d.filename)
PY
```

Expected: every course lists documents with **non-empty ISO dates**. If dates come back empty, the date lives outside the row text — extend `list_documents` to read the specific date cell found in Step 1 and re-run. **Do not proceed to Task 12 with empty dates**: it would make every document permanently "new".

- [ ] **Step 7: Commit**

```bash
git add src/omnivox.py tests/test_document_parsing.py
git commit -m "feat(omnivox): document table parsing with stable ISO date normalisation"
```

---

## Task 12: Downloads via `expect_download`

**Files:**
- Modify: `src/omnivox.py` (append `download`)
- Test: `tests/test_download.py`

**Interfaces:**
- Consumes: `OmnivoxDocument`, `OmnivoxError` (Task 8), `open_documents_page` (Task 11).
- Produces: `OmnivoxSession.download(doc, dest) -> Path`. Task 13 calls it for every `DOWNLOAD`/`DOWNLOAD_REVISION` decision.

**Non-negotiable (spec §7 step 5):** Playwright's `expect_download` API only. **No native Save dialog is ever involved** — that is the exact failure that killed the browser-extension approach in March 2026. If a document only exposes a viewer page, extract the direct file URL from the viewer and fetch it through the session's cookies via `context.request`, which reuses the authenticated session without a browser download at all.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_download.py
from pathlib import Path

import pytest

from src.common import load_credentials
from src.omnivox import OmnivoxDocument, OmnivoxError, OmnivoxSession

REPO = Path(__file__).resolve().parents[1]


def test_download_rejects_a_document_with_no_ref(tmp_repo):
    doc = OmnivoxDocument(course_code="A", filename="f.pdf", publish_date="2026-09-03", ref="")
    with OmnivoxSession(tmp_repo / "state" / "p", screenshot_dir=tmp_repo / "logs") as s:
        with pytest.raises(OmnivoxError, match="no download reference"):
            s.download(doc, tmp_repo / "f.pdf")


@pytest.mark.live
def test_downloads_one_real_document(tmp_repo):
    """Fetches a single real file into a temp dir. Never writes to course folders."""
    user, password = load_credentials(REPO)
    with OmnivoxSession(
        REPO / "state" / "omnivox-profile", screenshot_dir=tmp_repo / "logs"
    ) as s:
        s.login(user, password)
        for course in s.list_courses():
            docs = s.list_documents(course)
            if not docs:
                continue
            dest = tmp_repo / docs[0].filename
            result = s.download(docs[0], dest)
            assert result.exists()
            assert result.stat().st_size > 0
            return
        pytest.skip("No documents available in the current session to download")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_download.py -v`
Expected: FAIL — `AttributeError: 'OmnivoxSession' object has no attribute 'download'`

- [ ] **Step 3: Append `download` to `src/omnivox.py`**

```python
    def download(self, doc: OmnivoxDocument, dest: Path) -> Path:
        """Fetch one document to `dest`.

        Strategy 1: click the row link inside expect_download (spec section 7 step 5).
        Strategy 2: if that yields a viewer page instead of a file, fetch the
        resolved URL through the authenticated context. A native Save dialog is
        never involved in either path.
        """
        if not doc.ref:
            raise OmnivoxError(
                f"Document {doc.filename!r} has no download reference; "
                "the row markup probably changed"
            )
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            with self.page.expect_download(timeout=DEFAULT_TIMEOUT_MS) as download_info:
                self.page.locator(f"a[href='{doc.ref}']").first.click()
            download = download_info.value
            download.save_as(str(dest))
        except Exception as click_error:  # noqa: BLE001 - fall through to the URL strategy
            self.log.info(
                "expect_download did not fire for %s (%s); trying direct fetch",
                doc.filename,
                click_error,
            )
            url = doc.ref if doc.ref.startswith("http") else LEA_HOME + doc.ref
            try:
                response = self._context.request.get(url, timeout=DEFAULT_TIMEOUT_MS)
                if not response.ok:
                    raise OmnivoxError(f"HTTP {response.status} fetching {doc.filename}")
                body = response.body()
            except OmnivoxError:
                self.screenshot(f"download-failed-{doc.course_code}")
                raise
            except Exception as fetch_error:  # noqa: BLE001
                self.screenshot(f"download-failed-{doc.course_code}")
                raise OmnivoxError(
                    f"Could not download {doc.filename}: {fetch_error}"
                ) from fetch_error
            if body[:15].lstrip().lower().startswith(b"<!doctype html") or body[:6].lower() == b"<html>":
                self.screenshot(f"download-got-html-{doc.course_code}")
                raise OmnivoxError(
                    f"Downloading {doc.filename} returned an HTML page, not a file. "
                    "It is probably behind a viewer; see the screenshot in logs/."
                )
            dest.write_bytes(body)

        if not dest.exists() or dest.stat().st_size == 0:
            raise OmnivoxError(f"Download of {doc.filename} produced an empty file at {dest}")
        self.log.info("Downloaded %s (%d bytes)", dest.name, dest.stat().st_size)
        return dest
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_download.py -v`
Expected: PASS, 2 tests.

- [ ] **Step 5: Confirm no Save dialog appeared**

The live test runs headless, so a native dialog would hang it rather than pop up. Re-run once headed and watch:
```bash
.venv/bin/python -m pytest tests/test_download.py -k real -v -s
```
Expected: the file lands with **no** Finder save panel. If a dialog appears, `expect_download` was not used — fix before continuing; this is the project's founding constraint.

- [ ] **Step 6: Commit**

```bash
git add src/omnivox.py tests/test_download.py
git commit -m "feat(omnivox): downloads via expect_download with authenticated-fetch fallback"
```

---

## Task 13: Sync orchestration and dry-run (fake driver, no browser)

**Files:**
- Modify: `src/omnivox_sync.py` (append `SyncResult`, `sync`)
- Modify: `tests/conftest.py` (add the `FakeDriver` fixture)
- Test: `tests/test_sync_orchestration.py`

**Interfaces:**
- Consumes: `Config`, `Course`, `StateStore` (Tasks 2–3); `classify_document`, `build_state_index` (Task 7); `needs_conversion`, `is_uploadable`, `convert_to_pdf`, `ConversionError` (Tasks 5–6); `OmnivoxDocument` (Task 8).
- Produces: `SyncResult` dataclass and `sync(cfg, driver, *, dry_run=False, only_course=None, logger=None) -> SyncResult`. Task 14 wraps it in the CLI; Task 15 consumes `result.upload_queue`.

`sync` takes a **driver object**, not a session class, so the entire pipeline runs under pytest against a fake. That is what makes spec §12's "pure logic under pytest" real for the orchestration layer, not just the leaf functions.

**Dry-run contract (spec §7 CLI flags):** report what *would* happen — **no downloads, no conversions, no state writes, no folder creation**. A dry run must be safe to execute against the live site at any moment.

- [ ] **Step 1: Add the fake driver to `tests/conftest.py`**

```python
from src.omnivox import OmnivoxCourse, OmnivoxDocument, OmnivoxError


class FakeDriver:
    """Stands in for OmnivoxSession. Records calls; writes real bytes on download."""

    def __init__(self, courses=None, documents=None, fail_on=None, content=b"filecontent"):
        self._courses = courses or []
        self._documents = documents or {}     # course_code -> list[OmnivoxDocument]
        self._fail_on = fail_on or set()      # filenames that raise on download
        self._content = content
        self.downloaded: list[tuple[str, str]] = []
        self.listed_courses = 0

    def list_courses(self):
        self.listed_courses += 1
        return list(self._courses)

    def list_documents(self, course):
        return list(self._documents.get(course.code, []))

    def download(self, doc, dest):
        if doc.filename in self._fail_on:
            raise OmnivoxError(f"simulated download failure for {doc.filename}")
        from pathlib import Path

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._content)
        self.downloaded.append((doc.course_code, dest.name))
        return dest


@pytest.fixture
def fake_driver():
    return FakeDriver


@pytest.fixture
def doc_factory():
    def _make(course="601-101-MQ", filename="ch01.pdf", publish_date="2026-09-03", ref="/r"):
        return OmnivoxDocument(
            course_code=course, filename=filename, publish_date=publish_date, ref=ref
        )

    return _make


@pytest.fixture
def course_factory():
    def _make(code="601-101-MQ", name="Écriture et littérature", href="/lea/a"):
        return OmnivoxCourse(code=code, name=name, href=href)

    return _make


@pytest.fixture
def loaded_config(write_config, tmp_repo):
    from src.common import load_config

    return load_config(write_config(), repo_root=tmp_repo)
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_sync_orchestration.py
import pytest

from src.common import StateStore
from src.omnivox_sync import sync


def _course(cfg, code="601-101-MQ"):
    return cfg.course_by_code(code)


def test_downloads_a_new_document_into_the_course_folder(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory()],
        documents={"601-101-MQ": [doc_factory(filename="ch01.pdf")]},
    )
    result = sync(cfg, driver)
    landed = cfg.base_path / "Écriture et littérature" / "ch01.pdf"
    assert landed.exists()
    assert len(result.downloaded) == 1
    assert result.downloaded[0]["filename"] == "ch01.pdf"
    assert result.downloaded[0]["converted_pdf"] is None


def test_course_folders_are_created_when_missing(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    assert not cfg.base_path.exists()
    sync(cfg, fake_driver(courses=[course_factory()],
                          documents={"601-101-MQ": [doc_factory()]}))
    assert (cfg.base_path / "Écriture et littérature").is_dir()


def test_state_is_written_and_second_run_is_a_no_op(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    make_driver = lambda: fake_driver(  # noqa: E731
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory()]}
    )
    first = sync(cfg, make_driver())
    assert len(first.downloaded) == 1

    second_driver = make_driver()
    second = sync(cfg, second_driver)
    assert second.downloaded == []
    assert second_driver.downloaded == [], "must not re-download a known document"

    records = StateStore(cfg.repo_root / "state" / "downloads.json").read()
    assert len(records) == 1


def test_only_configured_courses_are_synced(
    loaded_config, fake_driver, doc_factory, course_factory
):
    """A course visible on LEA but absent from config.yaml is ignored."""
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="999-999-ZZ", name="Not mine")],
        documents={
            "601-101-MQ": [doc_factory()],
            "999-999-ZZ": [doc_factory(course="999-999-ZZ", filename="other.pdf")],
        },
    )
    result = sync(cfg, driver)
    assert [d["course_code"] for d in result.downloaded] == ["601-101-MQ"]


def test_only_course_flag_limits_the_run(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")],
        documents={
            "601-101-MQ": [doc_factory()],
            "201-103-RE": [doc_factory(course="201-103-RE", filename="calc.pdf")],
        },
    )
    result = sync(cfg, driver, only_course="201-103-RE")
    assert [d["filename"] for d in result.downloaded] == ["calc.pdf"]


def test_convertible_file_is_converted_and_pdf_is_queued(
    loaded_config, fake_driver, doc_factory, course_factory, monkeypatch
):
    cfg = loaded_config
    calls = []

    def fake_convert(src, outdir, **kwargs):
        calls.append(src)
        pdf = outdir / (src.stem + ".pdf")
        pdf.write_bytes(b"%PDF-1.4 fake")
        return pdf

    monkeypatch.setattr("src.omnivox_sync.convert_to_pdf", fake_convert)
    driver = fake_driver(
        courses=[course_factory()],
        documents={"601-101-MQ": [doc_factory(filename="deck.pptx")]},
    )
    result = sync(cfg, driver)
    assert len(calls) == 1
    assert len(result.converted) == 1
    assert result.upload_queue[0]["path"].endswith("deck.pdf")
    assert (cfg.base_path / "Écriture et littérature" / "deck.pptx").exists(), "keep the original"
    assert result.downloaded[0]["converted_pdf"].endswith("deck.pdf")


def test_supported_file_is_queued_without_conversion(
    loaded_config, fake_driver, doc_factory, course_factory, monkeypatch
):
    cfg = loaded_config
    monkeypatch.setattr(
        "src.omnivox_sync.convert_to_pdf",
        lambda *a, **k: pytest.fail("must not convert a supported format"),
    )
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="notes.docx")]}
    )
    result = sync(cfg, driver)
    assert result.upload_queue[0]["path"].endswith("notes.docx")


def test_conversion_failure_of_an_uploadable_original_still_queues_the_original(
    loaded_config, fake_driver, doc_factory, course_factory, monkeypatch
):
    """Spec section 7 step 7: 'if the original format is NotebookLM-supported
    anyway, still queue the original for upload'. .doc is convertible but not
    uploadable; .csv is convertible and not uploadable; use .rtf-style logic by
    forcing a failure on a file that IS uploadable after all."""
    from src.convert import ConversionError

    cfg = loaded_config
    monkeypatch.setattr(
        "src.omnivox_sync.convert_to_pdf",
        lambda *a, **k: (_ for _ in ()).throw(ConversionError("soffice exploded")),
    )
    monkeypatch.setattr("src.omnivox_sync.is_uploadable", lambda path: True)
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="deck.pptx")]}
    )
    result = sync(cfg, driver)
    assert len(result.conversion_failures) == 1
    assert result.conversion_failures[0]["uploadable"] is True
    assert result.upload_queue[0]["path"].endswith("deck.pptx")


def test_conversion_failure_of_a_non_uploadable_original_skips_upload(
    loaded_config, fake_driver, doc_factory, course_factory, monkeypatch
):
    from src.convert import ConversionError

    cfg = loaded_config
    monkeypatch.setattr(
        "src.omnivox_sync.convert_to_pdf",
        lambda *a, **k: (_ for _ in ()).throw(ConversionError("soffice exploded")),
    )
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="deck.pptx")]}
    )
    result = sync(cfg, driver)
    assert result.conversion_failures[0]["uploadable"] is False
    assert result.upload_queue == []
    assert result.downloaded[0]["converted_pdf"] is None


def test_unknown_extension_is_filed_but_not_queued(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="bundle.zip")]}
    )
    result = sync(cfg, driver)
    assert (cfg.base_path / "Écriture et littérature" / "bundle.zip").exists()
    assert result.upload_queue == []


def test_dry_run_writes_absolutely_nothing(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory()]}
    )
    result = sync(cfg, driver, dry_run=True)
    assert len(result.downloaded) == 1, "dry run still reports what would happen"
    assert driver.downloaded == [], "no file was fetched"
    assert not cfg.base_path.exists(), "no folder was created"
    assert StateStore(cfg.repo_root / "state" / "downloads.json").read() == []


def test_dry_run_twice_reports_the_same_thing(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    make = lambda: fake_driver(  # noqa: E731
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory()]}
    )
    first = sync(cfg, make(), dry_run=True)
    second = sync(cfg, make(), dry_run=True)
    assert len(first.downloaded) == len(second.downloaded) == 1


def test_download_failure_records_an_error_and_continues_other_courses(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")],
        documents={
            "601-101-MQ": [doc_factory(filename="broken.pdf")],
            "201-103-RE": [doc_factory(course="201-103-RE", filename="fine.pdf")],
        },
        fail_on={"broken.pdf"},
    )
    result = sync(cfg, driver)
    assert len(result.errors) == 1
    assert [d["filename"] for d in result.downloaded] == ["fine.pdf"]


def test_failed_download_is_not_recorded_in_state(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory()],
        documents={"601-101-MQ": [doc_factory(filename="broken.pdf")]},
        fail_on={"broken.pdf"},
    )
    sync(cfg, driver)
    assert StateStore(cfg.repo_root / "state" / "downloads.json").read() == [], (
        "a failed download must stay 'new' so the next run retries it"
    )


def test_listing_failure_for_one_course_does_not_abort_the_run(
    loaded_config, fake_driver, doc_factory, course_factory
):
    from src.omnivox import ParseError

    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")],
        documents={"201-103-RE": [doc_factory(course="201-103-RE", filename="fine.pdf")]},
    )
    original = driver.list_documents

    def flaky(course):
        if course.code == "601-101-MQ":
            raise ParseError("layout changed")
        return original(course)

    driver.list_documents = flaky
    result = sync(cfg, driver)
    assert len(result.errors) == 1
    assert [d["filename"] for d in result.downloaded] == ["fine.pdf"]


def test_existing_file_without_state_is_skipped_and_recorded(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    folder = cfg.base_path / "Écriture et littérature"
    folder.mkdir(parents=True)
    (folder / "ch01.pdf").write_bytes(b"already here")

    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="ch01.pdf")]}
    )
    result = sync(cfg, driver)
    assert len(result.skipped_existing) == 1
    assert driver.downloaded == []
    assert (folder / "ch01.pdf").read_bytes() == b"already here"
    assert len(StateStore(cfg.repo_root / "state" / "downloads.json").read()) == 1
    assert result.upload_queue == [], "a file we did not fetch is not ours to upload"


def test_empty_course_list_is_not_an_error(loaded_config, fake_driver):
    result = sync(loaded_config, fake_driver(courses=[]))
    assert result.downloaded == []
    assert result.errors == []
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sync_orchestration.py -v`
Expected: FAIL — `ImportError: cannot import name 'sync'`

- [ ] **Step 4: Append `SyncResult` and `sync` to `src/omnivox_sync.py`**

Add to the imports: `import logging`, `from dataclasses import dataclass, field`, `from datetime import datetime`, `from src.common import Config, Course, StateStore`, `from src.convert import ConversionError, convert_to_pdf, is_uploadable, needs_conversion`, `from src.omnivox import OmnivoxError`.

```python
@dataclass
class SyncResult:
    downloaded: list[dict] = field(default_factory=list)
    converted: list[dict] = field(default_factory=list)
    conversion_failures: list[dict] = field(default_factory=list)
    upload_queue: list[dict] = field(default_factory=list)
    skipped_existing: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{len(self.downloaded)} downloaded"]
        if self.converted:
            parts.append(f"{len(self.converted)} converted")
        if self.conversion_failures:
            parts.append(f"{len(self.conversion_failures)} conversion failures")
        if self.skipped_existing:
            parts.append(f"{len(self.skipped_existing)} already on disk")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return ", ".join(parts)


def _handle_conversion(
    path: Path, folder: Path, result: SyncResult, dry_run: bool, log: logging.Logger
) -> Path | None:
    """Convert if needed and return the path that should be uploaded, or None."""
    if not needs_conversion(path):
        return path if is_uploadable(path) else None

    if dry_run:
        result.converted.append({"source": str(path), "pdf": str(folder / f"{path.stem}.pdf")})
        return folder / f"{path.stem}.pdf"

    try:
        pdf = convert_to_pdf(path, folder)
    except ConversionError as exc:
        uploadable = is_uploadable(path)
        log.error("Conversion failed for %s: %s", path.name, exc)
        result.conversion_failures.append(
            {"source": str(path), "error": str(exc), "uploadable": uploadable}
        )
        # Spec section 7 step 7: fall back to the original when NotebookLM accepts it.
        return path if uploadable else None

    result.converted.append({"source": str(path), "pdf": str(pdf)})
    return pdf


def _sync_course(
    cfg: Config,
    course: Course,
    driver,
    state_index: dict,
    result: SyncResult,
    new_records: list[dict],
    dry_run: bool,
    log: logging.Logger,
) -> None:
    folder = cfg.folder_for(course)
    if not dry_run:
        folder.mkdir(parents=True, exist_ok=True)

    site_course = next(
        (c for c in driver.list_courses() if c.code == course.code), None
    )
    if site_course is None:
        raise OmnivoxError(
            f"{course.code} is in config.yaml but not visible on LEA. "
            "Re-run --discover if the semester changed."
        )

    for doc in driver.list_documents(site_course):
        decision, dest = classify_document(doc, state_index, folder)

        if decision == SKIP_KNOWN:
            continue

        record = {
            "course_code": course.code,
            "filename": doc.filename,
            "publish_date": doc.publish_date,
            "downloaded_at": datetime.now().isoformat(timespec="seconds"),
            "path": str(dest),
            "converted_pdf": None,
        }

        if decision == SKIP_EXISTING:
            log.warning(
                "%s: %s already on disk but absent from state; recording without downloading",
                course.code,
                doc.filename,
            )
            result.skipped_existing.append(record)
            if not dry_run:
                new_records.append(record)
            state_index["tuples"].add(download_key(course.code, doc.filename, doc.publish_date))
            state_index["names"].add((course.code, doc.filename))
            continue

        # DOWNLOAD or DOWNLOAD_REVISION
        if dry_run:
            log.info("[dry-run] would download %s -> %s", doc.filename, dest)
            result.downloaded.append(record)
            upload_path = _handle_conversion(dest, folder, result, True, log)
            if upload_path is not None:
                result.upload_queue.append(
                    {
                        "course_code": course.code,
                        "notebook": course.notebook,
                        "path": str(upload_path),
                        "filename": Path(upload_path).name,
                    }
                )
            continue

        try:
            driver.download(doc, dest)
        except OmnivoxError as exc:
            log.error("Download failed for %s/%s: %s", course.code, doc.filename, exc)
            result.errors.append(
                {"scope": f"{course.code}/{doc.filename}", "error": str(exc), "screenshot": None}
            )
            continue  # Not recorded in state: the next run retries it.

        upload_path = _handle_conversion(dest, folder, result, False, log)
        if upload_path is not None and upload_path != dest:
            record["converted_pdf"] = str(upload_path)

        result.downloaded.append(record)
        new_records.append(record)
        state_index["tuples"].add(download_key(course.code, doc.filename, doc.publish_date))
        state_index["names"].add((course.code, doc.filename))

        if upload_path is not None:
            result.upload_queue.append(
                {
                    "course_code": course.code,
                    "notebook": course.notebook,
                    "path": str(upload_path),
                    "filename": Path(upload_path).name,
                }
            )


def sync(
    cfg: Config,
    driver,
    *,
    dry_run: bool = False,
    only_course: str | None = None,
    logger: logging.Logger | None = None,
) -> SyncResult:
    """Download every new document for every configured course.

    A failure in one course is recorded and the run continues with the rest
    (spec section 7 failure behavior).
    """
    log = logger or logging.getLogger("school.omnivox_sync")
    result = SyncResult()
    store = StateStore(cfg.repo_root / "state" / "downloads.json")
    state_index = build_state_index(store.read())
    new_records: list[dict] = []

    courses = cfg.courses
    if only_course:
        courses = [c for c in courses if c.code == only_course]
        if not courses:
            raise ConfigError(f"--course {only_course} is not in config.yaml")

    for course in courses:
        try:
            _sync_course(cfg, course, driver, state_index, result, new_records, dry_run, log)
        except Exception as exc:  # noqa: BLE001 - one bad course must not kill the run
            log.exception("Course %s failed", course.code)
            result.errors.append({"scope": course.code, "error": str(exc), "screenshot": None})

    if new_records and not dry_run:
        store.write(store.read() + new_records)

    log.info("Sync finished: %s", result.summary())
    return result
```

Add `from src.common import ConfigError` to the imports as well.

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sync_orchestration.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 6: Run the whole suite and commit**

```bash
.venv/bin/python -m pytest -m "not live"
git add src/omnivox_sync.py tests/conftest.py tests/test_sync_orchestration.py
git commit -m "feat(sync): orchestration with conversion routing, dry-run, and per-course isolation"
```

---

## Task 14: CLI entry point and loud failures

**Files:**
- Modify: `src/omnivox_sync.py` (append `build_parser`, `main`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `sync`, `render_discovery_yaml` (Tasks 10, 13); `load_config`, `load_credentials`, `notify`, `setup_logging` (Tasks 2, 4).
- Produces: `build_parser()`, `main(argv=None) -> int`. Task 16's launchd plist invokes `python -m src.omnivox_sync`.

Flags (spec §7): `--discover`, `--dry-run`, `--course CODE`, `--headed`. Plus `--config PATH` for testing.

**Exit codes:** `0` success (including "nothing new"), `1` partial failure (some courses errored), `2` fatal (config, credentials, or login failed — nothing ran).

**Loud failures (spec §2):** every non-zero exit sends a notification. A fatal error is `critical=True`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
import pytest

from src.omnivox_sync import build_parser, main


def test_parser_accepts_every_spec_flag():
    args = build_parser().parse_args(["--dry-run", "--course", "601-101-MQ", "--headed"])
    assert args.dry_run is True
    assert args.course == "601-101-MQ"
    assert args.headed is True
    assert args.discover is False


def test_discover_is_mutually_exclusive_with_course(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--discover", "--course", "X"])


def test_missing_config_exits_2_and_notifies(tmp_repo, monkeypatch):
    notes = []
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append((a, k)))
    code = main(["--config", str(tmp_repo / "absent.yaml"), "--dry-run"])
    assert code == 2
    assert notes and notes[0][1].get("critical") is True


def test_missing_env_exits_2_and_notifies(write_config, tmp_repo, monkeypatch):
    notes = []
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append((a, k)))
    code = main(["--config", str(write_config()), "--dry-run"])
    assert code == 2
    assert any("env" in str(n).lower() or "OMNIVOX" in str(n) for n in notes)


def test_successful_dry_run_exits_0(
    write_config, write_env, tmp_repo, monkeypatch, fake_driver, course_factory, doc_factory
):
    write_env()
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory()]}
    )
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)
    assert main(["--config", str(write_config()), "--dry-run"]) == 0


def test_partial_failure_exits_1_and_notifies(
    write_config, write_env, tmp_repo, monkeypatch, fake_driver, course_factory, doc_factory
):
    notes = []
    write_env()
    driver = fake_driver(
        courses=[course_factory()],
        documents={"601-101-MQ": [doc_factory(filename="broken.pdf")]},
        fail_on={"broken.pdf"},
    )
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append((a, k)))
    assert main(["--config", str(write_config())]) == 1
    assert notes


def test_nothing_new_exits_0_and_stays_silent(
    write_config, write_env, tmp_repo, monkeypatch, fake_driver, course_factory
):
    """Spec section 9: silence means 'checked, nothing new'."""
    notes = []
    write_env()
    monkeypatch.setattr(
        "src.omnivox_sync._open_session", lambda *a, **k: _ctx(fake_driver(courses=[course_factory()]))
    )
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append((a, k)))
    assert main(["--config", str(write_config())]) == 0
    assert notes == [], "no notification when there is nothing to report"


def test_discover_prints_paste_ready_yaml(
    write_config, write_env, tmp_repo, monkeypatch, capsys, fake_driver, course_factory
):
    import yaml

    write_env()
    monkeypatch.setattr(
        "src.omnivox_sync._open_session",
        lambda *a, **k: _ctx(fake_driver(courses=[course_factory()])),
    )
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)
    assert main(["--config", str(write_config()), "--discover"]) == 0
    out = capsys.readouterr().out
    assert "courses:" in out
    parsed = yaml.safe_load(out[out.index("courses:"):])
    assert parsed["courses"][0]["code"] == "601-101-MQ"


class _ctx:
    """Minimal context manager wrapping a fake driver."""

    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self.driver

    def __exit__(self, *a):
        return False
```

Move the `_ctx` helper above its first use in the file when writing it.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_parser'`

- [ ] **Step 3: Append the CLI to `src/omnivox_sync.py`**

Add imports: `import argparse`, `import sys`, `from contextlib import contextmanager`, `from src.common import load_config, load_credentials, notify, setup_logging`, `from src.omnivox import OmnivoxSession`.

```python
REPO_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _open_session(cfg, *, headed: bool, logger):
    """Open a logged-in Omnivox session. Seam for tests: patched with a fake."""
    user, password = load_credentials(cfg.repo_root)
    with OmnivoxSession(
        cfg.repo_root / "state" / "omnivox-profile",
        headed=headed,
        screenshot_dir=cfg.repo_root / "logs",
        logger=logger,
    ) as session:
        session.login(user, password)
        yield session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.omnivox_sync",
        description="Download new Omnivox LEA documents into local course folders.",
    )
    parser.add_argument(
        "--config", default=str(REPO_ROOT / "config.yaml"), help="path to config.yaml"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what would happen; no downloads, conversions, or state writes",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser (debugging)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--discover", action="store_true",
        help="print a paste-ready courses: block for config.yaml and exit",
    )
    group.add_argument("--course", metavar="CODE", help="limit the run to one course code")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    repo_root = config_path.resolve().parent
    log = setup_logging("omnivox_sync", repo_root)

    # --- fatal setup errors: nothing ran ---
    try:
        cfg = load_config(config_path, repo_root=repo_root)
        load_credentials(repo_root)  # fail fast before opening a browser
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        notify("School sync failed", str(exc), critical=True)
        return 2

    try:
        with _open_session(cfg, headed=args.headed, logger=log) as session:
            if args.discover:
                courses = session.list_courses()
                print(f"# Discovered {len(courses)} courses for {cfg.semester}.")
                print("# Paste the block below into config.yaml, replacing 'courses:'.\n")
                print(render_discovery_yaml(courses, cfg.semester))
                log.info("Discovery listed %d courses", len(courses))
                return 0

            result = sync(
                cfg, session, dry_run=args.dry_run, only_course=args.course, logger=log
            )
    except ConfigError as exc:
        log.error("%s", exc)
        notify("School sync failed", str(exc), critical=True)
        return 2
    except Exception as exc:  # noqa: BLE001 - login/browser failures are fatal for the run
        log.exception("Sync aborted")
        notify(
            "School sync failed",
            f"{type(exc).__name__}: {exc}. See logs/omnivox_sync.log and logs/*.png",
            critical=True,
        )
        return 2

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}{result.summary()}")
    for item in result.upload_queue:
        print(f"{prefix}queued for upload: {item['course_code']} / {item['filename']}")

    chain_downstream(cfg, result, dry_run=args.dry_run, logger=log)

    if result.errors:
        detail = "; ".join(f"{e['scope']}: {e['error']}" for e in result.errors[:3])
        notify("School sync: partial failure", detail, critical=True, cfg=cfg.notify)
        return 1

    if result.conversion_failures:
        names = ", ".join(Path(f["source"]).name for f in result.conversion_failures[:3])
        notify("School sync: conversion failed", names, cfg=cfg.notify)

    # Silence when nothing is new (spec section 9).
    if result.downloaded and not args.dry_run:
        courses_touched = sorted({d["course_code"] for d in result.downloaded})
        notify(
            "School sync",
            f"{len(result.downloaded)} new document(s): {', '.join(courses_touched)}",
            cfg=cfg.notify,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: PASS, 8 tests. `chain_downstream` does not exist yet — add a one-line stub `def chain_downstream(cfg, result, *, dry_run=False, logger=None): pass` and replace it properly in Task 15.

- [ ] **Step 5: Commit**

```bash
git add src/omnivox_sync.py tests/test_cli.py
git commit -m "feat(sync): CLI with dry-run, discover, course filter, and loud failures"
```

---

## Task 15: The M2/M3 seam

**Files:**
- Modify: `src/omnivox_sync.py` (replace the `chain_downstream` stub)
- Test: `tests/test_chain.py`

**Interfaces:**
- Consumes: `SyncResult` (Task 13), `StateStore` (Task 3).
- Produces: `chain_downstream(cfg, result, *, dry_run=False, logger=None) -> None`, and the persisted contract `state/upload_queue.json`. **Milestone 2 reads that file; Milestone 3 reads `SyncResult`.**

This task ships no NotebookLM or digest behavior. It ships the *contract* so M2 and M3 attach without touching M1's logic — and, critically, it makes M1 satisfy spec §2's visibility rule on its own: anything downloaded is recorded somewhere the user can see before M3 exists.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chain.py
from src.common import StateStore
from src.omnivox_sync import SyncResult, chain_downstream


def _result():
    r = SyncResult()
    r.downloaded.append(
        {"course_code": "601-101-MQ", "filename": "ch01.pdf", "publish_date": "2026-09-03",
         "downloaded_at": "2026-09-03T07:30:00", "path": "/x/ch01.pdf", "converted_pdf": None}
    )
    r.upload_queue.append(
        {"course_code": "601-101-MQ", "notebook": "Écriture - Cegep Automne 2026",
         "path": "/x/ch01.pdf", "filename": "ch01.pdf"}
    )
    return r


def test_upload_queue_is_persisted(loaded_config):
    chain_downstream(loaded_config, _result())
    queued = StateStore(loaded_config.repo_root / "state" / "upload_queue.json").read()
    assert len(queued) == 1
    assert queued[0]["notebook"] == "Écriture - Cegep Automne 2026"
    assert "queued_at" in queued[0]


def test_queue_accumulates_across_runs(loaded_config):
    chain_downstream(loaded_config, _result())
    chain_downstream(loaded_config, _result())
    queued = StateStore(loaded_config.repo_root / "state" / "upload_queue.json").read()
    assert len(queued) == 2, "M2 drains the queue; M1 only appends"


def test_dry_run_persists_nothing(loaded_config):
    chain_downstream(loaded_config, _result(), dry_run=True)
    assert StateStore(loaded_config.repo_root / "state" / "upload_queue.json").read() == []


def test_empty_result_writes_nothing(loaded_config):
    chain_downstream(loaded_config, SyncResult())
    assert StateStore(loaded_config.repo_root / "state" / "upload_queue.json").read() == []


def test_run_is_appended_to_the_digest_file(loaded_config):
    """Spec section 2: nothing the pipeline touches may become invisible.
    Until M3 exists, M1 writes its own line into _digest.md."""
    chain_downstream(loaded_config, _result())
    digest = loaded_config.base_path / "_digest.md"
    assert digest.exists()
    text = digest.read_text(encoding="utf-8")
    assert "ch01.pdf" in text
    assert "601-101-MQ" in text


def test_digest_dry_run_writes_nothing(loaded_config):
    chain_downstream(loaded_config, _result(), dry_run=True)
    assert not (loaded_config.base_path / "_digest.md").exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chain.py -v`
Expected: FAIL — the stub does nothing, so the first assertion fails.

- [ ] **Step 3: Replace the stub in `src/omnivox_sync.py`**

```python
def _append_digest_stub(cfg: Config, result: SyncResult) -> None:
    """Minimal digest line so nothing the pipeline touched is invisible.

    Milestone 3 replaces this with digest.py's full dated sections; the file
    format (dated H2 heading + bullets) is chosen now so M3 can append to the
    same document without a migration.
    """
    if not (result.downloaded or result.conversion_failures or result.errors):
        return
    cfg.base_path.mkdir(parents=True, exist_ok=True)
    digest = cfg.base_path / "_digest.md"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n## {stamp} — omnivox_sync\n"]
    for record in result.downloaded:
        converted = f" (converted to {Path(record['converted_pdf']).name})" if record["converted_pdf"] else ""
        lines.append(f"- 📄 **{record['course_code']}** {record['filename']}{converted}")
    for failure in result.conversion_failures:
        lines.append(f"- ⚠️ conversion failed: {Path(failure['source']).name} — {failure['error']}")
    for skipped in result.skipped_existing:
        lines.append(
            f"- ℹ️ already on disk, recorded without downloading: {skipped['filename']}"
        )
    for error in result.errors:
        lines.append(f"- ❌ {error['scope']}: {error['error']}")
    with digest.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def chain_downstream(
    cfg: Config,
    result: SyncResult,
    *,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
) -> None:
    """Hand this run's output to M2 (upload) and M3 (digest).

    Milestone 1 ships the contract, not the modules:
      * state/upload_queue.json is the queue M2 will drain.
      * SyncResult is the object M3 will rank and format.
    """
    log = logger or logging.getLogger("school.omnivox_sync")

    if dry_run:
        log.info("[dry-run] would queue %d file(s) for upload", len(result.upload_queue))
        return

    if result.upload_queue:
        store = StateStore(cfg.repo_root / "state" / "upload_queue.json")
        stamp = datetime.now().isoformat(timespec="seconds")
        store.write(store.read() + [dict(item, queued_at=stamp) for item in result.upload_queue])
        log.info("Queued %d file(s) in state/upload_queue.json", len(result.upload_queue))

    log.info("M2 notebooklm_upload not implemented yet (Milestone 2); queue is persisted")
    log.info("M3 digest not implemented yet (Milestone 3); writing minimal digest entry")
    _append_digest_stub(cfg, result)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chain.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Run the full offline suite and commit**

```bash
.venv/bin/python -m pytest -m "not live"
git add src/omnivox_sync.py tests/test_chain.py
git commit -m "feat(sync): persist upload queue and minimal digest entry as the M2/M3 seam"
```

---

## Task 16: `launchd` scheduling

**Files:**
- Create: `launchd/com.school.sync.plist.template`, `scripts/install_launchd.sh`
- Test: `tests/test_launchd_render.py`

**Interfaces:**
- Consumes: `main()` via `python -m src.omnivox_sync` (Task 14).
- Produces: `make install-launchd` / `make uninstall-launchd` (spec §11).

Spec §11 requirements, each of which has a test:
- `StartCalendarInterval` weekdays (Mon–Fri) at **07:30** and **12:15**.
- `StandardOutPath` / `StandardErrorPath` into `logs/`.
- **Absolute paths everywhere** — launchd has no shell environment.
- Python invoked via the repo venv's absolute path.

`PATH` is set explicitly in `EnvironmentVariables` so `find_soffice()` resolves `/opt/homebrew/bin/soffice` under launchd; without it the fallback list in Task 6 is the only thing that saves conversion.

- [ ] **Step 1: Write `launchd/com.school.sync.plist.template`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.school.sync</string>

    <key>ProgramArguments</key>
    <array>
        <string>__PYTHON__</string>
        <string>-m</string>
        <string>src.omnivox_sync</string>
    </array>

    <key>WorkingDirectory</key>
    <string>__REPO__</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>PYTHONPATH</key>
        <string>__REPO__</string>
    </dict>

    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>15</integer></dict>
    </array>

    <key>StandardOutPath</key>
    <string>__REPO__/logs/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>__REPO__/logs/launchd.err.log</string>

    <key>RunAtLoad</key>
    <false/>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_launchd_render.py
import plistlib
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "launchd" / "com.school.sync.plist.template"


def _render(tmp_path):
    text = TEMPLATE.read_text(encoding="utf-8")
    rendered = text.replace("__REPO__", str(tmp_path)).replace(
        "__PYTHON__", str(tmp_path / ".venv" / "bin" / "python")
    )
    out = tmp_path / "com.school.sync.plist"
    out.write_text(rendered, encoding="utf-8")
    return out


def test_template_exists():
    assert TEMPLATE.exists()


def test_rendered_plist_is_valid(tmp_path):
    data = plistlib.loads(_render(tmp_path).read_bytes())
    assert data["Label"] == "com.school.sync"


def test_no_placeholders_survive_rendering(tmp_path):
    text = _render(tmp_path).read_text(encoding="utf-8")
    assert "__REPO__" not in text and "__PYTHON__" not in text


def test_every_path_is_absolute(tmp_path):
    data = plistlib.loads(_render(tmp_path).read_bytes())
    assert data["ProgramArguments"][0].startswith("/")
    assert data["WorkingDirectory"].startswith("/")
    assert data["StandardOutPath"].startswith("/")
    assert data["StandardErrorPath"].startswith("/")


def test_runs_the_sync_module(tmp_path):
    data = plistlib.loads(_render(tmp_path).read_bytes())
    assert data["ProgramArguments"][1:] == ["-m", "src.omnivox_sync"]


def test_schedule_is_weekdays_at_0730_and_1215(tmp_path):
    data = plistlib.loads(_render(tmp_path).read_bytes())
    entries = {(e["Weekday"], e["Hour"], e["Minute"]) for e in data["StartCalendarInterval"]}
    expected = {(d, 7, 30) for d in range(1, 6)} | {(d, 12, 15) for d in range(1, 6)}
    assert entries == expected


def test_logs_go_into_the_repo_logs_dir(tmp_path):
    data = plistlib.loads(_render(tmp_path).read_bytes())
    assert data["StandardOutPath"].endswith("/logs/launchd.out.log")
    assert data["StandardErrorPath"].endswith("/logs/launchd.err.log")


def test_homebrew_bin_is_on_path_so_soffice_resolves(tmp_path):
    data = plistlib.loads(_render(tmp_path).read_bytes())
    assert "/opt/homebrew/bin" in data["EnvironmentVariables"]["PATH"]


def test_does_not_run_at_load(tmp_path):
    """Loading the job must not immediately fire a sync."""
    data = plistlib.loads(_render(tmp_path).read_bytes())
    assert data["RunAtLoad"] is False


def test_install_script_is_executable():
    script = REPO / "scripts" / "install_launchd.sh"
    assert script.exists()
    assert script.stat().st_mode & 0o111, "install_launchd.sh must be chmod +x"


def test_install_script_passes_shellcheck_syntax():
    script = REPO / "scripts" / "install_launchd.sh"
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_launchd_render.py -v`
Expected: FAIL — `scripts/install_launchd.sh` does not exist.

- [ ] **Step 4: Write `scripts/install_launchd.sh`**

```bash
#!/usr/bin/env bash
# Render and (un)install the com.school.sync launchd job.
# Absolute paths only: launchd jobs run with no shell environment.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.school.sync"
TEMPLATE="$REPO/launchd/$LABEL.plist.template"
RENDERED="$REPO/launchd/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$REPO/.venv/bin/python"
DOMAIN="gui/$(id -u)"

usage() { echo "usage: $0 {install|uninstall|status}" >&2; exit 64; }

install_job() {
  [ -x "$PYTHON" ] || { echo "No venv at $PYTHON. Run: make venv" >&2; exit 1; }
  [ -f "$TEMPLATE" ] || { echo "Missing template: $TEMPLATE" >&2; exit 1; }

  mkdir -p "$REPO/logs" "$HOME/Library/LaunchAgents"
  sed -e "s|__REPO__|$REPO|g" -e "s|__PYTHON__|$PYTHON|g" "$TEMPLATE" > "$RENDERED"
  plutil -lint "$RENDERED" >/dev/null
  cp "$RENDERED" "$TARGET"

  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$TARGET"
  launchctl enable "$DOMAIN/$LABEL"
  echo "Installed $LABEL (weekdays 07:30 and 12:15)."
  echo "Run it once now with: launchctl kickstart -p $DOMAIN/$LABEL"
}

uninstall_job() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$TARGET" "$RENDERED"
  echo "Uninstalled $LABEL."
}

case "${1:-}" in
  install)   install_job ;;
  uninstall) uninstall_job ;;
  status)    launchctl print "$DOMAIN/$LABEL" 2>/dev/null || echo "$LABEL is not loaded." ;;
  *)         usage ;;
esac
```

Then: `chmod +x scripts/install_launchd.sh`

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_launchd_render.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 6: Install for real and fire it once**

```bash
make install-launchd
launchctl print "gui/$(id -u)/com.school.sync" | head -20
launchctl kickstart -p "gui/$(id -u)/com.school.sync"
sleep 45 && tail -30 logs/omnivox_sync.log logs/launchd.err.log
```

Expected: the job is listed, the kickstart produces a real sync run in `logs/omnivox_sync.log`, and `launchd.err.log` is empty. A non-empty `launchd.err.log` almost always means a missing absolute path or `soffice` not being found — both are covered by the template, so read the error before changing anything.

- [ ] **Step 7: Commit**

```bash
git add launchd/com.school.sync.plist.template scripts/install_launchd.sh tests/test_launchd_render.py
git commit -m "feat(launchd): weekday 07:30 and 12:15 sync job with install script"
```

---

## Task 17: End-to-end verification and README

**Files:**
- Create: `README.md`
- Modify: `config.yaml` (paste the real `--discover` output)

**Interfaces:**
- Consumes: everything above.
- Produces: a verified, documented, scheduled system. This is the milestone deliverable.

- [ ] **Step 1: Full offline suite must be green**

Run: `.venv/bin/python -m pytest -m "not live" -v`
Expected: PASS, roughly 130 tests, zero failures. Do not continue past a red suite.

- [ ] **Step 2: Discover the real semester and fill `config.yaml`**

```bash
.venv/bin/python -m src.omnivox_sync --discover
```

Paste the printed `courses:` block into `config.yaml`, replacing `courses: []`. Adjust `folder:` values to taste, then verify the file still loads:

```bash
.venv/bin/python -c "
from pathlib import Path
from src.common import load_config
cfg = load_config(Path('config.yaml'), repo_root=Path('.'))
print(cfg.semester, '->', cfg.base_path)
for c in cfg.courses: print(' ', c.code, '|', c.folder, '|', c.notebook)
"
```

**If the fall course list is not published yet** (this plan is written 2026-08-06, before the semester starts): `--discover` will print an empty list or `ParseError`. That is expected. Use LEA's session selector to switch to **Winter 2026** by hand and run `--discover` against it to exercise the whole path with real data, keeping `config.yaml` on the winter courses until fall appears. Everything from Step 3 on works identically.

- [ ] **Step 3: Dry run against the live site — the safe rehearsal**

```bash
.venv/bin/python -m src.omnivox_sync --dry-run
```

Expected: a per-file report of what would be downloaded and converted. Verify by hand:
- Every course in `config.yaml` appears.
- Filenames and dates look right; **no date is blank**.
- Nothing was created: `git status` clean, `ls state/` shows no `downloads.json`, no new course folders.

```bash
test ! -f state/downloads.json && echo "OK: dry run wrote no state"
```

- [ ] **Step 4: Dry run one course, headed, to watch it work**

```bash
.venv/bin/python -m src.omnivox_sync --dry-run --course <A-REAL-CODE> --headed
```

Expected: the browser opens, navigates from the homepage, reaches the documents page, and closes. No native save dialog at any point.

- [ ] **Step 5: The supervised live run**

```bash
.venv/bin/python -m src.omnivox_sync --course <A-REAL-CODE>
```

Verify all of the following:
- Files landed in `{base_path}/{folder}/` with original filenames.
- Any `.pptx`/`.xlsx` has a sibling `.pdf`, **and the original is still there**.
- `state/downloads.json` has one record per file, with non-empty `publish_date`.
- `state/upload_queue.json` lists the upload targets.
- `{base_path}/_digest.md` has a dated section listing everything.
- A macOS notification appeared.

- [ ] **Step 6: Idempotence — the most important check in the milestone**

```bash
.venv/bin/python -m src.omnivox_sync --course <SAME-CODE>
```

Expected: `0 downloaded`, **no notification**, no new files, `state/downloads.json` unchanged. If anything re-downloads, the `publish_date` is unstable — go back to Task 11 Step 6. A sync that re-downloads on every run would re-upload the whole semester to NotebookLM in Milestone 2.

- [ ] **Step 7: Full live run across every course**

```bash
.venv/bin/python -m src.omnivox_sync
```

Then run it a second time and confirm it reports `0 downloaded`.

- [ ] **Step 8: Write `README.md`**

````markdown
# school-automation

Automates the repetitive parts of a cégep workflow. Milestone 1 (Omnivox sync) is
implemented; Milestones 2–4 are specified in
`docs/superpowers/specs/2026-08-06-school-automation-design.md`.

## What Milestone 1 does

Weekdays at 07:30 and 12:15, a `launchd` job logs into Omnivox LEA, downloads every
document it has not seen before into the matching local course folder, converts
Office formats to PDF, and records what it did in `state/` and `_digest.md`.

## Setup

```bash
make venv
cp .env.example .env        # then fill in OMNIVOX_USER / OMNIVOX_PASS
.venv/bin/python -m src.omnivox_sync --discover   # paste output into config.yaml
make dry-run                # rehearse: no downloads, no state writes
make sync                   # the real thing
make install-launchd        # schedule it
```

## Commands

| Command | What it does |
|---|---|
| `make dry-run` | Report what would happen. Writes nothing. Safe any time. |
| `make sync` | Download, convert, record. |
| `make discover` | Print a paste-ready `courses:` block for a new semester. |
| `make test` | Full suite (includes live tests; needs `.env`). |
| `make test-unit` | Offline tests only. |
| `make install-launchd` / `make uninstall-launchd` | Schedule / unschedule. |

Flags: `--dry-run`, `--course CODE`, `--headed`, `--discover`.

## Each semester

1. `make discover`, paste the block into `config.yaml`.
2. Update `semester:` and `base_path:`.
3. Create the matching NotebookLM notebooks by hand (automated creation is out of
   scope for v1).
4. `make dry-run` to confirm, then `make sync`.

## When it breaks

Failures notify and log. Start with:

```bash
tail -50 logs/omnivox_sync.log
ls -lt logs/*.png | head        # failure screenshots
```

A layout change on Omnivox shows up as `ParseError` plus a screenshot. The selector
candidate lists at the top of `src/omnivox.py` are the place to fix it.

## Files

- `config.yaml` — semester config. The only file that changes between semesters.
- `.env` — credentials. Never committed.
- `state/downloads.json` — every document ever downloaded, keyed by
  `(course_code, filename, publish_date)`. Deleting it is safe: the on-disk
  guard prevents re-downloading files already present.
- `state/upload_queue.json` — files waiting for Milestone 2.
- `{base_path}/_digest.md` — human-readable log of everything the pipeline touched.
````

- [ ] **Step 9: Confirm no secret ever entered the repo**

```bash
git log --all --numstat --pretty=format: | grep -c '\.env$'          # expect 0
git grep -nE "OMNIVOX_PASS *= *.+" -- . ':!*.example' ':!docs'       # expect no output
```

- [ ] **Step 10: Final commit**

```bash
git add README.md config.yaml
git commit -m "docs: README and verified semester config for Milestone 1"
```

---

## Self-Review

**1. Spec coverage.**

| Spec section | Covered by |
|---|---|
| §4 Repository layout | Task 1 (+ 3 documented deviations, listed in Scope) |
| §5.1 `config.yaml` schema | Tasks 1, 2 |
| §5.2 `.env` | Tasks 1, 4, 9 |
| §5.3 Semester bootstrap / `--discover` | Tasks 10, 14, 17 |
| §6 `load_config` | Task 2 |
| §6 State store, atomic writes | Task 3 |
| §6 `notify()` | Task 4 |
| §6 Logging, rotation | Task 4 |
| §7 step 1 Persistent context | Task 8 |
| §7 step 2 Login, homepage-first | Task 9 |
| §7 step 3 Per-course document pages | Task 11 |
| §7 step 4 New-file tuple + secondary guard | Task 7 |
| §7 step 5 `expect_download`, viewer fallback | Task 12 |
| §7 step 6 Save path + state record | Task 13 |
| §7 step 7 Conversion + failure fallbacks | Tasks 5, 6, 13 |
| §7 step 8 Chain M2/M3 | Task 15 |
| §7 CLI flags | Task 14 |
| §7 Failure behavior (notify + screenshot + continue) | Tasks 8, 13, 14 |
| §11 launchd | Task 16 |
| §12 Testing approach | every task; live runs in Task 17 |

**Deliberate gaps, each argued in place rather than left silent:**
- §7's "while on the site, also collect for M3" is deferred to Milestone 3 per §16. Seam in Task 15.
- §8, §9, §10, §13, §14, §15 are Milestones 2–4 and out of scope by the user's instruction.

**2. Placeholder scan.** No `TBD`, no "add error handling", no "similar to Task N". Every code step carries runnable code. The one genuinely unknown input — Omnivox's DOM — is handled by explicit discovery steps (Tasks 9.1, 10.1, 11.1) that produce the selectors, with hypothesis-plus-fallback lists rather than invented certainties.

**3. Type consistency.** `classify_document` returns `(decision, Path)` in Task 7 and is destructured that way in Task 13. `convert_to_pdf(src, outdir)` matches its call site and its monkeypatch signature. `SyncResult` field names used in Tasks 14 and 15 match the Task 13 definition. `chain_downstream(cfg, result, *, dry_run, logger)` is stubbed in Task 14 with the exact signature Task 15 implements. `download_key` is used identically in Tasks 7 and 13. `StateStore.read/write/append` match across Tasks 3, 13, 15.

**4. Fixture dependencies.** `loaded_config` (Task 13) is used in Task 15's tests; both live in `conftest.py`. `fake_driver`, `doc_factory`, `course_factory` are added in Task 13 Step 1 and reused in Task 14 — Task 14 must be implemented after Task 13.

---

## Live Findings (2026-08-06) — what the real site changed

The plan's selectors were explicitly hypotheses pending the discovery steps in
Tasks 9.1 / 10.1 / 11.1. Those ran against the live Cégep Édouard-Montpetit
Omnivox. What follows is what actually differed, recorded so Milestones 2–4
start from reality rather than from the guesses above.

### 1. Omnivox requires identity validation (MFA) — not anticipated by the spec

Login succeeds, then redirects to `…/apps/mfa/login/validate-method/…`
("Validation d'identité"): a 6-digit code emailed to the user, plus a
**"J'utilise un appareil de confiance"** checkbox.

Handled as a first-class path, not a workaround:
- `MfaRequired(LoginError)` is raised so it never masquerades as a bad password.
- `python -m src.omnivox_sync --login` opens a **headed** browser; the user
  enters the code and ticks the trusted-device box. The program never reads,
  requests, stores, or types the code.
- The resulting trust cookie lives in `state/omnivox-profile/`, which headless
  scheduled runs already reuse. Verified: after `--login`, a fully headless run
  authenticates with no challenge.

**Open question — durability.** During this session the trust was revoked once
after rapid testing across *copied* browser profiles (two profiles, same
account, seconds apart, which plausibly reads as a new device). A single stable
profile was not observed to lose trust on its own, but the real TTL is unknown.
If the scheduled job starts failing, the fix is one `--login`. Never copy
`state/omnivox-profile/`; a copy is not portable and provokes re-challenge.

### 2. Navigating to the LEA host directly ends the session

`page.goto("https://cegepmontpetit-lea.omnivox.ca")` lands on
`…/Login/Account/Login?isLogout=1` — it logs the user out. LEA must be entered
by clicking the **"Léa"** link on `…/intr/`, which passes through a Skytech
redirector that mints the LEA session. This is the concrete form of the spec's
"never reuse deep links" rule, and it is now `OmnivoxSession.goto_lea()`.

### 3. Course list is a card grid, not a table

`div.card-panel` → `.card-panel-title` containing `"CODE NAME"`. Course titles
are **ALL CAPS** and long ones are truncated server-side with an ellipsis.
`humanize_course_name()` converts to French sentence case, which reproduces the
user's existing folder names exactly (`ÉCRITURE ET LITTÉRATURE` →
`Écriture et littérature`), and strips the trailing ellipsis.

### 4. Documents need two hops, and two parsing fixes

Navigation: card's "Documents et vidéos" → a **shared** summary page listing all
courses → the course's own `ListeDocuments.aspx`.

Two bugs the unit tests could not have caught, both of which would have been
severe in production:

- **Dates are abbreviated and glued**: `"depuis le4 fév 2026"`. The original
  parser knew only full month names and required a word boundary before the
  day, so it returned `""` for *every* document. An empty `publish_date` makes
  every document permanently new — the sync would have re-downloaded the entire
  semester on every run, and (from Milestone 2) re-uploaded it to NotebookLM.
  Fixed: all 12 abbreviations, optional trailing period, no leading `\b`.
- **Filenames are truncated in the visible cell** (`ch01_presentation...`).
  The untruncated name is the file-type icon's `title` attribute
  (`ch01_presentationVE.pptx`). Parsing now reads that.

Also captured per row: the teacher's `description` ("Chapitre 1") and LEA's
stable `IDDocCoursDocument` GUID (recorded in state for future use; the spec's
`(course, filename, publish_date)` tuple remains the identity).

### 5. Teachers distribute web links, not only files

Rows carrying the `lienExterne_petit.png` icon are external links (a Microsoft
Forms link in Économie). Downloading one returns an HTML page. Because failed
downloads are deliberately not recorded in state so they retry, such a row
would have failed and re-notified **every run, forever**.

`OmnivoxDocument.is_link` marks them. They are recorded in state (so they
report exactly once) and surfaced in the digest as `🔗`, but never fetched —
satisfying spec §2 without generating recurring noise.

### 6. Verified end to end against real data

- 7 courses, **165 documents parsed, 0 with an unparseable date**.
- Secondary guard against the user's real `Cegep Winter 2026/L'entreprise`
  folder: 15 files correctly recognised as already on disk, 7 new, 6 to convert
  — dry-run wrote nothing (no state, no folders, no digest).
- Real run into a scratch folder: 22 downloaded, 11 converted to PDF, every PDF
  valid (`%PDF-` header), no zero-byte files, originals kept beside the PDFs.
- **Idempotence: second run downloaded 0**, no new files, state unchanged, no
  second digest section, no notification.

### 7. Still outstanding

- Fall 2026 courses are not published yet, so `config.yaml` ships with
  `courses: []`. Run `make discover` once the semester appears; the sync exits
  cleanly and silently until then.
- `base_path` remains the spec's `~/Documents/School/Cegep Automne 2026`, while
  the user's existing folders use English season names (`Cegep Winter 2026`).
  One-line change in `config.yaml` if they prefer consistency.
