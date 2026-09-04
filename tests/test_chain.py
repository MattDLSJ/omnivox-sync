from src.common import StateStore
from src.omnivox_sync import SyncResult, chain_downstream


def _result():
    r = SyncResult()
    r.downloaded.append(
        {"course_code": "601-101-MQ", "filename": "ch01.pdf", "publish_date": "2026-09-03",
         "downloaded_at": "2026-09-03T07:30:00", "path": "/x/ch01.pdf", "converted_pdf": None}
    )
    r.upload_queue.append(
        {"course_code": "601-101-MQ", "notebook": "Écriture - Cegep Automne 2026",
         "path": "/x/ch01.pdf", "filename": "ch01.pdf"}
    )
    return r


def test_upload_queue_is_persisted(loaded_config):
    chain_downstream(loaded_config, _result())
    queued = StateStore(loaded_config.repo_root / "state" / "upload_queue.json").read()
    assert len(queued) == 1
    assert queued[0]["notebook"] == "Écriture - Cegep Automne 2026"
    assert "queued_at" in queued[0]


def test_queue_accumulates_across_runs(loaded_config):
    chain_downstream(loaded_config, _result())
    chain_downstream(loaded_config, _result())
    assert len(StateStore(loaded_config.repo_root / "state" / "upload_queue.json").read()) == 2


def test_dry_run_persists_nothing(loaded_config):
    chain_downstream(loaded_config, _result(), dry_run=True)
    assert StateStore(loaded_config.repo_root / "state" / "upload_queue.json").read() == []


def test_empty_result_writes_nothing(loaded_config):
    chain_downstream(loaded_config, SyncResult())
    assert StateStore(loaded_config.repo_root / "state" / "upload_queue.json").read() == []


def test_run_is_appended_to_the_digest_file(loaded_config):
    chain_downstream(loaded_config, _result())
    digest = loaded_config.base_path / "_digest.md"
    assert digest.exists()
    text = digest.read_text(encoding="utf-8")
    assert "ch01.pdf" in text
    assert "601-101-MQ" in text


def test_digest_dry_run_writes_nothing(loaded_config):
    chain_downstream(loaded_config, _result(), dry_run=True)
    assert not (loaded_config.base_path / "_digest.md").exists()


# --- M2 chaining -------------------------------------------------------------


def test_chain_invokes_m2_when_the_queue_is_non_empty(loaded_config, monkeypatch):
    calls = []

    class FakeResult:
        staged = []

        def summary(self):
            return "1 uploaded"

    monkeypatch.setattr(
        "src.notebooklm_upload.upload_queue",
        lambda cfg, uploader, **kw: calls.append(cfg) or FakeResult(),
    )
    monkeypatch.setattr("src.notebooklm_upload.NlmUploader", lambda *a, **k: object())
    chain_downstream(loaded_config, _result())
    assert len(calls) == 1


def test_chain_skips_m2_when_nothing_is_queued(loaded_config, monkeypatch):
    monkeypatch.setattr(
        "src.notebooklm_upload.upload_queue",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    r = SyncResult()
    r.downloaded.append(
        {"course_code": "601-101-MQ", "filename": "a.pdf", "publish_date": "2026-09-03",
         "downloaded_at": "x", "path": "/x/a.pdf", "converted_pdf": None}
    )
    chain_downstream(loaded_config, r)  # no upload_queue entries -> no M2 call


def test_m2_failure_does_not_break_the_sync(loaded_config, monkeypatch):
    """The queue is durable; an upload outage must not fail the whole run."""
    monkeypatch.setattr("src.notebooklm_upload.NlmUploader", lambda *a, **k: object())
    monkeypatch.setattr(
        "src.notebooklm_upload.upload_queue",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("google exploded")),
    )
    result = _result()
    chain_downstream(loaded_config, result)  # must not raise
    assert any(e["scope"] == "notebooklm" for e in result.errors)
    assert (loaded_config.base_path / "_digest.md").exists(), "digest still written"
