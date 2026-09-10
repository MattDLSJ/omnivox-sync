"""Self-update, and the two ways it must refuse.

The people most likely to be running an old version are the people whose agent
patched a file to get them running at all. A pull that discards that patch is
worse than no pull, and a pull that silently fails for them is barely better,
because the fix they made is then helping exactly one person forever.

Nothing here touches a network or a real repository: `runner` is injected.
"""

import subprocess
from pathlib import Path

import pytest

from src.updater import check_and_apply


class FakeGit:
    """Answers git commands from a script, and records what was asked."""

    def __init__(self, **answers):
        self.answers = {
            "remote": (0, "https://github.com/someone/repo.git"),
            "status": (0, ""),
            "fetch": (0, ""),
            "rev-list": (0, "0"),
            "merge": (0, ""),
            "describe": (0, "v7"),
            "rev-parse": (0, "abc1234"),
            "pip": (0, ""),
        }
        self.answers.update(answers)
        self.calls: list[list[str]] = []

    def __call__(self, args, cwd, timeout=30):
        self.calls.append(args)
        for key, (code, out) in self.answers.items():
            if key in args or any(key in a for a in args):
                return subprocess.CompletedProcess(args, code, out, out)
        return subprocess.CompletedProcess(args, 0, "", "")

    def ran(self, word):
        return any(word in a for call in self.calls for a in call)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text("playwright\n", encoding="utf-8")
    return tmp_path


def test_a_zip_download_is_repaired_not_skipped(tmp_path):
    """This used to return "not a git checkout" and give up, which left three
    real installs permanently unable to receive a fix or send one. A ZIP is
    now adopted in place before anything else is attempted."""
    git = FakeGit(**{"rev-parse": (128, "not a git repository")})
    check_and_apply(tmp_path, runner=git)
    assert git.ran("init"), "a ZIP install must be repaired, not reported and abandoned"


def test_nothing_happens_when_already_current(repo):
    git = FakeGit(**{"rev-list": (0, "0")})
    result = check_and_apply(repo, runner=git)
    assert not result.changed
    assert not git.ran("merge")


def test_a_local_change_stops_the_update_and_says_what_it_is(repo):
    """The whole point of the refusal. Their fix is the reason they are behind."""
    git = FakeGit(**{"status": (0, " M src/omnivox.py\n M src/convert.py\n")})
    result = check_and_apply(repo, runner=git)
    assert result.blocked
    assert not result.changed
    assert "src/omnivox.py" in result.message
    assert "make report" in result.message
    assert not git.ran("merge"), "refused, so it must not have touched the tree"


def test_untracked_files_do_not_stop_an_update(repo):
    """A new file of theirs does not conflict with a fast-forward, and their
    config.yaml is gitignored and would never appear here anyway."""
    git = FakeGit(**{"rev-list": (0, "3")})
    check_and_apply(repo, runner=git)
    assert git.ran("--untracked-files=no")


def test_being_offline_is_not_an_event(repo):
    """This runs three times a day. A laptop in a bag must not produce news."""
    git = FakeGit(**{"fetch": (1, "could not resolve host")})
    result = check_and_apply(repo, runner=git)
    assert not result.changed and not result.blocked
    assert not git.ran("merge")


def test_a_refused_fast_forward_is_reported_not_forced(repo):
    git = FakeGit(**{"rev-list": (0, "2"), "merge": (1, "error: untracked file would be overwritten")})
    result = check_and_apply(repo, runner=git)
    assert result.blocked
    assert not result.changed
    assert not git.ran("--force")
    assert not git.ran("reset")


def test_an_update_reports_the_version_it_landed_on(repo):
    git = FakeGit(**{"rev-list": (0, "4"), "describe": (0, "v9")})
    result = check_and_apply(repo, runner=git)
    assert result.changed
    assert "v9" in result.message


def test_unchanged_requirements_do_not_trigger_an_install(repo):
    """pip install on every sync would add minutes to a job that runs three
    times a day, for nothing."""
    git = FakeGit(**{"rev-list": (0, "1")})
    check_and_apply(repo, runner=git)
    assert not git.ran("pip")


def test_changed_requirements_trigger_an_install(repo):
    """New code with old packages fails on import, which reads as a broken
    release rather than a half-finished update."""
    venv = repo / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("", encoding="utf-8")

    reqs = repo / "requirements.txt"

    def rewrite_on_merge(args, cwd, timeout=30):
        if "merge" in args:
            reqs.write_text("playwright\nportalocker\n", encoding="utf-8")
        return git_inner(args, cwd, timeout)

    git_inner = FakeGit(**{"rev-list": (0, "1")})
    result = check_and_apply(repo, runner=rewrite_on_merge)
    assert result.changed
    assert git_inner.ran("pip")
    assert "installed" in result.message


