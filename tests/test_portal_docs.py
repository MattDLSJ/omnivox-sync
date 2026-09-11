"""The college's own documents, as opposed to a course's.

Policies, guides and forms live in Omnivox "communautés", which are portal
pages rather than anything in LÉA, so nothing that walks courses had ever seen
one. The folder for everything-that-is-not-a-course sat empty all semester
while the portal was carrying a dozen documents.
"""

from pathlib import Path

import pytest

from src.portal_docs import destination, safe_name, sync_portal_documents


class FakeDriver:
    def __init__(self, documents=None, raises=None, fetch_raises=None):
        self._documents = documents if documents is not None else [
            {"title": "Règlement relatif aux technologies.pdf",
             "ref": "/intr/webpart.gestion.consultation/reglement.pdf",
             "community": "CEM_etudiants"},
        ]
        self._raises = raises
        self._fetch_raises = fetch_raises
        self.fetched = []

    def list_portal_documents(self, limit=60):
        if self._raises:
            raise self._raises
        return list(self._documents)

    portal = type("P", (), {"home": "https://example.omnivox.ca"})()

    def fetch_portal_document(self, ref, dest, referer=""):
        if self._fetch_raises:
            raise self._fetch_raises
        self.fetched.append((ref, referer))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"%PDF-1.4 pretend")
        return Path(dest)


def test_a_college_document_lands_beside_the_courses(loaded_config):
    """It belongs to none of them, so it goes into none of them."""
    driver = FakeDriver()
    queued = sync_portal_documents(loaded_config, driver, logger=None)

    assert len(queued) == 1
    assert Path(queued[0]["path"]).parent == loaded_config.digest_dir()
    assert Path(queued[0]["path"]).is_file()


def test_the_same_document_is_not_fetched_twice(loaded_config):
    """These sit on the portal for years."""
    driver = FakeDriver()
    assert len(sync_portal_documents(loaded_config, driver, logger=None)) == 1
    assert sync_portal_documents(loaded_config, driver, logger=None) == []


def test_a_dry_run_downloads_nothing(loaded_config):
    driver = FakeDriver()
    sync_portal_documents(loaded_config, driver, dry_run=True, logger=None)
    assert driver.fetched == []


def test_a_document_with_no_extension_gets_one(loaded_config):
    """Most of these anchors are icons, so the name comes from the URL and can
    arrive bare."""
    driver = FakeDriver([{"title": "guide sans extension",
                          "ref": "/intr/x/guide.pdf", "community": "c"}])
    queued = sync_portal_documents(loaded_config, driver, logger=None)
    assert queued[0]["filename"].endswith(".pdf")


def test_one_that_will_not_download_does_not_stop_the_others(loaded_config):
    driver = FakeDriver(fetch_raises=RuntimeError("HTTP 404"))
    assert sync_portal_documents(loaded_config, driver, logger=None) == []


def test_a_listing_failure_never_costs_the_run(loaded_config):
    driver = FakeDriver(raises=RuntimeError("portal markup changed"))
    assert sync_portal_documents(loaded_config, driver, logger=None) == []


def test_nothing_is_overwritten(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"first")
    assert destination(tmp_path, "a.pdf").name == "a (2).pdf"


@pytest.mark.parametrize("raw", ['a:b*c?d"e<f>g|h', "trailing dot."])
def test_filenames_survive_every_filesystem(raw):
    assert not any(c in safe_name(raw) for c in ':*?"<>|')
    assert not safe_name(raw).endswith(".")


def test_the_community_page_is_sent_as_the_referer(loaded_config):
    """Sent because every other fetch here sends one and some Omnivox
    endpoints check it. It does not lift a 403: eleven of twelve documents on
    a real account are forbidden with it and without it, because that account
    is not entitled to them."""
    driver = FakeDriver()
    sync_portal_documents(loaded_config, driver, logger=None)
    _ref, referer = driver.fetched[0]
    assert referer.endswith("/intr/CEM_etudiants/")
