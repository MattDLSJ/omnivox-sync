import plistlib
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "launchd" / "com.school.sync.plist.template"


def _render(tmp_path):
    text = TEMPLATE.read_text(encoding="utf-8")
    rendered = text.replace("__REPO__", str(tmp_path)).replace(
        "__PYTHON__", str(tmp_path / ".venv" / "bin" / "python")
    )
    out = tmp_path / "com.school.sync.plist"
    out.write_text(rendered, encoding="utf-8")
    return out


def test_template_exists():
    assert TEMPLATE.exists()


def test_rendered_plist_is_valid(tmp_path):
    assert plistlib.loads(_render(tmp_path).read_bytes())["Label"] == "com.school.sync"


def test_no_placeholders_survive_rendering(tmp_path):
    text = _render(tmp_path).read_text(encoding="utf-8")
    assert "__REPO__" not in text and "__PYTHON__" not in text


def test_every_path_is_absolute(tmp_path):
    data = plistlib.loads(_render(tmp_path).read_bytes())
    assert data["ProgramArguments"][0].startswith("/")
    assert data["WorkingDirectory"].startswith("/")
    assert data["StandardOutPath"].startswith("/")
    assert data["StandardErrorPath"].startswith("/")


def test_runs_the_sync_module(tmp_path):
    data = plistlib.loads(_render(tmp_path).read_bytes())
    assert data["ProgramArguments"][1:] == ["-m", "src.omnivox_sync"]


def test_schedule_runs_every_day_not_just_weekdays(tmp_path):
    """Teachers post on weekends, and the sync only needs the internet — never
    the campus — so restricting it to weekdays just delayed things by two days."""
    data = plistlib.loads(_render(tmp_path).read_bytes())
    entries = data["StartCalendarInterval"]
    assert all("Weekday" not in e for e in entries), "no weekday restriction"
    assert {(e["Hour"], e["Minute"]) for e in entries} == {(7, 30), (12, 15), (18, 30)}


def test_logs_go_into_the_repo_logs_dir(tmp_path):
    data = plistlib.loads(_render(tmp_path).read_bytes())
    assert data["StandardOutPath"].endswith("/logs/launchd.out.log")
    assert data["StandardErrorPath"].endswith("/logs/launchd.err.log")


def test_homebrew_bin_is_on_path_so_soffice_resolves(tmp_path):
    data = plistlib.loads(_render(tmp_path).read_bytes())
    assert "/opt/homebrew/bin" in data["EnvironmentVariables"]["PATH"]


def test_does_not_run_at_load(tmp_path):
    assert plistlib.loads(_render(tmp_path).read_bytes())["RunAtLoad"] is False


def test_install_script_is_executable():
    script = REPO / "scripts" / "install_launchd.sh"
    assert script.exists()
    assert script.stat().st_mode & 0o111


def test_install_script_has_valid_bash_syntax():
    script = REPO / "scripts" / "install_launchd.sh"
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


# ---------------------------------------------------------------------------
# com.school.manual: the button's job. The whole point is that it is dormant
# until kickstarted, so the tests that matter are about what it must NOT have.
# ---------------------------------------------------------------------------

MANUAL_TEMPLATE = REPO / "launchd" / "com.school.manual.plist.template"


def _render_manual(tmp_path):
    text = MANUAL_TEMPLATE.read_text(encoding="utf-8")
    rendered = text.replace("__REPO__", str(tmp_path)).replace(
        "__PYTHON__", str(tmp_path / ".venv" / "bin" / "python")
    )
    out = tmp_path / "com.school.manual.plist"
    out.write_text(rendered, encoding="utf-8")
    return plistlib.loads(out.read_bytes())


def test_manual_template_is_valid(tmp_path):
    assert _render_manual(tmp_path)["Label"] == "com.school.manual"


def test_manual_is_never_scheduled(tmp_path):
    """The regression that would turn a button into a fourth cron job, doubling
    the Omnivox traffic and firing 'started' banners at random."""
    data = _render_manual(tmp_path)
    assert "StartCalendarInterval" not in data
    assert "StartInterval" not in data
    assert data["RunAtLoad"] is False
    assert "KeepAlive" not in data


def test_manual_passes_the_manual_flag(tmp_path):
    """Without --manual it is just an unscheduled duplicate of the sync job:
    it would arm retries and stay silent when nothing is new."""
    args = _render_manual(tmp_path)["ProgramArguments"]
    assert args[1:] == ["-m", "src.omnivox_sync", "--manual"]


def test_manual_logs_somewhere_of_its_own(tmp_path):
    """A hand press must not muddy the scheduled job's log."""
    manual = _render_manual(tmp_path)
    scheduled = plistlib.loads(_render(tmp_path).read_bytes())
    assert manual["StandardOutPath"] != scheduled["StandardOutPath"]
    assert manual["StandardErrorPath"] != scheduled["StandardErrorPath"]


def test_manual_paths_are_absolute_and_placeholder_free(tmp_path):
    data = _render_manual(tmp_path)
    assert data["ProgramArguments"][0].startswith("/")
    assert data["WorkingDirectory"].startswith("/")
    for key in ("StandardOutPath", "StandardErrorPath"):
        assert data[key].startswith("/") and "__" not in data[key]


def test_manual_has_the_same_hardened_path_as_the_scheduled_job(tmp_path):
    """launchd gives no shell profile, so PATH must be spelled out or soffice,
    ffmpeg and nlm vanish."""
    assert "/opt/homebrew/bin" in _render_manual(tmp_path)["EnvironmentVariables"]["PATH"]
