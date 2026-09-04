"""Format routing and LibreOffice conversion.

Knows nothing about courses, config, or state: paths in, paths out.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# NotebookLM accepts these directly (spec section 7 step 7).
NOTEBOOKLM_SUPPORTED = frozenset(
    {
        ".pdf", ".docx", ".txt", ".md",
        ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".mp3", ".wav",
        ".epub",
    }
)

# Office formats LibreOffice can render to PDF.
CONVERTIBLE = frozenset(
    {".pptx", ".ppt", ".xlsx", ".xls", ".odp", ".odt", ".ods", ".doc", ".rtf", ".csv"}
)

#: Where LibreOffice lands when it is not on PATH. The Windows installer does
#: not add itself to PATH at all, so that entry is the only way it is ever
#: found there. Target soffice.com and not soffice.exe: the .com build is the
#: console one that waits for the conversion to finish, while the .exe returns
#: immediately and the caller checks for the PDF on the very next line.
_SOFFICE_FALLBACKS = (
    "/opt/homebrew/bin/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/local/bin/soffice",
    r"C:\Program Files\LibreOffice\program\soffice.com",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
)


class ConversionError(Exception):
    """Raised when a file could not be converted to PDF."""


def _ext(path: Path) -> str:
    return path.suffix.lower()


def needs_conversion(path: Path) -> bool:
    """True when the file must be rendered to PDF before upload."""
    return _ext(path) in CONVERTIBLE


def is_uploadable(path: Path) -> bool:
    """True when NotebookLM accepts this file as-is."""
    return _ext(path) in NOTEBOOKLM_SUPPORTED


def find_soffice() -> str:
    """Locate the soffice binary. Checks PATH, then known install locations,
    because launchd jobs run with no shell PATH (spec section 11)."""
    found = shutil.which("soffice")
    if found:
        return found
    for candidate in _SOFFICE_FALLBACKS:
        if Path(candidate).exists():
            return candidate
    how = (
        "winget install TheDocumentFoundation.LibreOffice"
        if os.name == "nt"
        else "brew install --cask libreoffice"
    )
    raise ConversionError(f"LibreOffice (soffice) not found. Install it with: {how}")


def convert_to_pdf(src: Path, outdir: Path, *, timeout: int = 180) -> Path:
    """Render `src` to PDF inside `outdir`. The original is left untouched.

    LibreOffice never sees `outdir`. It reads a COPY of the source inside a
    private sandbox and writes the PDF there, and only the finished file is
    moved into place. Two reasons, both observed rather than theoretical:

    * LibreOffice drops a `.~lock.<name>#` file beside whatever it opens. The
      course folders sit inside a live Google Drive mirror, so Drive raced to
      upload those locks, and when LibreOffice deleted them a moment later the
      half-uploaded copy was orphaned in `School/.tmp.driveupload/`.
    * A PDF written straight into the course folder is visible while it is
      still being written. Drive uploads the partial file, and a conversion
      that dies midway leaves a truncated PDF at exactly the name the sync
      later reads as "already on disk".

    The last step is os.replace within `outdir`, so the destination name goes
    from absent to complete with nothing in between.

    Returns the path to the produced PDF. Raises ConversionError on any failure.
    """
    src = Path(src)
    outdir = Path(outdir)
    if not src.exists():
        raise ConversionError(f"Source file not found: {src}")
    outdir.mkdir(parents=True, exist_ok=True)

    soffice = find_soffice()
    final = outdir / f"{src.stem}.pdf"

    # A private UserInstallation lets this run even while the user has
    # LibreOffice open; otherwise soffice refuses to start a second instance.
    with tempfile.TemporaryDirectory(prefix="soffice-") as sandbox:
        work = Path(sandbox)
        profile = work / "profile"
        staged_src = work / src.name
        shutil.copy2(src, staged_src)
        produced = work / f"{src.stem}.pdf"

        cmd = [
            soffice,
            # as_uri(), not an f-string: gluing "file://" onto a path only
            # produces a valid URL when the path starts with a slash. On
            # Windows it yields file://C:\Users\..., which LibreOffice
            # rejects, and every conversion fails.
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work),
            str(staged_src),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(
                f"Conversion of {src.name} timed out after {timeout}s"
            ) from exc
        except OSError as exc:
            raise ConversionError(f"Could not run soffice for {src.name}: {exc}") from exc

        if not produced.exists() or produced.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:400]
            raise ConversionError(
                f"soffice produced no output for {src.name} "
                f"(exit {proc.returncode}){': ' + detail if detail else ''}"
            )

        # Cross-device, so copy in under a hidden name first; os.replace is
        # only atomic within one filesystem.
        landing = outdir / f".{src.stem}.pdf.part"
        try:
            shutil.copy2(produced, landing)
            os.replace(landing, final)
        finally:
            landing.unlink(missing_ok=True)

    return final
