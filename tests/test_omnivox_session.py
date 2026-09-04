import pytest

from src.omnivox import LoginError, OmnivoxError, OmnivoxSession, ParseError


def test_exception_hierarchy():
    assert issubclass(LoginError, OmnivoxError)
    assert issubclass(ParseError, OmnivoxError)


def test_session_records_its_dirs(tmp_repo):
    profile = tmp_repo / "state" / "omnivox-profile"
    session = OmnivoxSession(profile, screenshot_dir=tmp_repo / "logs")
    assert session.profile_dir == profile
    assert session.screenshot_dir == tmp_repo / "logs"


@pytest.mark.live
def test_session_opens_and_closes_a_browser(tmp_repo):
    profile = tmp_repo / "state" / "omnivox-profile"
    with OmnivoxSession(profile, screenshot_dir=tmp_repo / "logs") as session:
        session.page.goto("about:blank")
        assert session.page.title() == ""
    assert profile.exists()


@pytest.mark.live
def test_screenshot_writes_a_png(tmp_repo):
    with OmnivoxSession(
        tmp_repo / "state" / "omnivox-profile", screenshot_dir=tmp_repo / "logs"
    ) as session:
        session.page.goto("about:blank")
        shot = session.screenshot("unit-test")
        assert shot.exists()
        assert shot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert shot.parent == tmp_repo / "logs"


@pytest.mark.live
def test_login_rejects_blank_credentials(tmp_repo):
    with OmnivoxSession(tmp_repo / "state" / "p", screenshot_dir=tmp_repo / "logs") as s:
        with pytest.raises(LoginError, match="empty"):
            s.login("", "")


def test_download_rejects_a_document_with_no_ref(tmp_repo):
    from src.omnivox import OmnivoxDocument

    doc = OmnivoxDocument(course_code="A", filename="f.pdf", publish_date="2026-09-03", ref="")
    session = OmnivoxSession(tmp_repo / "state" / "p", screenshot_dir=tmp_repo / "logs")
    with pytest.raises(OmnivoxError, match="no download reference"):
        session.download(doc, tmp_repo / "f.pdf")


def test_mfa_required_is_a_login_error():
    from src.omnivox import MfaRequired

    assert issubclass(MfaRequired, LoginError)
    assert issubclass(MfaRequired, OmnivoxError)


# --- navigation retry --------------------------------------------------------
# Four scheduled runs aborted between 12 and 20 Aug on `Page.goto` 30s timeouts.


class _FlakyPage:
    """Fails `fail_times` times, then succeeds."""

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.attempts = 0
        self.waited = 0

    def goto(self, url, **kw):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise TimeoutError("Page.goto: Timeout 30000ms exceeded.")
        return "ok"

    def wait_for_timeout(self, ms):
        self.waited += ms


def test_goto_succeeds_after_a_transient_timeout():
    from src.omnivox import _goto

    page = _FlakyPage(fail_times=1)
    assert _goto(page, "https://x", retries=3) == "ok"
    assert page.attempts == 2


def test_goto_backs_off_between_attempts():
    from src.omnivox import _goto

    page = _FlakyPage(fail_times=2)
    _goto(page, "https://x", retries=3)
    assert page.waited > 0, "must wait before retrying, not hammer immediately"


def test_goto_gives_up_with_a_clear_error():
    from src.omnivox import OmnivoxError, _goto

    page = _FlakyPage(fail_times=99)
    with pytest.raises(OmnivoxError, match="after 3 attempts"):
        _goto(page, "https://cegepmontpetit.omnivox.ca", retries=3)
    assert page.attempts == 3


def test_goto_does_not_retry_when_it_works_first_time():
    from src.omnivox import _goto

    page = _FlakyPage(fail_times=0)
    _goto(page, "https://x")
    assert page.attempts == 1
    assert page.waited == 0


# --- mandatory consultation interstitial -------------------------------------
# Omnivox blocks navigation with a compulsory survey from time to time. It must
# never break the crawl, and must never be answered on the user's behalf.


class _FakeLocator:
    def __init__(self, n=1, visible=True):
        self._n, self._visible = n, visible
        self.clicked = False

    def count(self):
        return self._n

    def is_visible(self, timeout=None):
        return self._visible

    def click(self):
        self.clicked = True

    @property
    def first(self):
        return self


class _ConsultPage:
    def __init__(self, url, button=None):
        self.url = url
        self._button = button or _FakeLocator(0)

    def get_by_role(self, role, name=None):
        return self._button

    def locator(self, sel):
        return _FakeLocator(0)

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass


def _session_with(page, tmp_repo):
    from src.omnivox import OmnivoxSession

    s = OmnivoxSession(tmp_repo / "state" / "p", screenshot_dir=tmp_repo / "logs")
    s.page = page
    return s


def test_no_consultation_means_no_action(tmp_repo):
    page = _ConsultPage("https://cegepmontpetit.omnivox.ca/intr/")
    assert _session_with(page, tmp_repo).dismiss_consultation() is False


def test_consultation_is_deferred_not_answered(tmp_repo):
    button = _FakeLocator()
    page = _ConsultPage(
        "https://cegepmontpetit-estd.omnivox.ca/estd/SVET/consultation/EffectueConsultation.ovx",
        button,
    )
    assert _session_with(page, tmp_repo).dismiss_consultation() is True
    assert button.clicked, "must click 'Remettre à plus tard'"


def test_a_blocked_consultation_does_not_raise(tmp_repo):
    """No defer button: log and screenshot, never crash the sync."""
    page = _ConsultPage("https://cegepmontpetit-estd.omnivox.ca/estd/SVET/AccesSV.ovx")
    assert _session_with(page, tmp_repo).dismiss_consultation() is False
