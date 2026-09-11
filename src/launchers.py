"""Double-clickable launchers, because Windows hides windows you did not ask for.

Two problems, one answer.

The first is a real Windows behaviour a tester found and named precisely: a GUI
launched from a background or subshell process is created with SW_HIDE, so the
Playwright sign-in window exists and is invisible. The command reports success,
nothing appears, and the person waits for a browser that is already open where
they cannot see it. A .cmd double-clicked from Explorer gets a real console and
a visible window.

The second is that macOS has had a "Sync School.app" sitting next to the course
folders since the start, and Windows had nothing: no button, no mention of one.

So both platforms get both launchers, next to the course folders where somebody
will actually find them rather than inside the project.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

IS_WINDOWS = os.name == "nt"

WINDOWS_SYNC = """@echo off
title Sync School
cd /d "{repo}"
echo Checking Omnivox for anything new...
echo.
"{python}" -m src.omnivox_sync
echo.
echo Done. This window stays open so you can read what happened.
pause
"""

WINDOWS_LOGIN = """@echo off
title Sign in to Omnivox
cd /d "{repo}"
echo A browser window is about to open.
echo.
echo Type your student number and password into it. If Omnivox e-mails you a
echo six-digit code, enter that too, and TICK "J'utilise un appareil de
echo confiance" before you validate. Missing that box is what makes this stop
echo working tomorrow.
echo.
echo Run this file by DOUBLE-CLICKING it. Windows hides windows opened by
echo background processes, so a sign-in started any other way can be invisible.
echo.
"{python}" -m src.omnivox_sync --login
echo.
pause
"""

UNIX_SYNC = """#!/bin/sh
# Sync School. Double-clickable from Finder if you like.
cd "{repo}" || exit 1
echo "Checking Omnivox for anything new..."
"{python}" -m src.omnivox_sync
echo
# Terminal profiles set to close on a clean exit take the report with them,
# which is the whole reason somebody double-clicked this. The Windows
# launchers have always ended with `pause`; this is the same thing.
printf "Done. Press return to close this window."
read -r _
"""

UNIX_LOGIN = """#!/bin/sh
# Sign in to Omnivox. Use this when the sync says it needs you.
cd "{repo}" || exit 1
echo "A browser window will open on Omnivox. Sign in there."
echo
"{python}" -m src.omnivox_sync --login
echo
printf "Done. Press return to close this window."
read -r _
"""


def _python(repo: Path) -> Path:
    return repo / (".venv/Scripts/python.exe" if IS_WINDOWS else ".venv/bin/python")


def write_launchers(cfg, *, logger=None) -> list[Path]:
    """Put the buttons next to the course folders. Returns what it wrote."""
    log = logger or logging.getLogger("school.launchers")
    repo = Path(cfg.repo_root).resolve()
    base = Path(cfg.base_path)
    python = _python(repo)
    written: list[Path] = []
    try:
        base.mkdir(parents=True, exist_ok=True)
        if IS_WINDOWS:
            files = {
                "Sync School.cmd": WINDOWS_SYNC,
                "Sign in to Omnivox.cmd": WINDOWS_LOGIN,
            }
        else:
            # Both, same as Windows. The note here used to say macOS already
            # gets a real .app from `make button`, so a sign-in button was not
            # needed. install.py never runs `make button`, so on a fresh Mac
            # install there was no .app and no sign-in launcher either:
            # Windows recovered from a revoked trusted device with a double
            # click and macOS required knowing to type `make login`.
            files = {
                "Sync School.command": UNIX_SYNC,
                "Sign in to Omnivox.command": UNIX_LOGIN,
            }
        for name, template in files.items():
            path = base / name
            path.write_text(
                template.format(repo=str(repo), python=str(python)), encoding="utf-8"
            )
            if not IS_WINDOWS:
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
            written.append(path)
            log.info("Wrote launcher: %s", name)
    except OSError as exc:
        log.warning("Could not write the launchers: %s", exc)
    return written
