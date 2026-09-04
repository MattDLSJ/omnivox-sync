"""Selecting and assembling pages from an i+ Interactif textbook.

The network half is not tested here: it needs a live licensed session, and a
fake of it would only assert that the fake works. What IS tested is everything
that decides WHICH pages get pulled and what the file ends up being, because
those are the parts that fail silently and produce a plausible-looking PDF of
the wrong thirty pages.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.iplus import (
    MEDIUM_SUFFIX, Book, IplusError, Page, build_pdf, find_book, select_pages,
)


def _pages() -> list[Page]:
    """A book shaped like the real ones: front matter, then printed numbers."""
    front = [Page(1, "C1", 0, "9/C1.png", 100), Page(2, "C2", 1, "9/C2.png", 100),
             Page(3, "I", 2, "9/I.png", 100)]
    ch1 = [Page(10 + i, str(i), 3 + i, f"9/{i}.png", 200) for i in range(1, 6)]
    ch2 = [Page(20 + i, str(i), 8 + i, f"9/{i}.png", 300) for i in range(6, 11)]
    return front + ch1 + ch2


# --- chapter selection ------------------------------------------------------


def test_chapter_one_is_the_front_matter():
    """Group 1 in the manifest is the cover and the tables, not Chapitre 1.
    Anyone reading the numbers has to know that, so it is pinned here."""
    got = select_pages(_pages(), chapter=1, first=None, last=None)
    assert [p.name for p in got] == ["C1", "C2", "I"]


def test_a_chapter_is_returned_whole_and_in_order():
    got = select_pages(_pages(), chapter=2, first=None, last=None)
    assert [p.name for p in got] == ["1", "2", "3", "4", "5"]


def test_a_chapter_past_the_end_is_refused_not_clamped():
    """Clamping would hand back the last chapter and look like it worked."""
    with pytest.raises(IplusError, match="outside"):
        select_pages(_pages(), chapter=99, first=None, last=None)


# --- page-label selection ---------------------------------------------------


def test_a_run_of_printed_labels_is_inclusive_at_both_ends():
    got = select_pages(_pages(), chapter=None, first="2", last="4")
    assert [p.name for p in got] == ["2", "3", "4"]


def test_labels_are_not_indices():
    """"page 1" is the page printed 1, which here is the fourth image in the
    volume. Treating the label as an index silently returns the wrong pages."""
    got = select_pages(_pages(), chapter=None, first="1", last="1")
    assert got[0].page_id == 11


def test_a_reversed_range_still_works():
    got = select_pages(_pages(), chapter=None, first="4", last="2")
    assert [p.name for p in got] == ["2", "3", "4"]


def test_a_single_label_gives_one_page():
    got = select_pages(_pages(), chapter=None, first="3", last=None)
    assert len(got) == 1 and got[0].name == "3"


def test_an_unknown_label_is_refused():
    with pytest.raises(IplusError, match="no page labelled"):
        select_pages(_pages(), chapter=None, first="999", last=None)


def test_asking_for_nothing_is_refused():
    with pytest.raises(IplusError):
        select_pages(_pages(), chapter=None, first=None, last=None)


# --- book matching ----------------------------------------------------------


BOOKS = [
    Book(623, "Initiation à la psychologie (4e éd)"),
    Book(1353, "Initiation à la recherche qualitative en sciences humaines"),
    Book(1195, "Histoire du monde depuis le 15e siècle"),
]


def test_a_distinctive_fragment_matches():
    assert find_book(BOOKS, "Histoire du monde").book_id == 1195


def test_matching_ignores_case_and_padding():
    assert find_book(BOOKS, "  HISTOIRE du Monde  ").book_id == 1195


def test_an_ambiguous_fragment_matches_nothing():
    """"Initiation" is two of his books. Picking one would be a coin flip that
    downloads thirty pages of the wrong textbook."""
    assert find_book(BOOKS, "Initiation") is None


def test_an_exact_title_wins_over_being_a_prefix_of_another():
    books = BOOKS + [Book(9, "Histoire du monde depuis le 15e siècle - cahier")]
    assert find_book(books, "Histoire du monde depuis le 15e siècle").book_id == 1195


# --- assembly ---------------------------------------------------------------


def _png(tmp_path: Path, name: str) -> bytes:
    out = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=white:s=200x260", "-frames:v", "1", "-y", str(out)],
        check=True, capture_output=True, timeout=60,
    )
    return out.read_bytes()


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_pages_become_one_pdf_in_order(tmp_path):
    images = [_png(tmp_path, f"{i}.png") for i in range(3)]
    dest = build_pdf(images, tmp_path / "out" / "book.pdf")
    assert dest.exists()
    assert dest.read_bytes().startswith(b"%PDF")
    assert not list(dest.parent.glob("*.part")), "the temp file was left behind"


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_the_pdf_appears_whole_or_not_at_all(tmp_path):
    """Written to .part then renamed: a half-written PDF in a course folder
    would sync to Drive and upload to NotebookLM looking like a real source."""
    import inspect

    from src import iplus

    src = inspect.getsource(iplus.build_pdf)
    assert ".part" in src and "replace" in src


def test_no_pages_is_an_error_not_an_empty_pdf(tmp_path):
    with pytest.raises(IplusError):
        build_pdf([], tmp_path / "empty.pdf")


def test_the_medium_render_is_the_default():
    """1350px is legible down to map keys and a third the bytes of the 2700px
    original, which is what keeps a chapter under NotebookLM's 200 MB cap."""
    assert MEDIUM_SUFFIX == "_m"


