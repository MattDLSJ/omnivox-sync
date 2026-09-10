"""Pick which parts of this you actually want, in a browser, once.

Everybody who installs this wants a different subset. One tester does not
record lectures. Another does not use NotebookLM at all. A third is on Windows,
where the folder colours do nothing whatever you set them to. Until now the
only way to discover any of that was for the AI to ask, one question at a time,
in a chat window, and an agent working through a long setup brief will
reliably compress eight questions into two and guess the rest.

So it is a page. The same shape as `make login`: a browser window opens, the
person does the part only a person can do, the window says thank you, and the
program carries on. It shows every option at once, with what each one actually
costs, which is the thing a chat transcript is worst at.

    make setup

It writes only config.yaml, and only the keys on the form. Anything that needs
a credential lives in .env and is described here but never written from here:
a web page, however local, is not where secrets should be typed.

Bound to 127.0.0.1 on a port the OS picks, behind a single-use token. Nothing
else on the machine, and nothing on the network, can reach it.
"""

from __future__ import annotations

import html
import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

from src.config_edit import set_many, set_value

#: Long enough that somebody can read the descriptions and go and check
#: something, short enough that a forgotten window does not wedge a script.
TIMEOUT_S = 1800

#: Every question, and the config key it writes. `options` are
#: (value, label, detail). Keeping this as data rather than markup is what
#: lets the page and the writer never disagree about which keys exist.
#:
#: `tier` is the important field. "onboarding" questions are the ones whose
#: answer changes what happens on day one and cannot be guessed from anything
#: else; there are three, and three is the budget. Everything else is
#: "settings", reachable any time from `make settings`, because a wall of
#: choices in front of somebody who just wants their notes downloaded is how
#: you lose them before the first sync.
#: The college. Not a radio button like everything else: it is free text that
#: gets resolved against the live portal, so it is handled separately by
#: `_resolve_college` rather than through the option-matching in `_parse`.
COLLEGE_FIELD = "__college"

