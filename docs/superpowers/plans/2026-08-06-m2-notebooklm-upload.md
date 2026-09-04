# Milestone 2: NotebookLM Upload — Implementation Plan

> **For agentic workers:** implement task-by-task; steps use checkbox (`- [ ]`) syntax.

**Goal:** Drain `state/upload_queue.json` into the matching NotebookLM notebook per course, with a staging fallback that keeps the pipeline alive when the unofficial API breaks.

**Architecture:** `src/notebooklm_upload.py` wraps the `nlm` CLI behind a small typed interface, exactly as `omnivox.py` wraps Playwright — so all queue/dedup/fallback logic is testable against a fake uploader with no network. M1 already writes the queue; this milestone only consumes it.

**Tech Stack:** `notebooklm-mcp-cli` v0.9.7 (`nlm`), installed via `uv tool install`, Python 3.14, pytest.

---

## Verified facts (live, 2026-08-06)

These replace the spec's open questions in §8. **The Drive/rclone pivot is not needed.**

- `nlm` v0.9.7 installed at `~/.local/bin/nlm`; authenticated as `<your-google-account>`.
- **Local file upload is supported**: `nlm source add <notebook_id> --file <path> --wait`.
- `nlm notebook list --json` returns `[{id, title, source_count, updated_at}]`.
- `nlm login --check` reports auth validity — used as a preflight.
- **`source add` takes a notebook _id_, not a title**, so the config's `notebook:` name must be resolved to an id at runtime.
- **Existing notebook titles already match the spec's convention**, e.g.
  `"Écriture et littérature - Cegep Winter 2026"`.
- **Some titles carry a leading space** (`" L'entreprise - Cegep Winter 2026"`).
  Name matching must normalise whitespace and case, or every match fails.

## Global Constraints

- **Never upload the same `(course_code, filename)` twice** — `state/uploads.json` (spec §8 dedup).
- **200 MB per source file**; skip larger with a digest flag (spec §8).
- **Any `auto` failure falls back to staging for that run** — a Google-side breakage must never block the pipeline (spec §8).
- **Staging is always available** regardless of mode: move pending files to `{base_path}/{folder}/_to_upload/` and send one notification.
- **Notebook auto-creation is out of scope.** Missing notebook → notify, stage, continue.
- **`--dry-run` writes nothing**: no uploads, no state, no file moves.
- The queue is drained only on success; a failed item stays queued for the next run.

---

## File Structure

```
src/notebooklm_upload.py     # M2: uploader interface, queue drain, staging fallback, CLI
tests/test_notebooklm.py     # pure logic against a fake uploader
tests/test_nlm_live.py       # marked `live`: real nlm calls
```

## Interface Reference

```python
# src/notebooklm_upload.py
MAX_SOURCE_BYTES = 200 * 1024 * 1024

class UploadError(Exception): ...
class NotebookNotFound(UploadError): ...
class AuthError(UploadError): ...

@dataclass(frozen=True)
class Notebook:
    id: str; title: str

def normalize_title(title: str) -> str          # casefold + collapse whitespace

class NlmUploader:                               # the real CLI wrapper
    def __init__(self, binary: str | None = None, timeout: int = 900) -> None
    def check_auth(self) -> bool
    def list_notebooks(self) -> list[Notebook]
    def add_file(self, notebook_id: str, path: Path) -> None

def find_notebook(notebooks: list[Notebook], wanted: str) -> Notebook | None
def stage_file(path: Path, course_folder: Path) -> Path          # -> _to_upload/<name>
def upload_queue(cfg, uploader, *, dry_run=False, logger=None) -> UploadResult

@dataclass
class UploadResult:
    uploaded: list[dict]; staged: list[dict]; skipped_oversize: list[dict]
    already_done: list[dict]; errors: list[dict]
    def summary(self) -> str
```

**`state/uploads.json` record:** `{"course_code", "filename", "notebook", "notebook_id", "uploaded_at", "mode"}` where `mode` is `"auto"` or `"staging"`.

---

