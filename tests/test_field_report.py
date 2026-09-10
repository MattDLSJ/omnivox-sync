"""The road back: what a tester fixed, in a form somebody can merge.

Two guards carry the whole thing, and both are here because the failure is
silent otherwise. A report that is nothing but auto-collected facts says
something broke and nothing about what, and a report sent without checking is
a public issue containing somebody's paths, which contain their name.
"""

import pytest

import scripts.field_report as field_report

#: field_report puts scripts/ on sys.path and imports check_private as a
#: top-level module, so `scripts.check_private` is a SECOND, unrelated module
#: object. Patching that one patches nothing, and every guard test passes by
#: doing no work.
check_private = field_report.check_private


@pytest.fixture
def report(tmp_path, monkeypatch):
    path = tmp_path / "field-report.md"
    monkeypatch.setattr(field_report, "REPORT", path)
    return path


def test_the_template_asks_questions_the_guard_can_find():
    """If the marker in the template ever stops matching the one the send
    looks for, an unanswered report sails straight through."""
    filled = field_report.TEMPLATE.format(facts="", patch="", marker=field_report.MARKER)
    assert filled.count(field_report.MARKER) >= 4


def test_send_refuses_while_questions_are_unanswered(report, monkeypatch):
    report.write_text(
        f"# Field report\n\n## What went wrong\n\n{field_report.MARKER} the error\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        check_private, "main", lambda argv: pytest.fail("checked before refusing")
    )
    with pytest.raises(SystemExit) as exit_info:
        field_report.send(dry_run=True)
    assert "unanswered" in str(exit_info.value).lower() or "answer them" in str(exit_info.value).lower()


def test_send_refuses_when_the_report_names_something_private(report, monkeypatch):
    report.write_text("# Field report\n\nAll answered.\n", encoding="utf-8")
    monkeypatch.setattr(check_private, "main", lambda argv: check_private.EXIT_HIT)
    with pytest.raises(SystemExit):
        field_report.send(dry_run=True)


def test_a_refused_report_is_left_on_disk_to_be_edited(report, monkeypatch):
    """Deleting it would throw away the only write-up of the problem."""
    report.write_text("# Field report\n\nAll answered.\n", encoding="utf-8")
    monkeypatch.setattr(check_private, "main", lambda argv: check_private.EXIT_HIT)
    with pytest.raises(SystemExit):
        field_report.send(dry_run=True)
    assert report.exists()


def test_send_refuses_when_the_guard_could_not_check(report, monkeypatch):
    """Exit 2 is "I could not tell", which is not the same as clean and has
    been treated as one before."""
    report.write_text("# Field report\n\nAll answered.\n", encoding="utf-8")
    monkeypatch.setattr(
        check_private, "main", lambda argv: check_private.EXIT_CANNOT_CHECK
    )
    with pytest.raises(SystemExit):
        field_report.send(dry_run=True)


def test_send_needs_a_report_to_exist(report):
    with pytest.raises(SystemExit) as exit_info:
        field_report.send(dry_run=True)
    assert "make report" in str(exit_info.value)


def test_building_twice_does_not_silently_discard_the_first(report):
    """An agent re-running a command is normal. Losing a written-up bug to it
    is not."""
    report.write_text("# Field report\n\nhours of notes\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        field_report.build(force=False, run_tests=False)
    assert "FORCE=1" in str(exit_info.value)
    assert "hours of notes" in report.read_text(encoding="utf-8")
