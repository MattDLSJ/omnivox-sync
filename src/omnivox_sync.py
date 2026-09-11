"""Milestone 1: Omnivox LEA sync.

Orchestration only. All Playwright work lives in src/omnivox.py, so every
decision in this module is testable without a browser.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from src.common import (
    build_portal,
    Config,
    ConfigError,
    Course,
    StateStore,
    load_config,
    load_credentials,
    notify,
    setup_logging,
    human_courses,
    human_scope,
    RunBusy,
    run_lock,
)
from src.convert import ConversionError, convert_to_pdf, is_uploadable, needs_conversion
from src.finder import decorate
from src.omnivox import (
    MfaRequired,
    OmnivoxCourse,
    OmnivoxDocument,
    OmnivoxError,
    OmnivoxSession,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# When the sync fails for a network reason, a retry is armed until the next
# scheduled window. Bounded on purpose: a laptop offline all morning must not
# queue up a pile of runs that all fire at once when the wifi returns.
SYNC_WINDOWS = ((7, 30), (12, 15), (18, 30))
_NETWORK_HINTS = (
    "timeout", "err_internet", "err_name_not_resolved", "err_connection",
    "err_network", "temporary failure in name resolution", "connection refused",
    "network is unreachable", "nodename nor servname",
)


LOCK_BUSY = 4  # another run held state/omnivox.lock


def is_network_error(exc: Exception) -> bool:
    """Was this failure plausibly just 'no internet'?

    Deliberately generous: a false positive only costs one extra retry, while a
    false negative means a missed morning of documents.
    """
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(hint in blob for hint in _NETWORK_HINTS)


def next_window(now: datetime, windows=None) -> datetime:
    """The next scheduled sync time after `now`."""
    from datetime import timedelta as _td

    windows = windows or SYNC_WINDOWS
    for hour, minute in windows:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate
    first = windows[0]
    return (now + _td(days=1)).replace(
        hour=first[0], minute=first[1], second=0, microsecond=0
    )


def arm_retry(cfg: Config, reason: str, *, now: datetime | None = None) -> None:
    """Record that a retry is wanted, expiring at the next scheduled window."""
    now = now or datetime.now()
    StateStore(cfg.repo_root / "state" / "retry_pending.json").write(
        [{
            "armed_at": now.isoformat(timespec="seconds"),
            "deadline": next_window(now, cfg.sync_times).isoformat(timespec="seconds"),
            "reason": reason[:300],
        }]
    )


def clear_retry(cfg: Config) -> None:
    (cfg.repo_root / "state" / "retry_pending.json").unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Login that needs a human
# --------------------------------------------------------------------------
#
# Omnivox revokes the trusted-device cookie every so often, and once it does,
# nothing automated can get past the identity check: it wants a six-digit code
# from an e-mail. The scheduled job used to keep trying anyway, three times a
# day, forever. Each attempt asked Omnivox to e-mail another code.
#
# That is worse than being stuck. It buries the real code among unrequested
# ones, it looks like a credential-stuffing pattern from the school's side, and
# the person only finds out because their inbox fills with codes rather than
# because their own tool told them.

_LOGIN_BLOCK = "login_required.json"


def block_login(cfg: Config, reason: str, *, now: datetime | None = None) -> None:
    """Stop attempting a login that we know needs a person, and say why."""
    now = now or datetime.now()
    path = cfg.repo_root / "state" / _LOGIN_BLOCK
    existing = StateStore(path).read()
    first = existing[0].get("since") if existing else now.isoformat(timespec="seconds")
    attempts = (existing[0].get("attempts", 0) if existing else 0) + 1
    StateStore(path).write([{
        "since": first,
        "last": now.isoformat(timespec="seconds"),
        "attempts": attempts,
        "reason": reason,
    }])
    _write_attention(cfg, login_block_summary(cfg))


#: Written beside _digest.md while login is broken, and deleted the moment it
#: works again. A notification can be missed, swiped away, or tuned out after
#: the third identical one. A file that appears in the folder you already use,
#: and that syncs to your phone with the rest of it, cannot be.
ATTENTION_FILE = "_ATTENTION.md"


def _write_attention(cfg: Config, summary: str) -> None:
    try:
        path = cfg.digest_dir() / ATTENTION_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Your school sync has stopped\n\n"
            f"{summary}\n\n"
            "Until then no new documents are arriving, and nothing else is\n"
            "wrong: your files on disk are untouched and your password is\n"
            "fine. Omnivox simply stopped trusting this computer, which it\n"
            "does every so often, and only a person can answer its code.\n\n"
            "## What to do\n\n"
            "In a terminal, in the project folder:\n\n"
            "    make login\n\n"
            "A browser opens. Log in, type the six-digit code Omnivox e-mails\n"
            "you, and tick **J'utilise un appareil de confiance** before you\n"
            "validate. Missing that box is what brings this back.\n\n"
            "This file deletes itself once the next sync succeeds.\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # cosmetics must never be the reason a run dies


def _clear_attention(cfg: Config) -> None:
    try:
        (cfg.digest_dir() / ATTENTION_FILE).unlink(missing_ok=True)
    except OSError:
        pass


def clear_login_block(cfg: Config) -> None:
    (cfg.repo_root / "state" / _LOGIN_BLOCK).unlink(missing_ok=True)
    _clear_attention(cfg)


def login_block(cfg: Config) -> dict | None:
    """The recorded block, or None when login is believed to work."""
    records = StateStore(cfg.repo_root / "state" / _LOGIN_BLOCK).read()
    return records[0] if records else None


def login_block_summary(cfg: Config) -> str:
    """One line a human can act on, short enough to survive a phone banner."""
    block = login_block(cfg)
    if not block:
        return ""
    since = str(block.get("since", ""))[:16].replace("T", " ")
    return (
        f"Run `make login` to fix. Nothing has synced since {since}, "
        f"and every run until then asks Omnivox to e-mail you another code."
    )


def retry_due(cfg: Config, *, now: datetime | None = None) -> bool:
    """Should a retry run right now?

    False once the next scheduled window has passed: that run supersedes the
    retry, so nothing stacks.
    """
    now = now or datetime.now()
    records = StateStore(cfg.repo_root / "state" / "retry_pending.json").read()
    if not records:
        return False
    deadline = records[-1].get("deadline", "")
    if deadline and now >= datetime.fromisoformat(deadline):
        clear_retry(cfg)
        return False
    return True


def internet_up(timeout: int = 6, probe_url: str = "") -> bool:
    """Reachability, probed against the school's own portal.

    Probing a hardcoded school told you the wrong thing on any other one: the
    host would be up while yours was down, or vice versa, and the retry logic
    keys off this answer.
    """
    import urllib.request

    from src.omnivox import DEFAULT_PORTAL

    try:
        urllib.request.urlopen(probe_url or DEFAULT_PORTAL.home, timeout=timeout).close()
        return True
    except Exception:  # noqa: BLE001
        return False

DOWNLOAD = "download"
DOWNLOAD_REVISION = "download_revision"
SKIP_KNOWN = "skip_known"
SKIP_EXISTING = "skip_existing"


# --------------------------------------------------------------------------
# New-file diffing (pure)
# --------------------------------------------------------------------------


def download_key(course_code: str, filename: str, publish_date: str) -> tuple[str, str, str]:
    """The spec's new-file identity tuple (section 7 step 4)."""
    return (course_code, filename, publish_date)


