"""Exam accommodations, read from the college rather than typed into a config.

A student registered with a centre de services adaptés gets their exams in a
separate room with extra time. Both facts live in Omnivox, in a module called
SRAE that Skytech supplies to colleges across Quebec, so this is not one
college's quirk: the same page shape serves anywhere the module is deployed.

The reason this belongs in a sync tool at all is arithmetic nobody does by
hand. Extra time is a percentage of the class block, and the booking form
starts your session when the class starts. A two hour exam at 33 percent runs
forty minutes past the end of the period, which means it runs into whatever
you have next. Nothing in the college's system says so: the exam booking
module does not know your timetable, and your timetable does not know you have
an accommodation. The collision only appears if something holds both, and this
project already holds both.

It found a real one on the first run: two exams on the same morning where the
first, extended, overlapped the start of the second by forty minutes.

Off unless asked for. Most students have no accommodation and should not be
asked to care.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta

#: "Temps supplémentaire lors d'un examen théorique : 33 %". The label varies
#: between colleges and between accommodation types; the number does not.
_PERCENT = re.compile(r"temps\s+suppl[ée]mentaire[^%\d]{0,80}?(\d{1,3})\s*%", re.I)

#: The accommodations panel is a flat list of short lines. Anything that looks
#: like a heading, a session tab or the counsellor's name is not one.
_NOT_AN_ACCOMMODATION = re.compile(
    r"^(accommodements?|automne|hiver|[ée]t[ée])\b|^fermer$", re.I
)

#: The panel renders a hidden error placeholder whether or not anything failed,
#: and it is not an accommodation.
_NOISE = re.compile(r"erreur inattendue|veuillez r[ée]essayer|chargement", re.I)


@dataclass(frozen=True)
class Accommodations:
    """What the college has granted, as it states it."""

    items: tuple[str, ...] = ()
    extra_time_percent: int = 0

    def __bool__(self) -> bool:
        return bool(self.items)

    @property
    def separate_room(self) -> bool:
        return any(
            re.search(r"salle d'examen|local des services adapt", i, re.I)
            for i in self.items
        )


def parse_accommodations(page_html: str) -> Accommodations:
    """Read the accommodations panel. Empty when the page shows none.

    Deliberately forgiving about the wording and strict about the number: a
    college that words the label differently still yields its percentage, and
    a page that yields no percentage reports zero rather than a guess, because
    a wrong extra-time figure produces a wrong end time and a wrong end time
    is the whole thing this module exists to get right.
    """
    text = re.sub(r"<[^>]+>", "\n", page_html)
    lines = [
        " ".join(_html.unescape(line).replace("\xa0", " ").split())
        for line in text.splitlines()
    ]
    anchor = next(
        (i for i, l in enumerate(lines) if re.fullmatch(r"accommodements?", l, re.I)),
        None,
    )
    if anchor is None:
        return Accommodations()

    items: list[str] = []
    for line in lines[anchor + 1:]:
        if not line or len(line) > 160:
            continue
        if _NOISE.search(line):
            continue
        if _NOT_AN_ACCOMMODATION.match(line):
            if re.fullmatch(r"fermer", line, re.I):
                break
            continue
        # The page puts the label and its value in separate elements, so
        # "Temps supplémentaire lors d'un examen théorique" and ": 33%" arrive
        # as two lines. Rejoining them is not cosmetic: split apart, neither
        # half is a statement of what was granted, and the percentage reads as
        # an accommodation of its own.
        if line.startswith(":") and items:
            items[-1] = f"{items[-1]} {line}".replace(" :", " :")
            continue
        if line not in items:
            items.append(line)

    percent = 0
    found = _PERCENT.search(" ".join(items))
    if found:
        percent = int(found.group(1))
    return Accommodations(tuple(items), percent)


def extended_end(start: dtime, end: dtime, percent: int) -> dtime:
    """When a session that runs `start`-`end` actually ends with extra time.

    A LOWER bound, deliberately. This works from the real timetable minutes,
    while the college's own booking form rounds the class block up to whole
    periods first: a 10:10-12:00 class is 1h50, and the form calls it 2h00 and
    then adds the percentage to that. So the real booking can run later than
    this says, never earlier, which is the safe direction for a collision
    check. Anything this flags is a genuine collision; it just might be worse.

    Rounded to the minute and clamped inside the day, because a late-afternoon
    exam plus extra time can otherwise wrap past midnight and produce an end
    that sorts before its own start.
    """
    base = datetime.combine(date(2000, 1, 1), start)
    finish = datetime.combine(date(2000, 1, 1), end)
    minutes = round((finish - base).total_seconds() / 60 * (1 + percent / 100))
    extended = base + timedelta(minutes=minutes)
    if extended.date() != base.date():
        return dtime(23, 59)
    return extended.time()


@dataclass(frozen=True)
class Collision:
    """An extended exam that runs into something else on the timetable."""

    course: str
    weekday: str
    exam_start: dtime
    exam_end: dtime      # with the extra time applied
    clashes_with: str
    clash_start: dtime
    overlap_minutes: int


def collisions(entries, percent: int, *, course: str | None = None) -> list[Collision]:
    """Where extra time pushes one class's exam into the next class.

    `entries` are recorder.ScheduleEntry values, which is the timetable this
    project already scrapes from the college. Pass `course` to test only that
    course's slots as the exam; without it every slot is considered, which is
    what a whole-semester check wants.

    Nothing here knows which slots hold exams. That is on purpose: the caller
    knows, and a collision between an extended slot and the next class is
    worth reporting whether or not an exam has been booked into it yet.
    """
    if percent <= 0:
        return []
    by_day: dict[str, list] = {}
    for entry in entries:
        by_day.setdefault(entry.weekday, []).append(entry)

    out: list[Collision] = []
    for weekday, slots in by_day.items():
        slots = sorted(slots, key=lambda e: e.start)
        for i, slot in enumerate(slots):
            if course and slot.course != course:
                continue
            finishes = extended_end(slot.start, slot.end, percent)
            for later in slots[i + 1:]:
                if later.start >= finishes:
                    break
                if later.course == slot.course:
                    # One class the timetable splits into two rows. Running on
                    # into its own second block is not missing a class.
                    continue
                overlap = round(
                    (
                        datetime.combine(date(2000, 1, 1), min(finishes, later.end))
                        - datetime.combine(date(2000, 1, 1), later.start)
                    ).total_seconds()
                    / 60
                )
                if overlap > 0:
                    out.append(
                        Collision(
                            course=slot.course, weekday=weekday,
                            exam_start=slot.start, exam_end=finishes,
                            clashes_with=later.course, clash_start=later.start,
                            overlap_minutes=overlap,
                        )
                    )
    return sorted(out, key=lambda c: (c.weekday, c.exam_start))


def summary(acc: Accommodations, clashes: list[Collision], labels=None) -> str:
    """What to show a person. "" when there is nothing worth saying."""
    if not acc:
        return ""
    name = (labels or {}).get
    lines = ["Services adaptés:"]
    for item in acc.items:
        lines.append(f"  - {item}")
    if not clashes:
        return "\n".join(lines)
    lines.append("")
    lines.append(
        f"With {acc.extra_time_percent}% extra time, "
        f"{len(clashes)} exam slot(s) would run into your next class:"
    )
    for c in clashes:
        first = name(c.course, c.course) if labels else c.course
        second = name(c.clashes_with, c.clashes_with) if labels else c.clashes_with
        lines.append(
            f"  - {c.weekday} {first} ends {c.exam_end:%H:%M} with extra time, "
            f"but {second} starts {c.clash_start:%H:%M} "
            f"({c.overlap_minutes} min over)"
        )
    lines.append("")
    lines.append("Tell the CSA before the date, not on the morning.")
    return "\n".join(lines)
