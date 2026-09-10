#!/usr/bin/env python3
"""Ship an update from this repo to the public one.

The first version of this force-pushed a brand new single-commit repository
every time. That kept the private history out, which was the whole point, but
it made the published repo useless to anyone who had already cloned it: a
force-push of unrelated history breaks `git pull`, so every update meant
re-cloning, and there was no way to see what had changed between two of them.

So the public repo gets a history of its own. Each publish adds ONE commit on
top of what is already there, carrying the tree at HEAD and a message you
write for the public. The private history is never read, never copied and
never referenced. What people downstream get is an ordinary repository they
can pull from, with a changelog and version tags.

  make publish MESSAGE="what changed, in one line"

Creating the public repository in the first place is a different job, and
`make public-snapshot` does that one.

Two guards you cannot skip. The tests must pass, and the release note goes
through the same private-data check as everything else: a note is prose you
write in a hurry about work you just did, which is exactly how the private
commit messages in this repo ended up naming a class its author skipped.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_private  # noqa: E402
import public_snapshot as snap  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
WORK = REPO_ROOT / "build" / "publish"
CHANGELOG = "CHANGELOG.md"

#: Kept across the tree swap because it belongs to the published repo and not
#: to this one. Everything else in the public checkout is replaced wholesale.
PRESERVE = {".git", CHANGELOG}

#: public_snapshot leaves this in the directory it builds, to recognise its own
#: output next time. It must not ride along into the published commit.
BUILD_LEFTOVERS = {".snapshot-build"}


def _run(*args: str, cwd: Path, check: bool = True) -> str:
    got = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and got.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{got.stderr.strip()}")
    return got.stdout.strip()


def _remote() -> str:
    url = _run("config", "--get", "snapshot.remote", cwd=REPO_ROOT, check=False)
    if not url:
        raise SystemExit(
            "No publish target set. Create the public repository once with\n"
            "`make public-snapshot`, then remember where it lives:\n\n"
            "  git config snapshot.remote <the url it printed>"
        )
    return url


def _preflight() -> None:
    dirty = _run("status", "--porcelain", cwd=REPO_ROOT)
    if dirty:
        raise SystemExit(
            "Uncommitted changes here, so the published tree would not match\n"
            "any commit of yours. Commit or stash first:\n\n" + dirty
        )
    print("Running the offline tests before publishing anything...")
    tests = subprocess.run(
        # No -q here: pyproject already sets it in addopts, and a second one
        # makes it -qq, which suppresses the "N passed" summary line entirely.
        [sys.executable, "-m", "pytest", "-m", "not live"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if tests.returncode != 0:
        print(tests.stdout[-2000:])
        raise SystemExit("Tests fail. Not publishing.")
    print(f"  {tests.stdout.strip().splitlines()[-1]}")


def _check_message(message: str) -> None:
    """The release note is prose written in a hurry. Treat it like one."""
    note = WORK.parent / "_release-note.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(message, encoding="utf-8")
    try:
        code = check_private.main(
            ["--message-file", str(note), "--label", "release note"]
        )
    finally:
        note.unlink(missing_ok=True)
    if code == check_private.EXIT_HIT:
        raise SystemExit("The release note names something private. Reword it.")
    if code == check_private.EXIT_CANNOT_CHECK:
        raise SystemExit("Could not check the release note, so it is not published.")


def _fetch_public(url: str) -> Path:
    if WORK.exists():
        snap._assert_safe_to_overwrite(REPO_ROOT, WORK)
        shutil.rmtree(WORK)
    WORK.parent.mkdir(parents=True, exist_ok=True)
    print(f"Fetching the published repo from {url}")
    # Through the helper, so "repository not found" or "authentication failed"
    # is readable instead of a Python traceback with the real message eaten.
    _run("clone", "--quiet", url, str(WORK), cwd=REPO_ROOT)
    (WORK / ".git" / "snapshot-build").write_text("built by scripts/publish.py\n")
    return WORK


def _swap_tree(public: Path) -> None:
    """Replace the checkout with the tree at HEAD, keeping .git and the log."""
    for entry in public.iterdir():
        if entry.name in PRESERVE:
            continue
        shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    snap._export_head(REPO_ROOT, public / "_incoming")
    incoming = public / "_incoming"
    for entry in incoming.iterdir():
        if entry.name in PRESERVE or entry.name in BUILD_LEFTOVERS:
            continue
        shutil.move(str(entry), str(public / entry.name))
    shutil.rmtree(incoming, ignore_errors=True)


def _summarise(public: Path) -> str:
    """What changed, from the FILES. Never from the private commit messages,
    which are the thing this whole exercise exists to keep out."""
    stat = _run("diff", "--stat", "HEAD", cwd=public, check=False)
    names = _run("diff", "--name-status", "HEAD", cwd=public, check=False)
    if not names:
        return ""
    added = sum(1 for line in names.splitlines() if line.startswith("A"))
    modified = sum(1 for line in names.splitlines() if line.startswith("M"))
    removed = sum(1 for line in names.splitlines() if line.startswith("D"))
    print(f"\n{added} added, {modified} changed, {removed} removed:\n")
    print("\n".join("  " + line for line in stat.splitlines()[-24:]))
    return names


def _next_version(public: Path) -> str:
    """Highest released number plus one, from the tags AND the changelog.

    Tags alone were not enough: a tag that failed to push, or was deleted,
    silently restarted the count on top of a repo already at v6.
    """
    tags = _run("tag", "--list", "v*", cwd=public, check=False).split()
    numbers = [int(t[1:]) for t in tags if t[1:].isdigit()]
    log = public / CHANGELOG
    if log.exists():
        import re

        numbers += [
            int(m) for m in re.findall(r"^## v(\d+)", log.read_text(encoding="utf-8"), re.M)
        ]
    return f"v{max(numbers) + 1 if numbers else 1}"


def _write_changelog(public: Path, version: str, message: str) -> None:
    path = public / CHANGELOG
    head = (
        "# Changelog\n\n"
        "Every released version of this project, newest first. Written by hand\n"
        "at publish time; the development history it comes from is private.\n"
    )
    existing = path.read_text(encoding="utf-8") if path.exists() else head
    entry = f"\n## {version} ({date.today().isoformat()})\n\n{message.strip()}\n"
    if path.exists():
        marker = existing.index("\n## ") if "\n## " in existing else len(existing)
        path.write_text(existing[:marker] + entry + existing[marker:], encoding="utf-8")
    else:
        path.write_text(head + entry, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-m", "--message", help="the release note, one or two lines")
    parser.add_argument(
        "--message-env",
        help="name of an environment variable holding the note, so it never "
        "passes through a shell that would mangle quotes and dollar signs",
    )
    parser.add_argument("--dry-run", action="store_true", help="stop before pushing")
    args = parser.parse_args(argv)

    url = _remote()
    name, email = snap._identity(REPO_ROOT)
    _preflight()

    public = _fetch_public(url)
    _swap_tree(public)
    _run("add", "--all", cwd=public)

    names = _summarise(public)
    if not names:
        print("\nNothing changed since the last release. Nothing to publish.")
        return 0

    removed = [l.split("\t", 1)[1] for l in names.splitlines() if l.startswith("D")]
    if removed:
        # A file somebody else added to the public repo disappears here without
        # a word, because the tree is replaced wholesale. Say so out loud.
        print("\nThese files exist in the published repo and will be DELETED:")
        for name in removed:
            print(f"    {name}")
        if not sys.stdin.isatty():
            raise SystemExit(
                "Refusing to delete published files without a confirmation. "
                "Run this in a terminal."
            )
        if input("Delete them? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SystemExit("Nothing published.")

    snap._assert_nothing_forbidden(public)

    message = args.message
    if not message and args.message_env:
        message = os.environ.get(args.message_env, "").strip()
    if not message:
        if not sys.stdin.isatty():
            raise SystemExit("No release note. Pass MESSAGE=\"...\" or run this in a terminal.")
        print("\nOne or two lines, for the people downloading this. Blank to abort.")
        message = input("> ").strip()
    if not message:
        raise SystemExit("No release note. Nothing published.")
    _check_message(message)

    version = _next_version(public)
    _write_changelog(public, version, message)
    _run("add", CHANGELOG, cwd=public)
    _run("config", "user.name", name, cwd=public)
    _run("config", "user.email", email, cwd=public)
    _run("commit", "--quiet", "-m", f"{version}: {message}", cwd=public)
    _run("tag", version, cwd=public)

    verdict = snap._verify(public)
    if verdict != check_private.EXIT_CLEAN:
        raise SystemExit("The built release did not pass the private-data check. Not pushed.")

    if args.dry_run:
        print(f"\n[dry-run] {version} is built in {public} and was NOT pushed.")
        return 0

    print(f"\nPushing {version}...")
    # One atomic push. Two separate ones could land the commit and lose the
    # tag, and _next_version reads only tags, so the NEXT release would reuse
    # the same number and the changelog would carry two entries for it.
    _run("push", "--atomic", "origin", "HEAD", version, cwd=public)
    print(
        f"\nPublished {version}.\n"
        f"  {url}\n\n"
        "Anyone who already has it updates with `make update`, or plain "
        "`git pull` if they are not on a Mac."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
