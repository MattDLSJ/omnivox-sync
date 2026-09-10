#!/usr/bin/env python3
"""Render a launchd plist from its template, schedule included.

The shell installer did this with two `sed` substitutions, which was fine
while the only variables were two absolute paths. The sync schedule now comes
from config.yaml, and a schedule is a block of XML rather than a word, so it
is generated here instead: sed can substitute a path, and injecting
multi-line markup through it is how you get a plist that fails to parse at
2 a.m. with no error anybody sees.

The two places a sync time appears must agree. launchd decides when the job
runs, and next_window() decides when a failed run has been superseded rather
than retried; if those disagree, a retry either fires forever or never fires.
Both now read the same config key.

    python scripts/render_plist.py com.school.sync

Prints the rendered plist on stdout. Validity is the caller's job to check
with plutil, which is what install_launchd.sh does.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SCHEDULE_MARK = "__SCHEDULE__"


def schedule_xml(times, indent: str = "        ") -> str:
    """The StartCalendarInterval entries, one dict per time of day."""
    return "\n".join(
        f"{indent}<dict><key>Hour</key><integer>{hour}</integer>"
        f"<key>Minute</key><integer>{minute}</integer></dict>"
        for hour, minute in times
    )


def render(label: str, repo_root: Path = REPO_ROOT) -> str:
    template = repo_root / "launchd" / f"{label}.plist.template"
    if not template.is_file():
        raise SystemExit(f"Missing template: {template}")
    text = template.read_text(encoding="utf-8")

    python = repo_root / ".venv" / "bin" / "python"
    text = text.replace("__REPO__", str(repo_root)).replace("__PYTHON__", str(python))

    if SCHEDULE_MARK in text:
        # Imported late and defensively: a broken config must not stop an
        # uninstall, and the default schedule is a perfectly good fallback.
        try:
            sys.path.insert(0, str(repo_root))
            from src.common import DEFAULT_SYNC_TIMES, load_config

            config = repo_root / "config.yaml"
            times = (
                load_config(config, repo_root=repo_root).sync_times
                if config.is_file()
                else DEFAULT_SYNC_TIMES
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"warning: could not read sync_times ({type(exc).__name__}: {exc}); "
                "using the default schedule",
                file=sys.stderr,
            )
            times = ((7, 30), (12, 15), (18, 30))
        text = text.replace(SCHEDULE_MARK, schedule_xml(times))
    return text


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        raise SystemExit("usage: render_plist.py <label>")
    sys.stdout.write(render(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
