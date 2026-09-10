"""Rendering the launchd plist, schedule included.

This used to be two sed substitutions, which was fine while the only variables
were absolute paths. The schedule is a block of XML, and pushing multi-line
markup through sed is how you end up with a plist that fails to parse at 2am
with no error anybody ever sees.
"""

import plistlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.render_plist import render, schedule_xml  # noqa: E402


def test_the_schedule_becomes_one_dict_per_time():
    xml = schedule_xml(((7, 30), (18, 5)))
    assert xml.count("<dict>") == 2
    assert "<integer>7</integer>" in xml and "<integer>5</integer>" in xml


def test_a_rendered_plist_parses_as_a_plist():
    parsed = plistlib.loads(render("com.school.sync").encode("utf-8"))
    assert parsed["Label"] == "com.school.sync"
    assert len(parsed["StartCalendarInterval"]) >= 1


def test_the_schedule_in_the_plist_is_the_one_from_config():
    parsed = plistlib.loads(render("com.school.sync").encode("utf-8"))
    from src.common import load_config

    expected = load_config(REPO / "config.yaml", repo_root=REPO).sync_times
    got = tuple(
        (entry["Hour"], entry["Minute"]) for entry in parsed["StartCalendarInterval"]
    )
    assert got == expected, "launchd would run at different times than next_window expects"


def test_a_template_with_no_schedule_is_left_alone(tmp_path):
    """Most of the jobs run on an interval and have no calendar at all."""
    parsed = plistlib.loads(render("com.school.live").encode("utf-8"))
    assert "StartCalendarInterval" not in parsed
    assert parsed["StartInterval"] == 60


def test_an_unreadable_config_still_produces_a_working_plist(tmp_path, capsys):
    """A broken config must not be the reason somebody cannot uninstall a job
    or reinstall one. The shipped schedule is a perfectly good fallback."""
    (tmp_path / "launchd").mkdir()
    (tmp_path / "launchd" / "com.school.sync.plist.template").write_text(
        (REPO / "launchd" / "com.school.sync.plist.template").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text("this: [is not\n", encoding="utf-8")

    parsed = plistlib.loads(render("com.school.sync", repo_root=tmp_path).encode("utf-8"))
    assert len(parsed["StartCalendarInterval"]) == 3
    assert "warning" in capsys.readouterr().err
