import json
from pathlib import Path

import pytest
import yaml
# Every name in this suite is invented. Teachers, classmates and paired devices
# are real people who never agreed to appear in a public repo, and a fixture is
# the easiest place in a codebase to forget that. `make check-private` fails the
# build if a real one from your config.yaml turns up in here.


from src.omnivox import OmnivoxCourse, OmnivoxDocument, OmnivoxError


@pytest.fixture
def sample_config_dict(tmp_path):
    """Minimal valid config with two courses."""
    return {
        "semester": "Automne 2026",
        "base_path": str(tmp_path / "School" / "Cegep Automne 2026"),
        "courses": [
            {
                "code": "601-101-MQ",
                "omnivox_name": "Écriture et littérature",
                "folder": "Écriture et littérature",
                "notebook": "Écriture et littérature - Cegep Automne 2026",
                "record": False,
            },
            {
                "code": "201-103-RE",
                "omnivox_name": "Calcul différentiel",
                "folder": "Calcul différentiel",
                "notebook": "Calcul différentiel - Cegep Automne 2026",
                "record": False,
            },
        ],
        "schedule": [],
        "notify": {"macos": False, "ntfy_topic": ""},
        "digest": {"ranking": "rules"},
        "notebooklm": {"mode": "auto"},
        "at_school_check": "off",
        "school_ip_prefix": "",
    }


@pytest.fixture
def tmp_repo(tmp_path):
    """A repo-shaped temp dir with state/ and logs/."""
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def write_config(tmp_repo, sample_config_dict):
    """Write a config dict to tmp_repo/config.yaml and return its path."""

    def _write(overrides=None):
        data = dict(sample_config_dict)
        if overrides:
            data.update(overrides)
        path = tmp_repo / "config.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def write_env(tmp_repo):
    """Write a .env into tmp_repo."""

    def _write(user="student", password="secret", extra=""):
        path = tmp_repo / ".env"
        path.write_text(
            f"OMNIVOX_USER={user}\nOMNIVOX_PASS={password}\n{extra}", encoding="utf-8"
        )
        return path

    return _write


@pytest.fixture
def read_json():
    def _read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    return _read


class FakeDriver:
    """Stands in for OmnivoxSession. Records calls; writes real bytes on download."""

    def __init__(self, courses=None, documents=None, fail_on=None, content=b"filecontent",
                 videos=None, assignments=None):
        self._courses = courses or []
        self._documents = documents or {}  # course_code -> list[OmnivoxDocument]
        self._assignments = assignments or {}  # course_code -> list[OmnivoxDocument]
        self._fail_on = fail_on or set()  # filenames that raise on download
        self._content = content
        self._videos = videos or {}  # doc filename -> resolved YouTube URL
        self.downloaded: list[tuple[str, str]] = []
        self.resolved: list[str] = []
        self.listed_courses = 0

    def list_courses(self):
        self.listed_courses += 1
        return list(self._courses)

    def list_documents(self, course):
        return list(self._documents.get(course.code, []))

    def list_assignments(self, course):
        """Énoncés de travaux. Defaults to empty so the existing tests, which
        only care about the Documents section, keep asserting exactly what they
        did before."""
        return list(self._assignments.get(course.code, []))

    def resolve_video_url(self, doc):
        self.resolved.append(doc.filename)
        return self._videos.get(doc.filename, "")

    def download(self, doc, dest):
        if doc.filename in self._fail_on:
            raise OmnivoxError(f"simulated download failure for {doc.filename}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._content)
        self.downloaded.append((doc.course_code, dest.name))
        return dest


@pytest.fixture
def fake_driver():
    return FakeDriver


@pytest.fixture
def doc_factory():
    def _make(course="601-101-MQ", filename="ch01.pdf", publish_date="2026-09-03", ref="/r"):
        return OmnivoxDocument(
            course_code=course, filename=filename, publish_date=publish_date, ref=ref
        )

    return _make


@pytest.fixture
def course_factory():
    def _make(code="601-101-MQ", name="Écriture et littérature", href="/lea/a"):
        return OmnivoxCourse(code=code, name=name, href=href)

    return _make


@pytest.fixture
def loaded_config(write_config, tmp_repo):
    from src.common import load_config

    return load_config(write_config(), repo_root=tmp_repo)