## Task 1: Uploader wrapper and notebook resolution

**Files:** create `src/notebooklm_upload.py`, `tests/test_notebooklm.py`

- [ ] **Step 1: Failing tests for `normalize_title` / `find_notebook`**

```python
from src.notebooklm_upload import Notebook, find_notebook, normalize_title

NBS = [
    Notebook(id="1", title=" L'entreprise - Cegep Winter 2026"),      # leading space, real
    Notebook(id="2", title="Écriture et littérature - Cegep Winter 2026"),
]

def test_leading_space_in_title_still_matches():
    assert find_notebook(NBS, "L'entreprise - Cegep Winter 2026").id == "1"

def test_match_is_case_insensitive_and_whitespace_tolerant():
    assert find_notebook(NBS, "  écriture ET littérature -  Cegep Winter 2026 ").id == "2"

def test_unknown_notebook_returns_none():
    assert find_notebook(NBS, "Physique - Cegep Automne 2026") is None

def test_normalize_collapses_internal_whitespace():
    assert normalize_title("a   b\tc") == normalize_title("a b c")

def test_empty_notebook_list_returns_none():
    assert find_notebook([], "anything") is None
```

- [ ] **Step 2: Implement**

```python
def normalize_title(title: str) -> str:
    return " ".join((title or "").split()).casefold()


def find_notebook(notebooks, wanted):
    target = normalize_title(wanted)
    return next((n for n in notebooks if normalize_title(n.title) == target), None)
```

- [ ] **Step 3: `NlmUploader`** — subprocess wrapper. `list_notebooks` runs
`nlm notebook list --json` and parses; `add_file` runs
`nlm source add <id> --file <path> --wait`; non-zero exit raises `UploadError`
carrying stderr. `check_auth` runs `nlm login --check`. Binary resolved from
PATH then `~/.local/bin/nlm` (launchd has no shell PATH — same problem as
`soffice` in M1).

- [ ] **Step 4: Commit**

---

## Task 2: Queue drain, dedup, oversize, staging fallback

**Files:** modify `src/notebooklm_upload.py`, `tests/test_notebooklm.py`

- [ ] **Step 1: Failing tests** (fake uploader; no network)

Cases to cover:
- a queued file uploads and is recorded in `uploads.json`
- the same `(course, filename)` is never uploaded twice
- a >200 MB file is skipped with a flag, not uploaded
- a missing notebook → staged + error recorded, run continues
- an `nlm` failure on one file → that file staged, other files still upload
- `mode: staging` never calls the uploader at all
- `--dry-run` uploads nothing, stages nothing, writes no state
- a successfully uploaded item is removed from `upload_queue.json`
- a failed item stays in `upload_queue.json` for the next run
- staging moves the file into `_to_upload/` and does not lose it
- a queued file that no longer exists on disk → error, not a crash

- [ ] **Step 2: Implement `upload_queue`**

Order per item: dedup check → existence check → size check → resolve notebook →
upload → record → drop from queue. Any failure routes to `stage_file` and
records an error. One notification at the end summarising staged files.

- [ ] **Step 3: Commit**

---

## Task 3: CLI and M1 chaining

**Files:** modify `src/notebooklm_upload.py`, `src/omnivox_sync.py`

- [ ] **Step 1:** `--list-notebooks`, `--dry-run`, `--config`, `--course`.
Exit codes match M1: 0 ok, 1 partial, 2 fatal.
- [ ] **Step 2:** Replace M1's `chain_downstream` stub log line with a real call
into `upload_queue`, guarded so an M2 failure never fails the sync run.
- [ ] **Step 3:** Tests for the chain seam; commit.

---

## Task 4: Live verification

- [ ] `nlm login --check` passes.
- [ ] `--list-notebooks` prints the 12 real notebooks with ids.
- [ ] Upload one real file to a real notebook; confirm `source_count` increments.
- [ ] Re-run: the same file is **not** uploaded again (dedup).
- [ ] Force a failure (bad notebook name): file is staged, run exits 1, notification fires.
