"""Teacher MIO, filed into the course its sender teaches.

Omnivox's internal mail is one flat inbox: fifty messages, of which eight were
from a teacher and the rest were student-association blasts and invitations to
socioculturelle activities. The eight are course material and never reached a
folder; the rest are not, and filing them next to the lecture slides would make
the folder worse.
"""

import pytest

from src.mio import match_course, render, safe_name, split_subject, sync_mio


class Course:
    def __init__(self, code, teacher, folder="F", notebook="N"):
        self.code, self.teacher, self.folder, self.notebook = code, teacher, folder, notebook


class FakeDriver:
    def __init__(self, messages=None, raises=None):
        self._messages = messages if messages is not None else [{
            "id": "guid-1", "sender": "Jordan Tremblay", "date": "2026-09-08",
            "preview": "Rappel du devoir Bonjour tout le monde, lisez le chapitre 4.",
            "unread": False,
        }]
        self._raises = raises
        self.opened: list[int] = []

    def list_mio(self, *, with_bodies=False):
        if self._raises:
            raise self._raises
        return list(self._messages)

    def read_mio_body(self, message_id):
        """Opening a message is what marks it read, so the tests count these.

        Keyed by id, never by position: the real list skips rows and reorders
        them as they are read, so an index opened somebody else's message.
        """
        self.opened.append(message_id)
        found = next((m for m in self._messages if m.get("id") == message_id), {})
        return found.get("body", ""), found.get("course_code", "")


# --------------------------------------------------------- splitting the cell


@pytest.mark.parametrize("preview,subject", [
    ("Rappel du devoir Bonjour tout le monde, j'espère", "Rappel du devoir"),
    ("Rechch quali : devoirs pour le prochain cours Très cher·es étudiant·es",
     "Rechch quali : devoirs pour le prochain cours"),
    ("Rappel : Examen Bonjour, ceci est pour vous rappeler", "Rappel : Examen"),
])
def test_the_subject_is_split_off_the_glued_preview(preview, subject):
    """LÉA renders the subject and the start of the body into one cell with
    nothing between them, so a French letter's greeting is the only boundary
    there is."""
    assert split_subject(preview)[0] == subject


def test_a_message_with_no_greeting_still_gets_a_subject():
    got, _body = split_subject("Un sujet sans salutation du tout")
    assert got and len(got) <= 90


def test_an_empty_preview_does_not_crash():
    assert split_subject("") == ("", "")


def test_a_greeting_inside_the_first_word_is_not_a_boundary():
    """Splitting at position 0 would leave an empty subject and a filename of
    nothing."""
    subject, _ = split_subject("Bonjour tout le monde, voici le plan")
    assert subject


# ------------------------------------------------------------------ matching


def test_a_message_from_your_teacher_finds_its_course():
    courses = [Course("340-101-MQ", "Jordan Tremblay")]
    assert match_course("Jordan Tremblay", courses).code == "340-101-MQ"


def test_accents_do_not_have_to_match_exactly():
    """A name typed one way in config and another by Omnivox still matches,
    which is not hypothetical in a list of cégep teacher names."""
    courses = [Course("X", "Éloïse Sainte-Marie-Gagnon")]
    assert match_course("eloise sainte-marie-gagnon", courses) is not None


def test_the_rest_of_the_inbox_is_left_alone():
    """An invitation to a socioculturelle activity is not course material."""
    courses = [Course("X", "Jordan Tremblay")]
    for stranger in ("Association étudiante AGECEM", "Plus Sciences humaines",
                     "examen-cem CSA", ""):
        assert match_course(stranger, courses) is None


def test_a_course_with_no_teacher_configured_matches_nobody():
    """Otherwise an empty teacher field would swallow the whole inbox."""
    assert match_course("Anyone At All", [Course("X", "")]) is None


# ------------------------------------------------------------------- saving


def test_a_teacher_message_is_saved_and_queued(loaded_config):
    course = loaded_config.courses[0]
    driver = FakeDriver([{
        "id": "g1", "sender": course.teacher, "date": "2026-09-08",
        "preview": "Rappel du devoir Bonjour, lisez le chapitre 4.", "unread": False,
    }])
    queued = sync_mio(loaded_config, driver, logger=None)

    assert len(queued) == 1
    folder = loaded_config.folder_for(course) / loaded_config.mio_folder
    saved = list(folder.glob("*.md"))
    assert len(saved) == 1
    assert saved[0].name == "2026-09-08 - Rappel du devoir.md"
    assert "lisez le chapitre 4" in saved[0].read_text(encoding="utf-8")


