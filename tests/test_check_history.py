"""Refusing to update across a rewritten history, with an explanation.

`git pull` has one failure it explains badly. When the published history is
rewritten, an existing clone shares no commit with it and git says "refusing
to merge unrelated histories". To somebody who has never rewritten a history
that reads like their copy is broken, and both obvious reactions are wrong:
deleting the folder destroys .env, config.yaml and the signed-in browser
profile, and forcing a merge produces a tree that is neither version.

This happened on 2026-09-16, when a file that had been publishing a third
party's Cloudflare account name since v33 was removed from every past release.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_history  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)


@pytest.fixture
def pair(tmp_path):
    """A clone and an "upstream" whose histories share nothing.

    Built for real rather than mocked, because the thing under test is whether
    `git merge-base` finds a common ancestor, and a mock of that tests only
    that the mock was called.
    """
    upstream = tmp_path / "upstream.git"
    upstream.mkdir()
    _git(upstream, "init", "-q", "--bare", "-b", "main")

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "T")
    (seed / "a.txt").write_text("published", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "published history")
    _git(seed, "remote", "add", "origin", str(upstream))
    _git(seed, "push", "-q", "origin", "main")

    clone = tmp_path / "clone"
    clone.mkdir()
    _git(clone, "init", "-q", "-b", "main")
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "T")
    (clone / "a.txt").write_text("mine", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "an unrelated root")
    _git(clone, "remote", "add", "origin", str(upstream))
    return clone


@pytest.fixture
def shared(tmp_path):
    """The normal case: a clone that really did come from the upstream."""
    upstream = tmp_path / "up.git"
    upstream.mkdir()
    _git(upstream, "init", "-q", "--bare", "-b", "main")
    seed = tmp_path / "seed2"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "T")
    (seed / "a.txt").write_text("x", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "one")
    _git(seed, "remote", "add", "origin", str(upstream))
    _git(seed, "push", "-q", "origin", "main")
    clone = tmp_path / "clone2"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True)
    return clone


def test_a_shared_history_says_nothing(shared, monkeypatch, capsys):
    """The overwhelmingly common case. An update that prints a paragraph every
    time is an update people stop reading."""
    monkeypatch.setattr(check_history, "ROOT", shared)
    assert check_history.main() == 0
    assert capsys.readouterr().out == ""


def test_an_unrelated_history_stops_the_update(pair, monkeypatch, capsys):
    monkeypatch.setattr(check_history, "ROOT", pair)
    assert check_history.main() == 1
    out = capsys.readouterr().out
    assert "rewritten" in out
    assert "nothing of yours" in out
    assert "git reset --hard origin/main" in out


def test_it_names_what_is_not_at_risk(pair, monkeypatch, capsys):
    """The whole reason for the message. Someone told only "reset --hard" will
    reasonably assume it wipes their credentials, and delete the folder
    instead, which is the one action that actually does."""
    monkeypatch.setattr(check_history, "ROOT", pair)
    check_history.main()
    out = capsys.readouterr().out
    for safe in (".env", "config.yaml", "state/", "course folder"):
        assert safe in out


def test_local_edits_get_the_stash_route(pair, monkeypatch, capsys):
    """reset --hard would discard them, and this is the one case where it is
    not the harmless command the other branch describes."""
    (pair / "a.txt").write_text("edited by me", encoding="utf-8")
    monkeypatch.setattr(check_history, "ROOT", pair)
    assert check_history.main() == 1
    out = capsys.readouterr().out
    assert "git stash" in out and "git stash pop" in out


def test_being_offline_is_not_reported_as_a_rewrite(pair, monkeypatch, capsys):
    """If the fetch cannot run we do not know anything, and announcing a
    rewritten history to somebody on a train would be worse than silence."""
    _git(pair, "remote", "set-url", "origin", str(pair / "does-not-exist.git"))
    monkeypatch.setattr(check_history, "ROOT", pair)
    assert check_history.main() == 0
    assert capsys.readouterr().out == ""


def test_a_zip_install_is_not_this_check_problem(tmp_path, monkeypatch):
    """No .git at all. The updater repairs that elsewhere; saying anything
    here would be a second, contradictory explanation."""
    monkeypatch.setattr(check_history, "ROOT", tmp_path)
    assert check_history.main() == 0
