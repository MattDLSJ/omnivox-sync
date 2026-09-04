import json
from pathlib import Path

import pytest

from src.common import StateStore
from src.notebooklm_upload import (
    MAX_SOURCE_BYTES,
    Notebook,
    UploadError,
    build_upload_index,
    find_notebook,
    normalize_title,
    stage_file,
    upload_queue,
)

# Real titles observed in the user's account: note the leading spaces.
NBS = [
    Notebook(id="1", title=" L'entreprise - Cegep Winter 2026"),
    Notebook(id="2", title="Écriture et littérature - Cegep Winter 2026"),
]


# --- notebook resolution -----------------------------------------------------


def test_leading_space_in_title_still_matches():
    assert find_notebook(NBS, "L'entreprise - Cegep Winter 2026").id == "1"


def test_match_is_case_insensitive_and_whitespace_tolerant():
    assert find_notebook(NBS, "  écriture ET littérature -  Cegep Winter 2026 ").id == "2"


def test_unknown_notebook_returns_none():
    assert find_notebook(NBS, "Physique - Cegep Automne 2026") is None


def test_normalize_collapses_internal_whitespace():
    assert normalize_title("a   b\tc") == normalize_title("a b c")


def test_empty_notebook_list_returns_none():
    assert find_notebook([], "anything") is None


def test_build_upload_index_ignores_incomplete_records():
    idx = build_upload_index([{"course_code": "A"}, {}, {"course_code": "A", "filename": "f"}])
    assert idx == {("A", "f")}


# --- fakes -------------------------------------------------------------------


class FakeUploader:
    def __init__(self, notebooks=None, fail_on=None, list_raises=None):
        self._notebooks = notebooks if notebooks is not None else list(NBS)
        self._fail_on = fail_on or set()
        self._list_raises = list_raises
        self.added: list[tuple[str, str]] = []
        self.videos: list[tuple[str, str, str]] = []

    def list_notebooks(self):
        if self._list_raises:
            raise self._list_raises
        return list(self._notebooks)

    def add_file(self, notebook_id, path):
        if Path(path).name in self._fail_on:
            raise UploadError(f"simulated failure for {Path(path).name}")
        self.added.append((notebook_id, Path(path).name))

    def add_youtube(self, notebook_id, url, title=""):
        if url in self._fail_on:
            raise UploadError(f"simulated failure for {url}")
        self.videos.append((notebook_id, url, title))


@pytest.fixture
def queued(loaded_config):
    """Config with one real file queued for the first course."""
    cfg = loaded_config
    folder = cfg.folder_for(cfg.courses[0])
    folder.mkdir(parents=True, exist_ok=True)
    src = folder / "ch01.pdf"
    src.write_bytes(b"%PDF-1.4 hello")
    StateStore(cfg.repo_root / "state" / "upload_queue.json").write(
        [
            {
                "course_code": cfg.courses[0].code,
                "notebook": cfg.courses[0].notebook,
                "path": str(src),
                "filename": "ch01.pdf",
                "queued_at": "2026-09-03T07:30:00",
            }
        ]
    )
    return cfg, src


def _nbs_for(cfg):
    return [Notebook(id="nb1", title=cfg.courses[0].notebook)]


# --- queue drain -------------------------------------------------------------


def test_queued_file_is_uploaded_and_recorded(queued):
    cfg, src = queued
    up = FakeUploader(notebooks=_nbs_for(cfg))
    result = upload_queue(cfg, up)
    assert len(result.uploaded) == 1
    assert up.added == [("nb1", "ch01.pdf")]
    recs = StateStore(cfg.repo_root / "state" / "uploads.json").read()
    assert recs[0]["mode"] == "auto"
    assert recs[0]["notebook_id"] == "nb1"


def test_successful_upload_leaves_the_queue(queued):
    cfg, _ = queued
    upload_queue(cfg, FakeUploader(notebooks=_nbs_for(cfg)))
    assert StateStore(cfg.repo_root / "state" / "upload_queue.json").read() == []


