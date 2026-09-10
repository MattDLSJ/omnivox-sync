"""Finding a college's Omnivox hostname without asking anyone to read a URL.

Every hostname asserted here was confirmed by fetching it. None was inferred
from a pattern, because the patterns are exactly what is unreliable: cégep
Édouard-Montpetit is `cegepmontpetit`, which drops half its own name, and
Champlain St-Lawrence is `slc`, which keeps none of it.

Nothing in this file touches the network. `fetch` is injected everywhere.
"""

import pytest

from src.portal_finder import find, language_of, probe, slugs

OMNIVOX = (
    "<html><head><title>Omnivox - Cégep du Vieux Montréal</title></head>"
    "<body>Mot de passe</body></html>"
)
ENGLISH = (
    "<html><head><title>Omnivox - Dawson College</title></head>"
    "<body>Student number ... Forgot your password?</body></html>"
)


@pytest.mark.parametrize(
    "college,hostname",
    [
        # The half-name case. "Edouard" is nowhere in the hostname.
        ("Cégep Édouard-Montpetit", "cegepmontpetit"),
        ("Edouard-Montpetit", "cegepmontpetit"),
        # The whole name, once the word "Collège" is dropped.
        ("Collège Ahuntsic", "collegeahuntsic"),
        # The initials family, where the leading c is the dropped "cégep".
        ("Cégep du Vieux Montréal", "cvm"),
        # "Saint" abbreviates to st rather than initialising to s. Getting
        # this one word wrong misses every Saint-something college there is.
        ("Cégep de Saint-Jérôme", "cstj"),
    ],
)
def test_the_real_hostname_is_among_the_candidates(college, hostname):
    assert hostname in slugs(college)


def test_accents_do_not_have_to_survive_into_a_hostname():
    """É is not a legal hostname character, so folding it is the whole job."""
    assert all("é" not in slug and "É" not in slug for slug in slugs("Édouard-Montpetit"))


def test_candidate_list_stays_short():
    """Each candidate is one request against somebody else's server."""
    assert len(slugs("Cégep régional de Lanaudière à Terrebonne")) <= 8


def test_candidates_are_not_repeated():
    assert len(slugs("Ahuntsic")) == len(set(slugs("Ahuntsic")))


def test_a_name_of_nothing_but_noise_still_returns_something():
    """"cégep" alone carries no information, but returning [] would leave the
    caller with nothing to probe and no explanation."""
    assert slugs("cégep")[0] == "cegep"


def test_probe_reads_the_college_name_out_of_the_page_title():
    """The point of probing rather than guessing: the answer identifies itself,
    so a person can tell at a glance whether it is their school."""
    assert probe("cvm", fetch=lambda url: OMNIVOX)[0] == "Cégep du Vieux Montréal"


def test_probe_rejects_a_host_that_is_not_omnivox():
    served = "<html><head><title>Welcome to nginx</title></head>"
    assert probe("whatever", fetch=lambda url: served) is None


def test_probe_rejects_a_hostname_that_does_not_resolve():
    """The common case, and the reason a wrong guess cannot pass for a right
    one: a hostname nobody registered fails before it can answer anything."""

    def refuse(url):
        raise OSError("nodename nor servname provided")

    assert probe("notarealcegepxyz", fetch=refuse) is None


def test_probe_asks_for_the_right_url():
    seen = []
    probe("slc", fetch=lambda url: seen.append(url) or OMNIVOX)
    assert seen == ["https://slc.omnivox.ca/"]


def test_find_answers_a_known_college_without_touching_the_network():
    def explode(url):
        raise AssertionError(f"should not have fetched {url}")

    got = find("Édouard-Montpetit", fetch=explode)
    assert (got.slug, got.college, got.language) == (
        "cegepmontpetit",
        "Cégep Édouard-Montpetit",
        "fr",
    )


def test_find_probes_when_the_college_is_not_one_it_knows():
    """Only cvm answers, so the earlier candidates have to be tried and
    rejected before it is reached."""

    def only_cvm(url):
        if url != "https://cvm.omnivox.ca/":
            raise OSError("no such host")
        return OMNIVOX

    got = find("Vieux Montréal", fetch=only_cvm)
    assert (got.slug, got.college) == ("cvm", "Cégep du Vieux Montréal")


def test_find_gives_up_rather_than_returning_a_guess():
    """Champlain Saint-Lambert is real and none of these spellings reach it.

    Returning the closest candidate would be worse than nothing: it would be
    written into config.yaml and fail later, at a point that looks unrelated.
    """

    def nothing(url):
        raise OSError("no such host")

    assert find("Champlain Saint-Lambert", fetch=nothing) is None


def test_a_partial_college_name_does_not_confidently_pick_a_campus():
    """"Champlain" is a substring of "Cégep Champlain-St.Lawrence", and the
    question START-HERE asks is "which cégep do you attend, in plain words".
    A student at Champlain Saint-Lambert answering exactly as asked was handed
    St-Lawrence's portal, with no network check, presented as confirmed.

    Falling through to probing cannot make that mistake: a hostname nobody
    registered does not answer.
    """

    def nothing(url):
        raise OSError("no such host")

    assert find("Champlain", fetch=nothing) is None


def test_the_full_name_still_matches_the_known_college():
    def explode(url):
        raise AssertionError("should not have needed the network")

    got = find("Champlain St-Lawrence", fetch=explode)
    assert (got.slug, got.college) == ("slc", "Cégep Champlain-St.Lawrence")


def test_extra_words_do_not_stop_a_known_college_matching():
    """"I go to cégep Édouard-Montpetit in Longueuil" still identifies it."""

    def explode(url):
        raise AssertionError("should not have needed the network")

    assert find("cégep Édouard-Montpetit in Longueuil", fetch=explode)[0] == "cegepmontpetit"


# ---------------------------------------------------------------------------
# Language, which nobody should have to be asked about
#
# The setup used to ask "is your Omnivox interface in French or English?".
# That is a question about a config file wearing the costume of a question
# about somebody's life: they do not know why it is being asked, and they
# cannot judge the cost of getting it wrong. The cost is that the scraper
# navigates by clicking French text, finds nothing, and reports "no documents"
# cheerfully, forever.
#
# The sign-in page is public and answers it. The markers below were read off
# three live portals, not guessed.
# ---------------------------------------------------------------------------


def test_a_french_portal_is_recognised_as_french():
    assert language_of("<html><body>Mot de passe</body></html>") == "fr"


def test_an_english_portal_is_recognised_as_english():
    assert language_of("<html><body>Student number</body></html>") == "en"


def test_the_word_password_alone_decides_nothing():
    """It appears on every Omnivox page in both languages as an HTML input
    type, so treating it as an English marker would call Édouard-Montpetit an
    English college."""
    assert language_of('<input type="password">') == ""


def test_a_page_that_says_nothing_either_way_returns_nothing():
    """Better to say "could not tell" than to pick one and be silently wrong
    on every course."""
    assert language_of("<html><body>Omnivox</body></html>") == ""


def test_a_page_carrying_both_languages_is_not_guessed_at():
    both = "<html><body>Mot de passe / Student number</body></html>"
    assert language_of(both) == ""


def test_probe_reports_the_language_with_the_college():
    assert probe("dawson", fetch=lambda url: ENGLISH) == ("Dawson College", "en")


def test_find_carries_the_language_through():
    got = find("Dawson", fetch=lambda url: ENGLISH)
    assert got.language == "en"
