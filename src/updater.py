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

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FETCH_TIMEOUT_S = 45
INSTALL_TIMEOUT_S = 300

#: Where this project comes from. The one place in the code that knows, and it
#: exists for exactly one situation: a copy that was downloaded as a ZIP has no
#: remote to ask, because it has no git at all.
UPSTREAM = "https://github.com/MattDLSJ/omnivox-sync.git"


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
        repaired = repair_checkout(repo_root, runner=runner)
        if not repaired.changed:
            return repaired
        # Fall through: it is a real checkout now, so it can update like any
        # other. Usually there is nothing to fetch, because the ZIP came from
        # the same branch, and that is the quiet success this is aiming for.

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


def _remote_branch(repo_root: Path, runner) -> str:
    """The branch the upstream actually publishes, not the one we assume.

    `origin/main` is right today and was not always: resetting onto a ref that
    does not exist fails after .git has already been created, which used to
    leave the folder in a state that could never be repaired again.
    """
    for candidate in ("origin/main", "origin/master"):
        got = runner(["git", "rev-parse", "--verify", "--quiet", candidate], repo_root)
        if got.returncode == 0 and got.stdout.strip():
            return candidate
    head = runner(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], repo_root)
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip().replace("refs/remotes/", "", 1)
    return ""


def _commit_matching_tree(repo_root: Path, branch: str, runner, limit: int = 100) -> str:
    """The newest commit whose tree is what is actually on disk.

    A ZIP is a snapshot of one commit. Pointing HEAD at the branch tip instead
    tells git that every file changed since that commit is a local
    modification of the user's, which is the opposite of the truth: they are
    the release's changes, not theirs. The updater then refuses to
    fast-forward "over their work" forever, and the copy is frozen exactly as
    it was before the repair.

    So find the commit the ZIP was cut from, adopt that, and let the ordinary
    fast-forward carry them to the tip afterwards.
    """
    listed = runner(
        ["git", "rev-list", f"--max-count={limit}", branch], repo_root, 60
    )
    if listed.returncode != 0:
        return ""
    for sha in listed.stdout.split():
        if runner(["git", "diff", "--quiet", sha], repo_root, 60).returncode == 0:
            return sha
    return ""


def repair_checkout(repo_root: Path, *, runner=_run, upstream: str = UPSTREAM) -> UpdateResult:
    """Turn a ZIP download back into a real checkout, without touching a file.

    Downloading the ZIP is what people actually do, no matter what the
    instructions say, and the result is indistinguishable from a clone until
    the day it matters: no history, so no fix can ever arrive and no fix can
    ever leave. Telling them to start over is the wrong answer, because by
    then their credentials and their timetable are in the folder.

    So this adopts the history in place. `git reset --mixed` moves HEAD and the
    index and leaves every file exactly where it is, which means .env,
    config.yaml and anything their agent patched all survive. Verified against
    a real GitHub zipball: afterwards the only thing `git status` reports is
    the patch, which is precisely what should be reported.

    It is all-or-nothing. Every earlier version could fail after `git init` and
    leave a .git with an unborn HEAD behind, which is worse than it found
    things AND a one-way door, because the repair only runs when there is no
    .git at all.
    """
    try:
        inside = runner(["git", "rev-parse", "--show-toplevel"], repo_root)
    except (OSError, subprocess.SubprocessError) as exc:
        return UpdateResult(
            message=f"cannot repair this copy without git installed ({exc})"
        )

    if inside.returncode == 0 and inside.stdout.strip():
        # No .git here, yet git answers: this folder sits INSIDE somebody
        # else's repository. Running `git init` would nest a second one in it,
        # which is not ours to do.
        return UpdateResult(
            blocked=True,
            message=(
                "This folder has no git of its own but sits inside the "
                f"repository at {inside.stdout.strip()}. Move it somewhere of "
                "its own and run this again, or clone it fresh. Nothing has "
                "been touched."
            ),
        )

    created_git = False

    def undo(step: str, why: str) -> UpdateResult:
        """Leave the folder exactly as it was found, so the next run retries."""
        if created_git:
            shutil.rmtree(repo_root / ".git", ignore_errors=True)
        return UpdateResult(
            message=(
                "This copy was downloaded as a ZIP and cannot receive fixes. "
                f"Repairing it failed at `{step}`: {why}. Nothing has been "
                "touched; see INSTALL.md."
            )
        )

    try:
        for step in (
            ["git", "init", "--quiet"],
            ["git", "remote", "add", "origin", upstream],
            ["git", "fetch", "--quiet", "origin"],
            # A zipball carries no permission bits on Windows and unreliable
            # ones elsewhere, so every shell script can come back as a
            # mode-only change. That is not a modification anybody made, and
            # left alone it would show up forever as "you have local changes"
            # and block every update.
            ["git", "config", "core.fileMode", "false"],
        ):
            got = runner(step, repo_root, FETCH_TIMEOUT_S if "fetch" in step else 30)
            if step[1] == "init" and got.returncode == 0:
                created_git = True
            if got.returncode != 0:
                return undo(
                    " ".join(step[1:3]),
                    (got.stderr.strip().splitlines() or ["unknown reason"])[0],
                )

        branch = _remote_branch(repo_root, runner)
        if not branch:
            return undo("git fetch", "the upstream has no main or master branch")

        got = runner(["git", "reset", "--mixed", "--quiet", branch], repo_root, 60)
        if got.returncode != 0:
            return undo(
                "git reset",
                (got.stderr.strip().splitlines() or ["unknown reason"])[0],
            )

        note = ""
        dirty = runner(
            ["git", "status", "--porcelain", "--untracked-files=no"], repo_root
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            # Either the ZIP is older than the tip, or they changed something.
            # Only the first is worth correcting, and finding the commit whose
            # tree is on disk is what tells the two apart.
            sha = _commit_matching_tree(repo_root, branch, runner)
            if sha:
                runner(["git", "reset", "--mixed", "--quiet", sha], repo_root, 60)
                note = (
                    " It was several releases behind, so it now sits on the "
                    "release it was downloaded from and will update from there."
                )
    except (OSError, subprocess.SubprocessError) as exc:
        return undo("repair", f"{type(exc).__name__}: {exc}")

    return UpdateResult(
        changed=True,
        message=(
            "This copy was downloaded as a ZIP, so it had no way to receive a "
            "fix or send one. It is now a real checkout. Every file you had, "
            "including .env and config.yaml, is untouched." + note
        ),
    )


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
