from datetime import datetime

import pytest
import yaml

from src.omnivox_sync import build_parser, main


class _ctx:
    """Minimal context manager wrapping a fake driver."""

    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self.driver

    def __exit__(self, *a):
        return False


def test_parser_accepts_every_spec_flag():
    args = build_parser().parse_args(["--dry-run", "--course", "601-101-MQ", "--headed"])
    assert args.dry_run is True
    assert args.course == "601-101-MQ"
    assert args.headed is True
    assert args.discover is False


def test_discover_is_mutually_exclusive_with_course():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--discover", "--course", "X"])


def test_missing_config_exits_2_and_notifies(tmp_repo, monkeypatch):
    notes = []
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append((a, k)))
    assert main(["--config", str(tmp_repo / "absent.yaml"), "--dry-run"]) == 2
    assert notes and notes[0][1].get("critical") is True


def test_missing_env_exits_2_and_notifies(write_config, tmp_repo, monkeypatch):
    notes = []
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append((a, k)))
    assert main(["--config", str(write_config()), "--dry-run"]) == 2
    assert any("env" in str(n).lower() or "OMNIVOX" in str(n) for n in notes)


def test_successful_dry_run_exits_0(
    write_config, write_env, monkeypatch, fake_driver, course_factory, doc_factory
):
    write_env()
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")],
        documents={"601-101-MQ": [doc_factory()]},
    )
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)
    assert main(["--config", str(write_config()), "--dry-run"]) == 0


def test_partial_failure_exits_1_and_notifies(
    write_config, write_env, monkeypatch, fake_driver, course_factory, doc_factory
):
    notes = []
    write_env()
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")],
        documents={"601-101-MQ": [doc_factory(filename="broken.pdf")]},
        fail_on={"broken.pdf"},
    )
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append((a, k)))
    assert main(["--config", str(write_config())]) == 1
    assert notes


def test_nothing_new_exits_0_and_stays_silent(
    write_config, write_env, monkeypatch, fake_driver, course_factory
):
    """Spec section 9: silence means 'checked, nothing new'."""
    notes = []
    write_env()
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")]
    )
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append((a, k)))
    assert main(["--config", str(write_config())]) == 0
    assert notes == []


def test_discover_prints_paste_ready_yaml(
    write_config, write_env, monkeypatch, capsys, fake_driver, course_factory
):
    write_env()
    monkeypatch.setattr(
        "src.omnivox_sync._open_session",
        lambda *a, **k: _ctx(fake_driver(courses=[course_factory()])),
    )
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)
    assert main(["--config", str(write_config()), "--discover"]) == 0
    out = capsys.readouterr().out
    assert "courses:" in out
    parsed = yaml.safe_load(out[out.index("courses:"):])
    assert parsed["courses"][0]["code"] == "601-101-MQ"


def test_login_failure_exits_2(write_config, write_env, monkeypatch):
    from src.omnivox import LoginError

    notes = []
    write_env()

    def boom(*a, **k):
        raise LoginError("bad password")

    monkeypatch.setattr("src.omnivox_sync._open_session", boom)
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append((a, k)))
    assert main(["--config", str(write_config())]) == 2
    assert notes[0][1].get("critical") is True


def test_login_flag_is_exclusive_with_discover_and_course():
    for other in (["--discover"], ["--course", "X"]):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--login"] + other)


def test_login_flag_parses():
    assert build_parser().parse_args(["--login"]).login is True


def test_login_bootstrap_never_touches_the_normal_session_path(
    write_config, write_env, monkeypatch
):
    """--login must use its own headed path, not _open_session."""
    write_env()
    monkeypatch.setattr(
        "src.omnivox_sync._open_session",
        lambda *a, **k: pytest.fail("--login must not go through _open_session"),
    )
    monkeypatch.setattr("src.omnivox_sync._bootstrap_login", lambda cfg, log: 0)
    assert main(["--config", str(write_config()), "--login"]) == 0