def test_nothing_is_saved_twice(loaded_config):
    """Every message stays in the inbox for the rest of the semester."""
    course = loaded_config.courses[0]
    driver = FakeDriver([{
        "id": "g1", "sender": course.teacher, "date": "2026-09-08",
        "preview": "Rappel Bonjour", "unread": False,
    }])
    assert len(sync_mio(loaded_config, driver, logger=None)) == 1
    assert sync_mio(loaded_config, driver, logger=None) == []


def test_a_dry_run_writes_nothing(loaded_config, tmp_repo):
    course = loaded_config.courses[0]
    driver = FakeDriver([{
        "id": "g1", "sender": course.teacher, "date": "2026-09-08",
        "preview": "Rappel Bonjour", "unread": False,
    }])
    sync_mio(loaded_config, driver, dry_run=True, logger=None)
    folder = loaded_config.folder_for(course) / loaded_config.mio_folder
    assert not folder.exists()


def test_a_failure_reading_the_inbox_does_not_cost_the_run(loaded_config):
    driver = FakeDriver(raises=RuntimeError("MIO frame not found"))
    assert sync_mio(loaded_config, driver, logger=None) == []


def test_the_saved_file_says_it_may_be_truncated():
    """It is the inbox preview, not the message. Saying so is the difference
    between a note and a wrong answer."""
    out = render("Jordan Tremblay", "Rappel", "2026-09-08", "Bonjour, lisez", "X-1")
    assert "may be cut off" in out
    assert "mark it read" in out


def test_filenames_survive_a_subject_with_a_colon_in_it():
    assert ":" not in safe_name("Rappel : Examen")


# --------------------------------------------------- the full-body switch


DETAIL = """De
Jordan Tremblay (601-101-MQ gr.1060 (A2026))
Répondre
Transférer
Supprimer
Imprimer
À (masqués)
 A Student Name	
Date
Jeu 10-sep-2026 à 11:09 - il y a 2 heures
Ouvrage
Bonjour,

Il faudra avoir le livre pour jeudi.

Merci"""


def test_the_detail_pane_is_parsed_into_its_parts():
    from src.mio import parse_detail

    got = parse_detail(DETAIL)
    assert got["subject"] == "Ouvrage"
    assert got["sender"].startswith("Jordan Tremblay")
    assert "Il faudra avoir le livre" in got["body"]


def test_the_recipient_block_is_not_saved():
    """It is the reader's own name, and it would be in every single file."""
    from src.mio import parse_detail

    assert "A Student Name" not in parse_detail(DETAIL)["body"]
    assert "Répondre" not in parse_detail(DETAIL)["body"]


def test_the_hyphenated_detail_date_is_understood():
    """The detail pane writes 10-sep-2026 where the rest of Omnivox writes
    10 sep 2026, and the shared parser only takes the spaced form."""
    from src.mio import detail_date

    assert detail_date("Jeu 10-sep-2026 à 11:09 - il y a 2 heures") == "2026-09-10"
    assert detail_date("nothing dateish") == ""


def test_no_message_is_opened_unless_configured(loaded_config):
    driver = FakeDriver()
    sync_mio(loaded_config, driver, logger=None)
    assert driver.opened == [], "opening a message is what marks it read"


def test_only_a_teacher_message_is_ever_opened(loaded_config):
    """On a real inbox this was 50 messages opened at 2.5 s each to keep 7,
    three times a day, for the rest of the semester."""
    object.__setattr__(loaded_config, "mio_full_bodies", True)
    course = loaded_config.courses[0]
    driver = FakeDriver([
        {"id": "a", "sender": "Association étudiante AGECEM", "date": "",
         "preview": "Venez au party", "unread": False, "index": 0},
        {"id": "b", "sender": course.teacher, "date": "2026-09-08",
         "preview": "Devoir Bonjour", "unread": False, "index": 1, "body": DETAIL,
         "course_code": course.code},
    ])
    sync_mio(loaded_config, driver, logger=None)
    assert driver.opened == ["b"], "the association blast must not be opened"


