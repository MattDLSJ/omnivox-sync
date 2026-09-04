import logging
import logging.handlers
from pathlib import Path

import pytest

from src.common import ConfigError, NotifyConfig, load_credentials, notify, setup_logging


def test_load_credentials_reads_env(tmp_repo, write_env):
    write_env(user="1234567", password="hunter2")
    assert load_credentials(tmp_repo) == ("1234567", "hunter2")


def test_missing_env_file_raises(tmp_repo):
    with pytest.raises(ConfigError, match=r"\.env"):
        load_credentials(tmp_repo)


def test_blank_password_raises_naming_the_key(tmp_repo, write_env):
    write_env(user="1234567", password="")
    with pytest.raises(ConfigError, match="OMNIVOX_PASS"):
        load_credentials(tmp_repo)


def test_credentials_are_stripped(tmp_repo, write_env):
    write_env(user="  1234567  ", password=" hunter2 ")
    assert load_credentials(tmp_repo) == ("1234567", "hunter2")


def test_notify_invokes_osascript(monkeypatch):
    calls = []
    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: calls.append(a[0]))
    notify("Title", "Body", cfg=NotifyConfig(macos=True, ntfy_topic=""))
    assert calls and calls[0][0] == "osascript"
    assert "Body" in calls[0][-1] and "Title" in calls[0][-1]


def test_notify_escapes_double_quotes(monkeypatch):
    calls = []
    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: calls.append(a[0]))
    notify('He said "hi"', 'a "quoted" body', cfg=NotifyConfig(macos=True, ntfy_topic=""))
    assert '\\"' in calls[0][-1]


def test_notify_skips_osascript_when_macos_false(monkeypatch):
    calls = []
    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: calls.append(a))
    notify("T", "B", cfg=NotifyConfig(macos=False, ntfy_topic=""))
    assert calls == []


def test_notify_posts_to_ntfy_when_topic_set(monkeypatch):
    posted = {}

    def fake_urlopen(request, timeout=None):
        posted["url"] = request.full_url
        posted["body"] = request.data

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp()

    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr("src.common.urllib.request.urlopen", fake_urlopen)
    notify("T", "B", cfg=NotifyConfig(macos=False, ntfy_topic="my-topic"))
    import json as _json

    body = _json.loads(posted["body"])
    assert posted["url"] == "https://ntfy.sh/"
    assert body["topic"] == "my-topic"
    assert body["message"] == "B"
    assert body["title"] == "T"


def test_ntfy_preserves_emoji_and_accents_in_the_title(monkeypatch):
    """Regression: the title used to go through an ASCII header, so the digest's
    '⚠ 4 important · 15 annonces' arrived as '? 4 important ? 15 annonces'."""
    import json as _json

    posted = {}

    def fake_urlopen(request, timeout=None):
        posted["body"] = request.data

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr("src.common.urllib.request.urlopen", fake_urlopen)
    notify("⚠ 4 important · 15 annonces", "Évaluations terminales",
           cfg=NotifyConfig(macos=False, ntfy_topic="t"))
    body = _json.loads(posted["body"])
    assert body["title"] == "⚠ 4 important · 15 annonces"
    assert body["message"] == "Évaluations terminales"


def test_an_alert_stands_out_without_seizing_the_phone(monkeypatch):
    """Priority 4 sounds and stands out. Priority 5, which this used to send,
    bypasses do-not-disturb and vibrates in repeated bursts, which is what he
    asked to stop on 2026-09-02."""
    import json as _json

    posted = {}

    def fake_urlopen(request, timeout=None):
        posted["body"] = request.data

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr("src.common.urllib.request.urlopen", fake_urlopen)
    notify("T", "B", critical=True, cfg=NotifyConfig(macos=False, ntfy_topic="t"))
    assert _json.loads(posted["body"])["priority"] == 4


def test_notify_never_raises_when_osascript_explodes(monkeypatch):
    def boom(*a, **k):
        raise OSError("no osascript here")

    monkeypatch.setattr("src.common.subprocess.run", boom)
    notify("T", "B", cfg=NotifyConfig(macos=True, ntfy_topic=""))


def test_notify_never_raises_when_ntfy_explodes(monkeypatch):
    monkeypatch.setattr("src.common.subprocess.run", lambda *a, **k: None)
    monkeypatch.setattr(
        "src.common.urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")),
    )
    notify("T", "B", cfg=NotifyConfig(macos=False, ntfy_topic="t"))


def test_setup_logging_writes_to_logs_dir(tmp_repo):
    logger = setup_logging("unit_test_mod", tmp_repo)
    logger.info("hello from the test")
    for handler in logger.handlers:
        handler.flush()
    log_file = tmp_repo / "logs" / "unit_test_mod.log"
    assert log_file.exists()
    assert "hello from the test" in log_file.read_text(encoding="utf-8")


def test_setup_logging_is_idempotent(tmp_repo):
    a = setup_logging("idem_mod", tmp_repo)
    b = setup_logging("idem_mod", tmp_repo)
    assert a is b
    assert len(a.handlers) == 2


def test_setup_logging_rotates_at_1mb(tmp_repo):
    logger = setup_logging("rotate_check", tmp_repo)
    fh = next(
        h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    )
    assert fh.maxBytes == 1_048_576
    assert fh.backupCount == 3


def test_console_logging_goes_to_stdout_not_stderr(tmp_repo):
    """Under launchd, stderr is StandardErrorPath. Routine INFO lines there
    would drown out real failures in logs/launchd.err.log."""
    import sys

    logger = setup_logging("stream_check", tmp_repo)
    stream_handlers = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert stream_handlers, "expected a console handler"
    assert stream_handlers[0].stream is sys.stdout
