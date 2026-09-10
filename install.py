#!/usr/bin/env python3
"""Set this up, end to end, in one command.

    python3 install.py          macOS
    py install.py               Windows

Everything else in this project assumes a working environment. This is the
thing that makes one, and it exists because the alternative was a brief
telling an AI to run about a dozen commands in order: create the environment,
install the dependencies, fetch a browser, copy three files, find the college,
sign in, discover the courses, choose the settings, rehearse, run. Every one of
those was somewhere to stop, ask a question nobody needed asked, or quietly do
the wrong thing, and a setup that stops eleven times is a setup people abandon
halfway.

It stops in exactly three places, and each one is something only a person can
supply: the name of your college, signing in to Omnivox in a browser, and the
three questions on the settings page.

Safe to run again. Every step checks whether it is already done and skips it,
so an interrupted install is fixed by running this a second time rather than
by starting over. It runs on stock Python and needs nothing installed first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
MIN_PYTHON = (3, 11)

IS_WINDOWS = os.name == "nt"
VENV_PYTHON = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")

#: Set once the script has re-launched itself inside the virtual environment.
IN_VENV_FLAG = "--inside-venv"


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

_step = 0


def step(title: str) -> None:
    global _step
    _step += 1
    print(f"\n[{_step}] {title}", flush=True)


def ok(message: str) -> None:
    print(f"    {message}", flush=True)


def warn(message: str) -> None:
    print(f"    ! {message}", flush=True)


def die(message: str) -> None:
    print(f"\nStopped: {message}\n", file=sys.stderr)
    raise SystemExit(1)


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(ROOT), **kwargs)


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------


def check_python() -> None:
    step("Checking Python")
    if sys.version_info < MIN_PYTHON:
        die(
            f"this needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer, and "
            f"you are running {sys.version.split()[0]}."
        )
    ok(f"Python {sys.version.split()[0]}")


def check_checkout() -> None:
    step("Checking this is a git checkout")
    if (ROOT / ".git").exists():
        ok("yes, so it can receive fixes and send them")
        return
    warn("this folder has no .git, so it was downloaded as a ZIP rather than cloned.")
    warn("It can never receive a fix or report one. Repairing it now; no file is touched.")
    sys.path.insert(0, str(ROOT))
    try:
        from src.updater import repair_checkout

        result = repair_checkout(ROOT)
    except Exception as exc:  # noqa: BLE001
        warn(f"could not repair it ({exc}). See INSTALL.md.")
        return
    ok(result.message)


def make_venv() -> None:
    step("Building the Python environment")
    if VENV_PYTHON.exists():
        ok("already there")
        return
    got = run([sys.executable, "-m", "venv", str(VENV)])
    if got.returncode != 0 or not VENV_PYTHON.exists():
        die("could not create the virtual environment in .venv")
    ok(f"created {VENV.name}")


def install_requirements() -> None:
    step("Installing the Python dependencies")
    got = run([str(VENV_PYTHON), "-m", "pip", "install", "-q", "-r", "requirements.txt"])
    if got.returncode != 0:
        die("pip could not install requirements.txt")
    ok("done")


def install_browser() -> None:
    step("Fetching the browser Playwright drives (about 550 MB, once)")
    check = run(
        [str(VENV_PYTHON), "-c",
         "from playwright.sync_api import sync_playwright\n"
         "with sync_playwright() as p:\n"
         "    print(p.chromium.executable_path)"],
        capture_output=True, text=True,
    )
    path = check.stdout.strip()
    if check.returncode == 0 and path and Path(path).exists():
        ok("already downloaded")
        return
    ok("downloading, this is the slowest part...")
    got = run([str(VENV_PYTHON), "-m", "playwright", "install", "chromium"])
    if got.returncode != 0:
        die("could not download Chromium. Check your internet connection and run this again.")
    ok("done")


def make_config_files() -> None:
    step("Creating your config files")
    for example, real in (
        ("config.example.yaml", "config.yaml"),
        (".env.example", ".env"),
        (".private-patterns.example", ".private-patterns"),
    ):
        target = ROOT / real
        if target.exists():
            ok(f"{real} already there, left alone")
            continue
        shutil.copyfile(ROOT / example, target)
        ok(f"{real} created")


def check_converters() -> None:
    """LibreOffice and ffmpeg. Reported, never installed without being asked.

    Nothing before the first sync needs either, and a conversion that cannot
    run is already handled: it is logged and the original file is kept. So a
    missing one is a note, not a reason to stop or to start a 700 MB download
    somebody did not agree to.
    """
    step("Checking the file converters")
    sys.path.insert(0, str(ROOT))
    missing = []
    if not shutil.which("ffmpeg"):
        missing.append(("ffmpeg", "brew install ffmpeg", "winget install Gyan.FFmpeg"))
    try:
        from src.convert import find_soffice

        find_soffice()
        ok("LibreOffice found")
    except Exception:  # noqa: BLE001
        missing.append((
            "LibreOffice",
            "brew install --cask libreoffice",
            "winget install TheDocumentFoundation.LibreOffice",
        ))
    if not missing:
        ok("ffmpeg found")
        return
    for name, mac, win in missing:
        warn(f"{name} is not installed. Word and PowerPoint files will download")
        warn("but not be converted to PDF, which is logged and not fatal. To fix:")
        warn(f"    {win if IS_WINDOWS else mac}")


def set_portal() -> None:
    """Fallback only. The settings page asks this now, so this runs when the
    page was closed without answering, or on a machine with no browser."""
    step("Checking which cégep this is pointed at")
    sys.path.insert(0, str(ROOT))
    import yaml

    from src.config_edit import set_value

    config = ROOT / "config.yaml"
    text = config.read_text(encoding="utf-8")
    existing = ((yaml.safe_load(text) or {}).get("school") or {}).get("portal")
    if existing:
        ok(f"already set to {existing}")
        return
    if (ROOT / "state" / "omnivox-profile").exists():
        # An empty `portal:` legitimately means "the built-in default", so it
        # is not proof that nobody has set this up. A signed-in profile is.
        ok("already signed in somewhere, so leaving this alone")
        return
    if not sys.stdin.isatty():
        warn("not running in a terminal, so this cannot ask. Run `make find-portal` later.")
        return

    from src.portal_finder import find

    print("    Type its name the way you would say it out loud.")
    print("    Give the full name: several colleges share a first word.")
    for _attempt in range(3):
        try:
            name = input("    > ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not name:
            return
        print("    Looking...", flush=True)
        got = find(name)
        if not got:
            warn("no Omnivox portal answered for that name. Try the fuller name,")
            warn("or press Enter to skip and run `make find-portal` later.")
            continue
        config.write_text(
            set_value(config.read_text(encoding="utf-8"), ["school", "portal"], got.slug),
            encoding="utf-8",
        )
        ok(f"{got.college}  ->  https://{got.slug}.omnivox.ca")
        if got.language == "en":
            warn("Its sign-in page is in ENGLISH. This navigates by clicking French")
            warn("text, so after signing in, run `make find-portal` and follow what")
            warn("it prints, or every course will report no documents.")
        return
    warn("skipping. Run `make find-portal` when you know the name.")


#: Returned when everything automatable is done and the rest needs a person
#: sitting at the machine. Not a failure.
NEEDS_A_PERSON = 3


def _this_is_a_person() -> bool:
    """Is a human at this terminal, or is an agent driving it?

    stdin being a terminal is the best proxy available. It matters because the
    next two steps open windows on somebody's screen, and an agent tool that
    runs commands in a sandbox gets a browser its user will never see: the
    command reports that it launched, nothing appears, and the person waits.
    """
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


def _hand_over(remaining: str) -> int:
    command = "py install.py" if IS_WINDOWS else "python3 install.py"
    print(
        "\n" + "=" * 66
        + "\nEverything that can be automated is done. The rest needs you at the\n"
        "keyboard, because it opens windows on your screen:\n\n"
        f"    {remaining}\n\n"
        "Open a terminal yourself"
        + (" (PowerShell)" if IS_WINDOWS else "")
        + f", go to this folder, and run:\n\n    {command}\n\n"
        "It picks up exactly where this stopped and skips everything already\n"
        "done. This is not an error, and nothing has gone wrong.\n"
        + "=" * 66
    )
    return NEEDS_A_PERSON


def sign_in() -> None:
    step("Signing in to Omnivox")
    print("    A browser window is about to open. Type your student number and")
    print("    password into it. If Omnivox e-mails you a six-digit code, enter it")
    print("    and TICK \u00abJ'utilise un appareil de confiance\u00bb before validating.")
    print("    Missing that box is what makes it stop working tomorrow.")
    print("    If no window appears within a few seconds, press Ctrl-C and tell")
    print("    me, because that means the browser opened somewhere you cannot see.\n")
    got = run([str(VENV_PYTHON), "-m", "src.omnivox_sync", "--login"])
    if got.returncode != 0:
        die("the sign-in did not complete. Run the installer again to retry.")


def choose_settings() -> None:
    step("Your college, and which parts of this you want")
    print("    A page opens in your browser. Four questions, then it closes.\n")
    run([str(VENV_PYTHON), "-m", "src.setup_page"])


def first_sync() -> None:
    step("Rehearsing, then running for real")
    got = run([str(VENV_PYTHON), "-m", "src.omnivox_sync", "--dry-run"])
    if got.returncode != 0:
        warn("the rehearsal reported a problem. Not syncing. See logs/ and `make doctor`.")
        return
    ok("rehearsal fine, syncing for real. The first run backfills the whole")
    ok("semester so far, which is normally a hundred-odd files.")
    run([str(VENV_PYTHON), "-m", "src.omnivox_sync"])


def finish() -> None:
    print("\n" + "=" * 66)
    print("Done. What you have now:")
    run([str(VENV_PYTHON), "-m", "src.omnivox_sync", "--doctor"])
    print(
        "\nRun it any time with `make sync`, or on Windows:\n"
        "    .venv\\Scripts\\python -m src.omnivox_sync\n"
        "\n`make settings` changes anything you were asked, and more.\n"
        "`make doctor` answers \"is this working\" without touching the network.\n"
    )


def main(argv: list[str]) -> int:
    print(__doc__.split("\n\n")[0])

    if IN_VENV_FLAG not in argv:
        check_python()
        check_checkout()
        make_venv()
        install_requirements()
        # Re-launch inside the environment we just built, so everything after
        # this can import the project directly rather than shelling out to
        # guess at it.
        return subprocess.run(
            [str(VENV_PYTHON), str(Path(__file__).resolve()), IN_VENV_FLAG],
            cwd=str(ROOT),
        ).returncode

    install_browser()
    make_config_files()
    check_converters()

    if not _this_is_a_person():
        # Everything from here opens a window. An agent running this in a
        # sandbox gets a browser nobody can see, reports that it launched, and
        # leaves its user staring at a screen where nothing happened.
        return _hand_over("one settings page, then signing in to Omnivox")

    # The settings page first, and it asks which college too. Two windows
    # instead of a terminal prompt plus two windows, and the college has to be
    # known before the sign-in anyway, because it decides which Omnivox to
    # open.
    choose_settings()
    set_portal()
    sign_in()
    first_sync()
    finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
