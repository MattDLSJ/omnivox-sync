"""The setup and settings pages.

A page instead of a conversation, because an agent working through a long
setup brief reliably compresses eight questions into two and guesses the rest,
and because "what does staging actually mean" is something you read rather
than something you are told once in a chat window.

The split matters as much as the page: three questions at install time, the
rest behind `make settings`. A wall of choices in front of somebody who just
wants their notes downloaded is how you lose them before the first sync.
"""

import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest
import yaml

from src.setup_page import (
    QUESTIONS,
    _parse,
    current_values,
    questions_for,
    serve,
)


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "semester: \"Automne 2026\"\n\n"
        "# Keep this comment.\n"
        "notify:\n  macos: true\n\n"
        "notebooklm:\n  mode: \"auto\"\n\n"
        "transcribe: true\n",
        encoding="utf-8",
    )
    return path


def test_every_onboarding_question_defaults_to_the_feature_being_on():
    """The rule that replaced the three-question budget.

    A setup that ends with "next steps and recommendations" is a setup where
    the next steps do not happen: the person is done, the window is closed,
    and the thing that was supposed to run three times a day never runs. So
    everything is configured here, and everything arrives switched on. Turning
    something off is one click; going and finding it afterwards is not.
    """
    off = {
        "notebooklm.mode": "off",
        "transcribe": "false",
        "schedule.auto": "false",
        "organize.scan": "false",
        "notify.macos": "false",
    }
    for question in questions_for("onboarding", "darwin"):
        assert question["default"] != off.get(question["key"]), (
            f'{question["key"]} arrives switched off'
        )


def test_onboarding_covers_everything_that_needs_deciding():
    keys = {q["key"] for q in questions_for("onboarding", "darwin")}
    assert keys == {
        "notebooklm.mode", "transcribe", "schedule.auto",
        "organize.scan", "notify.macos",
    }


def test_settings_shows_everything_including_the_onboarding_three():
    assert len(questions_for("settings")) == len(QUESTIONS)
    assert {q["key"] for q in questions_for("onboarding")} <= {
        q["key"] for q in questions_for("settings")
    }


def test_every_question_declares_a_tier():
    """A question with no tier silently vanishes from both pages."""
    missing = [q["key"] for q in QUESTIONS if q.get("tier") not in ("onboarding", "settings")]
    assert not missing, f"questions with no tier: {missing}"


def test_every_default_is_one_of_the_options_offered():
    """A default that is not on the form renders with nothing selected, and
    then a submit writes whatever happened to be first."""
    for q in QUESTIONS:
        assert q["default"] in {o[0] for o in q["options"]}, q["key"]


def test_the_form_starts_on_what_the_config_already_says(config):
    values = current_values(config.read_text(encoding="utf-8"))
    assert values["notebooklm.mode"] == "auto"
    assert values["transcribe"] == "true"


def test_a_half_written_config_still_produces_a_usable_form(tmp_path):
    """The moment somebody most needs this page is when their config is a
    mess, so unparseable YAML must not be the thing that stops it opening."""
    assert current_values("notify:\n  macos: [unclosed\n") == {}


def test_only_values_the_form_offered_are_accepted():
    """Not paranoia about an attacker, who would need the token. It stops a
    stale form from writing a value load_config then rejects, leaving somebody
    with a config that no longer loads and no idea why."""
    assert _parse("notebooklm.mode=nonsense") == {}
    assert _parse("made.up.key=true") == {}
    assert _parse("notebooklm.mode=off") == {"notebooklm.mode": "off"}


def test_booleans_arrive_as_booleans_not_strings():
    assert _parse("transcribe=false") == {"transcribe": False}


# ---------------------------------------------------------------------------
# The server itself
# ---------------------------------------------------------------------------


def _serve_in_background(config, tier="onboarding"):
    """Start serve() on a thread and return the URL it is listening on."""
    result = {}
    holder = {}

    def run():
        result["written"] = serve(
            config, tier=tier, open_browser=False, timeout_s=20
        )

    import src.setup_page as page

    captured = []

    # serve() prints the URL it is listening on; capture that rather than
    # reaching into the server object, so the test exercises the same thing a
    # person on a machine with no browser would read off their terminal.
    def capture(*args, **kwargs):
        captured.append(" ".join(str(a) for a in args))

    page.print = capture  # type: ignore[attr-defined]
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    for _ in range(200):
        url = next((c.strip() for c in captured if c.strip().startswith("http://")), "")
        if url:
            holder["url"] = url
            break
        threading.Event().wait(0.02)
    holder["thread"] = thread
    holder["result"] = result
    # Remove the shim entirely rather than restoring a copy of the builtin,
    # which would leave a stray module attribute behind for the next reader.
    holder["restore"] = lambda: page.__dict__.pop("print", None)
    return holder


def test_a_submitted_form_writes_the_config_and_keeps_its_comments(config):
    served = _serve_in_background(config)
    assert "url" in served, "the server never printed a URL"
    try:
        body = urllib.parse.urlencode(
            {"notebooklm.mode": "off", "transcribe": "false", "notify.macos": "false"}
        ).encode()
        save = served["url"].replace("/?t=", "/save?t=")
        urllib.request.urlopen(urllib.request.Request(save, data=body), timeout=10).read()
        served["thread"].join(timeout=10)
    finally:
        served["restore"]()

    written = config.read_text(encoding="utf-8")
    loaded = yaml.safe_load(written)
    assert loaded["notebooklm"]["mode"] == "off"
    assert loaded["transcribe"] is False
    assert loaded["notify"]["macos"] is False
    assert "# Keep this comment." in written
    assert loaded["semester"] == "Automne 2026"


