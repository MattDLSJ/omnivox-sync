import pytest

from src.omnivox import OmnivoxDocument
from src.omnivox_sync import (
    DOWNLOAD,
    DOWNLOAD_REVISION,
    SKIP_EXISTING,
    SKIP_KNOWN,
    build_state_index,
    classify_document,
    download_key,
)


def _doc(course="601-101-MQ", filename="ch01.pptx", publish_date="2026-09-03"):
    return OmnivoxDocument(
        course_code=course, filename=filename, publish_date=publish_date, ref="r1"
    )


def _record(course="601-101-MQ", filename="ch01.pptx", publish_date="2026-09-03"):
    return {"course_code": course, "filename": filename, "publish_date": publish_date}


def test_download_key_is_the_spec_tuple():
    assert download_key("A", "f.pdf", "2026-09-03") == ("A", "f.pdf", "2026-09-03")


def test_unseen_document_with_empty_folder_is_downloaded(tmp_path):
    decision, dest = classify_document(_doc(), build_state_index([]), tmp_path)
    assert decision == DOWNLOAD
    assert dest == tmp_path / "ch01.pptx"


def test_known_tuple_is_skipped(tmp_path):
    decision, _ = classify_document(_doc(), build_state_index([_record()]), tmp_path)
    assert decision == SKIP_KNOWN


def test_known_tuple_is_skipped_even_if_file_was_deleted_from_disk(tmp_path):
    decision, _ = classify_document(_doc(), build_state_index([_record()]), tmp_path)
    assert decision == SKIP_KNOWN


def test_file_on_disk_with_no_state_record_is_skipped_as_state_loss(tmp_path):
    (tmp_path / "ch01.pptx").write_text("existing", encoding="utf-8")
    decision, dest = classify_document(_doc(), build_state_index([]), tmp_path)
    assert decision == SKIP_EXISTING
    assert dest == tmp_path / "ch01.pptx"
    assert dest.read_text(encoding="utf-8") == "existing"


def test_republished_file_is_downloaded_with_a_dated_suffix(tmp_path):
    (tmp_path / "ch01.pptx").write_text("old version", encoding="utf-8")
    index = build_state_index([_record(publish_date="2026-09-03")])
    decision, dest = classify_document(_doc(publish_date="2026-09-17"), index, tmp_path)
    assert decision == DOWNLOAD_REVISION
    assert dest == tmp_path / "ch01 (2026-09-17).pptx"


def test_revision_suffix_preserves_multi_dot_names(tmp_path):
    (tmp_path / "notes.v2.docx").write_text("old", encoding="utf-8")
    index = build_state_index([_record(filename="notes.v2.docx", publish_date="2026-09-03")])
    _, dest = classify_document(
        _doc(filename="notes.v2.docx", publish_date="2026-09-17"), index, tmp_path
    )
    assert dest.name == "notes.v2 (2026-09-17).docx"


def test_same_filename_in_two_courses_is_independent(tmp_path):
    index = build_state_index([_record(course="601-101-MQ")])
    decision, _ = classify_document(_doc(course="201-103-RE"), index, tmp_path)
    assert decision == DOWNLOAD


def test_missing_target_directory_is_treated_as_empty(tmp_path):
    decision, _ = classify_document(_doc(), build_state_index([]), tmp_path / "nope")
    assert decision == DOWNLOAD


def test_build_state_index_handles_legacy_records_missing_keys():
    index = build_state_index([{"course_code": "A"}, {}, _record()])
    assert ("601-101-MQ", "ch01.pptx", "2026-09-03") in index["tuples"]


def test_unicode_filenames_match_exactly(tmp_path):
    doc = _doc(filename="Écriture — résumé.pdf")
    index = build_state_index([_record(filename="Écriture — résumé.pdf")])
    decision, _ = classify_document(doc, index, tmp_path)
    assert decision == SKIP_KNOWN


@pytest.mark.parametrize("evil", ["../escape.pdf", "a/b.pdf", "/etc/passwd"])
def test_filenames_from_the_site_cannot_escape_the_course_folder(tmp_path, evil):
    decision, dest = classify_document(_doc(filename=evil), build_state_index([]), tmp_path)
    assert dest.parent == tmp_path
    assert ".." not in dest.parts