def test_mfa_required_puts_the_fix_where_a_phone_shows_it(
    write_config, write_env, monkeypatch
):
    """The alert used to be the whole exception text, and the instruction sat
    at character 257. A phone banner shows about 120, so what the person read
    was "Omnivox is asking for a 6-digit identity-validation code", which reads
    like information rather than "your automation is dead, do this".
    """
    from src.omnivox import MfaRequired

    notes = []
    write_env()

    def boom(*a, **k):
        raise MfaRequired("Omnivox wants a code; run --login and tick the box")

    monkeypatch.setattr("src.omnivox_sync._open_session", boom)
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append((a, k)))
    assert main(["--config", str(write_config())]) == 2

    title, body = notes[-1][0][0], notes[-1][0][1]
    assert "make login" in title, "the fix belongs in the title, not the body"
    assert "make login" in (title + " " + body)[:120]


def test_a_blocked_login_is_never_retried_against_omnivox(
    write_config, write_env, tmp_repo, monkeypatch
):
    """Each attempt asks Omnivox to e-mail another six-digit code.

    Three scheduled runs a day meant the real code arrived among a pile of
    unrequested ones, it looked like credential stuffing from the school's
    side, and the person found out from their inbox rather than from this.
    """
    from src.omnivox import MfaRequired

    write_env()
    config = write_config()
    opened = []

    def boom(*a, **k):
        opened.append(1)
        raise MfaRequired("Omnivox wants a code; run --login")

    monkeypatch.setattr("src.omnivox_sync._open_session", boom)
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)

    assert main(["--config", str(config)]) == 2
    assert len(opened) == 1, "the first run does attempt it"

    for _ in range(5):
        assert main(["--config", str(config)]) == 2
    assert len(opened) == 1, "no further run may touch Omnivox"


def test_the_alarm_is_loud_once_then_stops_shouting(
    write_config, write_env, tmp_repo, monkeypatch
):
    """Three identical alerts a day for a week is how an alert becomes
    wallpaper, which is why this went unnoticed for two days."""
    from src.omnivox import MfaRequired

    write_env()
    config = write_config()
    loud = []

    monkeypatch.setattr(
        "src.omnivox_sync._open_session",
        lambda *a, **k: (_ for _ in ()).throw(MfaRequired("code needed")),
    )
    monkeypatch.setattr(
        "src.omnivox_sync.notify",
        lambda *a, **k: loud.append(k.get("critical", False)),
    )

    main(["--config", str(config)])
    main(["--config", str(config)])
    main(["--config", str(config)])
    assert loud.count(True) == 1, f"one loud alert, got {loud}"


def test_a_button_press_always_gets_an_answer(
    write_config, write_env, tmp_repo, monkeypatch
):
    """Quiet on a schedule is right. Quiet when somebody just pressed the
    button is a broken button."""
    from src.omnivox import MfaRequired

    write_env()
    config = write_config()
    notes = []

    monkeypatch.setattr(
        "src.omnivox_sync._open_session",
        lambda *a, **k: (_ for _ in ()).throw(MfaRequired("code needed")),
    )
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append(a))

    main(["--config", str(config)])
    notes.clear()
    main(["--config", str(config), "--manual"])
    assert notes, "a manual run must say something"
    assert "make login" in str(notes)


def test_logging_in_clears_the_block(write_config, write_env, tmp_repo):
    """Otherwise the fix works and the tool still refuses to run."""
    from src.common import load_config
    from src.omnivox_sync import block_login, clear_login_block, login_block

    cfg = load_config(write_config(), repo_root=tmp_repo)
    block_login(cfg, "MfaRequired")
    assert login_block(cfg) is not None
    clear_login_block(cfg)
    assert login_block(cfg) is None


# --- offline retry -----------------------------------------------------------
# The case this exists for: 07:30 fires while the laptop is shut on the way to
# school. It should catch up the moment the internet returns, not at 12:15 —
# but it must not stack a queue of runs either.


def test_network_failure_arms_a_retry_instead_of_shouting(
    write_config, write_env, tmp_repo, monkeypatch
):
    from src.omnivox_sync import retry_due

    notes = []
    write_env()
    cfgpath = write_config()

    def offline(*a, **k):
        raise TimeoutError("Page.goto: Timeout 30000ms exceeded")

    monkeypatch.setattr("src.omnivox_sync._open_session", offline)
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append(a))
    code = main(["--config", str(cfgpath)])

    assert code == 3, "offline is its own exit code, not a hard failure"
    assert notes == [], "being offline is not worth a critical alert"

    from src.common import load_config

    assert retry_due(load_config(cfgpath, repo_root=tmp_repo)) is True


