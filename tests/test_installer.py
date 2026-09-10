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
