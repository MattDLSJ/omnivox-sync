"""A one-double-click link to each course's NotebookLM notebook, in its folder.

Why this exists
---------------
The course folder is where the work happens: the plans de cours, the decks, the
transcripts. The notebook that holds the same material is three navigations away
in a browser tab that is never already open. Putting the link *in the folder*
removes the gap.

The file is named so it sorts to the top. Finder compares leading digits
numerically, so "1_NotebookLM" lands above "703_A26_01_Intro_JV_Et.pdf" rather
than between 6 and 8. That is the whole reason for the number.

Format: `.webloc`, which is the native macOS internet-location file. Double
click, the notebook opens in the default browser. It is a plain XML plist, so
it diffs and it is readable, unlike the binary form Finder writes.

Idempotence matters more than it looks. These folders sit inside a live Google
Drive mirror, and rewriting a file with identical content still costs an upload
and a new version in Drive's history. So an unchanged shortcut is left alone.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from src.common import Config, ConfigError, Course, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Sorts above every document in the folder. See the module docstring.
SHORTCUT_NAME = "1_NotebookLM.webloc"

NOTEBOOK_URL = "https://notebooklm.google.com/notebook/{id}"

_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>URL</key>
\t<string>{url}</string>
</dict>
</plist>
"""


def webloc(url: str) -> str:
    """The file body for a URL. Escaped, because a notebook id is put in raw."""
    return _TEMPLATE.format(url=escape(url))


def shortcut_path(cfg: Config, course: Course) -> Path:
    return cfg.folder_for(course) / SHORTCUT_NAME


def write_shortcut(path: Path, url: str) -> bool:
    """Write the shortcut. Returns True only if the file actually changed.

    See the module docstring on why an unchanged file must not be rewritten.
    """
    body = webloc(url)
    try:
        if path.read_text(encoding="utf-8") == body:
            return False
    except (OSError, UnicodeDecodeError):
        pass  # missing, or a binary .webloc written by Finder: replace it
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return True


def ensure_course_folders(cfg) -> list[Path]:
    """Give every course its numbered subfolders, whether or not it has content.

    An empty 3_Livres in a course with no digital manual is not clutter: it is
    the answer to "where does this go", which is the whole point of numbering
    them. Creating them only on first write means the shape of a course folder
    depends on what has happened in it, and the shape is supposed to be the
    same everywhere.
    """
    made = []
    for course in cfg.courses:
        subs = (getattr(cfg, "recordings_folder", ""), getattr(cfg, "books_folder", ""))
        for sub in subs:
            if not sub or Path(sub).is_absolute():
                continue  # an absolute recordings root lives outside the course
            folder = cfg.folder_for(course) / sub
            if not folder.exists():
                folder.mkdir(parents=True, exist_ok=True)
                made.append(folder)
    return made


def build(cfg: Config, *, dry_run: bool = False) -> list[str]:
    """Create or refresh one shortcut per course. Returns human-readable lines.

    A course whose notebook does not exist yet is reported and skipped, never
    guessed at: a .webloc pointing at a notebook that is not there is worse
    than no .webloc, because it looks like it works.
    """
    from src.notebooklm_upload import NlmUploader, find_notebook

    notebooks = NlmUploader().list_notebooks()
    lines: list[str] = []
    if not dry_run:
        for folder in ensure_course_folders(cfg):
            lines.append(f"  +  {folder.parent.name}: {folder.name}/")
    for course in cfg.courses:
        found = find_notebook(notebooks, course.notebook)
        if found is None:
            lines.append(f"  ?  {course.label()}: aucun notebook nommé « {course.notebook} »")
            continue
        path = shortcut_path(cfg, course)
        if dry_run:
            lines.append(f"  ~  {course.label()}: {path}")
            continue
        changed = write_shortcut(path, NOTEBOOK_URL.format(id=found.id))
        lines.append(f"  {'+' if changed else '='}  {course.label()}: {path.name}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.shortcuts",
        description="Put a NotebookLM shortcut at the top of each course folder.",
    )
    parser.add_argument("--config", default=str(REPO_ROOT / "config.yaml"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2

    for line in build(cfg, dry_run=args.dry_run):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
