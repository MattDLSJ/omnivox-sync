import sys
import plistlib
import subprocess

import pytest

from src.finder import (
    ICON_XATTR,
    TAG_XATTR,
    decorate,
    icon_payload,
    read_tags,
    set_finder_tag,
    set_folder_icon,
    tag_value,
)


# --- payload shapes (these match bytes observed on the user's own folders) ---

# Skipped off macOS rather than failed. A Windows install running the
# offline suite as START-HERE instructs would otherwise open on dozens
# of red lines about tooling that platform does not have, at step one,
# on the one platform the author has never tested. skipif rather than a
# marker so that `pytest -m "not live"` stays correct everywhere and
# nobody has to remember a second flag.
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Finder tags and folder icons: xattr, and macOS-only colour labels",
)


def test_emoji_payload():
    assert icon_payload("📖") == '{"emoji":"📖"}'


def test_sf_symbol_payload():
    """The user's 'Other' folders use a symbol, not an emoji."""
    assert icon_payload("sym:cross.fill") == '{"sym":"cross.fill"}'


@pytest.mark.parametrize("spec", ["", "   ", "sym:", "sym:   "])
def test_empty_specs_produce_nothing(spec):
    assert icon_payload(spec) is None


def test_tag_value_carries_the_colour_index():
    """Finder stores '<name>\\n<colour>'; without it the tag shows uncoloured."""
    assert tag_value("School", "green") == "School\n2"


def test_tag_colour_is_case_insensitive():
    assert tag_value("School", "GREEN") == "School\n2"


def test_unknown_colour_falls_back_to_a_plain_tag():
    assert tag_value("School", "chartreuse") == "School"


def test_none_colour_is_a_plain_tag():
    assert tag_value("School", "none") == "School"


def test_tag_bytes_match_finders_own_format(tmp_path):
    """Pin the exact binary plist Finder writes."""
    expected = plistlib.dumps(["School\n2"], fmt=plistlib.FMT_BINARY)
    folder = tmp_path / "c"
    folder.mkdir()
    set_finder_tag(folder, "School", "green")
    out = subprocess.run(["xattr", "-px", TAG_XATTR, str(folder)],
                         capture_output=True, text=True)
    assert bytes.fromhex("".join(out.stdout.split())) == expected


# --- real filesystem round trips --------------------------------------------


def test_icon_round_trip(tmp_path):
    folder = tmp_path / "Basketball"
    folder.mkdir()
    assert set_folder_icon(folder, "🏀") is True
    out = subprocess.run(["xattr", "-p", ICON_XATTR, str(folder)],
                         capture_output=True, text=True)
    assert '"emoji":"🏀"' in out.stdout


def test_tag_round_trip(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    set_finder_tag(folder, "School", "green")
    assert read_tags(folder) == ["School"], "colour suffix must be stripped on read"


def test_read_tags_on_untagged_folder(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    assert read_tags(folder) == []


def test_decorate_applies_both(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    decorate(folder, icon="🧠", tag="School", color="green")
    assert read_tags(folder) == ["School"]
    out = subprocess.run(["xattr", "-p", ICON_XATTR, str(folder)],
                         capture_output=True, text=True)
    assert "🧠" in out.stdout


def test_decorate_on_a_missing_path_is_silent(tmp_path):
    decorate(tmp_path / "nope", icon="🧠", tag="School")  # must not raise


def test_decorate_with_nothing_configured_is_a_no_op(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    decorate(folder)
    assert read_tags(folder) == []


def test_cosmetic_failure_never_raises(tmp_path, monkeypatch):
    """A broken xattr must not be able to fail a sync run."""
    folder = tmp_path / "c"
    folder.mkdir()
    monkeypatch.setattr(
        "src.finder.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no xattr here")),
    )
    assert set_folder_icon(folder, "🧠") is False
    assert set_finder_tag(folder, "School") is False
    decorate(folder, icon="🧠", tag="School")  # must not raise


# --- FinderInfo colour label -------------------------------------------------
# Setting only the icon xattr leaves a plain folder: Finder will not draw a
# customised folder unless FinderInfo carries the colour label too.


def test_color_label_is_written(tmp_path):
    from src.finder import read_color_label, set_color_label

    folder = tmp_path / "c"
    folder.mkdir()
    assert set_color_label(folder, "green") is True
    assert read_color_label(folder) == 2


def test_finderinfo_is_exactly_32_bytes(tmp_path):
    from src.finder import FINDERINFO_LEN, FINDERINFO_XATTR, set_color_label

    folder = tmp_path / "c"
    folder.mkdir()
    set_color_label(folder, "green")
    out = subprocess.run(["xattr", "-px", FINDERINFO_XATTR, str(folder)],
                         capture_output=True, text=True)
    assert len(bytes.fromhex("".join(out.stdout.split()))) == FINDERINFO_LEN


def test_green_label_matches_the_bytes_finder_writes(tmp_path):
    """Pinned against a folder the user had customised by hand."""
    from src.finder import FINDERINFO_XATTR, set_color_label

    folder = tmp_path / "c"
    folder.mkdir()
    set_color_label(folder, "green")
    out = subprocess.run(["xattr", "-px", FINDERINFO_XATTR, str(folder)],
                         capture_output=True, text=True)
    assert "".join(out.stdout.split()).lower() == "00" * 9 + "04" + "00" * 22


def test_other_finderinfo_flags_are_preserved(tmp_path):
    """FolderInfo also holds flags we must not clobber."""
    from src.finder import FINDERINFO_XATTR, read_color_label, set_color_label

    folder = tmp_path / "c"
    folder.mkdir()
    existing = bytearray(32)
    existing[9] = 0x00
    existing[8] = 0x40  # kIsShared-ish bit living above the colour mask
    existing[20] = 0xAB  # ExtendedFolderInfo payload
    subprocess.run(["xattr", "-wx", FINDERINFO_XATTR, existing.hex(), str(folder)], check=True)

    set_color_label(folder, "green")

    out = subprocess.run(["xattr", "-px", FINDERINFO_XATTR, str(folder)],
                         capture_output=True, text=True)
    data = bytes.fromhex("".join(out.stdout.split()))
    assert read_color_label(folder) == 2
    assert data[8] == 0x40, "high flag byte must survive"
    assert data[20] == 0xAB, "extended info must survive"


@pytest.mark.parametrize("color,index", [("green", 2), ("red", 6), ("blue", 4), ("none", 0)])
def test_each_colour_maps_to_finders_index(tmp_path, color, index):
    from src.finder import read_color_label, set_color_label

    folder = tmp_path / color
    folder.mkdir()
    set_color_label(folder, color)
    assert read_color_label(folder) == index


def test_decorate_sets_the_label_too(tmp_path):
    """Regression: the first version set icon + tag but no label, and Finder
    rendered a plain green folder with no emoji."""
    from src.finder import read_color_label

    folder = tmp_path / "c"
    folder.mkdir()
    decorate(folder, icon="🏀", tag="School", color="green")
    assert read_color_label(folder) == 2