def test_a_failed_install_is_said_out_loud(repo):
    venv = repo / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("", encoding="utf-8")
    reqs = repo / "requirements.txt"

    def rewrite_on_merge(args, cwd, timeout=30):
        if "merge" in args:
            reqs.write_text("playwright\nsomething-new\n", encoding="utf-8")
        return git_inner(args, cwd, timeout)

    git_inner = FakeGit(**{"rev-list": (0, "1"), "pip": (1, "no network")})
    result = check_and_apply(repo, runner=rewrite_on_merge)
    assert "could NOT be installed" in result.message


def test_a_hang_cannot_take_the_sync_down_with_it(repo):
    def hang(args, cwd, timeout=30):
        raise subprocess.TimeoutExpired(args, timeout)

    result = check_and_apply(repo, runner=hang)
    assert not result.changed and not result.blocked


# ---------------------------------------------------------------------------
# Repairing a ZIP download
#
# Three people set this up from a ZIP before anybody noticed, and a ZIP has no
# git: it can never receive a fix and can never send one. Telling them to
# start over is the wrong answer, because by then their credentials and their
# timetable are in the folder. So it is adopted in place, and the thing these
# tests pin down is that no file is ever touched to do it.
# ---------------------------------------------------------------------------


def test_a_zip_install_is_repaired_rather_than_reported(tmp_path):
    """No .git at all is the whole trigger. `rev-parse` failing means this
    folder is not inside any repository, which is the normal case."""
    from src.updater import UPSTREAM, repair_checkout

    git = FakeGit(**{"rev-parse": (128, "not a git repository")})
    result = repair_checkout(tmp_path, runner=git)

    assert result.changed
    assert git.ran("init")
    assert git.ran(UPSTREAM)
    assert git.ran("--mixed")


def test_the_repair_never_checks_out_over_the_users_files(tmp_path):
    """`reset --mixed` moves HEAD and the index and leaves the working tree
    alone. Anything that writes files would take .env, config.yaml and every
    fix their agent made with it."""
    from src.updater import repair_checkout

    git = FakeGit(**{"rev-parse": (128, "")})
    repair_checkout(tmp_path, runner=git)

    for destructive in ("--hard", "checkout", "clean", "restore", "stash"):
        assert not git.ran(destructive), f"repair ran a destructive git {destructive}"


def test_the_repair_disables_filemode(tmp_path):
    """A zipball carries no permission bits, so every shell script comes back
    as a mode-only change. Left alone that reads as "you have local changes"
    forever and blocks every future update."""
    from src.updater import repair_checkout

    git = FakeGit(**{"rev-parse": (128, "")})
    repair_checkout(tmp_path, runner=git)
    assert git.ran("core.fileMode")


def test_a_folder_inside_someone_elses_repo_is_left_alone(tmp_path):
    """No .git here, but git answers, so this sits inside another repository.
    `git init` would nest a second one inside it, which is not ours to do."""
    from src.updater import repair_checkout

    git = FakeGit(**{"rev-parse": (0, "/Users/someone/Documents")})
    result = repair_checkout(tmp_path, runner=git)

    assert result.blocked
    assert not result.changed
    assert "/Users/someone/Documents" in result.message
    assert not git.ran("init")


def test_a_repair_that_cannot_reach_the_network_changes_nothing(tmp_path):
    from src.updater import repair_checkout

    git = FakeGit(**{"rev-parse": (128, ""), "fetch": (1, "could not resolve host")})
    result = repair_checkout(tmp_path, runner=git)

    assert not result.changed
    assert not git.ran("--mixed"), "must not reset onto a ref it never fetched"


def test_a_repair_that_fails_says_which_step(tmp_path):
    """"It did not work" sends somebody to reinstall. "It failed at git fetch"
    sends them to check their network."""
    from src.updater import repair_checkout

    git = FakeGit(**{"rev-parse": (128, ""), "fetch": (1, "fatal: unable to access")})
    result = repair_checkout(tmp_path, runner=git)
    assert "fetch" in result.message


