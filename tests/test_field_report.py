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


def _break_gh_only(monkeypatch):
    """Make `gh` missing without also making `git` missing.

    An earlier version of these tests replaced subprocess.run wholesale, which
    broke the git calls send() makes before it ever reaches gh, and so tested
    nothing about the fallback.
    """
    real = field_report.subprocess.run

    def run(args, **kwargs):
        if args and args[0] == "gh":
            raise FileNotFoundError("gh")
        return real(args, **kwargs)

    monkeypatch.setattr(field_report.subprocess, "run", run)


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


def test_a_missing_github_cli_reaches_the_fallback_instead_of_crashing(report, monkeypatch):
    """subprocess raises FileNotFoundError when an executable does not exist;
    it does not return a non-zero code. So every `if returncode != 0` recovery
    path was unreachable on precisely the machine that needed it: the one with
    no gh, where the hand-written fallback is the only route the report has.
    """
    report.write_text("# Field report\n\nAll answered.\n", encoding="utf-8")
    monkeypatch.setattr(
        field_report.check_private, "main", lambda argv: field_report.check_private.EXIT_CLEAN
    )

    _break_gh_only(monkeypatch)

    with pytest.raises(SystemExit) as exit_info:
        field_report.send(dry_run=False)
    message = str(exit_info.value)
    assert "Nothing is lost" in message
    assert "issues/new" in message
    assert report.exists()


def test_the_fallback_says_no_special_access_is_needed(report, monkeypatch):
    """The repo is public, so anyone with a GitHub account can file. Somebody
    who thinks they need to be added as a collaborator simply will not."""
    report.write_text("# Field report\n\nAll answered.\n", encoding="utf-8")
    monkeypatch.setattr(
        field_report.check_private, "main", lambda argv: field_report.check_private.EXIT_CLEAN
    )
    _break_gh_only(monkeypatch)
    with pytest.raises(SystemExit) as exit_info:
        field_report.send(dry_run=False)
    assert "do NOT need any special access" in str(exit_info.value)


def test_a_todo_inside_the_patch_does_not_block_the_send(report, monkeypatch):
    """The agent writing the workaround is the agent writing the report, and
    `# TODO:` is its house style. A marker inside the diff refused the send
    forever, with no way out short of editing the fix itself."""
    report.write_text(
        "# Field report\n\n## What went wrong\n\nIt broke.\n\n"
        "## Patch\n\n```diff\n+# TODO: check this on an English portal\n```\n",
        encoding="utf-8",
    )
    seen = {}
    monkeypatch.setattr(
        field_report.check_private,
        "main",
        lambda argv: seen.setdefault("argv", argv) and field_report.check_private.EXIT_CLEAN,
    )
    _break_gh_only(monkeypatch)
    with pytest.raises(SystemExit) as exit_info:
        field_report.send(dry_run=False)
    # It got past the marker check to the send, which is the point.
    assert "unanswered" not in str(exit_info.value).lower()


def test_the_privacy_check_is_asked_to_certify(report, monkeypatch):
    """Without --certify the guard passes anything at all on a fresh install,
    where .private-patterns is still the comments-only example and .env is
    empty by design. That is exactly the machine a first report comes from."""
    report.write_text("# Field report\n\nAll answered.\n", encoding="utf-8")
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return field_report.check_private.EXIT_CANNOT_CHECK

    monkeypatch.setattr(field_report.check_private, "main", fake_main)
    with pytest.raises(SystemExit) as exit_info:
        field_report.send(dry_run=True)
    assert "--certify" in seen["argv"]
    assert "student number" in str(exit_info.value)


# ---------------------------------------------------------------------------
# The relay
#
# Filing an issue needs a GitHub identity, and a token cannot ship in a public
# repo, so the fallback was a prefilled link somebody had to click. Nobody
# clicks it. The whole feedback loop was resting on that click.
# ---------------------------------------------------------------------------


def test_the_relay_is_tried_before_anything_that_needs_an_account(report, monkeypatch):
    order = []
    report.write_text("# Field report\n\nAll answered.\n", encoding="utf-8")
    monkeypatch.setattr(
        field_report.check_private, "main", lambda argv: field_report.check_private.EXIT_CLEAN
    )
    monkeypatch.setattr(field_report, "_send_via_relay",
                        lambda t, b: order.append("relay") or "https://x/issues/1")
    monkeypatch.setattr(field_report, "_gh",
                        lambda *a: order.append("gh") or pytest.fail("gh must not run"))

    field_report.send(dry_run=False)
    assert order == ["relay"]


def test_a_relay_that_is_down_falls_through_rather_than_failing(report, monkeypatch):
    """Three routes exist so that one being unavailable costs nothing. A relay
    that is down must not look like the report was rejected."""
    report.write_text("# Field report\n\nAll answered.\n", encoding="utf-8")
    monkeypatch.setattr(
        field_report.check_private, "main", lambda argv: field_report.check_private.EXIT_CLEAN
    )
    monkeypatch.setattr(field_report, "_relay_url", lambda: "https://relay.invalid")

    def refuse(request, timeout=0):
        raise OSError("connection refused")

    monkeypatch.setattr(field_report.urllib.request, "urlopen", refuse)
    _break_gh_only(monkeypatch)

    with pytest.raises(SystemExit) as exit_info:
        field_report.send(dry_run=False)
    assert "Nothing is lost" in str(exit_info.value)


def test_no_relay_configured_is_not_an_error(monkeypatch):
    monkeypatch.setattr(field_report, "_relay_url", lambda: "")
    assert field_report._send_via_relay("[field report] x", "body") == ""


def test_a_relay_refusal_is_reported_and_falls_through(monkeypatch, capsys):
    import io
    import json as _json

    monkeypatch.setattr(field_report, "_relay_url", lambda: "https://relay.example")

    class Answer(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(
        field_report.urllib.request, "urlopen",
        lambda request, timeout=0: Answer(
            _json.dumps({"ok": False, "reason": "rate limited, try later"}).encode()
        ),
    )
    assert field_report._send_via_relay("[field report] x", "body") == ""
    assert "rate limited" in capsys.readouterr().out


def test_the_prefill_stays_under_what_github_will_accept():
    """6000 made GitHub's own web application answer HTTP 500, which reads to
    the reporter as their report being rejected."""
    assert field_report.MAX_PREFILL <= 2000
