"""Making the scraped textbook pages readable.

`make books` pulls pages the way the i+ Interactif reader shows them, as PNGs,
and assembles a PDF. The PDF looks right and contains no text at all: measured
on six real chapters, `pdftotext` returned exactly zero characters from every
one. Which defeats the point of pulling the chapter, since the notebook and
the assistant are the things that were supposed to read it. Somebody asked an
assistant to quiz them from the psychology manual in their own course folder
and was told, correctly and uselessly, that the file had no text.
"""

from pathlib import Path

import pytest

from src import book_text


def test_a_page_of_images_is_not_mistaken_for_a_text_layer(tmp_path, monkeypatch):
    """The threshold is not zero on purpose: a scan often carries a stray
    character from a watermark, and one stray character is not a reason to
    skip OCR on a whole chapter."""
    monkeypatch.setattr(book_text, "extractable_text", lambda pdf: "  \n 4 \n")
    assert not book_text.has_text_layer(tmp_path / "chapter.pdf")


def test_a_real_text_layer_is_left_alone(tmp_path, monkeypatch):
    """A PDF that pdftotext can already read is readable by everything
    downstream, so OCR would be time spent to produce a worse copy."""
    monkeypatch.setattr(book_text, "extractable_text", lambda pdf: "mot " * 200)
    assert book_text.has_text_layer(tmp_path / "vian.pdf")
    assert book_text.ensure_text(tmp_path / "vian.pdf") is None


def test_no_ocr_engine_is_reported_and_skipped_not_fatal(tmp_path, monkeypatch, caplog):
    """Same contract as a missing LibreOffice: the chapter still downloads and
    a person can still read it. It just has no sidecar yet."""
    monkeypatch.setattr(book_text, "backend", lambda: "")
    monkeypatch.setattr(book_text, "extractable_text", lambda pdf: "")
    pdf = tmp_path / "chapter.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with caplog.at_level("INFO"):
        assert book_text.ensure_text(pdf) is None
    assert "OCR" in caplog.text
    assert not book_text.sidecar(pdf).exists()


def test_the_install_hint_matches_the_platform(monkeypatch):
    """Telling a Windows user to pip install a macOS framework is worse than
    saying nothing."""
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "darwin")
    assert "pyobjc" in book_text.install_hint()
    monkeypatch.setattr(_sys, "platform", "win32")
    hint = book_text.install_hint()
    assert "tesseract" in hint.lower() and "pyobjc" not in hint


def test_the_sidecar_sits_beside_the_pdf(tmp_path):
    assert book_text.sidecar(tmp_path / "Psycho - p2-22.pdf").name == "Psycho - p2-22.txt"


def test_an_existing_sidecar_is_not_rebuilt(tmp_path, monkeypatch):
    """OCR on a chapter takes the better part of a minute. Re-running the sync
    three times a day must not redo it."""
    pdf = tmp_path / "chapter.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    book_text.sidecar(pdf).write_text("already done", encoding="utf-8")
    monkeypatch.setattr(book_text, "ocr_pdf", lambda *a, **k: pytest.fail("re-OCR'd"))
    assert book_text.ensure_text(pdf) == book_text.sidecar(pdf)


@pytest.mark.skipif(book_text.backend() != "vision", reason="needs macOS Vision")
def test_vision_reads_french_with_its_accents(tmp_path):
    """The reason French is first in LANGUAGES. Recognised on a real page of
    the psychology manual: "apprendre quelque chose par coeur"."""
    assert book_text.LANGUAGES[0].startswith("fr")
