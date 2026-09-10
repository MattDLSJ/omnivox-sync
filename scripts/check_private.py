#!/usr/bin/env python3
"""Fail if anything personal is about to enter, or already sits in, the repo.

The repo leaked once in a way no secret scanner catches. There was no API key
and no password in it. What was in it was a person: a home GPS reading in a
test fixture, a partner's name on a paired audio device, a real timetable with
real teachers in config.yaml, and a commit message naming a class its author
had skipped. Every one of those was written on purpose, by someone documenting
a real bug with real data, which is exactly why no rule about "don't commit
secrets" stopped any of it.

So this checks for the author's own values, and it has to do that without
containing them. Contradiction resolved by reading the needles from files that
are never committed:

  .env              every value in it, credentials included
  config.yaml       the leaves that name a person or a private network
  .private-patterns anything else you want caught, one per line (see the
                    .example next to it): your name, your machine, a partner,
                    a housemate, a home network. A line starting with ! is an
                    exception instead of a needle.
  git config        your commit e-mail, so a rewrite that missed a commit shows

Run it four ways:

  make check-private              the tracked tree at HEAD
  make check-private STAGED=1     the staged blobs, which is what the hook runs
  make check-private HISTORY=1    also commit messages and author lines
  python scripts/check_private.py --path DIR    an exported snapshot

It matches plain substrings case-insensitively, plus your own regexes, against
each line, the file path, and the file with its whitespace collapsed so a value
wrapped onto the next line still counts.

What it does NOT catch, said plainly so nobody trusts it further than it goes:
a value that has been encoded, or broken up by punctuation rather than by
whitespace. That is deliberate. This is a seatbelt against pasting real data
in, which is how every leak here actually happened, and not a defence against
somebody hiding data on purpose.

Nothing here writes. Exit 0 means checked and clean, 1 means a hit, 2 means it
could not check properly, which is NOT the same as clean and must never be
treated as a pass.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

EXIT_CLEAN = 0
EXIT_HIT = 1
EXIT_CANNOT_CHECK = 2

#: config.yaml keys whose values name a PERSON or a private network. Course
#: titles, folder names, notebook names and room codes are deliberately not
#: here: a room code is a building, a course title is in the calendar, and
#: matching on them buries the real hits under two hundred false ones. The
#: line is "would a stranger learn something about a human being from this".
PERSONAL_CONFIG_KEYS = {
    "teacher",
    "school_ssid",
    "school_ip_prefix",
    "ntfy_topic",
    "base_path",
}

#: Values too short or too common to match on without drowning the report.
#: Six, because four matches every room code in the building.
MIN_NEEDLE = 6

#: Binary formats where a substring scan means nothing.
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf", ".m4a", ".wav", ".zip", ".bin"}

#: The needle sources themselves. Matched on the path relative to the root, not
#: on the basename: a nested `docs/.env` is a file that SHIPS and must be read,
#: while the `.env` at the root is nothing but needles and would report every
#: line of itself forever. `.env.example` is deliberately NOT here. It is
#: tracked, it reaches every clone and every snapshot, and it is the likeliest
#: place for someone to paste a real password "just to test".
SKIP_PATHS = {".env", "config.yaml", ".private-patterns"}


@dataclass(frozen=True)
class Needle:
    """One thing to look for, and where it came from."""

    label: str
    value: str
    is_regex: bool = False

    def redacted(self) -> str:
        """Enough to recognise it, not enough to leak it in a CI log."""
        if self.is_regex:
            return self.value
        if len(self.value) <= 3:
            return "*" * len(self.value)
        return f"{self.value[0]}{'*' * (len(self.value) - 2)}{self.value[-1]}"

    def search(self, line: str) -> bool:
        if self.is_regex:
            return re.search(self.value, line, re.IGNORECASE) is not None
        return self.value.lower() in line.lower()


class CannotCheck(Exception):
    """Something stopped the check from being meaningful. Never a pass."""


def _from_env(repo_root: Path) -> list[Needle]:
    path = repo_root / ".env"
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if len(value) >= MIN_NEEDLE:
            out.append(Needle(f".env {key.strip()}", value))
    return out


def _walk_config(node, out: list[Needle], key: str = "") -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _walk_config(v, out, str(k))
        return
    if isinstance(node, list):
        for item in node:
            _walk_config(item, out, key)
        return
    if key in PERSONAL_CONFIG_KEYS and isinstance(node, str):
        value = node.strip()
        # A leading ~ is a home directory, identical on every Mac.
        if len(value) >= MIN_NEEDLE and not value.startswith("~"):
            out.append(Needle(f"config.yaml {key}", value))
    # Campus coordinates are deliberately NOT collected here. They are a
    # public address, they belong in on-campus test fixtures, and flagging
    # them trains you to ignore this tool. A coordinate that matters is one
    # that points at somewhere you sleep, and that goes in .private-patterns.


def _from_config(repo_root: Path) -> list[Needle]:
    path = repo_root / "config.yaml"
    if not path.exists():
        return []
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on the interpreter
        # Swallowing this used to drop seven needles of twenty-one, including
        # every teacher, and still print the word "clean".
        raise CannotCheck(
            "config.yaml exists but PyYAML is not importable, so the needles "
            "in it cannot be read. Use .venv/bin/python, or `make check-private`."
        ) from exc
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[Needle] = []
    _walk_config(raw, out)
    return out


def _from_pattern_file(repo_root: Path) -> tuple[list[Needle], list[str]]:
    """Needles, and the exceptions that cancel a hit."""
    path = repo_root / ".private-patterns"
    if not path.exists():
        return [], []
    # An exception naming an internal label rather than real content would
    # switch off a whole needle SOURCE at once. "!private-patterns" silenced
    # every pattern in the file, which is not an exception, it is an off switch.
    reserved = {"private-patterns", ".env", "config.yaml", "teacher", "git ",
                "ntfy_topic", "school_ssid", "school_ip_prefix", "base_path"}
    needles, allowed = [], []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            # The error message has always told people to add an exception
            # here. For a while that was a lie: there was no such syntax, and
            # the only way past a false positive was --no-verify, which turns
            # the whole thing off.
            token = line[1:].strip().lower()
            if not token:
                continue
            if token in reserved:
                raise CannotCheck(
                    f"'!{token}' in .private-patterns names one of this tool's "
                    "own labels rather than something in your files, which "
                    "would switch off a whole source of needles at once. "
                    "Write the text that should be allowed, or the path of the "
                    "file that should be skipped."
                )
            if len(token) < MIN_NEEDLE:
                raise CannotCheck(
                    f"'!{token}' in .private-patterns is too short to be an "
                    f"exception; it would cancel almost every hit. Use at "
                    f"least {MIN_NEEDLE} characters."
                )
            allowed.append(token)
            continue
        if line.startswith("/") and line.endswith("/") and len(line) > 2:
            needles.append(Needle(".private-patterns", line[1:-1], is_regex=True))
        elif len(line) >= MIN_NEEDLE:
            needles.append(Needle(".private-patterns", line))
    return needles, allowed


def _from_git_identity(repo_root: Path) -> list[Needle]:
    """Your commit e-mail. A rewrite that missed a commit shows up here."""
    got = subprocess.run(
        ["git", "-C", str(repo_root), "config", "--get", "user.email"],
        capture_output=True,
        text=True,
    )
    value = got.stdout.strip()
    if got.returncode == 0 and value and "noreply" not in value.lower():
        return [Needle("git user.email", value)]
    return []


def collect_needles(repo_root: Path) -> list[Needle]:
    from_patterns, _ = _from_pattern_file(repo_root)
    seen: dict[str, Needle] = {}
    for needle in (
        _from_env(repo_root)
        + _from_config(repo_root)
        + from_patterns
        + _from_git_identity(repo_root)
    ):
        seen.setdefault(needle.value.lower(), needle)
    return list(seen.values())


def collect_exceptions(repo_root: Path) -> list[str]:
    _, allowed = _from_pattern_file(repo_root)
    return allowed


def _flatten(text: str) -> str:
    """Every whitespace run removed, so a value broken over two lines matches.

    Not an anti-obfuscation measure. It is for the accident: prose that got
    wrapped mid-value at some column, leaving neither half matchable.
    """
    return "".join(text.split()).lower()


def _flatten_path(text: str) -> str:
    """Same, but underscores and hyphens go too. `Jordan_Tremblay.md` is how a
    name lands in a filename far more often than with a literal space."""
    return _flatten(text.replace("_", " ").replace("-", " "))


def _is_skipped(rel: Path) -> bool:
    return rel.as_posix() in SKIP_PATHS or rel.suffix.lower() in SKIP_SUFFIXES


def _files_at_head(root: Path) -> list[tuple[Path, str | None]]:
    """(relative path, staged content). None means read it from disk."""
    got = subprocess.run(
        ["git", "-C", str(root), "ls-files"], capture_output=True, text=True
    )
    if got.returncode != 0:
        raise CannotCheck(f"{root} is not a git repository, so there is nothing to check.")
    names = [n for n in got.stdout.split("\n") if n]
    if not names:
        raise CannotCheck(f"{root} has no tracked files. Checking it proves nothing.")
    return [(Path(n), None) for n in names]


def _files_staged(root: Path) -> list[tuple[Path, str | None]]:
    """The staged BLOBS, not the files on disk.

    Reading the working tree here was a real hole: stage a file, edit it, and
    git commits the dirty blob while the check reads the clean file.
    """
    got = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
    )
    if got.returncode != 0:
        raise CannotCheck("could not list the staged files")
    out: list[tuple[Path, str | None]] = []
    for name in (n for n in got.stdout.split("\n") if n):
        blob = subprocess.run(
            ["git", "-C", str(root), "show", f":{name}"], capture_output=True
        )
        if blob.returncode != 0:
            continue
        # A latin-1 file full of French accents is ordinary here. Replacing
        # the undecodable bytes still lets every ASCII needle match, where
        # skipping the file outright found nothing at all.
        out.append((Path(name), blob.stdout.decode("utf-8", errors="replace")))
    return out


def scan(
    root: Path,
    files: list[tuple[Path, str | None]],
    needles: list[Needle],
    exceptions: list[str] | None = None,
) -> list[str]:
    exceptions = [e for e in (exceptions or []) if e]
    hits: list[str] = []

    def record(hit: str, haystack: str) -> None:
        """`haystack` is the offending text, never the report line.

        Matching the report line was worse than useless: the value in it is
        redacted, so "!Jordan Tremblay" could never match and never worked,
        while "!README.md" matched the path in every report line for that file
        and silently exempted the whole file.
        """
        if not any(allowed in haystack.lower() for allowed in exceptions):
            hits.append(hit)

    for rel, staged in files:
        if _is_skipped(rel):
            continue

        # A file NAMED after someone leaks just as loudly as one containing
        # their name, and no amount of reading the contents ever finds it.
        flat_path = _flatten_path(str(rel))
        for needle in needles:
            if needle.search(str(rel)) or (
                not needle.is_regex and _flatten_path(needle.value) in flat_path
            ):
                record(f"{rel}  {needle.label} ({needle.redacted()}) in the path", str(rel))

        if staged is not None:
            text = staged
        else:
            path = root / rel
            if not path.is_file():
                continue
            try:
                text = path.read_bytes().decode("utf-8", errors="replace")
            except OSError:
                continue

        found_on_a_line = set()
        for number, line in enumerate(text.splitlines(), 1):
            for needle in needles:
                if needle.search(line):
                    found_on_a_line.add(needle.value)
                    record(f"{rel}:{number}  {needle.label} ({needle.redacted()})", line)

        flat = _flatten(text)
        for needle in needles:
            if needle.is_regex or needle.value in found_on_a_line:
                continue
            if _flatten(needle.value) in flat:
                record(f"{rel}  {needle.label} ({needle.redacted()}) split across lines", flat)
    return hits


def scan_commit_messages(root: Path, needles: list[Needle], limit: int = 500) -> list[str]:
    """The half no file scan reaches. A message cannot be edited in place."""
    got = subprocess.run(
        ["git", "-C", str(root), "log", f"-{limit}", "--format=%H%x1f%an <%ae>%x1f%B%x1e"],
        capture_output=True,
        text=True,
    )
    if got.returncode != 0:
        return []
    hits = []
    for entry in got.stdout.split("\x1e"):
        if not entry.strip():
            continue
        parts = entry.strip().split("\x1f")
        if len(parts) < 3:
            continue
        sha, author, body = parts[0][:9], parts[1], parts[2]
        # Report every needle in a commit, not the first. Stopping at one
        # meant the author address masked every name in every message.
        for needle in needles:
            if needle.search(body) or needle.search(author):
                hits.append(f"commit {sha}  {needle.label} ({needle.redacted()})")
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", default=str(REPO_ROOT), help="tree to scan")
    parser.add_argument("--staged", action="store_true", help="the staged blobs")
    parser.add_argument(
        "--history", action="store_true", help="also scan commit messages and authors"
    )
    parser.add_argument(
        "--certify",
        action="store_true",
        help="this answer gates a publication, so a half-armed guard is a "
        "failure rather than a warning",
    )
    parser.add_argument(
        "--message-file",
        help="scan one piece of prose being written, which is what commit-msg runs",
    )
    parser.add_argument(
        "--label",
        default="commit message",
        help="what that prose IS, so the refusal names the right thing. A "
        "field report told to reword its commit message sends people looking "
        "for a commit that does not exist.",
    )
    args = parser.parse_args(argv)

    if args.message_file:
        try:
            needles = collect_needles(REPO_ROOT)
            exceptions = collect_exceptions(REPO_ROOT)
        except CannotCheck as exc:  # noqa: F841 - re-raised below with context
            print(f"check-private: CANNOT CHECK. {exc}", file=sys.stderr)
            return EXIT_CANNOT_CHECK
        from_patterns = any(n.label.startswith(".private-patterns") for n in needles)
        if not needles or not from_patterns:
            # .private-patterns is the ONLY source that holds a person's legal
            # name, their short username and their student number. .env holds
            # credentials, and config.yaml contributes teachers and an SSID.
            # On a fresh install the example file is comments only and .env is
            # deliberately empty, so this branch is the normal state, not a
            # rare one.
            print(
                "check-private: WARNING, the things no config file can know "
                "(your name, your username, your student number, your machine) "
                "are NOT being checked, because .private-patterns is missing or "
                "empty. Fill it in from the .example beside it.",
                file=sys.stderr,
            )
            if args.certify:
                # A commit message stays a warning, because refusing every
                # commit on a fresh clone just teaches people --no-verify. But
                # this text is about to be published, and a guard with nothing
                # to look for must never be the thing that says a public post
                # is clean.
                raise CannotCheck(
                    "refusing to certify this text while .private-patterns is "
                    "empty. It is the only place your own name and student "
                    "number are known, and without it nothing is checking for "
                    "them. Fill it in, then run this again."
                )
            if not needles:
                print(
                    "check-private: nothing private configured here, so there "
                    "is nothing to guard. Normal on a fresh clone."
                )
                return EXIT_CLEAN
        message = Path(args.message_file).read_text(encoding="utf-8", errors="replace")
        # Strip the comment lines git adds; they quote the diff and the branch.
        body = "\n".join(
            line for line in message.splitlines() if not line.startswith("#")
        )
        found = [
            f"{args.label}  {n.label} ({n.redacted()})"
            for n in needles
            if n.search(body)
            and not any(a in body.lower() for a in exceptions)
        ]
        if not found:
            print(f"check-private: {args.label} clean.")
            return EXIT_CLEAN
        print(f"check-private: the {args.label} names something personal.\n")
        for hit in found:
            print(f"  {hit}")
        if args.label == "commit message":
            print(
                "\nA commit message cannot be edited later without rewriting every\n"
                "SHA after it. Reword it now."
            )
        else:
            print(f"\nReword the {args.label} and try again.")
        return EXIT_HIT

    root = Path(args.path).resolve()
    try:
        if not root.is_dir():
            raise CannotCheck(f"{root} does not exist.")
        needles = collect_needles(REPO_ROOT)
        exceptions = collect_exceptions(REPO_ROOT)
        from_patterns = any(n.label.startswith(".private-patterns") for n in needles)

        if not needles:
            # A consumer's clone: no credentials, an unfilled config, and the
            # example patterns file which holds only comments. There is
            # genuinely nothing here to guard, and that is the normal state
            # rather than a failure. Returning 2 here made the commit hook
            # reject every commit they tried to make on their own copy.
            print(
                "check-private: nothing private configured here, so there is "
                "nothing to guard. Normal on a fresh clone."
            )
            return EXIT_CLEAN

        if not from_patterns:
            # .private-patterns is untracked by design, so it does not survive
            # a fresh clone or a new machine. Losing it silently drops the
            # needles that no config file can know: your name, your machine,
            # someone else's name on a paired device, your home coordinates.
            print(
                "check-private: WARNING, nothing came from .private-patterns, "
                "so the things no config file can know (your name, your "
                "machine, someone else's name on a paired device, your home "
                "coordinates) are NOT being checked. Fill in "
                ".private-patterns, from the .example beside it.",
                file=sys.stderr,
            )
            if args.certify:
                # Only a publication is blocked by this. Failing every manual
                # run and every commit instead would just teach people to pass
                # --no-verify, and a guard nobody runs guards nothing.
                raise CannotCheck(
                    "refusing to certify a release while .private-patterns is "
                    "missing. Restore it, then run this again."
                )

        files = _files_staged(root) if args.staged else _files_at_head(root)
    except CannotCheck as exc:
        print(f"check-private: CANNOT CHECK. {exc}", file=sys.stderr)
        return EXIT_CANNOT_CHECK

    hits = scan(root, files, needles, exceptions)
    if args.history:
        hits += scan_commit_messages(root, needles)

    scope = "staged changes" if args.staged else f"{len(files)} tracked file(s)"
    if not hits:
        print(f"check-private: clean. {len(needles)} needle(s) across {scope}.")
        return EXIT_CLEAN

    print(f"check-private: {len(hits)} hit(s) across {scope}.\n")
    for hit in hits:
        print(f"  {hit}")
    print(
        "\nEach line is one of YOUR values appearing in something that ships.\n"
        "Fix the file, or add an exception line to .private-patterns starting\n"
        "with ! and the text that should be allowed."
    )
    return EXIT_HIT


if __name__ == "__main__":
    sys.exit(main())
