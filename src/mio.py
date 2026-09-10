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


#: Buttons and labels the detail pane renders around the message. None of it
#: is content, and one of them is the recipient list, which is the reader's own
#: name repeated into every file.
_DETAIL_CHROME = {
    "de", "à", "a", "date", "répondre", "repondre", "transférer", "transferer",
    "supprimer", "imprimer", "à (masqués)", "a (masques)", "objet", "sujet",
    "fermer", "retour",
}


def parse_detail(text: str) -> dict:
    """Pull sender, date, subject and body out of an opened message.

    The pane is laid out as label-then-value on separate lines:

        De / Jordan Tremblay (340-101-MQ gr.1060 (A2026))
        Répondre / Transférer / Supprimer / Imprimer
        À (masqués) / <your own name>
        Date / Jeu 10-sep-2026 à 11:09 - il y a 2 heures
        <subject>
        <body...>

    So the subject is the first line after the date's value, and everything
    after that is the message. The recipient block is dropped rather than
    saved: it is the reader's own name, in every single file.
    """
    lines = [l.rstrip() for l in (text or "").splitlines()]
    out = {"sender": "", "date": "", "subject": "", "body": ""}
    index = 0
    while index < len(lines):
        label = lines[index].strip().lower().rstrip(":")
        value = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if label == "de" and value:
            out["sender"] = value
            index += 2
            continue
        if label == "date" and value:
            out["date"] = value
            index += 2
            # What follows the date is the subject, then the message.
            rest = [l for l in lines[index:]]
            while rest and not rest[0].strip():
                rest.pop(0)
            if rest:
                out["subject"] = rest[0].strip()
                rest = rest[1:]
            body = [l for l in rest if l.strip().lower() not in _DETAIL_CHROME]
            while body and not body[0].strip():
                body.pop(0)
            while body and not body[-1].strip():
                body.pop()
            out["body"] = "\n".join(body).strip()
            return out
        index += 1
    return out


def detail_date(text: str) -> str:
    """"Jeu 10-sep-2026 à 11:09 - il y a 2 heures" -> "2026-09-10".

    The detail pane hyphenates where the rest of Omnivox uses spaces, and the
    shared date parser only takes the spaced form, so this un-hyphenates
    before handing it over rather than teaching that parser a shape only this
    one page uses.
    """
    from src.omnivox import parse_publish_date

    found = re.search(r"(\d{1,2})[-\s]([A-Za-zéûî]+)[-\s](\d{4})", text or "")
    if not found:
        return ""
    return parse_publish_date(" ".join(found.groups())) or ""


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


def clean_sender(sender: str) -> str:
    """"Jordan Tremblay (340-101-MQ gr.1060 (A2026))" -> "Jordan Tremblay".

    The detail pane appends the course to the name, and the byline already
    carries the course code, so leaving it gives every file a line reading
    "340-101-MQ · Jordan Tremblay (340-101-MQ gr.1060 (A2026))".
    """
    return re.sub(r"\s*\([^)]*\d{3}-[A-Z0-9]{3}-[A-Z]{2}.*$", "", sender or "").strip()


def render(sender: str, subject: str, date: str, body: str, course_code: str,
           *, truncated: bool = True) -> str:
    head = [f"# {subject or 'Message'}", ""]
    meta = [p for p in (course_code, clean_sender(sender), date) if p]
    if meta:
        head += ["*" + " · ".join(meta) + "*", ""]
    head.append(body.strip())
    if truncated:
        # Only when this really is the listing preview. Saying "may be cut off"
        # on a complete message would teach people to distrust the ones that
        # are fine.
        head += [
            "",
            "---",
            "",
            "*Saved from the Omnivox inbox listing, so this may be cut off. The "
            "full message is in Omnivox; it is not opened here because opening "
            "it would mark it read. Set `mio: full_bodies: true` to save the "
            "whole thing instead.*",
        ]
    return "\n".join(head).strip() + "\n"


def sync_mio(cfg, driver, *, dry_run: bool = False, logger=None) -> list[dict]:
    """Save new teacher MIO into course folders. Returns upload queue entries."""
    log = logger or logging.getLogger("school.mio")
    store = StateStore(cfg.repo_root / "state" / STATE_FILE)
    known = {r.get("key") for r in store.read()}
    queued: list[dict] = []
    fresh: list[dict] = []

    full = bool(getattr(cfg, "mio_full_bodies", False))
    try:
        messages = driver.list_mio()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read MIO: %s", exc)
        return []

    for message in messages or []:
        course = match_course(message.get("sender", ""), cfg.courses)
        if course is None:
            continue  # not a teacher of yours; the inbox is full of those

        # Everything above this line is free. Opening a message is not: it
        # costs seconds and it marks the message read, so it happens only for
        # one that is from a teacher AND has not been saved already.
        preview_key = (
            message.get("id")
            or f"{message.get('sender')}\x1f{message.get('preview', '')[:60]}"
        )
        if preview_key in known:
            continue

        detail = {}
        if full:
            try:
                body, code = driver.read_mio_body(message.get("id", ""))
            except Exception as exc:  # noqa: BLE001 - one bad row, not the run
                log.warning("Could not open a MIO: %s", exc)
                body, code = "", ""
            if body:
                detail = parse_detail(body)
                # The opened message names its own course, which beats matching
                # a teacher's name against a sender string.
                by_code = {c.code: c for c in cfg.courses}
                course = by_code.get(code) or course
        if detail.get("body"):
            subject = detail.get("subject") or ""
            body = detail["body"]
            date = detail_date(detail.get("date", "")) or message.get("date") or ""
        else:
            subject, body = split_subject(message.get("preview", ""))
            date = message.get("date") or "" 
        key = preview_key

        name = f"{date} - {safe_name(subject)}.md" if date else f"{safe_name(subject)}.md"
        folder = cfg.folder_for(course) / cfg.mio_folder
        path = folder / name
        if dry_run:
            log.info("[dry-run] would save %s", path)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(
                render(
                    detail.get("sender") or message.get("sender", ""),
                    subject, date, body, course.code,
                    truncated=not detail.get("body"),
                ),
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