QUESTIONS = [
    {
        "key": "notebooklm.mode",
        "tier": "onboarding",
        "title": "NotebookLM",
        "lede": "New course documents can be pushed into a NotebookLM notebook per course, so you can ask questions of your own material.",
        "kind": "choice",
        "default": "staging",
        "options": [
            ("off", "I do not use NotebookLM",
             "Nothing is uploaded and nothing is copied. Choose this if the name means nothing to you; you can turn it on later."),
            ("staging", "Copy new files to a folder, I will drag them in",
             "Each course gets a _to_upload/ folder. You keep all the downloading and filing, and lose only the drag. Start here."),
            ("auto", "Upload automatically",
             "Needs a separate command-line tool installed, and a notebook created by hand per course, named exactly as in config.yaml. If the notebook is missing it silently stages instead."),
        ],
    },
    {
        "key": "transcribe",
        "tier": "onboarding",
        "macos_only": True,
        "title": "Lecture recording",
        "lede": "Record classes and transcribe them. This is the largest and most fragile part of the project, it is macOS only, and it needs a 1.5 GB speech model.",
        "kind": "choice",
        "default": "false",
        "options": [
            ("false", "No, skip it",
             "Recommended. It stays completely inert: no microphone access is ever requested and nothing runs."),
            ("true", "Yes, I want to set it up",
             "Recording still will not start until a class timetable is in config.yaml. Ask your AI to walk you through it once the rest works."),
        ],
    },
    {
        "key": "notify.macos",
        "tier": "onboarding",
        "macos_only": True,
        "title": "Notifications",
        "lede": "One notification per sync, covering what is new. Silence means it checked and found nothing.",
        "kind": "choice",
        "default": "true",
        "options": [
            ("true", "Desktop notifications", "macOS only. On Windows this does nothing either way."),
            ("false", "None", "It still writes _digest.md next to your course folders, which is the same information without the interruption."),
        ],
        "note": "For notifications on your phone instead, put a random topic name in NTFY_TOPIC in .env and subscribe to the same name in the ntfy app. Anyone who knows the name can read them, which is why it is not on this page.",
    },
    {
        "key": "digest.ranking",
        "tier": "settings",
        "title": "How the digest decides what matters",
        "lede": "The digest flags the important items first.",
        "kind": "choice",
        "default": "rules",
        "options": [
            ("rules", "Simple rules", "Looks for words like examen, remise and travail. No account, no cost, no network."),
            ("gemini", "Ask an AI to rank it", "Needs GEMINI_API_KEY in .env. Falls back to the rules whenever the key is missing or the call fails."),
        ],
    },
    {
        "key": "finder.color",
        "tier": "settings",
        "macos_only": True,
        "title": "Folder colours",
        "lede": "Course folders get a Finder tag and colour so they are findable at a glance.",
        "kind": "choice",
        "default": "green",
        "options": [
            ("green", "Green", "macOS only."),
            ("blue", "Blue", "macOS only."),
            ("purple", "Purple", "macOS only."),
            ("orange", "Orange", "macOS only."),
            ("none", "No colours", "Choose this on Windows, where it does nothing anyway and logs a warning per folder."),
        ],
    },
    {
        "key": "sync_times",
        "tier": "settings",
        "title": "When it checks",
        "lede": "The scheduled job wakes up, looks for anything new, and goes back to sleep. A check with nothing new costs nothing and says nothing.",
        "default": "07:30, 12:15, 18:30",
        "options": [
            ("07:30, 12:15, 18:30", "Three times a day",
             "Before class, at lunch, and after supper. The default, and what most people want."),
            ("07:30, 18:30", "Morning and evening",
             "Enough to never be surprised in class, without a midday interruption."),
            ("18:30", "Once, in the evening",
             "Everything for the day arrives in one batch. Quietest option."),
            ("07:00, 10:00, 12:15, 15:00, 18:30", "Five times a day",
             "For a semester where things get posted an hour before they are due."),
        ],
        "after": "Run `make install-launchd` again to put the new times into effect. Until you do, the old schedule is still what runs.",
    },
    {
        "key": "update.auto",
        "tier": "settings",
        "title": "Automatic updates",
        "lede": "This project is still changing. Fixes only reach you if you receive them.",
        "kind": "choice",
        "default": "true",
        "options": [
            ("true", "Keep it up to date by itself",
             "Recommended. It fast-forwards at the start of each scheduled sync, and refuses to touch anything if you have made your own changes."),
            ("false", "I will run make update myself", "Nothing is fetched without you asking."),
        ],
    },
]

_TRUE = {"true", "false"}


