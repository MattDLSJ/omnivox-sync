"""Milestone 3: the daily digest.

This module is load-bearing, not cosmetic. The sync clears Omnivox's "new
document" indicators as a side effect, which removes the user's existing
forcing function for noticing things. The digest replaces it, so anything the
pipeline saw must end up here (spec sections 2 and 9).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from src.common import Config, StateStore

# Spec section 9. Matched case- and accent-insensitively, so "evaluation",
# "Évaluation" and "ÉVALUATION" all hit. Easy to extend over the semester.
IMPORTANT_KEYWORDS = (
    "examen",
    "evaluation",
    "test",
    "remise",
    "echeance",
    "reporte",
    "annule",
    "changement",
    "obligatoire",
    "local",
    "absence",
    "retard",
)

KIND_LABEL = {
    "document": "📄",
    "mio": "✉️",
    "quoi_de_neuf": "🆕",
    "event": "📅",
    "link": "🔗",
    "flag": "⚠️",
}

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)


@dataclass(frozen=True)
class DigestItem:
    kind: str  # document | mio | quoi_de_neuf | event | link | flag
    title: str
    course: str = ""  # course code, or "" for portal-wide
    date: str = ""
    detail: str = ""
    important: bool = False
    reason: str = ""


@dataclass
class DigestResult:
    items: list[DigestItem] = field(default_factory=list)
    important: list[DigestItem] = field(default_factory=list)
    notification: tuple[str, str] | None = None
    digest_path: Path | None = None

    def summary(self) -> str:
        return f"{len(self.items)} new item(s), {len(self.important)} important"


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text or "") if unicodedata.category(c) != "Mn"
    ).casefold()


def item_hash(item: DigestItem) -> str:
    """Stable identity for dedup: (kind, course, title, date). Spec section 9."""
    raw = "|".join([item.kind, item.course, " ".join(item.title.split()), item.date])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def rank_rules(items: list[DigestItem]) -> list[DigestItem]:
    """Keyword ranking (spec section 9, default backend)."""
    ranked = []
    for item in items:
        haystack = strip_accents(f"{item.title} {item.detail}")
        hit = next((k for k in IMPORTANT_KEYWORDS if k in haystack), None)
        ranked.append(
            replace(item, important=bool(hit), reason=f"keyword: {hit}" if hit else "")
        )
    return ranked


def rank_gemini(items: list[DigestItem], api_key: str, *, timeout: int = 30) -> list[DigestItem]:
    """Optional Gemini Flash ranking. Any failure is the caller's cue to
    fall back to rules; this raises rather than guessing."""
    numbered = [
        {"id": i, "kind": it.kind, "course": it.course, "title": it.title[:200]}
        for i, it in enumerate(items)
    ]
    prompt = (
        "You rank cegep course announcements for a student. Return ONLY a JSON "
        "array, no prose, no markdown fence. For each input item return "
        '{"id": <int>, "important": <bool>, "reason": "<max 8 words>"}. '
        "Important means it needs action or has a deadline: exams, evaluations, "
        "submissions, due dates, cancellations, room changes, mandatory events. "
        "Routine course material is not important.\n\nItems:\n"
        + json.dumps(numbered, ensure_ascii=False)
    )
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    request = urllib.request.Request(
        f"{GEMINI_URL}?key={api_key}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    text = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    verdicts = {v["id"]: v for v in json.loads(text)}

    ranked = []
    for index, item in enumerate(items):
        verdict = verdicts.get(index, {})
        ranked.append(
            replace(
                item,
                important=bool(verdict.get("important", False)),
                reason=str(verdict.get("reason", ""))[:60],
            )
        )
    return ranked


def rank(
    items: list[DigestItem],
    backend: str = "rules",
    *,
    api_key: str | None = None,
    logger: logging.Logger | None = None,
) -> list[DigestItem]:
    log = logger or logging.getLogger("school.digest")
    if backend == "gemini" and api_key:
        try:
            return rank_gemini(items, api_key)
        except Exception as exc:  # noqa: BLE001 - spec: fall back to rules on any error
            # Name the model. This endpoint pins one by name, model names get
            # retired, and a failure that only ever says "it failed" is how a
            # dead one goes unnoticed for a whole semester behind a fallback
            # that works fine.
            log.warning(
                "Gemini ranking failed (%s) at %s; falling back to keyword rules. "
                "If this repeats, the model name in GEMINI_URL may have been "
                "retired.",
                exc,
                GEMINI_URL.rsplit("/", 1)[-1].split(":")[0],
            )
    elif backend == "gemini":
        log.warning("digest.ranking is 'gemini' but GEMINI_API_KEY is unset; using rules")
    return rank_rules(items)


# --------------------------------------------------------------------------
# Dedup / assembly
# --------------------------------------------------------------------------


def items_from_observations(raw: list[dict]) -> list[DigestItem]:
    """Convert omnivox.collect_announcements() output into DigestItems."""
    return [
        DigestItem(
            kind=r.get("kind", "quoi_de_neuf"),
            title=(r.get("title") or "").strip(),
            course=r.get("course", ""),
            date=r.get("date", ""),
            detail=(r.get("detail") or "").strip(),
        )
        for r in raw
        if (r.get("title") or "").strip()
    ]


def items_from_sync(result) -> list[DigestItem]:
    """Everything M1 did this run, so no downloaded file is invisible."""
    items: list[DigestItem] = []
    for record in getattr(result, "downloaded", []):
        converted = record.get("converted_pdf")
        items.append(
            DigestItem(
                kind="document",
                title=record["filename"],
                course=record.get("course_code", ""),
                date=record.get("publish_date", ""),
                detail=(record.get("description") or "")
                + (f" (converted to {Path(converted).name})" if converted else ""),
            )
        )
    for link in getattr(result, "links", []):
        items.append(
            DigestItem(
                kind="link",
                title=link.get("description") or link.get("filename", ""),
                course=link.get("course_code", ""),
                date=link.get("publish_date", ""),
                detail="web link posted by the teacher",
            )
        )
    for failure in getattr(result, "conversion_failures", []):
        items.append(
            DigestItem(
                kind="flag",
                title=f"conversion failed: {Path(failure['source']).name}",
                detail=str(failure.get("error", ""))[:160],
            )
        )
    for error in getattr(result, "errors", []):
        items.append(
            DigestItem(
                kind="flag",
                title=f"sync error: {error.get('scope', '')}",
                detail=str(error.get("error", ""))[:160],
            )
        )
    return items


def select_new(items: list[DigestItem], seen: set[str]) -> list[DigestItem]:
    """Drop anything already reported, and de-duplicate within this run."""
    fresh: list[DigestItem] = []
    batch: set[str] = set()
    for item in items:
        digest = item_hash(item)
        if digest in seen or digest in batch:
            continue
        batch.add(digest)
        fresh.append(item)
    return fresh


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def format_notification(
    items: list[DigestItem], labels: dict[str, str] | None = None
) -> tuple[str, str] | None:
    """Spec section 9: '⚠ 1 important · 3 docs (Calcul, Éco) · 2 MIO'.

    Returns (title, body), or None when there is nothing new — silence means
    "checked, nothing new".
    """
    if not items:
        return None

    labels = labels or {}
    name = lambda code: labels.get(code, code)  # noqa: E731

    important = [i for i in items if i.important]
    docs = [i for i in items if i.kind == "document"]
    mios = [i for i in items if i.kind == "mio"]
    others = [i for i in items if i.kind in ("quoi_de_neuf", "event", "link")]
    flags = [i for i in items if i.kind == "flag"]

    parts = []
    if important:
        parts.append(f"⚠ {len(important)} important")
    if docs:
        courses = sorted({name(d.course) for d in docs if d.course})
        suffix = f" ({', '.join(courses)})" if courses else ""
        parts.append(f"{len(docs)} doc{'s' if len(docs) != 1 else ''}{suffix}")
    if mios:
        parts.append(f"{len(mios)} MIO")
    if others:
        parts.append(f"{len(others)} annonce{'s' if len(others) != 1 else ''}")
    if flags:
        parts.append(f"{len(flags)} problème{'s' if len(flags) != 1 else ''}")

    title = " · ".join(parts) if parts else f"{len(items)} nouveau(x)"
    body = important[0].title if important else (items[0].title if items else "")
    if important and len(important) > 1:
        body = f"{important[0].title}  (+{len(important) - 1})"
    return title[:200], body[:240]


def _append_block(path: Path, text: str) -> None:
    """Append one dated block to the digest in a single O_APPEND write.

    `open("a").write()` was not visibly wrong: "a" is already O_APPEND, and
    CPython passes a write bigger than its 8 KB buffer straight to one raw
    write(). But that atomicity was a property of the buffering, not something
    the code asked for, and it is the kind of thing that quietly stops being
    true. The digest is never rewritten, so a torn block would stay torn for
    the rest of the semester, and it sits in the Google Drive mirror, so Drive
    would upload the damage.

    So: one explicit O_APPEND syscall, and an fsync, because this runs under
    launchd and the Mac can sleep with the block still in the page cache.

    The loop only matters on a short write, which a few KB to a local file will
    not do in practice; a crash inside it could still tear.
    """
    blob = text.encode("utf-8")
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        written = 0
        while written < len(blob):
            written += os.write(handle, blob[written:])
        os.fsync(handle)
    finally:
        os.close(handle)


def append_digest(
    base_path: Path, items: list[DigestItem], labels: dict[str, str] | None = None
) -> Path:
    """Append a dated section to {base_path}/_digest.md with full detail.

    This file doubles as the memory a future 'ask the hub' agent reads, so it
    keeps everything, not just the important items.
    """
    base_path = Path(base_path)
    base_path.mkdir(parents=True, exist_ok=True)
    path = base_path / "_digest.md"

    labels = labels or {}
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n## {stamp}\n"]

    important = [i for i in items if i.important]
    if important:
        lines.append("**À noter :**\n")
        for item in important:
            where = f"**{labels.get(item.course, item.course)}** " if item.course else ""
            why = f"  _{item.reason}_" if item.reason else ""
            lines.append(f"- ⚠️ {where}{item.title}{why}")
        lines.append("")

    for kind in ("document", "link", "mio", "quoi_de_neuf", "event", "flag"):
        group = [i for i in items if i.kind == kind and not i.important]
        if not group:
            continue
        icon = KIND_LABEL.get(kind, "•")
        for item in group:
            where = f"**{labels.get(item.course, item.course)}** " if item.course else ""
            when = f" _{item.date or item.detail}_" if (item.date or item.detail) else ""
            lines.append(f"- {icon} {where}{item.title}{when}")

    _append_block(path, "\n".join(lines) + "\n")
    return path


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def run_digest(
    cfg: Config,
    observations: list[dict],
    sync_result=None,
    *,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
) -> DigestResult:
    """Rank, dedup, record and report everything observed this run."""
    log = logger or logging.getLogger("school.digest")
    result = DigestResult()

    items = items_from_observations(observations)
    if sync_result is not None:
        items = items_from_sync(sync_result) + items

    store = StateStore(cfg.repo_root / "state" / "announcements.json")
    seen = {r["hash"] for r in store.read() if r.get("hash")}
    fresh = select_new(items, seen)

    if not fresh:
        log.info("Digest: nothing new (checked %d item(s))", len(items))
        return result

    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip() or _env_key(cfg)
    fresh = rank(fresh, cfg.digest_ranking, api_key=api_key, logger=log)

    result.items = fresh
    result.important = [i for i in fresh if i.important]
    result.notification = format_notification(fresh, cfg.labels())

    if dry_run:
        log.info("[dry-run] digest would report: %s", result.summary())
        return result

    result.digest_path = append_digest(cfg.digest_dir(), fresh)
    stamp = datetime.now().isoformat(timespec="seconds")
    store.write(
        store.read()
        + [
            {
                "hash": item_hash(i),
                "kind": i.kind,
                "course": i.course,
                "title": i.title,
                "date": i.date,
                "important": i.important,
                "seen_at": stamp,
            }
            for i in fresh
        ]
    )
    log.info("Digest: %s", result.summary())
    return result


def _env_key(cfg: Config) -> str | None:
    """GEMINI_API_KEY from .env, when digest.ranking is 'gemini'."""
    if cfg.digest_ranking != "gemini":
        return None
    try:
        from dotenv import dotenv_values

        return (dotenv_values(cfg.repo_root / ".env").get("GEMINI_API_KEY") or "").strip() or None
    except Exception:  # noqa: BLE001
        return None
