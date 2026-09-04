import pytest

from src.common import StateStore
from src.omnivox_sync import sync


def test_downloads_a_new_document_into_the_course_folder(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="ch01.pdf")]}
    )
    result = sync(cfg, driver)
    assert (cfg.base_path / "Écriture et littérature" / "ch01.pdf").exists()
    assert len(result.downloaded) == 1
    assert result.downloaded[0]["filename"] == "ch01.pdf"
    assert result.downloaded[0]["converted_pdf"] is None


def test_course_folders_are_created_when_missing(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    assert not cfg.base_path.exists()
    sync(cfg, fake_driver(courses=[course_factory()], documents={"601-101-MQ": [doc_factory()]}))
    assert (cfg.base_path / "Écriture et littérature").is_dir()


def test_state_is_written_and_second_run_is_a_no_op(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config

    def make():
        return fake_driver(
            courses=[course_factory()], documents={"601-101-MQ": [doc_factory()]}
        )

    assert len(sync(cfg, make()).downloaded) == 1
    second_driver = make()
    second = sync(cfg, second_driver)
    assert second.downloaded == []
    assert second_driver.downloaded == []
    assert len(StateStore(cfg.repo_root / "state" / "downloads.json").read()) == 1


def test_only_configured_courses_are_synced(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="999-999-ZZ", name="Not mine")],
        documents={
            "601-101-MQ": [doc_factory()],
            "999-999-ZZ": [doc_factory(course="999-999-ZZ", filename="other.pdf")],
        },
    )
    assert [d["course_code"] for d in sync(cfg, driver).downloaded] == ["601-101-MQ"]


def test_only_course_flag_limits_the_run(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")],
        documents={
            "601-101-MQ": [doc_factory()],
            "201-103-RE": [doc_factory(course="201-103-RE", filename="calc.pdf")],
        },
    )
    result = sync(cfg, driver, only_course="201-103-RE")
    assert [d["filename"] for d in result.downloaded] == ["calc.pdf"]


def test_convertible_file_is_converted_and_pdf_is_queued(
    loaded_config, fake_driver, doc_factory, course_factory, monkeypatch
):
    cfg = loaded_config
    calls = []

    def fake_convert(src, outdir, **kwargs):
        calls.append(src)
        pdf = outdir / (src.stem + ".pdf")
        pdf.write_bytes(b"%PDF-1.4 fake")
        return pdf

    monkeypatch.setattr("src.omnivox_sync.convert_to_pdf", fake_convert)
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="deck.pptx")]}
    )
    result = sync(cfg, driver)
    assert len(calls) == 1
    assert len(result.converted) == 1
    assert result.upload_queue[0]["path"].endswith("deck.pdf")
    assert (cfg.base_path / "Écriture et littérature" / "deck.pptx").exists()
    assert result.downloaded[0]["converted_pdf"].endswith("deck.pdf")


def test_supported_file_is_queued_without_conversion(
    loaded_config, fake_driver, doc_factory, course_factory, monkeypatch
):
    cfg = loaded_config
    monkeypatch.setattr(
        "src.omnivox_sync.convert_to_pdf",
        lambda *a, **k: pytest.fail("must not convert a supported format"),
    )
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="notes.docx")]}
    )
    assert sync(cfg, driver).upload_queue[0]["path"].endswith("notes.docx")


def test_conversion_failure_of_an_uploadable_original_still_queues_the_original(
    loaded_config, fake_driver, doc_factory, course_factory, monkeypatch
):
    from src.convert import ConversionError

    cfg = loaded_config
    monkeypatch.setattr(
        "src.omnivox_sync.convert_to_pdf",
        lambda *a, **k: (_ for _ in ()).throw(ConversionError("soffice exploded")),
    )
    monkeypatch.setattr("src.omnivox_sync.is_uploadable", lambda path: True)
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="deck.pptx")]}
    )
    result = sync(cfg, driver)
    assert len(result.conversion_failures) == 1
    assert result.conversion_failures[0]["uploadable"] is True
    assert result.upload_queue[0]["path"].endswith("deck.pptx")


