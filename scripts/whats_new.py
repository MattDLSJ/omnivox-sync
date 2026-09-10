#!/usr/bin/env python3
"""What changed in the release you just pulled, and what is asked of you.

Two jobs, both of which exist because of how people actually run this.

The first: an update that lands silently teaches you to ignore updates. If a
release fixed the exact thing you patched around last week, you need to be
told, or you keep the workaround and it rots. So this prints the changelog
entries you have not seen, once, and remembers where it got to.

The second: this project is run by people the author has never met, on
machines he does not have, at colleges whose portals he cannot see. The fixes
they make are the only source of information about any of that, and a fix that
stays on one laptop helps one person. So every update ends by asking for them.

Called by `make update`, and safe to run any time. It writes one file, the
marker in state/, and nothing else. On the author's own copy there is no
CHANGELOG.md, because that file is written at publish time and lives only in
the published repository, so this quietly does nothing there.
"""

from __future__ import annotations

import re
import subprocess
import sys
import sys as _sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_sys.path.insert(0, str(REPO_ROOT))
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
MARKER = REPO_ROOT / "state" / ".last-seen-version"

SECTION = re.compile(r"^## (v\d+)", re.M)

ASK = """\
Did you have to fix anything to get this running?

  A wrong path, a label that did not match, a command that only works on
  Windows, a step in START-HERE.md that was wrong for your college. Anything
  you worked around counts, including the ones you are not sure about.

      make report

  writes it up, mostly by itself, and `make send-report` sends it. It checks
  the report for your own private data first and refuses to send if it finds
  any.

  This is the only way a fix reaches anyone else. The author cannot see your
  machine, your college's portal, or your operating system, so the problems
  you hit are invisible to him until you say so."""


def _upstream() -> str:
    """The real URL, never a word in capitals standing in for one.

    This block is printed to a person, at runtime, with no agent in the loop
    to substitute anything. A previous version printed
    URL_OF_THE_REPOSITORY, and running it verbatim succeeds at `git remote
    add`, fails at `git fetch`, and leaves a half-built .git that silences
    this very warning for good.
    """
    try:
        from src.updater import UPSTREAM

        return UPSTREAM
    except Exception:  # noqa: BLE001
        return "https://github.com/MattDLSJ/omnivox-sync.git"


def _is_zip_install() -> bool:
    return not (REPO_ROOT / ".git").exists()


def _new_sections(text: str, since: str) -> list[str]:
    """Every changelog block above `since`, newest first, as written."""
    bounds = [(m.group(1), m.start()) for m in SECTION.finditer(text)]
    out = []
    for index, (version, start) in enumerate(bounds):
        if version == since:
            break
        end = bounds[index + 1][1] if index + 1 < len(bounds) else len(text)
        out.append(text[start:end].strip())
    return out


def main(argv: list[str] | None = None) -> int:
    if _is_zip_install():
        print(
            "\nThis folder is not a git checkout, so it cannot be updated and\n"
            "cannot report a fix. It was downloaded as a ZIP.\n\n"
            "Nothing you have set up is lost. From inside this folder:\n\n"
            "  git init\n"
            f"  git remote add origin {_upstream()}\n"
            "  git fetch origin\n"
            "  git reset --mixed origin/main\n\n"
            "That adopts the history without touching a single one of your\n"
            "files, .env and config.yaml included. See INSTALL.md.",
            file=sys.stderr,
        )
        return 1

    if not CHANGELOG.is_file():
        return 0

    text = CHANGELOG.read_text(encoding="utf-8")
    current = SECTION.search(text)
    if not current:
        return 0
    latest = current.group(1)
    seen = MARKER.read_text(encoding="utf-8").strip() if MARKER.is_file() else ""

    sections = _new_sections(text, seen) if seen else []
    if not seen:
        # First run after adding this. Announcing every release back to v1
        # would be noise, so show the one they are on and start counting.
        sections = _new_sections(text, "")[:1]

    if sections:
        print(f"\nNew since {seen or 'your install'}:\n")
        for section in reversed(sections):
            print("\n".join("  " + line for line in section.splitlines()))
            print()

    print(ASK)

    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(latest + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
