"""The script that produces the copy other people receive.

Two things cannot be fixed by editing a file: a commit message, and the author
line on a commit. Both live in the commit object, both travel with a clone,
and a force-push does not delete them from a host. So the shareable artifact
is not a cleaned-up history, it is a tree with no history at all.

These tests cover the ways that artifact stopped being trustworthy: a rmtree
that deleted the wrong directory, an author line nobody validated, and a
verification step that read "could not check" as "clean".
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import public_snapshot as snap

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def tiny_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.st"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "x"], check=True)
    return repo


# --------------------------------------------------------------------------
# The rmtree
# --------------------------------------------------------------------------


def test_it_refuses_to_delete_a_repository_it_did_not_build(tmp_path, tiny_repo):
    """--out pointed at a checkout used to remove it, .git and all."""
    with pytest.raises(SystemExit) as exc:
        snap._assert_safe_to_overwrite(tmp_path, tiny_repo)
    assert "git repository" in str(exc.value)


def test_it_refuses_a_directory_with_someone_elses_files_in_it(tmp_path):
    out = tmp_path / "not-mine"
    out.mkdir()
    (out / "thesis.docx").write_text("years of work\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        snap._assert_safe_to_overwrite(tmp_path / "repo", out)
    assert "not empty" in str(exc.value)


def test_it_refuses_to_build_into_a_parent_of_the_repo(tmp_path):
    repo = tmp_path / "a" / "b"
    repo.mkdir(parents=True)
    with pytest.raises(SystemExit) as exc:
        snap._assert_safe_to_overwrite(repo, tmp_path / "a")
    assert "contains this repo" in str(exc.value)


def test_it_reuses_a_directory_it_built_itself(tmp_path):
    """Rebuilding must not require deleting by hand every time."""
    out = tmp_path / "build"
    out.mkdir()
    (out / ".snapshot-build").write_text("x\n", encoding="utf-8")
    (out / "stale.py").write_text("old\n", encoding="utf-8")
    snap._assert_safe_to_overwrite(tmp_path / "repo", out)


def test_an_empty_directory_is_fine(tmp_path):
    out = tmp_path / "empty"
    out.mkdir()
    snap._assert_safe_to_overwrite(tmp_path / "repo", out)


# --------------------------------------------------------------------------
# The author line
# --------------------------------------------------------------------------


def test_a_personal_address_is_refused_as_the_snapshot_author(tmp_path, monkeypatch):
    """The script exists to publish without an author. It used to accept the
    real address, stamp it on the commit, and print the word Clean."""
    from scripts.check_private import Needle

    monkeypatch.setattr(
        snap.check_private,
        "collect_needles",
        lambda root: [Needle(".private-patterns", "realname@example.com")],
    )
    monkeypatch.setattr(
        snap,
        "_git",
        lambda *a, **k: "realname@example.com" if "snapshot.email" in a else "handle",
    )
    with pytest.raises(SystemExit) as exc:
        snap._identity(tmp_path)
    assert "private" in str(exc.value).lower()


def test_a_missing_identity_is_refused_rather_than_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(snap, "_git", lambda *a, **k: "")
    with pytest.raises(SystemExit) as exc:
        snap._identity(tmp_path)
    assert "snapshot.name" in str(exc.value)


# --------------------------------------------------------------------------
# What may and may not be in the export
# --------------------------------------------------------------------------


def test_a_forbidden_file_at_any_depth_is_caught(tmp_path):
    """Checking only the root missed a tracked docs/.env, which lands on disk
    with a real password in it and travels with the folder."""
    out = tmp_path / "out"
    (out / "docs").mkdir(parents=True)
    (out / "docs" / ".env").write_text("SECRET=x\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        snap._assert_nothing_forbidden(out)
    assert "docs/.env" in str(exc.value)


def test_a_refused_export_is_deleted_rather_than_left_lying_there(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / ".env").write_text("SECRET=x\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        snap._assert_nothing_forbidden(out)
    assert not out.exists()


def test_state_and_logs_ship_as_empty_shells(tmp_path):
    """The code needs the directories. It must never get their contents: that
    is where the authenticated browser profile and the portal screenshots go.
    """
    out = tmp_path / "out"
    for name in ("state", "logs"):
        (out / name).mkdir(parents=True)
        (out / name / ".gitkeep").write_text("", encoding="utf-8")
    snap._assert_nothing_forbidden(out)

    (out / "state" / "Cookies").write_text("session\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        snap._assert_nothing_forbidden(out)
    assert "state/Cookies" in str(exc.value)


# --------------------------------------------------------------------------
# The verification step
# --------------------------------------------------------------------------


def test_could_not_check_is_not_reported_as_clean(tmp_path, monkeypatch, capsys):
    """Exit 2 from the guard arrived here as a non-zero it never inspected,
    and the caller printed Clean above a `gh repo create --public --push`."""
    import scripts.check_private as guard

    monkeypatch.setattr(
        snap.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], guard.EXIT_CANNOT_CHECK, "", "nothing to look for"
        ),
    )
    assert snap._verify(tmp_path) == guard.EXIT_CANNOT_CHECK


def _cannot_build() -> str | None:
    """Why an end-to-end snapshot cannot run here, or None if it can.

    Both reasons are ordinary rather than broken: mid-edit, HEAD is not the
    code you are looking at, and a fresh clone has no snapshot identity
    because choosing one is deliberately the operator's call.
    """
    dirty = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        return "the working tree has uncommitted changes"
    for key in ("snapshot.name", "snapshot.email"):
        got = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "config", "--get", key],
            capture_output=True,
            text=True,
        )
        if got.returncode != 0 or not got.stdout.strip():
            return f"no {key} configured; see the README"
    return None


CANNOT_BUILD = _cannot_build()


def _tree_is_dirty() -> bool:
    return bool(
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


@pytest.mark.skipif(CANNOT_BUILD is not None, reason=CANNOT_BUILD or "")
def test_the_real_snapshot_of_this_repo_is_clean(tmp_path):
    """End to end, on the actual repository, into a throwaway directory.

    Skipped mid-edit on purpose: a snapshot of HEAD would not be the code you
    are looking at, and the script refuses for exactly that reason.
    """
    out = tmp_path / "snapshot"
    assert snap.main(["--out", str(out)]) == 0

    log = subprocess.run(
        ["git", "-C", str(out), "log", "--format=%an <%ae>|%cn <%ce>"],
        capture_output=True,
        text=True,
    ).stdout.strip().splitlines()
    assert len(log) == 1, "a snapshot carries exactly one commit"
    author, committer = log[0].split("|")
    assert "noreply" in author and "noreply" in committer
    assert not (out / ".snapshot-build").exists(), "the build marker must not ship"


@pytest.mark.skipif(CANNOT_BUILD is not None, reason=CANNOT_BUILD or "")
def test_a_finished_snapshot_can_be_rebuilt_over(tmp_path):
    """The marker that proves a directory is ours used to sit in the tree, so
    it was deleted before the commit and every rebuild refused its own output.
    """
    out = tmp_path / "snapshot"
    assert snap.main(["--out", str(out)]) == 0
    assert not (out / ".snapshot-build").exists(), "the marker must not ship"
    snap._assert_safe_to_overwrite(REPO_ROOT, out)
    assert snap.main(["--out", str(out)]) == 0


def test_the_publish_remote_survives_a_rebuild(tmp_path):
    """Every rebuild deletes the export directory, .git included. The remote
    you pushed to last time lived in there, so the next update looked like it
    needed a brand new repository. It is kept in the parent repo's config."""
    out = tmp_path / "snapshot"
    out.mkdir()
    (out / "README.md").write_text("something to commit\n", encoding="utf-8")
    snap._init_repo(out, "handle", "h@users.noreply.github.com", "https://example.test/x.git")
    assert "https://example.test/x.git" in snap._git("remote", "-v", cwd=out)
