"""Generate an .ics for the semester, one event per class meeting.

Why one event per meeting and not a weekly recurrence: a class title has to
carry its class number ("1a", "13b"), and a recurring event shares one title
across every occurrence. Per-class events also survive the thing that actually
happens, which a recurrence does not: a class gets moved or cancelled, or the
cégep swaps a weekday ("horaire du lundi"), and every number after it shifts.

The live Google Calendar is populated directly, one create_event per meeting
through the calendar MCP. That is ~112 calls a semester and takes a few minutes,
which is fine for something that runs three more times. This file is the
portable copy: a backup, and the path for any calendar that is not this Google
account. Every event carries a stable UID derived from (course, start), so
re-importing a regenerated file UPDATES existing events rather than duplicating
them.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

from src.common import Config, Course
from src.recorder import class_label, class_dates, parse_schedule

PRODID = "-//school-automation//Automne 2026//FR"


def _esc(text: str) -> str:
    """Escape per RFC 5545: backslash, semicolon, comma, newline."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """RFC 5545 caps lines at 75 octets; continuations start with a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 73:
        return line
    out, chunk = [], b""
    for char in line:
        encoded = char.encode("utf-8")
        if len(chunk) + len(encoded) > 73:
            out.append(chunk.decode("utf-8"))
            chunk = b" "
        chunk += encoded
    out.append(chunk.decode("utf-8"))
    return "\r\n".join(out)


def stable_uid(course_code: str, start: datetime) -> str:
    digest = hashlib.sha1(f"{course_code}|{start:%Y%m%dT%H%M}".encode()).hexdigest()[:16]
    return f"{digest}@school-automation"


def _local(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def class_events(cfg: Config) -> list[dict]:
    """One entry per class meeting, in chronological order."""
    entries = parse_schedule(cfg.schedule)
    by_course: dict[str, list] = {}
    for entry in entries:
        by_course.setdefault(entry.course, []).append(entry)

    events: list[dict] = []
    for course in cfg.courses:
        for start in class_dates(cfg, course.code):
            entry = next(
                (
                    e
                    for e in by_course.get(course.code, [])
                    if e.weekday == start.strftime("%A").lower() and e.start == start.time()
                ),
                None,
            )
            if entry is None:
                continue
            end = datetime.combine(start.date(), entry.end)
            label = class_label(cfg, course.code, start)
            events.append(
                {
                    "course": course,
                    "start": start,
                    "end": end,
                    "label": label,
                    "room": entry.room,
                    "block": entry.block,
                    "uid": stable_uid(course.code, start),
                }
            )
    return sorted(events, key=lambda e: e["start"])


def event_title(course: Course, label: str | None, block: str = "") -> str:
    """The calendar title. One definition, so the .ics and the live Google
    calendar cannot drift apart.

    The E block earns a suffix and the others do not. Recherche's lecture and
    its encadrement fall on the same Thursday and share one class number, so
    without it the calendar shows the same title twice in one day and the only
    difference is the start time.
    """
    stem = f"{course.icon} {label} · {course.folder}" if label else (
        f"{course.icon} {course.folder}"
    )
    return f"{stem} (encadrement)" if block == "E" else stem


def _header_lines(course: Course, local: str, block: str) -> list[str]:
    """The five identifying lines, in the order this calendar has used since 2025."""
    code = f"{course.code} gr.{course.group}" if course.group else course.code
    where = f"Local {local}    {block}".rstrip() if local else ""
    return [course.folder, code, where, course.teacher, "Présentiel"]


def _description(course: Course, local: str, label: str | None, block: str = "") -> str:
    """Plain-text description, for the .ics DESCRIPTION property."""
    number = label or "?"
    lines = [l for l in _header_lines(course, local, block) if l]
    return "\n".join(
        lines + ["", f"{number} À faire / Devoirs :", "", "Contenu du cours :", "",
                 "Notes / Rappels :"]
    )


def _esc_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def html_description(course: Course, local: str, label: str | None, block: str = "") -> str:
    """Rich description for Google Calendar.

    Google renders a small HTML subset here, and line breaks must be <br>: a
    bare newline is whitespace to an HTML parser and collapses. That is the
    trap. This mirrors the markup already on his 2025-2026 events, so a
    semester rollover looks continuous rather than like a different tool made
    it: course name bold, delivery mode bold-italic, headings as <strong>.

    The three headings are left empty on purpose. An assistant fills in the
    first two; Notes / Rappels is his, for the thing an assistant would never
    know to write down.
    """
    lines = [l for l in _header_lines(course, local, block) if l]
    parts = [f"<b>{_esc_html(lines[0])}</b>"]
    parts += [_esc_html(l) for l in lines[1:-1]]
    parts.append(f"<b><i>{_esc_html(lines[-1])}</i></b>")
    return (
        "<br>".join(parts)
        + f"<p><strong>{_esc_html(label or '?')} À faire / Devoirs :</strong></p>"
        + "<p><strong>Contenu du cours :</strong></p>"
        + "<p><strong>Notes / Rappels :</strong></p>"
    )


def build_ics(cfg: Config) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VTIMEZONE",
        "TZID:America/Toronto",
        "BEGIN:DAYLIGHT",
        "TZOFFSETFROM:-0500",
        "TZOFFSETTO:-0400",
        "TZNAME:EDT",
        "DTSTART:19700308T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
        "END:DAYLIGHT",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:-0400",
        "TZOFFSETTO:-0500",
        "TZNAME:EST",
        "DTSTART:19701101T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    stamp = datetime(2026, 1, 1, 0, 0, 0)  # fixed: a re-export must not churn

    for event in class_events(cfg):
        course: Course = event["course"]
        label = event["label"]
        room, block = event["room"], event["block"]
        title = event_title(course, label, block)
        location = f"Local {room} · {course.teacher}" if room else course.teacher
        lines += [
            "BEGIN:VEVENT",
            f"UID:{event['uid']}",
            f"DTSTAMP:{_local(stamp)}Z",
            f"DTSTART;TZID=America/Toronto:{_local(event['start'])}",
            f"DTEND;TZID=America/Toronto:{_local(event['end'])}",
            _fold(f"SUMMARY:{_esc(title.strip())}"),
            _fold(f"LOCATION:{_esc(location)}"),
            _fold(f"DESCRIPTION:{_esc(_description(course, room, label, block))}"),
            _fold(
                "X-ALT-DESC;FMTTYPE=text/html:"
                + _esc(html_description(course, room, label, block))
            ),
            "BEGIN:VALARM",
            "TRIGGER:-PT10M",
            "ACTION:DISPLAY",
            "DESCRIPTION:Rappel",
            "END:VALARM",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def write_ics(cfg: Config, dest: Path | None = None) -> Path:
    dest = Path(dest) if dest else cfg.digest_dir() / f"Horaire {cfg.semester}.ics"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(build_ics(cfg), encoding="utf-8")
    return dest


def main() -> int:
    from src.common import load_config

    cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    path = write_ics(cfg)
    events = class_events(cfg)
    print(f"{len(events)} class meetings -> {path}")
    print("\nThe School calendar is kept up to date directly, one event per")
    print("meeting, via the calendar MCP. Ask Claude to sync it after editing")
    print("the schedule in config.yaml.")
    print("\nThis file is the portable backup. To load it into a calendar by")
    print("hand: calendar.google.com > Settings > Import & export > Import,")
    print("choosing 'School' as the destination. Stable UIDs mean a re-import")
    print("UPDATES the existing events rather than duplicating them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
