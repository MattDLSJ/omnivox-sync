#!/usr/bin/env python3
"""Ask for one .env value, check it, and write it. No placeholders to swap.

Why this exists
---------------
Reported 2026-08-25: *"every time you send me commands like these, I don't know
if you're asking me to replace the value in the command, or you're asking me to
run it first."*

He is right, and it is the instruction's fault, not his. A command containing
`<paste the URL here>` asks the reader to do two things at once: edit it, then
run it. So nothing here has a placeholder. You run the command as printed, it
asks, you paste, you press enter. The value never goes through a shell, so it
never lands in the shell history either, which for a credential matters.

It also verifies. Writing the wrong value is the actual risk, and a setup step
that says "done" without checking has just moved the failure somewhere less
obvious.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

ENV = REPO / ".env"


# --------------------------------------------------------------------------
# What each key is, where to find it, and how to know it is right
# --------------------------------------------------------------------------


def _check_ics(url: str) -> str:
    """Fetch the feed and say what came back. Returns a success line."""
    from src.calendar_mirror import MirrorError, fetch_ics, parse_ics

    if "/public/" in url:
        raise MirrorError(
            "Ça, c'est l'adresse PUBLIQUE, pas la secrète.\n"
            "Elle ne fonctionne que si « Make available to public » est coché, "
            "ce qui rend ton horaire lisible par n'importe qui sur le web.\n"
            "Prends plutôt « Secret address in iCal format », juste en dessous : "
            "elle contient private- au lieu de public."
        )
    events = parse_ics(fetch_ics(url))
    if not events:
        raise MirrorError(
            "Le flux répond, mais ne contient aucun événement. C'est peut-être "
            "le mauvais calendrier : il faut celui qui s'appelle School."
        )
    return f"{len(events)} événements lus dans le calendrier."


KEYS = {
    "SCHOOL_ICS_URL": {
        "what": "l'adresse secrète iCal du calendrier School",
        "secret": True,
        "where": """
  1. Ouvre Google Agenda dans le navigateur (calendar.google.com)
  2. Passe la souris sur « School » dans la liste de gauche, clique les trois
     points, puis « Settings and sharing »
  3. SCROLLE JUSQU'EN BAS. Tu vas passer par « Access permissions for events »,
     « Share with specific people », puis trois ou quatre blocs de
     notifications. Continue.
  4. Tout en bas il y a une section « Integrate calendar ». Dedans, dans
     l'ordre : Calendar ID, Public address in iCal format, Embed code, et
     enfin « Secret address in iCal format ».
  5. C'est la DERNIÈRE des deux adresses iCal. Clique dessus pour la révéler,
     puis copie-la.

  Elle ressemble à ceci, et se termine toujours par .ics :
     https://calendar.google.com/calendar/ical/....../private-....../basic.ics

  Ne coche PAS « Make available to public ». Ce n'est pas nécessaire, et ça
  rendrait ton horaire lisible par tout le monde.""",
        "check": _check_ics,
    },
    "OMNIVOX_USER": {
        "what": "ton numéro d'étudiant Omnivox",
        "secret": False,
        "where": "  C'est le numéro à 7 chiffres, celui de ta carte étudiante.",
        "check": None,
    },
    "OMNIVOX_PASS": {
        "what": "ton mot de passe Omnivox",
        "secret": True,
        "where": "  Le même que sur omnivox.ca. Il n'est écrit que dans .env, "
                 "qui est gitignoré.",
        "check": None,
    },
    "CHENELIERE_USER": {
        "what": "ton courriel de compte Chenelière (i+ Interactif)",
        "secret": False,
        "where": "  Celui avec lequel tu ouvres https://interactif.cheneliere.ca\n"
                 "  C'est le compte qui donne accès au manuel numérique, pas Omnivox.",
        "check": None,
    },
    "CHENELIERE_PASS": {
        "what": "ton mot de passe Chenelière",
        "secret": True,
        "where": "  Le même que sur interactif.cheneliere.ca. Il n'est écrit que\n"
                 "  dans .env, qui est gitignoré, et il ne passe jamais par le shell.",
        "check": None,
    },
    "GEMINI_API_KEY": {
        "what": "une clé API Gemini (optionnel, seulement si digest.ranking = gemini)",
        "secret": True,
        "where": "  aistudio.google.com > Get API key",
        "check": None,
    },
}


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def write_key(key: str, value: str, env: Path = ENV) -> None:
    """Replace this key's line, or add it. Everything else is left alone.

    Atomic, because .env holds the Omnivox credentials and a half-written .env
    locks the sync out of the site until someone notices.
    """
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")

    env.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=env.parent, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(out).rstrip("\n") + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, env)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in KEYS:
        print("usage: set_env.py <KEY>")
        print("keys :", ", ".join(KEYS))
        return 2

    key = argv[1]
    spec = KEYS[key]

    print()
    print(f"  {key}")
    print(f"  {spec['what']}")
    print(spec["where"])
    print()

    try:
        value = input("  Colle la valeur ici puis appuie sur Entrée : ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Annulé, rien n'a été écrit.")
        return 1

    if not value:
        print("  Rien collé, rien écrit.")
        return 1

    if spec["check"]:
        try:
            note = spec["check"](value)
        except Exception as exc:  # the checkers raise their own clear messages
            print()
            print(f"  Ça n'a pas marché :\n\n{exc}\n")
            print("  Rien n'a été écrit. Relance la commande quand tu as la bonne.")
            return 1
    else:
        note = ""

    write_key(key, value)
    shown = "(gardée secrète)" if spec["secret"] else value
    print()
    print(f"  Écrit dans .env : {key}={shown}")
    if note:
        print(f"  Vérifié : {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
