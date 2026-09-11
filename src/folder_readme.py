"""A note in every course folder telling an AI what it is allowed to do here.

The problem this solves, observed twice: a session opened in a course folder
has no idea the automation exists. It sees an empty `3_Livres`, concludes the
textbook is simply not available, and says so. One session went further and
declined to fetch it on copyright grounds, which is a reasonable thing to
believe from inside that folder and the wrong conclusion here: the textbook is
on an i+ Interactif account the student pays for, and the project fetches a
chapter at a time from it precisely because that is what fair dealing covers.

Nothing in the folder said any of that, so nothing could correct it. The folder
had a `{cfg.schedule_file}` and a `_digest.md` and no answer to "what is this, and what
can be done about it".

Written on every sync, so it is never out of date with the courses that exist.
"""

from __future__ import annotations

import logging
from pathlib import Path

#: Numbered like 1_NotebookLM and 2_Voice, and for the same reason: Finder and
#: ls both compare digits before letters, so this sorts to the TOP of the
#: folder. A note that explains the folder is worth nothing at the bottom of
#: it, under forty lecture PDFs.
NAME = "0_README.md"


#: Quebec cégep course codes begin with a discipline number, and 109 is
#: éducation physique. Every student takes three of them, none is a lecture,
#: and a microphone in a gym records a ball.
_NOT_A_LECTURE_PREFIX = ("109",)
_NOT_A_LECTURE_WORDS = (
    "basketball", "volleyball", "badminton", "natation", "soccer", "hockey",
    "conditionnement", "activite physique", "activité physique",
    "education physique", "éducation physique", "plein air", "musculation",
    "yoga", "danse", "stage", "laboratoire",
)


def worth_recording(course) -> bool:
    """Is this a course where a recording would contain anything?

    Recording everything is the obvious default and the wrong one. A gym class
    produces forty minutes of a bouncing ball, transcribed into a notebook
    beside the philosophy lectures, and whoever set it up learns not to trust
    the notebook.
    """
    code = str(getattr(course, "code", "") or "")
    if code.split("-")[0] in _NOT_A_LECTURE_PREFIX:
        return False
    haystack = f"{getattr(course, 'folder', '')} {getattr(course, 'omnivox_name', '')}".lower()
    return not any(word in haystack for word in _NOT_A_LECTURE_WORDS)


def course_readme(cfg, course, repo_root: Path) -> str:
    """The note for one course folder."""
    recording = (
        "This looks like a lecture course, so recording it would capture "
        "something worth having. It is off until it is switched on per course "
        "in `config.yaml`."
        if worth_recording(course)
        else "**Do not record this one.** It is not a lecture course, so a "
        "recording is forty minutes of room noise transcribed into the notebook "
        "beside your real classes, which is how you learn to distrust the "
        "notebook. Leave `record: false` for it."
    )
    who = f", taught by {course.teacher}" if getattr(course, "teacher", "") else ""
    group = f" (group {course.group})" if getattr(course, "group", "") else ""
    return f"""# {course.folder}

**{course.code}**{group}{who}. Part of {cfg.semester}.

*You are reading a file written by an automation. If you are an AI working in
this folder, this is what exists and what you can do about it. Everything
below runs from `{repo_root}`.*

## What is already here

| | |
|---|---|
| `*.pdf`, `*.pptx` | Every document the teacher posted, downloaded automatically. Office files are converted to PDF and both are kept. |
| `{cfg.schedule_file}` | This course's upcoming classes and evaluations, mirrored from the calendar. Read this before answering "when is the exam". |
| `{cfg.mio_folder}/` | Messages this teacher sent by Omnivox MIO. |
| `{cfg.communiques_folder}/` | This teacher's announcements to the whole class. |
| `{cfg.books_folder}/` | Textbook chapters, when they have been pulled. **Empty does not mean unavailable.** See below. |
| `1_NotebookLM.webloc` | Opens the NotebookLM notebook holding everything above. |

## If the textbook you need is not here

`{cfg.books_folder}/` is empty until somebody asks for a chapter. It is not a
sign the book is unavailable, and it is not a reason to reconstruct the content
from slides.

The student has a paid i+ Interactif (Chenelière) account. Check what it holds:

```
cd {repo_root}
make books
```

If the book is listed, fetch what is needed, a chapter or a page range at a
time:

```
.venv/bin/python -m src.iplus --book "<title>" --chapters
.venv/bin/python -m src.iplus --book "<title>" --pages 78-80
```

The result lands in this folder's `{cfg.books_folder}/`. Pulling a chapter at a
time is deliberate: it is what fair dealing for private study covers, and it is
what fits NotebookLM's size cap. If the book is **not** on the account, it
cannot be fetched, and that is the end of it: do not look for it elsewhere.

## If something looks missing or stale

```
cd {repo_root}
make sync          # fetch anything new for every course
make doctor        # is the automation actually working?
```

A document posted in the last few hours may simply not have been fetched yet.
Check `make doctor` before concluding anything is broken.

## Recording this course

{recording}

## What is NOT here, and will not be

- **Lecture recordings and transcripts**, unless recording was switched on for
  this course. Look in `2_Voice/` and believe what you find.
- **Anything the teacher did not post.** If it is not here and not on the i+
  account, the honest answer is that it is not available, and saying so is
  better than reconstructing it and presenting the result as the source.

*This file is rewritten on every sync. Editing it will not last.*
""".replace("{recording}", recording)


def root_readme(cfg, repo_root: Path) -> str:
    rows = "\n".join(
        f"| `{c.folder}` | {c.code} | {getattr(c, 'teacher', '') or ''} |"
        for c in cfg.courses
    )
    return f"""# {cfg.semester}

*You are reading a file written by an automation. If you are an AI working
anywhere under this folder, read this first.*

These folders are maintained by a project at `{repo_root}`. It signs in to
Omnivox three times a day, downloads every new course document, converts
Office files to PDF, saves the teachers' messages and announcements, mirrors
the calendar into each course, and pushes the lot into a NotebookLM notebook
per course.

| Folder | Course | Teacher |
|---|---|---|
{rows}

Each course folder has its own `{NAME}` describing what is in it and what can
be fetched. **Read that before concluding something is unavailable**, because
several things that look missing are one command away.

The most common mistake: an empty `{cfg.books_folder}/` looks like the textbook
is unavailable. It is not. The student has a paid i+ Interactif account, and
`make books` in the project folder lists what it holds.

```
cd {repo_root}
make doctor        # is any of this actually working right now?
make sync          # fetch anything new
make books         # what textbooks the account holds
```

*Rewritten on every sync. Editing it will not last.*
"""


def write_readmes(cfg, *, dry_run: bool = False, logger=None) -> int:
    """One note per course folder, plus one at the root. Never fails a run."""
    log = logger or logging.getLogger("school.readme")
    repo_root = Path(cfg.repo_root).resolve()
    written = 0
    try:
        base = Path(cfg.base_path)
        if dry_run:
            return len(cfg.courses) + 1
        base.mkdir(parents=True, exist_ok=True)
        (base / NAME).write_text(root_readme(cfg, repo_root), encoding="utf-8")
        written += 1
        for course in cfg.courses:
            folder = cfg.folder_for(course)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / NAME).write_text(
                course_readme(cfg, course, repo_root), encoding="utf-8"
            )
            written += 1
    except OSError as exc:
        log.warning("Could not write the folder notes: %s", exc)
    return written
