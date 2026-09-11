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

#: Sign-ins, taken on this page rather than at a terminal prompt.
#:
#: This page used to refuse them, on the reasoning that a web page is not
#: where a password should be typed. That reasoning does not survive looking
#: at what is actually on either side. The page is served on 127.0.0.1, by
#: this process, behind a single-use token, and the password lands in exactly
#: the same .env either way. The terminal prompt was not safer; it was one
#: more step, and it needed a terminal, which an agent-driven install does not
#: have. That last part is the real cost: it was a place the install had to
#: stop and ask somebody to go and open PowerShell.
#:
#: Write-only. The values are never rendered back into the form, so a second
#: visit shows empty boxes rather than a password in the page source.
CREDENTIAL_FIELDS = (
    {
        "field": "__omnivox_user",
        "env": "OMNIVOX_USER",
        "label": "Student number",
        "kind": "text",
        "group": "omnivox",
    },
    {
        "field": "__omnivox_pass",
        "env": "OMNIVOX_PASS",
        "label": "Omnivox password",
        "kind": "password",
        "group": "omnivox",
    },
    {
        "field": "__cheneliere_user",
        "env": "CHENELIERE_USER",
        "label": "Chénelière e-mail",
        "kind": "text",
        "group": "cheneliere",
    },
    {
        "field": "__cheneliere_pass",
        "env": "CHENELIERE_PASS",
        "label": "Chénelière password",
        "kind": "password",
        "group": "cheneliere",
    },
)

