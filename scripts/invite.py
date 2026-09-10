#!/usr/bin/env python3
"""One link you can hand to somebody, that their AI can actually read.

The obvious way to share this is a link to INSTALL.md on GitHub. That fails,
silently and confusingly, while the repository is private: an agent fetching
that URL gets a sign-in page, reports that the file is empty or missing, and
then improvises. Which is how three people ended up downloading the ZIP.

So the invitation lives in a public gist instead. The gist holds INSTALL.md
and nothing else. It says how to get the code, which still requires access, so
publishing it gives nothing away that the repository does not already gate.
The repository stays private; only the instructions are public.

  make invite

Creates the gist the first time, updates it every time after, and prints the
message to send. The gist id is remembered in this repo's git config, so it is
per-machine and never committed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL = REPO_ROOT / "INSTALL.md"
DESCRIPTION = "school-automation: give this to your AI coding agent"


def _git(*args: str, check: bool = True) -> str:
    got = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True
    )
    if check and got.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{got.stderr.strip()}")
    return got.stdout.strip()


def _gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--new", action="store_true", help="force a fresh gist")
    args = parser.parse_args(argv)

    if not INSTALL.is_file():
        raise SystemExit(f"No {INSTALL.name} to publish.")

    if _gh("auth", "status").returncode != 0:
        raise SystemExit(
            "gh is not signed in, so it cannot create the gist. Run `gh auth "
            "login`\nfirst. Nothing else here needs gh."
        )

    gist = "" if args.new else _git("config", "--get", "invite.gist", check=False)

    if gist:
        got = _gh("gist", "edit", gist, "--filename", "INSTALL.md", str(INSTALL))
        if got.returncode != 0:
            print(got.stderr.strip(), file=sys.stderr)
            raise SystemExit(
                f"Could not update gist {gist}. If it was deleted, make a new "
                "one:\n  make invite NEW=1"
            )
        print(f"Updated gist {gist}.")
    else:
        got = _gh("gist", "create", "--public", "--desc", DESCRIPTION, str(INSTALL))
        if got.returncode != 0:
            print(got.stderr.strip(), file=sys.stderr)
            raise SystemExit("Could not create the gist.")
        url = got.stdout.strip().splitlines()[-1]
        gist = url.rstrip("/").rsplit("/", 1)[-1]
        _git("config", "invite.gist", gist)
        print(f"Created gist {gist} and remembered it in this repo's git config.")

    owner = _gh("api", "user", "--jq", ".login").stdout.strip() or "USER"
    raw = f"https://gist.githubusercontent.com/{owner}/{gist}/raw/INSTALL.md"

    print(
        "\nThe link to send. Raw markdown, so an agent that fetches it gets the\n"
        "instructions rather than a web page:\n\n"
        f"  {raw}\n\n"
        f"The human-readable version of the same thing:\n\n"
        f"  https://gist.github.com/{owner}/{gist}\n\n"
        "Re-run `make invite` after editing INSTALL.md. The link never changes."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
