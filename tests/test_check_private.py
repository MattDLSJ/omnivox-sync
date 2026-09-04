"""The guard that keeps personal data out of the repo.

It exists because the leak this repo actually had was not a credential. It was
a home GPS reading in a test fixture, a partner's name on a paired device, and
real teachers in real fixtures. A secret scanner sees none of that, because
none of it looks like a secret. This one looks for the author's own values.

The awkward part is that a checker for your personal data must not contain
your personal data. These tests pin that property: the needles come from files
that are never committed, and the report redacts what it matched.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_private import (
    MIN_NEEDLE,
    SKIP_PATHS,
    Needle,
    collect_needles,
    scan,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def fake_repo(tmp_path):
    """A repo root carrying the three untracked needle sources."""
    (tmp_path / ".env").write_text(
        "# a comment\nOMNIVOX_USER=1234567\nOMNIVOX_PASS=corrrecthorse\nEMPTY=\n",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        "semester: X\n"
        "base_path: /srv/school\n"
        "school_ssid: HOUSEHOLD-5G\n"
        "courses:\n"
        "  - code: 101-101-XX\n"
        "    folder: Philosophie\n"
        "    teacher: Camille Nadeau\n"
        "    room: B035\n"
        "notify:\n"
        "  ntfy_topic: topic-abcdefgh\n",
        encoding="utf-8",
    )
    (tmp_path / ".private-patterns").write_text(
        "# mine\nJordan Tremblay\n/\\b9999999\\b/\nab\n", encoding="utf-8"
    )
    return tmp_path


def _labels(needles):
    return {n.label for n in needles}


def _values(needles):
    return {n.value for n in needles}


def on_disk(*names):
    """scan() takes (path relative to root, staged content). None means
    read it from disk, which is every case except the pre-commit hook."""
    return [(Path(n), None) for n in names]


# --------------------------------------------------------------------------
# Where the needles come from
# --------------------------------------------------------------------------


def test_every_env_value_becomes_a_needle(fake_repo):
    values = _values(collect_needles(fake_repo))
    assert "1234567" in values
    assert "corrrecthorse" in values


def test_an_empty_env_value_is_not_a_needle(fake_repo):
    """EMPTY= would otherwise match the empty string, which is every line."""
    assert "" not in _values(collect_needles(fake_repo))


def test_the_teacher_is_a_needle_but_the_room_is_not(fake_repo):
    """A teacher is a person. A room is a door in a public building.

    Matching on room codes buried the eight real hits under two hundred false
    ones, and a checker you learn to ignore is worse than none.
    """
    values = _values(collect_needles(fake_repo))
    assert "Camille Nadeau" in values
    assert "B035" not in values
    assert "Philosophie" not in values


def test_the_ssid_and_the_topic_are_needles(fake_repo):
    values = _values(collect_needles(fake_repo))
    assert "HOUSEHOLD-5G" in values
    assert "topic-abcdefgh" in values


def test_the_pattern_file_supports_plain_text_and_regex(fake_repo):
    needles = collect_needles(fake_repo)
    assert "Jordan Tremblay" in _values(needles)
    regexes = [n for n in needles if n.is_regex]
    assert any(n.value == r"\b9999999\b" for n in regexes)


def test_a_pattern_shorter_than_the_floor_is_dropped(fake_repo):
    """'ab' would match half the codebase."""
    assert "ab" not in _values(collect_needles(fake_repo))
    assert MIN_NEEDLE > 2


def test_no_sources_means_nothing_from_files(tmp_path):
    """An empty checkout must not fail the build for having no .env.

    The one needle that survives is the commit identity, because git always
    answers, falling back to the global config outside a repo.
    """
    from_files = [n for n in collect_needles(tmp_path) if not n.label.startswith("git ")]
    assert from_files == []


def test_a_noreply_commit_address_is_not_a_needle(tmp_path, monkeypatch):
    """Once the identity is already anonymous there is nothing to catch, and
    flagging it would fail every build forever."""
    import scripts.check_private as guard

    monkeypatch.setattr(
        guard.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, "1+handle@users.noreply.github.com\n", ""
        ),
    )
    assert guard._from_git_identity(tmp_path) == []


# --------------------------------------------------------------------------
# What it finds, and what it refuses to look at
# --------------------------------------------------------------------------


def test_a_planted_value_is_found_with_its_line_number(fake_repo):
    target = fake_repo / "notes.md"
    target.write_text("fine\nthe password is corrrecthorse\nfine\n", encoding="utf-8")
    hits = scan(fake_repo, on_disk("notes.md"), collect_needles(fake_repo))
    assert len(hits) == 1
    assert "notes.md:2" in hits[0]


def test_matching_ignores_case(fake_repo):
    target = fake_repo / "notes.md"
    target.write_text("CAMILLE NADEAU taught this\n", encoding="utf-8")
    assert scan(fake_repo, on_disk("notes.md"), collect_needles(fake_repo))


def test_a_regex_needle_matches(fake_repo):
    target = fake_repo / "notes.md"
    target.write_text("student 9999999 enrolled\n", encoding="utf-8")
    assert scan(fake_repo, on_disk("notes.md"), collect_needles(fake_repo))


def test_the_needle_sources_are_never_scanned(fake_repo):
    """.env is nothing but needles. Scanning it reports every line forever."""
    sources = on_disk(".env", "config.yaml", ".private-patterns")
    assert scan(fake_repo, sources, collect_needles(fake_repo)) == []
    assert {".env", "config.yaml", ".private-patterns"} <= SKIP_PATHS


def test_a_binary_file_does_not_crash_the_scan(fake_repo):
    blob = fake_repo / "sound.m4a"
    blob.write_bytes(b"\x00\x01\x02corrrecthorse")
    assert scan(fake_repo, on_disk("sound.m4a"), collect_needles(fake_repo)) == []


# --------------------------------------------------------------------------
# The report must be safe to paste
# --------------------------------------------------------------------------


def test_the_report_redacts_what_it_matched(fake_repo):
    target = fake_repo / "notes.md"
    target.write_text("corrrecthorse\n", encoding="utf-8")
    hit = scan(fake_repo, on_disk("notes.md"), collect_needles(fake_repo))[0]
    assert "corrrecthorse" not in hit
    assert "c***********e" in hit


def test_a_needle_is_recognisable_but_not_readable():
    """Redacting a six-character needle to ****** made the report useless:
    it named neither which value matched nor why."""
    assert Needle("x", "abcdef").redacted() == "a****f"


# --------------------------------------------------------------------------
# The repo's own state
# --------------------------------------------------------------------------


def test_this_repo_is_clean():
    """The whole point. If this fails, something personal is tracked.

    Skipped where the guard cannot be armed at all, which is any checkout with
    no .env, no config.yaml, no .private-patterns and an already-anonymous
    commit address. The published snapshot is exactly that, and a checkout
    that knows none of your values cannot meaningfully look for them.
    """
    import scripts.check_private as guard

    got = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_private.py")],
        capture_output=True,
        text=True,
    )
    if got.returncode == guard.EXIT_CANNOT_CHECK:
        pytest.skip(f"nothing to check against here: {got.stderr.strip()}")
    assert got.returncode == guard.EXIT_CLEAN, got.stdout


def test_the_pre_commit_hook_is_executable():
    """A hook without the execute bit is silently skipped by git."""
    hook = REPO_ROOT / ".githooks" / "pre-commit"
    assert hook.exists()
    assert os.stat(hook).st_mode & stat.S_IXUSR


def test_the_hook_runs_the_guard_against_staged_files_only():
    """Scanning the whole tree on every commit would block work on a repo that
    already has a known exception waiting to be fixed."""
    body = (REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    assert "check_private.py" in body
    assert "--staged" in body


# --------------------------------------------------------------------------
# The two accidents a line-by-line substring scan misses
# --------------------------------------------------------------------------


def test_a_value_wrapped_onto_the_next_line_is_caught(fake_repo):
    """Not an anti-obfuscation measure, an anti-accident one.

    Prose in a README or a docstring gets wrapped at some column, and a name
    that lands on the boundary is split in half. Neither half matches
    anything, and the scan reads one line at a time.
    """
    target = fake_repo / "notes.md"
    target.write_text("the course was taught by Camille\nNadeau that autumn\n", encoding="utf-8")
    hits = scan(fake_repo, on_disk("notes.md"), collect_needles(fake_repo))
    assert any("split across lines" in hit for hit in hits)


def test_a_value_broken_by_punctuation_is_a_known_miss(fake_repo):
    """The limit, pinned so nobody assumes otherwise.

    Flattening removes whitespace, not the quotes and escapes a folded string
    literal inserts. This tool is a seatbelt against pasting real data in, not
    a defence against somebody deliberately hiding it.
    """
    target = fake_repo / "fixture.py"
    target.write_text('S = ("Camille Na\\r\\n" " deau")\n', encoding="utf-8")
    assert scan(fake_repo, on_disk("fixture.py"), collect_needles(fake_repo)) == []


def test_a_split_value_is_not_also_reported_per_line(fake_repo):
    """One value, one report. A hit found on a line must not be repeated by
    the flattened pass, or every real hit gets counted twice."""
    target = fake_repo / "notes.md"
    target.write_text("Camille Nadeau taught this\n", encoding="utf-8")
    hits = scan(fake_repo, on_disk("notes.md"), collect_needles(fake_repo))
    assert len(hits) == 1
    assert "split across lines" not in hits[0]


def test_a_name_in_the_filename_is_caught(fake_repo):
    """A file named after somebody leaks as loudly as one containing the name,
    and reading its contents never finds it."""
    target = fake_repo / "notes-by-Jordan Tremblay.md"
    target.write_text("nothing personal in here\n", encoding="utf-8")
    hits = scan(fake_repo, on_disk("notes-by-Jordan Tremblay.md"), collect_needles(fake_repo))
    assert len(hits) == 1
    assert "in the path" in hits[0]


# --------------------------------------------------------------------------
# A check that could not run is not a pass
# --------------------------------------------------------------------------


def test_a_consumer_clone_with_nothing_private_is_clean(tmp_path, monkeypatch):
    """Somebody who downloaded this has no credentials, an unfilled config and
    the example patterns file, which holds only comments. There is genuinely
    nothing to guard, and that is normal rather than a failure.

    This returned exit 2 for a while, which made the commit hook reject every
    commit they tried to make on their own copy of the project.
    """
    import scripts.check_private as guard

    monkeypatch.setattr(guard, "collect_needles", lambda root: [])
    monkeypatch.setattr(guard, "collect_exceptions", lambda root: [])
    assert guard.main(["--path", str(tmp_path)]) == guard.EXIT_CLEAN


def test_a_half_armed_owner_cannot_certify_a_release(tmp_path, fake_repo, monkeypatch):
    """.private-patterns is untracked, so it does not survive a fresh clone or
    a new machine. Losing it drops every needle no config file can know: your
    name, your machine, someone else's name on a paired device, your home.

    With .env and config.yaml still supplying needles, that loss used to look
    exactly like a fully armed run.
    """
    import scripts.check_private as guard

    (fake_repo / ".private-patterns").unlink()
    monkeypatch.setattr(guard, "REPO_ROOT", fake_repo)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "f.txt"], check=True)

    assert guard.main(["--path", str(tmp_path)]) == guard.EXIT_CANNOT_CHECK


def test_a_path_that_does_not_exist_is_an_error(tmp_path):
    import scripts.check_private as guard

    missing = tmp_path / "nope"
    assert guard.main(["--path", str(missing)]) == guard.EXIT_CANNOT_CHECK


def test_a_directory_with_no_tracked_files_is_an_error(tmp_path):
    """A typo in a publish script must not read as a green light."""
    import scripts.check_private as guard

    assert guard.main(["--path", str(tmp_path)]) == guard.EXIT_CANNOT_CHECK


# --------------------------------------------------------------------------
# The index, not the working tree
# --------------------------------------------------------------------------


def test_staged_reads_the_blob_that_is_about_to_be_committed(tmp_path, fake_repo):
    """Stage a file, then fix it. git commits the dirty blob; reading the file
    from disk sees the clean one and waves it through.
    """
    import scripts.check_private as guard

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.st"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)

    target = repo / "notes.md"
    target.write_text("password corrrecthorse\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "notes.md"], check=True)
    target.write_text("all clean now\n", encoding="utf-8")

    staged = guard._files_staged(repo)
    hits = guard.scan(repo, staged, collect_needles(fake_repo))
    assert hits, "the staged blob still carries the value; the file on disk does not"


# --------------------------------------------------------------------------
# Exceptions, which the error message had promised for a while
# --------------------------------------------------------------------------


def test_an_exception_matches_the_offending_text_not_the_report(fake_repo):
    """The exception the error message advertises has to be the value itself.

    It used to be matched against the REPORT line, where the value is
    redacted. So the documented remedy, naming the text to allow, could never
    match and never worked; while naming the file's path matched every report
    line for that file and silently exempted the whole file.
    """
    from scripts.check_private import collect_exceptions

    target = fake_repo / "notes.md"
    target.write_text("Camille Nadeau taught this\n", encoding="utf-8")
    needles = collect_needles(fake_repo)
    assert scan(fake_repo, on_disk("notes.md"), needles)

    (fake_repo / ".private-patterns").write_text(
        "Jordan Tremblay\n!Camille Nadeau\n", encoding="utf-8"
    )
    assert scan(fake_repo, on_disk("notes.md"), needles, collect_exceptions(fake_repo)) == []


def test_an_exception_cannot_name_one_of_the_tools_own_labels(fake_repo):
    """"!private-patterns" matched the label printed in every report line, so
    one line switched off an entire source of needles. That is not an
    exception, it is an off switch."""
    from scripts.check_private import CannotCheck, collect_exceptions

    (fake_repo / ".private-patterns").write_text("!private-patterns\n", encoding="utf-8")
    with pytest.raises(CannotCheck, match="own labels"):
        collect_exceptions(fake_repo)


def test_a_tiny_exception_is_refused(fake_repo):
    """A short exception cancels almost every hit by accident."""
    from scripts.check_private import CannotCheck, collect_exceptions

    (fake_repo / ".private-patterns").write_text("!abc\n", encoding="utf-8")
    with pytest.raises(CannotCheck, match="too short"):
        collect_exceptions(fake_repo)


# --------------------------------------------------------------------------
# Files that ship must be read, wherever they sit
# --------------------------------------------------------------------------


def test_the_env_example_is_scanned_because_it_ships(fake_repo):
    """It is tracked, it reaches every clone and every snapshot, and it is the
    likeliest place for somebody to paste a real password to test something."""
    (fake_repo / ".env.example").write_text("OMNIVOX_PASS=corrrecthorse\n", encoding="utf-8")
    assert scan(fake_repo, on_disk(".env.example"), collect_needles(fake_repo))


def test_a_nested_env_is_scanned_even_though_the_root_one_is_not(fake_repo):
    """Skipping by basename skipped it at every depth. A tracked docs/.env is
    a file that ships, not a needle source."""
    nested = fake_repo / "docs"
    nested.mkdir()
    (nested / ".env").write_text("SECRET=corrrecthorse\n", encoding="utf-8")
    assert scan(fake_repo, on_disk("docs/.env"), collect_needles(fake_repo))


def test_a_name_joined_by_underscores_in_a_path_is_caught(fake_repo):
    """Which is how a name lands in a filename far more often than with a
    literal space."""
    target = fake_repo / "Jordan_Tremblay_notes.md"
    target.write_text("nothing here\n", encoding="utf-8")
    hits = scan(fake_repo, on_disk("Jordan_Tremblay_notes.md"), collect_needles(fake_repo))
    assert any("in the path" in hit for hit in hits)


def test_every_needle_in_a_commit_is_reported_not_just_the_first(tmp_path, fake_repo):
    """Stopping at the first match meant the author address masked every name
    in every message, so --history reported the same needle 79 times and no
    others."""
    import scripts.check_private as guard

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.st"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "f"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m",
         "notes from Camille Nadeau and Jordan Tremblay"],
        check=True,
    )
    hits = guard.scan_commit_messages(repo, collect_needles(fake_repo))
    assert len(hits) >= 2


def test_a_disclosure_with_no_known_value_is_a_known_miss(tmp_path, fake_repo, monkeypatch):
    """The limit of any needle list, pinned so nobody assumes otherwise.

    This repo's history carries "He chose to skip Psycho on 2026-09-02 ...
    720 m from campus". It names no person, no address and no coordinate the
    guard knows, so nothing matches and it goes through. That is not a bug to
    fix, it is the reason commit messages should describe the code.
    """
    import scripts.check_private as guard

    monkeypatch.setattr(guard, "REPO_ROOT", fake_repo)
    message = tmp_path / "msg"
    message.write_text("skipped Psycho today, 720 m from campus\n", encoding="utf-8")
    assert guard.main(["--message-file", str(message)]) == guard.EXIT_CLEAN


def test_a_known_value_in_a_commit_message_is_caught(tmp_path, fake_repo, monkeypatch):
    import scripts.check_private as guard

    monkeypatch.setattr(guard, "REPO_ROOT", fake_repo)
    message = tmp_path / "msg"
    message.write_text("fix: reported by Jordan Tremblay\n", encoding="utf-8")
    assert guard.main(["--message-file", str(message)]) == guard.EXIT_HIT