def build_state_index(records: list[dict]) -> dict:
    """Index download records for O(1) lookup.

    Returns {"tuples": set[(course, filename, date)], "names": set[(course, filename)],
    "conversions": set[path]}. Records missing keys are ignored rather than
    crashing the run.

    "conversions" is the set of PDFs this pipeline rendered itself. They sit in
    the course folder under a name nobody downloaded, so without this the
    existence check below cannot tell one from a real document.
    """
    tuples: set[tuple[str, str, str]] = set()
    names: set[tuple[str, str]] = set()
    conversions: set[str] = set()
    for record in records:
        course = record.get("course_code")
        filename = record.get("filename")
        if not course or not filename:
            continue
        names.add((course, filename))
        tuples.add(download_key(course, filename, record.get("publish_date", "")))
        if record.get("converted_pdf"):
            conversions.add(str(record["converted_pdf"]))
    return {"tuples": tuples, "names": names, "conversions": conversions}


#: Characters NTFS refuses in a filename. macOS accepts all of them, so a
#: teacher's "Chapitre 3: la mondialisation.pdf" downloads fine on a Mac and
#: then fails to write on Windows AFTER the transfer finished, with an error
#: that reads like a network fault.
_WINDOWS_ILLEGAL = ':*?"<>|'

#: Device names Windows reserves at any extension: CON.pdf is not a file.
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def safe_filename(filename: str) -> str:
    """Reduce a site-supplied filename to one path component, legal anywhere.

    Filenames come from a remote page and are never trusted to stay inside
    the course folder. They also have to survive the strictest filesystem
    anyone runs this on, which is NTFS: this repo is macOS-first, but the
    name it produces here is what a Windows checkout writes to disk.

    Accents are kept. They are legal on every filesystem in question and
    stripping them would mangle most of the French course material.
    """
    name = Path(filename.replace("\\", "/")).name.strip()
    name = "".join("_" if ch in _WINDOWS_ILLEGAL else ch for ch in name)
    # Windows silently drops trailing dots and spaces, so a name ending in one
    # resolves to a different file than the one you asked for.
    name = name.rstrip(" .")
    if name in ("", ".", ".."):
        return "unnamed_document"
    stem, dot, ext = name.partition(".")
    if stem.upper() in _WINDOWS_RESERVED:
        name = f"{stem}_{dot}{ext}"
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
        # Seen this name before at a different publish date: a revision.
        return DOWNLOAD_REVISION, target_dir / _revision_name(filename, doc.publish_date)

    # The file sitting there is a PDF we rendered from a slide deck, not
    # something downloaded. Teachers who post a deck often post their own PDF
    # of it beside the deck, under exactly this name. Treating ours as "already
    # downloaded" is how the teacher's own export never got fetched: it is the
    # better render, and it was one row further down the same listing.
    if str(dest) in state_index.get("conversions", ()):
        return DOWNLOAD, dest

    # No record at all: the state file was lost. Do not re-download
    # (spec section 7 step 4).
    return SKIP_EXISTING, dest


# --------------------------------------------------------------------------
# Semester bootstrap (--discover)
# --------------------------------------------------------------------------


def humanize_course_name(display_name: str) -> str:
    """Make an ALL-CAPS LEA course title readable.

    LEA renders course names in caps ("ÉCRITURE ET LITTÉRATURE") and truncates
    long ones with an ellipsis. French uses sentence case, so lowercasing
    everything but the first letter reproduces the user's existing folder
    names exactly ("Écriture et littérature"). Names that already contain
    lowercase are left alone.
    """
    cleaned = " ".join(display_name.split())
    # LEA truncates long titles server-side; a trailing ellipsis is not part
    # of the name and must not end up in a folder name.
    while cleaned.endswith((".", "…", " ")):
        cleaned = cleaned[:-1].rstrip()

    if not cleaned:
        return cleaned
    if any(c.islower() for c in cleaned):
        return cleaned  # already mixed case; trust it

    lowered = cleaned.lower()
    # Roman numerals must not be lowercased: "DU XVE SIÈCLE" is "du XVe siècle",
    # never "du xve siècle".
    lowered = " ".join(_restore_roman(word) for word in lowered.split(" "))
    for index, char in enumerate(lowered):
        if char.isalpha():
            return lowered[:index] + char.upper() + lowered[index + 1 :]
    return lowered


# A whitelist, not a general numeral regex. "DIX" (French for ten) and "MILLE"
# are both valid Roman numeral strings, so a general matcher shouts ordinary
# words. Course titles only ever use century and volume numbers.
_ROMAN_NUMERALS = frozenset(
    {
        # "vi" is deliberately absent: "VIe siecle" is vanishingly rare in a
        # cegep title, while "vie" (life) is common.
        "ii", "iii", "iv", "vii", "viii", "ix",
        "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix",
        "xx", "xxi", "xxii", "xxiii",
    }
)
# Optional French ordinal suffix, only the forms that pair with a multi-letter
# numeral: XVe, XXes.
_ROMAN_RE = re.compile(r"^([ivxlcdm]{2,})(e|es)?$")


def _restore_roman(word: str) -> str:
    """Re-uppercase a Roman numeral token, keeping any French ordinal suffix
    lowercase: "xve" -> "XVe", "xxi" -> "XXI", "xixe" -> "XIXe"."""
    core = word.strip(".,;:()")
    if not core:
        return word
    prefix_len = word.index(core)
    leading, trailing = word[:prefix_len], word[prefix_len + len(core) :]
    match = _ROMAN_RE.match(core)
    if not match or match.group(1) not in _ROMAN_NUMERALS:
        return word
    return f"{leading}{match.group(1).upper()}{match.group(2) or ''}{trailing}"


# Cégep titles carry a trailing qualifier that adds nothing in a folder list:
# "Calcul intégral POUR LES SCIENCES HUMAINES". Cut it, but only when the name
# is actually long and enough of it survives -- otherwise "Pouvoirs, idéologies
# et enjeux démocratiques" would collapse to "Pouvoirs".
SHORTEN_ABOVE = 30
MIN_KEPT = 12
_CUT_POINTS = (", ", " pour ", " en ")


