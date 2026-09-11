"""Filing the course material already on the machine.

Almost nobody installs this in week one. They install it in week three, with
four PDFs in Downloads and a syllabus on the desktop, and the sync fetches from
today forward and leaves all of that where it was. That is how you end up with
two copies of a course: the tidy one this made and the real one they have been
using.

The matching is deliberately timid. A file moved into the WRONG course is worse
than a file not moved: it is gone from where its owner put it and wrong where
it landed.
"""

from pathlib import Path

import pytest

from src.organize import (
    candidates,
    distinctive_words,
    organize,
    plan_moves,
    summary,
    unique_destination,
)


class Course:
    def __init__(self, code, folder, omnivox_name=""):
        self.code, self.folder = code, folder
        self.omnivox_name = omnivox_name or folder


COURSES = [
    Course("340-101-MQ", "Philosophie et rationalité"),
    Course("350-703-EM", "Initiation à la psychologie"),
    Course("300-204-EM", "Initiation à la recherche qualitative"),
]


def test_a_word_two_courses_share_identifies_neither():
    """"Initiation" opens two of these course names. Treating it as evidence
    is exactly how a file lands in the wrong folder."""
    words = distinctive_words(COURSES)
    assert "initiation" not in words["350-703-EM"]
    assert "psychologie" in words["350-703-EM"]
    assert "qualitative" in words["300-204-EM"]


def test_a_course_code_in_the_name_wins_outright(tmp_path):
    plan = plan_moves([tmp_path / "340-101-MQ_notes.pdf"], COURSES, home=tmp_path)
    assert plan.moves[0][1].code == "340-101-MQ"


def test_a_distinctive_word_is_enough(tmp_path):
    plan = plan_moves([tmp_path / "resume psychologie ch2.pdf"], COURSES, home=tmp_path)
    assert plan.moves[0][1].code == "350-703-EM"


def test_something_that_matches_two_courses_is_left_alone(tmp_path):
    """Ambiguous evidence is not evidence."""
    plan = plan_moves([tmp_path / "initiation.pdf"], COURSES, home=tmp_path)
    assert plan.moves == []
    assert plan.unmatched


def test_something_that_matches_nothing_is_left_alone(tmp_path):
    plan = plan_moves([tmp_path / "tax return 2025.pdf"], COURSES, home=tmp_path)
    assert plan.moves == []
    assert plan.unmatched


def test_only_documents_are_considered(tmp_path):
    """A .zip might be a course archive and might be a game. The cost of being
    wrong is higher than the value of being right."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "psychologie.pdf").write_text("x")
    (downloads / "psychologie.zip").write_text("x")
    (downloads / "psychologie.dmg").write_text("x")
    found = [p.name for p in candidates(tmp_path, skip=[])]
    assert "psychologie.pdf" in found
    assert "psychologie.zip" not in found
    assert "psychologie.dmg" not in found


def test_the_projects_own_folders_are_never_searched(tmp_path):
    """Otherwise it would find every file it downloaded and move it onto
    itself."""
    downloads = tmp_path / "Downloads"
    (downloads / "School").mkdir(parents=True)
    (downloads / "School" / "psychologie.pdf").write_text("x")
    assert candidates(tmp_path, skip=[downloads / "School"]) == []


def test_nothing_is_ever_overwritten(tmp_path):
    (tmp_path / "a.pdf").write_text("first")
    assert unique_destination(tmp_path, "a.pdf").name == "a (2).pdf"


def _home_outside_the_repo(tmp_path, cfg):
    """A home directory the project does not sit inside.

    The fixture's repo_root is tmp_path itself, so a home under it is skipped
    wholesale by the same guard that stops this searching its own folders.
    Real installs are ~/omnivox-sync next to ~/Downloads, not above it.
    """
    object.__setattr__(cfg, "repo_root", tmp_path / "repo")
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True, exist_ok=True)
    return home


def test_a_dry_run_moves_nothing(tmp_path, loaded_config):
    home = _home_outside_the_repo(tmp_path, loaded_config)
    source = home / "Downloads" / f"{loaded_config.courses[0].code} plan.pdf"
    source.write_text("x")

    organize(loaded_config, home=home, dry_run=True, logger=None)
    assert source.exists(), "a dry run that moves a file is not a dry run"


def test_a_real_run_files_it_into_the_course(tmp_path, loaded_config):
    home = _home_outside_the_repo(tmp_path, loaded_config)
    course = loaded_config.courses[0]
    source = home / "Downloads" / f"{course.code} plan.pdf"
    source.write_text("x")

    plan = organize(loaded_config, home=home, logger=None)

    assert not source.exists()
    assert (loaded_config.folder_for(course) / f"{course.code} plan.pdf").is_file()
    assert len(plan.moves) == 1


def test_the_summary_says_what_was_left_behind(tmp_path):
    """Silence about the files it did not move reads as "there were none"."""
    from src.organize import Plan

    plan = Plan(moves=[], unmatched=[Path("/x/a.pdf"), Path("/x/b.pdf")])
    assert "2 other document" in summary(plan)
    assert "Nothing was deleted" in summary(plan)
