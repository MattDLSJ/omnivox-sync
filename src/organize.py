"""Find the course files already scattered on the machine, and file them.

Almost nobody installs this in the first week of a semester. They install it in
week three, with four PDFs in Downloads, a syllabus on the desktop, and a
folder called "cegep" somewhere in Documents. The sync fetches everything from
today forward and leaves all of that exactly where it was, which is how you end
up with two copies of a course: the tidy one this made and the real one they
have been using.

Matching is deliberately conservative. A file moves only on evidence that
names the course: its code, or a distinctive word from its name that no other
course shares. Anything else is REPORTED and left alone, because a file moved
into the wrong course is worse than a file not moved: it is gone from where
its owner put it and wrong where it landed.

Nothing is ever deleted. Nothing is overwritten: a name that already exists
gets a numbered suffix.
"""

from __future__ import annotations

import logging
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

#: Where course material realistically sits. Not the whole home directory: a
#: full walk reads a lot of somebody's private filesystem for very little.
SEARCH_DIRS = ("Downloads", "Desktop", "Documents")

#: Coursework, not everything. A .zip might be a course archive and might be a
#: game; the cost of being wrong is higher than the value of being right.
EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    ".odt", ".odp", ".ods", ".rtf", ".txt", ".md", ".epub",
}

#: Words too common to identify a course. "Introduction à la psychologie" and
#: "Introduction à la recherche" share the first two.
STOPWORDS = {
    "de", "du", "des", "la", "le", "les", "et", "a", "au", "aux", "en", "l",
    "introduction", "initiation", "cours", "notes", "document", "cegep",
    "college", "session", "automne", "hiver", "ete", "fall", "winter", "summer",
    "and", "the", "of", "to", "in", "for", "class", "lecture", "chapter",
}

#: Not \b at the ends. Underscore is a word character, so \b never fires in
#: "340-101-MQ_notes.pdf", which is exactly how teachers name files. These
#: lookarounds treat anything that is not alphanumeric as a boundary, which
#: covers the underscore, the space and the dot.
_CODE = re.compile(
    r"(?<![A-Za-z0-9])(\d{3}-[A-Z0-9]{3}-[A-Z]{2})(?![A-Za-z0-9])", re.I
)


def _fold(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


def distinctive_words(courses) -> dict[str, set[str]]:
    """Words that identify exactly one course, keyed by course code.

    A word shared by two courses identifies neither, and this is where a file
    lands in the wrong folder if you are careless: "Initiation à la
    psychologie" and "Initiation à la recherche qualitative" agree on their
    first two words.
    """
    per_course = {}
    for course in courses:
        words = {
            w for w in _fold(f"{course.folder} {course.omnivox_name}").split()
            if len(w) > 3 and w not in STOPWORDS
        }
        per_course[course.code] = words
    counts: dict[str, int] = {}
    for words in per_course.values():
        for word in words:
            counts[word] = counts.get(word, 0) + 1
    return {
        code: {w for w in words if counts[w] == 1}
        for code, words in per_course.items()
    }


@dataclass
class Plan:
    moves: list[tuple[Path, object]] = field(default_factory=list)
    unmatched: list[Path] = field(default_factory=list)

    def by_course(self) -> dict[str, list[Path]]:
        out: dict[str, list[Path]] = {}
        for path, course in self.moves:
            out.setdefault(course.folder, []).append(path)
        return out


def candidates(home: Path, skip: list[Path]) -> list[Path]:
    """Files worth looking at, outside anything this project already owns."""
    skip_resolved = [p.resolve() for p in skip]
    found = []
    for name in SEARCH_DIRS:
        root = home / name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
                continue
            resolved = path.resolve()
            if any(
                resolved == s or s in resolved.parents for s in skip_resolved
            ):
                continue  # already filed, or part of the project itself
            found.append(path)
    return found


def plan_moves(files, courses, *, home: Path) -> Plan:
    """Decide where each file goes. Pure: reads nothing, moves nothing."""
    words = distinctive_words(courses)
    by_code = {c.code: c for c in courses}
    plan = Plan()
    for path in files:
        haystack = _fold(f"{path.stem} {path.parent.name}")

        # A course code in the name is unambiguous, so it wins outright.
        # The raw stem. Stripping spaces glued the code to the next word and
        # stopped it matching at all: "601-101-MQ plan" became "601-101-MQplan".
        code_hit = _CODE.search(path.stem)
        if code_hit:
            course = by_code.get(code_hit.group(1).upper())
            if course:
                plan.moves.append((path, course))
                continue

        matched = [
            by_code[code]
            for code, distinctive in words.items()
            if distinctive and any(f" {w} " in f" {haystack} " for w in distinctive)
        ]
        # Exactly one course, or it is not evidence.
        if len(matched) == 1:
            plan.moves.append((path, matched[0]))
        else:
            plan.unmatched.append(path)
    return plan


def unique_destination(folder: Path, name: str) -> Path:
    """A path that does not already exist, so nothing is ever overwritten."""
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    for n in range(2, 200):
        candidate = folder / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
    return folder / f"{stem} ({len(name)}){suffix}"


def organize(cfg, *, home: Path | None = None, dry_run: bool = False,
             logger=None) -> Plan:
    """Find course files elsewhere on the machine and move them in."""
    log = logger or logging.getLogger("school.organize")
    home = home or Path.home()
    skip = [Path(cfg.base_path), Path(cfg.repo_root)]
    files = candidates(home, skip)
    plan = plan_moves(files, cfg.courses, home=home)

    if dry_run:
        return plan
    for path, course in list(plan.moves):
        folder = cfg.folder_for(course)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = unique_destination(folder, path.name)
            shutil.move(str(path), str(target))
            log.info("Filed %s -> %s", path.name, course.folder)
        except OSError as exc:
            log.warning("Could not move %s: %s", path.name, exc)
            plan.moves.remove((path, course))
    return plan


def summary(plan: Plan) -> str:
    if not plan.moves and not plan.unmatched:
        return "Nothing on this machine looked like course material."
    lines = []
    if plan.moves:
        lines.append(f"Filed {len(plan.moves)} file(s) already on this machine:")
        for folder, paths in sorted(plan.by_course().items()):
            lines.append(f"  {folder}")
            for path in paths[:8]:
                lines.append(f"      {path.name}")
            if len(paths) > 8:
                lines.append(f"      ... and {len(paths) - 8} more")
    if plan.unmatched:
        lines.append(
            f"\n{len(plan.unmatched)} other document(s) were left where they are, "
            "because\nnothing in the name said which course they belong to. "
            "Nothing was deleted."
        )
    return "\n".join(lines)