def shorten_course_name(name: str) -> str:
    """Trim a long course title down to its distinctive head."""
    name = " ".join((name or "").split())
    if len(name) <= SHORTEN_ABOVE:
        return name
    best = None
    for marker in _CUT_POINTS:
        index = name.find(marker)
        if index >= MIN_KEPT and (best is None or index < best):
            best = index
    return name[:best].rstrip(" ,;:") if best else name


def folder_name_from(display_name: str) -> str:
    """Turn an Omnivox display name into a safe single-component folder name."""
    cleaned = shorten_course_name(humanize_course_name(display_name))
    cleaned = cleaned.replace("/", "-").replace("\\", "-").strip()
    cleaned = " ".join(cleaned.split())
    if cleaned in ("", ".", ".."):
        return "unnamed course"
    return cleaned


def write_courses(config_path: Path, courses: list[OmnivoxCourse], semester: str) -> int:
    """Put the discovered courses into config.yaml, keeping the rest of it.

    Printing a block for somebody to paste in assumed a person who knows what
    a YAML list is and where in the file it goes. The whole promise here is
    that they do not have to, and it is also a step that cannot be automated
    away by the AI when the AI is not the one running the command.

    A copy is left beside it first. This overwrites a file somebody may have
    hand-edited, and one command that silently replaces a semester of
    corrections is not a trade worth making for the convenience.
    """
    import yaml as _yaml

    from src.config_edit import set_block

    config_path = Path(config_path)
    original = config_path.read_text(encoding="utf-8")
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")

    merged, added, kept = merge_courses(original, courses, semester)
    updated = set_block(original, "courses", merged)
    config_path.write_text(updated, encoding="utf-8")

    # Read it back. A config that no longer parses is a config that stops the
    # next run dead, and the previous version is right there.
    try:
        import yaml as _yaml

        loaded = _yaml.safe_load(updated) or {}
        assert isinstance(loaded.get("courses"), list) and loaded["courses"]
    except Exception:  # noqa: BLE001
        config_path.write_text(original, encoding="utf-8")
        raise

    print(f"\n{config_path.name}: {added} course(s) added, {kept} left as they were.")
    for course in courses:
        print(f"  {course.code}  {course.name}")
    if kept:
        print(
            f"\nThe {kept} that were already there keep their folder names and "
            "anything\nelse set by hand. The previous file is at "
            f"{backup.name} either way."
        )
    return len(courses)


def merge_courses(config_text: str, courses, semester: str) -> tuple[str, int, int]:
    """Add what is new, leave alone what is already configured.

    Replacing the whole block wholesale was how a real config lost every
    teacher name, folder emoji, short name and group number in it. Discovery
    knows a course's code and its SHOUTED Omnivox title and nothing else; the
    readable folder name, the teacher and the icon are a person's work, and a
    command that silently discards them for the convenience of one setup step
    is not a trade worth making.

    A course in the config that Omnivox no longer lists is kept too. It might
    be a dropped course, and it might equally be a scraper hiccup on one
    afternoon; deleting somebody's configuration on that evidence is not
    something to do quietly.
    """
    import yaml as _yaml

    try:
        existing = (_yaml.safe_load(config_text) or {}).get("courses") or []
    except Exception:  # noqa: BLE001 - a broken config is still worth writing to
        existing = []
    by_code = {str(c.get("code")): c for c in existing if isinstance(c, dict)}

    out, added, kept = [], 0, 0
    for course in courses:
        if course.code in by_code:
            out.append(by_code.pop(course.code))
            kept += 1
        else:
            folder = folder_name_from(course.name)
            out.append({
                "code": course.code,
                "omnivox_name": course.name,
                "folder": folder,
                "notebook": f"{folder} - Cegep {semester}",
                "record": False,
            })
            added += 1
    # Anything configured that discovery did not return, kept at the end.
    out.extend(by_code.values())

    return (
        _yaml.safe_dump({"courses": out}, allow_unicode=True, sort_keys=False, width=1000),
        added,
        kept,
    )


def render_discovery_yaml(courses: list[OmnivoxCourse], semester: str) -> str:
    """A paste-ready `courses:` block for config.yaml (spec section 5.3)."""
    block = {
        "courses": [
            {
                "code": course.code,
                "omnivox_name": course.name,
                "folder": folder_name_from(course.name),
                "notebook": f"{folder_name_from(course.name)} - Cegep {semester}",
                "record": False,
            }
            for course in courses
        ]
    }
    return yaml.safe_dump(block, allow_unicode=True, sort_keys=False, width=1000)


# --------------------------------------------------------------------------
# Sync
# --------------------------------------------------------------------------


@dataclass
class SyncResult:
    downloaded: list[dict] = field(default_factory=list)
    converted: list[dict] = field(default_factory=list)
    conversion_failures: list[dict] = field(default_factory=list)
    upload_queue: list[dict] = field(default_factory=list)
    skipped_existing: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{len(self.downloaded)} downloaded"]
        if self.converted:
            parts.append(f"{len(self.converted)} converted")
        if self.conversion_failures:
            parts.append(f"{len(self.conversion_failures)} conversion failures")
        if self.skipped_existing:
            parts.append(f"{len(self.skipped_existing)} already on disk")
        if self.links:
            parts.append(f"{len(self.links)} web links")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return ", ".join(parts)


def published_pdf_stems(documents) -> set[str]:
    """Stems the teacher publishes as a PDF in their own right.

    Case-folded, because LEA filenames are whatever the teacher typed.
    """
    stems = set()
    for doc in documents:
        if getattr(doc, "is_link", False):
            continue
        name = safe_filename(doc.filename)
        if name.lower().endswith(".pdf"):
            stems.add(Path(name).stem.lower())
    return stems


