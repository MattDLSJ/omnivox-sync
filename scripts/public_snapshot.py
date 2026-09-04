#!/usr/bin/env python3
"""Build a shareable copy of this repo with no history and no author.

Two problems that a commit cannot fix:

  A commit message is immutable. Some of the messages in this history name a
  class that was skipped, a distance from campus, and an evaluation weighting.
  Rewriting them changes every SHA and GitHub still serves the old objects to
  anyone who knows one, so a force-push is not a delete.

  So is an author line. Every commit carries a name and an e-mail in the object
  itself, in no file, which is why no amount of editing files removes it.

The way out is not to repair the history. It is to not send one. This exports
the tree at HEAD, drops it into a fresh repository with exactly one commit and
an identity you choose, and refuses to finish if anything personal survived.

  make public-snapshot

The result is a directory that is a complete git repo, ready to push wherever
you want it. Your own repository is untouched: it keeps the full history,
because that history is worth having. It just stops being the thing you hand
to other people.

Who the snapshot commit is authored by comes from git config, so it lives on
this machine and not in any tracked file:

  git config snapshot.name  "your-github-handle"
  git config snapshot.email "0000000+handle@users.noreply.github.com"
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_private  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "build" / "public-snapshot"

#: Nothing matching these may exist in the export at all. Belt and braces:
#: they are gitignored already, so a hit here means .gitignore was edited wrong.
FORBIDDEN = (
    ".env",
    "config.yaml",
    ".private-patterns",
    "horaires-cegep",
)

#: These directories DO ship, because the code expects them to exist, but only
#: ever as an empty shell. state/ holds the browser profile with the live
#: session cookies and logs/ holds full-page screenshots of the portal, so a
#: single stray file here is the worst leak in the project.
MUST_BE_EMPTY = ("state", "logs")
ALLOWED_IN_EMPTY = {".gitkeep"}

COMMIT_MESSAGE = """Initial commit

A snapshot of a working tree, published without its development history.

