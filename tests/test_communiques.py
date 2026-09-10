"""Teacher announcements, saved into the course folder they belong to.

The gap these close: a communiqué is what a teacher sends to a whole class,
and it is where the actual instruction often lives. Two real ones from a single
course said "se procurer l'ouvrage le Gorgias de Platon à la COOP" and carried
a five-point guide to studying for the exam. Neither is a document, so neither
was ever downloaded, and neither reached any folder or notebook. The word
"communiqué" did not occur anywhere in this project's source: they were never
being looked at, which is different from being missed sometimes.
"""

import pytest

from src.communiques import full_title, render, safe_name, strip_chrome, sync_communiques

BODY = (
    "À compléter avant le cours de vendredi\n"
    "340-101-MQ gr. 1060\n"
    "Philosophie et rationalité\n"
    "Publié par Jordan Tremblay le 8 sep 2026\t\n"
    "\n"
    "Se procurer l'ouvrage à la COOP\n"
    "Lire les pages 123 à 131\n"
    "\n\nFermer"
)


class FakeDriver:
    def __init__(self, found=None, body=BODY, fetch_raises=None, list_raises=None):
        self._found = found if found is not None else {
            "601-101-MQ": [{"date": "", "title": "À compléter (e...",
                            "url": "/cvir/comm/Communique.aspx?x=1", "unread": True}]
        }
        self._body = body
        self._fetch_raises = fetch_raises
        self._list_raises = list_raises
        self.fetched = []

    def list_communiques(self):
        if self._list_raises:
            raise self._list_raises
        return dict(self._found)

    def fetch_communique(self, url):
        if self._fetch_raises:
            raise self._fetch_raises
        self.fetched.append(url)
        return self._body

    def communique_meta(self, body):
        return ("Jordan Tremblay", "2026-09-08") if "Publié" in body else ("", "")


# ---------------------------------------------------------------- rendering


def test_the_title_comes_from_the_body_not_the_card():
    """The card truncates: "À compléter pour le prochain cours (e..." became a
    FILENAME ending in "(e"."""
    assert full_title(BODY, "(e...") == "À compléter avant le cours de vendredi"


def test_a_body_with_nothing_in_it_falls_back_to_the_card():
    assert full_title("", "from the card") == "from the card"


def test_the_readers_own_buttons_are_not_content():
    assert strip_chrome(["real", "", "Fermer", ""]) == ["real"]
    assert strip_chrome(["real", "Close"]) == ["real"]


def test_the_repeated_header_is_dropped_but_the_body_is_not():
    out = render("340-101-MQ", "À compléter avant le cours de vendredi",
                 "Jordan Tremblay", "2026-09-08", BODY)
    assert "Se procurer l'ouvrage à la COOP" in out
    assert "Lire les pages 123 à 131" in out
    assert "Publié par" not in out, "that line is in the metadata already"
    assert "gr. 1060" not in out
    assert out.rstrip().endswith("131"), "the Fermer button is not content"


def test_the_metadata_line_carries_who_and_when():
    out = render("340-101-MQ", "T", "Jordan Tremblay", "2026-09-08", BODY)
    assert "*340-101-MQ · Jordan Tremblay · 2026-09-08*" in out


def test_a_body_with_no_published_line_still_renders():
    out = render("X-1", "Titre", "", "", "Titre\nX-1 gr. 2\n\nLe contenu")
    assert "Le contenu" in out


@pytest.mark.parametrize("raw,expected_absent", [
    ('a:b*c?d"e<f>g|h', ':*?"<>|'),
    ("trailing dot.", "."),
])
def test_filenames_survive_every_filesystem(raw, expected_absent):
    """A title is free text a teacher typed, and NTFS refuses several of the
    characters they reach for."""
    name = safe_name(raw)
    for char in expected_absent:
        assert not name.endswith(char)
    assert not any(c in name for c in ':*?"<>|')


# ------------------------------------------------------------------- saving


def test_a_new_communique_is_saved_and_queued(loaded_config, tmp_repo):
    driver = FakeDriver()
    queued = sync_communiques(loaded_config, driver, logger=None)

    assert len(queued) == 1
    course = loaded_config.courses[0]
    folder = loaded_config.folder_for(course) / loaded_config.communiques_folder
    saved = list(folder.glob("*.md"))
    assert len(saved) == 1
    assert saved[0].name.startswith("2026-09-08 - ")
    assert "COOP" in saved[0].read_text(encoding="utf-8")


def test_the_same_communique_is_not_saved_twice(loaded_config):
    """It is on the card every day for the rest of the semester."""
    driver = FakeDriver()
    assert len(sync_communiques(loaded_config, driver, logger=None)) == 1
    assert sync_communiques(loaded_config, driver, logger=None) == []


def test_a_dry_run_writes_nothing(loaded_config):
    driver = FakeDriver()
    sync_communiques(loaded_config, driver, dry_run=True, logger=None)
    course = loaded_config.courses[0]
    folder = loaded_config.folder_for(course) / loaded_config.communiques_folder
    assert not folder.exists()


def test_a_course_that_is_not_configured_is_skipped(loaded_config):
    """LÉA shows every class on the account, including ones not in config."""
    driver = FakeDriver(found={"999-NOT-IN": [
        {"date": "", "title": "x", "url": "/u", "unread": True}]})
    assert sync_communiques(loaded_config, driver, logger=None) == []


def test_a_listing_that_fails_does_not_cost_the_run(loaded_config):
    """An announcement is worth having and worth nothing at the cost of the
    documents, which are the run's actual job."""
    driver = FakeDriver(list_raises=RuntimeError("LÉA changed its markup"))
    assert sync_communiques(loaded_config, driver, logger=None) == []


def test_one_communique_that_will_not_fetch_does_not_stop_the_others(loaded_config):
    driver = FakeDriver(
        found={"601-101-MQ": [
            {"date": "", "title": "bad", "url": "/bad", "unread": True},
        ]},
        fetch_raises=RuntimeError("HTTP 404"),
    )
    assert sync_communiques(loaded_config, driver, logger=None) == []
