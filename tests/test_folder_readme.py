"""The note that tells a session opened in a course folder what it can do.

Twice a session working out of a course folder concluded that a textbook was
unavailable, once declining on copyright grounds, while the project could have
fetched it from an i+ Interactif account the student pays for, a chapter at a
time. Nothing in the folder said the automation existed, so nothing could
correct it. The folder had a schedule and a digest and no answer to "what is
this, and what can be done about it".
"""

from pathlib import Path

from src.folder_readme import NAME, course_readme, root_readme, write_readmes


def test_the_note_names_the_project_folder(loaded_config):
    """A path an agent can cd to is the whole point; without it the commands
    below are unrunnable."""
    body = course_readme(loaded_config, loaded_config.courses[0], Path("/x/repo"))
    assert "/x/repo" in body


def test_an_empty_book_folder_is_explained_rather_than_left_to_inference(loaded_config):
    body = course_readme(loaded_config, loaded_config.courses[0], Path("/x/repo"))
    assert "Empty does not mean unavailable" in body
    assert "make books" in body
    assert "fair dealing" in body


def test_it_says_what_to_do_when_the_book_is_not_on_the_account(loaded_config):
    """The other half of the honest answer: if it is not there, stop."""
    body = course_readme(loaded_config, loaded_config.courses[0], Path("/x/repo"))
    assert "do not look for it elsewhere" in body


def test_it_refuses_to_license_making_things_up(loaded_config):
    body = course_readme(loaded_config, loaded_config.courses[0], Path("/x/repo"))
    assert "presenting the result as the source" in body


def test_the_course_is_identified_by_code_and_teacher(loaded_config):
    course = loaded_config.courses[0]
    body = course_readme(loaded_config, course, Path("/x/repo"))
    assert course.code in body
    if course.teacher:
        assert course.teacher in body


def test_the_folder_names_come_from_config(loaded_config):
    """A note naming folders that do not exist is worse than none."""
    body = course_readme(loaded_config, loaded_config.courses[0], Path("/x/repo"))
    for folder in (loaded_config.mio_folder, loaded_config.communiques_folder,
                   loaded_config.books_folder):
        assert folder in body


def test_the_root_note_lists_every_course(loaded_config):
    body = root_readme(loaded_config, Path("/x/repo"))
    for course in loaded_config.courses:
        assert course.folder in body


def test_writing_puts_one_in_each_course_and_one_at_the_root(loaded_config):
    written = write_readmes(loaded_config, logger=None)
    assert written == len(loaded_config.courses) + 1
    assert (Path(loaded_config.base_path) / NAME).is_file()
    for course in loaded_config.courses:
        assert (loaded_config.folder_for(course) / NAME).is_file()


def test_a_dry_run_writes_nothing(loaded_config):
    write_readmes(loaded_config, dry_run=True, logger=None)
    assert not (Path(loaded_config.base_path) / NAME).exists()


def test_it_is_rewritten_rather_than_appended(loaded_config):
    """It is regenerated every sync, so a stale copy must not survive."""
    write_readmes(loaded_config, logger=None)
    path = Path(loaded_config.base_path) / NAME
    path.write_text("something a person typed", encoding="utf-8")
    write_readmes(loaded_config, logger=None)
    assert "something a person typed" not in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Which courses are worth recording
#
# Recording everything is the obvious default and the wrong one. A gym class
# produces forty minutes of a bouncing ball, transcribed into a notebook beside
# the philosophy lectures, and whoever set it up learns not to trust the
# notebook.
# ---------------------------------------------------------------------------


class _Course:
    def __init__(self, code, folder, omnivox_name="", teacher="", group=""):
        self.code, self.folder = code, folder
        self.omnivox_name = omnivox_name or folder
        self.teacher, self.group = teacher, group


def test_a_physical_education_course_is_not_worth_recording():
    """109 is éducation physique at every Quebec cégep, every student takes
    three, and none of them is a lecture."""
    from src.folder_readme import worth_recording

    assert worth_recording(_Course("109-321-EM", "Basketball")) is False
    assert worth_recording(_Course("109-101-MQ", "Activité physique")) is False


def test_a_lecture_course_is():
    from src.folder_readme import worth_recording

    assert worth_recording(_Course("340-101-MQ", "Philosophie et rationalité"))
    assert worth_recording(_Course("601-102-MQ", "Littérature et imaginaire"))


def test_the_name_is_enough_even_when_the_code_is_not():
    """Not every college numbers its disciplines the same way."""
    from src.folder_readme import worth_recording

    assert worth_recording(_Course("999-111-XX", "Natation avancée")) is False


def test_the_note_tells_the_reader_which_one_this_is(loaded_config):
    from src.folder_readme import course_readme

    gym = _Course("109-321-EM", "Basketball")
    assert "Do not record this one" in course_readme(loaded_config, gym, Path("/x"))

    lecture = _Course("340-101-MQ", "Philosophie")
    assert "Do not record this one" not in course_readme(
        loaded_config, lecture, Path("/x")
    )


def test_the_brief_does_not_assume_one_calendar_provider():
    """A cégep hands out an Outlook address, and a tester connected Microsoft
    because the free tier of their agent had no Google support. Naming one
    provider makes the other look unsupported when nothing here cares."""
    from pathlib import Path as _Path

    brief = _Path("START-HERE.md").read_text(encoding="utf-8")
    start = brief.index("My calendar, through you")
    section = brief[start:start + 2200]
    assert "Microsoft" in section, "the alternative has to be named to be believed"
    assert "wrong Google account" not in section


def test_the_front_page_points_at_the_file_not_the_repository():
    """Tested on two agents. A link to the repository made one of them ask
    "what would you like me to do with it" and wait, because a repository does
    not say what it is for. A link to a document is unambiguous. Reverting this
    to the tidier repository URL would break it again on that agent, silently
    and only for some people, which is why the reason is pinned here too."""
    from pathlib import Path as _Path

    readme = _Path("README.md").read_text(encoding="utf-8")
    top = readme[:1800]
    assert "blob/main/INSTALL.md" in top
    assert "not at the repository" in top