def _handle_conversion(
    path: Path,
    folder: Path,
    result: SyncResult,
    dry_run: bool,
    log: logging.Logger,
    published_pdfs: set[str] | None = None,
) -> Path | None:
    """Convert if needed and return the path that should be uploaded, or None.

    Conversion exists because NotebookLM refuses .pptx, not because a rendered
    PDF is desirable in itself. So when the teacher has already published a PDF
    of the same deck, rendering is not merely redundant, it is worse: it writes
    a LibreOffice render of a PowerPoint over the teacher's own export, and
    LibreOffice routinely shifts fonts and mangles diagrams. Skip it and let the
    published PDF be downloaded and uploaded on its own row.
    """
    if not needs_conversion(path):
        return path if is_uploadable(path) else None

    if published_pdfs and path.stem.lower() in published_pdfs:
        log.info(
            "%s: not converting; the teacher publishes %s.pdf themselves",
            path.name, path.stem,
        )
        return None

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
    site_courses: dict[str, OmnivoxCourse],
    state_index: dict,
    result: SyncResult,
    new_records: list[dict],
    dry_run: bool,
    log: logging.Logger,
) -> None:
    folder = cfg.folder_for(course)
    if not dry_run:
        existed = folder.exists()
        folder.mkdir(parents=True, exist_ok=True)
        if not existed:
            # Match the Finder presentation the user keeps by hand on past
            # semesters, so a new semester looks right without manual work.
            decorate(folder, icon=course.icon, tag=cfg.finder.tag, color=cfg.finder.color)

    site_course = site_courses.get(course.code)
    if site_course is None:
        # Loud by design: a configured course that vanished from LEA means the
        # semester rolled over or the session landed somewhere unexpected.
        raise OmnivoxError(
            f"{course.code} is in config.yaml but not visible on LEA. "
            "Re-run --discover if the semester changed."
        )

    documents = list(driver.list_documents(site_course))

    # Assignment briefs live in a different LEA section from Documents, and
    # until 2026-09-03 nothing here ever looked at it. Four weeks of syncing had
    # collected every slide deck across six courses and not one énoncé, which is
    # the half that actually carries the deadlines and the marking criteria.
    briefs = []
    try:
        briefs = list(driver.list_assignments(site_course))
    except Exception as exc:  # noqa: BLE001
        # A section that breaks must not cost the documents that already worked.
        log.warning("%s: could not read the assignment briefs: %s", course.code, exc)
        result.errors.append({"course": course.code, "scope": "travaux", "error": str(exc)})

    # Computed over BOTH listings before anything downloads: the deck and the
    # PDF of it are two separate rows, either can come first, and this reads
    # only filenames, which do not expire.
    published_pdfs = published_pdf_stems(documents + briefs)

    # A LEA document link carries the timestamp of the listing that produced
    # it (Ref=20260910035716) and DIES the moment a newer listing for that
    # course is rendered. Listing the briefs renders one, so every document
    # link went stale before it was ever used: the click waited its full 30
    # seconds for an anchor no longer on the page, and the fetch that followed
    # got HTTP 404. Four documents had been failing that way on every run
    # since 2026-09-02, two minutes of timeouts a run, reported as "download
    # failed" rather than as the expired link it was.
    #
    # So each set is downloaded while its OWN listing is the current page.
    # Briefs first, because listing them is what we just did. Confirmed live
    # in both directions: the same document is 404 after navigating away and
    # HTTP 200 on a listing rendered for the purpose.
    batch = {
        "cfg": cfg,
        "course": course,
        "site_course": site_course,
        "folder": folder,
        "driver": driver,
        "result": result,
        "state_index": state_index,
        "new_records": new_records,
        "published_pdfs": published_pdfs,
        "dry_run": dry_run,
        "log": log,
    }
    if briefs:
        _download_batch(briefs, **batch)
    if documents and any(
        classify_document(doc, state_index, folder)[0] != SKIP_KNOWN
        for doc in documents
    ):
        documents = list(driver.list_documents(site_course))
    _download_batch(documents, **batch)


def _download_batch(documents, *, cfg, course, site_course, folder, driver,
                    result, state_index, new_records, published_pdfs, dry_run,
                    log):
    """Download one listing's worth of rows. See _sync_course for why the
    caller must render that listing immediately before calling this."""
    for doc in documents:
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
            "description": doc.description,
            "doc_id": doc.doc_id,
        }

        if decision == DOWNLOAD and getattr(doc, "is_video", False):
            # A video the teacher embedded rather than uploaded. There is no
            # file to fetch: what the row is worth is the YouTube address, and
            # NotebookLM takes those directly, so it goes in as a source
            # alongside the slides instead of being lost like it was before.
            url = driver.resolve_video_url(doc)
            title = (doc.description or doc.filename).strip()
            if not url:
                log.warning("%s: could not resolve the video %r", course.code, title)
                result.errors.append(
                    {"scope": course.code, "error": f"unresolved video: {title}"}
                )
                continue

            link_path = folder / f"{safe_filename(title)}.webloc"
            log.info("%s: video %r -> %s", course.code, title, url)
            record["path"] = str(link_path)
            record["kind"] = "video"
            record["url"] = url
            if not dry_run:
                from src.shortcuts import write_shortcut

                write_shortcut(link_path, url)
                new_records.append(record)
                result.upload_queue.append({
                    "course_code": course.code,
                    "notebook": course.notebook,
                    "youtube": url,
                    "filename": title,
                    "path": "",
                    "queued_at": datetime.now().isoformat(timespec="seconds"),
                })
            result.links.append(record)
            state_index["tuples"].add(
                download_key(course.code, doc.filename, doc.publish_date)
            )
            state_index["names"].add((course.code, doc.filename))
            continue

        if decision == DOWNLOAD and doc.is_link:
            # An external web link, not a file. Never downloadable, but it is
            # still information the teacher posted, so it is recorded once and
            # surfaced in the digest (spec section 2).
            log.info("%s: web link posted: %s", course.code, doc.description or doc.filename)
            record["path"] = ""
            record["kind"] = "link"
            result.links.append(record)
            if not dry_run:
                new_records.append(record)
            state_index["tuples"].add(
                download_key(course.code, doc.filename, doc.publish_date)
            )
            state_index["names"].add((course.code, doc.filename))
            continue

        if decision == SKIP_EXISTING:
            log.warning(
                "%s: %s already on disk but absent from state; recording without downloading",
                course.code,
                doc.filename,
            )
            result.skipped_existing.append(record)
            if not dry_run:
                new_records.append(record)
            state_index["tuples"].add(
                download_key(course.code, doc.filename, doc.publish_date)
            )
            state_index["names"].add((course.code, doc.filename))
            continue

        # DOWNLOAD or DOWNLOAD_REVISION
        if dry_run:
            log.info("[dry-run] would download %s -> %s", doc.filename, dest)
            result.downloaded.append(record)
            upload_path = _handle_conversion(dest, folder, result, True, log, published_pdfs)
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

        upload_path = _handle_conversion(dest, folder, result, False, log, published_pdfs)
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

    if not courses:
        # Pre-bootstrap state (spec section 5.3): config.yaml has `courses: []`
        # until --discover has been run. Not an error.
        log.info("No courses configured; run --discover to bootstrap config.yaml")
        return result

    # Fetch the dashboard once, not once per course.
    site_courses = {c.code: c for c in driver.list_courses()}

    # Read-only portal sweep for M3. Never allowed to break the sync.
    if hasattr(driver, "collect_announcements"):
        try:
            result.observations = driver.collect_announcements()
        except Exception as exc:  # noqa: BLE001
            log.warning("Announcement collection failed: %s", exc)

    # Teacher announcements, saved into their course folders. Here rather than
    # in chain_downstream because the driver only exists inside this function,
    # and an earlier version read it off the result object, where it does not
    # exist: the step would have silently never run.
    if hasattr(driver, "list_mio"):
        try:
            from src.mio import sync_mio

            queued = sync_mio(cfg, driver, dry_run=dry_run, logger=log)
            if queued:
                log.info("MIO: %d new from your teachers", len(queued))
                result.upload_queue.extend(queued)
        except Exception as exc:  # noqa: BLE001 - never at the cost of documents
            log.warning("MIO step failed: %s", exc)
            result.errors.append({"scope": "mio", "error": str(exc)})

    if hasattr(driver, "list_communiques"):
        try:
            from src.communiques import sync_communiques

            queued = sync_communiques(cfg, driver, dry_run=dry_run, logger=log)
            if queued:
                log.info("Communiqués: %d new", len(queued))
                result.upload_queue.extend(queued)
        except Exception as exc:  # noqa: BLE001 - never at the cost of documents
            log.warning("Communiqué step failed: %s", exc)
            result.errors.append({"scope": "communiques", "error": str(exc)})

    for course in courses:
        try:
            _sync_course(
                cfg, course, driver, site_courses, state_index, result, new_records, dry_run, log
            )
        except Exception as exc:  # noqa: BLE001 - one bad course must not kill the run
            log.exception("Course %s failed", course.code)
            result.errors.append({"scope": course.code, "error": str(exc), "screenshot": None})

    if new_records and not dry_run:
        store.write(store.read() + new_records)

    log.info("Sync finished: %s", result.summary())
    return result


