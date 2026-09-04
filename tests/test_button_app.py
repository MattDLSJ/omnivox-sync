"""The Dock button.

The app is deliberately tiny: it asks launchd to run com.school.manual once and
exits. Everything it does NOT do is the design, so that is what these assert.
A button that built its own command line would drift the first time the venv
moved, and one that ran python directly would lose launchd's refusal to start a
second instance of a job already running, which is the only thing standing
between an impatient double-click and two browsers on one Omnivox profile.
"""

import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "make_button_app.sh"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Build a real bundle somewhere harmless."""
    app = tmp_path_factory.mktemp("apps") / "Sync School.app"
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(app)], capture_output=True, text=True, cwd=REPO
    )
    if proc.returncode != 0:
        pytest.skip(f"com.school.manual not installed here: {proc.stderr.strip()}")
    return app


def test_script_is_executable():
    assert SCRIPT.exists() and SCRIPT.stat().st_mode & 0o111


def test_builds_a_bundle_macos_will_accept(built):
    info = plistlib.loads((built / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleExecutable"] == "syncschool"
    assert (built / "Contents" / "MacOS" / info["CFBundleExecutable"]).exists()


def test_the_executable_is_executable(built):
    assert (built / "Contents" / "MacOS" / "syncschool").stat().st_mode & 0o111


def test_it_is_a_background_agent_with_no_window(built):
    """LSUIElement: clicking must not raise a window or bounce a second icon."""
    info = plistlib.loads((built / "Contents" / "Info.plist").read_bytes())
    assert info["LSUIElement"] is True


def test_the_click_goes_through_launchd(built):
    """The core design decision. Anything else loses single-instance protection."""
    body = (built / "Contents" / "MacOS" / "syncschool").read_text()
    assert "launchctl kickstart" in body
    assert "com.school.manual" in body


def test_the_button_does_not_re_declare_the_command(built):
    """One copy of the command, in the plist. A second copy here would rot."""
    body = (built / "Contents" / "MacOS" / "syncschool").read_text()
    for leak in ("src.omnivox_sync", ".venv", "python", "--manual"):
        assert leak not in body, f"the button should not know about {leak!r}"


def test_a_missing_job_tells_the_user_what_to_run(built):
    body = (built / "Contents" / "MacOS" / "syncschool").read_text()
    assert "make install-manual" in body


def test_rebuilding_is_idempotent(built, tmp_path):
    """`make button` after an edit must not leave half of an old bundle."""
    target = tmp_path / "Sync School.app"
    shutil.copytree(built, target)
    (target / "Contents" / "MacOS" / "stale").write_text("leftover")
    subprocess.run(["bash", str(SCRIPT), str(target)], check=True, capture_output=True, cwd=REPO)
    assert not (target / "Contents" / "MacOS" / "stale").exists()


def test_the_script_refuses_when_the_job_is_absent(tmp_path, monkeypatch):
    """Building a button for a job that does not exist would produce a file that
    silently does nothing when clicked."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "launchctl").write_text("#!/bin/sh\nexit 1\n")  # every query fails
    (fake_bin / "launchctl").chmod(0o755)
    env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(tmp_path)}
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path / "X.app")],
        capture_output=True, text=True, cwd=REPO, env=env,
    )
    assert proc.returncode != 0
    assert "make install-manual" in proc.stderr
    assert not (tmp_path / "X.app").exists(), "must not leave a dud bundle behind"
