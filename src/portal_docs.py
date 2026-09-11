"""College documents, as opposed to course documents.

The policies, guides and forms a cégep publishes to everybody: the intranet
rules, the technology regulation, the sollicitation guide. They live in
Omnivox "communautés", which are portal pages rather than anything in LÉA, so
nothing that walks courses has ever seen one. That is why the folder for
everything-that-is-not-a-course sat empty all semester while the portal was
carrying a dozen documents.

They go to the same folder as the digest, beside the courses rather than
inside one, because they belong to none of them.

What this does NOT get, said plainly because it is the thing somebody will
look for first: the year's academic calendar. At this college that is a page
on the public website, not a file on Omnivox, so there is nothing here to
fetch. An AI can open it and read it; this cannot download it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.common import StateStore

STATE_FILE = "portal_docs.json"


def safe_name(text: str, limit: int = 90) -> str:
    cleaned = re.sub(r'[:*?"<>|/\\\x00-\x1f]', " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned[:limit].strip() or "document"


def destination(folder: Path, name: str) -> Path:
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    for n in range(2, 100):
        candidate = folder / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
    return folder / f"{stem} (x){suffix}"


def sync_portal_documents(cfg, driver, *, dry_run: bool = False,
                          logger=None) -> list[dict]:
    """Download the college's own documents. Returns upload queue entries."""
    log = logger or logging.getLogger("school.portal_docs")
    store = StateStore(cfg.repo_root / "state" / STATE_FILE)
    known = {r.get("ref") for r in store.read()}
    folder = cfg.digest_dir()
    queued: list[dict] = []
    fresh: list[dict] = []

    try:
        documents = driver.list_portal_documents()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read the portal documents: %s", exc)
        return []

    for document in documents or []:
        ref = document.get("ref") or ""
        if not ref or ref in known:
            continue
        name = safe_name(document.get("title") or "")
        if not Path(name).suffix:
            name += Path(ref.split("?")[0]).suffix or ".pdf"
        path = destination(folder, name)
        if dry_run:
            log.info("[dry-run] would fetch %s", name)
        else:
            try:
                folder.mkdir(parents=True, exist_ok=True)
                # The community page the link sits on, sent because the
                # rest of this project sends one. It does not lift a 403: a
                # forbidden document stays forbidden either way.
                community = document.get("community") or ""
                referer = (
                    f"{getattr(driver, 'portal', None) and driver.portal.home}"
                    f"/intr/{community}/"
                    if community
                    else ""
                )
                driver.fetch_portal_document(ref, path, referer)
                log.info("College document: %s", name)
            except Exception as exc:  # noqa: BLE001 - one file, not the run
                # 403 is the common one and it is not a fault: a community can
                # list documents the reader is not entitled to, and saying
                # "could not fetch" about those reads as breakage.
                if "403" in str(exc):
                    log.info("Not available to this account: %s", name)
                else:
                    log.warning("Could not fetch %s: %s", name, exc)
                continue
        fresh.append({"ref": ref, "name": name})
        queued.append({
            "course_code": "",
            "notebook": "",
            "path": str(path),
            "filename": name,
        })

    if fresh and not dry_run:
        store.write(store.read() + fresh)
    return queued