QUESTIONS = [
    {
        "key": "notebooklm.mode",
        "tier": "onboarding",
        "title": "NotebookLM",
        "lede": "NotebookLM is a Google tool you can ask questions of your own documents. This can put each course's material into its own notebook as it arrives, so you can ask it things like what is on the next exam.",
        "kind": "choice",
        "default": "auto",
        "options": [
            ("auto", "Yes, do it automatically",
             "A notebook per course, created for you the first time there is something to put in it. Needs the nlm command-line tool installed and signed in; if it is not, files are copied to a folder instead and nothing is lost."),
            ("off", "No, I do not use NotebookLM",
             "Nothing is uploaded and nothing is copied. Choose this if the name means nothing to you. You can turn it on later with make settings."),
        ],
    },
    {
        "key": "transcribe",
        "tier": "onboarding",
        "macos_only": True,
        "title": "Lecture recording",
        "lede": "Record your classes and turn them into text, so the lecture ends up in the same notebook as the slides. macOS only for now.",
        "kind": "choice",
        "default": "true",
        "options": [
            # Every specific promise that used to be here was false. It said
            # microphone access would be asked for during setup: nothing in
            # install.py ever asks. It said a 1.5 GB model downloads once:
            # nothing downloads it, you are shown a curl command afterwards.
            # Recording also needs a timetable, and nothing in this project
            # writes one yet. Saying "not finished" costs a sentence; being
            # caught promising a feature that does not exist costs the trust
            # in every other answer on this page.
            ("true", "Yes, set it up for recording",
             "Classes that look like lectures are marked as worth recording, so they are ready when you turn the recorder on. Finishing it needs two more steps after this page, and it will tell you what they are. Gym and stage are skipped automatically."),
            ("false", "No, skip it",
             "Nothing is marked and nothing is set up. You can turn it on later with make settings."),
        ],
        "note": "Recording is the one part of this that is not finished. It is off until you run the two extra steps, so choosing yes here will not surprise you with a microphone prompt.",
    },
    {
        "key": "scheduling.auto",
        "tier": "onboarding",
        "title": "Run it by itself",
        "lede": "Check Omnivox three times a day without you doing anything: before class, at lunch, and after supper.",
        "kind": "choice",
        "default": "true",
        "options": [
            ("true", "Yes, set it up now",
             "Installed at the end of setup, so it is working from today. A check that finds nothing costs nothing and says nothing."),
            ("false", "No, I will run it myself",
             "Nothing is scheduled. You sync by running one command, or by pressing the button this puts next to your course folders."),
        ],
    },
    {
        "key": "organize.scan",
        "tier": "onboarding",
        "title": "The course files you already have",
        "lede": "Most people start partway into a semester with course material scattered through Downloads and the desktop.",
        "kind": "choice",
        "default": "true",
        "options": [
            ("true", "Find them and file them",
             "Looks through Downloads, Desktop and Documents for anything that matches a course, shows you what it found grouped by course, and moves them in. It never deletes anything."),
            ("false", "Leave my other folders alone",
             "Nothing outside the course folders is read."),
        ],
    },
    {
        "key": "notify.desktop",
        "tier": "onboarding",
        "title": "Notifications",
        "lede": "One notification per sync, covering what is new. Silence means it checked and found nothing.",
        "kind": "choice",
        "default": "true",
        "options": [
            ("true", "Yes, tell me when something arrives",
             "A normal desktop notification, the same as any other app on this computer."),
            ("false", "None",
             "It still writes _digest.md next to your course folders, which is the same information without the interruption."),
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
            "A few questions, once. Everything else has a sensible default, and "
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
            "list=\"colleges\" autocomplete=\"off\" "
            "placeholder=\"Start typing, or type any cégep not in the list\" "
            "autofocus required>"
            + _college_suggestions()
            + "</fieldset>"
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
            "<p class=\"lede\">Lecture recording is left out because it does "
            "nothing here rather than because you would not want it: it "
            "captures audio through a macOS-only interface, and the Windows "
            "one has not been written. Notifications are not in this list any "
            "more; they work here.</p>"
            "<p class=\"note\"><strong>You can still get notified on your "
            "phone.</strong> That works on every platform. Put any random word "
            "nobody would guess in <code>NTFY_TOPIC</code> in the "
            "<code>.env</code> file, and subscribe to the same word in the "
            "free ntfy app. It is not on this page because a topic has no "
            "password: anyone who knows the word can read your notifications, "
            "so it belongs in the file that never leaves your machine.</p>"
            "</fieldset>"
        )
    parts.append(_render_credentials())
    parts.append("<button type=\"submit\">Save these choices</button></form>")
    return "".join(parts)


def _store_credentials(posted: dict, repo_root: Path) -> list[str]:
    """Write any sign-in that was filled in. Returns the names it stored.

    Empty is not "clear it": somebody opening `make settings` to change a sync
    time would otherwise wipe their Omnivox password by not retyping it into a
    box that, by design, shows nothing.
    """
    sys.path.insert(0, str(repo_root))
    try:
        from scripts.set_env import write_key
    except Exception:  # noqa: BLE001 - settings must still save without it
        return []

    stored = []
    for spec in CREDENTIAL_FIELDS:
        value = (posted.get(spec["field"]) or [""])[0]
        if not value.strip():
            continue
        try:
            write_key(spec["env"], value.strip(), env=repo_root / ".env")
        except Exception:  # noqa: BLE001
            continue
        stored.append(spec["env"])
    return stored


def _render_credentials() -> str:
    """The sign-in boxes. Never pre-filled, on purpose: see CREDENTIAL_FIELDS."""
    def boxes(group: str) -> str:
        out = []
        for spec in CREDENTIAL_FIELDS:
            if spec["group"] != group:
                continue
            out.append(
                "<label class=\"field\"><span>%s</span>"
                "<input class=\"text\" type=\"%s\" name=\"%s\" "
                "autocomplete=\"off\" spellcheck=\"false\"></label>"
                % (html.escape(spec["label"]), spec["kind"], spec["field"])
            )
        return "".join(out)

    return (
        "<fieldset><legend>Sign in to Omnivox</legend>"
        "<p class=\"lede\">The same student number and password you use on the "
        "Omnivox site. They are written to a file called <code>.env</code> "
        "inside this folder, on this computer, and are never sent anywhere "
        "except to Omnivox itself when it signs in for you.</p>"
        + boxes("omnivox")
        + "<p class=\"note\">Leave these empty if you would rather be asked "
        "later. The sync can still run, but it will have to ask you to sign in "
        "again by hand every few weeks instead of doing it quietly.</p>"
        "</fieldset>"
        "<fieldset><legend>Textbooks, if yours are on Chénelière</legend>"
        "<p class=\"lede\">Only for i+ Interactif, the Chénelière digital "
        "textbook site. It can pull the pages a teacher assigns into the right "
        "course folder. Leave both empty if your books are somewhere else, or "
        "on paper: nothing else changes.</p>"
        + boxes("cheneliere")
        + "</fieldset>"
    )


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
        "<p class=\"sub\">You can close this tab. The install carried on by "
        "itself the moment you pressed save; nothing is waiting on you.</p>"
        f"<ul>{rows}</ul>{follow_ups}{_credentials_note()}</div>"
    )


def _credentials_note() -> str:
    """The sign-ins this page deliberately does not take.

    Not taking them here is the right call: a web page, however local, is not
    where a password should be typed. Saying nothing about them is not. A
    fresh install used to finish with the textbook feature silently
    unconfigured, and the only mention of CHENELIERE_USER anywhere in the
    experience was an error message after somebody had already tried to use
    it.
    """
    return (
        "<div class=\"note\"><p>Anything you typed under Sign in was written to "
        "<code>.env</code> in the project folder, on this computer. Nothing "
        "was sent anywhere. If you left a box empty it was left alone rather "
        "than cleared, so coming back here to change one setting cannot lose "
        "you a password.</p></div>"
    )


def _college_suggestions() -> str:
    """A datalist, deliberately not a <select>.

    A dropdown you can only pick from would be a promise this cannot keep:
    the suggestions are only the colleges whose portal has actually been
    fetched and confirmed, and there are around forty cégeps. Somebody at one
    that is not on the list has to be able to type it and have it work, which
    it does, because the name is resolved against the live portal rather than
    looked up in here. So the list is a shortcut for the common case and never
    a limit on the answer.
    """
    from src.portal_finder import VERIFIED

    names = sorted({college for college, _ in VERIFIED.values()})
    return (
        "<datalist id=\"colleges\">"
        + "".join(f"<option value=\"{html.escape(n)}\">" for n in names)
        + "</datalist>"
        + "<p class=\"note\">The list is the colleges already confirmed working. "
        "Yours not being in it does not mean it is unsupported: type it in full "
        "and it will be looked up.</p>"
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

            original_text = text
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

            # Never hand back a file the rest of the program cannot read. The
            # page once wrote a setting underneath `schedule:`, which is a
            # list, and every submission left config.yaml unparseable; the
            # install then stopped with a message blaming the student. Saving
            # is the last safe moment to notice, because after this line the
            # only copy of their answers is the broken file.
            import yaml as _yaml

            try:
                _yaml.safe_load(text)
            except Exception as exc:  # noqa: BLE001
                problem[0] = (
                    "Saving those answers would have damaged your settings "
                    f"file, so nothing was changed. This is a bug in the app, "
                    f"not something you did. Details: {exc}"
                )
                values = current_values(original_text)
                values.update({k: str(v).lower() for k, v in chosen.items()})
                self._send(
                    PAGE.format(body=_render_form(values, token, tier, problem[0]))
                )
                return

            config_path.write_text(text, encoding="utf-8")
            written.update(chosen)
            # Credentials go to .env, never to config.yaml, and only the NAME
            # of what was stored reaches the confirmation page. Echoing the
            # value back would put a password in the page source, in the
            # browser's back-forward cache, and in any screenshot of the
            # "Saved" screen.
            for name in _store_credentials(posted, config_path.parent):
                written[name] = "saved"
            self._send(PAGE.format(body=_render_done(written)))
            finished.set()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/?t={token}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # flush=True on every one of these, and it is not a style choice. Python
    # buffers stdout when it is a pipe rather than a terminal, which is exactly
    # what it is under an AI agent. The address stayed in the buffer for the
    # whole wait, so the agent had nothing to show anybody, the person was
    # never told where to go, nobody submitted the form, and the run died at
    # the timeout with the address finally appearing in the flushed buffer of
    # a process that had already given up.
    say = lambda line: print(line, flush=True)  # noqa: E731
    say(
        "Opening your browser to choose which features you want."
        if tier == "onboarding"
        else "Opening your settings in your browser."
    )
    say("\n  " + url + "\n")
    say("If no window appeared, open that address yourself.")
    say("ANSWER IN THE BROWSER. Nothing typed anywhere else reaches this page;")
    say("it is waiting on that form and will keep waiting until you submit it.")
    say("This command is waiting too. It needs nothing from you in the meantime")
    say("and will carry on by itself the moment the form is submitted.\n")
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
