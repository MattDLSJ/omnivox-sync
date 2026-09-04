import pytest

from src.omnivox import parse_publish_date


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-09-03", "2026-09-03"),
        ("03-09-2026", "2026-09-03"),
        ("03/09/2026", "2026-09-03"),
        ("2026/09/03", "2026-09-03"),
        ("3 septembre 2026", "2026-09-03"),
        ("13 décembre 2026", "2026-12-13"),
        ("1 janvier 2027", "2027-01-01"),
        ("Publié le 3 septembre 2026", "2026-09-03"),
        ("2026-09-03 14:22", "2026-09-03"),
        ("  03-09-2026  ", "2026-09-03"),
    ],
)
def test_dates_normalise_to_iso(raw, expected):
    assert parse_publish_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "not a date", "Nouveau"])
def test_unparseable_dates_return_empty_string(raw):
    assert parse_publish_date(raw) == ""


def test_all_twelve_french_months_parse():
    months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
              "août", "septembre", "octobre", "novembre", "décembre"]
    for index, month in enumerate(months, start=1):
        assert parse_publish_date(f"5 {month} 2026") == f"2026-{index:02d}-05"


def test_french_months_parse_without_accents():
    assert parse_publish_date("5 fevrier 2026") == "2026-02-05"
    assert parse_publish_date("5 decembre 2026") == "2026-12-05"


def test_parsing_is_idempotent():
    once = parse_publish_date("3 septembre 2026")
    assert parse_publish_date(once) == once


def test_ambiguous_ddmm_prefers_day_first():
    assert parse_publish_date("05-09-2026") == "2026-09-05"


# --- Real LEA formats, captured live 2026-08-06 ------------------------------
# The documents list renders "depuis le4 fév 2026": abbreviated month, and the
# day glued to "le". Both broke the original parser.


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("depuis le4 fév 2026", "2026-02-04"),
        ("depuis le22 jan 2026", "2026-01-22"),
        ("depuis le5 mar 2026", "2026-03-05"),
        ("depuis le8 avr 2026", "2026-04-08"),
        ("depuis le1 mai 2026", "2026-05-01"),
        ("depuis le7 mai 2026", "2026-05-07"),
        ("Chapitre 1 depuis le4 fév 2026 ch01_presentation... 1.1 Mo", "2026-02-04"),
    ],
)
def test_real_lea_document_dates(raw, expected):
    assert parse_publish_date(raw) == expected


@pytest.mark.parametrize(
    "abbrev,month",
    [("jan", 1), ("fév", 2), ("mar", 3), ("avr", 4), ("mai", 5), ("juin", 6),
     ("juil", 7), ("août", 8), ("sept", 9), ("oct", 10), ("nov", 11), ("déc", 12)],
)
def test_every_abbreviated_month_parses(abbrev, month):
    assert parse_publish_date(f"depuis le9 {abbrev} 2026") == f"2026-{month:02d}-09"


def test_abbreviation_with_trailing_period():
    assert parse_publish_date("9 fév. 2026") == "2026-02-09"


def test_a_document_date_is_never_silently_empty_for_known_formats():
    """Guards the failure mode that would re-download the semester every run."""
    for raw in ("depuis le4 fév 2026", "4 février 2026", "2026-02-04", "04-02-2026"):
        assert parse_publish_date(raw) != "", raw


# --------------------------------------------------------------------------
# Filenames that a Mac accepts and Windows refuses
# --------------------------------------------------------------------------


def test_a_colon_in_a_teacher_s_filename_survives_ntfs():
    """The failure this prevents is nasty because it is late and misleading.

    A colon is legal on macOS, so "Chapitre 3: la mondialisation.pdf"
    downloads fine here. On Windows the transfer completes and only then does
    the write fail, with an error that reads like a network fault.
    """
    from src.omnivox_sync import safe_filename

    assert safe_filename("Chapitre 3: la mondialisation.pdf") == (
        "Chapitre 3_ la mondialisation.pdf"
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('quoi?.pdf', "quoi_.pdf"),
        ('notes*.docx', "notes_.docx"),
        ('a<b>c.pdf', "a_b_c.pdf"),
        ('x|y.pptx', "x_y.pptx"),
        ('say "hi".pdf', "say _hi_.pdf"),
    ],
)
def test_every_character_ntfs_forbids_is_replaced(raw, expected):
    from src.omnivox_sync import safe_filename

    assert safe_filename(raw) == expected


def test_a_trailing_dot_or_space_is_dropped():
    """Windows drops them silently, so the name you asked for and the name on
    disk stop being the same string, and the dedup key stops matching."""
    from src.omnivox_sync import safe_filename

    assert safe_filename("notes.pdf ") == "notes.pdf"
    assert safe_filename("notes.pdf.") == "notes.pdf"


def test_a_reserved_device_name_is_defused():
    """CON.pdf is not a file on Windows, it is the console."""
    from src.omnivox_sync import safe_filename

    assert safe_filename("CON.pdf") == "CON_.pdf"
    assert safe_filename("com1.docx") == "com1_.docx"
    assert safe_filename("console.pdf") == "console.pdf"


def test_accents_are_kept():
    """Legal everywhere this runs, and stripping them would mangle most of the
    French course material."""
    from src.omnivox_sync import safe_filename

    assert safe_filename("Énoncé de travail — été.pdf") == "Énoncé de travail — été.pdf"


def test_it_still_refuses_to_escape_the_course_folder():
    """The original job of this function, unchanged."""
    from src.omnivox_sync import safe_filename

    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("..") == "unnamed_document"
    assert safe_filename("") == "unnamed_document"
