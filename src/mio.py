"""MIO messages from your own teachers, filed into their course folders.

Omnivox's internal mail is where a teacher says the thing that is not on any
document: what to buy, what changed, what to have read by Thursday. It arrives
in one flat inbox mixed with student-association blasts, and it never reached
the folders, so asking a notebook "what did my Recherche qualitative teacher
say" had no answer.

This saves the ones from a teacher named in `config.yaml`, into that course's
folder. Everything else in the inbox is ignored on purpose: an invitation to a
socioculturelle activity is not course material, and filing it next to the
lecture slides makes the folder worse.

**Nothing here opens a message, so nothing is marked read.** That is a
deliberate limit and it costs something: what gets saved is the preview LEA
shows in the list, which is long but truncated. It is usually the whole
instruction ("Pour le prochain cours, vous devez lire la section 2.5.3 du
manuel et") and sometimes cuts mid-sentence. Opening messages to get the rest
would silently mark an inbox read, which is not a thing a background job
should do to somebody.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

from src.common import StateStore

STATE_FILE = "mio.json"

#: A French letter almost always opens with one of these, and the subject sits
#: in front of it with no punctuation between, because the list cell glues the
#: subject and the body together with a single space.
_GREETINGS = (
    "bonjour", "bonsoir", "allo", "allô", "salut", "tres cher", "très cher",
    "chers", "cher", "chere", "chère", "bon debut", "bon début", "ne pas repondre",
)


def _fold(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in stripped if not unicodedata.combining(c)).lower()


def split_subject(preview: str, limit: int = 90) -> tuple[str, str]:
    """("Rappel du devoir", "Bonjour tout le monde, ...") out of one glued cell.

    LEA renders the subject and the start of the body into a single cell with
    nothing between them, so there is no separator to split on. A French
    letter's opening greeting is the most reliable boundary there is here, and
    when there is none the first clause has to do.
    """
    text = (preview or "").strip()
    if not text:
        return "", ""
    folded = _fold(text)
    best = None
    for greeting in _GREETINGS:
        at = folded.find(greeting)
        # Only if it is plausibly past the subject, not inside the first word.
        if at > 2 and (best is None or at < best):
            best = at
    if best is not None:
        return text[:best].strip(" ,:;-–"), text[best:].strip()
    return text[:limit].strip(" ,:;-–"), text


def safe_name(text: str, limit: int = 80) -> str:
    cleaned = re.sub(r'[:*?"<>|/\\\x00-\x1f]', " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned[:limit].strip() or "message"


def match_course(sender: str, courses) -> object | None:
    """The course whose teacher sent this, or None.

    Compared on folded text so that an accent typed one way in config and
    another way by Omnivox still matches, which is not hypothetical in a list
    of names like Vandenbossche-Makombo.
    """
    who = _fold(sender).strip()
    if not who:
        return None
    for course in courses:
        teacher = _fold(getattr(course, "teacher", "") or "").strip()
        if teacher and (teacher == who or teacher in who or who in teacher):
            return course
    return None


def render(sender: str, subject: str, date: str, body: str, course_code: str) -> str:
    head = [f"# {subject or 'Message'}", ""]
    meta = [p for p in (course_code, sender, date) if p]
    if meta:
        head += ["*" + " · ".join(meta) + "*", ""]
    head += [
        body.strip(),
        "",
        "---",
        "",
        "*Saved from the Omnivox inbox listing, so this may be cut off. The "
        "full message is in Omnivox; it is not opened here because opening it "
        "would mark it read.*",
    ]
    return "\n".join(head).strip() + "\n"


def sync_mio(cfg, driver, *, dry_run: bool = False, logger=None) -> list[dict]:
    """Save new teacher MIO into course folders. Returns upload queue entries."""
    log = logger or logging.getLogger("school.mio")
    store = StateStore(cfg.repo_root / "state" / STATE_FILE)
    known = {r.get("key") for r in store.read()}
    queued: list[dict] = []
    fresh: list[dict] = []

    try:
        messages = driver.list_mio()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read MIO: %s", exc)
        return []

    for message in messages or []:
        course = match_course(message.get("sender", ""), cfg.courses)
        if course is None:
            continue  # not a teacher of yours; the inbox is full of those
        subject, body = split_subject(message.get("preview", ""))
        date = message.get("date") or ""
        key = message.get("id") or f"{message.get('sender')}\x1f{subject}\x1f{date}"
        if key in known:
            continue

        name = f"{date} - {safe_name(subject)}.md" if date else f"{safe_name(subject)}.md"
        folder = cfg.folder_for(course) / cfg.mio_folder
        path = folder / name
        if dry_run:
            log.info("[dry-run] would save %s", path)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(
                render(message.get("sender", ""), subject, date, body, course.code),
                encoding="utf-8",
            )
            log.info("MIO saved: %s/%s", course.folder, name)
        fresh.append({"key": key, "course_code": course.code, "subject": subject})
        queued.append({
            "course_code": course.code,
            "notebook": course.notebook,
            "path": str(path),
            "filename": name,
        })

    if fresh and not dry_run:
        store.write(store.read() + fresh)
    return queued
