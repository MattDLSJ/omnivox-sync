#!/usr/bin/env python3
"""Notifications on your phone, in one command.

Every notification this project sends can also go to a phone through ntfy.sh,
a free push service with an app on both stores. It has no accounts: a message
goes to a "topic", and anyone subscribed to that topic's name receives it. So
the name is the only thing keeping your notifications yours, and it has to be
long and random. START-HERE used to ask people to invent one and paste it into
.env by hand, which is two chances to pick "my-school" and one to paste it
somewhere it should not go.

This picks the name, saves it in .env (never config.yaml, which gets pasted
into issues and handed to assistants), says how to subscribe, and sends a test.
Run it again and it keeps the topic you have; `--new` replaces it, which is
also how to shut out anyone who learned the old one.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

KEY = "NTFY_TOPIC"


def main(argv: list[str]) -> int:
    import set_env
    from src.common import NotifyConfig, env_value, notify

    fresh = "--new" in argv[1:]
    topic = "" if fresh else env_value(REPO, KEY)
    if topic:
        print("\n  Ton téléphone a déjà un sujet ntfy. On le garde.")
    else:
        # 64 random bits: nobody guesses it, and it still fits on one line of
        # the app's "subscribe" box without scrolling.
        topic = f"school-{secrets.token_hex(8)}"
        set_env.write_key(KEY, topic, env=REPO / ".env")
        print("\n  Nouveau sujet ntfy enregistré dans .env.")

    print(f"""
  Sur ton téléphone :
    1. Installe l'app « ntfy » (App Store ou Google Play).
    2. Touche +, puis écris ce nom de sujet, sans rien changer d'autre :

         {topic}

  Une notification d'essai part maintenant. Si elle n'arrive pas, vérifie le
  nom lettre par lettre.

  Ce nom est la seule protection : quiconque le connaît peut lire tes
  notifications. Ne le mets nulle part ailleurs. Pour en changer :
  make setup-phone NEW=1
""")
    notify(
        "Omnivox Sync",
        "Les notifications arrivent sur ton téléphone.",
        cfg=NotifyConfig(desktop=False, ntfy_topic=topic),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