# --------------------------------------------------------------------------
# The M2 / M3 seam
# --------------------------------------------------------------------------


def _run_digest(cfg: Config, result: SyncResult, dry_run: bool, log: logging.Logger) -> None:
    """Hand everything observed to M3. Never allowed to fail the sync run."""
    try:
        from src.digest import run_digest

        digest_result = run_digest(
            cfg, result.observations, result, dry_run=dry_run, logger=log
        )
        if digest_result.notification and not dry_run:
            title, body = digest_result.notification
            notify(title, body, critical=bool(digest_result.important), cfg=cfg.notify)
    except Exception as exc:  # noqa: BLE001
        log.exception("Digest step failed")
        notify(
            "School digest failed",
            f"{type(exc).__name__}: {exc}. Downloads are safe; see logs/.",
            critical=True,
            cfg=cfg.notify,
        )


def chain_downstream(
    cfg: Config,
    result: SyncResult,
    *,
    dry_run: bool = False,
    manual: bool = False,
    logger: logging.Logger | None = None,
) -> None:
    """Hand this run's output to the uploader and the digest.

    Both are real work, not a contract: the upload shells out to `nlm source
    add --wait` per file with a 900s timeout, and the digest may call Gemini.
    An upload failure must never fail the sync, since the documents are already
    safely on disk by this point.

    `manual` forces the upload step to run even when this sweep downloaded
    nothing, so a button press drains files stranded by an earlier failure.
    """
    log = logger or logging.getLogger("school.omnivox_sync")

    # A note in every course folder saying what the automation can do, because
    # a session opened in one of those folders cannot otherwise know. Twice a
    # session concluded a textbook was unavailable, once on copyright grounds,
    # while `make books` could have fetched it from an account the student
    # pays for.
    try:
        from src.folder_readme import write_readmes

        write_readmes(cfg, dry_run=dry_run, logger=log)
    except Exception as exc:  # noqa: BLE001 - a note is never worth a run
        log.warning("Could not write the folder notes: %s", exc)

    # Before the queue is written, so a refreshed _horaire.md rides out with
    # this run's documents under the same lock rather than waiting for the next.
    _run_mirror(cfg, result, dry_run, log)

    if dry_run:
        log.info("[dry-run] would queue %d file(s) for upload", len(result.upload_queue))
        _run_digest(cfg, result, True, log)
        return

    if result.upload_queue:
        store = StateStore(cfg.repo_root / "state" / "upload_queue.json")
        stamp = datetime.now().isoformat(timespec="seconds")
        # read-modify-write, so it needs the lock: the recorder appends a
        # transcript to this same file, and the loser of a race vanishes with
        # no error anywhere. The file is already recorded as downloaded, so
        # nothing would ever re-queue it.
        try:
            with run_lock(cfg.repo_root / "state" / "queue.lock", wait=30):
                store.write(
                    store.read() + [dict(item, queued_at=stamp) for item in result.upload_queue]
                )
            log.info("Queued %d file(s) in state/upload_queue.json", len(result.upload_queue))
        except RunBusy:
            log.error(
                "Could not take state/queue.lock in 30s; %d file(s) not queued. "
                "They are on disk and the next run will pick them up.",
                len(result.upload_queue),
            )

    # Sequential, never nested: the append above has released queue.lock before
    # the drain starts. flock is not reentrant, so nesting them would deadlock.
    _run_upload(cfg, result, log, force=manual)

    _run_digest(cfg, result, dry_run, log)


def _run_mirror(
    cfg: Config, result: SyncResult, dry_run: bool, log: logging.Logger
) -> None:
    """Refresh each course's `_horaire.md` from the live calendar.

    Inert without SCHOOL_ICS_URL in .env. A calendar problem must never fail the
    sync: the documents are already safely on disk by this point, and the mirror
    is derived data that the next run will rebuild.

    Only files that actually changed are queued, so an unchanged semester does
    not re-upload six files to NotebookLM every morning.
    """
    try:
        from src.calendar_mirror import MirrorError, mirror

        outcome = mirror(cfg, dry_run=dry_run, logger=log)
    except (MirrorError, OSError) as exc:
        log.error("Calendar mirror failed: %s", exc)
        result.errors.append({"scope": "calendar_mirror", "error": str(exc)})
        return

    if not outcome.written and not outcome.unchanged:
        return
    log.info("Calendar mirror: %s", outcome.summary())

    # "Written" is not the same as "in the notebook". If an upload failed, the
    # file sits on disk unchanged and would never be queued again, because
    # nothing about it changes until the calendar does. So also queue any
    # mirror that has never landed successfully.
    landed = {
        (r.get("course_code"), r.get("filename"))
        for r in StateStore(cfg.repo_root / "state" / "uploads.json").read()
        if r.get("mode") == "auto"
    }

    by_folder = {cfg.folder_for(c): c for c in cfg.courses}
    for path in outcome.written + outcome.unchanged:
        course = by_folder.get(path.parent)
        if course is None:
            continue
        if path in outcome.unchanged and (course.code, path.name) in landed:
            continue
        result.upload_queue.append(
            {
                "course_code": course.code,
                "notebook": course.notebook,
                "path": str(path),
                "filename": path.name,
                # Regenerated every time it changes, so the notebook must end up
                # with one copy, not one per sync.
                "replace": True,
            }
        )


