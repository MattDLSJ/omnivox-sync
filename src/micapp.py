"""Give ffmpeg an identity macOS is willing to ask about.

The problem
-----------
On 2026-08-26 the recorder captured a full Psycho lecture as digital silence.
ffmpeg opened the avfoundation device, wrote a segment every 60 seconds, exited
cleanly and left an empty error log. Nothing anywhere reported a failure. The
microphone had simply never been granted, and no entry for ffmpeg or for python
existed in System Settings > Privacy & Security > Microphone to turn on.

Why nothing was listed
----------------------
TCC will not prompt for a subject it cannot name. Read from the system log
while a bare ffmpeg asked for the mic from launchd: no AUTHREQ_PROMPTING line
at all, and ffmpeg blocked forever inside AVFoundation without printing a
single byte, even at -loglevel info. A plain Homebrew binary has no bundle
identifier and no usage description, so there is nothing to put in the dialog
and nothing to list afterwards. The request is neither granted nor denied. It
hangs.

Wrap the same binary in a .app with an identifier and an
NSMicrophoneUsageDescription and the log changes to:

    AUTHREQ_PROMPTING: service=kTCCServiceMicrophone,
                       subject=Sub:{com.school.recorder.mic}
    found general usage key

which is macOS agreeing to ask. Grant it once and it appears by name in System
Settings from then on.

Why the executable is a copy and not a symlink
----------------------------------------------
TCC resolves the real path of what it is looking at, so a symlink lands back on
/opt/homebrew and loses the bundle identity. It also has to be a real Mach-O:
a shell script would be executed by /bin/sh, and /bin/sh is the process TCC
would see. So the bundle holds a copy of the ffmpeg binary.

That copy is dynamically linked against a VERSIONED Homebrew path
(/opt/homebrew/Cellar/ffmpeg/8.0.1_4/lib/...), so a Homebrew upgrade moves the
libraries out from under it. `healthy()` runs the copy before anyone relies on
it, and the recorder falls back to plain ffmpeg rather than failing outright.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
from pathlib import Path

BUNDLE_ID = "com.school.recorder.mic"
APP_NAME = "SchoolRecorder.app"

#: Shown in the macOS permission dialog, so it is in French and says what it is.
USAGE = "SchoolRecorder enregistre les cours auxquels tu assistes, pour les transcrire."


class MicAppError(RuntimeError):
    pass


def app_path(repo_root: Path) -> Path:
    """Build output, so it lives under state/ and is gitignored.

    The path is part of the TCC record. Moving or rebuilding the bundle means
    granting the microphone again, which is what `make setup-mic` is for.
    """
    return Path(repo_root).resolve() / "state" / APP_NAME


def binary_path(repo_root: Path) -> Path:
    return app_path(repo_root) / "Contents" / "MacOS" / "ffmpeg"


def healthy(binary: Path) -> bool:
    """Does this copy still run? See the module docstring on Homebrew upgrades."""
    if not binary.exists():
        return False
    try:
        proc = subprocess.run(
            [str(binary), "-hide_banner", "-version"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def capture_binary(repo_root: Path, fallback: str) -> str:
    """The ffmpeg to record with: the bundled one when it works.

    Falls back rather than raising. A broken bundle should cost the permission
    prompt, not the class.
    """
    binary = binary_path(repo_root)
    return str(binary) if healthy(binary) else fallback


def info_plist() -> bytes:
    return plistlib.dumps({
        "CFBundleExecutable": "ffmpeg",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "SchoolRecorder",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        # No Dock icon and no menu bar: this is a recorder, not an app to look at.
        "LSBackgroundOnly": True,
        "NSMicrophoneUsageDescription": USAGE,
    })


def build(repo_root: Path, source_ffmpeg: str) -> Path:
    """Create (or replace) the bundle. Returns the path to its executable."""
    app = app_path(repo_root)
    macos = app / "Contents" / "MacOS"
    shutil.rmtree(app, ignore_errors=True)
    macos.mkdir(parents=True)

    real = Path(source_ffmpeg).resolve()  # Homebrew's bin/ffmpeg is a symlink
    target = macos / "ffmpeg"
    shutil.copy2(real, target)
    target.chmod(0o755)
    (app / "Contents" / "Info.plist").write_bytes(info_plist())

    # Ad-hoc is enough: TCC needs a stable identity, not a Developer ID. The
    # signature covers the Info.plist, so the identifier cannot be swapped
    # underneath the grant.
    proc = subprocess.run(
        ["codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID, str(app)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise MicAppError(f"codesign failed: {(proc.stderr or '').strip()[:300]}")

    if not healthy(target):
        raise MicAppError(
            f"The bundled ffmpeg copied from {real} does not run. "
            "Homebrew may have moved its libraries; try `brew reinstall ffmpeg`."
        )
    return target


# --------------------------------------------------------------------------
# Running the capture so that TCC attributes it to the bundle
# --------------------------------------------------------------------------
#
# Wrapping ffmpeg is only half of it. TCC does not look at the process that
# asks; it looks at the RESPONSIBLE process, which for a subprocess is the
# ancestor that owns the session. When python spawns the bundle, python stays
# responsible, and the log says exactly what that costs:
#
#     Prompting policy for hardened runtime; service: kTCCServiceMicrophone
#     requires entitlement com.apple.security.device.audio-input but it is
#     missing for responsible={identifier=python3, ...python3.14}
#     Policy disallows prompt for Sub:{...python3.14}; access denied
#
# python.org's interpreter is built with the hardened runtime and without the
# audio-input entitlement, so TCC refuses to even ASK and denies instead. That
# denial is what reaches ffmpeg as a stream of zeroes: no error, no prompt, no
# entry in System Settings, a full lecture of silence.
#
# start_new_session=True does not help; the log still names python. The fix is
# for python not to be in the chain at all. Handing the capture to launchd
# makes launchd the parent, attribution lands on the bundle, and the same log
# line becomes AUTHREQ_PROMPTING for com.school.recorder.mic.

CAPTURE_LABEL = "com.school.capture"


def _launchctl(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, timeout=timeout
    )


def _domain() -> str:
    import os

    return f"gui/{os.getuid()}"


def capture_plist(label: str, argv: list[str], err_path: Path) -> bytes:
    """A one-shot agent. No KeepAlive: when the class ends ffmpeg exits and
    launchd must let it, not restart it into the next lecture.

    Every path must be absolute. launchd runs with no working directory of its
    own, so a relative ProgramArguments[0] starts a job that launchd reports as
    running, with a pid, that has in fact executed nothing: no audio, no error,
    no stderr. That is indistinguishable from a working capture until the class
    is over.
    """
    if not argv:
        raise MicAppError("capture_plist needs a command")
    if not Path(argv[0]).is_absolute():
        raise MicAppError(f"the capture binary must be an absolute path, got {argv[0]!r}")
    # The last argument is ffmpeg's output. A relative one fails differently
    # from a relative binary and just as quietly: launchd starts the job, ffmpeg
    # runs, and the only trace is "Could not write header (incorrect codec
    # parameters ?): No such file or directory" in a log nobody is watching,
    # which names neither the path nor the working directory. Cost a class on
    # 2026-08-27 before the real cause was spotted.
    if not Path(argv[-1]).is_absolute():
        raise MicAppError(f"the capture output must be an absolute path, got {argv[-1]!r}")
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": argv,
        "StandardErrorPath": str(Path(err_path).resolve()),
        "RunAtLoad": False,
        "KeepAlive": False,
        "ProcessType": "Interactive",
    })


def running_pid(label: str = CAPTURE_LABEL) -> int | None:
    """The pid launchd gave the job, or None if it is not running."""
    import re

    proc = _launchctl("print", f"{_domain()}/{label}", timeout=30)
    if proc.returncode != 0:
        return None
    match = re.search(r"^\s*pid\s*=\s*(\d+)", proc.stdout or "", re.MULTILINE)
    return int(match.group(1)) if match else None


def stop_capture_job(label: str = CAPTURE_LABEL) -> None:
    """Unregister the job. Safe to call when nothing is registered."""
    _launchctl("bootout", f"{_domain()}/{label}", timeout=60)


def start_capture_job(
    argv: list[str], err_path: Path, plist_path: Path, label: str = CAPTURE_LABEL,
) -> int | None:
    """Run argv under launchd and return its pid, or None if it never started.

    See the block comment above for why this cannot simply be Popen.
    """
    stop_capture_job(label)  # a leftover registration blocks bootstrap
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(capture_plist(label, argv, err_path))

    proc = _launchctl("bootstrap", _domain(), str(plist_path))
    if proc.returncode != 0:
        raise MicAppError(
            f"launchctl bootstrap failed: {(proc.stderr or proc.stdout or '').strip()[:300]}"
        )
    proc = _launchctl("kickstart", f"{_domain()}/{label}")
    if proc.returncode != 0:
        stop_capture_job(label)
        raise MicAppError(
            f"launchctl kickstart failed: {(proc.stderr or proc.stdout or '').strip()[:300]}"
        )

    # launchd reports the pid a moment after kickstart returns. Poll rather
    # than sleep a fixed amount, because "no pid at all" is the signal that
    # ffmpeg died on startup and the caller needs it quickly.
    import time

    for _ in range(20):
        pid = running_pid(label)
        if pid:
            return pid
        time.sleep(0.1)
    return None