def test_check_and_apply_repairs_a_zip_then_carries_on(tmp_path):
    """After the repair it is an ordinary checkout, so the ordinary update
    path has to run: the ZIP may be several releases old."""
    (tmp_path / "requirements.txt").write_text("playwright\n", encoding="utf-8")
    git = FakeGit(**{"rev-parse": (128, ""), "rev-list": (0, "2")})

    # .git appears partway through, exactly as `git init` would create it.
    real_call = git.__call__

    def create_git_on_init(args, cwd, timeout=30):
        got = real_call(args, cwd, timeout)
        if "init" in args:
            (tmp_path / ".git").mkdir(exist_ok=True)
        return got

    result = check_and_apply(tmp_path, runner=create_git_on_init)
    assert result.changed
    assert git.ran("init") and git.ran("merge")


def test_a_failed_repair_removes_the_git_it_created(tmp_path):
    """The one-way door. Every earlier version could fail after `git init` and
    leave a .git with an unborn HEAD, and since the repair only runs when
    there is no .git at all, that copy could never be repaired again. It then
    reported "already up to date" forever."""
    from src.updater import repair_checkout

    def git(args, cwd, timeout=30):
        FakeGit.calls_seen.append(args)
        if "init" in args:
            (tmp_path / ".git").mkdir(exist_ok=True)
            return subprocess.CompletedProcess(args, 0, "", "")
        if "rev-parse" in args:
            return subprocess.CompletedProcess(args, 128, "", "")
        if "fetch" in args:
            return subprocess.CompletedProcess(args, 1, "", "could not resolve host")
        return subprocess.CompletedProcess(args, 0, "", "")

    FakeGit.calls_seen = []
    result = repair_checkout(tmp_path, runner=git)

    assert not result.changed
    assert not (tmp_path / ".git").exists(), (
        "a failed repair left a .git behind, which permanently disables the "
        "next attempt"
    )
    assert "Nothing has been touched" in result.message


def test_a_zip_older_than_the_tip_adopts_the_release_it_came_from(tmp_path):
    """A ZIP is a snapshot of ONE commit. Pointing HEAD at the branch tip
    tells git that every file the release changed since then is a local
    modification of the user's, so the updater refuses to fast-forward "over
    their work" forever and the copy stays frozen."""
    from src.updater import repair_checkout

    calls = []

    def git(args, cwd, timeout=30):
        calls.append(args)
        if "rev-parse" in args and "--show-toplevel" in args:
            return subprocess.CompletedProcess(args, 128, "", "")
        if "rev-parse" in args:  # origin/main exists
            return subprocess.CompletedProcess(args, 0, "deadbeef", "")
        if "status" in args:  # tree differs from the tip
            return subprocess.CompletedProcess(args, 0, " M src/omnivox.py\n", "")
        if "rev-list" in args:
            return subprocess.CompletedProcess(args, 0, "tip111\nolder22\nolder33\n", "")
        if "diff" in args:  # only older22 matches what is on disk
            return subprocess.CompletedProcess(args, 0 if "older22" in args else 1, "", "")
        if "init" in args:
            (tmp_path / ".git").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(args, 0, "", "")

    result = repair_checkout(tmp_path, runner=git)

    assert result.changed
    resets = [c for c in calls if "reset" in c]
    assert any("older22" in c for c in resets), (
        "it stayed on the tip, so the release's own changes look like the "
        "user's and every future update is refused"
    )
    assert "behind" in result.message


def test_a_missing_git_binary_is_a_message_not_a_traceback(tmp_path):
    """subprocess raises FileNotFoundError for a missing executable rather
    than returning non-zero, and this runs inside a scheduled sync."""
    from src.updater import repair_checkout

    def no_git(args, cwd, timeout=30):
        raise FileNotFoundError("git")

    result = repair_checkout(tmp_path, runner=no_git)
    assert not result.changed
    assert "git" in result.message.lower()


def test_an_upstream_without_main_does_not_strand_the_copy(tmp_path):
    """Resetting onto a ref that does not exist used to fail after .git was
    already created."""
    from src.updater import repair_checkout

    def git(args, cwd, timeout=30):
        if "rev-parse" in args and "--show-toplevel" in args:
            return subprocess.CompletedProcess(args, 128, "", "")
        if "rev-parse" in args or "symbolic-ref" in args:
            return subprocess.CompletedProcess(args, 1, "", "")
        if "init" in args:
            (tmp_path / ".git").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(args, 0, "", "")

    result = repair_checkout(tmp_path, runner=git)
    assert not result.changed
    assert not (tmp_path / ".git").exists()