def test_a_message_already_saved_is_not_opened_again(loaded_config):
    """The expensive half must not be paid twice for the same message."""
    object.__setattr__(loaded_config, "mio_full_bodies", True)
    course = loaded_config.courses[0]
    messages = [{"id": "b", "sender": course.teacher, "date": "2026-09-08",
                 "preview": "Devoir Bonjour", "unread": False, "index": 0,
                 "body": DETAIL, "course_code": course.code}]
    driver = FakeDriver(list(messages))
    sync_mio(loaded_config, driver, logger=None)
    assert driver.opened == ["b"]

    again = FakeDriver(list(messages))
    sync_mio(loaded_config, again, logger=None)
    assert again.opened == [], "already saved, so there is nothing to open it for"


def test_a_full_body_is_saved_without_the_truncation_note(loaded_config):
    """Saying "this may be cut off" on a complete message would teach people
    to distrust the ones that are fine."""
    object.__setattr__(loaded_config, "mio_full_bodies", True)
    course = loaded_config.courses[0]
    driver = FakeDriver([{
        "id": "g9", "sender": course.teacher, "date": "", "preview": "Ouvrage Bonjour",
        "unread": False, "index": 0, "body": DETAIL, "course_code": course.code,
    }])
    sync_mio(loaded_config, driver, logger=None)

    folder = loaded_config.folder_for(course) / loaded_config.mio_folder
    saved = list(folder.glob("*.md"))[0]
    text = saved.read_text(encoding="utf-8")
    assert "Il faudra avoir le livre" in text
    assert "may be cut off" not in text
    assert saved.name.startswith("2026-09-10 - "), "the date comes from the message"


def test_the_course_code_in_the_message_beats_matching_the_name(loaded_config):
    """An opened message names its own course outright."""
    object.__setattr__(loaded_config, "mio_full_bodies", True)
    course = loaded_config.courses[0]
    driver = FakeDriver([{
        "id": "g10", "sender": course.teacher, "date": "",
        "preview": "x", "unread": False, "index": 0, "body": DETAIL,
        "course_code": course.code,
    }])
    assert len(sync_mio(loaded_config, driver, logger=None)) == 1


def test_the_course_is_not_repeated_in_the_byline():
    """The detail pane appends the course to the sender, and the byline
    already carries the code, so every file read
    "300-204-EM · Jordan Tremblay (300-204-EM gr.1040 (A2026))"."""
    from src.mio import clean_sender

    assert clean_sender("Jordan Tremblay (340-101-MQ gr.1060 (A2026))") == "Jordan Tremblay"
    assert clean_sender("Jordan Tremblay") == "Jordan Tremblay"


def test_a_stranger_is_not_filed_even_when_the_message_names_a_course(loaded_config):
    """This one really happened. An activity invitation and two administrative
    notices were filed into three different course folders, because the opened
    message named a course and that was allowed to override the sender check.
    Being addressed to a class does not make something course material.
    """
    object.__setattr__(loaded_config, "mio_full_bodies", True)
    course = loaded_config.courses[0]
    driver = FakeDriver([{
        "id": "stranger", "sender": "Association étudiante AGECEM", "date": "",
        "preview": "Inscriptions activités socioculturelles", "unread": False,
        "index": 0, "body": DETAIL, "course_code": course.code,
    }])

    assert sync_mio(loaded_config, driver, logger=None) == []
    assert driver.opened == [], "and it must not be opened to find that out"


def test_a_message_is_opened_by_id_and_never_by_position(loaded_config):
    """This one really happened, twice over. The listing skips rows, so its
    numbering already differed from the page's, and clicking a row marks it
    read, which changes its class and shifts every row after it. Each message
    opened its neighbour's contents, so a teacher's name from the list ended
    up above a stranger's message on disk.
    """
    object.__setattr__(loaded_config, "mio_full_bodies", True)
    course = loaded_config.courses[0]
    driver = FakeDriver([{
        "id": "the-guid", "sender": course.teacher, "date": "",
        "preview": "Devoir Bonjour", "unread": False, "index": 7,
        "body": DETAIL, "course_code": course.code,
    }])

    sync_mio(loaded_config, driver, logger=None)

    assert driver.opened == ["the-guid"], "an index would have been used here"
