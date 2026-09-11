"""Playwright driver for Omnivox / LEA.

The only module that touches a browser. Returns plain typed records so
orchestration stays testable without one.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import Page, sync_playwright

DEFAULT_TIMEOUT_MS = 30_000


def _strip_leading_date(text: str) -> str:
    """"8 sepÀ compléter avant le cours" -> "À compléter avant le cours".

    The card renders the date and the title as two spans with no separator, so
    they arrive glued together with no space to split on.
    """
    return re.sub(
        r"^\s*\d{1,2}\s*"
        r"(janv?|f[ée]vr?|mars|avr|mai|juin|juil|ao[uû]t|sept?|oct|nov|d[ée]c)[a-z]*\.?\s*",
        "",
        text,
        flags=re.I,
    ).strip()


@dataclass(frozen=True)
class Portal:
    """Which Omnivox this talks to, and what the buttons are called on it.

    Omnivox is one product, sold by Skytech to nearly every cégep in Quebec,
    so the machinery is identical everywhere: the same ASPX page names, the
    same CSS classes, the same login form. Two things do differ, and they are
    the only two things in here.

    The HOST. Each school gets `<school>.omnivox.ca` for the portal and
    `<school>-lea.omnivox.ca` for LÉA, which are different origins and must
    not be confused: resolving a LÉA path against the portal is a 404 on every
    course, and this project has made that mistake once already.

    The LANGUAGE. Navigation happens by clicking visible text, so the French
    interface is wired in by default. An English-language college on the same
    Omnivox (Dawson, Vanier, John Abbott) needs these six strings swapped, and
    nothing else. Everything not in this class is language-independent.
    """

    school: str = "cegepmontpetit"
    home: str = ""
    lea: str = ""

    docs_link: str = "Documents et vidéos"
    travaux_link: str = "Énoncés distribués"
    docs_nav: tuple[str, ...] = (
        "Documents distribués",
        "Documents et vidéos",
        "Documents",
    )
    lea_link_name: str = "Léa"
    logged_in_text: str = "Quoi de neuf"

    def __post_init__(self) -> None:
        # Derived, but overridable: a school whose hostname does not follow the
        # pattern can set `home` and `lea` outright.
        if not self.home:
            object.__setattr__(self, "home", f"https://{self.school}.omnivox.ca")
        if not self.lea:
            object.__setattr__(self, "lea", f"https://{self.school}-lea.omnivox.ca")


DEFAULT_PORTAL = Portal()

#: Kept because the whole codebase and its tests refer to them, and because a
#: single-school checkout has exactly one portal.
OMNIVOX_HOME = DEFAULT_PORTAL.home
LEA_HOME = DEFAULT_PORTAL.lea


class OmnivoxError(Exception):
    """Base class for every Omnivox site failure."""


class LoginError(OmnivoxError):
    """Credentials rejected, or the login form could not be completed."""


class MfaRequired(LoginError):
    """Omnivox is asking for a 6-digit identity-validation code.

    Credentials were accepted; the session just is not trusted on this profile.
    Resolved by a one-time headed bootstrap (`--login`) where the user enters
    the emailed code and ticks "appareil de confiance". The trust cookie then
    lives in the persistent profile and headless runs stop being challenged.
    """


class ParseError(OmnivoxError):
    """The page loaded but did not have the structure we expect."""


@dataclass(frozen=True)
class OmnivoxCourse:
    code: str
    name: str
    href: str


@dataclass(frozen=True)
class OmnivoxDocument:
    course_code: str
    filename: str
    publish_date: str  # ISO YYYY-MM-DD; "" when the page shows no parseable date
    ref: str  # opaque handle used by download(): an href or a row selector
    description: str = ""  # the teacher's label for the row, e.g. "Chapitre 1"
    doc_id: str = ""  # LEA's stable IDDocCoursDocument GUID, recorded for debugging
    is_link: bool = False  # an external web link, not a downloadable file
    # A video the teacher embedded rather than uploaded. LEA serves these
    # through VisualiseVideo.aspx, whose anchor is a javascript: call and whose
    # icon is a YouTube thumbnail, so NONE of the file selectors match it and
    # the row was invisible to the sync entirely: not downloaded, not recorded,
    # not logged. Found on 2026-08-27 in Basketball, where "Règles de bases -
    # FIBA" had been sitting unnoticed between two files that synced fine.
    is_video: bool = False


# --------------------------------------------------------------------------
# Selector candidates.
#
# Ordered: first match wins. The leading entries are updated from the live
# discovery runs in the plan (Tasks 9.1, 10.1, 11.1); the rest are fallbacks
# across Omnivox skins so a layout tweak degrades instead of crashing.
# --------------------------------------------------------------------------

# Verified 2026-08-06 against cegepmontpetit.omnivox.ca: the login form is
# form#formLogin -> input#Identifiant[name=NoDA] + input#Password[name=PasswordEtu],
# submitted by a <button> whose label contains "Connexion".
_USER_FIELDS = (
    "form#formLogin input#Identifiant",
    "input[name='NoDA']",
    "input#NoDA",
    "input[name='UserID']",
    "form input[type='text']:not([type='hidden'])",
)
_PASS_FIELDS = (
    "form#formLogin input#Password",
    "input[name='PasswordEtu']",
    "input#PasswordEtu",
    "input[type='password']",
)
_SUBMIT = (
    "form#formLogin button:has-text('Connexion')",
    "button:has-text('Connexion')",
    "input[type='submit']",
    "button[type='submit']",
    "button:has-text('Valider')",
)
_LOGGED_IN_MARKERS = (
    "a[href*='Quitter.aspx']",
    "a[href*='IdServiceSkytech']",
    "a[href*='Deconnexion']",
    "a[href*='Logout']",
    "text=Quoi de neuf",
    "#entete_nom_usager",
)
# Omnivox's identity-validation (MFA) step. Credentials were fine; the profile
# just is not a trusted device yet.
_MFA_URL_MARKERS = ("/apps/mfa/", "/mfa/login/", "validate-method")
# Omnivox periodically blocks navigation with a mandatory survey ("Consultation
# obligatoire"). It offers "Remettre a plus tard", which only postpones it.
# The automation defers so the crawl is not blocked; it NEVER answers, and the
# pending consultation still reaches the user through the digest.
_CONSULTATION_URL_MARKERS = ("/svet/", "effectueconsultation", "accessv.ovx")
_CONSULTATION_DEFER = (
    "Remettre à plus tard",
    "Remettre a plus tard",
    "plus tard",
)
_MFA_TRUST_CHECKBOX = (
    "input[type='checkbox']",
    "label:has-text('appareil de confiance')",
)
# Verified 2026-08-06. LEA must be entered by clicking the "Léa" link on the
# Omnivox intranet homepage, which hits a Skytech redirector that mints the LEA
# session. Navigating to the LEA host directly logs the session OUT
# (lands on .../Login?isLogout=1) — this is the concrete form of the spec's
# "never reuse deep links" rule.
_LEA_ENTRY_LINKS = (
    "a[href*='Skytech.aspx'][href*='Skytech_Omnivox']",
    "a[href*='IdServiceSkytech']",
)
_LEA_LINK_NAME = "Léa"

# LEA dashboard: one div.card-panel per course, titled "CODE NAME", containing
# a "Documents et vidéos" link. Those hrefs embed Ref=<timestamp>&SID=<uuid>,
# so they are valid only within the current run and are never persisted.
_COURSE_CARD = "div.card-panel"
_COURSE_TITLE = ".card-panel-title"
_DOCS_LINK_TEXT = "Documents et vidéos"

# The per-course list of distributed documents.
# Teachers distribute both files and external web links; links carry a distinct
# icon. Downloading one yields an HTML page, so they are recorded but never
# fetched — otherwise they would fail and re-notify on every single run.
_EXTERNAL_LINK_ICON = "lienExterne"
_DOC_LIST_URL_MARKER = "ListeDocuments.aspx"
_DOC_ANCHOR = "a[href*='VisualiseDocument.aspx']"

# --- Énoncés de travaux -----------------------------------------------------
# A separate LEA section from Documents, and the one that carries the actual
# assignments. The sync ignored it entirely until 2026-09-03: four weeks of
# syncing had fetched every slide deck across six courses and not one
# assignment brief, which is the half that carries the deadlines.
#
# Three hops, where Documents needs two: card -> ListeTravauxEtu -> DepotTravail.
# The card link is matched on "Énoncés distribués" rather than on "Travaux",
# because an SVG <title>Travaux</title> shadows the anchor and Playwright
# resolves the invisible one, then waits 30 s for it to become clickable.
_TRAVAUX_LINK_TEXT = "Énoncés distribués"
_TRAVAUX_LIST_URL_MARKER = "ListeTravauxEtu.aspx"

# The row title is href="javascript:;" and the real target lives in an onclick
# that opens a popup, so it cannot be followed by clicking or by reading href.
_TRAVAIL_POPUP = re.compile(r"OpenCentre\('(DepotTravail\.aspx[^']+)'")

# On the detail page, the brief hangs off ReadDocumentTravail.aspx. That page is
# ALSO the submission form, with a file picker and a TRANSMETTRE button. Nothing
# here ever clicks: we navigate, read the anchor, and fetch the bytes through
# the authenticated context. Handing an automation the button that submits
# schoolwork is not a risk worth taking for a download.
_ENONCE_ANCHOR = "a[href*='ReadDocumentTravail.aspx']"
_ENONCE_MARKER = "ReadDocumentTravail.aspx"
_DOCS_NAV_TEXTS = ("Documents distribués", "Documents et vidéos", "Documents")

_COURSE_CODE_RE = re.compile(r"\b(\d{3}-\w{3}-\w{2})\b")


# --------------------------------------------------------------------------
# Date normalisation (pure)
# --------------------------------------------------------------------------

# Full names and LEA's abbreviations. The documents list renders dates as
# "depuis le 4 fév 2026" — abbreviated. Missing these silently yields an empty
# publish_date, which would make every document look new on every run and
# re-download the whole semester. Accents are stripped before lookup.
_FRENCH_MONTHS = {
    "janvier": 1, "jan": 1, "janv": 1,
    "fevrier": 2, "fev": 2, "fevr": 2,
    "mars": 3, "mar": 3,
    "avril": 4, "avr": 4,
    "mai": 5,
    "juin": 6, "jun": 6,
    "juillet": 7, "juil": 7, "jui": 7,
    "aout": 8, "aou": 8,
    "septembre": 9, "sept": 9, "sep": 9,
    "octobre": 10, "oct": 10,
    "novembre": 11, "nov": 11,
    "decembre": 12, "dec": 12,
}
_ISO_RE = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
_DMY_RE = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")
# No leading \b: LEA emits "depuis le4 fév 2026" with the day glued to "le".
# Trailing "." tolerated for abbreviations ("fév.").
_TEXT_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})(?!\d)")
# Homepage event rows read "Mardi 2 juin Délai pour remise des notes" (no year).
_EVENT_ROW_RE = re.compile(
    r"^((?:Lundi|Mardi|Mercredi|Jeudi|Vendredi|Samedi|Dimanche)\s+\d{1,2}\s+[A-Za-zÀ-ÿ]+)\s+(.+)$",
    re.IGNORECASE,
)


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    ).lower()


def parse_publish_date(raw: str) -> str:
    """Normalise an Omnivox date cell to ISO YYYY-MM-DD, or "" if unparseable.

    Stability matters: this value is part of the identity tuple that decides
    whether a document is new (spec section 7 step 4).
    """
    if not raw:
        return ""
    text = raw.strip()

    match = _ISO_RE.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"

    match = _DMY_RE.search(text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    match = _TEXT_DATE_RE.search(text)
    if match:
        day_s, month_word, year_s = match.groups()
        month = _FRENCH_MONTHS.get(_strip_accents(month_word))
        if month:
            return f"{int(year_s):04d}-{month:02d}-{int(day_s):02d}"

    return ""


NAV_RETRIES = 3
NAV_BACKOFF_MS = 4000


def _goto(page: Page, url: str, *, logger=None, retries: int = NAV_RETRIES):
    """page.goto with retries.

    Omnivox intermittently stalls past 30 s, which aborted four scheduled runs
    between 12 and 20 August. A transient stall should cost a few seconds, not
    a whole sync.
    """
    log = logger or logging.getLogger("school.omnivox")
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001 - retry any navigation failure
            last = exc
            if attempt < retries:
                log.warning(
                    "Navigation to %s failed (attempt %d/%d): %s; retrying",
                    url, attempt, retries, str(exc).splitlines()[0][:120],
                )
                page.wait_for_timeout(NAV_BACKOFF_MS * attempt)
    raise OmnivoxError(
        f"Could not load {url} after {retries} attempts: "
        f"{str(last).splitlines()[0][:160]}"
    )


def _first_visible(page: Page, selectors: tuple[str, ...], timeout_ms: int = 4000):
    """Return the first selector in `selectors` that resolves to a visible element."""
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except Exception:  # noqa: BLE001 - try the next candidate
            continue
    return None


class OmnivoxSession:
    """A logged-in browser session against Omnivox, reused across runs.

    Use as a context manager:

        with OmnivoxSession(profile_dir) as session:
            session.login(user, password)
            courses = session.list_courses()
    """

    #: Class-level so a session built without __init__ still resolves URLs.
    #: Tests exercise _absolute() on a bare instance, and a portal is a
    #: property of the school rather than of any one browser session.
    portal: Portal = DEFAULT_PORTAL

    def __init__(
        self,
        profile_dir: Path,
        *,
        headed: bool = False,
        screenshot_dir: Path | None = None,
        logger: logging.Logger | None = None,
        portal: Portal | None = None,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.portal = portal or DEFAULT_PORTAL
        self.headed = headed
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self.log = logger or logging.getLogger("school.omnivox")
        self._playwright = None
        self._context = None
        self.page: Page | None = None

    def __enter__(self) -> "OmnivoxSession":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=not self.headed,
            accept_downloads=True,
            locale="fr-CA",
            timezone_id="America/Toronto",
            viewport={"width": 1440, "height": 900},
        )
        self._context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
        return False

    def screenshot(self, label: str) -> Path | None:
        """Save a full-page screenshot for debugging a failure. Never raises."""
        if self.screenshot_dir is None or self.page is None:
            return None
        try:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.screenshot_dir / f"{safe}-{stamp}.png"
            self.page.screenshot(path=str(path), full_page=True)
            self.log.info("Saved failure screenshot: %s", path)
            return path
        except Exception as exc:  # noqa: BLE001 - diagnostics must never mask the real error
            self.log.warning("Could not save screenshot %s: %s", label, exc)
            return None

    # ----------------------------------------------------------------------
    # Login
    # ----------------------------------------------------------------------

    def is_logged_in(self) -> bool:
        """True when the current page shows post-login chrome."""
        if self.page is None:
            return False
        for marker in _LOGGED_IN_MARKERS:
            try:
                if self.page.locator(marker).first.is_visible(timeout=1500):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def login(self, user: str, password: str) -> None:
        """Log in if needed. A no-op when the stored session cookie is still valid.

        Always starts from the Omnivox homepage: session deep links expire
        (spec section 7 step 2).
        """
        _goto(self.page, self.portal.home, logger=self.log)

        if self.is_logged_in():
            self.log.info("Existing Omnivox session reused; skipping login form")
            return

        # Only now do credentials matter. Checking them first, which is what
        # this used to do, refused to start for anyone who had signed in by
        # hand and never put a password on disk, even though their stored
        # session was valid and about to be used.
        if not user or not password:
            raise LoginError(
                "There is no password in .env, so this cannot sign in.\n\n"
                "The signed-in session does not survive the browser closing, so "
                "every\nscheduled run signs in from scratch and needs one. The "
                "trusted-device\ncookie in the stored profile is a different "
                "thing: it stops the six-digit\ncodes, and it is already "
                "working.\n\n"
                "    make setup-omnivox\n\n"
                "asks for them and writes them to .env without printing them."
            )

        user_field = _first_visible(self.page, _USER_FIELDS)
        pass_field = _first_visible(self.page, _PASS_FIELDS)
        if user_field is None or pass_field is None:
            self.screenshot("login-failed-noform")
            raise LoginError(
                f"Login form not found at {self.page.url}. The page layout probably "
                "changed; see the screenshot in logs/ and update _USER_FIELDS/_PASS_FIELDS."
            )

        user_field.fill(user)
        pass_field.fill(password)

        submit = _first_visible(self.page, _SUBMIT)
        if submit is None:
            self.screenshot("login-failed-nosubmit")
            raise LoginError(f"No submit control found on the login form at {self.page.url}")

        submit.click()
        try:
            self.page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - verify by marker instead
            pass

        if self.is_mfa_challenge():
            self.screenshot("login-mfa-required")
            raise MfaRequired(
                "Omnivox is asking for a 6-digit identity-validation code, so this "
                "browser profile is not a trusted device (yet). Credentials were "
                "accepted — nothing is wrong with .env.\n"
                "Fix it once, interactively:\n"
                "    .venv/bin/python -m src.omnivox_sync --login\n"
                "Enter the emailed code and TICK 'J'utilise un appareil de confiance'. "
                "The trust cookie is then stored in state/omnivox-profile/ and "
                "scheduled headless runs stop being challenged."
            )

        if not self.is_logged_in():
            self.screenshot("login-failed-rejected")
            raise LoginError(
                "Login did not complete. Most likely a wrong password, a changed "
                f"password, or a new interstitial. Landed on {self.page.url}; "
                "see the screenshot in logs/."
            )
        self.log.info("Logged in to Omnivox as %s", user)

    def dismiss_consultation(self) -> bool:
        """Defer a mandatory Omnivox survey blocking the page.

        Returns True if one was deferred. Only ever clicks "Remettre à plus
        tard": the survey asks personal questions and is the user's to answer,
        and the digest already reports that one is pending.
        """
        url = (self.page.url or "").lower()
        if not any(m in url for m in _CONSULTATION_URL_MARKERS):
            return False
        for label in _CONSULTATION_DEFER:
            try:
                button = self.page.get_by_role("button", name=re.compile(label, re.I)).first
                if button.count() == 0:
                    button = self.page.locator(f"input[value*='{label}' i]").first
                if button.count() and button.is_visible(timeout=2000):
                    self.log.info("Deferring a mandatory Omnivox consultation (not answering)")
                    button.click()
                    self.page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
                    self.page.wait_for_timeout(2000)
                    return True
            except Exception:  # noqa: BLE001 - try the next label
                continue
        self.screenshot("consultation-blocked")
        self.log.warning(
            "A mandatory consultation is blocking %s and no defer button was found",
            self.page.url,
        )
        return False

    def is_mfa_challenge(self) -> bool:
        """True when Omnivox is on the 'Validation d'identité' step."""
        if self.page is None:
            return False
        url = (self.page.url or "").lower()
        if any(marker.lower() in url for marker in _MFA_URL_MARKERS):
            return True
        try:
            return self.page.locator("text=Validation d'identité").first.is_visible(
                timeout=1500
            )
        except Exception:  # noqa: BLE001
            return False

    def await_manual_login(
        self, user: str, password: str, *, timeout_s: int = 600
    ) -> tuple[str, str]:
        """One-time interactive bootstrap: wait for the user to sign in, and
        clear the MFA challenge, in a visible browser window.

        Returns whatever was actually typed into the form, so the caller can
        OFFER to save it. It does not save anything itself and it never prints
        or logs it.

        Why that matters: the stored profile keeps Omnivox's trusted-device
        cookie, which is what stops the six-digit codes, but it does NOT keep
        the session cookie, which dies with the browser. Measured over 93
        scheduled runs on the author's machine: 114 form logins, zero session
        reuses. So a scheduled run signs in with a password every single time,
        and without one it cannot run at all. Making somebody type the same
        password again into a second command, minutes after typing it here, is
        friction for nothing.

        The 6-digit code is never read, requested, or typed by this program.
        """
        _goto(self.page, self.portal.home, logger=self.log)

        if self.is_logged_in():
            self.log.info("Already logged in; this profile is already trusted.")
            # Nothing was typed, so there is nothing to offer to save. Returning
            # a bare None here made the caller's unpack raise on the one path
            # where everything had gone right.
            return "", ""

        # Pre-fill only if there is something to pre-fill. With no .env the
        # user types both fields themselves in the window that just opened,
        # which is the normal first-time path and the whole point of it: no
        # file to edit, and nothing types their password but them.
        if user and password:
            user_field = _first_visible(self.page, _USER_FIELDS)
            pass_field = _first_visible(self.page, _PASS_FIELDS)
            if user_field is not None and pass_field is not None:
                user_field.fill(user)
                pass_field.fill(password)
                submit = _first_visible(self.page, _SUBMIT)
                if submit is not None:
                    submit.click()

        captured = ("", "")
        deadline = time.monotonic() + timeout_s
        warned = False
        while time.monotonic() < deadline:
            # Read the form back while it is still on screen. Once the page
            # navigates the fields are gone, and asking for the password a
            # second time is exactly the friction this avoids.
            try:
                seen_user = _first_visible(self.page, _USER_FIELDS)
                seen_pass = _first_visible(self.page, _PASS_FIELDS)
                if seen_user is not None and seen_pass is not None:
                    typed = (seen_user.input_value(), seen_pass.input_value())
                    if all(typed):
                        captured = typed
            except Exception:  # noqa: BLE001 - the form is gone, which is fine
                pass

            if self.is_logged_in():
                self.log.info("Login complete; session stored in %s", self.profile_dir)
                return captured
            if self.is_mfa_challenge() and not warned:
                warned = True
                self.log.warning(
                    "Identity validation required. Enter the 6-digit code from your "
                    "email in the browser window, and TICK "
                    "\"J'utilise un appareil de confiance\" before validating."
                )
            self.page.wait_for_timeout(2000)

        self.screenshot("login-bootstrap-timeout")
        raise LoginError(
            f"Timed out after {timeout_s}s waiting for the login to complete. "
            "Re-run --login and finish the identity validation in the browser."
        )
        return captured  # unreachable; kept so every path has a return type

    # ----------------------------------------------------------------------
    # Courses and documents
    # ----------------------------------------------------------------------

    def goto_lea(self) -> None:
        """Enter LEA the only way that works: from the intranet homepage.

        Going straight to the LEA host ends the session (…/Login?isLogout=1),
        so this always clicks through the Skytech redirector.
        """
        _goto(self.page, self.portal.home + "/intr/", logger=self.log)

        link = self.page.get_by_role("link", name=self.portal.lea_link_name, exact=True).first
        try:
            if not link.is_visible(timeout=4000):
                link = None
        except Exception:  # noqa: BLE001
            link = None
        if link is None:
            link = _first_visible(self.page, _LEA_ENTRY_LINKS)
        if link is None:
            self.screenshot("lea-entry-not-found")
            raise ParseError(
                f"Could not find the 'Léa' link on {self.page.url}. Omnivox's "
                "homepage layout changed; see the screenshot in logs/ and update "
                "_LEA_ENTRY_LINKS."
            )

        link.click()
        self.page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        self.page.wait_for_timeout(2500)
        self.dismiss_consultation()

        if "isLogout" in (self.page.url or ""):
            self.screenshot("lea-session-lost")
            raise LoginError(
                "Entering LEA ended the session. Re-run --login to refresh the "
                "stored Omnivox session."
            )

    def list_courses(self) -> list[OmnivoxCourse]:
        """Every course visible on the LEA dashboard.

        The returned `href` is the course's 'Documents et vidéos' link. It embeds
        a per-session SID and is valid only for the current run — never store it.
        """
        self.goto_lea()

        courses: list[OmnivoxCourse] = []
        seen: set[str] = set()
        cards = self.page.locator(_COURSE_CARD)
        for index in range(cards.count()):
            card = cards.nth(index)
            try:
                title_el = card.locator(_COURSE_TITLE).first
                if title_el.count() == 0:
                    continue
                title = " ".join((title_el.text_content() or "").split())
            except Exception:  # noqa: BLE001 - a malformed card must not kill the crawl
                continue

            match = _COURSE_CODE_RE.search(title)
            if not match:
                continue
            code = match.group(1)
            if code in seen:
                continue

            href = ""
            try:
                docs = card.get_by_text(self.portal.docs_link).first
                anchor = docs.locator("xpath=ancestor-or-self::a[1]")
                if anchor.count() > 0:
                    href = anchor.first.get_attribute("href") or ""
            except Exception:  # noqa: BLE001 - fall back to re-navigation at open time
                href = ""

            name = title.replace(code, "", 1).strip(" -–—\t\n")
            seen.add(code)
            courses.append(OmnivoxCourse(code=code, name=name or code, href=href))

        if not courses:
            self.screenshot("courses-parse-failed")
            raise ParseError(
                f"No courses found on the LEA dashboard at {self.page.url}. "
                "Either the semester has no courses yet, or the page layout changed; "
                "see the screenshot in logs/ and update _COURSE_CARD/_COURSE_TITLE."
            )
        self.log.info("Found %d courses on LEA", len(courses))
        return courses

    def open_documents_page(self, course: OmnivoxCourse) -> None:
        """Land on this course's ListeDocuments page.

        Two hops: the card's "Documents et vidéos" link opens a summary page
        listing every course, then the course's own entry opens its document
        list. Uses the same-run href when available, otherwise re-enters LEA
        from the homepage and finds the card by course code.
        """
        landed = False
        if course.href:
            url = self._absolute(course.href)
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(1500)
            landed = "isLogout" not in (self.page.url or "")
            if not landed:
                self.log.info(
                    "Stored LEA link for %s expired; re-entering from home", course.code
                )

        if not landed:
            self.goto_lea()
            card = self.page.locator(_COURSE_CARD).filter(has_text=course.code).first
            if card.count() == 0:
                self.screenshot(f"course-card-missing-{course.code}")
                raise ParseError(f"No LEA card found for {course.code} at {self.page.url}")
            card.get_by_text(self.portal.docs_link).first.click()
            self.page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
            self.page.wait_for_timeout(1500)

        self._open_course_document_list(course)

    def _open_course_document_list(self, course: OmnivoxCourse) -> None:
        """From the shared 'Sommaire des documents distribués' page, open the
        list belonging to `course`. A no-op when already on a ListeDocuments page.
        """
        if _DOC_LIST_URL_MARKER in (self.page.url or ""):
            return

        # The summary lists each course; its entry links into ListeDocuments.
        candidates = (
            self.page.locator(f"a:has-text('{course.code}')"),
            self.page.get_by_role("link", name=re.compile(re.escape(course.code))),
            self.page.get_by_role(
                "link", name=re.compile(re.escape(course.name[:24]), re.I)
            )
            if course.name
            else None,
        )
        for locator in candidates:
            if locator is None:
                continue
            try:
                if locator.count() == 0:
                    continue
                locator.first.click()
                self.page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
                self.page.wait_for_timeout(2000)
                if _DOC_LIST_URL_MARKER in (self.page.url or ""):
                    return
            except Exception:  # noqa: BLE001 - try the next candidate
                continue

        self.screenshot(f"documents-list-not-reached-{course.code}")
        raise ParseError(
            f"Could not open the document list for {course.code}; stopped at "
            f"{self.page.url}. See the screenshot in logs/."
        )

    def open_travaux_page(self, course: OmnivoxCourse) -> None:
        """Land on the course's ListeTravauxEtu page."""
        if _TRAVAUX_LIST_URL_MARKER in (self.page.url or ""):
            return
        self.goto_lea()
        card = self.page.locator(_COURSE_CARD).filter(has_text=course.code).first
        if card.count() == 0:
            self.screenshot(f"course-card-missing-{course.code}")
            raise ParseError(f"No LEA card found for {course.code} at {self.page.url}")
        link = card.locator("a").filter(has_text=self.portal.travaux_link).first
        if link.count() == 0:
            # A course with no assignments posted yet has no such link. That is
            # a normal state, not a parse failure.
            self.log.info("%s: no Travaux link on the card", course.code)
            return
        link.click()
        self.page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        self.page.wait_for_timeout(1500)

    def list_assignments(self, course: OmnivoxCourse) -> list[OmnivoxDocument]:
        """Every assignment brief for one course, as downloadable documents.

        Returned as OmnivoxDocument so the whole existing pipeline (dedup,
        conversion, NotebookLM upload, digest) applies unchanged.

        `publish_date` carries the SUBMISSION DEADLINE, which is the only date
        LEA shows here. That is a deliberate compromise: it makes the dedup key
        stable, but a teacher who revises a brief without moving the deadline
        will not trigger a re-download. The same blind spot exists on the
        Documents path, and `doc_id` records the idtravail GUID so a future
        version can key on it properly.
        """
        self.open_travaux_page(course)
        if _TRAVAUX_LIST_URL_MARKER not in (self.page.url or ""):
            return []

        rows = self.page.evaluate(
            r"""() => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[onclick*="DepotTravail"]')) {
            const oc = a.getAttribute('onclick') || '';
            const m = oc.match(/idtravail=([0-9a-fA-F-]+)/);
            const id = m ? m[1] : oc;
            if (seen.has(id)) continue;   // LEA repeats the row's anchor
            seen.add(id);
            const tr = a.closest('tr');
            const cells = tr
              ? [...tr.querySelectorAll('td')].map(td => (td.textContent||'').replace(/\s+/g,' ').trim())
              : [];
            out.push({
              id: id,
              onclick: oc,
              title: (a.textContent || '').replace(/\s+/g, ' ').trim(),
              cells: cells,
              // LEA stars the briefs you have never opened.
              star: !!(tr && tr.querySelector('img[src*="etoile" i], img[alt*="pas encore" i]')),
            });
          }
          return out;
        }"""
        )

        if not rows:
            self.log.info("%s: no assignments posted", course.code)
            return []

        listing_url = self.page.url
        out: list[OmnivoxDocument] = []
        for row in rows:
            match = _TRAVAIL_POPUP.search(row.get("onclick") or "")
            if not match:
                self.log.warning(
                    "%s: could not read the popup target for %r",
                    course.code, row.get("title", ""),
                )
                continue

            due = ""
            for cell in row.get("cells") or []:
                due = parse_publish_date(cell)
                if due:
                    break

            detail = self._absolute(match.group(1))
            try:
                self.page.goto(detail, wait_until="domcontentloaded",
                               timeout=DEFAULT_TIMEOUT_MS)
                self.page.wait_for_timeout(1200)
                link = self.page.locator(_ENONCE_ANCHOR).last
                if link.count() == 0:
                    # A work with a deadline but no attached brief: the teacher
                    # explained it in class and only opened a submission box.
                    self.log.info(
                        "%s: %r has no attached brief", course.code, row.get("title", "")
                    )
                    continue
                href = link.get_attribute("href") or ""
                filename = (link.inner_text() or "").strip() or href.rsplit("/", 1)[-1]
            except Exception as exc:  # noqa: BLE001 - one bad row must not kill the run
                self.log.warning(
                    "%s: could not open the brief for %r: %s",
                    course.code, row.get("title", ""), exc,
                )
                continue
            finally:
                self.page.goto(listing_url, wait_until="domcontentloaded",
                               timeout=DEFAULT_TIMEOUT_MS)

            out.append(
                OmnivoxDocument(
                    course_code=course.code,
                    filename=filename.split("?")[0],
                    publish_date=due,
                    ref=href,
                    description=(row.get("title") or "")[:120],
                    doc_id=row.get("id") or "",
                )
            )

        self.log.info("%s: %d assignment brief(s) listed", course.code, len(out))
        return out

    def list_documents(self, course: OmnivoxCourse) -> list[OmnivoxDocument]:
        """Every distributed document row for one course.

        An empty list is a valid result (a course may have posted nothing yet)
        and is NOT an error. A missing table is an error.

        The visible filename cell is truncated by LEA ("ch01_presentation..."),
        so the real name is read from the file-type icon's title attribute.
        """
        self.open_documents_page(course)

        rows = self.page.evaluate(
            r"""() => {
          const out = [];
          const seen = new Set();
          const SEL = "a[href*='VisualiseDocument.aspx'], a[href*='VisualiseVideo.aspx']";
          for (const a of document.querySelectorAll(SEL)) {
            const tr = a.closest('tr');
            if (!tr) continue;
            // LEA repeats the same document as a description link and a filename
            // link; the IDDocCoursDocument GUID collapses them to one row.
            const href = a.getAttribute('href') || '';
            const m = href.match(/IDDocCoursDocument=([0-9a-fA-F-]+)/);
            const id = m ? m[1] : href;
            if (seen.has(id)) continue;
            // Prefer the anchor that carries the file icon: its title is the
            // untruncated filename.
            let icon = a.querySelector('img[title]');
            let chosen = a;
            if (!icon) {
              for (const sib of tr.querySelectorAll(SEL)) {
                const i2 = sib.querySelector('img[title]');
                const h2 = sib.getAttribute('href') || '';
                if (i2 && h2.includes(id)) { icon = i2; chosen = sib; break; }
              }
            }
            if (!icon) continue;
            seen.add(id);
            const cells = [...tr.querySelectorAll('td')].map(
              td => (td.textContent || '').replace(/\s+/g, ' ').trim());
            out.push({
              isVideo: href.includes('VisualiseVideo.aspx'),
              filename: (icon.getAttribute('title') || '').trim(),
              iconSrc: icon.getAttribute('src') || '',
              href: chosen.getAttribute('href') || '',
              docId: id,
              rowText: (tr.textContent || '').replace(/\s+/g, ' ').trim(),
              cells: cells,
            });
          }
          return out;
        }"""
        )

        if not rows:
            # Distinguish "no documents posted" from "page did not load".
            has_table = self.page.locator("table").count() > 0
            if not has_table:
                self.screenshot(f"documents-no-table-{course.code}")
                raise ParseError(
                    f"No document table found for {course.code} at {self.page.url}. "
                    "See the screenshot in logs/ and update the ListeDocuments parsing."
                )
            self.log.info("%s: no distributed documents", course.code)
            return []

        documents: list[OmnivoxDocument] = []
        for row in rows:
            filename = row.get("filename") or ""
            if not filename:
                continue
            publish_date = parse_publish_date(row.get("rowText") or "")
            description = ""
            for cell in row.get("cells") or []:
                if cell and "depuis le" not in cell and filename[:12] not in cell:
                    description = cell
                    break
            documents.append(
                OmnivoxDocument(
                    course_code=course.code,
                    filename=filename,
                    publish_date=publish_date,
                    ref=row.get("href") or "",
                    description=description[:120],
                    doc_id=row.get("docId") or "",
                    is_link=_EXTERNAL_LINK_ICON in (row.get("iconSrc") or ""),
                    is_video=bool(row.get("isVideo")),
                )
            )

        undated = [
            d.filename for d in documents
            if not d.publish_date and not d.is_link and not d.is_video
        ]
        if undated:
            self.log.warning(
                "%s: %d document(s) with an unparseable date (%s). They would look "
                "new on every run; check the date format on %s",
                course.code,
                len(undated),
                ", ".join(undated[:3]),
                self.page.url,
            )
        self.log.info("%s: %d documents listed", course.code, len(documents))
        return documents

    # ----------------------------------------------------------------------
    # Announcements (Milestone 3) - strictly read-only
    # ----------------------------------------------------------------------

    def collect_announcements(self) -> list[dict]:
        """Everything M3 needs from the portal, without changing any state.

        Returns raw dicts: {kind, title, detail, date, unread}.
        kind is one of "quoi_de_neuf", "mio", "event".

        MIO is the delicate one. Spec section 7: read subjects only and never
        open a message, so unread stays unread. Loading the MIO frameset is
        verified safe -- the detail pane renders "Aucun message selectionne"
        until a row is clicked, and this method never clicks one.
        """
        items: list[dict] = []
        try:
            items.extend(self._collect_quoi_de_neuf())
        except Exception as exc:  # noqa: BLE001 - the digest is best-effort
            self.log.warning("Could not read 'Quoi de neuf': %s", exc)
        try:
            items.extend(self._collect_events())
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Could not read events: %s", exc)
        try:
            items.extend(self._collect_mio())
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Could not read MIO: %s", exc)
        self.log.info("Collected %d portal item(s) for the digest", len(items))
        return items

    def _home(self) -> None:
        if not (self.page.url or "").rstrip("/").endswith("/intr"):
            _goto(self.page, self.portal.home + "/intr/", logger=self.log)
            self.page.wait_for_timeout(1200)

    def _collect_quoi_de_neuf(self) -> list[dict]:
        self._home()
        raw = self.page.evaluate(
            r"""() => {
          const clean = e => (e.textContent||'').replace(/\s+/g,' ').trim();
          const out=[];
          for (const a of document.querySelectorAll('#QuoiDeNeufsWrapper a[data-type-service]')) {
            let text = clean(a);
            // The card duplicates its own label; collapse an exact doubling.
            const half = text.length/2;
            if (text.length % 2 === 0 && text.slice(0,half) === text.slice(half)) {
              text = text.slice(0, half);
            }
            out.push({service: a.getAttribute('data-type-service') || '', text: text});
          }
          return out;
        }"""
        )
        items = []
        for card in raw:
            title = (card.get("text") or "").strip()
            if not title:
                continue
            items.append(
                {
                    "kind": "quoi_de_neuf",
                    "title": title[:200],
                    "detail": card.get("service", ""),
                    "date": "",
                    "unread": True,
                }
            )
        return items

    def _collect_events(self) -> list[dict]:
        """Upcoming dated items from the homepage Evenements block."""
        self._home()
        raw = self.page.evaluate(
            r"""() => {
          const clean = e => (e.textContent||'').replace(/\s+/g,' ').trim();
          // Each event is a div.carte-evenement whose text reads
          // "Mardi 2 juin <title>".
          const out = [];
          for (const card of document.querySelectorAll('div.carte-evenement')) {
            const t = clean(card);
            if (t.length > 7 && t.length < 200) out.push(t);
          }
          return out.slice(0, 25);
        }"""
        )
        # Keep only rows shaped like "Mardi 2 juin <title>": the block also
        # renders the date and the title as separate nodes, plus UI chrome
        # ("Calendrier", "Vue par mois"), all of which is noise in a digest.
        items = []
        seen: set[str] = set()
        for text in raw:
            match = _EVENT_ROW_RE.match(text)
            if not match:
                continue
            when, title = match.group(1).strip(), match.group(2).strip()
            if len(title) < 4:
                continue
            key = f"{when}|{title}"
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "kind": "event",
                    "title": title[:160],
                    "detail": when,
                    "date": "",  # LEA omits the year here; do not invent one
                    "unread": False,
                }
            )
        return items

    def list_communiques(self) -> dict[str, list[dict]]:
        """Every course's communiqués, keyed by course code.

        A communiqué is a teacher's announcement to the whole class, and it is
        where the actual instruction often lives: "à compléter avant le cours
        de vendredi" is a communiqué, not a document. Nothing in this project
        had ever read one. They were not being missed intermittently, they were
        never being looked at: the word did not appear anywhere in the source.

        They are not a section inside a course. They live on the LÉA landing
        page, in a panel on each course card, which is why one page load
        returns them for every course at once.

        The body is behind OpenCentre('/cvir/comm/Communique.aspx?...'), an
        ordinary URL that can be fetched. Read the caller's note about the Ref
        in that URL going stale.
        """
        self.list_courses()  # the LÉA landing page, where the cards live
        rows = self.page.evaluate(
            r"""() => {
          const clean = e => (e.textContent||'').replace(/\s+/g,' ').trim();
          const out = {};
          for (const card of document.querySelectorAll('.card-panel, .card')) {
            const head = clean(card).slice(0, 40);
            const code = (head.match(/\b(\d{3}-[A-Z0-9]{3}-[A-Z]{2})\b/) || [])[1];
            if (!code) continue;
            for (const item of card.querySelectorAll('.card-panel-item')) {
              const title = clean(item.querySelector('.item-header-title') || item);
              if (!/Communiqu/i.test(title)) continue;
              const found = [];
              for (const row of item.querySelectorAll('.communique-date-title-wrapper')) {
                const onclick = row.getAttribute('onclick') || '';
                const url = (onclick.match(/OpenCentre\('([^']+)'/) || [])[1] || '';
                // The unread marker is a dot rendered as a sibling icon.
                const unread = /unread|nouveau|new/i.test(
                  String(row.className || '') + ' ' + String(row.parentElement?.className || '')
                );
                found.push({text: clean(row).slice(0, 200), url, unread});
              }
              if (found.length) out[code] = found;
            }
          }
          return out;
        }"""
        )
        cleaned: dict[str, list[dict]] = {}
        for code, items in (rows or {}).items():
            good = []
            for item in items:
                url = item.get("url") or ""
                if not url:
                    continue
                text = item.get("text") or ""
                good.append({
                    "date": parse_publish_date(text) or "",
                    "title": _strip_leading_date(text),
                    "url": url,
                    "unread": bool(item.get("unread")),
                })
            if good:
                cleaned[code] = good
        return cleaned

    #: "Publié par Jordan Tremblay le 8 sep 2026" at the top of the body. The
    #: card glues the date to the title with no separator and truncates both,
    #: so the body is the only place either can be read reliably.
    _PUBLISHED_BY = re.compile(
        r"Publi[ée]e?\s+par\s+(?P<who>.+?)\s+le\s+(?P<when>\d{1,2}\s+\S+\s+\d{4})",
        re.I,
    )

    def communique_meta(self, body: str) -> tuple[str, str]:
        """(author, ISO date) read out of a communiqué's own text."""
        found = self._PUBLISHED_BY.search(body or "")
        if not found:
            return "", ""
        return found.group("who").strip(), (parse_publish_date(found.group("when")) or "")

    def fetch_communique(self, url: str) -> str:
        """One communiqué's text, fetched rather than clicked.

        Fetched through the authenticated context for the same reason
        documents are: clicking opens a centred popup window, and a popup is
        one more thing to go wrong for no gain.
        """
        absolute = self._absolute(url) if not url.startswith("http") else url
        response = self._context.request.get(absolute, timeout=DEFAULT_TIMEOUT_MS)
        if not response.ok:
            raise OmnivoxError(f"HTTP {response.status} fetching a communiqué")
        html = response.text()
        # The page is a full LÉA frame; keep the readable text and drop markup.
        page = self._context.new_page()
        try:
            page.set_content(html)
            return page.evaluate(
                "() => (document.body ? document.body.innerText : '')"
            ).strip()
        finally:
            page.close()

    #: The portal's MIO entry point. NOT `a[data-type-service='MIO']`, which is
    #: what this used to look for: that attribute is empty on both MIO links,
    #: so the selector matched nothing, waited its full timeout and gave up.
    #: The digest has been quietly missing MIO whenever it took that path.
    _MIO_ENTRY = "/intr/Module/ServicesExterne/RedirigeMio.ashx"

    def _open_mio_list(self):
        """Navigate to MIO and return the frame holding the message list.

        Reading the list changes nothing: read state lives on the message, and
        the list is just a table.
        """
        try:
            self.page.goto(
                self.portal.home + self._MIO_ENTRY, wait_until="domcontentloaded"
            )
            self.page.wait_for_timeout(6000)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Could not open MIO: %s", exc)
            return None
        # "/MioListe.aspx" with the slash: the frameset around it is called
        # MioListeDetailFrameset.aspx and matches a bare "MioListe".
        frame = next(
            (f for f in self.page.frames if "/MioListe.aspx" in (f.url or "")), None
        )
        if frame is None:
            self.log.warning("MIO list frame not found; skipping MIO for this run")
        return frame

    #: In the detail pane the sender is written "Name (340-101-MQ gr.1060
    #: (A2026))", which identifies the course outright instead of by matching
    #: a teacher's name.
    _MIO_SENDER_COURSE = re.compile(r"\((\d{3}-[A-Z0-9]{3}-[A-Z]{2})")

    def read_mio_body(self, message_id: str) -> tuple[str, str]:
        """(body, course code) for one message, by opening it.

        **This marks the message read**, which is why it is off unless asked
        for. There is no way round it: the id in the detail URL is the row's
        checkbox id, a GUID with the row's position glued on the end, so it
        cannot be built without the row, and reaching the row means clicking.

        Addressed by message id and NOT by position. Positions looked obvious
        and were wrong twice over: the listing skips rows, so its numbering
        already differed from the DOM's, and clicking a row marks it read,
        which changes its class and shifts every row after it. The result was
        each message opening its neighbour's contents, so a teacher's name in
        the list ended up over a stranger's message on disk.

        The list frame must already be open; `list_mio` leaves it that way.
        """
        if not message_id:
            return "", ""
        frame = next(
            (f for f in self.page.frames if "/MioListe.aspx" in (f.url or "")), None
        )
        if frame is None:
            return "", ""
        # The checkbox id is "chk" + the GUID + the row's position, so a prefix
        # match finds the row wherever it has moved to.
        row = frame.locator(f'tr:has(input[id^="chk{message_id}"])').first
        if row.count() == 0:
            return "", ""
        row.click()
        self.page.wait_for_timeout(2500)
        detail = next(
            (f for f in self.page.frames if "MioDetail" in (f.url or "")), None
        )
        if detail is None:
            return "", ""
        text = detail.evaluate(
            "() => (document.body ? document.body.innerText : '')"
        ).strip()
        found = self._MIO_SENDER_COURSE.search(text)
        return text, (found.group(1) if found else "")

    def list_mio(self, *, with_bodies: bool = False) -> list[dict]:  # noqa: ARG002
        """Every message in the inbox: who, what, when, and the preview.

        With `with_bodies=False`, the default, nothing is opened and nothing is
        marked read. The preview LÉA puts in the list is long enough to carry
        the instruction most of the time: "Pour le prochain cours, vous devez
        lire la section 2.5.3 du manuel et" is a preview, not a body.

        With `with_bodies=True` each message is opened for its full text, and
        every unread one it touches becomes read. That is the whole trade.
        """
        frame = self._open_mio_list()
        if frame is None:
            return []
        rows = frame.evaluate(
            r"""() => {
          const clean = e => (e.textContent||'').replace(/\s+/g,' ').trim();
          const out = [];
          const trs = [...document.querySelectorAll('tr.norm')];
          for (let i = 0; i < trs.length; i++) {
            const tr = trs[i];
            const cells = [...tr.querySelectorAll('td')].map(clean).filter(Boolean);
            if (!cells.length) continue;
            const dot = tr.querySelector('[data-message]');
            const isNew = tr.querySelector('input[id^=hidIsNew]');
            out.push({
              // The row's position in the DOM, which is what has to be clicked
              // later. Numbering the returned list instead is off by however
              // many rows were skipped, and it opens the wrong message.
              row: i,
              id: dot ? (dot.getAttribute('data-message')||'') : '',
              cells,
              unread: isNew ? String(isNew.value||'').toLowerCase() !== 'non' : false,
            });
          }
          return out;
        }"""
        )
        items = []
        for row in rows or []:
            cells = [
                c for c in row.get("cells", [])
                if c not in ("Message lu", "Nouveau message", "Catégoriser")
            ]
            if not cells:
                continue
            date = ""
            body_cells = []
            for cell in cells:
                parsed = parse_publish_date(cell)
                if parsed and not date:
                    date = parsed
                else:
                    body_cells.append(cell)
            sender = body_cells[0] if body_cells else ""
            preview = body_cells[1] if len(body_cells) > 1 else ""
            items.append({
                "id": row.get("id", ""),
                "sender": sender[:120],
                "preview": preview,
                "date": date,
                "unread": bool(row.get("unread")),
                # The DOM row, not this list's position: filtering happens
                # above, so the two diverge and clicking the wrong one opens
                # somebody else's message.
                "index": row.get("row", len(items)),
                "body": "",
                "course_code": "",
            })

        # Deliberately NOT fetching bodies here, even when asked. This has no
        # idea which messages the caller will keep, and on a real inbox that
        # was 50 messages opened at 2.5 s each to keep 7, three times a day,
        # for the rest of the semester. The caller filters first and asks for
        # the handful it actually wants.
        return items

    def _collect_mio(self) -> list[dict]:
        """Unread MIO senders and subjects. Never opens a message."""
        list_frame = self._open_mio_list()
        if list_frame is None:
            return []

        rows = list_frame.evaluate(
            r"""() => {
          const clean = e => (e.textContent||'').replace(/\s+/g,' ').trim();
          const out=[];
          for (const tr of document.querySelectorAll('tr.norm')) {
            const unread = (tr.className||'').includes('newmsg');
            const cells = [...tr.querySelectorAll('td')].map(clean).filter(Boolean);
            out.push({unread, cells, text: clean(tr).slice(0,300)});
          }
          return out;
        }"""
        )

        items = []
        for row in rows:
            if not row.get("unread"):
                continue  # spec: unread only
            cells = [c for c in row.get("cells", []) if c not in ("Nouveau message", "Message lu")]
            sender = cells[0] if cells else ""
            subject = cells[1] if len(cells) > 1 else ""
            date = ""
            for cell in cells:
                parsed = parse_publish_date(cell)
                if parsed:
                    date = parsed
                    break
            if not subject and row.get("text"):
                subject = row["text"].replace("Nouveau message", "").strip()[:120]
            items.append(
                {
                    "kind": "mio",
                    "title": subject[:200],
                    "detail": sender[:80],
                    "date": date,
                    "unread": True,
                }
            )
        # Leave the browser back on the portal home for whatever runs next.
        _goto(self.page, self.portal.home + "/intr/", logger=self.log)
        return items

    # ----------------------------------------------------------------------
    # Download
    # ----------------------------------------------------------------------

    #: LEA wraps the viewer URL in a javascript: call on the anchor.
    _VIDEO_CALL = re.compile(r"VisualiserVideo\('([^']+)'")
    #: Any YouTube form the embed might take. The id is always 11 characters.
    _YOUTUBE_ID = re.compile(r"(?:youtube(?:-nocookie)?\.com/embed/|youtu\.be/|[?&]v=)([A-Za-z0-9_-]{11})")

    def resolve_video_url(self, doc: OmnivoxDocument) -> str:
        """The real YouTube URL behind a VisualiseVideo row, or "".

        The URL is not in the documents page at all. The anchor is a
        javascript: call carrying an encrypted Info= blob, and the YouTube
        address only exists once LEA has rendered its viewer, as the src of an
        embed iframe. So it has to be opened.

        Those viewer URLs are session-bound and carry a timestamp, so the
        result is resolved fresh at sync time and only the YouTube URL is ever
        stored. Returns "" rather than raising: a video that cannot be resolved
        should cost that one row, not the whole course's sync.
        """
        match = self._VIDEO_CALL.search(doc.ref or "")
        target = match.group(1) if match else (doc.ref or "")
        if not target or "VisualiseVideo" not in target:
            return ""
        if not target.startswith("http"):
            base = self.page.url.rsplit("/", 1)[0]
            target = f"{base}/{target.lstrip('/')}"

        page = None
        try:
            page = self._context.new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
            page.wait_for_timeout(2000)  # the embed is written by script
            for frame in page.frames:
                found = self._YOUTUBE_ID.search(frame.url or "")
                if found:
                    return f"https://www.youtube.com/watch?v={found.group(1)}"
            found = self._YOUTUBE_ID.search(page.content())
            if found:
                return f"https://www.youtube.com/watch?v={found.group(1)}"
            self.log.warning("No YouTube embed found on the viewer for %s", doc.filename)
            return ""
        except Exception as exc:  # noqa: BLE001 - one row must not fail the course
            self.log.warning("Could not resolve the video %s: %s", doc.filename, exc)
            return ""
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def _absolute(self, ref: str) -> str:
        """Turn an href scraped off a LEA page into a URL that resolves.

        The old code did `LEA_HOME + ref`, which is correct only when exactly
        one of the two carries the slash between them. LEA serves both shapes:
        the course cards use "/Lea/..." and the document rows use a bare
        "VisualiseDocument.aspx?...". The bare ones produced
        "...omnivox.caVisualiseDocument.aspx", a hostname rather than a path,
        so DNS failed with ENOTFOUND and six basketball documents never
        downloaded. The error named the file, never the URL, which is why it
        read like a missing document rather than a broken link.

        Resolving against the CURRENT page rather than the host root also keeps
        a relative href inside its own directory: from /Lea/Documents.aspx,
        "VisualiseDocument.aspx" belongs to /Lea/, not to the site root.

        But only when that page is on the LEA host. Omnivox and LEA are two
        different hosts, cegepmontpetit.omnivox.ca and
        cegepmontpetit-lea.omnivox.ca, and course hrefs are LEA paths read while
        the browser may still be sitting on the Omnivox portal. Resolving one
        against the other sends you to HttpError.ovx?Code=404, which is exactly
        what the first version of this helper did to 330-704-EM.
        """
        if ref.startswith("http"):
            return ref
        base = self.page.url or ""
        if not base.startswith(self.portal.lea):
            base = self.portal.lea + "/"
        return urljoin(base, ref)

    def download(self, doc: OmnivoxDocument, dest: Path) -> Path:
        """Fetch one document to `dest`.

        Strategy 1: click the row link inside expect_download (spec section 7 step 5).
        Strategy 2: if that yields a viewer page instead of a file, fetch the
        resolved URL through the authenticated context. A native Save dialog is
        never involved in either path.
        """
        if not doc.ref:
            raise OmnivoxError(
                f"Document {doc.filename!r} has no download reference; "
                "the row markup probably changed"
            )
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        # An assignment brief is never reachable by clicking. Its anchor lives on
        # DepotTravail.aspx, which we deliberately navigate away from before
        # downloading, so expect_download has nothing to wait for and burns the
        # full 30 s timeout before the fetch that was always going to be the
        # answer. Observed on the first live run: 30 s of waiting, then 0.4 s to
        # actually get the file.
        if _ENONCE_MARKER in doc.ref:
            return self._fetch_to(doc, dest)

        try:
            # Match on the GUID, not the whole href. A LÉA document link carries
            # a Ref timestamp and an Info crypto token that both change on every
            # page load, so an exact href match silently stops matching the
            # moment anything re-renders. IDDocCoursDocument is the one stable
            # part of it.
            #
            # And 3 seconds, not 30. When the selector does miss, the direct
            # fetch below is what was always going to work, and it takes under
            # a second: the old timeout spent half a minute per document
            # waiting for an anchor that was never coming back.
            selector = (
                f"a[href*='IDDocCoursDocument={doc.doc_id}']"
                if doc.doc_id
                else f"a[href='{doc.ref}']"
            )
            with self.page.expect_download(timeout=3000) as download_info:
                self.page.locator(selector).first.click(timeout=3000)
            download_info.value.save_as(str(dest))
        except Exception as click_error:  # noqa: BLE001 - fall through to the URL strategy
            self.log.info(
                "expect_download did not fire for %s (%s); trying direct fetch",
                doc.filename,
                click_error,
            )
            return self._fetch_to(doc, dest)
        return dest

    def _fetch_to(self, doc: OmnivoxDocument, dest: Path) -> Path:
        """Pull the bytes through the authenticated context, no browser click."""
        url = self._absolute(doc.ref)
        try:
            response = self._context.request.get(url, timeout=DEFAULT_TIMEOUT_MS)
            if not response.ok:
                raise OmnivoxError(f"HTTP {response.status} fetching {doc.filename}")
            body = response.body()
        except OmnivoxError:
            self.screenshot(f"download-failed-{doc.course_code}")
            raise
        except Exception as fetch_error:  # noqa: BLE001
            self.screenshot(f"download-failed-{doc.course_code}")
            raise OmnivoxError(
                f"Could not download {doc.filename}: {fetch_error}"
            ) from fetch_error
        head = body[:15].lstrip().lower()
        if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
            self.screenshot(f"download-got-html-{doc.course_code}")
            raise OmnivoxError(
                f"Downloading {doc.filename} returned an HTML page, not a file. "
                "It is probably behind a viewer; see the screenshot in logs/."
            )
        dest.write_bytes(body)

        if not dest.exists() or dest.stat().st_size == 0:
            raise OmnivoxError(f"Download of {doc.filename} produced an empty file at {dest}")
        self.log.info("Downloaded %s (%d bytes)", dest.name, dest.stat().st_size)
        return dest
