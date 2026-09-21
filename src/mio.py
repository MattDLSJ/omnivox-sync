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

from dataclasses import dataclass

from src.common import StateStore, notify

STATE_FILE = "mio.json"
#: Every message already announced, so each one rings exactly once. Separate
#: from STATE_FILE, which only ever held the teacher messages that were filed.
SEEN_FILE = "mio_seen.json"

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
    out = {"sender": "", "date": "", "subject": "", "body": "", "direct": False}
    index = 0
    while index < len(lines):
        label = lines[index].strip().lower().rstrip(":")
        value = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if label == "de" and value:
            out["sender"] = value
            index += 2
            continue
        # "À (masqués)" is a message to a group whose names are hidden; a bare
        # "À" is followed by the recipients, which for a message to you alone is
        # just you. Kept as a yes/no, never as the name.
        if label in ("à", "a") and value:
            out["direct"] = True
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


# ------------------------------------------------------------ importance
#
# Fixed rules, no model: asked for that way, and it keeps every message on the
# Mac. Matched on the accent-folded text so "échéance" and "echeance", or a
# teacher's missing accents, count the same.

_CHANGE = re.compile(
    r"\b(annul\w*|report\w*|deplac\w*|changement\w*|modifi\w*|remplac\w*"
    r"|pas de cours|en ligne|a distance|nouveau local|conge)\b"
)
_EVALUATION = re.compile(
    r"\b(examens?|mini-tests?|tests?|quiz|evaluations?|remises?|remettre|ponder\w*"
    # What may be brought in is always about an evaluation: "documents
    # autorisés pour jeudi" came the day before a 15 % one.
    r"|autorise\w*|documents? permis)\b"
    r"|\b\d{1,3} ?%"
)
_DEADLINE = re.compile(
    r"\b(\d{1,2}(er)? (janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre"
    r"|octobre|novembre|decembre)|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche"
    r"|demain|aujourd'hui|ce soir|cette semaine|semaine prochaine|prochain cours"
    r"|date limite|echeance|au plus tard|avant le|d'ici)\b"
)
#: A deadline stated as one is enough on its own; "jeudi" alone is not.
_HARD_DEADLINE = re.compile(r"\b(date limite|echeance|au plus tard|derniere journee)\b")
# Not "rappel": half the college's mass mail opens with it, including "the help
# desk is open today", and it rang as loudly as a moved exam.
_ACTION = re.compile(
    r"\b(obligatoire|important|urgent|n'oubli\w*|vous devez|tu dois"
    r"|a completer|a faire|a lire|apporte\w*|prepare\w*|inscri\w*|confirme\w*)\b"
)
#: College services whose messages are about you: the CSA, the API, the CAF,
#: the registrar, financial aid. Matched on the sender, never on the body.
_STAFF = re.compile(
    r"\b(csa|services? adaptes?|api|caf|registrariat|aide financiere|cheminement"
    r"|aide pedagogique|conseill\w*|cegep)\b"
)
#: Below this, a message written to you by name is a "merci" or an "ok".
_PERSONAL_WORDS = 25


@dataclass(frozen=True)
class Importance:
    important: bool
    reasons: tuple[str, ...]
    #: The sentence that made it important, or the opening one when nothing did.
    excerpt: str


def _plain(text: str) -> str:
    return _fold(text).replace("’", "'")


def is_staff(sender: str, cfg) -> bool:
    """Your teachers, the college's services, and anyone listed in `mio: staff:`."""
    if match_course(sender or "", cfg.courses) is not None:
        return True
    folded = _plain(sender)
    if _STAFF.search(folded):
        return True
    return any(_plain(name) in folded for name in getattr(cfg, "mio_staff", ()) if name)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip() for p in parts if len(p.strip()) > 3]


def assess(subject: str, body: str, *, staff: bool, direct: bool) -> Importance:
    """Is this worth interrupting you for, why, and which sentence says so.

    Only staff can be important: a classmate's "c'est obligatoire!" about a
    party is not. From staff, any change to a class or any evaluation counts; a
    reminder counts when it carries a date; and a real message written to you
    alone counts on its own, because that is how a decision on your file
    arrives.
    """
    text = f"{subject}. {body}" if subject else body
    folded = _plain(text)
    change = bool(_CHANGE.search(folded))
    evaluation = bool(_EVALUATION.search(folded))
    deadline = bool(_DEADLINE.search(folded))
    hard_deadline = bool(_HARD_DEADLINE.search(folded))
    action = bool(_ACTION.search(folded))
    personal = direct and len((body or "").split()) >= _PERSONAL_WORDS

    reasons = []
    if change:
        reasons.append("changement")
    if evaluation:
        reasons.append("évaluation")
    if deadline:
        reasons.append("échéance")
    if action:
        reasons.append("à faire")
    if personal:
        reasons.append("t'est adressé")
    important = staff and (
        change or evaluation or hard_deadline or (action and deadline) or personal
    )

    excerpt = ""
    sentences = _sentences(body)
    for rule in (_CHANGE, _EVALUATION, _DEADLINE, _ACTION):
        at = next((n for n, s in enumerate(sentences) if rule.search(_plain(s))), None)
        if at is not None:
            excerpt = sentences[at]
            # "Voici la liste :" is only worth quoting with the list after it.
            if excerpt.endswith(":"):
                items = [s.lstrip("-•* ").strip() for s in sentences[at + 1:at + 4]]
                excerpt = f"{excerpt} {' ; '.join(i for i in items if i)}"
            break
    if not excerpt:
        opening = [s for s in _sentences(body) if not _plain(s).startswith(("bonjour", "bonsoir", "salut"))]
        excerpt = opening[0] if opening else ""
    if len(excerpt) > 180:
        excerpt = excerpt[:177].rstrip() + "..."
    return Importance(important, tuple(reasons) if important else (), excerpt)


