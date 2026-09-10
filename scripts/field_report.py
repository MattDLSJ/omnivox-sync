#!/usr/bin/env python3
"""Send back what you had to fix, in a shape somebody can actually merge.

This project is one person's semester that other people are now running on
other machines, at other colleges, in another language. Every one of those is
a case the author never hit, and the person who hits it is the only person who
can describe it. That description is worth more than the fix: a patch tells
you what changed, and the report tells you whether it generalises.

Three commands, and the order matters:

  make report        builds field-report.md, already filled with everything a
                     machine can know, and a handful of questions only you can
                     answer.
  make send-report   checks the finished report for YOUR OWN private data,
                     then opens it as an issue on the repository you cloned.
  make field-notes   the maintainer's side: pull every report into one file.

Why the privacy check is not optional here. A field report is the single most
dangerous file in this repo to publish: it quotes your terminal, which quotes
your paths, which contain your name, and it quotes errors, which contain your
student number often enough. It goes through the same guard as everything
else, and a hit stops the send rather than warning about it.

What does NOT go in a report, ever: your password, the contents of .env, or
your config.yaml. If a fix was only to your own config, that is not a fix,
that is setup, and there is nothing to send.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_private  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT = REPO_ROOT / "field-report.md"

#: A whole-repo diff pasted into an issue is unreadable and usually means the
#: reporter committed their own course folders by accident. Truncate loudly.
MAX_DIFF = 60_000

#: Every question in the template carries this. The send refuses while any
#: survive, because a report of nothing but auto-collected facts tells the
#: maintainer that something broke and nothing about what.
MARKER = "TODO:"

TEMPLATE = """# Field report

{facts}

## What I was trying to do

{marker} one sentence. "First setup", "a normal sync", "make discover".

## What went wrong

{marker} the command, and the error exactly as it appeared. Paste it, do not
summarise it. The stack trace is the useful part.

## What I changed to get past it

{marker} for each change: which file, and why that fixed it. If you only
edited config.yaml or .env, write "config only" here. That is setup, not a
fix, and it is not upstreamable.

## Does this belong upstream?

{marker} answer for each change, and say why. This is the question that
decides whether the fix ships to everyone, so guess out loud rather than
leaving it blank.

  - "Yes, any English-language portal hits this."
  - "Yes, this is every Windows install, not just mine."
  - "No, this is my own timetable."
  - "Not sure: it works, but I do not know why it was broken."

## Still broken

{marker} what you gave up on, worked around, or never got running. Write
"nothing" if it all works. A workaround left in place is worth reporting even
when it works, because it is somebody else's bug report next month.

{patch}
"""


def _git(*args: str, cwd: Path = REPO_ROOT, check: bool = True) -> str:
    got = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and got.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{got.stderr.strip()}")
    return got.stdout.strip()


def _release() -> str:
    """Just the identifier: a release tag, or the commit it is sitting on."""
    tag = _git("describe", "--tags", "--abbrev=0", "--match", "v*", check=False)
    return tag or _git("rev-parse", "--short", "HEAD", check=False) or "unknown"


def _version() -> str:
    release = _release()
    sha = _git("rev-parse", "--short", "HEAD", check=False)
    if release.startswith("v"):
        return f"{release} ({sha})"
    return f"{release}, not a published release"


def _base_ref() -> str:
    """What to diff against: the released state, not the reporter's own start."""
    for ref in ("origin/main", "origin/HEAD"):
        if _git("rev-parse", "--verify", "--quiet", ref, check=False):
            return ref
    tags = [t for t in _git("tag", "--list", "v*", check=False).split() if t[1:].isdigit()]
    if tags:
        return f"v{max(int(t[1:]) for t in tags)}"
    return "HEAD"


def _portal() -> str:
    """Which Omnivox this copy actually talks to, and in which language.

    The single most useful line in a report, because almost every surprise in
    this project is one college's portal differing from another's. Resolved
    the way the code resolves it rather than read raw out of config.yaml:
    `portal:` is empty for everyone who is on the built-in default, and a
    report saying "not set" answers the wrong question.
    """
    if not (REPO_ROOT / "config.yaml").is_file():
        return "no config.yaml yet"
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from src.common import build_portal, load_config

        cfg = load_config(REPO_ROOT / "config.yaml", repo_root=REPO_ROOT)
        portal = build_portal(cfg)
        labels = "overridden" if cfg.school.labels else "default French"
        return f"{portal.school} ({portal.home}), labels {labels}"
    except Exception as exc:  # a half-written config is itself worth reporting
        return f"config.yaml could not be read ({exc.__class__.__name__}: {exc})"


