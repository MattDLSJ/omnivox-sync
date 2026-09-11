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
questions on the settings page.

Safe to run again. Every step checks whether it is already done and skips it,
so an interrupted install is fixed by running this a second time rather than
by starting over. It runs on stock Python and needs nothing installed first.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
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


def check_notebooklm() -> None:
    """Say out loud whether the headline feature can actually run.

    NotebookLM defaults to "yes, do it automatically" on the settings page,
    and doing it automatically needs the `nlm` CLI. Nothing installed it,
    nothing looked for it, and the failure is silent by design: the uploader
    falls back to copying files into _to_upload/ folders. So somebody could
    pick the default, watch the install succeed, and get staging folders
    forever without one line anywhere telling them why.

    Reported, not installed. Installing it needs `uv`, which is another thing
    this would be fetching on somebody's behalf without asking.
    """
    step("Checking NotebookLM")
    if shutil.which("nlm"):
        ok("nlm found")
        return
    warn("The `nlm` command is not installed, so NotebookLM uploads cannot run.")
    warn("Nothing is lost: files are copied into a _to_upload folder per course")
    warn("instead, and you can upload them by hand. To do it automatically:")
    warn("    uv tool install git+https://github.com/jacob-bd/notebooklm-mcp-cli.git")
    warn("    nlm login")
    warn("(`uv` is a Python tool installer. If you do not have it either, see")
    warn(" https://docs.astral.sh/uv/ . Or answer No to NotebookLM in `make settings`.)")


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

#: How long the settings page waits. Generous when somebody is at the keyboard,
#: shorter when an agent is driving: if the address cannot be reached from
#: wherever they are, half an hour of silence is a poor way to find that out.
TIMEOUT_INTERACTIVE = 1800
# Under an agent this runs inside a tool call, and those are capped: Claude
# Code cuts a Bash call off at 600 seconds. A 900 second wait could therefore
# never be reached, it could only be killed partway through, losing the form
# and everything printed with it. Shorter than the cap, so the installer is
# the thing that decides how the wait ends.
TIMEOUT_DRIVEN = 540


def _page_timeout(interactive: bool) -> int:
    """Overridable, because a test that has to wait fifteen real minutes to
    prove a code path is a test nobody runs."""
    override = os.environ.get("OMNIVOX_SETUP_TIMEOUT")
    if override and override.isdigit():
        return int(override)
    return TIMEOUT_INTERACTIVE if interactive else TIMEOUT_DRIVEN


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


def _credentials_present() -> bool:
    sys.path.insert(0, str(ROOT))
    from src.common import load_credentials

    user, password = load_credentials(ROOT, required=False)
    return bool(user and password)


def ensure_credentials() -> None:
    """Ask for them properly, if signing in did not already capture them.

    `make login` offers to keep what was typed into the browser, but that can
    come back empty: a portal that asks for the number and the password on two
    separate screens never has both on the page at once, and a password
    manager may fill the form in a way the page does not report. Without a
    fallback here, the next thing that happens is an agent inventing one, and
    the one it invented was opening Notepad and handing over a template with
    `your_password` written in it.

    scripts/set_env.py asks, validates, and writes. The value never goes
    through a shell, never appears in history, and is never shown.
    """
    if _credentials_present():
        return
    step("Saving your credentials so scheduled runs can sign in")
    print("    The signed-in session does not survive the browser closing, so a")
    print("    scheduled run signs in from scratch every time and needs these.")
    print("    They go straight into .env on this machine. Nothing prints them.\n")
    if not sys.stdin.isatty():
        warn("not a terminal, so this cannot ask. Later, run:")
        warn("    make setup-omnivox")
        return
    for key in ("OMNIVOX_USER", "OMNIVOX_PASS"):
        run([str(VENV_PYTHON), str(ROOT / "scripts" / "set_env.py"), key])