def test_a_real_failure_still_notifies(write_config, write_env, tmp_repo, monkeypatch):
    notes = []
    write_env()

    def broken(*a, **k):
        raise ValueError("selectors changed")

    monkeypatch.setattr("src.omnivox_sync._open_session", broken)
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append(a))
    assert main(["--config", str(write_config())]) == 2
    assert notes, "a genuine breakage must still shout"


def test_retry_is_a_no_op_when_nothing_is_pending(write_config, write_env, monkeypatch):
    write_env()
    monkeypatch.setattr(
        "src.omnivox_sync._open_session",
        lambda *a, **k: pytest.fail("must not open a session with no retry pending"),
    )
    assert main(["--config", str(write_config()), "--retry"]) == 0


def test_retry_waits_while_still_offline(write_config, write_env, tmp_repo, monkeypatch):
    from src.common import load_config
    from src.omnivox_sync import arm_retry

    write_env()
    cfgpath = write_config()
    arm_retry(load_config(cfgpath, repo_root=tmp_repo), "offline")
    monkeypatch.setattr("src.omnivox_sync.internet_up", lambda **k: False)
    monkeypatch.setattr(
        "src.omnivox_sync._open_session",
        lambda *a, **k: pytest.fail("must not try while still offline"),
    )
    assert main(["--config", str(cfgpath), "--retry"]) == 0


def test_retry_expires_at_the_next_window_so_nothing_stacks(
    write_config, tmp_repo, sample_config_dict
):
    """Offline all morning must not queue a pile of runs for when wifi returns."""
    from datetime import datetime, timedelta

    from src.common import load_config
    from src.omnivox_sync import arm_retry, retry_due

    cfg = load_config(write_config(), repo_root=tmp_repo)
    armed = datetime(2026, 9, 8, 7, 35)
    arm_retry(cfg, "offline", now=armed)
    assert retry_due(cfg, now=armed + timedelta(minutes=30)) is True
    assert retry_due(cfg, now=datetime(2026, 9, 8, 12, 20)) is False, "12:15 supersedes it"


def test_a_successful_sync_clears_a_pending_retry(
    write_config, write_env, tmp_repo, monkeypatch, fake_driver, course_factory
):
    from src.common import load_config
    from src.omnivox_sync import arm_retry, retry_due

    write_env()
    cfgpath = write_config()
    cfg = load_config(cfgpath, repo_root=tmp_repo)
    arm_retry(cfg, "offline")

    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")]
    )
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: _ctx(driver))
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)
    assert main(["--config", str(cfgpath)]) == 0
    assert retry_due(cfg) is False


# ---------------------------------------------------------------------------
# The run lock. Every entry point drives one Playwright profile, and a headless
# Chromium shares a profile with another process rather than refusing, so these
# assert the process-level guard that replaces the one Chromium does not give.
# ---------------------------------------------------------------------------


def test_parser_accepts_wait():
    assert build_parser().parse_args([]).wait == 0.0
    assert build_parser().parse_args(["--wait", "30"]).wait == 30.0


def test_wait_is_not_mutually_exclusive_with_the_mode_flags():
    args = build_parser().parse_args(["--retry", "--wait", "5"])
    assert args.retry is True and args.wait == 5.0


def test_a_busy_lock_skips_without_opening_a_browser(
    write_config, tmp_repo, write_env, monkeypatch
):
    """The whole point: the second run must not reach Playwright at all."""
    from src.common import run_lock

    write_env(user="1234567", password="hunter2")
    monkeypatch.setattr(
        "src.omnivox_sync._open_session",
        lambda *a, **k: pytest.fail("opened a browser while another run held the lock"),
    )
    with run_lock(tmp_repo / "state" / "omnivox.lock"):
        assert main(["--config", str(write_config()), "--dry-run"]) == 0


def test_a_busy_lock_does_not_page_him(write_config, tmp_repo, write_env, monkeypatch):
    """A skip is routine. Notifying would fire every time a retry tick met the
    12:15 sync, which is the normal case, not an incident."""
    from src.common import run_lock

    notes = []
    write_env(user="1234567", password="hunter2")
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: notes.append((a, k)))
    monkeypatch.setattr("src.omnivox_sync._open_session", lambda *a, **k: None)
    with run_lock(tmp_repo / "state" / "omnivox.lock"):
        main(["--config", str(write_config()), "--dry-run"])
    assert notes == []


