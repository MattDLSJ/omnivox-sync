"""Milestone 2: push new course documents into NotebookLM.

Wraps the unofficial `nlm` CLI (notebooklm-mcp-cli) behind a small typed
interface, so the queue/dedup/fallback logic is testable without a network.

Design rule from the spec: an upload failure must never block the pipeline.
Anything that cannot be uploaded is *staged* into `<course>/_to_upload/` and
reported, so the user can drag it in by hand.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.common import (
    Config,
    ConfigError,
    StateStore,
    human_courses,
    load_config,
    notify,
    setup_logging,
    RunBusy,
    run_lock,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Spec section 8: NotebookLM rejects sources above 200 MB.
MAX_SOURCE_BYTES = 200 * 1024 * 1024
STAGING_DIRNAME = "_to_upload"

# launchd runs with no shell PATH, same problem soffice has in M1.
_NLM_FALLBACKS = (
    "~/.local/bin/nlm",
    "/opt/homebrew/bin/nlm",
    "/usr/local/bin/nlm",
)


class UploadError(Exception):
    """Any failure that should route a file to staging."""


class NotebookNotFound(UploadError):
    """The configured notebook name does not exist in NotebookLM."""


class AuthError(UploadError):
    """The nlm CLI is not authenticated."""


@dataclass(frozen=True)
class Notebook:
    id: str
    title: str


@dataclass
class UploadResult:
    uploaded: list[dict] = field(default_factory=list)
    staged: list[dict] = field(default_factory=list)
    skipped_oversize: list[dict] = field(default_factory=list)
    already_done: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{len(self.uploaded)} uploaded"]
        if self.staged:
            parts.append(f"{len(self.staged)} staged")
        if self.skipped_oversize:
            parts.append(f"{len(self.skipped_oversize)} too large")
        if self.already_done:
            parts.append(f"{len(self.already_done)} already done")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return ", ".join(parts)


# --------------------------------------------------------------------------
# Notebook name resolution (pure)
# --------------------------------------------------------------------------


def normalize_title(title: str) -> str:
    """Collapse whitespace and case for comparison.

    Real NotebookLM titles carry stray leading spaces
    (" L'entreprise - Cegep Winter 2026"), so exact matching fails.
    """
    return " ".join((title or "").split()).casefold()


def find_notebook(notebooks: list[Notebook], wanted: str) -> Notebook | None:
    target = normalize_title(wanted)
    return next((n for n in notebooks if normalize_title(n.title) == target), None)


def upload_key(course_code: str, filename: str) -> tuple[str, str]:
    """Spec section 8 dedup identity."""
    return (course_code, filename)


def build_upload_index(records: list[dict]) -> set[tuple[str, str]]:
    """Which (course, filename) pairs are genuinely in NotebookLM already.

    A staged record is NOT one of them. Staging means the upload failed and the
    file was copied into <course>/_to_upload/ for him to drag in by hand, so
    counting it as done means the retry never happens and the file never
    arrives. Tour_de_taille.png hit exactly that on 2026-09-02: NotebookLM timed
    out processing it, the failure was recorded as mode "staging", and every
    later run then answered "already done" and skipped it.

    The whole point of staging is that the work is still outstanding.
    """
    return {
        upload_key(r["course_code"], r["filename"])
        for r in records
        if r.get("course_code") and r.get("filename")
        and r.get("mode") != "staging"
    }


# --------------------------------------------------------------------------
# The nlm CLI wrapper
# --------------------------------------------------------------------------


class NlmUploader:
    """Thin subprocess wrapper around the `nlm` CLI."""

    def __init__(self, binary: str | None = None, timeout: int = 900) -> None:
        self._binary = binary
        self.timeout = timeout

    @property
    def binary(self) -> str:
        if self._binary:
            return self._binary
        found = shutil.which("nlm")
        if found:
            self._binary = found
            return found
        for candidate in _NLM_FALLBACKS:
            path = Path(candidate).expanduser()
            if path.exists():
                self._binary = str(path)
                return self._binary
        raise UploadError(
            "The `nlm` CLI was not found. Install it with:\n"
            "    uv tool install git+https://github.com/jacob-bd/notebooklm-mcp-cli.git"
        )

    def _run(self, args: list[str], *, timeout: int | None = None):
        try:
            return subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                # Notebooks are matched BY NAME, and the names carry accents.
                # Without this, a French Windows decodes as cp1252, the match
                # fails, and every file silently goes to staging instead.
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise UploadError(f"`nlm {' '.join(args)}` timed out") from exc
        except OSError as exc:
            raise UploadError(f"Could not run `nlm`: {exc}") from exc

    def check_auth(self) -> bool:
        proc = self._run(["login", "--check"], timeout=120)
        return proc.returncode == 0 and "valid" in (proc.stdout + proc.stderr).lower()

    def list_notebooks(self) -> list[Notebook]:
        proc = self._run(["notebook", "list", "--json"], timeout=180)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:300]
            if "auth" in detail.lower() or "login" in detail.lower():
                raise AuthError(f"nlm is not authenticated: {detail}. Run: nlm login")
            raise UploadError(f"`nlm notebook list` failed: {detail}")
        try:
            raw = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise UploadError(f"Could not parse `nlm notebook list` output: {exc}") from exc
        return [
            Notebook(id=str(item["id"]), title=str(item.get("title", "")))
            for item in raw
            if item.get("id")
        ]

    def list_sources(self, notebook_id: str) -> list[tuple[str, str]]:
        """(id, title) for every source in a notebook."""
        proc = self._run(["source", "list", notebook_id, "--json"], timeout=180)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:400]
            raise UploadError(f"`nlm source list` failed: {detail}")
        try:
            return [(s["id"], s.get("title", "")) for s in json.loads(proc.stdout or "[]")]
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            raise UploadError(f"Could not parse `nlm source list` output: {exc}") from exc

    def replace_file(self, notebook_id: str, path: Path) -> None:
        """Add `path`, first removing any source that already has its name.

        For a file that is regenerated every run, `_horaire.md`, adding without
        removing would leave the notebook holding one stale copy per sync and
        answering questions from whichever it happened to read. The delete goes
        first on purpose: ending up with none is recoverable on the next run,
        ending up with two contradicting each other is not obvious at all.
        """
        stale = [sid for sid, title in self.list_sources(notebook_id)
                 if title.strip() == path.name]
        if stale:
            proc = self._run(["source", "delete", *stale, "--confirm"], timeout=300)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()[:400]
                raise UploadError(f"`nlm source delete` failed: {detail}")
        self.add_file(notebook_id, path)

    def add_youtube(self, notebook_id: str, url: str, title: str = "") -> None:
        """Add a YouTube video as a source.

        NotebookLM ingests YouTube directly, transcript and all, so a video the
        teacher posted on LEA goes in as a first-class source rather than being
        dropped for not being a file. `--wait` is deliberate: a video takes far
        longer to process than a PDF and a queue entry that returns before
        NotebookLM is finished looks successful while the notebook is empty.
        """
        args = ["source", "add", notebook_id, "--youtube", url, "--wait"]
        if title:
            args += ["--title", title]
        proc = self._run(args)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:400]
            raise UploadError(f"Adding the video {url} failed: {detail}")

    def add_file(self, notebook_id: str, path: Path) -> None:
        proc = self._run(["source", "add", notebook_id, "--file", str(path), "--wait"])
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:400]
            raise UploadError(f"Upload of {path.name} failed: {detail}")


# --------------------------------------------------------------------------
# Staging fallback
# --------------------------------------------------------------------------


def stage_file(path: Path, course_folder: Path) -> Path:
    """Copy a file into <course>/_to_upload/ for manual drag-and-drop.

    Copies rather than moves: the file belongs in the course folder, and losing
    it from there to fix an upload problem would be a regression.
    """
    staging = Path(course_folder) / STAGING_DIRNAME
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / Path(path).name
    if not target.exists():
        shutil.copy2(path, target)
    return target


# --------------------------------------------------------------------------
# Queue drain
# --------------------------------------------------------------------------


def upload_queue(
    cfg: Config,
    uploader,
    *,
    dry_run: bool = False,
    only_course: str | None = None,
    logger: logging.Logger | None = None,
) -> UploadResult:
    """Drain state/upload_queue.json into NotebookLM.

    Successful items leave the queue; failed items stay for the next run.

    Two locks, deliberately separate:

    * `upload.lock` is held for the whole drain and stops a second drain
      starting. Without it, `make upload` during a sync means both read the
      same queue and both upload every item, which shows up as duplicate
      sources in NotebookLM.
    * `queue.lock` is taken only around the final rewrite, for milliseconds.
      The drain must NOT hold it while uploading: `nlm source add --wait` can
      take minutes per file, and the recorder appends a transcript through the
      same lock. Blocking a recorder tick risks missing the start of a class.

    Order is always upload.lock then queue.lock, never the reverse.
    """
    log = logger or logging.getLogger("school.notebooklm_upload")
    result = UploadResult()

    try:
        lock = run_lock(cfg.repo_root / "state" / "upload.lock", wait=0)
        lock.__enter__()
    except RunBusy:
        log.info("Another upload is already draining the queue; skipping this one")
        return result
    try:
        return _drain(cfg, uploader, dry_run, only_course, log, result)
    finally:
        lock.__exit__(None, None, None)


def _drain(cfg, uploader, dry_run, only_course, log, result) -> UploadResult:
    """The drain itself. Always called with state/upload.lock held."""

    queue_store = StateStore(cfg.repo_root / "state" / "upload_queue.json")
    uploads_store = StateStore(cfg.repo_root / "state" / "uploads.json")
    queue = queue_store.read()
    if only_course:
        queue = [q for q in queue if q.get("course_code") == only_course]
    if not queue:
        log.info("Upload queue is empty")
        return result

    done = build_upload_index(uploads_store.read())
    new_records: list[dict] = []
    completed_keys: set[tuple[str, str]] = set()

    staging_mode = cfg.notebooklm_mode == "staging"
    notebooks: list[Notebook] | None = None
    if not staging_mode and not dry_run:
        try:
            notebooks = uploader.list_notebooks()
        except UploadError as exc:
            # Google-side breakage: fall back to staging for the whole run.
            log.error("NotebookLM unavailable, staging everything this run: %s", exc)
            result.errors.append({"scope": "notebooklm", "error": str(exc)})
            staging_mode = True

    # Which keys were staged before. A staged file must be retried, but the
    # retry has to be safe both ways, because "staged" does not reliably mean
    # "absent from the notebook". Tour_de_taille.png was staged on 2026-09-02
    # after NotebookLM timed out WAITING for it to process, and it turned out to
    # be sitting in the notebook the whole time. Re-adding it plainly would have
    # produced a second copy of the same source.
    staged_before = {
        upload_key(r["course_code"], r["filename"])
        for r in uploads_store.read()
        if r.get("course_code") and r.get("filename") and r.get("mode") == "staging"
    }

    for item in queue:
        course_code = item.get("course_code", "")
        filename = item.get("filename", "")
        path = Path(item.get("path", ""))
        key = upload_key(course_code, filename)

        # A regenerated file re-uploads every time it changes; everything else
        # is uploaded once and then recognised by (course, filename) forever.
        # A previously staged file replaces rather than adds, so the retry is
        # idempotent whether or not the earlier attempt actually landed.
        replace = bool(item.get("replace")) or key in staged_before
        if key in done and not replace:
            result.already_done.append(item)
            completed_keys.add(key)
            continue

        course = cfg.course_by_code(course_code)
        folder = cfg.folder_for(course) if course else cfg.base_path
        notebook_name = item.get("notebook") or (course.notebook if course else "")

        youtube = item.get("youtube")
        if youtube:
            if dry_run:
                log.info("[dry-run] would add the video %s -> %s", filename, notebook_name)
                result.uploaded.append(dict(item, mode="auto"))
                completed_keys.add(key)
                continue
            if staging_mode or notebooks is None:
                # Nothing to stage on disk: the .webloc shortcut the sync wrote
                # into the course folder already IS the fallback. Leave the item
                # queued so the next run tries NotebookLM again.
                log.warning("NotebookLM unavailable; %s stays queued", filename)
                continue
            notebook = find_notebook(notebooks, notebook_name)
            if notebook is None:
                log.error("No notebook named %r for %s", notebook_name, filename)
                result.errors.append(
                    {"scope": f"{course_code}/{filename}",
                     "error": f"notebook not found: {notebook_name}"}
                )
                continue
            try:
                uploader.add_youtube(notebook.id, youtube, filename)
            except UploadError as exc:
                log.error("Could not add the video %s: %s", filename, exc)
                result.errors.append({"scope": f"{course_code}/{filename}", "error": str(exc)})
                continue
            log.info("Added the video %s to %s", filename, notebook_name)
            result.uploaded.append(dict(item, mode="auto"))
            completed_keys.add(key)
            new_records.append({
                "course_code": course_code, "filename": filename,
                "notebook": notebook_name, "youtube": youtube, "mode": "auto",
                "uploaded_at": datetime.now().isoformat(timespec="seconds"),
            })
            continue

        if not path.exists():
            log.error("Queued file is gone: %s", path)
            result.errors.append(
                {"scope": f"{course_code}/{filename}", "error": f"file missing: {path}"}
            )
            continue

        size = path.stat().st_size
        if size > MAX_SOURCE_BYTES:
            log.warning("%s is %.0f MB, over NotebookLM's 200 MB cap", filename, size / 1e6)
            result.skipped_oversize.append(dict(item, size_bytes=size))
            completed_keys.add(key)  # never retry; it will never fit
            continue

        if dry_run:
            log.info("[dry-run] would upload %s -> %s", filename, notebook_name)
            result.uploaded.append(dict(item, mode="auto"))
            continue

        if staging_mode:
            staged = stage_file(path, folder)
            result.staged.append(dict(item, staged_path=str(staged)))
            new_records.append(_record(course_code, filename, notebook_name, "", "staging"))
            completed_keys.add(key)
            continue

        notebook = find_notebook(notebooks or [], notebook_name)
        if notebook is None:
            log.error("Notebook %r not found; staging %s", notebook_name, filename)
            result.errors.append(
                {
                    "scope": f"{course_code}/{filename}",
                    "error": f"notebook not found: {notebook_name!r}",
                }
            )
            staged = stage_file(path, folder)
            result.staged.append(dict(item, staged_path=str(staged)))
            new_records.append(_record(course_code, filename, notebook_name, "", "staging"))
            completed_keys.add(key)
            continue

        try:
            if replace:
                uploader.replace_file(notebook.id, path)
            else:
                uploader.add_file(notebook.id, path)
        except UploadError as exc:
            log.error("Upload failed for %s: %s", filename, exc)
            result.errors.append({"scope": f"{course_code}/{filename}", "error": str(exc)})
            staged = stage_file(path, folder)
            result.staged.append(dict(item, staged_path=str(staged)))
            new_records.append(
                _record(course_code, filename, notebook_name, notebook.id, "staging")
            )
            completed_keys.add(key)
            continue

        log.info("Uploaded %s -> %s", filename, notebook.title.strip())
        result.uploaded.append(dict(item, mode="auto"))
        if key not in done:
            # A replaced file is already on the books; a second record would
            # only make build_upload_index count it twice.
            new_records.append(
                _record(course_code, filename, notebook_name, notebook.id, "auto")
            )
        completed_keys.add(key)

    if not dry_run:
        if new_records:
            uploads_store.write(uploads_store.read() + new_records)
        if completed_keys:
            # Re-read under the lock: an append that landed while we were
            # uploading must survive. Held for milliseconds, never during the
            # uploads themselves.
            with run_lock(cfg.repo_root / "state" / "queue.lock", wait=30):
                remaining = [
                    q
                    for q in queue_store.read()
                    if upload_key(q.get("course_code", ""), q.get("filename", ""))
                    not in completed_keys
                ]
                queue_store.write(remaining)

    log.info("Upload finished: %s", result.summary())
    return result


def _record(course_code: str, filename: str, notebook: str, notebook_id: str, mode: str) -> dict:
    return {
        "course_code": course_code,
        "filename": filename,
        "notebook": notebook,
        "notebook_id": notebook_id,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.notebooklm_upload",
        description="Upload queued course documents into NotebookLM.",
    )
    parser.add_argument("--config", default=str(REPO_ROOT / "config.yaml"))
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be uploaded; writes nothing"
    )
    parser.add_argument("--course", metavar="CODE", help="limit to one course code")
    parser.add_argument(
        "--list-notebooks",
        action="store_true",
        help="print NotebookLM notebook names and ids, then exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    repo_root = config_path.resolve().parent
    log = setup_logging("notebooklm_upload", repo_root)

    try:
        cfg = load_config(config_path, repo_root=repo_root)
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        notify("NotebookLM upload failed", str(exc), critical=True)
        return 2

    uploader = NlmUploader()

    if args.list_notebooks:
        try:
            for notebook in uploader.list_notebooks():
                print(f"{notebook.id}  {notebook.title.strip()}")
        except UploadError as exc:
            log.error("%s", exc)
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    try:
        result = upload_queue(
            cfg, uploader, dry_run=args.dry_run, only_course=args.course, logger=log
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Upload aborted")
        notify("NotebookLM upload failed", f"{type(exc).__name__}: {exc}", critical=True)
        return 2

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}{result.summary()}")

    if result.staged:
        courses = human_courses(cfg, {s["course_code"] for s in result.staged})
        notify(
            "NotebookLM: files staged",
            f"{len(result.staged)} file(s) need a manual drag-in ({courses})",
            cfg=cfg.notify,
        )
    if result.skipped_oversize:
        names = ", ".join(s["filename"] for s in result.skipped_oversize[:3])
        notify("NotebookLM: files too large", names, cfg=cfg.notify)

    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