#: Windows CREATE_NEW_CONSOLE. Spelled out rather than read off subprocess,
#: because getattr(subprocess, "CREATE_NEW_CONSOLE", 0) falls back to 0 and 0
#: means "inherit the parent's console", which is precisely the bug this exists
#: to avoid. A silent fallback to the broken behaviour is worse than an
#: AttributeError, and worse still is that it only shows up on somebody else's
#: machine.
CREATE_NEW_CONSOLE = 0x00000010


def _visible_window(args: list[str], *, what: str, timeout_s: int = 900) -> int | None:
    """Run `args` in a window the person can actually see. None if it cannot.

    An AI agent runs commands inside its own process, and that process is not
    a desktop session. Two things follow, both seen on real installs. A
    browser launched from it either appears somewhere the user never sees or,
    on Windows, fails outright: Playwright's driver reported `spawn UNKNOWN`,
    which is Node saying the process could not be created in that environment
    at all. And the installer's answer was to stop and print "open PowerShell
    yourself and run this", which is three steps and a context switch for
    somebody who asked an agent precisely so they would not have to.

    So the sign-in gets its own console, started by the OS rather than
    inherited from whatever is driving us. On Windows that is CREATE_NEW_CONSOLE;
    on macOS it is a Terminal window. Both put a real window in front of the
    person with no instructions to follow, and both let this process wait and
    then carry on by itself.
    """
    marker = ROOT / "state" / "handover.exit"
    marker.unlink(missing_ok=True)
    marker.parent.mkdir(parents=True, exist_ok=True)

    if IS_WINDOWS:
        try:
            proc = subprocess.Popen(
                args,
                cwd=str(ROOT),
                creationflags=CREATE_NEW_CONSOLE,
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"could not open a window for {what}: {exc}")
            return None
        print(f"    A new window has opened for {what}. Do it there.")
        print("    This one waits, and carries on by itself when you are done.\n")
        try:
            return proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            return None

    if sys.platform == "darwin":
        import shlex
        import stat as _stat

        # `open` a .command file rather than telling Terminal to `do script`.
        # The AppleScript route needs Automation permission, and asking for it
        # puts up a system dialog that blocks: measured here, osascript sat
        # for the full 30 second timeout on a Mac that had never granted it,
        # then failed. `open` needs no permission and starts Terminal itself
        # if it is not running.
        inner = " ".join(shlex.quote(a) for a in args)
        launcher = ROOT / "state" / "handover.command"
        launcher.write_text(
            "#!/bin/sh\n"
            f"cd {shlex.quote(str(ROOT))} || exit 1\n"
            f"{inner}\n"
            f"echo $? > {shlex.quote(str(marker))}\n",
            encoding="utf-8",
        )
        launcher.chmod(launcher.stat().st_mode | _stat.S_IXUSR | _stat.S_IXGRP)
        try:
            subprocess.run(["open", "-a", "Terminal", str(launcher)],
                           check=True, timeout=30)
        except Exception as exc:  # noqa: BLE001
            warn(f"could not open a window for {what}: {exc}")
            return None
        print(f"    A Terminal window has opened for {what}. Do it there.")
        print("    This one waits, and carries on by itself when you are done.\n")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if marker.exists():
                try:
                    return int(marker.read_text(encoding="utf-8").strip() or "1")
                except ValueError:
                    return 1
            time.sleep(1)
        return None

    return None


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


def _config_problem() -> str:
    """"" when config.yaml is fine, else what is actually wrong with it.

    These used to be one boolean, so a file that would not parse was reported
    as "no college is set, run the installer again and answer the settings
    page" — telling the student to repeat the step that had just corrupted it.
    A parse error and an unanswered question are different problems and only
    one of them is the person's to fix.
    """
    import yaml

    try:
        loaded = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        return f"config.yaml is not valid YAML, so nothing can read it:\n{exc}"
    if not (loaded.get("school") or {}).get("portal"):
        return "no college is set yet"
    return ""