The history it came from is a personal engineering log: it names the author,
the classes, the teachers and the incidents behind each fix. That is useful to
keep and not useful to publish, and a commit message cannot be edited after
the fact. So this repository starts here instead.
"""


def _git(*args: str, cwd: Path, check: bool = True) -> str:
    got = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    if check and got.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{got.stderr.strip()}")
    return got.stdout.strip()


def _identity(repo_root: Path) -> tuple[str, str]:
    name = _git("config", "--get", "snapshot.name", cwd=repo_root, check=False)
    email = _git("config", "--get", "snapshot.email", cwd=repo_root, check=False)
    if not name or not email:
        raise SystemExit(
            "No snapshot identity set. This is the whole point of the exercise,\n"
            "so it is not guessed. Pick a name and an address that you are happy\n"
            "to see on a public commit, then:\n\n"
            "  git config snapshot.name  \"your-github-handle\"\n"
            "  git config snapshot.email \"0000000+handle@users.noreply.github.com\"\n\n"
            "GitHub gives you that noreply address under Settings > Emails."
        )

    # A script whose entire purpose is publishing without an author will
    # otherwise happily stamp your real address on the commit and call the
    # result clean.
    for needle in check_private.collect_needles(repo_root):
        if needle.search(name) or needle.search(email):
            raise SystemExit(
                f"snapshot.name / snapshot.email match one of your own private\n"
                f"patterns ({needle.label}). That is the one thing this script\n"
                "exists to keep off the commit. Set them to a handle and a\n"
                "noreply address you are happy to publish."
            )
    if not email.endswith("users.noreply.github.com"):
        print(
            f"WARNING: snapshot.email is {email}, which is not a GitHub noreply\n"
            "address. It will be public on every commit of the snapshot.\n",
            file=sys.stderr,
        )
    return name, email


def _assert_clean_tree(repo_root: Path) -> None:
    dirty = _git("status", "--porcelain", cwd=repo_root)
    if dirty:
        raise SystemExit(
            "The working tree has uncommitted changes, so a snapshot of HEAD\n"
            "would not be the code you are looking at. Commit or stash first:\n\n"
            + dirty
        )


#: Proof that this directory is a previous build of ours and may be replaced.
#: It lives inside .git/ rather than in the tree, because a marker in the tree
#: is deleted before the commit and then every rebuild refuses its own output.
BUILD_MARKER = Path(".git") / "snapshot-build"


def _is_our_build(out: Path) -> bool:
    return (out / BUILD_MARKER).exists() or (out / ".snapshot-build").exists()


def _assert_safe_to_overwrite(repo_root: Path, out: Path) -> None:
    """This function deletes a directory. Be extremely sure which one.

    Pointed at a repository, the unguarded version removed it, .git included.
    """
    if out == repo_root or out in repo_root.parents:
        raise SystemExit(f"refusing to build the snapshot into {out}: it contains this repo.")
    if not out.exists():
        return
    if not out.is_dir():
        raise SystemExit(f"{out} exists and is not a directory.")
    if _is_our_build(out):
        return
    if (out / ".git").exists():
        raise SystemExit(
            f"{out} is a git repository that this script did not create.\n"
            "Refusing to delete it. Pick an empty directory, or a previous\n"
            "snapshot build, with --out."
        )
    if any(out.iterdir()):
        raise SystemExit(
            f"{out} is not empty and was not built by this script.\n"
            "Refusing to delete it. Pick an empty directory with --out."
        )


def _export_head(repo_root: Path, out: Path) -> None:
    _assert_safe_to_overwrite(repo_root, out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # The marker that makes this directory safe to overwrite next time, and
    # nothing else: it is removed before the commit.
    (out / ".snapshot-build").write_text("built by scripts/public_snapshot.py\n")

    handle = tempfile.NamedTemporaryFile(suffix=".tar", delete=False)
    archive = Path(handle.name)
    try:
        got = subprocess.run(
            ["git", "archive", "--format=tar", "HEAD"],
            cwd=str(repo_root),
            stdout=handle,
            stderr=subprocess.PIPE,
            text=False,
        )
        handle.close()
        if got.returncode != 0:
            raise SystemExit(f"git archive failed:\n{got.stderr.decode(errors='replace')}")
        with tarfile.open(archive) as tar:
            # git archive emits only regular files and dirs from the tree, but
            # refuse anything that climbs out of the target anyway.
            for member in tar.getmembers():
                target = (out / member.name).resolve()
                if out.resolve() not in target.parents and target != out.resolve():
                    raise SystemExit(f"refusing path outside the export: {member.name}")
            tar.extractall(out, filter="data")
    finally:
        handle.close()
        archive.unlink(missing_ok=True)


def _assert_nothing_forbidden(out: Path) -> None:
    # At ANY depth, not just the root. A tracked `docs/.env` is exported to
    # disk with a real password in it, and gets zipped along with the folder.
    found = [
        p.relative_to(out).as_posix()
        for p in out.rglob("*")
        if p.name in FORBIDDEN and ".git" not in p.parts
    ]
    for name in MUST_BE_EMPTY:
        directory = out / name
        if not directory.is_dir():
            continue
        strays = [
            p.relative_to(out).as_posix()
            for p in directory.rglob("*")
            if p.is_file() and p.name not in ALLOWED_IN_EMPTY
        ]
        found.extend(strays)
    if found:
        # Do not leave the offending export sitting on disk after refusing it.
        shutil.rmtree(out, ignore_errors=True)
        raise SystemExit(
            "The export contained files that must never leave this machine: "
            + ", ".join(found)
            + "\nThe export has been deleted. Check .gitignore, then try again."
        )


def _init_repo(out: Path, name: str, email: str, remote: str = "") -> None:
    (out / ".snapshot-build").unlink(missing_ok=True)
    _git("init", "--quiet", "--initial-branch=main", cwd=out)
    (out / BUILD_MARKER).write_text("built by scripts/public_snapshot.py\n")
    if remote:
        # Every rebuild deletes this directory, .git included, so the remote
        # you pushed to last time would be gone and the next update would look
        # like it needed a whole new repository. Keep it in the PARENT repo's
        # config, where no rebuild can reach it, and wire it back up here.
        _git("remote", "add", "origin", remote, cwd=out)
    _git("config", "user.name", name, cwd=out)
    _git("config", "user.email", email, cwd=out)
    _git("add", "--all", cwd=out)
    _git("commit", "--quiet", "-m", COMMIT_MESSAGE, cwd=out)


def _verify(out: Path) -> int:
    """Run the private-data guard against the finished snapshot.

    Exit 2 from the guard means it could not check, which used to arrive here
    as a zero and print the word "Clean" above a `gh repo create --public`.
    """
    got = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_private.py"),
         "--path", str(out), "--history"],
        capture_output=True,
        text=True,
    )
    print(got.stdout.rstrip())
    if got.stderr.strip():
        print(got.stderr.rstrip(), file=sys.stderr)
    return got.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="where to build it")
    args = parser.parse_args(argv)

    out = Path(args.out).resolve()
    name, email = _identity(REPO_ROOT)
    _assert_clean_tree(REPO_ROOT)

    print(f"Exporting {_git('rev-parse', '--short', 'HEAD', cwd=REPO_ROOT)} to {out}")
    remote = _git("config", "--get", "snapshot.remote", cwd=REPO_ROOT, check=False)
    _export_head(REPO_ROOT, out)
    _assert_nothing_forbidden(out)
    _init_repo(out, name, email, remote)

    files = len(_git("ls-files", cwd=out).splitlines())
    print(f"One commit, {files} files, authored by {name} <{email}>.\n")

    verdict = _verify(out)
    if verdict == check_private.EXIT_CANNOT_CHECK:
        print(
            "\nThe snapshot was built but NOT verified, so it is not known to be\n"
            "clean. Fix the reason above and run this again before publishing."
        )
        return 1
    if verdict != 0:
        print("\nSnapshot built, but the guard found something. Fix it before pushing.")
        return 1

    if remote:
        print(
            f"\nClean, and wired to {remote}\n"
            "To publish this build over the last one:\n\n"
            f"  git -C {out} push --force origin main\n\n"
            "Force is correct and not dangerous here: a snapshot is always a\n"
            "single commit that replaces the previous single commit."
        )
    else:
        print(
            "\nClean. To publish it, pick a repository NAME THAT DOES NOT EXIST\n"
            "on your account yet, then from inside that directory:\n\n"
            "  gh repo create <new-name> --private --source=. --remote=origin --push\n\n"
            "Then remember it, so every later rebuild pushes to the same place:\n\n"
            f"  git -C {REPO_ROOT} config snapshot.remote <the-url-it-prints>"
        )
    print("\nNothing in your own repo was touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
