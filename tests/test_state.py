import json

import pytest

from src.common import StateError, StateStore


def test_missing_file_reads_as_empty_list(tmp_repo):
    assert StateStore(tmp_repo / "state" / "downloads.json").read() == []


def test_write_then_read_roundtrip(tmp_repo):
    store = StateStore(tmp_repo / "state" / "downloads.json")
    records = [{"course_code": "A", "filename": "f.pdf", "publish_date": "2026-09-01"}]
    store.write(records)
    assert store.read() == records


def test_append_preserves_existing(tmp_repo):
    store = StateStore(tmp_repo / "state" / "downloads.json")
    store.append({"n": 1})
    store.append({"n": 2})
    assert [r["n"] for r in store.read()] == [1, 2]


def test_write_creates_parent_directory(tmp_repo):
    store = StateStore(tmp_repo / "state" / "nested" / "deep.json")
    store.write([{"ok": True}])
    assert store.path.exists()


def test_write_is_atomic_no_temp_file_left(tmp_repo):
    store = StateStore(tmp_repo / "state" / "downloads.json")
    store.write([{"a": 1}])
    leftovers = [p.name for p in (tmp_repo / "state").iterdir() if p.name != "downloads.json"]
    assert leftovers == []


def test_corrupt_file_raises_rather_than_resetting(tmp_repo):
    path = tmp_repo / "state" / "downloads.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError, match="downloads.json"):
        StateStore(path).read()


def test_non_list_content_raises(tmp_repo):
    path = tmp_repo / "state" / "downloads.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    with pytest.raises(StateError, match="list"):
        StateStore(path).read()


def test_unicode_survives_roundtrip(tmp_repo):
    store = StateStore(tmp_repo / "state" / "downloads.json")
    store.write([{"filename": "Écriture — résumé.pdf"}])
    assert store.read()[0]["filename"] == "Écriture — résumé.pdf"


def test_original_file_intact_if_serialization_fails(tmp_repo):
    store = StateStore(tmp_repo / "state" / "downloads.json")
    store.write([{"good": 1}])
    with pytest.raises(TypeError):
        store.write([{"bad": object()}])
    assert store.read() == [{"good": 1}]