def _portal_is_set() -> bool:
    return not _config_problem()


def choose_settings(*, interactive: bool = True) -> None:
    step("Your college, and which parts of this you want")
    print("    A page opens in your browser, with its address printed below in")
    print("    case it does not. Answer it THERE: nothing typed anywhere else")
    print("    reaches it.\n")
    args = [str(VENV_PYTHON), "-m", "src.setup_page",
            "--timeout", str(_page_timeout(interactive))]
    # The window opens whether or not somebody is typing at this terminal.
    #
    # It used to be suppressed for any non-tty run, on the reasoning that
    # nobody is at that screen. That reasoning confused two different things.
    # A test or a script has nobody at the screen. An AI agent running a
    # command on somebody's own computer has a person sitting right there,
    # and suppressing the window meant the page never appeared: the agent
    # announced it had opened the setup page, nothing happened, and it then
    # spent three rounds trying to render a local address inside its own
    # in-app browser before finally handing over a link. Observed, on a real
    # install, by the person it was supposed to be helping.
    #
    # Where there is genuinely no display the call fails harmlessly and the
    # printed address is still the answer, so opening it is never worse.
    # Tests and CI opt out explicitly, which is what the distinction should
    # have been from the start.
    if os.environ.get("SCHOOL_NO_BROWSER") == "1":
        args.append("--no-browser")
    run(args)


def first_sync() -> None:
    step("Rehearsing, then running for real")
    got = run([str(VENV_PYTHON), "-m", "src.omnivox_sync", "--dry-run"])
    if got.returncode != 0:
        warn("the rehearsal reported a problem. Not syncing. See logs/ and `make doctor`.")
        return
    ok("rehearsal fine, syncing for real. The first run backfills the whole")
    ok("semester so far, which is normally a hundred-odd files.")
    run([str(VENV_PYTHON), "-m", "src.omnivox_sync"])


def organize_existing() -> None:
    """File the course material already scattered on this machine.

    Almost nobody installs this in week one. They install it in week three,
    with four PDFs in Downloads and a syllabus on the desktop, and the sync
    fetches from today forward and leaves all of that where it was. That is
    how somebody ends up with two copies of a course: the tidy one this made
    and the real one they have been using.
    """
    sys.path.insert(0, str(ROOT))
    from src.common import load_config

    try:
        cfg = load_config(ROOT / "config.yaml", repo_root=ROOT)
    except Exception:  # noqa: BLE001
        return
    if not getattr(cfg, "organize_scan", True):
        return

    step("Looking for course files already on this machine")
    print("    Downloads, Desktop and Documents. Nothing is ever deleted, and")
    print("    a file only moves when its name says which course it belongs to.")
    if sys.platform == "darwin":
        # This is where the TCC dialog appears, and it appears with no warning
        # in the middle of an install, which is how somebody clicks Don't
        # Allow. Declining is then silent: the scan finds nothing and says so
        # cheerfully, which reads as "there was nothing to find".
        print("    macOS will ask for access to your Desktop and Documents.")
        print("    Declining is fine, but then this step finds nothing at all.")
    print()
    from src.organize import organize, summary

    try:
        # Planned first, then shown, then done. It used to move the files and
        # print the summary afterwards, while the settings page promised it
        # "shows you what it found grouped by course, and moves them in", in
        # that order. Nobody reading a list of files that have already been
        # moved out of their Downloads folder experiences that as a preview.
        plan = organize(cfg, logger=None, dry_run=True)
    except Exception as exc:  # noqa: BLE001 - never worth the rest of setup
        warn(f"could not search for existing files ({type(exc).__name__})")
        return
    for line in summary(plan, planned=True).splitlines():
        print(f"    {line}")
    try:
        organize(cfg, logger=None)
    except Exception as exc:  # noqa: BLE001
        warn(f"could not move the files it found ({type(exc).__name__})")


