"""macOS Finder presentation: folder emoji icons and colour tags.

Purely cosmetic, so every function here is best-effort and never raises: a
failure to decorate a folder must not break a sync run.

Two undocumented-but-stable macOS conventions are used:

* ``com.apple.icon.folder#S`` holds JSON. macOS Tahoe's folder customisation
  writes ``{"emoji":"📖"}`` for an emoji, or ``{"sym":"cross.fill"}`` for an
  SF Symbol. Verified against folders the user had already customised by hand.
* ``com.apple.metadata:_kMDItemUserTags`` is a binary plist array of strings,
  where each entry is ``"<name>\\n<colour index>"``. Dropping the colour index
  produces a tag Finder shows without its colour, which is why the naive
  version of this did not match.
"""

from __future__ import annotations

import logging
import plistlib
import subprocess
from pathlib import Path

ICON_XATTR = "com.apple.icon.folder#S"
TAG_XATTR = "com.apple.metadata:_kMDItemUserTags"
# 32-byte FolderInfo. Bytes 8-9 are frFlags, whose bits 1-3 hold the legacy
# colour label. Finder will not render a customised folder without it: setting
# only the icon xattr leaves a plain folder, which is exactly what happened
# the first time round.
FINDERINFO_XATTR = "com.apple.FinderInfo"
FINDERINFO_LEN = 32
_COLOR_MASK = 0x0E

# Finder's built-in colour indices.
TAG_COLORS = {
    "none": 0, "gray": 1, "grey": 1, "green": 2, "purple": 3,
    "blue": 4, "yellow": 5, "red": 6, "orange": 7,
}

_log = logging.getLogger("school.finder")


def icon_payload(icon: str) -> str | None:
    """Build the JSON payload for a folder icon.

    ``"📖"`` -> emoji; ``"sym:cross.fill"`` -> SF Symbol. Returns None for an
    empty spec.
    """
    icon = (icon or "").strip()
    if not icon:
        return None
    if icon.startswith("sym:"):
        name = icon[4:].strip()
        return '{"sym":"%s"}' % name if name else None
    return '{"emoji":"%s"}' % icon


def set_folder_icon(path: Path, icon: str) -> bool:
    payload = icon_payload(icon)
    if payload is None:
        return False
    try:
        subprocess.run(
            ["xattr", "-w", ICON_XATTR, payload, str(path)],
            check=True, capture_output=True, timeout=20,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - cosmetics must never break a run
        _log.warning("Could not set folder icon on %s: %s", path, exc)
        return False


def tag_value(tag: str, color: str = "green") -> str:
    """Finder stores a tag as "<name>\\n<colour index>"."""
    index = TAG_COLORS.get((color or "").lower())
    return f"{tag}\n{index}" if index else tag


def set_finder_tag(path: Path, tag: str, color: str = "green") -> bool:
    if not tag:
        return False
    try:
        payload = plistlib.dumps([tag_value(tag, color)], fmt=plistlib.FMT_BINARY)
        subprocess.run(
            ["xattr", "-wx", TAG_XATTR, payload.hex(), str(path)],
            check=True, capture_output=True, timeout=20,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warning("Could not tag %s: %s", path, exc)
        return False


def _read_finderinfo(path: Path) -> bytearray:
    try:
        out = subprocess.run(
            ["xattr", "-px", FINDERINFO_XATTR, str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        if out.returncode == 0 and out.stdout.strip():
            data = bytearray(bytes.fromhex("".join(out.stdout.split())))
            if len(data) >= FINDERINFO_LEN:
                return data[:FINDERINFO_LEN]
    except Exception:  # noqa: BLE001
        pass
    return bytearray(FINDERINFO_LEN)


def set_color_label(path: Path, color: str = "green") -> bool:
    """Set the legacy Finder colour label, preserving every other flag.

    Read-modify-write: FolderInfo also carries flags we must not clobber.
    """
    index = TAG_COLORS.get((color or "").lower(), 0)
    try:
        info = _read_finderinfo(path)
        flags = (info[8] << 8) | info[9]
        flags = (flags & ~_COLOR_MASK) | ((index << 1) & _COLOR_MASK)
        info[8], info[9] = (flags >> 8) & 0xFF, flags & 0xFF
        subprocess.run(
            ["xattr", "-wx", FINDERINFO_XATTR, info.hex(), str(path)],
            check=True, capture_output=True, timeout=20,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warning("Could not set colour label on %s: %s", path, exc)
        return False


def read_color_label(path: Path) -> int:
    info = _read_finderinfo(path)
    return (((info[8] << 8) | info[9]) & _COLOR_MASK) >> 1


def read_tags(path: Path) -> list[str]:
    """Tag names currently on a path, colour suffix stripped."""
    try:
        out = subprocess.run(
            ["xattr", "-px", TAG_XATTR, str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return []
        raw = bytes.fromhex("".join(out.stdout.split()))
        return [str(t).split("\n")[0] for t in plistlib.loads(raw)]
    except Exception:  # noqa: BLE001
        return []


def decorate(path: Path, *, icon: str = "", tag: str = "", color: str = "green") -> None:
    """Apply icon and tag to a folder. Best effort, never raises."""
    path = Path(path)
    if not path.exists():
        return
    if icon:
        set_folder_icon(path, icon)
    if tag:
        set_finder_tag(path, tag, color)
    if icon or tag:
        # Required for Finder to actually draw the customised folder.
        set_color_label(path, color)
