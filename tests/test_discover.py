import yaml

from src.omnivox import OmnivoxCourse
from src.omnivox_sync import folder_name_from, render_discovery_yaml

COURSES = [
    OmnivoxCourse(code="601-101-MQ", name="Écriture et littérature", href="/lea/a"),
    OmnivoxCourse(code="201-103-RE", name="Calcul différentiel", href="/lea/b"),
]


def test_render_produces_parseable_yaml():
    parsed = yaml.safe_load(render_discovery_yaml(COURSES, "Automne 2026"))
    assert [c["code"] for c in parsed["courses"]] == ["601-101-MQ", "201-103-RE"]


def test_every_required_course_key_is_present():
    parsed = yaml.safe_load(render_discovery_yaml(COURSES, "Automne 2026"))
    for course in parsed["courses"]:
        assert set(course) == {"code", "omnivox_name", "folder", "notebook", "record"}
        # Was hardcoded False for every course, which is safe and useless:
        # nobody hand-edits six YAML entries, so nothing was ever opted in and
        # the recorder could not run for anyone who did not already know it
        # existed. worth_recording decides now, and it is the same rule the
        # per-course README already states, so the two cannot disagree.
        assert isinstance(course["record"], bool)


def test_notebook_name_includes_the_semester():
    parsed = yaml.safe_load(render_discovery_yaml(COURSES, "Automne 2026"))
    assert parsed["courses"][0]["notebook"] == "Écriture et littérature - Cegep Automne 2026"


def test_folder_defaults_to_the_display_name():
    parsed = yaml.safe_load(render_discovery_yaml(COURSES, "Automne 2026"))
    assert parsed["courses"][0]["folder"] == "Écriture et littérature"


def test_accents_survive_yaml_round_trip():
    assert "Écriture et littérature" in render_discovery_yaml(COURSES, "Automne 2026")


def test_rendered_block_passes_load_config(tmp_repo, sample_config_dict):
    from src.common import load_config

    parsed = yaml.safe_load(render_discovery_yaml(COURSES, "Automne 2026"))
    merged = dict(sample_config_dict, courses=parsed["courses"])
    path = tmp_repo / "config.yaml"
    path.write_text(yaml.safe_dump(merged, allow_unicode=True), encoding="utf-8")
    cfg = load_config(path, repo_root=tmp_repo)
    assert [c.code for c in cfg.courses] == ["601-101-MQ", "201-103-RE"]


def test_folder_name_strips_slashes_and_whitespace():
    assert folder_name_from("  Calcul / Algèbre  ") == "Calcul - Algèbre"
    assert folder_name_from("Économie") == "Économie"


def test_folder_name_never_produces_a_traversal():
    assert "/" not in folder_name_from("../etc")
    assert folder_name_from("..") == "unnamed course"


def test_empty_course_list_renders_valid_empty_yaml():
    parsed = yaml.safe_load(render_discovery_yaml([], "Automne 2026"))
    assert parsed["courses"] == []


# --- humanize_course_name: LEA renders course titles in caps -----------------

import pytest

from src.omnivox_sync import humanize_course_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ÉCRITURE ET LITTÉRATURE", "Écriture et littérature"),
        ("INITIATION À L'ÉCONOMIE", "Initiation à l'économie"),
        ("L'ENTREPRISE", "L'entreprise"),
        ("MISE EN FORME  RYTHMÉE", "Mise en forme rythmée"),
        ("POUVOIRS, IDÉOLOGIES ET ENJEUX DÉMOCRATIQUES",
         "Pouvoirs, idéologies et enjeux démocratiques"),
        ("CALCUL DIFFÉRENTIEL POUR LES SCIENCES HUMAINES",
         "Calcul différentiel pour les sciences humaines"),
    ],
)
def test_caps_names_become_french_sentence_case(raw, expected):
    """These are the real Winter 2026 titles; the output must match the
    folder names already on disk."""
    assert humanize_course_name(raw) == expected


def test_server_side_truncation_ellipsis_is_stripped():
    assert humanize_course_name("MÉTHODES DE TRAVAIL INTELLECTUEL EN SCIENCES...") == (
        "Méthodes de travail intellectuel en sciences"
    )


def test_already_mixed_case_names_are_left_alone():
    assert humanize_course_name("Calcul différentiel") == "Calcul différentiel"
    assert humanize_course_name("Physique NYA") == "Physique NYA"


def test_whitespace_is_collapsed():
    assert humanize_course_name("  A   B  ") == "A b"


def test_empty_name_does_not_crash():
    assert humanize_course_name("") == ""
    assert humanize_course_name("...") == ""


def test_notebook_name_uses_the_humanized_form():
    caps = [OmnivoxCourse(code="601-101-MQ", name="ÉCRITURE ET LITTÉRATURE", href="/a")]
    parsed = yaml.safe_load(render_discovery_yaml(caps, "Automne 2026"))
    course = parsed["courses"][0]
    assert course["folder"] == "Écriture et littérature"
    assert course["notebook"] == "Écriture et littérature - Cegep Automne 2026"
    assert course["omnivox_name"] == "ÉCRITURE ET LITTÉRATURE", "keep LEA's exact label"


