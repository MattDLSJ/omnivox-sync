"""The one-command installer, and the moment it has to hand over.

The failure it exists to prevent: an agent runs the setup inside a sandbox,
Playwright opens a browser somewhere that sandbox can see and the person
cannot, the command reports that it launched, and its user sits watching a
screen where nothing happens. That is indistinguishable from a hang, and it
happened twice in one afternoon on a real machine.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def installer():
    spec = importlib.util.spec_from_file_location("installer", REPO / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_driven_terminal_is_not_mistaken_for_a_person(installer, monkeypatch):
    class NotATerminal:
        def isatty(self):
            return False

    monkeypatch.setattr(sys, "stdin", NotATerminal())
    assert installer._this_is_a_person() is False


def test_a_real_terminal_is_recognised(installer, monkeypatch):
    class ATerminal:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", ATerminal())
    monkeypatch.setattr(sys, "stdout", ATerminal())
    assert installer._this_is_a_person() is True


def test_handing_over_is_not_reported_as_a_failure(installer, capsys):
    """Exit 0 would let an agent say "installed" and walk away. A generic
    failure code would send it debugging something that is not broken. So it
    has a code of its own, and the text says so in words too."""
    code = installer._hand_over("signing in")
    out = capsys.readouterr().out
    assert code == installer.NEEDS_A_PERSON
    assert code not in (0, 1, 2)
    assert "nothing has gone wrong" in out
    assert "install.py" in out


def test_the_handover_names_what_is_left(installer, capsys):
    installer._hand_over("choosing your college and signing in")
    assert "choosing your college and signing in" in capsys.readouterr().out


def test_running_it_without_a_terminal_stops_before_opening_a_browser():
    """End to end, the way an agent would run it: it must reach the handover
    and never launch anything with a window."""
    import os

    got = subprocess.run(
        [sys.executable, str(REPO / "install.py"), "--inside-venv"],
        cwd=str(REPO),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        # The settings page legitimately waits a quarter of an hour for
        # somebody to open it. Waiting that long to prove a code path is a
        # test nobody runs.
        env={**os.environ, "OMNIVOX_SETUP_TIMEOUT": "1"},
    )
    assert got.returncode == 3, got.stdout[-2000:]
    # Whitespace-insensitive: the phrase is wrapped across a line in the real
    # output, and a test that fails on the wrap point tests the formatter.
    assert "needs you at the keyboard" in " ".join(got.stdout.split())


def test_every_step_is_safe_to_run_twice(installer, capsys):
    """An interrupted install is fixed by running it again, not by starting
    over. That is a different promise from "it worked on my machine"."""
    installer.check_python()
    installer.check_checkout()
    installer.make_venv()
    installer.make_config_files()
    out = capsys.readouterr().out
    assert "already there" in out
    assert "left alone" in out


def test_the_settings_window_opens_even_when_an_agent_is_driving(installer, monkeypatch):
    """It used to be suppressed for any non-tty run, on the reasoning that
    nobody is at that screen. That conflated two different things. A test or a
    script has nobody at the screen; an AI agent running a command on
    somebody's own computer has a person sitting right in front of it.

    Suppressing it meant the page never appeared. Observed on a real install:
    the agent announced it had opened the setup page, nothing happened, the
    user said "you havent opened anything", and it then spent three rounds
    trying to render a localhost address inside its own in-app browser before
    finally handing over a link."""
    calls = []
    monkeypatch.setattr(installer, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(installer, "step", lambda *_a: None)
    monkeypatch.delenv("SCHOOL_NO_BROWSER", raising=False)

    installer.choose_settings(interactive=False)
    assert "--no-browser" not in calls[0], "an agent-driven install is still on a desktop"

    calls.clear()
    installer.choose_settings(interactive=True)
    assert "--no-browser" not in calls[0]


def test_a_script_can_still_say_it_has_no_display(installer, monkeypatch):
    """Which is what the opt-out should always have been: explicit, from the
    caller that actually knows, rather than guessed from a file descriptor."""
    calls = []
    monkeypatch.setattr(installer, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(installer, "step", lambda *_a: None)
    monkeypatch.setenv("SCHOOL_NO_BROWSER", "1")

    installer.choose_settings(interactive=False)
    assert "--no-browser" in calls[0]


def test_an_agent_driven_install_opens_a_window_instead_of_giving_instructions(
    installer, monkeypatch, tmp_path
):
    """It used to stop here and print "open PowerShell yourself and run this".

    That is three steps and a context switch for somebody who asked an agent
    precisely so they would not have to do that. Worse, the reason it stopped
    was real: an agent's process is not a desktop session, and on Windows
    Playwright's driver reported `spawn UNKNOWN`, which is Node saying the
    browser process could not be created in that environment at all.

    The answer is to ask the OS for a window rather than to ask the person to
    go and make one.
    """
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer, "warn", lambda *_a: None)

    seen = {}

    class _Proc:
        def wait(self, timeout=None):
            return 0

    def _popen(args, **kwargs):
        seen.update(args=args, kwargs=kwargs)
        return _Proc()

    monkeypatch.setattr(installer.subprocess, "Popen", _popen)
    code = installer._visible_window(["x", "--login"], what="the sign-in")

    assert code == 0
    assert seen["args"] == ["x", "--login"]
    # A NEW console, not this process's. Inheriting the agent's is the bug,
    # and 0 is exactly what "inherit" means, which is why the constant is
    # spelled out in install.py rather than read off subprocess with a
    # default of 0 on any platform that does not define it.
    assert seen["kwargs"]["creationflags"] == 0x00000010


def test_a_window_that_will_not_open_still_falls_back_to_instructions(
    installer, monkeypatch, tmp_path
):
    """Degrading to the old hand-over is correct. Failing silently is not:
    the person would be left with a finished-looking install and no session."""
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer, "warn", lambda *_a: None)

    def _boom(args, **kwargs):
        raise OSError("spawn UNKNOWN")

    monkeypatch.setattr(installer.subprocess, "Popen", _boom)
    assert installer._visible_window(["x"], what="the sign-in") is None
