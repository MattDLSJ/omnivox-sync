"""What an update tells you afterwards.

An update that lands silently teaches people to ignore updates, which matters
here more than usual: the person pulling it may be running a hand-patch around
the exact bug the release just fixed, and nothing else will ever tell them to
drop it.
"""

import scripts.whats_new as whats_new

CHANGELOG = """# Changelog

Every released version of this project, newest first.

## v3 (2026-09-09)

Windows LibreOffice path.

## v2 (2026-09-05)

English portal labels.

## v1 (2026-09-01)

Initial release.
"""


def test_only_the_versions_you_have_not_seen(tmp_path, monkeypatch):
    got = whats_new._new_sections(CHANGELOG, "v1")
    assert [s.splitlines()[0] for s in got] == ["## v3 (2026-09-09)", "## v2 (2026-09-05)"]


def test_nothing_new_when_you_are_current():
    assert whats_new._new_sections(CHANGELOG, "v3") == []


def test_a_section_carries_its_text_not_just_its_heading():
    assert "Windows LibreOffice path." in whats_new._new_sections(CHANGELOG, "v2")[0]


def test_a_version_that_is_not_in_the_changelog_shows_everything():
    """A marker from a deleted or renumbered release. Showing too much is the
    safe direction; showing nothing is how a fix goes unnoticed."""
    assert len(whats_new._new_sections(CHANGELOG, "v99")) == 3


def test_first_run_announces_only_the_current_version(tmp_path, monkeypatch):
    """Replaying every release back to v1 at people is noise, and noise is
    what the next release has to compete with."""
    monkeypatch.setattr(whats_new, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(whats_new, "CHANGELOG", tmp_path / "CHANGELOG.md")
    monkeypatch.setattr(whats_new, "MARKER", tmp_path / "state" / ".last-seen-version")
    (tmp_path / ".git").mkdir()
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")

    assert whats_new.main([]) == 0
    assert (tmp_path / "state" / ".last-seen-version").read_text().strip() == "v3"


def test_a_zip_download_is_reported_rather_than_ignored(tmp_path, monkeypatch):
    """No .git means no pull, no report, and no way to notice either."""
    monkeypatch.setattr(whats_new, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(whats_new, "CHANGELOG", tmp_path / "CHANGELOG.md")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    assert whats_new.main([]) == 1


def test_the_authors_own_copy_has_no_changelog_and_says_nothing(tmp_path, monkeypatch):
    """CHANGELOG.md is written at publish time and lives only in the published
    repo, so this runs on a tree that has never had one."""
    monkeypatch.setattr(whats_new, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(whats_new, "CHANGELOG", tmp_path / "CHANGELOG.md")
    (tmp_path / ".git").mkdir()
    assert whats_new.main([]) == 0
