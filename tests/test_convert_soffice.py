import shutil
import subprocess
from pathlib import Path

import pytest

from src.convert import ConversionError, convert_to_pdf, find_soffice

pytestmark = pytest.mark.soffice

soffice_missing = pytest.mark.skipif(
    shutil.which("soffice") is None
    and not Path("/Applications/LibreOffice.app/Contents/MacOS/soffice").exists(),
    reason="LibreOffice not installed",
)


@soffice_missing
def test_find_soffice_returns_an_executable_path():
    path = Path(find_soffice())
    assert path.exists()
    out = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=180)
    assert "LibreOffice" in out.stdout


@soffice_missing
def test_converts_a_real_presentation_to_pdf(tmp_path):
    src = tmp_path / "deck.fodp"
    src.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
        ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
        ' office:version="1.2"'
        ' office:mimetype="application/vnd.oasis.opendocument.presentation">'
        '<office:body><office:presentation><draw:page draw:name="p1">'
        "<draw:frame><draw:text-box><text:p>Chapitre 1</text:p></draw:text-box></draw:frame>"
        "</draw:page></office:presentation></office:body></office:document>",
        encoding="utf-8",
    )
    outdir = tmp_path / "out"
    outdir.mkdir()
    pdf = convert_to_pdf(src, outdir)
    assert pdf.exists() and pdf.suffix == ".pdf"
    assert pdf.read_bytes()[:5] == b"%PDF-"


@soffice_missing
def test_original_is_kept_beside_the_pdf(tmp_path):
    src = tmp_path / "notes.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    outdir = tmp_path / "out"
    outdir.mkdir()
    pdf = convert_to_pdf(src, outdir)
    assert src.exists(), "spec section 7: keep the original beside the PDF"
    assert pdf.exists()


@soffice_missing
def test_unicode_filename_survives(tmp_path):
    src = tmp_path / "Écriture — résumé n°3.csv"
    src.write_text("é,à\n1,2\n", encoding="utf-8")
    outdir = tmp_path / "out"
    outdir.mkdir()
    pdf = convert_to_pdf(src, outdir)
    assert pdf.exists()
    assert pdf.stem == src.stem


def test_missing_source_raises(tmp_path):
    with pytest.raises(ConversionError, match="not found"):
        convert_to_pdf(tmp_path / "ghost.pptx", tmp_path)


def test_zero_exit_but_no_output_still_raises(tmp_path, monkeypatch):
    """soffice often exits 0 while writing nothing. Never trust the return code."""
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"not really a pptx")
    outdir = tmp_path / "out"
    outdir.mkdir()
    monkeypatch.setattr("src.convert.find_soffice", lambda: "/usr/bin/true")
    with pytest.raises(ConversionError, match="produced no output"):
        convert_to_pdf(src, outdir)


def test_timeout_raises_conversion_error(tmp_path, monkeypatch):
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"x")
    monkeypatch.setattr("src.convert.find_soffice", lambda: "/bin/sleep")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr("src.convert.subprocess.run", fake_run)
    with pytest.raises(ConversionError, match="timed out"):
        convert_to_pdf(src, tmp_path, timeout=1)


def test_libreoffice_is_findable_on_windows():
    """The Windows installer does not put itself on PATH, so the fallback list
    is the only way soffice is ever found there."""
    from src.convert import _SOFFICE_FALLBACKS

    windows = [p for p in _SOFFICE_FALLBACKS if p.startswith("C:")]
    assert windows, "no Windows path in the fallback list"
    assert all(p.endswith("soffice.com") for p in windows), (
        "target soffice.com, not soffice.exe: the .exe returns before the "
        "conversion finishes and convert_to_pdf checks for the PDF immediately"
    )


def test_the_install_hint_matches_the_platform(monkeypatch):
    """Telling a Windows user to run brew is worse than saying nothing."""
    import src.convert as convert

    monkeypatch.setattr(convert.shutil, "which", lambda name: None)
    monkeypatch.setattr(convert.Path, "exists", lambda self: False)

    monkeypatch.setattr(convert.os, "name", "nt")
    with pytest.raises(convert.ConversionError, match="winget"):
        convert.find_soffice()

    monkeypatch.setattr(convert.os, "name", "posix")
    with pytest.raises(convert.ConversionError, match="brew"):
        convert.find_soffice()
