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


def test_onboarding_asks_three_questions_and_no_more():
    """The budget is three. Every question added here is one more thing
    between somebody and their first working sync, so this is a real limit
    rather than a note in a docstring."""
    assert len(questions_for("onboarding")) == 3


def test_onboarding_asks_only_what_cannot_be_guessed():
    keys = {q["key"] for q in questions_for("onboarding")}
    assert keys == {"notebooklm.mode", "transcribe", "notify.macos"}


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