def _run_upload(
    cfg: Config, result: SyncResult, log: logging.Logger, *, force: bool = False
) -> None:
    """Drain the upload queue. An upload problem must never fail the sync run.

    `force` runs the drain even when this sweep queued nothing, which is how a
    button press picks up files left stranded by an earlier failed upload.
    """
    if not result.upload_queue and not force:
        return
    if cfg.notebooklm_mode == "off":
        # Not everybody uses NotebookLM. Before this existed the only way to
        # say so was "staging", which still copied every new file into a
        # _to_upload/ folder in every course.
        log.info("NotebookLM: off in config; %d queued file(s) left alone",
                 len(result.upload_queue))
        return
    try:
        from src.notebooklm_upload import NlmUploader, upload_queue

        upload_result = upload_queue(cfg, NlmUploader(), logger=log)
        log.info("NotebookLM: %s", upload_result.summary())
        if upload_result.staged:
            courses = human_courses(cfg, {s["course_code"] for s in upload_result.staged})
            notify(
                "NotebookLM: files staged",
                f"{len(upload_result.staged)} file(s) need a manual drag-in "
                f"({courses})",
                cfg=cfg.notify,
            )
    except Exception as exc:  # noqa: BLE001 - the queue is durable; M2 retries next run
        log.exception("NotebookLM upload step failed; queue left intact for the next run")
        result.errors.append({"scope": "notebooklm", "error": str(exc), "screenshot": None})


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


@contextmanager
def _open_session(cfg, *, headed: bool, logger):
    """Open a logged-in Omnivox session. Seam for tests: patched with a fake."""
    user, password = load_credentials(cfg.repo_root, required=False)
    with OmnivoxSession(
        cfg.repo_root / "state" / "omnivox-profile",
        headed=headed,
        screenshot_dir=cfg.repo_root / "logs",
        logger=logger,
        portal=build_portal(cfg),
    ) as session:
        session.login(user, password)
        yield session


def _mfa_in_recent_log(cfg: Config, lines: int = 400) -> bool:
    """Did the tail of the log end on an identity check rather than a sync?"""
    path = cfg.repo_root / "logs" / "omnivox_sync.log"
    if not path.exists():
        return False
    tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    last_mfa = last_ok = -1
    for i, line in enumerate(tail):
        if "MfaRequired" in line:
            last_mfa = i
        if "Sync finished" in line:
            last_ok = i
    return last_mfa > last_ok


#: Path fragments that mean "something else may delete this without warning".
#: An agent installed the whole project into its own scratch directory, where
#: the credentials, the signed-in browser profile and a semester of documents
#: all sat inside a folder its tool is free to clear.
_DISPOSABLE = (
    "/tmp/", "\\temp\\", "/temp/", "\\tmp\\",
    "scratch", "appdata\\local\\temp",
    ".gemini", ".cache", "downloads",
)


def _warn_if_disposable_location(repo_root: Path) -> None:
    """Say so if this is installed somewhere that gets cleared out.

    A warning and not a failure: somebody may have a good reason, and being
    wrong about this must never be the thing that stops a sync. But finding
    out by losing the browser profile and a semester of documents, with no
    explanation, is worse than a line of output.
    """
    lowered = str(repo_root).lower().replace("\\", "\\")
    hit = next((f for f in _DISPOSABLE if f.strip("/\\") in lowered), "")
    if hit:
        print(
            f"  location       WARNING, this is installed under "
            f"{repo_root}\n"
            "                 That path looks temporary. Your credentials, "
            "your signed-in\n"
            "                 browser profile and your documents all live "
            "here. Move the\n"
            "                 whole folder somewhere permanent, then run "
            "`make venv` again."
        )


def _doctor(cfg: Config) -> int:
    """Is this thing actually working? Answer without touching the network.

    It exists because the honest answer to "is it stuck" used to require
    reading a log file, and nobody reads a log file. Exit 1 when something
    needs a person, so a script can tell too.
    """
    problems = []

    _warn_if_disposable_location(cfg.repo_root)

    if not (cfg.repo_root / ".git").exists():
        problems.append(
            "This folder is not a git checkout, so it cannot pull fixes and "
            "cannot report one. It was downloaded as a ZIP rather than cloned. "
            "See INSTALL.md; the repair keeps every file you have."
        )
    else:
        print("  checkout       git, so updates and reports work")

    block = login_block(cfg)
    if block:
        problems.append(f"Omnivox login is blocked. {login_block_summary(cfg)}")
    elif _mfa_in_recent_log(cfg):
        # No block recorded, but the log says the last thing that happened was
        # an identity check. That is the state of anyone who updated to this
        # version in the middle of an outage, and of anyone whose block file
        # was deleted along with the rest of state/.
        problems.append(
            "The last login attempt hit an Omnivox identity check, so nothing "
            "is syncing. Run `make login` and tick "
            "\u00abJ'utilise un appareil de confiance\u00bb."
        )
    else:
        print("  login          ok, no identity check pending")

    log_path = cfg.repo_root / "logs" / "omnivox_sync.log"
    last = ""
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "Sync finished" in line:
                last = line[:19]
    if last:
        age = datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        hours = age.total_seconds() / 3600
        state = "ok" if hours < 24 else f"STALE, {int(hours)}h ago"
        print(f"  last sync      {state} ({last})")
        if hours >= 24:
            problems.append(
                f"Nothing has synced in {int(hours)} hours. Last was {last}."
            )
    else:
        problems.append("No sync has ever finished. Try: make dry-run")

    profile = cfg.repo_root / "state" / "omnivox-profile"
    if not profile.exists():
        problems.append("No browser profile yet. Run: make login")
    else:
        print("  browser        profile stored")

    # Credentials are optional, so their absence is a MODE and not a fault.
    # Reporting "No .env" as a problem sent people off to edit a file when
    # signing in once in the browser is both easier and what they had already
    # done. It only becomes a problem when there is no session either.
    user, password = load_credentials(cfg.repo_root, required=False)
    if user and password:
        print("  credentials    stored, so it can sign back in unattended")
    elif profile.exists():
        problems.append(
            "No credentials in .env, so nothing scheduled can sign in: the "
            "signed-in session does not survive the browser closing. Running it "
            "by hand still works. Fix with: make setup-omnivox"
        )
    else:
        problems.append(
            "Nothing here can sign in: no stored session and no credentials. "
            "Run: make login"
        )

    updates = StateStore(cfg.repo_root / "state" / "last_update.json").read()
    if updates:
        last = updates[0]
        if last.get("blocked"):
            problems.append(f"Auto-update is stuck. {last.get('message', '')}")
        else:
            when = str(last.get("at", ""))[:16].replace("T", " ")
            print(f"  updates        checked {when}")
    elif not cfg.auto_update:
        print("  updates        automatic updates are off")

    retry = StateStore(cfg.repo_root / "state" / "retry_pending.json").read()
    if retry:
        print("  retry          armed (it was offline; it will catch up)")

    if not problems:
        print("\nEverything looks healthy.")
        return 0
    print("\nNeeds you:\n")
    for problem in problems:
        print(f"  - {problem}")
    return 1


