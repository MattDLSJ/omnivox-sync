"""Pull the latest release before syncing, so nobody has to remember to.

The reason this exists: somebody sets this up in September, it works, and they
stop thinking about it. That is the goal. It also means they never run `make
update` again, so a fix shipped in October reaches them never, and the bug
they hit in November was fixed a month before they hit it.

The reason it is careful: the people most likely to be behind are the people
whose agent patched a file to get them running in the first place, and a blind
pull either refuses to run for them or throws their fix away. Neither is
acceptable, so this refuses to touch a tree that has local changes and says so
out loud instead. Being told "you have your own changes, send them upstream"
is the useful outcome there, not a silent update and not a silent nothing.

Everything here is guarded and nothing here is fatal. A failed update must
never cost somebody their documents: the worst case is that this run uses the
code it already had, which is the code that worked yesterday.

The new code takes effect on the NEXT run, not this one. The modules for this
run are already imported, and swapping files under a running process is how
you get half of one version and half of another.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

FETCH_TIMEOUT_S = 45
INSTALL_TIMEOUT_S = 300


@dataclass(frozen=True)
class UpdateResult:
    """What happened, in a form both a log line and a digest can use."""

    changed: bool = False
    #: Needs a person. The only value of an auto-update that cannot proceed is
    #: that it says so.
    blocked: bool = False
    message: str = ""

    def __bool__(self) -> bool:
        return self.changed


def _run(args: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )


def check_and_apply(repo_root: Path, *, runner=_run) -> UpdateResult:
    """Fast-forward to the latest published release, when that is safe.

    `runner` is the seam: every test drives this without a network or a repo.
    """
    repo_root = Path(repo_root)

    if not (repo_root / ".git").exists():
        # A ZIP download. Nothing to do here, and `make doctor` is where that
        # gets explained, because it is a setup problem and not a sync one.
        return UpdateResult(message="not a git checkout, so it cannot self-update")

    try:
        remote = runner(["git", "remote", "get-url", "origin"], repo_root)
        if remote.returncode != 0 or not remote.stdout.strip():
            return UpdateResult(message="no origin remote")

        # Untracked files are ignored on purpose: a new file of theirs does
        # not conflict with a fast-forward, and their config.yaml and .env are
        # gitignored and would never show here anyway.
        dirty = runner(
            ["git", "status", "--porcelain", "--untracked-files=no"], repo_root
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            # Porcelain is "XY PATH", and X is a SPACE for a change that is not
            # staged. Stripping the whole blob before splitting therefore eats
            # the first line's leading space and reports src/foo.py as
            # rc/foo.py, in the one message whose entire job is naming files
            # accurately enough for somebody to recognise their own work.
            changed = [
                line[3:] for line in dirty.stdout.splitlines() if line.strip()
            ][:4]
            return UpdateResult(
                blocked=True,
                message=(
                    "Not updating: this copy has its own changes to "
                    + ", ".join(changed)
                    + ". They would be lost. If they are fixes, send them "
                    "upstream with `make report`; if they are experiments, "
                    "stash them. Nothing has been touched."
                ),
            )

        fetched = runner(["git", "fetch", "--quiet", "origin"], repo_root, FETCH_TIMEOUT_S)
        if fetched.returncode != 0:
            # Offline, most likely, and this runs three times a day. Not worth
            # a word to anybody.
            return UpdateResult(message="could not reach the remote")

        behind = runner(["git", "rev-list", "--count", "HEAD..origin/main"], repo_root)
        if behind.returncode != 0 or behind.stdout.strip() in ("", "0"):
            return UpdateResult(message="already up to date")

        before = _requirements_hash(repo_root)
        merged = runner(["git", "merge", "--ff-only", "origin/main"], repo_root, 60)
        if merged.returncode != 0:
            return UpdateResult(
                blocked=True,
                message=(
                    "An update is available but could not be applied: "
                    + (merged.stderr.strip().splitlines() or ["unknown reason"])[0]
                    + ". Nothing has been touched."
                ),
            )

        version = _version(repo_root, runner)
        note = f"Updated to {version}."

        if _requirements_hash(repo_root) != before:
            # New code with old packages fails on import, which looks exactly
            # like a bug in the release rather than a half-finished update.
            installed = _install(repo_root, runner)
            note += (
                " Dependencies changed and were installed."
                if installed
                else " Dependencies changed but could NOT be installed; run"
                " `make update` by hand before the next sync."
            )
        return UpdateResult(changed=True, message=note)

    except (subprocess.TimeoutExpired, OSError) as exc:
        return UpdateResult(message=f"update check skipped ({type(exc).__name__})")


def _requirements_hash(repo_root: Path) -> str:
    path = repo_root / "requirements.txt"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _version(repo_root: Path, runner) -> str:
    got = runner(
        ["git", "describe", "--tags", "--abbrev=0", "--match", "v*"], repo_root
    )
    if got.returncode == 0 and got.stdout.strip():
        return got.stdout.strip()
    short = runner(["git", "rev-parse", "--short", "HEAD"], repo_root)
    return short.stdout.strip() or "the latest commit"


def _install(repo_root: Path, runner) -> bool:
    for python in (
        repo_root / ".venv" / "bin" / "python",
        repo_root / ".venv" / "Scripts" / "python.exe",
    ):
        if python.exists():
            got = runner(
                [str(python), "-m", "pip", "install", "-q", "-r", "requirements.txt"],
                repo_root,
                INSTALL_TIMEOUT_S,
            )
            return got.returncode == 0
    return False
