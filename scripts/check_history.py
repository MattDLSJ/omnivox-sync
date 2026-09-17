#!/usr/bin/env python3
"""Refuse to update when the release history was rewritten, and say why.

`git pull` has one failure here that its own message explains badly. It
happened on 2026-09-16: a file that should never have shipped had been in every
release from v33 to v48, and removing it from the current release did not
remove it from the releases behind, so the history was rewritten.

Releases v1 to v32 came out of that untouched. Every release from v33 on was
rebuilt with the same message and the same dates, but as a different commit.
So an existing copy is not unrelated to the new release: it shares v1 to v32
with it, and then holds its own old copies of whichever later releases it had
already pulled. Git sees two branches that went separate ways after v32, and
`git pull --ff-only` says "Not possible to fast-forward", which is word for
word what it says about local work of your own. The obvious reactions are both
wrong: deleting the folder throws away `.env`, `config.yaml` and the signed-in
browser profile, and merging produces a tree that is neither version.

What tells the two apart is that a rebuilt release keeps its subject and its
dates, and a commit of yours has no twin in the release. Each commit this copy
has and the release does not is looked for in the release by subject, author
date and committer date. The committer date is there on purpose: rebuilding
kept it, and an amend, a rebase or a cherry-pick of your own does not, so a
release you amended by hand counts as yours rather than as disposable.

- None found: not a rewrite. The pull's own message is the right one.
- All found: the copy holds nothing but old releases, and a reset loses nothing.
- Some not found: those are your commits, and they go on a branch first.

A copy with no common commit at all is treated as rewritten too, whatever it
holds, since there is no pull that could ever cross that.

The fix is safe, which is the part worth saying out loud: `git reset --hard
origin/main` only touches files git tracks. Everything this project actually
holds for you, the credentials, the config, the state directory, the logs, your
course folders, is either gitignored or outside the repository entirely. None
of it is at risk.

Exits 0 when there is nothing to say, 1 when the caller should stop.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> subprocess.CompletedProcess:
    # UTF-8 explicitly: commit subjects are UTF-8, and Windows would otherwise
    # decode them as cp1252, which cannot decode every byte they contain.
    return subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )


def upstream_branch() -> str:
    for candidate in ("origin/main", "origin/master"):
        if git("rev-parse", "--verify", "--quiet", candidate).returncode == 0:
            return candidate
    return ""


def shares_history(branch: str) -> bool:
    """False when the two sides have no common ancestor at all."""
    return git("merge-base", "HEAD", branch).returncode == 0


def is_behind_or_level(branch: str) -> bool:
    """True when a pull is a plain fast-forward, or nothing at all."""
    return git("merge-base", "--is-ancestor", "HEAD", branch).returncode == 0


def commits(rev_range: str) -> list[tuple[str, tuple[str, str, str]]]:
    """(short hash, (subject, author date, committer date)), newest first."""
    listed = git("log", "--format=%h%x1f%s%x1f%at%x1f%ct", rev_range)
    out = []
    for line in listed.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            out.append((parts[0], (parts[1], parts[2], parts[3])))
    return out


def own_commits(branch: str) -> tuple[int, list[tuple[str, str]]]:
    """How many local-only commits are old copies of a release, and the rest.

    The rest come back as (short hash, subject), oldest first, which is the
    order they would have to be cherry-picked in.
    """
    released = {key for _, key in commits(branch)}
    rebuilt, mine = 0, []
    for short, key in commits(f"{branch}..HEAD"):
        if key in released:
            rebuilt += 1
        else:
            mine.append((short, key[0]))
    return rebuilt, mine[::-1]


def working_tree_is_clean() -> bool:
    # Untracked files do not count. A reset leaves them alone and a plain
    # `git stash` does not take them, so calling them edits would send
    # somebody down the stash route, where `git stash pop` then brings back an
    # older stash of theirs instead of nothing.
    return not git("status", "--porcelain", "--untracked-files=no").stdout.strip()


def free_branch_name(base: str = "my-changes") -> str:
    """A branch name not already taken. If `git branch` failed on a name that
    exists, the reset on the next line would still run and take the commits
    with it."""
    name, n = base, 1
    while git("rev-parse", "--verify", "--quiet", f"refs/heads/{name}").returncode == 0:
        n += 1
        name = f"{base}-{n}"
    return name


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def explain(branch: str, rebuilt: int, mine: list[tuple[str, str]], shared: bool) -> None:
    clean = working_tree_is_clean()
    keep = free_branch_name() if mine else ""
    them = "it" if len(mine) == 1 else "them"

    print()
    print("The published history was rewritten, so your copy and the release")
    print("have gone separate ways. This is not damage, and nothing of yours")
    print('has been lost. `git pull` cannot cross it and would only say "Not')
    print('possible to fast-forward", which makes your copy look broken.')
    print()
    print("Why it happened: a file that should never have shipped was removed")
    print("from every past release, not just the current one. The releases it")
    print("had been in were rebuilt with the same messages and dates.")
    if rebuilt:
        print(f"Your copy still holds the old versions of {plural(rebuilt, 'release')}.")
    elif not shared:
        print("Your copy and the release no longer share a single commit.")
    print()

    if mine:
        print(f"Your copy also has {plural(len(mine), 'commit')} of your own, which")
        print(f"the release does not. A reset on its own would drop {them}, so a")
        print(f"branch keeps {them} first:")
        print()
        for short, subject in mine:
            print(f"    {short} {subject[:66]}")
        print()
    elif clean:
        print("Everything your copy has that the release does not is an old")
        print("release, and there are no edits, so a reset loses nothing.")
        print()
    if not clean:
        print("You have edits you have not committed. `git stash` saves them")
        print("and `git stash pop`, at the end, puts them back.")
        print()

    steps = []
    if not clean:
        steps.append("git stash")
    if mine:
        steps.append(f"git branch {keep}")
    steps += [f"git reset --hard {branch}", "git fetch --force --tags origin", "make update"]
    if not clean:
        steps.append("git stash pop")

    print("Run these one at a time, in this order, and stop if one fails:")
    print()
    for step in steps:
        print(f"    {step}")
    print()
    print("The fetch line moves your version tags onto the rebuilt releases;")
    print("without it they keep pointing at the old ones.")
    print()

    if mine:
        print(f"The branch {keep} keeps {them} exactly as before, and the reset")
        print("does not touch it. Once you are across, bring back what you still")
        print("want, oldest first, skipping anything the release now does itself:")
        print()
        for short, _ in mine:
            print(f"    git cherry-pick {short}")
        print()
    print("A reset only replaces files git tracks. Your .env, your config.yaml,")
    print("the signed-in browser profile in state/, your logs and every")
    print("course folder are untouched: none of them are in git.")
    print()


def check() -> int:
    if git("rev-parse", "--git-dir").returncode != 0:
        return 0  # a ZIP install; the updater handles that elsewhere

    if git("fetch", "--quiet", "origin").returncode != 0:
        return 0  # offline, or no remote. Let the pull report it.

    branch = upstream_branch()
    if not branch or git("rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
        return 0
    if is_behind_or_level(branch):
        return 0

    shared = shares_history(branch)
    rebuilt, mine = own_commits(branch)
    if shared and not rebuilt:
        return 0  # ordinary work of your own; the pull says so well enough

    explain(branch, rebuilt, mine, shared)
    return 1


def main() -> int:
    try:
        return check()
    except (subprocess.TimeoutExpired, OSError):
        return 0  # a stalled network or no git at all. Let the pull report it.


if __name__ == "__main__":
    raise SystemExit(main())
