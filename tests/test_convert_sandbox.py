"""Conversion must not litter the course folder while it runs.

The course folders live inside a live Google Drive mirror (sync_type=MIRROR),
so anything that appears there, even for a second, is something Drive will try
to upload. Two things used to appear:

  * LibreOffice drops a `.~lock.<name>#` file beside whatever document it
    opens. Converting a deck in place meant creating one inside the mirror.
    Drive raced to upload it, LibreOffice deleted it a moment later, and the
    half-uploaded copy was orphaned. That is the file left behind:

        School/.tmp.driveupload/83933
        ,<user>,<machine>.local,23.08.2026 23:57,
        file:///.../T/soffice-profile-awxqt8sd;

    A LibreOffice lock file, timestamped to the minute the first sync ran its
    conversions.

  * The PDF itself, visible at its final name while still being written. Drive
    uploads the partial file, and a conversion that dies midway leaves a
    truncated PDF at exactly the name the sync later reads as "already on
    disk" -- which is the shape of the bug in test_deck_and_pdf_collision.

So LibreOffice is now pointed at a copy inside a private sandbox and never at
the course folder at all, and the finished PDF arrives by os.replace.

These tests fake soffice so they can look at the folder DURING the run, which
is the only moment the old behaviour was observable. The post-hoc state was
always clean: LibreOffice tidies its own lock up on exit.
"""

from pathlib import Path

import pytest

from src.convert import convert_to_pdf

DECK = b"PK\x03\x04 pretend pptx"


@pytest.fixture
def course(tmp_path):
    """A course folder as it really is: the deck sits in the folder we are
    also converting into."""
    folder = tmp_path / "Initiation à la psychologie"
    folder.mkdir()
    (folder / "703_A26_01_Intro_JV_Et.pptx").write_bytes(DECK)
    return folder


def _spy(monkeypatch, folder, seen):
    """Stand in for soffice: record what the course folder holds at the moment
    it would run, then write the PDF where the real one would."""
    def fake_run(cmd, **kwargs):
        seen.append({p.name for p in folder.iterdir()})
        seen.append(Path(cmd[-1]))
        work = Path(cmd[cmd.index("--outdir") + 1])
        seen.append(work)
        (work / "703_A26_01_Intro_JV_Et.pdf").write_bytes(b"%PDF-1.4 rendered")

        class Done:
            returncode, stdout, stderr = 0, "", ""
        return Done()

    monkeypatch.setattr("src.convert.find_soffice", lambda: "/usr/bin/true")
    monkeypatch.setattr("src.convert.subprocess.run", fake_run)


def test_the_course_folder_is_untouched_while_soffice_runs(course, monkeypatch):
    """The regression. Nothing may exist in the mirrored folder mid-conversion
    except what was already there."""
    seen = []
    _spy(monkeypatch, course, seen)

    convert_to_pdf(course / "703_A26_01_Intro_JV_Et.pptx", course)

    assert seen[0] == {"703_A26_01_Intro_JV_Et.pptx"}, (
        f"the folder gained {seen[0] - {'703_A26_01_Intro_JV_Et.pptx'}} mid-conversion"
    )
    assert seen[2] != course, (
        "soffice was told to write straight into the mirrored folder, so the "
        "PDF is visible there while it is still being written"
    )


def test_libreoffice_is_pointed_at_a_copy_outside_the_mirror(course, monkeypatch):
    """It locks whatever it opens, so it must not be handed the real file.
    The copy has to be faithful or the render would be of the wrong bytes."""
    seen = []
    _spy(monkeypatch, course, seen)

    convert_to_pdf(course / "703_A26_01_Intro_JV_Et.pptx", course)

    opened = seen[1]
    assert opened.parent != course
    assert opened.name == "703_A26_01_Intro_JV_Et.pptx"


def test_the_pdf_lands_complete_and_leaves_no_scratch_behind(course, monkeypatch):
    """os.replace needs a same-filesystem landing file first. It must not
    survive, hidden or not: Drive would upload that too."""
    seen = []
    _spy(monkeypatch, course, seen)

    pdf = convert_to_pdf(course / "703_A26_01_Intro_JV_Et.pptx", course)

    assert pdf == course / "703_A26_01_Intro_JV_Et.pdf"
    assert pdf.read_bytes() == b"%PDF-1.4 rendered"
    assert {p.name for p in course.iterdir()} == {
        "703_A26_01_Intro_JV_Et.pptx", "703_A26_01_Intro_JV_Et.pdf",
    }


def test_a_failed_conversion_leaves_the_folder_exactly_as_it_was(course, monkeypatch):
    """The truncated-file case. soffice exiting 0 having written nothing is
    routine, and the folder must not be left holding a stub at the real name."""
    def fake_run(cmd, **kwargs):
        class Done:
            returncode, stdout, stderr = 0, "", "could not load"
        return Done()

    monkeypatch.setattr("src.convert.find_soffice", lambda: "/usr/bin/true")
    monkeypatch.setattr("src.convert.subprocess.run", fake_run)

    from src.convert import ConversionError
    with pytest.raises(ConversionError, match="produced no output"):
        convert_to_pdf(course / "703_A26_01_Intro_JV_Et.pptx", course)

    assert {p.name for p in course.iterdir()} == {"703_A26_01_Intro_JV_Et.pptx"}
