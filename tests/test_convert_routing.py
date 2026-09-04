from pathlib import Path

import pytest

from src.convert import CONVERTIBLE, NOTEBOOKLM_SUPPORTED, is_uploadable, needs_conversion


@pytest.mark.parametrize(
    "name",
    ["a.pdf", "a.docx", "a.txt", "a.md", "a.png", "a.jpg", "a.jpeg",
     "a.mp3", "a.wav", "a.epub", "A.PDF", "A.DocX"],
)
def test_supported_formats_are_not_converted(name):
    assert needs_conversion(Path(name)) is False
    assert is_uploadable(Path(name)) is True


@pytest.mark.parametrize("name", ["a.pptx", "a.ppt", "a.xlsx", "a.xls", "a.odp", "A.PPTX"])
def test_office_formats_are_converted(name):
    assert needs_conversion(Path(name)) is True


@pytest.mark.parametrize("name", ["a.odt", "a.ods", "a.rtf", "a.doc"])
def test_other_office_types_are_converted(name):
    assert needs_conversion(Path(name)) is True


@pytest.mark.parametrize("name", ["a.zip", "a.exe", "a.mov", "noextension"])
def test_unknown_non_office_is_neither_converted_nor_uploadable(name):
    assert needs_conversion(Path(name)) is False
    assert is_uploadable(Path(name)) is False


def test_supported_and_convertible_sets_do_not_overlap():
    assert NOTEBOOKLM_SUPPORTED.isdisjoint(CONVERTIBLE)


def test_routing_is_case_insensitive_and_handles_full_paths():
    assert needs_conversion(Path("/a/b/Chapitre 3 — résumé.PPTX")) is True
    assert is_uploadable(Path("/a/b/Chapitre 3 — résumé.PDF")) is True
