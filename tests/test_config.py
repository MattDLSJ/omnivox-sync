from pathlib import Path

import pytest

from src.common import Config, ConfigError, load_config


def test_loads_valid_config(write_config, tmp_repo):
    cfg = load_config(write_config(), repo_root=tmp_repo)
    assert isinstance(cfg, Config)
    assert cfg.semester == "Automne 2026"
    assert len(cfg.courses) == 2
    assert cfg.courses[0].code == "601-101-MQ"
    assert cfg.courses[0].record is False
    assert cfg.notebooklm_mode == "auto"


def test_base_path_is_absolute_and_expanded(write_config, tmp_repo):
    cfg = load_config(write_config({"base_path": "~/Documents/School/X"}), repo_root=tmp_repo)
    assert cfg.base_path.is_absolute()
    assert "~" not in str(cfg.base_path)
    assert str(cfg.base_path).startswith(str(Path.home()))


def test_empty_courses_is_valid(write_config, tmp_repo):
    cfg = load_config(write_config({"courses": []}), repo_root=tmp_repo)
    assert cfg.courses == []


def test_missing_base_path_raises(write_config, tmp_repo):
    path = write_config()
    path.write_text("semester: X\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="base_path"):
        load_config(path, repo_root=tmp_repo)


def test_invalid_yaml_raises_with_filename(write_config, tmp_repo):
    path = write_config()
    path.write_text("courses: [oops\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(path, repo_root=tmp_repo)
    assert "config.yaml" in str(exc.value)


def test_missing_config_file_raises(tmp_repo):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_repo / "nope.yaml", repo_root=tmp_repo)


def test_course_missing_required_key_raises(write_config, tmp_repo):
    bad = [{"code": "X", "omnivox_name": "Y", "folder": "Z"}]
    with pytest.raises(ConfigError, match="notebook"):
        load_config(write_config({"courses": bad}), repo_root=tmp_repo)


def test_duplicate_course_code_raises(write_config, tmp_repo, sample_config_dict):
    dup = sample_config_dict["courses"] + [dict(sample_config_dict["courses"][0])]
    with pytest.raises(ConfigError, match="[Dd]uplicate"):
        load_config(write_config({"courses": dup}), repo_root=tmp_repo)


@pytest.mark.parametrize("folder", ["a/b", "..", ".", "/abs"])
def test_folder_traversal_raises(write_config, tmp_repo, sample_config_dict, folder):
    bad = [dict(sample_config_dict["courses"][0], folder=folder)]
    with pytest.raises(ConfigError, match="folder"):
        load_config(write_config({"courses": bad}), repo_root=tmp_repo)


@pytest.mark.parametrize(
    "key,value,needle",
    [
        ("digest", {"ranking": "magic"}, "ranking"),
        ("notebooklm", {"mode": "turbo"}, "mode"),
        ("at_school_check", "maybe", "at_school_check"),
    ],
)
def test_enum_values_validated(write_config, tmp_repo, key, value, needle):
    with pytest.raises(ConfigError, match=needle):
        load_config(write_config({key: value}), repo_root=tmp_repo)


# --- icon / finder / digest folder -------------------------------------------


def test_course_icon_is_parsed(write_config, tmp_repo, sample_config_dict):
    courses = [dict(sample_config_dict["courses"][0], icon="🏀")]
    cfg = load_config(write_config({"courses": courses}), repo_root=tmp_repo)
    assert cfg.courses[0].icon == "🏀"


def test_icon_defaults_to_empty(write_config, tmp_repo):
    assert load_config(write_config(), repo_root=tmp_repo).courses[0].icon == ""


def test_finder_tag_and_colour(write_config, tmp_repo):
    cfg = load_config(
        write_config({"finder": {"tag": "School", "color": "green"}}), repo_root=tmp_repo
    )
    assert cfg.finder.tag == "School"
    assert cfg.finder.color == "green"


def test_finder_defaults_to_no_tag(write_config, tmp_repo):
    assert load_config(write_config(), repo_root=tmp_repo).finder.tag == ""


def test_digest_folder_nests_the_digest(write_config, tmp_repo):
    cfg = load_config(
        write_config({"digest": {"ranking": "rules", "folder": "Other"}}), repo_root=tmp_repo
    )
    assert cfg.digest_folder == "Other"
    assert cfg.digest_dir() == cfg.base_path / "Other"


def test_digest_folder_empty_means_base_path(write_config, tmp_repo):
    cfg = load_config(write_config(), repo_root=tmp_repo)
    assert cfg.digest_dir() == cfg.base_path


@pytest.mark.parametrize("bad", ["a/b", "..", "/abs"])
def test_digest_folder_cannot_escape_base_path(write_config, tmp_repo, bad):
    with pytest.raises(ConfigError, match="digest.folder"):
        load_config(write_config({"digest": {"folder": bad}}), repo_root=tmp_repo)


# --------------------------------------------------------------------------
# The ntfy topic is a secret, so it lives in .env and not in config.yaml
# --------------------------------------------------------------------------


def test_ntfy_topic_comes_from_the_env_file(write_config, write_env, tmp_repo):
    """config.yaml is tracked by git; an ntfy topic is a bearer secret.

    Anyone who knows the topic name can read every notification the project
    sends and push their own, so it must never sit in a committed file.
    """
    write_env(extra="NTFY_TOPIC=school-abc123\n")
    cfg = load_config(write_config(), repo_root=tmp_repo)
    assert cfg.notify.ntfy_topic == "school-abc123"


def test_no_env_file_means_no_phone_push(write_config, tmp_repo):
    """A missing .env disables push instead of blowing up the whole config."""
    assert not (tmp_repo / ".env").exists()
    cfg = load_config(write_config(), repo_root=tmp_repo)
    assert cfg.notify.ntfy_topic == ""


def test_a_topic_left_in_config_yaml_still_works(write_config, tmp_repo):
    """Backwards compatible: an existing config.yaml keeps working."""
    cfg = load_config(
        write_config({"notify": {"macos": True, "ntfy_topic": "legacy-topic"}}),
        repo_root=tmp_repo,
    )
    assert cfg.notify.ntfy_topic == "legacy-topic"


def test_config_yaml_is_not_tracked_by_git():
    """config.yaml is one person's timetable, not source.

    It carries their courses, teachers, rooms, group numbers, the campus
    coordinates recording is gated on, and the path to their own folders. It
    was tracked once, which put all of that in every clone and in every commit
    of the history. config.example.yaml is the template; the real one stays on
    the machine that owns it.
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "config.yaml"],
        capture_output=True,
        text=True,
    )
    assert tracked.returncode != 0, "config.yaml is tracked again; git rm --cached it"


@pytest.mark.skipif(not Path("config.yaml").exists(), reason="no local config")
def test_the_example_config_covers_every_key_of_the_real_one():
    """Someone setting up from config.example.yaml must not silently lose a
    whole feature. The example drifted once and lost every recording and
    transcription key, so a reader following the README got a half-configured
    project with no error to tell them. Skipped where there is no local
    config to compare against, which is every machine but the author's."""
    import yaml

    real = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    example = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    assert set(real) == set(example)


def test_the_example_config_actually_loads(tmp_path):
    """And it must parse, not merely exist."""
    cfg = load_config(Path("config.example.yaml"), repo_root=tmp_path)
    assert cfg.semester
    assert cfg.notify.ntfy_topic == ""
