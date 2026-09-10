"""Find a college's Omnivox hostname instead of asking somebody to read it.

Omnivox is one product that nearly every cégep in Quebec runs, and the only
thing that differs between two installations is the hostname:
`<something>.omnivox.ca`. The `<something>` is not derivable. It is whatever
the college picked, and it ranges from the obvious (`collegeahuntsic`) through
the abbreviated (`cegepmontpetit`, which drops half the name) to the initials
(`slc`, for Champlain St-Lawrence).

So this does not guess it. It generates the handful of spellings a college
might plausibly have used, asks each one whether it exists, and believes only
the ones that answer. A real portal returns a page whose title is the
college's own name, which is the useful part: the answer comes back as
"cegepmontpetit is Cégep Édouard-Montpetit", and a person can tell at a glance
whether that is their school. A wrong guess cannot survive that, because a
wrong guess does not resolve at all.

When nothing matches, that is the honest answer and the caller falls back to
asking. Six colleges' worth of naming conventions is not a pattern.

    make find-portal

Nothing here logs in or sends anything. It fetches public login pages, which
is what a browser does when you type the address.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.error
import urllib.request

DOMAIN = "omnivox.ca"
TIMEOUT_S = 8

#: Confirmed by fetching them, not by reading a list. Used to answer instantly
#: and to keep a known-good spelling ahead of a generated one. Absent from
#: here means unknown, never means unsupported.
VERIFIED = {
    "cegepmontpetit": "Cégep Édouard-Montpetit",
    "collegeahuntsic": "Collège Ahuntsic",
    "slc": "Cégep Champlain-St.Lawrence",
}

#: Words that appear in a college's name and never in its hostname, or that
#: appear so often they are useless for telling two apart.
NOISE = {"cegep", "cégep", "college", "collège", "de", "du", "des", "d", "la",
         "le", "les", "l", "the", "regional", "campus"}

_TITLE = re.compile(r"<title>\s*(.*?)\s*</title>", re.I | re.S)


def _fold(text: str) -> str:
    """Accents off, punctuation off. É -> e, so "Édouard" can match a URL."""
    stripped = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", ascii_only.lower())


def slugs(name: str) -> list[str]:
    """Hostname spellings worth trying for a college called `name`.

    Ordered most to least likely, deduplicated, and short on purpose: this
    becomes one network request each, against somebody else's servers.
    """
    stripped = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in stripped if not unicodedata.combining(c))
    words = [w for w in re.split(r"[^A-Za-z0-9]+", ascii_name.lower()) if w]
    meaningful = [w for w in words if w not in NOISE] or words
    if not meaningful:
        return []

    joined = "".join(meaningful)
    last = meaningful[-1]
    first = meaningful[0]
    # "Saint" is abbreviated in a hostname, never initialised: Saint-Jerome is
    # cstj and not csj. Getting that one word wrong misses a whole family.
    initials = "".join(
        "st" if w in ("saint", "sainte", "st", "ste") else w[0] for w in meaningful
    )

    out = [
        joined,                 # collegeahuntsic, once cegep is dropped
        f"cegep{joined}",
        f"cegep{last}",         # cegepmontpetit, from Edouard-Montpetit
        last,
        # cvm for Vieux Montreal, cstj for Saint-Jerome: the c is the dropped
        # "cegep", which is exactly why it has to be added back here.
        f"c{initials}",
        f"college{joined}",
        first,
        initials if len(initials) > 2 else "",
    ]
    seen, ordered = set(), []
    for slug in out:
        if slug and slug not in seen:
            seen.add(slug)
            ordered.append(slug)
    return ordered


def _get(url: str) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (school-automation portal check)"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        raw = response.read(200_000)
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def probe(slug: str, fetch=_get) -> str | None:
    """The college's own name if `slug` is a real portal, else None.

    A hostname that does not exist fails to resolve, which is the common case
    and the reason a wrong spelling cannot be mistaken for a right one. A
    hostname that exists but is not Omnivox has no Omnivox title.
    """
    try:
        html = fetch(f"https://{slug}.{DOMAIN}/")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    found = _TITLE.search(html)
    if not found:
        return None
    title = " ".join(found.group(1).split())
    if "omnivox" not in title.lower():
        return None
    # "Omnivox - Cégep Édouard-Montpetit" -> the half that identifies a school.
    college = re.sub(r"^\s*omnivox\s*[-–|]\s*", "", title, flags=re.I).strip()
    return college or title


def find(name: str, fetch=_get) -> tuple[str, str] | None:
    """(hostname, college name) for `name`, or None when nothing answers."""
    folded = _fold(name)
    for slug, college in VERIFIED.items():
        if folded and (folded in _fold(college) or _fold(college) in folded):
            return slug, college
    for slug in slugs(name):
        college = probe(slug, fetch)
        if college:
            return slug, college
    return None


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = sys.argv[1:] if argv is None else argv
    name = " ".join(argv).strip()
    if not name:
        print("Which college? Type its name, as you would say it out loud.")
        print("  examples: Ahuntsic / Edouard-Montpetit / Champlain St-Lawrence")
        try:
            name = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 1
    if not name:
        return 1

    print(f"\nLooking for {name}...")
    got = find(name)
    if not got:
        print(
            "\nNo Omnivox portal answered for any spelling of that name. That "
            "does not\nmean your college is unsupported: it means the hostname "
            "is one this could\nnot derive, which is common.\n\n"
            "Log into Omnivox in your browser and read the address bar. If it "
            "says\n  https://SOMETHING.omnivox.ca\nthen SOMETHING is the value, "
            "and it goes in config.yaml exactly as below."
        )
        return 1

    slug, college = got
    print(
        f"\nFound {college}\n  https://{slug}.{DOMAIN}\n\n"
        "If that is your college, this block goes in config.yaml:\n\n"
        "  school:\n"
        f"    portal: \"{slug}\"\n\n"
        "If it is not, run this again with the fuller name of your college."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
