"""When a teacher posts a slide deck AND their own PDF of it.

Conversion exists for one reason: NotebookLM refuses .pptx. It is a workaround,
not an improvement, and LibreOffice's render of a PowerPoint routinely shifts
fonts and mangles diagrams.

One teacher posted every lecture twice, as X.pptx and X.pdf. Those
are two separate rows in one LEA listing, and the pipeline collapsed them onto
one path:

    Downloaded 703_A26_01_Intro_JV_Et.pptx
    WARNING  703_A26_01_Intro_JV_Et.pdf already on disk but absent from state;
             recording without downloading

The deck was downloaded, rendered to X.pdf, and then the teacher's own X.pdf
found a file already sitting at its destination. The existence check could not
tell "a document I downloaded" from "a PDF I rendered myself", so it took the
SKIP_EXISTING path meant for a lost state file, recorded the teacher's PDF as
handled, and never fetched it. Four decks reached NotebookLM as our render
instead of hers, and nothing failed.

Two fixes, because there are two orderings and a third case across runs:

  * the listing is read whole before the loop, so a deck is not converted at
    all when its PDF is published beside it (either row can come first, and
    a conversion must never overwrite a real download)
  * state records which PDFs it rendered, so one can never stand in for a
    document nobody fetched, even when the teacher posts the PDF weeks later
"""

import pytest

from src.omnivox import OmnivoxDocument
from src.omnivox_sync import (
    DOWNLOAD,
    SKIP_EXISTING,
    build_state_index,
    classify_document,
    published_pdf_stems,
    sync,
)

DECK = "703_A26_01_Intro_JV_Et.pptx"
PDF = "703_A26_01_Intro_JV_Et.pdf"
FOLDER = "Écriture et littérature"


def _docs(*names, course="601-101-MQ", date="2026-09-03"):
    return [
        OmnivoxDocument(course_code=course, filename=n, publish_date=date, ref=f"/r/{n}")
        for n in names
    ]


# --- the whole listing, both orderings --------------------------------------


@pytest.mark.parametrize(
    "order", [(DECK, PDF), (PDF, DECK)], ids=["deck-first", "pdf-first"]
)
def test_the_teachers_own_pdf_is_downloaded_not_shadowed_by_our_render(
    order, loaded_config, fake_driver, doc_factory, course_factory
):
    """The regression. Either row can come first: deck-first is what LEA
    actually served, and pdf-first is the ordering where a conversion would
    overwrite a file that had just been downloaded correctly."""
    cfg = loaded_config
    driver = fake_driver(courses=[course_factory()], documents={"601-101-MQ": _docs(*order)})

    result = sync(cfg, driver)

    assert (cfg.base_path / FOLDER / PDF).exists()
    assert ("601-101-MQ", PDF) in driver.downloaded, (
        "the teacher's own PDF was never fetched; a render stood in for it"
    )
    assert result.skipped_existing == []


@pytest.mark.parametrize("order", [(DECK, PDF), (PDF, DECK)], ids=["deck-first", "pdf-first"])
def test_nothing_is_converted_when_the_pdf_is_already_published(
    order, loaded_config, fake_driver, doc_factory, course_factory
):
    """Not merely redundant: converting here is how the worse render wins.
    Asserted on both lists so the test says the same thing whether or not
    LibreOffice is installed on the machine running it."""
    cfg = loaded_config
    driver = fake_driver(courses=[course_factory()], documents={"601-101-MQ": _docs(*order)})

    result = sync(cfg, driver)

    assert result.converted == []
    assert result.conversion_failures == []


def test_the_notebook_gets_one_pdf_for_the_deck_and_it_is_the_published_one(
    loaded_config, fake_driver, doc_factory, course_factory
):
    """The point of all of it. Two rows in, one source out, and it is hers."""
    cfg = loaded_config
    driver = fake_driver(courses=[course_factory()], documents={"601-101-MQ": _docs(DECK, PDF)})

    queued = [q["path"] for q in sync(cfg, driver).upload_queue]

    assert queued == [str(cfg.base_path / FOLDER / PDF)]


def test_a_deck_posted_alone_is_still_converted(
    loaded_config, fake_driver, doc_factory, course_factory, monkeypatch
):
    """The rule is 'prefer the teacher's PDF', not 'stop converting'. With no
    published PDF there is nothing to prefer, and NotebookLM still refuses the
    .pptx, so the render is the only way the deck gets there at all."""
    import src.omnivox_sync as sync_mod

    cfg = loaded_config
    made = cfg.base_path / FOLDER / PDF

    def fake_convert(src, outdir, **kw):
        made.parent.mkdir(parents=True, exist_ok=True)
        made.write_bytes(b"%PDF-1.4 rendered")
        return made

    monkeypatch.setattr(sync_mod, "convert_to_pdf", fake_convert)
    driver = fake_driver(courses=[course_factory()], documents={"601-101-MQ": _docs(DECK)})

    result = sync(cfg, driver)

    assert [c["pdf"] for c in result.converted] == [str(made)]
    assert [q["path"] for q in result.upload_queue] == [str(made)]


# --- across runs: the PDF turns up weeks after the deck ---------------------


def test_a_render_on_disk_does_not_block_a_later_published_pdf(tmp_path):
    """The delayed case, which skipping conversion alone does not cover: the
    render was made in September and the teacher posts her PDF in November. The
    file is already sitting at the destination, so without knowing it is ours
    this lands back on SKIP_EXISTING and the same silent loss repeats."""
    render = tmp_path / PDF
    render.write_bytes(b"%PDF-1.4 ours")
    index = build_state_index([
        {"course_code": "601-101-MQ", "filename": DECK, "publish_date": "2026-09-03",
         "converted_pdf": str(render)}
    ])

    doc = OmnivoxDocument(
        course_code="601-101-MQ", filename=PDF, publish_date="2026-11-05", ref="/r"
    )
    decision, dest = classify_document(doc, index, tmp_path)

    assert decision == DOWNLOAD
    assert dest == render, "the published PDF replaces the render, same name"


def test_a_stranger_on_disk_is_still_treated_as_lost_state(tmp_path):
    """The paired case, so the fix above cannot be mistaken for 'always
    re-download'. A file we did not render means the state file was lost, and
    spec section 7 step 4 says do not fetch the whole semester again."""
    (tmp_path / PDF).write_bytes(b"%PDF-1.4 not ours")
    doc = OmnivoxDocument(
        course_code="601-101-MQ", filename=PDF, publish_date="2026-11-05", ref="/r"
    )

    decision, _ = classify_document(doc, build_state_index([]), tmp_path)

    assert decision == SKIP_EXISTING


def test_the_index_records_every_render_and_nothing_else():
    index = build_state_index([
        {"course_code": "A", "filename": "a.pptx", "converted_pdf": "/x/a.pdf"},
        {"course_code": "A", "filename": "b.pdf", "converted_pdf": None},
        {"course_code": "A", "filename": "c.pdf"},
    ])
    assert index["conversions"] == {"/x/a.pdf"}


# --- the stem set -----------------------------------------------------------


def test_published_stems_ignore_case_because_teachers_type_the_filenames():
    docs = _docs("Intro_JV_Et.PDF", "notes.docx")
    assert published_pdf_stems(docs) == {"intro_jv_et"}


def test_a_web_link_is_not_a_published_pdf():
    """Links carry a filename too, and a link named like a PDF would silently
    suppress a real conversion."""
    doc = OmnivoxDocument(
        course_code="A", filename="lecture.pdf", publish_date="2026-09-03",
        ref="https://example.org/lecture.pdf", is_link=True,
    )
    assert published_pdf_stems([doc]) == set()