def test_conversion_failure_of_a_non_uploadable_original_skips_upload(
    loaded_config, fake_driver, doc_factory, course_factory, monkeypatch
):
    from src.convert import ConversionError

    cfg = loaded_config
    monkeypatch.setattr(
        "src.omnivox_sync.convert_to_pdf",
        lambda *a, **k: (_ for _ in ()).throw(ConversionError("soffice exploded")),
    )
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="deck.pptx")]}
    )
    result = sync(cfg, driver)
    assert result.conversion_failures[0]["uploadable"] is False
    assert result.upload_queue == []
    assert result.downloaded[0]["converted_pdf"] is None


def test_unknown_extension_is_filed_but_not_queued(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="bundle.zip")]}
    )
    result = sync(cfg, driver)
    assert (cfg.base_path / "Écriture et littérature" / "bundle.zip").exists()
    assert result.upload_queue == []


def test_dry_run_writes_absolutely_nothing(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(courses=[course_factory()], documents={"601-101-MQ": [doc_factory()]})
    result = sync(cfg, driver, dry_run=True)
    assert len(result.downloaded) == 1
    assert driver.downloaded == []
    assert not cfg.base_path.exists()
    assert StateStore(cfg.repo_root / "state" / "downloads.json").read() == []


def test_dry_run_twice_reports_the_same_thing(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config

    def make():
        return fake_driver(
            courses=[course_factory()], documents={"601-101-MQ": [doc_factory()]}
        )

    assert len(sync(cfg, make(), dry_run=True).downloaded) == 1
    assert len(sync(cfg, make(), dry_run=True).downloaded) == 1


def test_download_failure_records_an_error_and_continues_other_courses(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")],
        documents={
            "601-101-MQ": [doc_factory(filename="broken.pdf")],
            "201-103-RE": [doc_factory(course="201-103-RE", filename="fine.pdf")],
        },
        fail_on={"broken.pdf"},
    )
    result = sync(cfg, driver)
    assert len(result.errors) == 1
    assert [d["filename"] for d in result.downloaded] == ["fine.pdf"]


def test_failed_download_is_not_recorded_in_state(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory()],
        documents={"601-101-MQ": [doc_factory(filename="broken.pdf")]},
        fail_on={"broken.pdf"},
    )
    sync(cfg, driver)
    assert StateStore(cfg.repo_root / "state" / "downloads.json").read() == []


def test_listing_failure_for_one_course_does_not_abort_the_run(
    loaded_config, fake_driver, doc_factory, course_factory
):
    from src.omnivox import ParseError

    cfg = loaded_config
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")],
        documents={"201-103-RE": [doc_factory(course="201-103-RE", filename="fine.pdf")]},
    )
    original = driver.list_documents

    def flaky(course):
        if course.code == "601-101-MQ":
            raise ParseError("layout changed")
        return original(course)

    driver.list_documents = flaky
    result = sync(cfg, driver)
    assert len(result.errors) == 1
    assert [d["filename"] for d in result.downloaded] == ["fine.pdf"]


def test_existing_file_without_state_is_skipped_and_recorded(
    loaded_config, fake_driver, doc_factory, course_factory
):
    cfg = loaded_config
    folder = cfg.base_path / "Écriture et littérature"
    folder.mkdir(parents=True)
    (folder / "ch01.pdf").write_bytes(b"already here")
    driver = fake_driver(
        courses=[course_factory()], documents={"601-101-MQ": [doc_factory(filename="ch01.pdf")]}
    )
    result = sync(cfg, driver)
    assert len(result.skipped_existing) == 1
    assert driver.downloaded == []
    assert (folder / "ch01.pdf").read_bytes() == b"already here"
    assert len(StateStore(cfg.repo_root / "state" / "downloads.json").read()) == 1
    assert result.upload_queue == []


def test_unconfigured_semester_is_not_an_error(write_config, tmp_repo, fake_driver):
    """Spec 5.3: config.yaml carries `courses: []` until --discover has run."""
    from src.common import load_config

    cfg = load_config(write_config({"courses": []}), repo_root=tmp_repo)
    driver = fake_driver(courses=[])
    result = sync(cfg, driver)
    assert result.downloaded == []
    assert result.errors == []
    assert driver.listed_courses == 0, "must not even open the dashboard"


def test_configured_course_missing_from_lea_is_a_loud_error(
    loaded_config, fake_driver, course_factory
):
    """A course in config.yaml that LEA no longer shows must be reported,
    not silently counted as 'nothing new' (spec section 2: fail loudly)."""
    cfg = loaded_config
    result = sync(cfg, fake_driver(courses=[course_factory()]))  # only 1 of the 2 configured
    assert [e["scope"] for e in result.errors] == ["201-103-RE"]
    assert "not visible on LEA" in result.errors[0]["error"]


