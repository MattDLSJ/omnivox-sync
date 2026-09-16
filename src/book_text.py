"""Make the scraped textbook pages readable as text.

`make books` pulls pages out of i+ Interactif the way the reader displays
them, which is as PNG images, and assembles them into a PDF. That PDF looks
right and is completely opaque: `pdftotext` returns zero characters from it.

Which quietly defeats the point. The whole reason a chapter is pulled into the
course folder is so the notebook and the assistant can use it, and neither can
read a picture of a page. Somebody asked their assistant to quiz them on the
psychology manual sitting in `3_Livres/` and was told, correctly and
uselessly, that the file contained no text.

So the pages get OCR'd once, on arrival, into a `.txt` beside the PDF. The PDF
stays exactly as it was: it is what you print and read, and the sidecar is
what software reads.

Two backends, picked automatically:

  Vision      Apple's own text recognition, through PyObjC. Accurate on
              French including accents and ligatures, needs no Homebrew, and
              costs about 10 MB. macOS only.
  tesseract   The portable one, if the binary is on PATH. Works anywhere.

Neither is installed by this project. A missing OCR engine is reported and
skipped, exactly like a missing LibreOffice: the chapter still downloads and
is still readable by a person, it simply has no sidecar until an engine
exists.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

#: Enough characters to call it a real text layer. A scanned page sometimes
#: carries a stray character or two from a watermark, and one stray character
#: is not a reason to skip OCR on a whole chapter.
TEXT_LAYER_MIN = 200

#: Rasterisation density. 200 is the knee of the curve here: 150 starts losing
#: accented capitals in body text, and 300 triples the time for no measurable
#: gain on a textbook page.
DPI = 200

#: French first: these are Quebec textbooks. English second, because the
#: psychology and management manuals quote English sources untranslated.
LANGUAGES = ("fr-FR", "en-US")
TESSERACT_LANGS = "fra+eng"


def extractable_text(pdf: Path) -> str:
    """What `pdftotext` can already get out. "" when it is a page of images."""
    if not shutil.which("pdftotext"):
        return ""
    try:
        got = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"],
            capture_output=True, text=True, timeout=120,
        )
    except Exception:  # noqa: BLE001
        return ""
    return got.stdout or ""


def has_text_layer(pdf: Path) -> bool:
    return len("".join(extractable_text(pdf).split())) >= TEXT_LAYER_MIN


def backend() -> str:
    """"vision", "tesseract", or "" when neither is available."""
    try:
        import Vision  # noqa: F401
        import Quartz  # noqa: F401

        return "vision"
    except Exception:  # noqa: BLE001
        pass
    return "tesseract" if shutil.which("tesseract") else ""


def install_hint() -> str:
    """What to tell somebody who has no OCR engine, for their platform."""
    import sys

    if sys.platform == "darwin":
        return (
            "No OCR engine, so the textbook pages stay as images.\n"
            "    .venv/bin/pip install pyobjc-framework-Vision\n"
            "gives you Apple's own, about 10 MB, no Homebrew needed."
        )
    return (
        "No OCR engine, so the textbook pages stay as images.\n"
        "    winget install UB-Mannheim.TesseractOCR   (Windows)\n"
        "    sudo apt install tesseract-ocr tesseract-ocr-fra   (Linux)"
    )


def _vision_page(image: Path) -> str:
    import Quartz
    import Vision
    from Foundation import NSURL

    source = Quartz.CGImageSourceCreateWithURL(
        NSURL.fileURLWithPath_(str(image)), None
    )
    if source is None:
        return ""
    cg = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if cg is None:
        return ""
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(0)          # 0 = accurate, 1 = fast
    request.setRecognitionLanguages_(list(LANGUAGES))
    request.setUsesLanguageCorrection_(True)
    handler.performRequests_error_([request], None)

    lines = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if candidates and len(candidates):
            lines.append(candidates[0].string())
    return "\n".join(lines)


def _tesseract_page(image: Path) -> str:
    got = subprocess.run(
        ["tesseract", str(image), "-", "-l", TESSERACT_LANGS],
        capture_output=True, text=True, timeout=300,
    )
    return got.stdout or ""


def ocr_pdf(pdf: Path, *, dpi: int = DPI, logger=None) -> str:
    """Every page of `pdf`, recognised. Raises nothing; returns "" on failure."""
    log = logger or logging.getLogger("school.books")
    engine = backend()
    if not engine:
        log.info("%s", install_hint())
        return ""
    if not shutil.which("pdftoppm"):
        log.info(
            "pdftoppm is missing, so the pages cannot be rasterised for OCR. "
            "It comes with poppler, the same package that provides pdftotext."
        )
        return ""

    read_page = _vision_page if engine == "vision" else _tesseract_page
    out: list[str] = []
    with tempfile.TemporaryDirectory(prefix="booktext-") as tmp:
        stem = Path(tmp) / "page"
        try:
            subprocess.run(
                ["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(stem)],
                check=True, capture_output=True, timeout=900,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not rasterise %s: %s", pdf.name, exc)
            return ""
        pages = sorted(Path(tmp).glob("page*.png"))
        for number, image in enumerate(pages, 1):
            try:
                text = read_page(image)
            except Exception as exc:  # noqa: BLE001 - one bad page, not the run
                log.warning("OCR failed on page %d of %s: %s", number, pdf.name, exc)
                continue
            if text.strip():
                # The marker is not decoration. A quiz or a citation needs to
                # be able to say which page a sentence came from, and after
                # OCR the printed page number is just another line of text.
                out.append(f"--- page {number} of {len(pages)} ---\n{text.strip()}")
    return "\n\n".join(out)


def sidecar(pdf: Path) -> Path:
    return pdf.with_suffix(".txt")


def ensure_text(pdf: Path, *, force: bool = False, logger=None) -> Path | None:
    """Write the `.txt` beside `pdf` if it is needed and possible.

    Returns the sidecar, or None when there was nothing to do. Already having
    a text layer counts as nothing to do: a PDF that `pdftotext` can read is
    already readable by everything downstream.
    """
    log = logger or logging.getLogger("school.books")
    target = sidecar(pdf)
    if target.exists() and not force:
        return target
    if has_text_layer(pdf):
        return None
    text = ocr_pdf(pdf, logger=log)
    if not text.strip():
        return None
    target.write_text(text, encoding="utf-8")
    log.info("Read %s into %s (%d characters).", pdf.name, target.name, len(text))
    return target