def test_the_lock_is_released_after_a_normal_run(
    write_config, tmp_repo, write_env, monkeypatch, fake_driver
):
    """A run that wedged the lock would break every later run silently."""
    from src.common import run_lock

    write_env(user="1234567", password="hunter2")
    monkeypatch.setattr(
        "src.omnivox_sync._open_session", lambda *a, **k: _ctx(fake_driver())
    )
    main(["--config", str(write_config()), "--dry-run"])
    with run_lock(tmp_repo / "state" / "omnivox.lock"):  # must be free again
        pass


def test_the_lock_is_released_even_when_the_run_fails(
    write_config, tmp_repo, write_env, monkeypatch
):
    from src.common import run_lock

    def boom(*a, **k):
        raise RuntimeError("browser exploded")

    write_env(user="1234567", password="hunter2")
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)
    monkeypatch.setattr("src.omnivox_sync._open_session", boom)
    assert main(["--config", str(write_config()), "--dry-run"]) == 2
    with run_lock(tmp_repo / "state" / "omnivox.lock"):
        pass


# --- is it working? ----------------------------------------------------------


def test_doctor_is_quiet_and_exits_zero_when_healthy(write_config, tmp_repo, capsys):
    """Exit code, so a script can ask too, not only a person."""
    from src.omnivox_sync import main as sync_main

    (tmp_repo / ".env").write_text("OMNIVOX_USER=x\nOMNIVOX_PASS=y\n", encoding="utf-8")
    (tmp_repo / "state" / "omnivox-profile").mkdir(parents=True, exist_ok=True)
    (tmp_repo / "logs").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    (tmp_repo / "logs" / "omnivox_sync.log").write_text(
        f"{stamp},000 INFO    Sync finished: 0 downloaded\n", encoding="utf-8"
    )
    assert sync_main(["--config", str(write_config()), "--doctor"]) == 0
    assert "healthy" in capsys.readouterr().out


def test_doctor_reports_an_outage_that_predates_the_fix(write_config, tmp_repo, capsys):
    """Anyone who updates in the middle of an outage has no block file, because
    the version that broke never wrote one. The log still says what happened.
    """
    from src.omnivox_sync import main as sync_main

    (tmp_repo / ".env").write_text("OMNIVOX_USER=x\nOMNIVOX_PASS=y\n", encoding="utf-8")
    (tmp_repo / "state" / "omnivox-profile").mkdir(parents=True, exist_ok=True)
    (tmp_repo / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_repo / "logs" / "omnivox_sync.log").write_text(
        "2026-09-06 18:31:57,000 INFO    Sync finished: 0 downloaded\n"
        "2026-09-07 07:35:52,000 ERROR   src.omnivox.MfaRequired: code needed\n",
        encoding="utf-8",
    )
    assert sync_main(["--config", str(write_config()), "--doctor"]) == 1
    assert "make login" in capsys.readouterr().out


def test_a_stuck_sync_leaves_a_file_where_you_will_see_it(
    write_config, write_env, tmp_repo, monkeypatch
):
    """A notification can be missed, swiped, or tuned out after the third
    identical one. A file that appears in the folder you already use, and
    syncs to your phone with the rest of it, cannot be."""
    from src.omnivox import MfaRequired
    from src.common import load_config
    from src.omnivox_sync import ATTENTION_FILE

    write_env()
    config = write_config()
    monkeypatch.setattr(
        "src.omnivox_sync._open_session",
        lambda *a, **k: (_ for _ in ()).throw(MfaRequired("code needed")),
    )
    monkeypatch.setattr("src.omnivox_sync.notify", lambda *a, **k: None)
    main(["--config", str(config)])

    cfg = load_config(config, repo_root=tmp_repo)
    note = cfg.digest_dir() / ATTENTION_FILE
    assert note.exists(), "nothing appeared in the folder they actually open"
    body = note.read_text(encoding="utf-8")
    assert "make login" in body
    assert "appareil de confiance" in body, "the box people miss must be named"


def test_the_attention_file_deletes_itself_once_fixed(write_config, tmp_repo):
    from src.common import load_config
    from src.omnivox_sync import ATTENTION_FILE, block_login, clear_login_block

    cfg = load_config(write_config(), repo_root=tmp_repo)
    block_login(cfg, "MfaRequired")
    assert (cfg.digest_dir() / ATTENTION_FILE).exists()
    clear_login_block(cfg)
    assert not (cfg.digest_dir() / ATTENTION_FILE).exists()