def _which_python() -> str:
    # sys.prefix, not sys.executable: .venv/bin/python is a SYMLINK to the
    # system interpreter, so resolving it lands outside the project and
    # reports every correct setup as wrong.
    venv = REPO_ROOT / ".venv"
    inside = Path(sys.prefix).resolve() == venv.resolve() if venv.exists() else False
    return "project venv" if inside else "NOT the project venv, worth reporting"


def _tests() -> str:
    got = subprocess.run(
        # No -q: pyproject already sets one, and a second suppresses the
        # summary line, which is the only line worth collecting here.
        [sys.executable, "-m", "pytest", "-m", "not live"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=900,
    )
    lines = [l for l in got.stdout.strip().splitlines() if l.strip()]
    return lines[-1] if lines else "pytest produced no output"


def _facts(run_tests: bool) -> str:
    rows = [
        ("Reported", date.today().isoformat()),
        ("Version", _version()),
        ("OS", f"{platform.platform()} ({platform.machine()})"),
        # The VERSION, never the path. sys.executable is
        # /Users/<their first name>/... on most machines, and this file is
        # about to become a public issue.
        ("Python", f"{platform.python_version()} ({_which_python()})"),
        ("College", _portal()),
    ]
    if run_tests:
        print("Running the offline tests, so the report says whether they pass...")
        rows.append(("Offline tests", _tests()))
    width = max(len(name) for name, _ in rows)
    body = "\n".join(f"{name.ljust(width)}  {value}" for name, value in rows)
    return f"```\n{body}\n```"


def _patch() -> str:
    base = _base_ref()
    diff = _git("diff", base, "--", ":!config.yaml", ":!.env", check=False)
    untracked = [
        line[3:]
        for line in _git("status", "--porcelain", check=False).splitlines()
        if line.startswith("??")
    ]
    if not diff and not untracked:
        return (
            "## Patch\n\nNothing differs from the released code, so whatever "
            "you did was configuration or environment rather than a code "
            "change. That is still worth reporting; the sections above are "
            "the whole report."
        )
    out = [f"## Patch\n\nEverything that differs from `{base}`, working tree included."]
    if untracked:
        out.append("\nNew files, listed but not included:\n")
        out.extend(f"  {name}" for name in untracked)
    if diff:
        if len(diff) > MAX_DIFF:
            diff = (
                diff[:MAX_DIFF]
                + f"\n\n... truncated at {MAX_DIFF} characters. A diff this large "
                "usually means something got committed by accident; check "
                "`git status` before sending."
            )
        out.append("\n```diff\n" + diff + "\n```")
    return "\n".join(out)


def _remote_slug() -> str:
    url = _git("remote", "get-url", "origin", check=False)
    if not url:
        raise SystemExit(
            "No `origin` remote, so there is nowhere to send this. The report "
            f"is written at {REPORT}; send that file to whoever gave you the "
            "repository."
        )
    return url.rstrip("/").removesuffix(".git").split("github.com", 1)[-1].lstrip(":/")


def build(force: bool, run_tests: bool) -> int:
    if REPORT.exists() and not force:
        filled = MARKER not in REPORT.read_text(encoding="utf-8")
        raise SystemExit(
            f"{REPORT.name} already exists and is "
            + ("filled in" if filled else "still unanswered")
            + ".\nSend it with `make send-report`, or start over with "
            "`make report FORCE=1`."
        )
    REPORT.write_text(
        TEMPLATE.format(facts=_facts(run_tests), patch=_patch(), marker=MARKER),
        encoding="utf-8",
    )
    print(
        f"\nWrote {REPORT.name}.\n\n"
        f"Every line starting with {MARKER} is a question for you. Answer all "
        "of them,\nthen:\n\n  make send-report\n\n"
        "Nothing is sent until you run that, and it checks the finished "
        "report for\nyour own private data before it does."
    )
    return 0


def send(dry_run: bool) -> int:
    if not REPORT.is_file():
        raise SystemExit("No field-report.md. Build one first with `make report`.")
    text = REPORT.read_text(encoding="utf-8")

    left = [
        f"  line {number}: {line.strip()[:70]}"
        for number, line in enumerate(text.splitlines(), 1)
        if MARKER in line
    ]
    if left:
        print(f"{len(left)} question(s) in the report are still unanswered:\n")
        print("\n".join(left[:12]))
        raise SystemExit(
            "\nA report of auto-collected facts says something broke and "
            "nothing about\nwhat. Answer them, then run this again."
        )

    verdict = check_private.main(
        ["--message-file", str(REPORT), "--label", "field report"]
    )
    if verdict == check_private.EXIT_HIT:
        raise SystemExit(
            "\nThe report names something of yours. It was NOT sent, and the "
            "file is\nstill there: edit it and run this again. Terminal output "
            "pasted into a\nreport is the usual culprit, because your paths "
            "have your name in them."
        )
    if verdict == check_private.EXIT_CANNOT_CHECK:
        raise SystemExit("\nCould not check the report, so it was not sent.")

    slug = _remote_slug()
    title = f"[field report] {platform.system()} / {_portal().split(' (')[0]} / {_release()}"
    if dry_run:
        print(f"\n[dry-run] would open an issue on {slug} titled:\n  {title}")
        return 0

    got = subprocess.run(
        ["gh", "issue", "create", "--repo", slug, "--title", title,
         "--body-file", str(REPORT), "--label", "field report"],
        capture_output=True,
        text=True,
    )
    if got.returncode != 0:
        # Retry without the label: it may not exist on a fork, and a missing
        # label is not a reason to lose the report.
        got = subprocess.run(
            ["gh", "issue", "create", "--repo", slug, "--title", title,
             "--body-file", str(REPORT)],
            capture_output=True,
            text=True,
        )
    if got.returncode != 0:
        print(got.stderr.strip(), file=sys.stderr)
        raise SystemExit(
            f"\nCould not open the issue. The report is checked and ready at\n"
            f"  {REPORT}\n\n"
            f"Send that file to whoever gave you the repository, or paste it "
            f"in by hand at\n  https://github.com/{slug}/issues/new"
        )
    print(f"\nSent.\n  {got.stdout.strip()}\n\nDelete {REPORT.name} once you are done with it.")
    return 0


def collect(out: Path) -> int:
    """Maintainer's side. One file with every report in it, to hand to an AI."""
    slug = _remote_slug()
    got = subprocess.run(
        ["gh", "issue", "list", "--repo", slug, "--state", "all", "--limit", "100",
         "--json", "number,title,body,createdAt,author,state"],
        capture_output=True,
        text=True,
    )
    if got.returncode != 0:
        print(got.stderr.strip(), file=sys.stderr)
        raise SystemExit("Could not list the issues.")
    issues = [i for i in json.loads(got.stdout) if i["title"].startswith("[field report]")]
    if not issues:
        print(f"No field reports on {slug} yet.")
        return 0
    parts = [
        f"# Field reports from {slug}\n",
        f"{len(issues)} report(s), collected {date.today().isoformat()}.\n",
    ]
    for issue in sorted(issues, key=lambda i: i["createdAt"]):
        parts.append(
            f"\n---\n\n# #{issue['number']} {issue['title']}\n\n"
            f"From {issue['author']['login']} on {issue['createdAt'][:10]} "
            f"({issue['state'].lower()})\n\n{issue['body']}\n"
        )
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"{len(issues)} report(s) written to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--send", action="store_true", help="check it, then open the issue")
    parser.add_argument("--collect", action="store_true", help="maintainer: gather every report")
    parser.add_argument("--out", default=str(REPO_ROOT / "build" / "field-reports.md"))
    parser.add_argument("--force", action="store_true", help="overwrite an existing report")
    parser.add_argument("--no-tests", action="store_true", help="skip the test run")
    parser.add_argument("--dry-run", action="store_true", help="stop before sending")
    args = parser.parse_args(argv)

    if args.collect:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        return collect(out)
    if args.send:
        return send(args.dry_run)
    return build(args.force, not args.no_tests)


if __name__ == "__main__":
    sys.exit(main())
