"""The bundle that makes macOS willing to ask about the microphone.

The failure this prevents is the worst kind the recorder has: everything
reports success and the lecture is gone. A microphone denied by TCC does not
raise. ffmpeg opens the device, writes segments on schedule, exits clean, and
every byte it wrote is zero.

Two independent things have to be true for the fix to hold, and both are easy
to break by accident later:

  1. the bundle must carry an identifier AND a usage description, because TCC
     will not prompt for a subject it cannot name in a dialog, and
  2. python must not be the responsible process, because python.org's
     interpreter is hardened-runtime without com.apple.security.device
     .audio-input, which makes TCC skip the prompt and deny.
"""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from src import micapp
from src.micapp import (
    BUNDLE_ID, MicAppError, app_path, binary_path, capture_binary, capture_plist,
    healthy, info_plist,
)


def test_info_plist_names_a_subject_tcc_can_prompt_for():
    """Without both of these the log says "Policy disallows prompt" and the
    request is denied silently. They are the whole point of the bundle."""
    data = plistlib.loads(info_plist())
    assert data["CFBundleIdentifier"] == BUNDLE_ID
    assert data["NSMicrophoneUsageDescription"].strip()


def test_bundle_has_no_dock_icon():
    """It is a recorder, not something to look at. LSBackgroundOnly keeps it
    out of the Dock and the app switcher when launchd runs it."""
    assert plistlib.loads(info_plist())["LSBackgroundOnly"] is True


def test_executable_name_matches_the_bundle_layout():
    data = plistlib.loads(info_plist())
    assert binary_path(Path("/repo")).name == data["CFBundleExecutable"]
    assert binary_path(Path("/repo")).parent == app_path(Path("/repo")) / "Contents" / "MacOS"


def test_app_path_is_absolute_even_from_a_relative_root():
    """TCC records the path. A relative one would key the grant to whatever
    directory the process happened to start in."""
    assert app_path(Path(".")).is_absolute()


def test_capture_plist_rejects_a_relative_binary():
    """launchd has no working directory. A relative ProgramArguments[0] gives a
    job that launchd reports as running, with a pid, that executed nothing: no
    audio, no error, no stderr, indistinguishable from a working capture until
    the class is over. Caught for real on 2026-08-26.
    """
    with pytest.raises(MicAppError, match="absolute"):
        capture_plist("com.test", ["state/App.app/Contents/MacOS/ffmpeg", "-x"], Path("/tmp/e"))


def test_capture_plist_rejects_an_empty_command():
    with pytest.raises(MicAppError):
        capture_plist("com.test", [], Path("/tmp/e"))


def test_capture_plist_does_not_restart_the_job():
    """KeepAlive would relaunch ffmpeg the moment the class ended and record
    straight into the next lecture, or into the walk home."""
    data = plistlib.loads(capture_plist("com.test", ["/usr/bin/true"], Path("/tmp/e")))
    assert data["KeepAlive"] is False
    assert data["RunAtLoad"] is False


def test_capture_binary_falls_back_when_the_bundle_is_missing(tmp_path):
    """A bundle that has not been built must cost the permission prompt, not
    the class: recording denied-but-present beats not recording at all."""
    assert capture_binary(tmp_path, "/opt/homebrew/bin/ffmpeg") == "/opt/homebrew/bin/ffmpeg"


def test_capture_binary_falls_back_when_the_bundle_does_not_run(tmp_path, monkeypatch):
    """The bundled copy is linked against a VERSIONED Homebrew path
    (/opt/homebrew/Cellar/ffmpeg/8.0.1_4/lib/...), so a brew upgrade moves its
    libraries and the copy stops working. Notice, and use the real one."""
    target = binary_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\nexit 1\n")
    target.chmod(0o755)
    assert capture_binary(tmp_path, "/usr/bin/ffmpeg") == "/usr/bin/ffmpeg"


def test_healthy_rejects_a_missing_binary(tmp_path):
    assert healthy(tmp_path / "nope") is False


def test_healthy_accepts_something_that_runs(tmp_path):
    stub = tmp_path / "ffmpeg"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    assert healthy(stub) is True


@pytest.mark.skipif(not Path("/opt/homebrew/bin/ffmpeg").exists(), reason="no ffmpeg")
def test_build_produces_a_signed_bundle_that_runs(tmp_path):
    """Ad-hoc is enough: TCC wants a stable identity, not a Developer ID. The
    signature covers Info.plist, so the identifier cannot be swapped out from
    under a grant that was given for it."""
    built = micapp.build(tmp_path, "/opt/homebrew/bin/ffmpeg")
    assert healthy(built)

    proc = subprocess.run(
        ["codesign", "-dv", str(app_path(tmp_path))],
        capture_output=True, text=True, timeout=60,
    )
    assert f"Identifier={BUNDLE_ID}" in proc.stderr


@pytest.mark.skipif(not Path("/opt/homebrew/bin/ffmpeg").exists(), reason="no ffmpeg")
def test_build_copies_rather_than_links(tmp_path):
    """TCC resolves symlinks, which would land back on /opt/homebrew and lose
    the bundle identity entirely."""
    built = micapp.build(tmp_path, "/opt/homebrew/bin/ffmpeg")
    assert not built.is_symlink()
    assert built.resolve() == built


def test_capture_plist_rejects_a_relative_output_path():
    """launchd has no working directory, so a relative output is unwritable.

    It fails differently from a relative binary and just as quietly: the job
    starts, ffmpeg runs, and the only trace is "Could not write header
    (incorrect codec parameters ?): No such file or directory", which names
    neither the path nor the directory it was resolved against.
    """
    with pytest.raises(MicAppError, match="output"):
        capture_plist(
            "com.test",
            ["/usr/bin/ffmpeg", "-i", ":0", "state/rec_tmp/x/chunk_%03d.m4a"],
            Path("/tmp/e"),
        )


def test_capture_plist_accepts_absolute_paths_at_both_ends():
    data = plistlib.loads(capture_plist(
        "com.test", ["/usr/bin/ffmpeg", "-i", ":0", "/tmp/out/chunk_%03d.m4a"], Path("/tmp/e"),
    ))
    assert data["ProgramArguments"][-1] == "/tmp/out/chunk_%03d.m4a"
