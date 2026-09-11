"""The double-clickable buttons beside the course folders.

Two problems, one answer. Windows creates a GUI launched from a background or
subshell process with SW_HIDE, so the Playwright sign-in window exists and is
invisible: the command reports success, nothing appears, and the person waits
for a browser that is already open where they cannot see it. A .cmd
double-clicked from Explorer gets a real console and a visible window.

And macOS had a Sync School.app next to the course folders from the start while
Windows had nothing, not even a mention that such a thing could exist.
"""

import src.launchers as launchers
from src.launchers import write_launchers


def test_windows_gets_a_sign_in_launcher(loaded_config, monkeypatch):
    """The one that exists because of SW_HIDE."""
    monkeypatch.setattr(launchers, "IS_WINDOWS", True)
    names = {p.name for p in write_launchers(loaded_config, logger=None)}
    assert "Sign in to Omnivox.cmd" in names
    assert "Sync School.cmd" in names


def test_the_sign_in_launcher_says_why_it_must_be_double_clicked(
    loaded_config, monkeypatch
):
    """Somebody told to run it another way hits the invisible window again."""
    monkeypatch.setattr(launchers, "IS_WINDOWS", True)
    written = {p.name: p for p in write_launchers(loaded_config, logger=None)}
    body = written["Sign in to Omnivox.cmd"].read_text(encoding="utf-8")
    assert "DOUBLE-CLICKING" in body
    assert "hides windows opened by" in body


def test_the_sign_in_launcher_names_the_trusted_device_box(loaded_config, monkeypatch):
    """Missing that checkbox is what makes it stop working tomorrow, and the
    launcher is the last thing read before the browser opens."""
    monkeypatch.setattr(launchers, "IS_WINDOWS", True)
    written = {p.name: p for p in write_launchers(loaded_config, logger=None)}
    assert "confiance" in written["Sign in to Omnivox.cmd"].read_text(encoding="utf-8")


def test_windows_launchers_keep_the_window_open(loaded_config, monkeypatch):
    """Without pause the console closes the instant it finishes and whatever
    it said is gone."""
    monkeypatch.setattr(launchers, "IS_WINDOWS", True)
    for path in write_launchers(loaded_config, logger=None):
        assert "pause" in path.read_text(encoding="utf-8")


def test_a_unix_launcher_is_executable(loaded_config, monkeypatch):
    monkeypatch.setattr(launchers, "IS_WINDOWS", False)
    import os

    for path in write_launchers(loaded_config, logger=None):
        assert os.access(path, os.X_OK)


def test_launchers_land_beside_the_course_folders(loaded_config, monkeypatch):
    """Inside the project nobody would ever find them."""
    from pathlib import Path

    monkeypatch.setattr(launchers, "IS_WINDOWS", False)
    for path in write_launchers(loaded_config, logger=None):
        assert path.parent == Path(loaded_config.base_path)


def test_they_point_at_the_projects_own_python(loaded_config, monkeypatch):
    """A bare `python` on Windows is whatever the Store installed."""
    monkeypatch.setattr(launchers, "IS_WINDOWS", True)
    for path in write_launchers(loaded_config, logger=None):
        assert ".venv" in path.read_text(encoding="utf-8")


def test_both_platforms_get_a_sign_in_button():
    """A revoked trusted device is the failure this recovers from, and it is
    not rare. Windows recovered with a double click; macOS required knowing to
    type `make login`, because the code assumed `make button` had produced an
    .app and install.py never runs it."""
    from src.launchers import UNIX_LOGIN, UNIX_SYNC, WINDOWS_LOGIN

    assert "--login" in UNIX_LOGIN and "--login" in WINDOWS_LOGIN
    # And the window has to survive long enough to read what it said.
    assert "read -r" in UNIX_SYNC and "read -r" in UNIX_LOGIN
