import json

import pytest

from src.common import StateStore
from src.digest import (
    DigestItem,
    append_digest,
    format_notification,
    item_hash,
    items_from_observations,
    items_from_sync,
    rank,
    rank_rules,
    run_digest,
    select_new,
    strip_accents,
)


def _item(**kw):
    base = dict(kind="document", title="ch01.pdf", course="601-101-MQ", date="2026-09-03")
    base.update(kw)
    return DigestItem(**base)


# --- ranking -----------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    ["Examen final", "ÉVALUATION sommative", "Remise du travail", "Échéance vendredi",
     "Cours reporté", "Cours annulé", "Changement de local", "Présence obligatoire",
     "Local B-321", "Absence justifiée", "Retard accepté", "Test de lecture"],
)
def test_spec_keywords_mark_items_important(title):
    assert rank_rules([_item(title=title)])[0].important is True


def test_ranking_is_accent_and_case_insensitive():
    assert rank_rules([_item(title="EVALUATION")])[0].important is True
    assert rank_rules([_item(title="évaluation")])[0].important is True
    assert rank_rules([_item(title="Echeance")])[0].important is True


def test_routine_material_is_not_important():
    for title in ("ch01_presentationVE.pptx", "Chapitre 3", "Notes de cours"):
        assert rank_rules([_item(title=title)])[0].important is False


def test_keyword_also_matches_in_detail():
    assert rank_rules([_item(title="Message", detail="examen mardi")])[0].important is True


def test_reason_is_recorded():
    assert "examen" in rank_rules([_item(title="Examen final")])[0].reason


def test_strip_accents():
    assert strip_accents("ÉCHÉANCE") == "echeance"


def test_rank_falls_back_to_rules_when_gemini_key_missing(caplog):
    out = rank([_item(title="Examen")], "gemini", api_key=None)
    assert out[0].important is True


def test_rank_falls_back_to_rules_when_gemini_errors(monkeypatch):
    monkeypatch.setattr(
        "src.digest.rank_gemini",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api down")),
    )
    out = rank([_item(title="Examen")], "gemini", api_key="fake")
    assert out[0].important is True, "an API outage must not lose importance ranking"


# --- dedup -------------------------------------------------------------------


def test_hash_is_stable_across_calls():
    assert item_hash(_item()) == item_hash(_item())


def test_hash_ignores_whitespace_noise():
    assert item_hash(_item(title="a  b")) == item_hash(_item(title="a b"))


def test_hash_changes_with_date():
    assert item_hash(_item(date="2026-09-03")) != item_hash(_item(date="2026-09-17"))


def test_hash_changes_with_course():
    assert item_hash(_item(course="A")) != item_hash(_item(course="B"))


def test_select_new_drops_already_seen():
    seen = {item_hash(_item())}
    assert select_new([_item()], seen) == []


def test_select_new_dedupes_within_one_run():
    assert len(select_new([_item(), _item()], set())) == 1


def test_select_new_keeps_distinct_items():
    assert len(select_new([_item(title="a"), _item(title="b")], set())) == 2


# --- notification ------------------------------------------------------------


def test_no_items_means_no_notification():
    assert format_notification([]) is None, "silence means 'checked, nothing new'"


def test_notification_leads_with_important_count():
    items = rank_rules([_item(title="Examen final"), _item(title="ch02.pdf")])
    title, body = format_notification(items)
    assert title.startswith("⚠ 1 important")
    assert "Examen final" in body


def test_notification_lists_courses_for_documents():
    items = [_item(title="a.pdf", course="Calcul"), _item(title="b.pdf", course="Éco")]
    title, _ = format_notification(items)
    assert "2 docs" in title and "Calcul" in title and "Éco" in title


def test_notification_counts_mio():
    title, _ = format_notification([_item(kind="mio", title="Prof", course="")])
    assert "1 MIO" in title


def test_notification_without_important_is_a_plain_summary():
    title, _ = format_notification([_item(title="ch01.pdf")])
    assert "important" not in title


def test_notification_flags_extra_important_items():
    items = rank_rules([_item(title="Examen A"), _item(title="Remise B")])
    _, body = format_notification(items)
    assert "+1" in body


# --- conversion from observations / sync -------------------------------------


def test_items_from_observations_skips_empty_titles():
    raw = [{"kind": "mio", "title": "  "}, {"kind": "mio", "title": "Real", "detail": "Prof"}]
    items = items_from_observations(raw)
    assert len(items) == 1 and items[0].detail == "Prof"


def test_items_from_sync_includes_documents_links_and_flags():
    class R:
        downloaded = [{"filename": "a.pptx", "course_code": "X", "publish_date": "2026-09-03",
                       "converted_pdf": "/p/a.pdf", "description": "Chapitre 1"}]
        links = [{"filename": "L", "description": "Forms", "course_code": "X",
                  "publish_date": "2026-09-03"}]
        conversion_failures = [{"source": "/p/b.pptx", "error": "boom"}]
        errors = [{"scope": "X/c.pdf", "error": "404"}]

    items = items_from_sync(R())
    kinds = [i.kind for i in items]
    assert kinds == ["document", "link", "flag", "flag"]
    assert "converted to a.pdf" in items[0].detail


# --- digest file -------------------------------------------------------------


def test_append_digest_writes_a_dated_section(tmp_path):
    path = append_digest(tmp_path, rank_rules([_item(title="Examen final")]))
    text = path.read_text(encoding="utf-8")
    assert path.name == "_digest.md"
    assert "À noter" in text and "Examen final" in text