def _bootstrap_login(
    cfg: Config, log: logging.Logger, config_path: Path | None = None
) -> int:
    """Handle --login: open a visible browser, wait for the user to sign in and
    clear Omnivox's identity validation, then finish the setup in the same
    session.

    It does the discovery too, because the browser is already open and signed
    in and there is no reason to make somebody run a second command for it.
    That second command used to print a `courses:` block for a human to paste
    into a YAML file, which assumes a person who knows what a YAML list is,
    and which a tester ended up copying out of a terminal and back into a chat
    window by hand.

    The 6-digit code is entered by the user in the browser. This program never
    reads, requests, stores, or types it.
    """
    user, password = load_credentials(cfg.repo_root, required=False)
    print("Opening Omnivox in a visible browser window.")
    if user and password:
        print("Your saved credentials will be filled in for you.")
    else:
        print("Type your student number and password into that window.")
    print("If it asks for a 6-digit code, check your email, type the code in that")
    print("window, and TICK \"J'utilise un appareil de confiance\" before validating.")
    print("Waiting up to 10 minutes...\n")
    try:
        with OmnivoxSession(
            cfg.repo_root / "state" / "omnivox-profile",
            headed=True,
            screenshot_dir=cfg.repo_root / "logs",
            logger=log,
            portal=build_portal(cfg),
        ) as session:
            typed_user, typed_pass = session.await_manual_login(user, password)

            clear_login_block(cfg)  # a person just cleared what was blocking it
            print("\nLogged in, and this computer is now a trusted device, so")
            print("Omnivox will stop e-mailing you six-digit codes.")

            # While the browser is open and signed in. Failing here must not
            # undo the login, which is the part that needed a person.
            found = []
            if config_path and not cfg.courses:
                try:
                    print("\nFinding your courses...")
                    found = session.list_courses()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Discovery after login failed: %s", exc)
                    print(f"Could not list your courses ({type(exc).__name__}).")
                    print("Nothing is wrong with the login. Try: make discover")
    except Exception as exc:  # noqa: BLE001
        log.error("Interactive login failed: %s", exc)
        notify("School sync: login bootstrap failed", str(exc), critical=True, cfg=cfg.notify)
        return 2

    if found:
        try:
            write_courses(config_path, found, cfg.semester)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not write the discovered courses: %s", exc)
            print(f"Found your courses but could not write config.yaml ({exc}).")

    if not (user and password) and typed_user and typed_pass:
        _offer_to_save_credentials(cfg, typed_user, typed_pass)

    print("\nNext: make setup, then make dry-run")
    return 0


