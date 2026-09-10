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


def test_a_zip_download_is_left_alone(tmp_path):
    """No .git, nothing to fast-forward, and nothing to shout about here:
    `make doctor` is where a ZIP install gets explained."""
    git = FakeGit()
    result = check_and_apply(tmp_path, runner=git)
    assert not result.changed and not result.blocked
    assert git.calls == []


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