def test_same_file_is_never_uploaded_twice(queued):
    cfg, src = queued
    upload_queue(cfg, FakeUploader(notebooks=_nbs_for(cfg)))
    # requeue the identical item
    StateStore(cfg.repo_root / "state" / "upload_queue.json").write(
        [{"course_code": cfg.courses[0].code, "notebook": cfg.courses[0].notebook,
          "path": str(src), "filename": "ch01.pdf"}]
    )
    up2 = FakeUploader(notebooks=_nbs_for(cfg))
    result = upload_queue(cfg, up2)
    assert up2.added == []
    assert len(result.already_done) == 1


def test_oversize_file_is_skipped_not_uploaded(queued, monkeypatch):
    cfg, src = queued
    monkeypatch.setattr(
        "src.notebooklm_upload.MAX_SOURCE_BYTES", 5
    )  # our file is bigger than 5 bytes
    up = FakeUploader(notebooks=_nbs_for(cfg))
    result = upload_queue(cfg, up)
    assert up.added == []
    assert len(result.skipped_oversize) == 1
    assert StateStore(cfg.repo_root / "state" / "upload_queue.json").read() == [], (
        "an oversize file will never fit; do not retry it forever"
    )


def test_missing_notebook_stages_the_file_and_continues(queued):
    cfg, src = queued
    up = FakeUploader(notebooks=[Notebook(id="x", title="Some other notebook")])
    result = upload_queue(cfg, up)
    assert up.added == []
    assert len(result.staged) == 1
    assert len(result.errors) == 1
    staged = cfg.folder_for(cfg.courses[0]) / "_to_upload" / "ch01.pdf"
    assert staged.exists()


def test_upload_failure_stages_that_file(queued):
    cfg, src = queued
    up = FakeUploader(notebooks=_nbs_for(cfg), fail_on={"ch01.pdf"})
    result = upload_queue(cfg, up)
    assert len(result.staged) == 1
    assert len(result.errors) == 1
    assert (cfg.folder_for(cfg.courses[0]) / "_to_upload" / "ch01.pdf").exists()


def test_notebooklm_outage_stages_everything_for_the_run(queued):
    cfg, _ = queued
    up = FakeUploader(list_raises=UploadError("google is down"))
    result = upload_queue(cfg, up)
    assert len(result.staged) == 1
    assert result.uploaded == []


def test_staging_mode_never_calls_the_uploader(write_config, tmp_repo):
    from src.common import load_config

    cfg = load_config(write_config({"notebooklm": {"mode": "staging"}}), repo_root=tmp_repo)
    folder = cfg.folder_for(cfg.courses[0])
    folder.mkdir(parents=True, exist_ok=True)
    src = folder / "a.pdf"
    src.write_bytes(b"x")
    StateStore(cfg.repo_root / "state" / "upload_queue.json").write(
        [{"course_code": cfg.courses[0].code, "notebook": cfg.courses[0].notebook,
          "path": str(src), "filename": "a.pdf"}]
    )

    class Explode:
        def list_notebooks(self):
            pytest.fail("staging mode must not touch NotebookLM")

        def add_file(self, *a):
            pytest.fail("staging mode must not upload")

    result = upload_queue(cfg, Explode())
    assert len(result.staged) == 1


def test_dry_run_writes_nothing(queued):
    cfg, _ = queued
    up = FakeUploader(notebooks=_nbs_for(cfg))
    result = upload_queue(cfg, up, dry_run=True)
    assert len(result.uploaded) == 1
    assert up.added == []
    assert StateStore(cfg.repo_root / "state" / "uploads.json").read() == []
    assert len(StateStore(cfg.repo_root / "state" / "upload_queue.json").read()) == 1
    assert not (cfg.folder_for(cfg.courses[0]) / "_to_upload").exists()


def test_missing_file_on_disk_is_an_error_not_a_crash(queued):
    cfg, src = queued
    src.unlink()
    result = upload_queue(cfg, FakeUploader(notebooks=_nbs_for(cfg)))
    assert len(result.errors) == 1
    assert "missing" in result.errors[0]["error"]


