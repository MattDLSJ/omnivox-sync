"""One command for phone notifications, instead of inventing a secret by hand."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import setup_phone  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_phone, "REPO", tmp_path)
    sent = []
    monkeypatch.setattr(
        "src.common.notify",
        lambda title, message, **kw: sent.append(kw["cfg"].ntfy_topic),
    )
    return tmp_path, sent


def _topic(repo):
    lines = (repo / ".env").read_text(encoding="utf-8").splitlines()
    return next(l.split("=", 1)[1] for l in lines if l.startswith("NTFY_TOPIC="))


def test_a_long_random_topic_is_made_saved_and_tested(home):
    repo, sent = home
    assert setup_phone.main(["setup_phone"]) == 0
    topic = _topic(repo)
    assert topic.startswith("school-") and len(topic) >= 20
    assert sent == [topic], "the test notification goes to the topic just saved"


def test_running_it_again_keeps_the_topic_the_phone_subscribed_to(home):
    repo, _ = home
    setup_phone.main(["setup_phone"])
    first = _topic(repo)
    setup_phone.main(["setup_phone"])
    assert _topic(repo) == first


def test_new_replaces_it(home):
    repo, _ = home
    setup_phone.main(["setup_phone"])
    first = _topic(repo)
    setup_phone.main(["setup_phone", "--new"])
    assert _topic(repo) != first


def test_other_lines_in_env_survive(home):
    repo, _ = home
    (repo / ".env").write_text("OMNIVOX_USER=someone\n", encoding="utf-8")
    setup_phone.main(["setup_phone"])
    assert "OMNIVOX_USER=someone" in (repo / ".env").read_text(encoding="utf-8")