def _announce(cfg, sender: str, subject: str, body: str, *, staff: bool,
              direct: bool, log) -> None:
    verdict = assess(subject, body, staff=staff, direct=direct)
    title = f"MIO · {clean_sender(sender) or 'nouveau message'}"[:100]
    lines = [subject or "(sans objet)"]
    if verdict.important:
        lines.append(f"⚠ {', '.join(verdict.reasons)} : {verdict.excerpt}".strip(" :"))
    elif verdict.excerpt and verdict.excerpt != subject:
        lines.append(verdict.excerpt)
    notify(
        title, "\n".join(lines)[:400],
        level="alert" if verdict.important else "normal",
        cfg=getattr(cfg, "notify", None),
    )
    log.info("MIO announced: %s%s", title, " (important)" if verdict.important else "")


def sync_mio(cfg, driver, *, dry_run: bool = False, logger=None) -> list[dict]:
    """Announce new MIO, and save teacher MIO into course folders.

    Returns upload queue entries for the saved ones.
    """
    log = logger or logging.getLogger("school.mio")
    store = StateStore(cfg.repo_root / "state" / STATE_FILE)
    known = {r.get("key") for r in store.read()}
    queued: list[dict] = []
    fresh: list[dict] = []

    mode = getattr(cfg, "mio_open", "never")
    if mode == "never" and getattr(cfg, "mio_full_bodies", False):
        mode = "teachers"  # the older switch, still honoured
    announce = bool(getattr(cfg, "mio_notify", True))

    seen_store = StateStore(cfg.repo_root / "state" / SEEN_FILE)
    # The first run has no memory of what was already there. Announcing all of
    # it would ring once per message of the semester, so it announces only what
    # is still unread, and remembers the rest as seen.
    first_run = not seen_store.path.exists()
    seen = {r.get("key") for r in seen_store.read()}
    newly_seen: list[dict] = []

    try:
        messages = driver.list_mio()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read MIO: %s", exc)
        return []

    for message in messages or []:
        sender = message.get("sender", "")
        course = match_course(sender, cfg.courses)
        preview_key = (
            message.get("id")
            or f"{sender}\x1f{message.get('preview', '')[:60]}"
        )
        is_new = preview_key not in seen and (not first_run or bool(message.get("unread")))
        to_file = course is not None and preview_key not in known

        # Everything above this line is free. Opening a message is not: it
        # costs seconds and it marks the message read. So a teacher's message
        # is opened once, to file it, when `open` allows teachers; anyone
        # else's only when `open: all` and it is new. A message you had
        # already read before this existed is never opened.
        should_open = (to_file and mode in ("teachers", "all")) or (is_new and mode == "all")
        detail = {}
        if should_open:
            try:
                body, code = driver.read_mio_body(message.get("id", ""))
            except Exception as exc:  # noqa: BLE001 - one bad row, not the run
                log.warning("Could not open a MIO: %s", exc)
                body, code = "", ""
            if body:
                detail = parse_detail(body)
                # The opened message names its own course, which beats matching
                # a teacher's name against a sender string.
                if course is not None:
                    by_code = {c.code: c for c in cfg.courses}
                    course = by_code.get(code) or course
        if detail.get("body"):
            subject = detail.get("subject") or ""
            body = detail["body"]
            date = detail_date(detail.get("date", "")) or message.get("date") or ""
        else:
            subject, body = split_subject(message.get("preview", ""))
            date = message.get("date") or ""

        if is_new and announce and not dry_run:
            try:
                # The inbox's name for the sender, which is what you will look
                # for in Omnivox; the opened view appends the course to it.
                _announce(
                    cfg, sender, subject, body,
                    staff=is_staff(sender, cfg), direct=bool(detail.get("direct")),
                    log=log,
                )
            except Exception as exc:  # noqa: BLE001 - a notification never costs the run
                log.warning("Could not announce a MIO: %s", exc)
        if preview_key not in seen:
            newly_seen.append({"key": preview_key, "sender": clean_sender(sender)[:60], "date": date})

        if not to_file:
            continue

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
                    detail.get("sender") or sender,
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
    if (newly_seen or first_run) and not dry_run:
        seen_store.write(seen_store.read() + newly_seen)
    return queued
