"""LEA distributes embedded videos as well as files, and they were invisible.

Found on 2026-08-27 in Basketball: "Règles de bases - FIBA" had been sitting in
the documents list since the 26th, between two files that synced fine, and the
sync had never seen it. Not downloaded, not recorded, not even logged.

The row does not match anything the file path looks for. Its anchor is a
javascript: call into VisualiseVideo.aspx rather than VisualiseDocument.aspx,
and the YouTube address is not in the documents page at all: it only exists as
an iframe src once LEA has rendered its own viewer.

NotebookLM ingests YouTube directly, so the useful end state is the video as a
source in the notebook plus a shortcut in the course folder, which is what
these tests pin.
"""

from __future__ import annotations

import plistlib

import pytest

from src.omnivox import OmnivoxDocument, OmnivoxSession
from src.omnivox_sync import sync


def _video(course="601-101-MQ", name="Regles_de_bases_-_FIBA", date="2026-08-26"):
    return OmnivoxDocument(
        course_code=course, filename=name, publish_date=date,
        ref="javascript:VisualiserVideo('VisualiseVideo.aspx?C=EDM&IDDocCoursDocument=abc', true);",
        description="Règles de bases - FIBA", is_video=True,
    )


def _both(course_factory):
    return [course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")]


# --- the id regex, against every shape the embed has been seen in -----------


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/embed/v9FF1qI5pFo?wmode=opaque&autoplay=1", "v9FF1qI5pFo"),
    ("https://www.youtube-nocookie.com/embed/v9FF1qI5pFo", "v9FF1qI5pFo"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=abcDEF12345&t=30", "abcDEF12345"),
])
def test_youtube_ids_are_recognised(url, expected):
    assert OmnivoxSession._YOUTUBE_ID.search(url).group(1) == expected


def test_a_non_youtube_url_is_not_mistaken_for_one():
    assert OmnivoxSession._YOUTUBE_ID.search("https://vimeo.com/123456789") is None


def test_the_javascript_wrapper_is_unwrapped():
    """The href is not a URL. It is a javascript: call with the URL inside it."""
    ref = _video().ref
    assert OmnivoxSession._VIDEO_CALL.search(ref).group(1).startswith("VisualiseVideo.aspx?")


# --- the sync ---------------------------------------------------------------


def test_a_video_becomes_a_shortcut_and_a_notebook_source(
    loaded_config, fake_driver, course_factory
):
    cfg = loaded_config
    url = "https://www.youtube.com/watch?v=v9FF1qI5pFo"
    driver = fake_driver(
        courses=_both(course_factory),
        documents={"601-101-MQ": [_video()]},
        videos={"Regles_de_bases_-_FIBA": url},
    )
    result = sync(cfg, driver)

    assert driver.downloaded == [], "a video is not a file; never try to fetch it"
    assert result.errors == []

    folder = cfg.folder_for(cfg.course_by_code("601-101-MQ"))
    shortcut = folder / "Règles de bases - FIBA.webloc"
    assert shortcut.exists(), "no shortcut was written into the course folder"
    assert plistlib.loads(shortcut.read_bytes())["URL"] == url

    queued = [q for q in result.upload_queue if q.get("youtube")]
    assert len(queued) == 1
    assert queued[0]["youtube"] == url
    assert queued[0]["notebook"], "a video with no notebook can never be uploaded"


def test_the_shortcut_is_named_for_the_human_label(
    loaded_config, fake_driver, course_factory
):
    """The icon title is a sanitised filename, "Regles_de_bases_-_FIBA". The
    row's own text is what the teacher wrote, and it is what belongs in a
    folder you read with your eyes."""
    driver = fake_driver(
        courses=_both(course_factory), documents={"601-101-MQ": [_video()]},
        videos={"Regles_de_bases_-_FIBA": "https://www.youtube.com/watch?v=x"},
    )
    sync(loaded_config, driver)
    folder = loaded_config.folder_for(loaded_config.course_by_code("601-101-MQ"))
    assert (folder / "Règles de bases - FIBA.webloc").exists()
    assert not (folder / "Regles_de_bases_-_FIBA.webloc").exists()


def test_an_unresolvable_video_is_reported_and_costs_only_its_own_row(
    loaded_config, fake_driver, course_factory, doc_factory
):
    """The viewer URL is session-bound and can expire. One dead video must not
    take the rest of the course's documents down with it."""
    driver = fake_driver(
        courses=_both(course_factory),
        documents={"601-101-MQ": [_video(), doc_factory(filename="ch02.pdf")]},
        videos={},  # resolution returns ""
    )
    result = sync(loaded_config, driver)

    assert any("unresolved video" in e["error"] for e in result.errors)
    assert [n for _, n in driver.downloaded] == ["ch02.pdf"], "the other row still synced"
    assert [q for q in result.upload_queue if q.get("youtube")] == []


def test_a_video_is_recorded_so_it_reports_once(
    loaded_config, fake_driver, course_factory
):
    """Without a state record the same video is re-resolved, re-shortcut and
    re-queued on every single sync, which for NotebookLM means duplicates."""
    driver = fake_driver(
        courses=_both(course_factory), documents={"601-101-MQ": [_video()]},
        videos={"Regles_de_bases_-_FIBA": "https://www.youtube.com/watch?v=x"},
    )
    sync(loaded_config, driver)
    again = sync(loaded_config, driver)
    assert [q for q in again.upload_queue if q.get("youtube")] == []
    assert again.links == []
