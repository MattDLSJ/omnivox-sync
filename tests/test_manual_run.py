"""`--manual` means a person pressed a button and is waiting.

Three things follow from that, and each is a behaviour the scheduled jobs
deliberately do NOT have, so every test here has a paired assertion that the
cron path is unchanged:

  1. It reports the outcome either way. Silence is right for 07:30 and wrong
     for someone watching the Dock.
  2. It never arms a retry. Pressing a button with no signal must not schedule
     a full unattended sync and NotebookLM upload for hours later. That is not
     hypothetical: on 2026-08-21 an armed retry from a failed 07:30 run fired
     at 11:21.
  3. It drains the upload queue even when the sweep found nothing, so files
     stranded by an earlier failed upload get another go.
"""

import pytest

from src.common import load_config, run_lock
from src.omnivox_sync import build_parser, main
from tests.test_cli import _ctx


def _offline(*a, **k):
    raise TimeoutError("Page.goto: Timeout 30000ms exceeded")


@pytest.fixture
def quiet_driver(fake_driver, course_factory):
    """A driver that knows both config courses and has no documents, so a sweep
    succeeds with nothing new. An empty driver would exit 1 instead."""
    return fake_driver(
        courses=[
            course_factory(),
            course_factory(code="201-103-RE", name="Calcul différentiel"),
        ]
    )


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


def test_parser_accepts_manual():
    assert build_parser().parse_args([]).manual is False
    assert build_parser().parse_args(["--manual"]).manual is True


@pytest.mark.parametrize("other", [["--dry-run"], ["--course", "601-101-MQ"], ["--wait", "5"]])
def test_manual_composes_with_the_other_flags(other):
    """It must not land in the mutually exclusive group by accident."""
    assert build_parser().parse_args(["--manual", *other]).manual is True


# ---------------------------------------------------------------------------
# One press, one attempt
# ---------------------------------------------------------------------------


def test_manual_offline_does_not_arm_a_retry(write_config, write_env, tmp_repo, monkeypatch):
    """The core requirement. A button press that fails must stay failed."""
    from src.omnivox_sync import retry_due

    write_env()
    cfgpath = write_config()
    monkeypatch.setattr("src.omnivox_sync._open_session", _offline)
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)

    assert main(["--config", str(cfgpath), "--manual"]) == 3
    assert retry_due(load_config(cfgpath, repo_root=tmp_repo)) is False
    assert not (tmp_repo / "state" / "retry_pending.json").exists()


def test_scheduled_offline_still_arms_a_retry(write_config, write_env, tmp_repo, monkeypatch):
    """Guards the recovery path the scheduled jobs depend on. Deleting it would
    silently break every morning the laptop is shut on the way to school."""
    from src.omnivox_sync import retry_due

    write_env()
    cfgpath = write_config()
    monkeypatch.setattr("src.omnivox_sync._open_session", _offline)
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)

    assert main(["--config", str(cfgpath)]) == 3
    assert retry_due(load_config(cfgpath, repo_root=tmp_repo)) is True


def test_manual_offline_says_so(write_config, write_env, monkeypatch):
    notes = []
    write_env()
    monkeypatch.setattr("src.omnivox_sync._open_session", _offline)
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append(a))
    main(["--config", str(write_config()), "--manual"])
    assert any("internet" in " ".join(str(x) for x in n).lower() for n in notes), notes


# ---------------------------------------------------------------------------
# It always reports back
# ---------------------------------------------------------------------------


def test_manual_reports_even_when_nothing_is_new(
    write_config, write_env, monkeypatch, quiet_driver
):
    notes = []
    write_env()
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(quiet_driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append(a))
    assert main(["--config", str(write_config()), "--manual"]) == 0
    assert any("Up to date" in " ".join(str(x) for x in n) for n in notes), notes


def test_a_scheduled_run_stays_silent_when_nothing_is_new(
    write_config, write_env, monkeypatch, quiet_driver
):
    """The paired assertion: cron must not gain a daily 'nothing new' banner."""
    notes = []
    write_env()
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(quiet_driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append(a))
    assert main(["--config", str(write_config())]) == 0
    assert notes == [], notes


def test_manual_announces_that_it_started(write_config, write_env, monkeypatch, quiet_driver):
    """A multi-minute job with no acknowledgement gets pressed again."""
    notes = []
    write_env()
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(quiet_driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append(a))
    main(["--config", str(write_config()), "--manual"])
    assert notes and "started" in " ".join(str(x) for x in notes[0]).lower(), notes


# ---------------------------------------------------------------------------
# It drains the queue even with nothing new
# ---------------------------------------------------------------------------


def test_manual_drains_a_stranded_queue(write_config, write_env, monkeypatch, quiet_driver):
    """An earlier upload failure leaves files queued. A plain sync that finds
    nothing new returns early and never retries them; a button press must."""
    calls = []
    write_env()
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(quiet_driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)
    monkeypatch.setattr(
        "src.omnivox_sync._run_upload", lambda *a, **k: calls.append(k.get("force"))
    )
    main(["--config", str(write_config()), "--manual"])
    assert calls == [True], "manual must force the upload drain"


def test_a_scheduled_run_does_not_force_the_drain(
    write_config, write_env, monkeypatch, quiet_driver
):
    calls = []
    write_env()
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(quiet_driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)
    monkeypatch.setattr(
        "src.omnivox_sync._run_upload", lambda *a, **k: calls.append(k.get("force"))
    )
    main(["--config", str(write_config())])
    assert calls == [False]


# ---------------------------------------------------------------------------
# Busy lock
# ---------------------------------------------------------------------------


def test_manual_on_a_busy_lock_says_so_and_exits_4(
    write_config, write_env, tmp_repo, monkeypatch
):
    notes = []
    write_env()
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append(a))
    monkeypatch.setattr(
        "src.omnivox_sync._open_session",
        lambda *a, **k: pytest.fail("must not open a browser while the lock is held"),
    )
    with run_lock(tmp_repo / "state" / "omnivox.lock"):
        assert main(["--config", str(write_config()), "--manual"]) == 4
    assert any("already running" in " ".join(str(x) for x in n).lower() for n in notes), notes


# ---------------------------------------------------------------------------
# The dry-run bug this increment also fixed
# ---------------------------------------------------------------------------


def test_dry_run_does_not_cancel_a_pending_retry(
    write_config, write_env, tmp_repo, monkeypatch, quiet_driver
):
    """`make dry-run` used to clear a pending recovery without having synced,
    so a failed morning sync was silently abandoned."""
    from src.omnivox_sync import arm_retry, retry_due

    write_env()
    cfgpath = write_config()
    cfg = load_config(cfgpath, repo_root=tmp_repo)
    arm_retry(cfg, "offline")

    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(quiet_driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)
    main(["--config", str(cfgpath), "--dry-run"])

    assert retry_due(cfg) is True, "a dry run must leave the pending retry alone"
