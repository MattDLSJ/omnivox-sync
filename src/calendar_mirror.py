"""Mirror the live School calendar into a readable file per course.

Why this exists
---------------
The calendar became the richest thing in the system: it carries the per-class
content, the evaluations and their weightings, and your own notes. It was
also the only one of those that was not a file, which meant NotebookLM, the
place he actually goes to ask questions about a course, could not see any of
it. Ask it "what is my next évaluation" and it had the plan de cours but not
the calendar.

So: one writer, many readers. The calendar is the only thing he and an
assistant edit. This module derives everything else from it, one way, so there
is never a conflict to resolve and never a question of which copy is right.

    config.yaml ──generates──> Google Calendar ──mirrors──> _horaire.md
    (when and where)           (what he edits)              (what agents read)

The mirror doubles as the backup the .ics is not. `build_ics()` renders from
config.yaml, so it captures the schedule but none of the content; if the
horaire is reissued again and the events are rebuilt, this file is what makes
the content recoverable.

Reading the calendar without credentials
----------------------------------------
The pipeline runs under launchd and has no MCP connector, so it cannot use the
Calendar API the way an assistant does. It does not need to: every Google
calendar exposes a private iCal URL ("Secret address in iCal format"), which is
an ordinary HTTPS GET returning the live calendar, hand edits included. One
secret URL in .env, no OAuth, no token to refresh.

That URL grants read access to the whole calendar to anyone holding it, so it
lives in .env beside the Omnivox credentials and never in config.yaml.
Without it this module is inert, the same way the recorder is inert without a
schedule.
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from src.common import Config, Course

ICS_URL_KEY = "SCHOOL_ICS_URL"
MIRROR_NAME = "_horaire.md"
TZ = ZoneInfo("America/Toronto")

MOIS = {
    1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
    7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre",
    12: "décembre",
}
JOURS = {
    0: "lundi", 1: "mardi", 2: "mercredi", 3: "jeudi",
    4: "vendredi", 5: "samedi", 6: "dimanche",
}


class MirrorError(Exception):
    """The calendar could not be read."""


@dataclass
class CalEvent:
    uid: str = ""
    summary: str = ""
    description: str = ""
    location: str = ""
    start: datetime | date | None = None
    end: datetime | date | None = None


@dataclass
class MirrorResult:
    written: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    events: int = 0

    def summary(self) -> str:
        parts = [f"{self.events} événement(s) lus"]
        if self.written:
            parts.append(f"{len(self.written)} fichier(s) réécrit(s)")
        if self.unchanged:
            parts.append(f"{len(self.unchanged)} inchangé(s)")
        return ", ".join(parts)


# --------------------------------------------------------------------------
# Reading the calendar
# --------------------------------------------------------------------------


def ics_url(repo_root: Path) -> str | None:
    """The secret iCal URL, or None when it has not been set up."""
    values = dotenv_values(Path(repo_root) / ".env")
    return (values.get(ICS_URL_KEY) or "").strip() or None


WRONG_LINK = (
    "L'URL dans SCHOOL_ICS_URL n'est pas un flux iCal.\n"
    "Celle-là est le lien « Get shareable link » (elle contient ?cid=) : elle "
    "ouvre le calendrier dans le navigateur et demande une connexion.\n"
    "Il faut l'adresse secrète au format iCal, plus bas sur la même page :\n"
    "  Google Agenda > Paramètres > (calendrier School) > Intégrer l'agenda\n"
    "  > « Adresse secrète au format iCal »\n"
    "Elle contient /ical/ et se termine par .ics."
)


def looks_like_ics_feed(url: str) -> bool:
    """Cheap shape check before spending a request on it.

    The settings page offers two links a few centimetres apart. "Get shareable
    link" gives calendar.google.com/calendar/u/0?cid=…, which is the web UI and
    returns HTML; the feed is .../calendar/ical/…/private-…/basic.ics. Picking
    the wrong one is the obvious mistake, so name it rather than letting the
    parser find zero events in a login page and report an empty calendar.
    """
    return "/ical/" in url and url.endswith(".ics")


def fetch_ics(url: str, *, timeout: int = 60) -> str:
    if not looks_like_ics_feed(url):
        raise MirrorError(WRONG_LINK)
    request = urllib.request.Request(url, headers={"User-Agent": "school-automation"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise MirrorError(
            f"Le calendrier a répondu {exc.code}. L'adresse secrète iCal a "
            f"peut-être été réinitialisée; regénère-la dans les paramètres du "
            f"calendrier et remets-la dans .env sous {ICS_URL_KEY}."
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise MirrorError(f"Impossible de joindre le calendrier : {exc}") from exc

    # A feed that redirects to a sign-in page answers 200 with HTML. Parsing it
    # would find no VEVENTs and report an empty calendar, which reads exactly
    # like a semester with no classes.
    if "BEGIN:VCALENDAR" not in body[:2000]:
        raise MirrorError(
            "Le calendrier a répondu, mais pas en iCal (probablement une page "
            "de connexion).\n" + WRONG_LINK
        )
    return body


def _unfold(text: str) -> list[str]:
    """RFC 5545 folds long lines; a continuation starts with a space or tab."""
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def _unescape(value: str) -> str:
    """Inverse of the RFC 5545 escaping done in calendar_export._esc."""
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_stamp(value: str, params: str):
    """DTSTART in any of the three shapes Google emits."""
    if "VALUE=DATE" in params:
        return datetime.strptime(value, "%Y%m%d").date()
    if value.endswith("Z"):
        return (
            datetime.strptime(value, "%Y%m%dT%H%M%SZ")
            .replace(tzinfo=ZoneInfo("UTC"))
            .astimezone(TZ)
        )
    naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    return naive.replace(tzinfo=TZ)


def parse_ics(text: str) -> list[CalEvent]:
    """Every VEVENT, in file order. Cancelled events are dropped."""
    events: list[CalEvent] = []
    current: CalEvent | None = None
    cancelled = False
    in_alarm = False

    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current, cancelled, in_alarm = CalEvent(), False, False
            continue
        if line == "END:VEVENT":
            if current is not None and not cancelled and current.start is not None:
                events.append(current)
            current = None
            continue
        # A VALARM lives INSIDE the VEVENT and carries its own DESCRIPTION
        # ("Rappel") and TRIGGER. Reading straight through replaces the real
        # description with the alarm's, which is how every mirrored event ends
        # up saying "Rappel" and nothing else.
        if line == "BEGIN:VALARM":
            in_alarm = True
            continue
        if line == "END:VALARM":
            in_alarm = False
            continue
        if current is None or in_alarm or ":" not in line:
            continue

        head, _, value = line.partition(":")
        name, _, params = head.partition(";")
        name = name.upper()

        if name == "UID":
            current.uid = value
        elif name == "SUMMARY":
            current.summary = _unescape(value)
        elif name == "DESCRIPTION":
            current.description = _unescape(value)
        elif name == "LOCATION":
            current.location = _unescape(value)
        elif name == "STATUS" and value.upper() == "CANCELLED":
            cancelled = True
        elif name in ("DTSTART", "DTEND"):
            try:
                setattr(current, name.lower().replace("dt", ""), _parse_stamp(value, params))
            except ValueError:
                pass
    return events


# --------------------------------------------------------------------------
# The description is HTML; turn it back into structure
# --------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"<p>(.*?)</p>|<ul>(.*?)</ul>", re.S | re.I)
_ITEM = re.compile(r"<li>(.*?)</li>", re.S | re.I)


def _text(fragment: str) -> str:
    return html_mod.unescape(_TAG.sub("", fragment)).strip()


_FULLY_BOLD = re.compile(r"\s*<(strong|b)>.*</(strong|b)>\s*$", re.S | re.I)


def _is_heading(fragment: str, body: str) -> bool:
    """A heading, or a bold sentence that happens to be inside prose?

    Bold alone does not settle it: descriptions use <b> mid-paragraph for
    emphasis, and treating that as a heading chops a note in half and hangs the
    rest under a title that is really the middle of a sentence.

    What separates them is the colon. Every heading this project writes ends in
    one ("À faire / Devoirs :", "Contenu du cours :"), and prose does not.
    """
    return bool(_FULLY_BOLD.match(fragment)) and body.rstrip().endswith(":")


@dataclass
class Described:
    header: list[str] = field(default_factory=list)
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    # (heading it sits under, or None for prose before the first heading)
    prose: list[tuple[str | None, str]] = field(default_factory=list)

    @property
    def notes(self) -> list[str]:
        """Prose that came before any heading, e.g. the encadrement note."""
        return [text for title, text in self.prose if title is None]

    def prose_for(self, title: str) -> list[str]:
        return [text for owner, text in self.prose if owner == title]

    def section(self, needle: str) -> list[str]:
        for title, items in self.sections:
            if needle.lower() in title.lower():
                return items
        return []


def parse_description(description: str) -> Described:
    """Split an event description into its header lines and its headings.

    Written against the markup this project produces, and tolerant of the rest:
    anything it does not recognise still comes through as a plain line rather
    than being dropped, because these descriptions are also hand-edited.
    """
    out = Described()
    if not description:
        return out

    first = description.find("<p")
    head = description if first == -1 else description[:first]
    out.header = [t for t in (_text(p) for p in re.split(r"<br\s*/?>", head)) if t]

    rest = "" if first == -1 else description[first:]
    current: str | None = None
    for match in _BLOCK.finditer(rest):
        para, lst = match.group(1), match.group(2)
        if para is not None:
            body = _text(para)
            if not body:
                continue
            if _is_heading(para, body):
                current = body
                out.sections.append((body, []))
            else:
                out.prose.append((current, body))
        elif lst is not None:
            items = [_text(i) for i in _ITEM.findall(lst)]
            items = [i for i in items if i]
            if out.sections:
                out.sections[-1][1].extend(items)
            else:
                out.prose.extend((None, i) for i in items)
    return out


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def fr_date(moment) -> str:
    day = moment.date() if isinstance(moment, datetime) else moment
    return f"{JOURS[day.weekday()]} {day.day} {MOIS[day.month]}"


def _time_range(event: CalEvent) -> str:
    if not isinstance(event.start, datetime):
        return "toute la journée"
    if not isinstance(event.end, datetime):
        return f"{event.start:%H:%M}"
    return f"{event.start:%H:%M} à {event.end:%H:%M}"


def _class_number(summary: str) -> str:
    """The "4a" out of "📖 4a · Littérature et imaginaire · 📝 …"."""
    parts = [p.strip() for p in summary.split("·")]
    if len(parts) < 2:
        return ""
    lead = parts[0].split()
    return lead[-1] if lead and any(c.isdigit() for c in lead[-1]) else ""


def events_for(course: Course, events: list[CalEvent]) -> list[CalEvent]:
    """A course's own meetings, matched on the course code in the description.

    The code is the only field guaranteed present and unambiguous: titles get
    hand-edited and two courses can share a room or a teacher.
    """
    mine = [e for e in events if course.code in (e.description or "")]
    return sorted(mine, key=lambda e: (e.start is None, str(e.start)))


def render_course(course: Course, events: list[CalEvent], *, generated: datetime) -> str:
    described = [(e, parse_description(e.description)) for e in events]

    lines = [f"# {course.folder}", ""]
    if described:
        head = described[0][1].header
        if len(head) > 1:
            lines.append(f"**{head[1]}**" + (f" · {head[3]}" if len(head) > 3 else ""))
    lines += [
        "",
        f"> Copie en lecture seule du calendrier **School**, générée le "
        f"{fr_date(generated)} {generated:%Y} à {generated:%H:%M}.",
        "> Le calendrier est la source. Modifie un cours là, et ce fichier suivra "
        "au prochain sync.",
        "",
    ]

    evals = [
        (e, item)
        for e, d in described
        for item in d.section("À faire")
        if item.startswith("📝")
    ]
    if evals:
        lines += ["## Évaluations", "",
                  "| Cours | Date | Évaluation |", "| --- | --- | --- |"]
        for event, item in evals:
            lines.append(
                f"| {_class_number(event.summary) or '—'} | {fr_date(event.start)} "
                f"| {item.lstrip('📝').strip()} |"
            )
        lines.append("")

    lines += ["## Calendrier des cours", ""]
    for event, described_one in described:
        number = _class_number(event.summary)
        title = f"Cours {number}" if number else event.summary
        lines.append(f"### {title} — {fr_date(event.start)}, {_time_range(event)}")
        if event.location:
            lines.append(f"*{event.location}*")
        lines.append("")
        # Prose written before the first heading explains the whole event, e.g.
        # what the Thursday "E" block is. It has to lead, not be dropped.
        for note in described_one.notes:
            lines += [f"_{note}_", ""]
        for heading, items in described_one.sections:
            body = [i for i in items if i]
            attached = described_one.prose_for(heading)
            if not body and not attached:
                continue
            # "3 À faire / Devoirs" -> "À faire / Devoirs": the number is
            # already the heading of the section this sits under.
            clean = re.sub(r"^\d+[a-f]?\s+", "", heading).rstrip(" :")
            lines.append(f"**{clean}**")
            lines += [f"- {i}" for i in body]
            lines += [f"_{p}_" for p in attached]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def mirror(
    cfg: Config,
    *,
    ics_text: str | None = None,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
) -> MirrorResult:
    """Write one `_horaire.md` per course. Inert without the secret iCal URL."""
    log = logger or logging.getLogger("school.calendar_mirror")
    result = MirrorResult()

    if ics_text is None:
        url = ics_url(cfg.repo_root)
        if not url:
            log.info(
                "Pas de %s dans .env; le miroir du calendrier est inactif", ICS_URL_KEY
            )
            return result
        ics_text = fetch_ics(url)

    events = parse_ics(ics_text)
    result.events = len(events)
    generated = datetime.now(TZ)

    for course in cfg.courses:
        mine = events_for(course, events)
        if not mine:
            continue
        path = cfg.folder_for(course) / MIRROR_NAME
        body = render_course(course, mine, generated=generated)

        # The timestamp changes every run, so compare everything but it.
        def strip_stamp(text: str) -> str:
            return "\n".join(l for l in text.splitlines() if "générée le" not in l)

        if path.exists() and strip_stamp(path.read_text(encoding="utf-8")) == strip_stamp(body):
            result.unchanged.append(path)
            continue
        if dry_run:
            log.info("[dry-run] réécrirait %s", path)
            result.written.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        log.info("Miroir écrit : %s (%d cours)", path, len(mine))
        result.written.append(path)

    return result


def main() -> int:
    import argparse

    from src.common import load_config

    parser = argparse.ArgumentParser(
        description="Mirror the School calendar into a readable file per course."
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    repo_root = Path(__file__).resolve().parents[1]
    cfg = load_config(Path(args.config) if args.config else repo_root / "config.yaml")

    try:
        result = mirror(cfg, dry_run=args.dry_run)
    except MirrorError as exc:
        print(f"error: {exc}")
        return 1
    print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