def _offer_to_save_credentials(cfg: Config, user: str, password: str) -> None:
    """Ask whether to keep what was just typed. Never assume, never print it.

    The trusted-device cookie survives in the stored profile; the SESSION
    cookie does not, because it dies with the browser. Measured over 93
    scheduled runs: 114 form logins, zero session reuses. So a scheduled run
    signs in with a password every time, and a copy with none simply stops
    working the moment nobody is watching.

    Which makes asking the right thing to do, and asking HERE the right place:
    the alternative was telling somebody to run two more commands and type the
    same password again, minutes after typing it into the window that just
    closed.
    """
    print(
        "\nOne thing left. A scheduled run has to sign in on its own, and the\n"
        "signed-in session does not survive the browser closing, so it needs\n"
        "your student number and password saved locally to do that.\n\n"
        "They would go in .env, in this folder, which is gitignored and never\n"
        "leaves this machine. Nothing prints them and nothing sends them\n"
        "anywhere. Without them, this only works while you run it by hand.\n"
    )
    if not sys.stdin.isatty():
        print(
            "Not running in a terminal, so nothing was saved. To do it later:\n"
            "    make setup-omnivox"
        )
        return
    try:
        answer = input("Save the credentials you just typed? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nNothing saved. `make setup-omnivox` does it later.")
        return
    if answer and answer not in ("y", "yes", "o", "oui"):
        print("Nothing saved. `make setup-omnivox` does it later.")
        return

    sys.path.insert(0, str(cfg.repo_root / "scripts"))
    from set_env import write_key

    write_key("OMNIVOX_USER", user, cfg.repo_root / ".env")
    write_key("OMNIVOX_PASS", password, cfg.repo_root / ".env")
    print("Saved to .env. Scheduled runs can now sign in without you.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.omnivox_sync",
        description="Download new Omnivox LEA documents into local course folders.",
    )
    parser.add_argument(
        "--config", default=str(REPO_ROOT / "config.yaml"), help="path to config.yaml"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would happen; no downloads, conversions, or state writes",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser (debugging)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--discover",
        action="store_true",
        help="find your courses and write them into config.yaml",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="with --discover, print the block instead of writing the file",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="say whether this is working, and what needs a person",
    )
    group.add_argument("--course", metavar="CODE", help="limit the run to one course code")
    group.add_argument(
        "--retry",
        action="store_true",
        help="run only if a network-failed sync is pending and the internet is back",
    )
    group.add_argument(
        "--login",
        action="store_true",
        help=(
            "one-time interactive bootstrap: opens a visible browser so you can "
            "clear Omnivox's identity validation and tick 'appareil de confiance'"
        ),
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help=(
            "a person pressed the button: report the outcome either way, drain the "
            "upload queue even if nothing is new, and never schedule a later retry"
        ),
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "if another run holds the lock, wait up to N seconds for it to finish "
            "instead of skipping this one"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    repo_root = config_path.resolve().parent
    log = setup_logging("omnivox_sync", repo_root)

    # --- fatal setup errors: nothing ran ---
    try:
        cfg = load_config(config_path, repo_root=repo_root)
        # Fail fast before opening a browser, but on the real precondition:
        # SOMETHING must be able to sign in. A stored session is enough on its
        # own, and is how anyone who signed in by hand is set up.
        #
        # Not for --doctor or --login. Those are the two commands that EXIST to
        # answer and to fix this exact state, and refusing to run them because
        # of it leaves somebody with an error telling them to run the command
        # that just refused.
        needs_a_way_in = not (args.doctor or args.login)
        if needs_a_way_in and not (repo_root / "state" / "omnivox-profile").exists():
            user, password = load_credentials(repo_root, required=False)
            if not (user and password):
                raise ConfigError(
                    "Nothing here can sign in to Omnivox yet: no stored session, "
                    "and no credentials in .env. Sign in once, in a browser:\n"
                    "    make login"
                )
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        notify("School sync failed", str(exc), critical=True)
        return 2


    # One sweep at a time. Every entry point below drives the same Playwright
    # profile in state/omnivox-profile/, and a headless Chromium silently shares
    # a profile with another process rather than refusing, so this lock is the
    # only thing keeping two of them apart.
    try:
        with run_lock(repo_root / "state" / "omnivox.lock", wait=args.wait):
            return _run(args, cfg, log)
    except RunBusy:
        # A skip is not a failure: whoever holds the lock is running this same
        # sweep and their result lands in a moment. Shouting here would page him
        # every time a retry tick happened to meet the 12:15 sync.
        log.info("Another run holds state/omnivox.lock; skipping this one")
        if args.manual:
            notify(
                "School sync",
                "A sync is already running. Its result will arrive shortly.",
                cfg=cfg.notify,
            )
            return LOCK_BUSY
        return 0


def _run_update(cfg: Config, log: logging.Logger) -> None:
    """Self-update, recorded where a person will find it. Never raises."""
    try:
        from src.updater import check_and_apply

        result = check_and_apply(cfg.repo_root)
        store = StateStore(cfg.repo_root / "state" / "last_update.json")
        previous = store.read()
        said_before = bool(previous) and previous[0].get("message") == result.message
        store.write(
            [{
                "at": datetime.now().isoformat(timespec="seconds"),
                "changed": result.changed,
                "blocked": result.blocked,
                "message": result.message,
            }]
        )
        if result.blocked:
            log.warning("Auto-update: %s", result.message)
            # The one case worth interrupting somebody for: their own fix is
            # sitting on their laptop helping nobody, and they do not know it.
            # Once, though. The identical banner three times a day is how the
            # login outage went unnoticed for two days.
            if not said_before:
                notify("School sync: your copy has local changes",
                       result.message, cfg=cfg.notify)
        elif result.changed:
            # Deliberately quiet. The point is that they never think about it.
            log.info("Auto-update: %s", result.message)
        else:
            log.debug("Auto-update: %s", result.message)
    except Exception as exc:  # noqa: BLE001
        log.warning("Auto-update skipped: %s: %s", type(exc).__name__, exc)


def _run(args, cfg: Config, log: logging.Logger) -> int:
    """The sweep itself. Always called with state/omnivox.lock held."""
    if args.manual and not args.login:
        notify("School sync started", "Checking Omnivox...", cfg=cfg.notify)

    if args.doctor:
        return _doctor(cfg)

    if args.login:
        return _bootstrap_login(cfg, log, Path(args.config))

    # Before anything else that can fail, and before the login block, because
    # a release that fixes the thing somebody is stuck on is exactly the
    # release they will otherwise never receive. Never fatal: the fallback is
    # the code that already worked yesterday.
    if cfg.auto_update and not args.dry_run and not args.discover:
        _run_update(cfg, log)

    blocked = login_block(cfg)
    if blocked:
        # Do not touch Omnivox. Every attempt from here asks it to e-mail
        # another six-digit code, which is how the real one gets buried.
        summary = login_block_summary(cfg)
        log.error("Login needs you: %s", summary)
        print(f"BLOCKED: {summary}")
        if args.manual:
            # The failure that created this block already shouted once. Loud
            # three times a day after that is how an alert becomes wallpaper,
            # which is exactly how this went unnoticed for two days. A person
            # who just pressed the button still gets an answer, and the state
            # stays visible in the digest and in `make doctor`.
            notify("Omnivox: run `make login`", summary, critical=True, cfg=cfg.notify)
        block_login(cfg, str(blocked.get("reason", "")))
        return 2

    if args.retry:
        if not retry_due(cfg):
            return 0  # nothing pending, or the next window already superseded it
        if not internet_up(probe_url=build_portal(cfg).home):
            return 0  # still offline; a later tick will catch it
        log.info("Internet is back — retrying the failed sync")

    try:
        with _open_session(cfg, headed=args.headed, logger=log) as session:
            if args.discover:
                courses = session.list_courses()
                log.info("Discovery listed %d courses", len(courses))
                if args.print_only:
                    print(f"# Discovered {len(courses)} courses for {cfg.semester}.")
                    print("# Paste the block below into config.yaml, replacing the")
                    print("# existing course list. Adjust folder names if you like.\n")
                    print(render_discovery_yaml(courses, cfg.semester))
                    return 0
                write_courses(Path(args.config), courses, cfg.semester)
                return 0

            result = sync(
                cfg, session, dry_run=args.dry_run, only_course=args.course, logger=log
            )
    except ConfigError as exc:
        log.error("%s", exc)
        notify("School sync failed", str(exc), critical=True, cfg=cfg.notify)
        return 2
    except Exception as exc:  # noqa: BLE001 - login/browser failures are fatal for the run
        log.exception("Sync aborted")
        if is_network_error(exc):
            # Almost certainly just offline. Arm a retry instead of shouting:
            # the laptop was probably shut on the way to school.
            if args.manual:
                # One press, one attempt. Arming here would fire a full
                # unattended sync and NotebookLM upload hours later, which is
                # the opposite of what pressing a button asks for.
                log.info("Offline; manual run does not arm a retry")
                notify(
                    "School sync",
                    "No internet. Nothing ran, and nothing was scheduled.",
                    cfg=cfg.notify,
                )
                return 3
            arm_retry(cfg, f"{type(exc).__name__}: {exc}")
            log.info("Network failure, retry armed until %s", next_window(datetime.now()))
            return 3
        if isinstance(exc, MfaRequired):
            # Not a failure to retry. A person has to type a code, so stop
            # asking Omnivox for one until they do.
            block_login(cfg, str(exc).splitlines()[0])
            summary = login_block_summary(cfg)
            log.error("Login needs you: %s", summary)
            notify("Omnivox: run `make login`", summary, critical=True, cfg=cfg.notify)
            return 2
        notify(
            "School sync failed",
            f"{type(exc).__name__}: {exc}. See logs/omnivox_sync.log and logs/*.png",
            critical=True,
            cfg=cfg.notify,
        )
        return 2

    if not args.dry_run:
        clear_retry(cfg)  # a real run supersedes any pending retry; a dry one does not
        clear_login_block(cfg)  # we just logged in, so whatever blocked it is over

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}{result.summary()}")
    for item in result.upload_queue:
        print(f"{prefix}queued for upload: {item['course_code']} / {item['filename']}")

    chain_downstream(cfg, result, dry_run=args.dry_run, manual=args.manual, logger=log)

    if result.errors:
        detail = "; ".join(
            f"{human_scope(cfg, e['scope'])}: {e['error']}" for e in result.errors[:3]
        )
        notify("School sync: partial failure", detail, critical=True, cfg=cfg.notify)
        return 1

    if result.conversion_failures:
        names = ", ".join(Path(f["source"]).name for f in result.conversion_failures[:3])
        notify("School sync: conversion failed", names, cfg=cfg.notify)

    if args.manual and not result.downloaded and not args.dry_run:
        notify("School sync", "Up to date. Nothing new.", cfg=cfg.notify)

    # Silence when nothing is new (spec section 9).
    if result.downloaded and not args.dry_run:
        courses_touched = human_courses(cfg, {d["course_code"] for d in result.downloaded})
        notify(
            "School sync",
            f"{len(result.downloaded)} new document(s): {courses_touched}",
            cfg=cfg.notify,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