def test_failed_item_stays_queued_for_next_run(queued):
    """A file whose upload errored transiently must not be silently dropped."""
    cfg, src = queued
    src.unlink()  # error path that does NOT mark the key complete
    upload_queue(cfg, FakeUploader(notebooks=_nbs_for(cfg)))
    assert len(StateStore(cfg.repo_root / "state" / "upload_queue.json").read()) == 1


def test_empty_queue_is_a_no_op(loaded_config):
    result = upload_queue(loaded_config, FakeUploader())
    assert result.summary().startswith("0 uploaded")


def test_stage_file_copies_and_keeps_the_original(tmp_path):
    src = tmp_path / "course" / "f.pdf"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"data")
    staged = stage_file(src, tmp_path / "course")
    assert staged.exists() and src.exists(), "staging must not remove the filed copy"
    assert staged.read_bytes() == b"data"


def test_stage_file_is_idempotent(tmp_path):
    src = tmp_path / "c" / "f.pdf"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"data")
    a = stage_file(src, tmp_path / "c")
    b = stage_file(src, tmp_path / "c")
    assert a == b


def test_only_course_filter(queued):
    cfg, _ = queued
    up = FakeUploader(notebooks=_nbs_for(cfg))
    result = upload_queue(cfg, up, only_course="201-103-RE")
    assert result.uploaded == []
    assert up.added == []


# --- YouTube sources: LEA distributes videos, NotebookLM ingests them --------


@pytest.fixture
def queued_video(loaded_config):
    """A video queued for the first course. There is no file on disk: what the
    teacher posted is a URL, and NotebookLM takes URLs."""
    cfg = loaded_config
    StateStore(cfg.repo_root / "state" / "upload_queue.json").write([{
        "course_code": cfg.courses[0].code,
        "notebook": cfg.courses[0].notebook,
        "youtube": "https://www.youtube.com/watch?v=v9FF1qI5pFo",
        "filename": "Règles de bases - FIBA",
        "path": "",
        "queued_at": "2026-08-27T13:00:00",
    }])
    return cfg


def test_a_queued_video_is_added_to_the_notebook(queued_video):
    cfg = queued_video
    up = FakeUploader(notebooks=_nbs_for(cfg))
    result = upload_queue(cfg, up)

    assert up.videos == [
        ("nb1", "https://www.youtube.com/watch?v=v9FF1qI5pFo", "Règles de bases - FIBA")
    ]
    assert up.added == [], "a video must not be pushed through the file path"
    assert len(result.uploaded) == 1
    assert result.errors == []


def test_a_video_is_not_reported_as_a_missing_file(queued_video):
    """Its queue entry has no path. The file branch would call that a missing
    file and error on every run forever."""
    cfg = queued_video
    result = upload_queue(cfg, FakeUploader(notebooks=_nbs_for(cfg)))
    assert not any("missing" in e.get("error", "") for e in result.errors)


def test_a_video_is_uploaded_once(queued_video):
    cfg = queued_video
    upload_queue(cfg, FakeUploader(notebooks=_nbs_for(cfg)))
    up2 = FakeUploader(notebooks=_nbs_for(cfg))
    upload_queue(cfg, up2)
    assert up2.videos == [], "the video was added to the notebook twice"


def test_a_failing_video_is_reported_and_stays_queued(queued_video):
    cfg = queued_video
    up = FakeUploader(
        notebooks=_nbs_for(cfg),
        fail_on={"https://www.youtube.com/watch?v=v9FF1qI5pFo"},
    )
    result = upload_queue(cfg, up)
    assert result.errors and "simulated failure" in result.errors[0]["error"]
    assert result.uploaded == []


def test_a_video_with_no_matching_notebook_is_reported(queued_video):
    cfg = queued_video
    result = upload_queue(cfg, FakeUploader(notebooks=[]))
    assert any("notebook not found" in e.get("error", "") for e in result.errors)
    assert result.uploaded == []
