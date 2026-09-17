"""Refusing to update across a rewritten history, with an explanation.

`git pull` has one failure it explains badly. On 2026-09-16 the published
history was rewritten to remove a file that should never have shipped. v1 to
v32 came out unchanged and every release after was rebuilt with the same
subject and dates, so an existing copy still shares v32 with the release, and
git reports the two as merely diverged: "Not possible to fast-forward", the
same words it uses for somebody's own local work. To somebody who has never
rewritten a history that reads like their copy is broken, and both obvious
reactions are wrong: deleting the folder destroys .env, config.yaml and the
signed-in browser profile, and forcing a merge produces a tree that is neither
version.

The first version of this check only caught histories with no common commit at
all, and its tests only built that case, so they passed while every real copy
went straight past it. The fixtures below also build the real shape: a clone
that pulled some releases, and an upstream that then rebuilt them.

Every fixture is real git, never mocked. What is under test is what git itself
reports about two histories, whether they share an ancestor and which commits
one holds that the other does not, and a mock of that tests only that the mock
was called.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_history  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)


def _init(path, bare=False):
    path.mkdir()
    if bare:
        _git(path, "init", "-q", "--bare", "-b", "main")
    else:
        _git(path, "init", "-q", "-b", "main")
        _git(path, "config", "user.email", "t@example.com")
        _git(path, "config", "user.name", "T")
    return path


def _commit(repo, subject, day, files, committed_day=None):
    """Commit with fixed dates, so a rebuilt copy can carry exactly the same
    ones. Both dates are set because the check compares both."""
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": f"2026-09-{day:02d}T12:00:00+0000",
        "GIT_COMMITTER_DATE": f"2026-09-{committed_day or day:02d}T12:00:00+0000",
    }
    subprocess.run(
        ["git", "commit", "-qm", subject], cwd=str(repo), env=env,
        capture_output=True, check=True,
    )


def _follow(repo, out):
    """Run every git command the check printed, in order. Advice nobody has
    run is a guess. `make update` is left out: it would reinstall packages."""
    for line in out.splitlines():
        if line.startswith("    git "):
            subprocess.run(line.split(), cwd=str(repo), capture_output=True, check=True)


@pytest.fixture
def pair(tmp_path):
    """A clone and an "upstream" whose histories share nothing."""
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


@pytest.fixture
def published(tmp_path):
    """The author's repository, and a clone that has pulled v1 to v3 from it.

    v2 carries the file that should never have shipped, and v3 is tagged, the
    way every release is.
    """
    upstream = _init(tmp_path / "published.git", bare=True)
    author = _init(tmp_path / "author")
    _git(author, "remote", "add", "origin", str(upstream))
    _commit(author, "v1: first release", 1, {"a.txt": "one"})
    _commit(author, "v2: second release", 2, {"a.txt": "two", "leak.txt": "never ship"})
    _commit(author, "v3: third release", 3, {"a.txt": "three"})
    _git(author, "tag", "v3")
    _git(author, "push", "-q", "--tags", "origin", "main")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(upstream), str(clone)], check=True, capture_output=True
    )
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "T")
    return author, clone


@pytest.fixture
def rewritten(published):
    """What filter-repo did, in miniature: v1 kept, v2 and v3 rebuilt without
    the file under the same subjects and dates, the tag moved, and the result
    force-pushed. The clone has not fetched yet; the check does that."""
    author, clone = published
    _git(author, "reset", "-q", "--hard", "HEAD~2")
    _commit(author, "v2: second release", 2, {"a.txt": "two"})
    _commit(author, "v3: third release", 3, {"a.txt": "three"})
    _git(author, "tag", "-f", "v3")
    _git(author, "push", "-q", "--force", "--tags", "origin", "main")
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


def test_rebuilt_releases_stop_the_update(rewritten, monkeypatch, capsys):
    """The case that actually happened, and the one the first version of this
    check walked straight past: the two sides still share v1."""
    # Untracked, so not an edit: a reset leaves it alone, and calling it one
    # would send this copy down the stash route for nothing.
    (rewritten / "notes.txt").write_text("mine, untracked", encoding="utf-8")
    monkeypatch.setattr(check_history, "ROOT", rewritten)
    assert check_history.main() == 1
    assert _git(rewritten, "merge-base", "HEAD", "origin/main").returncode == 0

    out = capsys.readouterr().out
    assert "old versions of 2 releases" in out
    assert "    git reset --hard origin/main\n" in out
    assert "    git fetch --force --tags origin\n" in out
    assert "git branch" not in out and "git stash" not in out
    # One command per line. Windows PowerShell 5.1 has no &&.
    assert "&&" not in out


def test_following_the_route_lands_on_the_release(rewritten, monkeypatch, capsys):
    """And moves the tag, which a plain fetch never does, so without the second
    line v3 would keep naming the commit that held the file."""
    monkeypatch.setattr(check_history, "ROOT", rewritten)
    check_history.main()
    release = _git(rewritten, "rev-parse", "origin/main").stdout
    assert _git(rewritten, "rev-parse", "v3^{commit}").stdout != release

    _follow(rewritten, capsys.readouterr().out)
    assert _git(rewritten, "rev-parse", "HEAD").stdout == release
    assert _git(rewritten, "rev-parse", "v3^{commit}").stdout == release
    assert check_history.main() == 0
    assert capsys.readouterr().out == ""


def test_a_commit_of_your_own_goes_on_a_branch_first(rewritten, monkeypatch, capsys):
    """A bare reset here would silently drop the one thing in the folder that
    exists nowhere else. The branch must come before the reset, and following
    the advice must actually leave the commit on the branch and back on top."""
    _commit(rewritten, "Fix the portal hostname for my college", 4, {"b.txt": "mine"})
    monkeypatch.setattr(check_history, "ROOT", rewritten)
    assert check_history.main() == 1

    out = capsys.readouterr().out
    assert "1 commit of your own" in out
    assert "keeps it" in out
    assert "    git branch my-changes\n" in out
    assert out.index("git branch my-changes") < out.index("git reset --hard")
    assert "git cherry-pick" in out

    _follow(rewritten, out)
    kept = _git(rewritten, "log", "--format=%s", "my-changes").stdout
    assert "Fix the portal hostname for my college" in kept
    assert _git(rewritten, "log", "-1", "--format=%s").stdout.strip() == (
        "Fix the portal hostname for my college"
    )
    assert _git(rewritten, "rev-parse", "HEAD~1").stdout == (
        _git(rewritten, "rev-parse", "origin/main").stdout
    )


def test_a_branch_name_already_taken_is_not_reused(rewritten, monkeypatch, capsys):
    """The old advice for local work created `my-changes`. If `git branch`
    failed on the existing name, the reset on the next line would still run."""
    _commit(rewritten, "Fix the portal hostname for my college", 4, {"b.txt": "mine"})
    _git(rewritten, "branch", "my-changes")
    monkeypatch.setattr(check_history, "ROOT", rewritten)
    check_history.main()
    assert "    git branch my-changes-2\n" in capsys.readouterr().out


def test_edits_in_a_rebuilt_copy_get_the_stash_route(rewritten, monkeypatch, capsys):
    (rewritten / "a.txt").write_text("edited by me", encoding="utf-8")
    monkeypatch.setattr(check_history, "ROOT", rewritten)
    assert check_history.main() == 1
    out = capsys.readouterr().out
    assert "git branch" not in out
    assert out.index("    git stash\n") < out.index("git reset --hard")
    assert out.index("git reset --hard") < out.index("    git stash pop\n")


def test_a_copy_that_is_only_behind_says_nothing(published, monkeypatch, capsys):
    author, clone = published
    _commit(author, "v4: fourth release", 4, {"a.txt": "four"})
    _git(author, "push", "-q", "origin", "main")
    monkeypatch.setattr(check_history, "ROOT", clone)
    assert check_history.main() == 0
    assert capsys.readouterr().out == ""


def test_your_own_work_on_an_ordinary_update_is_left_to_the_pull(
    published, monkeypatch, capsys
):
    """Nothing was rewritten, so talking about a rewrite would be false, and
    recommending a reset would be destructive. The pull's refusal and the
    Makefile's advice after it are the right answer here."""
    author, clone = published
    _commit(clone, "Fix the portal hostname for my college", 5, {"b.txt": "mine"})
    _commit(author, "v4: fourth release", 4, {"a.txt": "four"})
    _git(author, "push", "-q", "origin", "main")
    monkeypatch.setattr(check_history, "ROOT", clone)
    assert check_history.main() == 0
    assert capsys.readouterr().out == ""


def test_a_release_you_amended_is_yours_not_an_old_copy(published, monkeypatch, capsys):
    """An amend keeps the subject and the author date, which is everything a
    rebuilt release keeps, except the committer date. Matching on the first two
    alone would call a fix amended into v3 an old copy of v3, and recommend the
    bare reset that throws it away."""
    author, clone = published
    (clone / "a.txt").write_text("three, fixed by me", encoding="utf-8")
    env = {**os.environ, "GIT_COMMITTER_DATE": "2026-09-10T12:00:00+0000"}
    subprocess.run(
        ["git", "commit", "-qa", "--amend", "--no-edit"], cwd=str(clone), env=env,
        capture_output=True, check=True,
    )
    _commit(author, "v4: fourth release", 4, {"a.txt": "four"})
    _git(author, "push", "-q", "origin", "main")
    monkeypatch.setattr(check_history, "ROOT", clone)
    assert check_history.main() == 0
    assert capsys.readouterr().out == ""
