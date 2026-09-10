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

    def list_mio(self):
        if self._raises:
            raise self._raises
        return list(self._messages)


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