def test_the_wrong_token_is_refused(config):
    """The token is the only thing between this and anything else that can
    reach localhost."""
    served = _serve_in_background(config)
    assert "url" in served
    try:
        bad = served["url"].split("?t=")[0] + "?t=guessed"
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(bad, timeout=10)
        assert exc.value.code == 403
        assert yaml.safe_load(config.read_text(encoding="utf-8"))["transcribe"] is True
    finally:
        served["restore"]()


def test_it_binds_to_localhost_only(config):
    """A settings page reachable from the network is a settings page somebody
    else can change."""
    served = _serve_in_background(config)
    assert "url" in served
    try:
        assert served["url"].startswith("http://127.0.0.1:")
    finally:
        served["restore"]()


def test_a_missing_config_is_a_clear_refusal_not_a_traceback(tmp_path):
    with pytest.raises(FileNotFoundError, match="config.example.yaml"):
        serve(tmp_path / "nope.yaml", open_browser=False, timeout_s=1)


# ---------------------------------------------------------------------------
# The college, asked on the page rather than at a terminal prompt
#
# It has to be known before the sign-in, because it decides which Omnivox to
# open. Asking it here takes the setup from three stops to two, and both of
# the remaining ones are browser windows: a terminal prompt is no more
# reachable than a browser from an agent's sandbox, so splitting them bought
# nothing.
# ---------------------------------------------------------------------------


def test_the_college_field_is_on_the_onboarding_form(config, monkeypatch):
    import src.setup_page as page

    monkeypatch.setattr(page, "current_values", lambda text: {})
    body = page._render_form({}, "tok", "onboarding")
    assert f'name="{page.COLLEGE_FIELD}"' in body


def test_the_college_field_is_gone_once_it_is_known():
    """A second visit must not ask again for something already answered."""
    import src.setup_page as page

    body = page._render_form({"__portal_set": "yes"}, "tok", "onboarding")
    assert f'name="{page.COLLEGE_FIELD}"' not in body


def test_the_settings_page_never_asks_for_the_college():
    """`make settings` is for changing things later, when it is long known."""
    import src.setup_page as page

    body = page._render_form({}, "tok", "settings")
    assert f'name="{page.COLLEGE_FIELD}"' not in body


def test_a_name_that_resolves_is_written_as_the_portal(monkeypatch):
    import src.setup_page as page
    from src.portal_finder import Match

    monkeypatch.setattr(
        "src.portal_finder.find", lambda name: Match("cvm", "Cégep du Vieux Montréal", "fr")
    )
    updated, error = page._resolve_college("Vieux Montréal", 'school:\n  portal: ""\n')
    assert error == ""
    assert yaml.safe_load(updated)["school"]["portal"] == "cvm"


def test_a_name_that_resolves_to_nothing_explains_itself(monkeypatch):
    """"It did not work" sends somebody to reinstall. Telling them the address
    could not be derived, and where to read it off, sends them to the answer."""
    import src.setup_page as page

    monkeypatch.setattr("src.portal_finder.find", lambda name: None)
    updated, error = page._resolve_college("Hogwarts", 'school:\n  portal: ""\n')
    assert updated is None
    assert "address bar" in error
    assert "unsupported" in error, "it must not read as a rejection of their college"


def test_a_wrong_college_does_not_lose_the_answers_already_given(config, monkeypatch):
    """Losing four answers to a typo in one field is how a form teaches people
    to dread it."""
    import src.setup_page as page

    monkeypatch.setattr("src.portal_finder.find", lambda name: None)
    served = _serve_in_background(config)
    assert "url" in served
    try:
        body = urllib.parse.urlencode(
            {page.COLLEGE_FIELD: "Nowhere", "notebooklm.mode": "off"}
        ).encode()
        save = served["url"].replace("/?t=", "/save?t=")
        back = urllib.request.urlopen(
            urllib.request.Request(save, data=body), timeout=10
        ).read().decode()
        assert 'value="off" checked' in back
        assert f'name="{page.COLLEGE_FIELD}"' in back
        assert yaml.safe_load(config.read_text(encoding="utf-8"))["notebooklm"]["mode"] == "auto", (
            "nothing may be written while the form is being re-shown"
        )
    finally:
        served["restore"]()


# ---------------------------------------------------------------------------
# Questions that do nothing here are not asked
#
# A tester on Windows was shown "Lecture recording (macOS only)" and
# "Notifications (macOS only)" and reasonably objected. The text was accurate
# and the questions were still wrong: both answers produce the same nothing
# there, so it was asking somebody to choose between two identical outcomes
# and telling them so in the same breath.
# ---------------------------------------------------------------------------


def test_macos_only_questions_are_not_asked_on_windows():
    keys = {q["key"] for q in questions_for("onboarding", "win32")}
    assert "transcribe" not in keys, "lecture recording is macOS only and inert elsewhere"
    assert "notify.macos" not in keys, "it shells out to osascript, which is not there"


def test_the_ones_that_do_work_are_still_asked_on_windows():
    keys = {q["key"] for q in questions_for("onboarding", "win32")}
    assert "notebooklm.mode" in keys


def test_macos_gets_the_ones_windows_cannot_use():
    keys = {q["key"] for q in questions_for("onboarding", "darwin")}
    assert {"transcribe", "notify.macos"} <= keys


def test_folder_colours_are_not_offered_off_macos():
    """Finder tags do nothing on Windows and log a warning per course folder."""
    keys = {q["key"] for q in questions_for("settings", "linux")}
    assert "finder.color" not in keys
    assert "sync_times" in keys, "the ones that work everywhere must survive the filter"


def test_windows_onboarding_is_shorter_than_macos():
    assert len(questions_for("onboarding", "win32")) < len(
        questions_for("onboarding", "darwin")
    )
