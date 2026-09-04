#!/usr/bin/env python3
"""Grant the microphone to the recorder. Run it as printed; it does the rest.

Why this is not just a System Settings link
-------------------------------------------
There is nothing in System Settings to switch on. The Microphone pane has no
"+" button, so a permission can only appear there after macOS has asked, and
macOS was refusing to ask. From the system log, with the recorder spawning
ffmpeg itself:

    requires entitlement com.apple.security.device.audio-input but it is
    missing for responsible={identifier=python3, ...python3.14}
    Policy disallows prompt; access to kTCCServiceMicrophone denied

TCC judges the RESPONSIBLE process, not the one that asks. python.org's
interpreter is built with the hardened runtime and without the audio-input
entitlement, so the request was denied without a prompt, and ffmpeg received a
clean stream of zeroes: right file size, right duration, no sound.

So this script builds a small signed .app around ffmpeg, which gives TCC an
identity and a usage description it is willing to show, and runs it from
launchd so that python is nowhere in the chain. Then macOS asks, once.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

SECONDS = 4


def main() -> int:
    from src.micapp import MicAppError, build, start_capture_job, stop_capture_job
    from src.recorder import find_ffmpeg
    from src.transcribe import SILENT_PEAK_DB, peak_db

    out = REPO / "state" / "mic-setup.m4a"
    err = REPO / "state" / "mic-setup.log"
    for path in (out, err):
        path.unlink(missing_ok=True)

    print()
    print("  Micro du recorder")
    print("  On construit SchoolRecorder.app, puis macOS va demander la permission.")
    print()

    try:
        binary = build(REPO, find_ffmpeg())
    except MicAppError as exc:
        print(f"  Échec de la construction : {exc}", file=sys.stderr)
        return 1

    argv = [
        str(binary), "-nostdin", "-hide_banner", "-loglevel", "warning",
        "-f", "avfoundation", "-i", ":0", "-t", str(SECONDS),
        "-ac", "1", "-c:a", "aac", "-b:a", "64k", "-y", str(out),
    ]
    try:
        start_capture_job(argv, err, REPO / "state" / "capture.plist")
    except MicAppError as exc:
        print(f"  Échec du lancement : {exc}", file=sys.stderr)
        return 1

    print("  >>> Clique « Autoriser » dans la fenêtre que macOS vient d'afficher. <<<")
    print()
    print("  (Elle peut être derrière une autre fenêtre. J'attends 90 secondes.)")

    # The prompt blocks ffmpeg until it is answered, so the file appearing IS
    # the answer. Polling beats a fixed sleep: clicking takes two seconds or
    # ninety depending on where the dialog landed.
    deadline = time.time() + 90
    while time.time() < deadline:
        if out.exists() and out.stat().st_size > 0:
            break
        time.sleep(0.5)
    stop_capture_job()

    if not out.exists() or out.stat().st_size == 0:
        print()
        print("  Rien n'a été enregistré. La fenêtre n'a probablement pas été",
              file=sys.stderr)
        print("  cliquée, ou elle a été refusée. Relance : make setup-mic",
              file=sys.stderr)
        detail = err.read_text(encoding="utf-8", errors="replace").strip()
        if detail:
            print(f"\n  ffmpeg a dit : {detail[:300]}", file=sys.stderr)
        return 1

    peak = peak_db(out)
    print()
    if peak <= SILENT_PEAK_DB:
        print(f"  Le fichier existe mais il est MUET ({peak:.0f} dBFS).", file=sys.stderr)
        print("  La permission a été refusée. Ouvre Réglages > Confidentialité et",
              file=sys.stderr)
        print("  sécurité > Microphone, active SchoolRecorder, puis relance.",
              file=sys.stderr)
        return 1

    print(f"  Vérifié : {SECONDS} s captées, crête à {peak:.0f} dBFS. Le micro fonctionne.")
    print("  SchoolRecorder apparaît maintenant dans Réglages > Confidentialité")
    print("  et sécurité > Microphone.")
    out.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