# --- the NotebookLM queue ---------------------------------------------------


def test_a_chapter_is_queued_for_its_notebook(tmp_path, monkeypatch):
    """The same queue the sync and the recorder feed, so `make upload` drains
    a textbook chapter exactly like a slide deck or a transcript."""
    import json
    import logging
    from types import SimpleNamespace

    from src.iplus import queue_for_notebook

    repo = tmp_path
    (repo / "state").mkdir(parents=True)
    cfg = SimpleNamespace(repo_root=repo)
    course = SimpleNamespace(code="350-703-EM", notebook="Psycho - Cegep Fall 2026")
    pdf = tmp_path / "ch2.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    assert queue_for_notebook(cfg, course, pdf, logging.getLogger("t")) is True
    queued = json.loads((repo / "state" / "upload_queue.json").read_text())
    assert len(queued) == 1
    assert queued[0]["course_code"] == "350-703-EM"
    assert queued[0]["notebook"] == "Psycho - Cegep Fall 2026"
    assert queued[0]["path"] == str(pdf)


def test_a_busy_queue_lock_does_not_lose_the_chapter(tmp_path, monkeypatch):
    """The PDF is already on disk. A missed queue entry costs one `make upload`;
    raising here would make the caller think the fetch itself failed."""
    import logging
    from types import SimpleNamespace

    from src import common
    from src.iplus import queue_for_notebook

    repo = tmp_path
    (repo / "state").mkdir(parents=True)

    def busy(*a, **k):
        raise common.RunBusy("held")

    monkeypatch.setattr("src.common.run_lock", busy)
    pdf = tmp_path / "ch2.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    ok = queue_for_notebook(
        SimpleNamespace(repo_root=repo),
        SimpleNamespace(code="X", notebook="N"), pdf, logging.getLogger("t"),
    )
    assert ok is False
    assert pdf.exists(), "the chapter must survive a queue failure"


def test_the_cap_leaves_room_under_notebooklms_limit():
    """NotebookLM refuses at 200 MB. Finding that out after uploading 190 MB
    over a phone tether is the failure this margin exists to avoid."""
    from src.iplus import MAX_SOURCE_MB

    assert 150 <= MAX_SOURCE_MB < 200
