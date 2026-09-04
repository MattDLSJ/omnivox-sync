"""The Énoncés de travaux section of LEA.

Until 2026-09-03 the sync only ever opened the Documents page. Four weeks of
running had collected every slide deck across six courses and not one
assignment brief, which is the half that carries the deadlines and the marking
criteria. The gap only surfaced when a brief that had been announced in class
turned out to be nowhere in the synced folder.
"""
from pathlib import Path

import pytest

from src.omnivox import OmnivoxCourse, OmnivoxDocument


# --------------------------------------------------------------------------
# Reading the listing
# --------------------------------------------------------------------------


def test_the_popup_target_is_extracted_from_the_onclick():
    """The row title is href="javascript:;". The real target is inside an
    onclick that opens a popup, so it can be reached neither by clicking nor by
    reading href, which is how a first attempt sat waiting 30 s for an
    invisible element."""
    from src.omnivox import _TRAVAIL_POPUP

    onclick = (
        "OpenCentre('DepotTravail.aspx?idtravail=9d685186-8e29-4ea5-8ca7-7c7a86b47c1d"
        "&Src=ListeTravaux&C=EDM&E=P&L=FRA&Ref=20260903172318&SID=eb07b7a9', "
        "'DepotTravailPopup', 'toolbar=no', 700, 780, true)"
    )
    m = _TRAVAIL_POPUP.search(onclick)
    assert m, "the popup target was not found"
    assert m.group(1).startswith("DepotTravail.aspx?idtravail=9d685186")
    assert "'" not in m.group(1), "the match ran past the closing quote"


def test_the_card_link_is_matched_on_enonces_not_on_travaux():
    """An SVG <title>Travaux</title> shadows the anchor. Playwright resolves the
    invisible one and then waits the full 30 s timeout for it to become
    clickable. "Énoncés distribués" appears only in the real link."""
    from src.omnivox import _TRAVAUX_LINK_TEXT

    assert _TRAVAUX_LINK_TEXT == "Énoncés distribués"
    assert "Travaux" != _TRAVAUX_LINK_TEXT


# --------------------------------------------------------------------------
# Never submit anything
# --------------------------------------------------------------------------


def test_the_brief_is_never_fetched_by_clicking():
    """DepotTravail.aspx is also the SUBMISSION form: it carries a file picker
    and a TRANSMETTRE button next to the download link.

    So the code navigates and reads, and never clicks on that page. Handing an
    automation the button that submits schoolwork is not a risk worth taking to
    save a download.
    """
    import inspect

    from src.omnivox import OmnivoxSession

    source = inspect.getsource(OmnivoxSession.list_assignments)
    assert ".click(" not in source, "list_assignments clicks on the submission page"
    assert "TRANSMETTRE" not in source
    for verb in ("set_input_files", "fill(", "press("):
        assert verb not in source, f"list_assignments uses {verb} on the submission page"


def test_open_travaux_page_only_clicks_the_card_link():
    """The one click allowed is the card link that opens the listing. It is on
    the LEA home card, nowhere near a submission form."""
    import inspect

    from src.omnivox import OmnivoxSession

    source = inspect.getsource(OmnivoxSession.open_travaux_page)
    assert source.count(".click()") == 1


# --------------------------------------------------------------------------
# Failure containment
# --------------------------------------------------------------------------


def test_a_course_with_no_travaux_link_is_not_an_error(monkeypatch):
    """A course that has posted no assignments has no such link on its card.
    That is a normal state and must not raise, or one quiet course would take
    down the whole sync."""
    import inspect

    from src.omnivox import OmnivoxSession

    source = inspect.getsource(OmnivoxSession.open_travaux_page)
    assert "no Travaux link" in source
    assert "return" in source


def test_a_broken_travaux_section_does_not_cost_the_documents():
    """The documents half already worked for four weeks. A new section that
    breaks must degrade to a logged error, not lose the files that were fine."""
    import inspect

    from src.omnivox_sync import _sync_course

    source = inspect.getsource(_sync_course)
    i = source.index("list_assignments")
    window = source[max(0, i - 400):i + 400]
    assert "try:" in window
    assert "except Exception" in window
    assert '"scope": "travaux"' in window


def test_a_brief_skips_the_click_that_can_never_work():
    """A brief's anchor lives on DepotTravail.aspx, which list_assignments
    deliberately navigates away from. So expect_download has nothing to wait for
    and burns its full 30 s timeout before falling through to the fetch that was
    always going to be the answer.

    Measured on the first live run: 30 s waiting, then 0.4 s to get the file.
    """
    import inspect

    from src.omnivox import _ENONCE_MARKER, OmnivoxSession

    source = inspect.getsource(OmnivoxSession.download)
    assert "_ENONCE_MARKER in doc.ref" in source
    i = source.index("_ENONCE_MARKER in doc.ref")
    # Against the CALL, not the word: download's own docstring names
    # expect_download in its first line, which sits before everything.
    j = source.index("with self.page.expect_download(")
    assert i < j, "the shortcut must come before the doomed click"
    assert _ENONCE_MARKER == "ReadDocumentTravail.aspx"


def test_the_fetch_path_is_shared_and_not_duplicated():
    """Both routes end in the same authenticated fetch. Two copies would drift,
    and the HTML-instead-of-a-file check is the kind of guard that only helps if
    every path runs it."""
    import inspect

    from src.omnivox import OmnivoxSession

    fetch = inspect.getsource(OmnivoxSession._fetch_to)
    assert "returned an HTML page" in fetch
    assert inspect.getsource(OmnivoxSession.download).count("_fetch_to") == 2