def schedule_it() -> None:
    """Install the scheduled job, if the settings page said so.

    At the end of setup rather than as a recommendation afterwards. A setup
    that finishes with "next steps" is a setup where the next steps do not
    happen: the person is done, the window is closed, and the thing that was
    supposed to run three times a day never runs at all.
    """
    sys.path.insert(0, str(ROOT))
    from src.common import load_config

    try:
        cfg = load_config(ROOT / "config.yaml", repo_root=ROOT)
    except Exception:  # noqa: BLE001
        return
    if not getattr(cfg, "schedule_auto", True):
        ok("not scheduling, because you asked for it to stay manual")
        return

    step("Setting it to run by itself")
    if IS_WINDOWS:
        _schedule_windows(cfg)
    else:
        got = run(["./scripts/install_launchd.sh", "install"])
        if got.returncode == 0:
            ok("installed. It will check " + _times(cfg))
        else:
            warn("could not install the scheduled job. Run: make install-launchd")


def _times(cfg) -> str:
    return ", ".join(f"{h:02d}:{m:02d}" for h, m in cfg.sync_times)


def _schedule_windows(cfg) -> None:
    """One Task Scheduler entry per sync time.

    Separate tasks rather than one with a repeat interval: /RI needs a
    duration window and gets the last run of the day wrong, and three plainly
    named tasks are three things somebody can see and delete.
    """
    python = str(VENV_PYTHON)
    made = 0
    for hour, minute in cfg.sync_times:
        name = f"OmnivoxSync_{hour:02d}{minute:02d}"
        got = run([
            "schtasks", "/Create", "/F",
            "/TN", name,
            "/TR", f'"{python}" -m src.omnivox_sync',
            "/SC", "DAILY",
            "/ST", f"{hour:02d}:{minute:02d}",
        ], capture_output=True, text=True)
        if got.returncode == 0:
            made += 1
        else:
            warn(f"could not create {name}: {(got.stderr or '').strip()[:120]}")
    if made:
        ok(f"{made} scheduled task(s) created. It will check " + _times(cfg))
        ok("Remove them any time with: schtasks /Delete /TN OmnivoxSync_0730 /F")
    else:
        warn("no scheduled tasks were created. Windows may need this window")
        warn("to be running as administrator.")


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
    check_notebooklm()

    # The settings page runs either way. It is a local web page, so what it
    # needs is a browser the PERSON can reach, not one this process can open,
    # and printing the address is enough for that. Only the Omnivox sign-in
    # genuinely needs a window on this desktop, because Playwright drives it.
    person = _this_is_a_person()
    choose_settings(interactive=person)
    if person:
        set_portal()

    if not _portal_is_set():
        if not person:
            return _hand_over(
                "the settings page, which was not answered, and then signing in"
            )
        warn("no college is set, so signing in would open the wrong Omnivox.")
        warn("Run the installer again and answer the settings page.")
        return NEEDS_A_PERSON

    if not person:
        # Not a hand-over any more, unless the window will not open. The
        # sign-in needs a desktop, this process does not have one, and the
        # answer is to ask the OS for a window rather than to ask the person
        # to go and make one.
        step("Signing in to Omnivox")
        print("    A browser opens for you to sign in. TICK")
        print("    \u00abJ'utilise un appareil de confiance\u00bb if it asks for a code,")
        print("    or it stops working tomorrow.\n")
        code = _visible_window(
            [str(VENV_PYTHON), "-m", "src.omnivox_sync", "--login"],
            what="the Omnivox sign-in",
        )
        if code is None:
            return _hand_over("signing in to Omnivox")
        if code != 0:
            warn("the sign-in window closed without finishing.")
            return _hand_over("signing in to Omnivox")
        ok("signed in")
        ensure_credentials()
        first_sync()
        organize_existing()
        schedule_it()
        finish()
        return 0

    sign_in()
    ensure_credentials()
    first_sync()
    organize_existing()
    schedule_it()
    finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