# --- Roman numerals: real Fall 2026 course title -----------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HISTOIRE DU MONDE, DU XVE SIÈCLE À NOS JOURS",
         "Histoire du monde, du XVe siècle à nos jours"),
        ("LE XXI SIECLE", "Le XXI siecle"),
        ("GUERRE DU XIXE SIÈCLE", "Guerre du XIXe siècle"),
        ("TOME II", "Tome II"),
        ("PARTIE III: SUITE", "Partie III: suite"),
    ],
)
def test_roman_numerals_stay_uppercase(raw, expected):
    assert humanize_course_name(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        # These are ordinary words that are ALSO valid roman numeral strings.
        ("DIX ANS DE DROIT", "Dix ans de droit"),
        ("MILLE ET UNE NUITS", "Mille et une nuits"),
        ("CIL ET VIE", "Cil et vie"),
        ("SCIENCES DE LA VIE", "Sciences de la vie"),
        ("IVRE MORT", "Ivre mort"),
        ("LA VILLE ET LE CIVIL", "La ville et le civil"),
        ("MISE EN FORME RYTHMÉE", "Mise en forme rythmée"),
    ],
)
def test_ordinary_french_words_are_not_shouted(raw, expected):
    assert humanize_course_name(raw) == expected


def test_single_letters_are_left_alone():
    assert humanize_course_name("PLAN C ET D") == "Plan c et d"


def test_the_real_fall_2026_titles_round_trip():
    """The seven Automne 2026 courses, as LEA actually renders them."""
    cases = {
        "BASKETBALL": "Basketball",
        "CALCUL INTÉGRAL POUR LES SCIENCES HUMAINES":
            "Calcul intégral pour les sciences humaines",
        "RECHERCHE QUALITATIVE EN SCIENCES HUMAINES":
            "Recherche qualitative en sciences humaines",
        "HISTOIRE DU MONDE, DU XVE SIÈCLE À NOS JOURS":
            "Histoire du monde, du XVe siècle à nos jours",
        "PHILOSOPHIE ET RATIONALITÉ": "Philosophie et rationalité",
        "INITIATION À LA PSYCHOLOGIE": "Initiation à la psychologie",
        "LITTÉRATURE ET IMAGINAIRE": "Littérature et imaginaire",
    }
    for raw, expected in cases.items():
        assert humanize_course_name(raw) == expected, raw


# --- shortening long titles --------------------------------------------------

from src.omnivox_sync import shorten_course_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The real Automne 2026 titles.
        ("Calcul intégral pour les sciences humaines", "Calcul intégral"),
        ("Recherche qualitative en sciences humaines", "Recherche qualitative"),
        ("Histoire du monde, du XVe siècle à nos jours", "Histoire du monde"),
        # Short enough already: untouched.
        ("Philosophie et rationalité", "Philosophie et rationalité"),
        ("Initiation à la psychologie", "Initiation à la psychologie"),
        ("Littérature et imaginaire", "Littérature et imaginaire"),
        ("Basketball", "Basketball"),
        ("Écriture et littérature", "Écriture et littérature"),
    ],
)
def test_long_titles_are_trimmed_to_their_head(raw, expected):
    assert shorten_course_name(raw) == expected


def test_a_cut_that_would_leave_too_little_is_rejected():
    """'Pouvoirs, idéologies...' must not collapse to 'Pouvoirs'."""
    raw = "Pouvoirs, idéologies et enjeux démocratiques"
    assert shorten_course_name(raw) == raw


def test_shortening_flows_through_folder_and_notebook_names():
    courses = [OmnivoxCourse(code="330-704-EM",
                             name="HISTOIRE DU MONDE, DU XVE SIÈCLE À NOS JOURS", href="/a")]
    parsed = yaml.safe_load(render_discovery_yaml(courses, "Fall 2026"))[
        "courses"][0]
    assert parsed["folder"] == "Histoire du monde"
    assert parsed["notebook"] == "Histoire du monde - Cegep Fall 2026"
    assert parsed["omnivox_name"] == "HISTOIRE DU MONDE, DU XVE SIÈCLE À NOS JOURS"


def test_discovery_does_not_offer_to_record_a_gym_class():
    """The spec item was "intelligently do not record gym classes". The rule
    was already written and already correct; it was wired to a paragraph of
    prose in the course README and to nothing else."""
    from types import SimpleNamespace

    from src.omnivox_sync import _discovered_entry

    gym = SimpleNamespace(code="109-321-EM", name="Activité physique et sportive")
    lecture = SimpleNamespace(code="340-101-MQ", name="Philosophie et rationalité")
    assert _discovered_entry(gym, "Fall 2026")["record"] is False
    assert _discovered_entry(lecture, "Fall 2026")["record"] is True