def test_append_digest_keeps_non_important_items_too(tmp_path):
    """Spec section 2: the digest is the record of everything observed."""
    path = append_digest(tmp_path, rank_rules([_item(title="ch01.pdf")]))
    assert "ch01.pdf" in path.read_text(encoding="utf-8")


def test_append_digest_appends_rather_than_overwrites(tmp_path):
    append_digest(tmp_path, [_item(title="first")])
    path = append_digest(tmp_path, [_item(title="second")])
    text = path.read_text(encoding="utf-8")
    assert "first" in text and "second" in text
    assert text.count("## ") == 2


# --- run_digest --------------------------------------------------------------


def test_run_digest_records_and_then_stays_silent(loaded_config):
    cfg = loaded_config
    obs = [{"kind": "mio", "title": "Examen mardi", "detail": "Prof X", "date": "2026-09-03"}]
    first = run_digest(cfg, obs)
    assert len(first.items) == 1
    assert first.important and first.notification is not None
    assert (cfg.base_path / "_digest.md").exists()

    second = run_digest(cfg, obs)
    assert second.items == []
    assert second.notification is None, "already-reported items must not re-notify"


def test_run_digest_dry_run_writes_nothing(loaded_config):
    cfg = loaded_config
    obs = [{"kind": "mio", "title": "Examen", "detail": "P", "date": "2026-09-03"}]
    result = run_digest(cfg, obs, dry_run=True)
    assert len(result.items) == 1
    assert not (cfg.base_path / "_digest.md").exists()
    assert StateStore(cfg.repo_root / "state" / "announcements.json").read() == []


def test_run_digest_with_nothing_new_returns_no_notification(loaded_config):
    assert run_digest(loaded_config, []).notification is None


def test_run_digest_merges_sync_output_and_portal_items(loaded_config):
    class R:
        downloaded = [{"filename": "ch01.pdf", "course_code": "601-101-MQ",
                       "publish_date": "2026-09-03", "converted_pdf": None,
                       "description": ""}]
        links = []
        conversion_failures = []
        errors = []

    obs = [{"kind": "quoi_de_neuf", "title": "2 nouveaux documents", "detail": "DINF"}]
    result = run_digest(loaded_config, obs, R())
    kinds = sorted(i.kind for i in result.items)
    assert kinds == ["document", "quoi_de_neuf"]


def test_sync_errors_reach_the_digest(loaded_config):
    """Spec section 2: a failure must never be invisible."""
    class R:
        downloaded = []
        links = []
        conversion_failures = []
        errors = [{"scope": "601-101-MQ", "error": "layout changed"}]

    result = run_digest(loaded_config, [], R())
    assert any(i.kind == "flag" for i in result.items)
    assert "layout changed" in (loaded_config.base_path / "_digest.md").read_text(encoding="utf-8")


def test_digest_is_written_into_the_configured_subfolder(write_config, tmp_repo):
    """Keeps _digest.md out of the top-level course list."""
    from src.common import load_config

    cfg = load_config(
        write_config({"digest": {"ranking": "rules", "folder": "Other"}}), repo_root=tmp_repo
    )
    run_digest(cfg, [{"kind": "mio", "title": "Examen", "detail": "P", "date": "2026-09-03"}])
    assert (cfg.base_path / "Other" / "_digest.md").exists()
    assert not (cfg.base_path / "_digest.md").exists()


# --- readable course labels --------------------------------------------------
# "3 docs (330-704-EM, 350-703-EM)" is unreadable at a glance on a phone.


def test_notification_uses_short_labels_not_codes():
    items = [_item(title="a.pdf", course="330-704-EM"), _item(title="b.pdf", course="350-703-EM")]
    labels = {"330-704-EM": "Histoire", "350-703-EM": "Psycho"}
    title, _ = format_notification(items, labels)
    assert "Histoire" in title and "Psycho" in title
    assert "330-704-EM" not in title and "350-703-EM" not in title


def test_notification_falls_back_to_the_code_when_unlabelled():
    title, _ = format_notification([_item(title="a.pdf", course="999-999-ZZ")], {})
    assert "999-999-ZZ" in title


def test_digest_file_uses_short_labels(tmp_path):
    path = append_digest(tmp_path, rank_rules([_item(title="Examen final", course="330-704-EM")]),
                         {"330-704-EM": "Histoire"})
    text = path.read_text(encoding="utf-8")
    assert "Histoire" in text and "330-704-EM" not in text


def test_label_falls_back_to_first_word_of_the_folder():
    from src.common import Course

    c = Course(code="X", omnivox_name="Y", folder="Histoire du monde", notebook="N")
    assert c.label() == "Histoire"


def test_explicit_short_wins_over_the_folder():
    from src.common import Course

    c = Course(code="X", omnivox_name="Y", folder="Initiation à la psychologie",
               notebook="N", short="Psycho")
    assert c.label() == "Psycho"


def test_run_digest_notification_is_readable(loaded_config):
    """End to end: the push a phone actually shows."""
    class R:
        downloaded = [{"filename": "pc.docx", "course_code": "601-101-MQ",
                       "publish_date": "2026-09-03", "converted_pdf": None, "description": ""}]
        links = []; conversion_failures = []; errors = []

    result = run_digest(loaded_config, [], R())
    title, _ = result.notification
    assert "601-101-MQ" not in title
