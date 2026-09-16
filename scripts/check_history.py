#!/usr/bin/env python3
"""Refuse to update when the release history was rewritten, and say why.

`git pull` has one failure here that its own message explains badly. If the
published repository's history is ever rewritten, an existing clone shares no
commit with it at all, and git reports "refusing to merge unrelated histories".
To somebody who has never rewritten a history that reads like their copy is
corrupt, and the obvious reactions are both wrong: deleting the folder throws
away `.env`, `config.yaml` and the signed-in browser profile, and forcing a
merge produces a tree that is neither version.

It happened here on 2026-09-16. A Wrangler cache file had been publishing the
name of a Cloudflare account belonging to somebody with no connection to this
project, from v33 to v48. Removing it from the current release did not remove
it from the forty-nine commits behind, so the history was rewritten.

The fix is one command and it is safe, which is the part worth saying out
loud: `git reset --hard origin/main` only touches files git tracks. Everything
this project actually holds for you, the credentials, the config, the state
directory, the logs, your course folders, is either gitignored or outside the
repository entirely. None of it is at risk.

Exits 0 when there is nothing to say, 1 when the caller should stop.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=60
    )


def upstream_branch() -> str:
    for candidate in ("origin/main", "origin/master"):
        if git("rev-parse", "--verify", "--quiet", candidate).returncode == 0:
            return candidate
    return ""


def shares_history(branch: str) -> bool:
    """False when the two sides have no common ancestor at all."""
    return git("merge-base", "HEAD", branch).returncode == 0


def working_tree_is_clean() -> bool:
    return not git("status", "--porcelain").stdout.strip()


def main() -> int:
    if git("rev-parse", "--git-dir").returncode != 0:
        return 0  # a ZIP install; the updater handles that elsewhere

    if git("fetch", "--quiet", "origin").returncode != 0:
        return 0  # offline, or no remote. Let the pull report it.

    branch = upstream_branch()
    if not branch or shares_history(branch):
        return 0

    print()
    print("The published history was rewritten, so your copy and the release")
    print("no longer share any commit. This is not damage and nothing of yours")
    print("is lost. `git pull` cannot cross it, and would tell you it is")
    print("'refusing to merge unrelated histories'.")
    print()
    print("Why it happened: a file that should never have shipped was removed")
    print("from every past release, not just the current one.")
    print()
    if working_tree_is_clean():
        print("Your copy has no edits, so one command moves you across:")
        print()
        print("    git reset --hard origin/main && make update")
        print()
        print("That only replaces files git tracks. Your .env, your")
        print("config.yaml, the signed-in browser profile in state/, your logs")
        print("and every course folder are untouched: none of them are in git.")
    else:
        print("You have local edits, so save them first:")
        print()
        print("    git stash")
        print("    git reset --hard origin/main")
        print("    git stash pop")
        print()
        print("Your .env, config.yaml, state/ and course folders are not in")
        print("git and are untouched either way.")
    print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
