"""The NotebookLM link that lives in the course folder.

Two things can go wrong and neither is visible by looking at the folder: the
file can point at the wrong notebook, and it can be rewritten on every run. The
second one matters because these folders are inside a live Google Drive mirror,
where an identical rewrite still costs an upload and a version in Drive's
history, six times a sync, forever.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from src.shortcuts import (
    NOTEBOOK_URL, SHORTCUT_NAME, build, shortcut_path, webloc, write_shortcut,
)


class FakeNotebook:
    def __init__(self, id: str, title: str) -> None:
        self.id, self.title = id, title


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    from src.common import Course

    from types import SimpleNamespace

    courses = [
        Course(code="350-703-EM", omnivox_name="Psycho", folder="Initiation à la psychologie",
               notebook="Initiation à la psychologie - Cegep Fall 2026", short="Psycho"),
        Course(code="330-704-EM", omnivox_name="Histoire", folder="Histoire du monde",
               notebook="Histoire du monde - Cegep Fall 2026", short="Histoire"),
    ]
    for course in courses:
        (tmp_path / course.folder).mkdir(parents=True)
    return SimpleNamespace(
        courses=courses,
        base_path=tmp_path,
        recordings_folder="2_Voice",
        books_folder="3_Livres",
        folder_for=lambda c: tmp_path / c.folder,
    )


def test_webloc_is_a_valid_plist_holding_the_url():
    """Finder writes these in binary. We write XML so they diff, but macOS has
    to accept them either way, so parse it the way macOS would."""
    data = plistlib.loads(webloc("https://example.com/a").encode("utf-8"))
    assert data == {"URL": "https://example.com/a"}


def test_url_is_escaped():
    """A notebook id goes into the body raw. It is a UUID today, and an
    ampersand in a URL would silently produce a file macOS refuses to open."""
    data = plistlib.loads(webloc("https://x.test/?a=1&b=2").encode("utf-8"))
    assert data["URL"] == "https://x.test/?a=1&b=2"


def test_name_sorts_above_the_documents():
    """The leading digit is the whole point: Finder compares runs of digits
    numerically, so 1_ beats 703_ instead of landing between 6 and 8."""
    assert SHORTCUT_NAME.startswith("1_")
    assert SHORTCUT_NAME.endswith(".webloc")


def test_write_reports_a_change_only_when_the_content_changed(tmp_path):
    """See the module docstring: an unchanged rewrite costs a Drive upload."""
    path = tmp_path / SHORTCUT_NAME
    assert write_shortcut(path, "https://x.test/one") is True
    assert write_shortcut(path, "https://x.test/one") is False
    assert write_shortcut(path, "https://x.test/two") is True


def test_write_replaces_a_binary_webloc(tmp_path):
    """Finder's own format is binary, and reading it as text raises. That must
    be treated as 'replace me', not as a crash."""
    path = tmp_path / SHORTCUT_NAME
    path.write_bytes(plistlib.dumps({"URL": "https://old.test/"}, fmt=plistlib.FMT_BINARY))
    assert write_shortcut(path, "https://x.test/new") is True
    assert plistlib.loads(path.read_bytes())["URL"] == "https://x.test/new"


def test_build_points_each_folder_at_its_own_notebook(cfg, monkeypatch):
    notebooks = [
        FakeNotebook("aaa", "Initiation à la psychologie - Cegep Fall 2026"),
        FakeNotebook("bbb", "Histoire du monde - Cegep Fall 2026"),
    ]
    monkeypatch.setattr(
        "src.notebooklm_upload.NlmUploader.list_notebooks", lambda self: notebooks
    )
    build(cfg)

    for course, expected in zip(cfg.courses, ("aaa", "bbb")):
        path = shortcut_path(cfg, course)
        assert plistlib.loads(path.read_bytes())["URL"] == NOTEBOOK_URL.format(id=expected)


def test_build_matches_notebooks_with_stray_leading_space(cfg, monkeypatch):
    """Real NotebookLM titles carry leading spaces. Exact matching drops them,
    and a course with no shortcut is the failure this whole module removes."""
    notebooks = [
        FakeNotebook("aaa", "  Initiation à la psychologie - Cegep Fall 2026 "),
        FakeNotebook("bbb", "Histoire du monde - Cegep Fall 2026"),
    ]
    monkeypatch.setattr(
        "src.notebooklm_upload.NlmUploader.list_notebooks", lambda self: notebooks
    )
    build(cfg)
    assert shortcut_path(cfg, cfg.courses[0]).exists()


def test_a_missing_notebook_writes_nothing(cfg, monkeypatch):
    """A .webloc pointing at a notebook that does not exist is worse than no
    .webloc: it looks like it works until you click it."""
    monkeypatch.setattr(
        "src.notebooklm_upload.NlmUploader.list_notebooks",
        lambda self: [FakeNotebook("bbb", "Histoire du monde - Cegep Fall 2026")],
    )
    lines = build(cfg)
    assert not shortcut_path(cfg, cfg.courses[0]).exists()
    assert shortcut_path(cfg, cfg.courses[1]).exists()
    assert any("aucun notebook" in line for line in lines)


def test_dry_run_writes_nothing(cfg, monkeypatch):
    monkeypatch.setattr(
        "src.notebooklm_upload.NlmUploader.list_notebooks",
        lambda self: [FakeNotebook("aaa", "Initiation à la psychologie - Cegep Fall 2026")],
    )
    build(cfg, dry_run=True)
    assert not shortcut_path(cfg, cfg.courses[0]).exists()


def test_every_course_gets_its_numbered_subfolders(cfg):
    """An empty 3_Livres in a course with no digital manual is not clutter: it
    is the answer to "where does this go". Creating them only on first write
    would make the shape of a course folder depend on what had happened in it.
    """
    from src.shortcuts import ensure_course_folders

    cfg.recordings_folder = "2_Voice"
    cfg.books_folder = "3_Livres"
    made = ensure_course_folders(cfg)
    assert len(made) == 4  # two courses x two subfolders
    for course in cfg.courses:
        assert (cfg.folder_for(course) / "2_Voice").is_dir()
        assert (cfg.folder_for(course) / "3_Livres").is_dir()


def test_scaffolding_is_idempotent(cfg):
    from src.shortcuts import ensure_course_folders

    cfg.recordings_folder = "2_Voice"
    cfg.books_folder = "3_Livres"
    ensure_course_folders(cfg)
    assert ensure_course_folders(cfg) == [], "a second run must create nothing"


def test_an_absolute_recordings_root_is_left_alone(cfg, tmp_path):
    """An absolute recordings_folder is deliberately OUTSIDE the course folder,
    which is how lecture audio is kept out of a cloud-synced tree. Creating it
    under the course would put it right back in."""
    from src.shortcuts import ensure_course_folders

    cfg.recordings_folder = str(tmp_path / "elsewhere")
    cfg.books_folder = "3_Livres"
    made = ensure_course_folders(cfg)
    assert all(p.name == "3_Livres" for p in made)
    for course in cfg.courses:
        assert not (cfg.folder_for(course) / "elsewhere").exists()
