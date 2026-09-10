"""Changing a setting without destroying the file around it.

config.example.yaml is roughly half comments, and those comments are the only
documentation at the point of use: what "staging" means, why an ntfy topic has
no password, which fields do nothing on Windows. A setup tool that silently
deletes all of them the first time somebody uses it has done more damage than
the setting was worth, so the comments are the thing these tests protect.
"""

import pytest
import yaml

from src.config_edit import ConfigEditError, set_many, set_value

SAMPLE = """\
# The semester this describes.
semester: "Automne 2026"

notify:
  macos: true
  # A topic is PUBLIC to whoever knows it.
  ntfy_topic: ""

notebooklm:
  mode: "auto"          # "auto" | "staging"

transcribe: true        # run whisper.cpp on each recording
"""


def test_a_nested_value_changes_and_nothing_else_does():
    out = set_value(SAMPLE, ["notebooklm", "mode"], "staging")
    assert yaml.safe_load(out)["notebooklm"]["mode"] == "staging"
    assert len(out.splitlines()) == len(SAMPLE.splitlines())


def test_every_comment_survives():
    out = set_many(SAMPLE, {"notify.macos": False, "transcribe": False})
    for comment in ("# The semester this describes.", "# A topic is PUBLIC to whoever knows it."):
        assert comment in out


def test_an_inline_comment_survives_the_line_it_is_on():
    """It is the only explanation of what the values mean."""
    out = set_value(SAMPLE, ["notebooklm", "mode"], "staging")
    assert '# "auto" | "staging"' in out


def test_a_top_level_scalar_changes():
    out = set_value(SAMPLE, ["transcribe"], False)
    assert yaml.safe_load(out)["transcribe"] is False


def test_booleans_are_written_as_yaml_booleans_not_strings():
    out = set_value(SAMPLE, ["transcribe"], False)
    assert yaml.safe_load(out)["transcribe"] is False
    assert "transcribe: false" in out


def test_a_missing_nested_key_is_added_under_its_parent():
    out = set_value(SAMPLE, ["notebooklm", "wait_s"], 30)
    assert yaml.safe_load(out)["notebooklm"]["wait_s"] == 30
    assert yaml.safe_load(out)["notebooklm"]["mode"] == "auto"


def test_a_missing_parent_is_created():
    out = set_value(SAMPLE, ["update", "auto"], True)
    assert yaml.safe_load(out)["update"]["auto"] is True
    assert yaml.safe_load(out)["semester"] == "Automne 2026"


def test_a_comment_between_settings_does_not_end_its_block():
    """The comment above ntfy_topic sits at an indent inside notify:. Treating
    it as the end of the block would append ntfy_topic at the top level, where
    nothing reads it and nothing complains."""
    out = set_value(SAMPLE, ["notify", "ntfy_topic"], "quiet-heron-42")
    loaded = yaml.safe_load(out)
    assert loaded["notify"]["ntfy_topic"] == "quiet-heron-42"
    assert "ntfy_topic" not in {k for k in loaded if k != "notify"}


def test_a_value_that_looks_like_a_boolean_stays_a_string():
    """`portal: no` is False in YAML. A college whose hostname is a word like
    that would otherwise become a boolean somewhere far from here."""
    out = set_value(SAMPLE, ["semester"], "no")
    assert yaml.safe_load(out)["semester"] == "no"


def test_a_number_shaped_string_stays_a_string():
    out = set_value(SAMPLE, ["semester"], "1010")
    assert yaml.safe_load(out)["semester"] == "1010"


def test_a_quote_in_a_value_does_not_break_the_file():
    out = set_value(SAMPLE, ["semester"], 'Hiver "2027"')
    assert yaml.safe_load(out)["semester"] == 'Hiver "2027"'


def test_it_refuses_to_guess_at_a_depth_it_cannot_handle():
    """Writing to the wrong place silently is worse than refusing."""
    with pytest.raises(ConfigEditError):
        set_value(SAMPLE, ["a", "b", "c"], 1)


def test_the_real_example_config_survives_a_full_pass():
    from pathlib import Path

    original = Path("config.example.yaml").read_text(encoding="utf-8")
    out = set_many(original, {
        "notebooklm.mode": "staging",
        "notify.macos": False,
        "transcribe": False,
        "update.auto": True,
    })
    before = [l for l in original.splitlines() if l.strip().startswith("#")]
    after = [l for l in out.splitlines() if l.strip().startswith("#")]
    assert before == after, "the example config's comments are its documentation"
    assert yaml.safe_load(out)["notebooklm"]["mode"] == "staging"
