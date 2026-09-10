"""Teacher announcements, saved into the course folder they belong to.

A communiqué is what a teacher sends to a whole class, and it is where the
actual instruction often lives. Two real ones from a single course:

    "Se procurer l'ouvrage le Gorgias de Platon à la COOP"
    "À compléter pour le prochain cours (examen)"

Neither is a document, so neither was ever downloaded, and neither appeared in
any folder or notebook. The word "communiqué" did not occur anywhere in this
project's source: they were not being missed intermittently, they had never
been looked at.

They live on the LÉA landing page, in a panel on each course card, so one page
load returns them for every course at once. The body sits behind an ordinary
URL, which is fetched rather than clicked: clicking opens a centred popup, and
a popup is one more thing to go wrong for nothing.

Written as Markdown next to the course documents, which means they reach
NotebookLM through the same queue as everything else. Asking a notebook "what
did the teacher say to buy" now has an answer.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.common import StateStore

#: One row per communiqué already saved, so a run does not rewrite the file
#: and does not re-queue it for upload.
STATE_FILE = "communiques.json"


def _key(course_code: str, title: str, date: str) -> str:
    return f"{course_code}\x1f{title}\x1f{date}"


def safe_name(text: str, limit: int = 80) -> str:
    """A filename that survives every filesystem this runs on.

    The same rules as the document filenames: NTFS refuses : * ? " < > | and
    a trailing dot, and a communiqué title is free text a teacher typed.
    """
    cleaned = re.sub(r'[:*?"<>|/\\\x00-\x1f]', " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return (cleaned[:limit].strip() or "communique")


#: Chrome the reader page ends with. It is a button, not content, and it was
#: landing at the bottom of every saved file.
_TRAILING_CHROME = {"fermer", "close", "imprimer", "print", "retour"}


def full_title(body: str, fallback: str) -> str:
    """The title as the communiqué itself states it.

    The course card truncates: "À compléter pour le prochain cours (e..."
    became a FILENAME ending in "(e". The body's first line carries it whole.
    """
    for line in (body or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return fallback


def strip_chrome(lines: list[str]) -> list[str]:
    """Drop the reader's own buttons off the end of the text."""
    while lines and (
        not lines[-1].strip() or lines[-1].strip().lower() in _TRAILING_CHROME
    ):
        lines.pop()
    return lines


def render(course_code: str, title: str, author: str, date: str, body: str) -> str:
    """The Markdown a notebook and a person both read."""
    head = [f"# {title}", ""]
    meta = [p for p in (course_code, author, date) if p]
    if meta:
        head.append("*" + " · ".join(meta) + "*")
        head.append("")
    # The body repeats its own header: title, course code, course name, then
    # "Publié par X le DATE". All of that is in the metadata above by now.
    # That last line always closes the block, so cut there rather than trying
    # to recognise each line: the course NAME is in there too and this
    # function has only ever been given the code.
    lines = (body or "").splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^\s*Publi[ée]e?\s+par\b", line, re.I):
            lines = lines[index + 1:]
            break
    else:
        while lines and (
            not lines[0].strip()
            or lines[0].strip() == title
            or lines[0].strip().startswith(course_code)
        ):
            lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(head + strip_chrome(lines)).strip() + "\n"


def sync_communiques(cfg, driver, *, dry_run: bool = False, logger=None) -> list[dict]:
    """Save every new communiqué. Returns entries for the upload queue.

    Never raises: an announcement is worth having and is worth nothing at the
    cost of the documents, which are the run's actual job.
    """
    log = logger or logging.getLogger("school.communiques")
    store = StateStore(cfg.repo_root / "state" / STATE_FILE)
    seen = {r.get("key") for r in store.read()}
    by_code = {c.code: c for c in cfg.courses}
    queued: list[dict] = []
    fresh: list[dict] = []

    try:
        found = driver.list_communiques()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read the communiqués: %s", exc)
        return []

    for code, items in (found or {}).items():
        course = by_code.get(code)
        if course is None:
            continue
        folder = cfg.folder_for(course) / cfg.communiques_folder
        for item in items:
            try:
                body = driver.fetch_communique(item["url"])
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: could not fetch a communiqué: %s", code, exc)
                continue
            author, published = driver.communique_meta(body)
            # The body's own first line, not the card's, which is truncated.
            item["title"] = full_title(body, item.get("title") or "Communiqué")
            # The body's own date beats the card's, which arrives glued to the
            # title and truncated.
            date = published or item.get("date") or ""
            title = item.get("title") or "Communiqué"
            key = _key(code, title, date)
            if key in seen:
                continue

            name = f"{date} - {safe_name(title)}.md" if date else f"{safe_name(title)}.md"
            path = folder / name
            if dry_run:
                log.info("[dry-run] would save %s", path)
            else:
                folder.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    render(code, title, author, date, body), encoding="utf-8"
                )
                log.info("Communiqué saved: %s/%s", course.folder, name)
            fresh.append({"key": key, "course_code": code, "title": title, "date": date})
            queued.append({
                "course_code": code,
                "notebook": course.notebook,
                "path": str(path),
                "filename": name,
            })

    if fresh and not dry_run:
        store.write(store.read() + fresh)
    return queued
