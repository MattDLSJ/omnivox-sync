"""Read the class timetable off Omnivox, so nobody has to type it.

The recorder needs a `schedule:` block: which course, which day, which hours.
Without one it is inert on every platform, which meant lecture recording could
not work from a fresh install no matter how much the installer did. Nothing in
this project wrote that block. The design spec put "auto-updating schedule from
LÉA" out of scope for v1, and it stayed out of scope by inertia.

It turns out the college publishes it. `Horaire de cours` on the Omnivox
landing page opens a Skytech module at `/estd/hrre/Horaire.ovx`, which asks
which session and then renders the timetable as an HTML table: weekdays across
the top, one row per fifty-minute period, and `rowspan` on each cell giving the
length of the slot.

That last detail is why this reads the page and not the PDF. A student can
print the same timetable, and one is sitting in this project's own Other/
folder, but printing flattens the merged cells: the text is centred inside its
box, so a four-period Friday slot ends up on whichever row happens to be
vertically middle, and a parser reads 09:10 for a class that starts at 08:10.
The `rowspan` attribute is the fact the print destroys.

The same cells carry the group number, the room, the activity letter and the
teacher, none of which course discovery has ever produced either.
"""

from __future__ import annotations

import html as _html
import re
from datetime import date

#: The page is French regardless of the interface language: it comes from the
#: college's own Skytech instance rather than from Omnivox's shell.
WEEKDAYS = {
    "lundi": "monday",
    "mardi": "tuesday",
    "mercredi": "wednesday",
    "jeudi": "thursday",
    "vendredi": "friday",
    "samedi": "saturday",
    "dimanche": "sunday",
}

_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh]\b([^>]*)>(.*?)</t[dh]>", re.S | re.I)
_ROWSPAN = re.compile(r"rowspan\s*=\s*[\"']?(\d+)", re.I)
_TIME = re.compile(r"\b(\d{1,2}:\d{2})\b")
#: "340-101-MQ gr.1060". The group is on the same line as the code.
_CODE = re.compile(r"\b(\d{3}-[A-Z0-9]{3}-[A-Z]{2})\b(?:\s*gr\.?\s*(\d+))?", re.I)
#: "Local C078    T" — the letter after the room is the activity type.
_ROOM = re.compile(r"Local\s+(\S+)(?:\s+([TLE])\b)?", re.I)


def session_code(today: date | None = None) -> str:
    """Omnivox's session id: the year, then 1 Hiver, 2 Été, 3 Automne.

    Derived rather than asked. The selector on the page offers all three of a
    year, and picking the wrong one silently returns somebody else's term.
    """
    today = today or date.today()
    term = 1 if today.month <= 5 else 2 if today.month <= 7 else 3
    return f"{today.year}{term}"


def _text(fragment: str) -> list[str]:
    """The visible lines of one cell, in order, blanks dropped."""
    broken = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    stripped = re.sub(r"<[^>]+>", "", broken)
    unescaped = _html.unescape(stripped).replace("\xa0", " ")
    return [" ".join(line.split()) for line in unescaped.splitlines() if line.strip()]


def _cells(row_html: str) -> list[tuple[int, str]]:
    return [
        (int(m.group(1)) if (m := _ROWSPAN.search(attrs)) else 1, body)
        for attrs, body in _CELL.findall(row_html)
    ]


def parse_horaire(page_html: str) -> list[dict]:
    """Every timetable slot on the page, as `schedule:` entries.

    Returns the shape `recorder.parse_schedule` validates: course, weekday,
    start, end, plus room and block where the page gives them. Teacher and
    group ride along for the caller to fold into `courses:`; they are ignored
    by the recorder and dropped before writing.
    """
    grid = _grid(page_html)
    if not grid:
        return []
    rows = _ROW.findall(grid)
    if len(rows) < 2:
        return []

    days = [line for cell in _cells(rows[0]) for line in _text(cell[1])]
    days = [WEEKDAYS[d.lower()] for d in days if d.lower() in WEEKDAYS]
    if not days:
        return []

    # A cell with rowspan=N occupies its column for the next N-1 rows too, and
    # those rows simply do not emit a <td> for it. Walking cells positionally
    # without tracking that is the classic way to read this table wrong: every
    # column after a spanned one shifts left by one, so Thursday's class is
    # reported on Wednesday.
    held: dict[int, int] = {}
    periods: list[tuple[str, str]] = []
    placed: list[tuple[int, int, int, str]] = []  # row, column, span, body

    for index, row_html in enumerate(rows[1:]):
        cells = _cells(row_html)
        if not cells:
            continue
        times = _TIME.findall(" ".join(_text(cells[0][1])))
        if len(times) < 2:
            continue
        periods.append((times[0], times[1]))
        row = len(periods) - 1

        column, rest = 0, cells[1:]
        opened: set[int] = set()
        for span, body in rest:
            while held.get(column, 0) > 0:
                column += 1
            if column >= len(days):
                break
            if _text(body):
                placed.append((row, column, span, body))
            if span > 1:
                held[column] = span - 1
                opened.add(column)
            column += 1
        # Not the ones opened on THIS row. Decrementing those here expires
        # every span one row early, and the failure is silent and plausible:
        # a four-period Thursday class frees its column for the 16:10 row, so
        # Friday's 16:10 class gets placed on Thursday. Caught by diffing a
        # scrape against a timetable that had been typed in by hand.
        for key in list(held):
            if key not in opened and held[key] > 0:
                held[key] -= 1

    out = []
    for row, column, span, body in placed:
        lines = _text(body)
        joined = " ".join(lines)
        code = _CODE.search(joined)
        if not code:
            continue  # a note or a legend, not a class
        last = min(row + span - 1, len(periods) - 1)
        room = _ROOM.search(joined)
        entry = {
            "course": code.group(1).upper(),
            "weekday": days[column],
            "start": periods[row][0],
            "end": periods[last][1],
        }
        if room:
            entry["room"] = room.group(1).replace(";", "/")
            if room.group(2):
                entry["block"] = room.group(2).upper()
        if code.group(2):
            entry["group"] = code.group(2)
        teacher = _teacher(lines)
        if teacher:
            entry["teacher"] = teacher
        out.append(entry)
    return out


def _teacher(lines: list[str]) -> str:
    """The name line: after the room line, before the delivery-mode line."""
    for index, line in enumerate(lines):
        if _ROOM.search(line) and index + 1 < len(lines):
            candidate = lines[index + 1]
            if not _CODE.search(candidate) and not _ROOM.search(candidate):
                return candidate
    return ""


def _grid(page_html: str) -> str:
    """The one table that holds the weekdays, innermost first.

    The page is 1990s HTML: the timetable sits five tables deep inside layout
    tables, and every one of those ancestors also "contains" the word Jeudi.
    """
    anchor = page_html.find("Jeudi")
    if anchor < 0:
        return ""
    start = page_html.rfind("<table", 0, anchor)
    if start < 0:
        return ""
    depth, cursor = 0, start
    pattern = re.compile(r"<(/?)table\b", re.I)
    while cursor < len(page_html):
        found = pattern.search(page_html, cursor)
        if not found:
            break
        depth += -1 if found.group(1) else 1
        cursor = found.end()
        if depth == 0:
            return page_html[start:cursor + 8]
    return page_html[start:]