def _coerce(key: str, value: str):
    """The form speaks strings. config.yaml should not, for a boolean."""
    if value in _TRUE:
        return value == "true"
    return value


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Set up your school sync</title>
<style>
  :root{{ --paper:#f2f4f6; --card:#fff; --ink:#16181d; --muted:#5b636e;
          --rule:#d5dbe1; --accent:#1f5f8b; --accent-ink:#fff; --ok:#276661; }}
  @media (prefers-color-scheme: dark){{
    :root{{ --paper:#111418; --card:#181c22; --ink:#e6eaef; --muted:#98a2ae;
            --rule:#2a313a; --accent:#5b9dc9; --accent-ink:#0b0e11; --ok:#6fb8b1; }}
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--paper);color:var(--ink);
    font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}}
  .wrap{{max-width:720px;margin:0 auto;padding:48px 20px 80px}}
  h1{{font-size:30px;line-height:1.15;margin:0 0 10px;letter-spacing:-.02em}}
  .sub{{color:var(--muted);margin:0 0 40px;font-size:16.5px}}
  fieldset{{border:1px solid var(--rule);border-radius:10px;background:var(--card);
    margin:0 0 22px;padding:22px}}
  legend{{font-weight:650;font-size:18px;padding:0 8px;letter-spacing:-.01em}}
  .lede{{color:var(--muted);margin:0 0 16px;font-size:15px}}
  label.opt{{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;
    border:1px solid var(--rule);border-radius:8px;margin-bottom:8px;cursor:pointer}}
  label.opt:hover{{border-color:var(--accent)}}
  label.opt:has(input:checked){{border-color:var(--accent);
    box-shadow:inset 0 0 0 1px var(--accent)}}
  label.opt input{{margin-top:5px;flex:none;accent-color:var(--accent)}}
  .opt b{{display:block;font-weight:600;font-size:15.5px}}
  .opt span{{display:block;color:var(--muted);font-size:14px;line-height:1.5;margin-top:2px}}
  .note{{border-left:3px solid var(--ok);padding:10px 14px;margin-top:14px;
    color:var(--muted);font-size:14px;line-height:1.55}}
  .warn{{border-left:3px solid #b4531b;padding:10px 14px;margin:0 0 14px;
    color:var(--ink);font-size:14.5px;line-height:1.55}}
  input.text{{width:100%;padding:13px 14px;font-size:16px;border-radius:8px;
    border:1px solid var(--rule);background:var(--paper);color:var(--ink)}}
  input.text:focus{{outline:2px solid var(--accent);outline-offset:1px}}
  button{{background:var(--accent);color:var(--accent-ink);border:0;border-radius:8px;
    padding:15px 30px;font-size:16.5px;font-weight:650;cursor:pointer;width:100%}}
  button:hover{{filter:brightness(1.08)}}
  .done{{text-align:center;padding:80px 20px}}
  .done h1{{margin-bottom:14px}}
  code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.9em;
    background:var(--paper);padding:2px 6px;border-radius:4px;border:1px solid var(--rule)}}
  ul{{color:var(--muted);font-size:14.5px;text-align:left;display:inline-block;margin-top:6px}}
</style></head><body><div class="wrap">{body}</div></body></html>"""


def questions_for(tier: str, platform: str | None = None) -> list[dict]:
    """"onboarding" is the short list. Anything else means all of them.

    Questions that do nothing on this operating system are not asked. Lecture
    recording is macOS only and stays inert everywhere else; desktop
    notifications shell out to `osascript`, which does not exist off macOS, so
    on Windows both answers produce the same nothing. Asking somebody to
    choose between two identical outcomes, and telling them "macOS only" while
    doing it, is worse than not asking.
    """
    platform = platform or sys.platform
    chosen = (
        [q for q in QUESTIONS if q.get("tier") == "onboarding"]
        if tier == "onboarding"
        else list(QUESTIONS)
    )
    if platform != "darwin":
        chosen = [q for q in chosen if not q.get("macos_only")]
    return chosen


def _render_form(current: dict, token: str, tier: str, problem: str = "") -> str:
    onboarding = tier == "onboarding"
    parts = [
        "<h1>%s</h1>" % ("Set up your semester" if onboarding else "Settings"),
        "<p class=\"sub\">%s</p>" % (
            "Four questions, once. Everything else has a sensible default, and "
            "<code>make settings</code> opens the rest whenever you want them."
            if onboarding else
            "Change any of these at any time. Nothing here is permanent, and "
            "nothing here is a credential; those live in <code>.env</code>."
        ),
        f"<form method=\"post\" action=\"/save?t={html.escape(token)}\">",
    ]
    # `problem` means they just typed a college and it did not resolve, so the
    # field comes back regardless of what else suggests it is already known.
    if onboarding and (problem or not current.get("__portal_set")):
        parts.append(
            "<fieldset><legend>Your college</legend>"
            "<p class=\"lede\">Omnivox is one system that nearly every cégep in "
            "Quebec runs, and the only thing that differs is the address. Type "
            "the name of yours the way you would say it out loud, in full: "
            "several colleges share a first word.</p>"
            + (f"<p class=\"warn\">{html.escape(problem)}</p>" if problem else "")
            + f"<input class=\"text\" type=\"text\" name=\"{COLLEGE_FIELD}\" "
            "placeholder=\"e.g. Édouard-Montpetit, Ahuntsic, Vieux Montréal\" "
            "autofocus required>"
            "</fieldset>"
        )
    for question in questions_for(tier, sys.platform):
        chosen = str(current.get(question["key"], question["default"]))
        parts.append("<fieldset><legend>%s</legend>" % html.escape(question["title"]))
        parts.append("<p class=\"lede\">%s</p>" % html.escape(question["lede"]))
        for value, label, detail in question["options"]:
            checked = " checked" if value == chosen else ""
            parts.append(
                "<label class=\"opt\"><input type=\"radio\" name=\"%s\" value=\"%s\"%s>"
                "<span><b>%s</b><span>%s</span></span></label>"
                % (html.escape(question["key"]), html.escape(value), checked,
                   html.escape(label), html.escape(detail))
            )
        if question.get("note"):
            parts.append("<p class=\"note\">%s</p>" % html.escape(question["note"]))
        parts.append("</fieldset>")
    if sys.platform != "darwin":
        parts.append(
            "<fieldset><legend>Not on this operating system</legend>"
            "<p class=\"lede\">Two things are left out because they do nothing "
            "here rather than because you would not want them. Lecture "
            "recording is macOS only. Desktop notifications go through a macOS "
            "command that does not exist on Windows or Linux.</p>"
            "<p class=\"note\"><strong>You can still get notified on your "
            "phone.</strong> That works on every platform. Put any random word "
            "nobody would guess in <code>NTFY_TOPIC</code> in the "
            "<code>.env</code> file, and subscribe to the same word in the "
            "free ntfy app. It is not on this page because a topic has no "
            "password: anyone who knows the word can read your notifications, "
            "so it belongs in the file that never leaves your machine.</p>"
            "</fieldset>"
        )
    parts.append("<button type=\"submit\">Save these choices</button></form>")
    return "".join(parts)


def _render_done(written: dict) -> str:
    rows = "".join(
        "<li><code>%s</code> &rarr; <code>%s</code></li>"
        % (html.escape(k), html.escape(str(v)))
        for k, v in written.items()
    )
    # Some settings need a further step before they mean anything. Saying so
    # here is the only moment anybody is looking.
    follow_ups = "".join(
        "<p class=\"note\">%s</p>" % html.escape(q["after"])
        for q in QUESTIONS
        if q.get("after") and q["key"] in written
    )
    return (
        "<div class=\"done\"><h1>Saved.</h1>"
        "<p class=\"sub\">You can close this tab and go back to the terminal.</p>"
        f"<ul>{rows}</ul>{follow_ups}</div>"
    )


def _resolve_college(name: str, config_text: str):
    """(new config text, error). Resolves against the live portal, never guesses."""
    from src.portal_finder import find

    got = find(name)
    if not got:
        return None, (
            f"No Omnivox portal answered for \u201c{name}\u201d. That does not mean "
            "your college is unsupported: it means the address could not be "
            "worked out from the name. Try the fuller name, or sign in to "
            "Omnivox in another tab and copy what comes before .omnivox.ca in "
            "the address bar."
        )
    return set_value(config_text, ["school", "portal"], got.slug), ""


def current_values(config_text: str) -> dict:
    """What the form should start on, read from the config as it stands."""
    import yaml

    try:
        loaded = yaml.safe_load(config_text) or {}
    except Exception:  # a half-written config still deserves a working form
        return {}
    out = {}
    # Whether the college is already known, so a second visit does not ask for
    # it again. An empty `portal:` means the built-in default, so a stored
    # browser profile is the better evidence that somebody has been here.
    school = loaded.get("school") or {}
    if school.get("portal") or (Path(__file__).resolve().parents[1] / "state" / "omnivox-profile").exists():
        out["__portal_set"] = "yes"
    for question in QUESTIONS:
        node = loaded
        for part in question["key"].split("."):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node is None:
            continue
        out[question["key"]] = (
            "true" if node is True else "false" if node is False else str(node)
        )
    return out


def _parse(body: str) -> dict:
    """Only the keys the form declares, and only values it offered.

    Not paranoia about an attacker, who would need the single-use token: it
    stops a stale or hand-edited form from writing a value that load_config
    then rejects, which would leave somebody with a config that no longer
    loads and no idea why.
    """
    posted = urllib.parse.parse_qs(body)
    allowed = {q["key"]: {o[0] for o in q["options"]} for q in QUESTIONS}
    out = {}
    for key, values in posted.items():
        if key in allowed and values and values[0] in allowed[key]:
            out[key] = _coerce(key, values[0])
    return out


def serve(config_path: Path, *, tier: str = "onboarding", open_browser: bool = True,
          timeout_s: int = TIMEOUT_S) -> dict:
    """Run the page until it is submitted. Returns what was written."""
    config_path = Path(config_path)
    if not config_path.is_file():
        raise FileNotFoundError(
            f"No {config_path}. Copy config.example.yaml to config.yaml first."
        )

    token = secrets.token_urlsafe(16)
    written: dict = {}
    problem = [""]  # a list so the handler can write to it
    finished = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: A003 - silence the access log
            pass

        def _send(self, body: str, status: int = 200) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            # This page is only ever meaningful to the person sitting here.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _authorised(self) -> bool:
            query = urllib.parse.urlparse(self.path).query
            got = urllib.parse.parse_qs(query).get("t", [""])[0]
            # Constant time, because the token is the only thing standing
            # between this and anything else that can reach localhost.
            return secrets.compare_digest(got, token)

        def do_GET(self):  # noqa: N802
            if not self._authorised():
                self._send(PAGE.format(body="<h1>Not this link.</h1>"), 403)
                return
            text = config_path.read_text(encoding="utf-8")
            self._send(
                PAGE.format(body=_render_form(current_values(text), token, tier, problem[0]))
            )

        def do_POST(self):  # noqa: N802
            if not self._authorised():
                self._send(PAGE.format(body="<h1>Not this link.</h1>"), 403)
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            posted = urllib.parse.parse_qs(body)
            chosen = _parse(body)
            text = config_path.read_text(encoding="utf-8")

            college = (posted.get(COLLEGE_FIELD) or [""])[0].strip()
            if college:
                updated, error = _resolve_college(college, text)
                if error:
                    # Back to the form with the message, keeping the radio
                    # answers they already gave. Losing those to a typo in one
                    # field is how a form teaches people to dread it.
                    problem[0] = error
                    values = current_values(text)
                    values.update({k: str(v).lower() for k, v in chosen.items()})
                    self._send(PAGE.format(body=_render_form(values, token, tier, error)))
                    return
                text = updated
                written["school.portal"] = college

            if chosen:
                text = set_many(text, chosen)
            config_path.write_text(text, encoding="utf-8")
            written.update(chosen)
            self._send(PAGE.format(body=_render_done(written)))
            finished.set()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/?t={token}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(
        "Opening your browser to choose which features you want."
        if tier == "onboarding"
        else "Opening your settings in your browser."
    )
    print("\n  " + url + "\n")
    print("If no window appeared, open that address yourself.")
    print("ANSWER IN THE BROWSER. Nothing typed anywhere else reaches this page;")
    print("it is waiting on that form and will keep waiting until you submit it.\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - the printed URL is the fallback
            pass

    try:
        if not finished.wait(timeout_s):
            print("Nobody answered, so nothing was changed. Run `make setup` again.")
    finally:
        server.shutdown()
        server.server_close()
    return written


def main(argv: list[str] | None = None) -> int:
    import argparse

    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(repo_root / "config.yaml"))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--all", action="store_true", help="every setting, not just the first few"
    )
    parser.add_argument(
        "--timeout", type=int, default=TIMEOUT_S, help="seconds to wait for an answer"
    )
    args = parser.parse_args(argv)

    try:
        written = serve(
            Path(args.config),
            tier="settings" if args.all else "onboarding",
            open_browser=not args.no_browser,
            timeout_s=args.timeout,
        )
    except FileNotFoundError as exc:
        print(str(exc))
        return 2
    if written:
        print(f"Saved {len(written)} setting(s) to {args.config}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
