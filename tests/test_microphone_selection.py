"""The microphone is chosen by name, because the index is not a reference.

On 2026-08-26 avfoundation listed the built-in microphone at index 0, and the
recorder captured a lecture through "-i :0". Six hours later, with four classes
about to be recorded, index 0 was "Steam Streaming Speakers": an OUTPUT device
that had registered itself in between and shifted everything down. ffmpeg opened
it without complaint, ran for the full duration, wrote a correctly sized file,
and every sample in it was zero.

That is the same symptom as a microphone macOS has denied and a completely
different cause, which is exactly why it is worth a test file of its own.
"""

from __future__ import annotations

import pytest

from src.recorder import MIC_BLOCKLIST, MIC_PREFERENCE, audio_devices, resolve_microphone

# Real output, captured on the morning this broke. Device names are the one
# thing edited: a paired phone or headset is usually named after its owner,
# and that owner never agreed to appear in anybody's test suite.
REAL_OUTPUT = """[AVFoundation indev @ 0x81b020140] AVFoundation video devices:
[AVFoundation indev @ 0x81b020140] [0] C922 Pro Stream Webcam
[AVFoundation indev @ 0x81b020140] AVFoundation audio devices:
[AVFoundation indev @ 0x81b020140] [0] Steam Streaming Speakers
[AVFoundation indev @ 0x81b020140] [1] USB PnP Audio Device
[AVFoundation indev @ 0x81b020140] [2] Steam Streaming Microphone
[AVFoundation indev @ 0x81b020140] [3] MacBook Pro Microphone
[AVFoundation indev @ 0x81b020140] [4] C922 Pro Stream Webcam
[AVFoundation indev @ 0x81b020140] [5] Écouteurs ♡
"""


@pytest.fixture()
def fake_ffmpeg(monkeypatch):
    """Feed resolve_microphone an arbitrary device list."""
    def install(stderr: str):
        import subprocess as sp

        def fake_run(cmd, **kwargs):
            return sp.CompletedProcess(cmd, 1, "", stderr)

        monkeypatch.setattr("src.recorder.subprocess.run", fake_run)
        monkeypatch.setattr("src.recorder.find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    return install


def test_parses_only_the_audio_section(fake_ffmpeg):
    """The video list restarts numbering from 0 and contains a device that also
    appears as an audio input. Reading past the boundary picks the wrong one."""
    fake_ffmpeg(REAL_OUTPUT)
    assert audio_devices() == [
        (0, "Steam Streaming Speakers"),
        (1, "USB PnP Audio Device"),
        (2, "Steam Streaming Microphone"),
        (3, "MacBook Pro Microphone"),
        (4, "C922 Pro Stream Webcam"),
        (5, "Écouteurs ♡"),
    ]


def test_finds_the_built_in_microphone_wherever_it_moved(fake_ffmpeg):
    """The regression, exactly. Index 0 is a speaker; the answer is 3."""
    fake_ffmpeg(REAL_OUTPUT)
    assert resolve_microphone() == ":3"


def test_an_explicit_preference_wins(fake_ffmpeg):
    fake_ffmpeg(REAL_OUTPUT)
    assert resolve_microphone("USB PnP Audio Device") == ":1"


def test_preference_is_case_insensitive(fake_ffmpeg):
    fake_ffmpeg(REAL_OUTPUT)
    assert resolve_microphone("macbook pro microphone") == ":3"


def test_a_missing_preference_falls_back_to_the_built_in(fake_ffmpeg):
    """The configured mic is not plugged in today. Record anyway."""
    fake_ffmpeg(REAL_OUTPUT)
    assert resolve_microphone("Some Podcast Mic That Is Not Here") == ":3"


def test_virtual_devices_are_never_chosen_by_accident(fake_ffmpeg):
    """With no known microphone present, anything is better than a loopback
    device, which returns silence forever and looks like a working capture."""
    fake_ffmpeg(
        "[x] AVFoundation audio devices:\n"
        "[x] [0] Steam Streaming Speakers\n"
        "[x] [1] BlackHole 2ch\n"
        "[x] [2] C922 Pro Stream Webcam\n"
    )
    assert resolve_microphone() == ":2"


def test_falls_back_to_zero_when_the_list_cannot_be_read(fake_ffmpeg):
    """A wrong device is recoverable from the audio. No capture is not."""
    fake_ffmpeg("")
    assert resolve_microphone() == ":0"


def test_falls_back_to_zero_when_ffmpeg_explodes(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ffmpeg")

    monkeypatch.setattr("src.recorder.subprocess.run", boom)
    monkeypatch.setattr("src.recorder.find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    assert resolve_microphone() == ":0"


def test_the_capture_command_carries_no_hardcoded_index():
    """The whole point. A literal ":0" anywhere in the capture path means the
    device is being chosen by position again."""
    import inspect

    from src import recorder

    src = inspect.getsource(recorder.start_capture)
    # Comments are allowed to name the old bug; code is not allowed to repeat it.
    code = "\n".join(
        line.split("#", 1)[0] for line in src.splitlines()
    )
    assert '":0"' not in code
    assert "resolve_microphone" in code


def test_blocklist_and_preference_are_lowercase_comparable():
    assert all(b == b.lower() for b in MIC_BLOCKLIST)
    assert all(m.strip() for m in MIC_PREFERENCE)