def test_dashboard_is_fetched_once_regardless_of_course_count(
    loaded_config, fake_driver, doc_factory, course_factory
):
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")],
        documents={"601-101-MQ": [doc_factory()]},
    )
    sync(loaded_config, driver)
    assert driver.listed_courses == 1


# --- External web links: LEA distributes links alongside files ---------------


def _both(course_factory):
    """Both configured courses, so no course is missing from the fake LEA."""
    return [course_factory(), course_factory(code="201-103-RE", name="Calcul différentiel")]


def _link_doc(course="601-101-MQ", name="Tests_Forms", date="2026-09-03"):
    from src.omnivox import OmnivoxDocument

    return OmnivoxDocument(
        course_code=course, filename=name, publish_date=date, ref="/r",
        description="Tests Forms", is_link=True,
    )


def test_web_links_are_recorded_but_never_downloaded(
    loaded_config, fake_driver, course_factory
):
    cfg = loaded_config
    driver = fake_driver(courses=_both(course_factory), documents={"601-101-MQ": [_link_doc()]})
    result = sync(cfg, driver)
    assert len(result.links) == 1
    assert result.downloaded == []
    assert driver.downloaded == [], "a web link is not a file; never fetch it"
    assert result.errors == []
    assert result.upload_queue == []


def test_web_links_are_recorded_in_state_so_they_report_once(
    loaded_config, fake_driver, course_factory
):
    """Without a state record they would be re-reported and re-notified daily."""
    cfg = loaded_config

    def make():
        return fake_driver(courses=_both(course_factory), documents={"601-101-MQ": [_link_doc()]})

    assert len(sync(cfg, make()).links) == 1
    assert sync(cfg, make()).links == [], "second run must be silent about the same link"


def test_web_link_appears_in_the_digest(loaded_config, fake_driver, course_factory):
    from src.omnivox_sync import chain_downstream

    cfg = loaded_config
    result = sync(cfg, fake_driver(courses=_both(course_factory),
                                   documents={"601-101-MQ": [_link_doc()]}))
    chain_downstream(cfg, result)
    text = (cfg.base_path / "_digest.md").read_text(encoding="utf-8")
    assert "Tests Forms" in text, "spec section 2: nothing observed may become invisible"


def test_link_and_file_in_the_same_course_are_handled_independently(
    loaded_config, fake_driver, course_factory, doc_factory
):
    cfg = loaded_config
    driver = fake_driver(
        courses=_both(course_factory),
        documents={"601-101-MQ": [_link_doc(), doc_factory(filename="real.pdf")]},
    )
    result = sync(cfg, driver)
    assert len(result.links) == 1
    assert [d["filename"] for d in result.downloaded] == ["real.pdf"]


# --- Finder decoration on new course folders ---------------------------------


def test_new_course_folders_get_icon_and_tag(
    write_config, tmp_repo, sample_config_dict, fake_driver, course_factory, doc_factory
):
    """A new semester should look right in Finder without manual work."""
    from src.common import load_config
    from src.finder import read_tags

    courses = [dict(c) for c in sample_config_dict["courses"]]
    courses[0]["icon"] = "🧠"
    cfg = load_config(
        write_config({"courses": courses, "finder": {"tag": "School", "color": "green"}}),
        repo_root=tmp_repo,
    )
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul")],
        documents={"601-101-MQ": [doc_factory()]},
    )
    sync(cfg, driver)
    folder = cfg.folder_for(cfg.courses[0])
    assert folder.is_dir()
    assert read_tags(folder) == ["School"]


def test_dry_run_does_not_decorate(
    write_config, tmp_repo, sample_config_dict, fake_driver, course_factory, doc_factory
):
    from src.common import load_config

    courses = [dict(c) for c in sample_config_dict["courses"]]
    courses[0]["icon"] = "🧠"
    cfg = load_config(
        write_config({"courses": courses, "finder": {"tag": "School"}}), repo_root=tmp_repo
    )
    driver = fake_driver(
        courses=[course_factory(), course_factory(code="201-103-RE", name="Calcul")],
        documents={"601-101-MQ": [doc_factory()]},
    )
    sync(cfg, driver, dry_run=True)
    assert not cfg.base_path.exists()
