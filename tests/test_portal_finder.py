"""Finding a college's Omnivox hostname without asking anyone to read a URL.

Every hostname asserted here was confirmed by fetching it. None was inferred
from a pattern, because the patterns are exactly what is unreliable: cégep
Édouard-Montpetit is `cegepmontpetit`, which drops half its own name, and
Champlain St-Lawrence is `slc`, which keeps none of it.

Nothing in this file touches the network. `fetch` is injected everywhere.
"""

import pytest

from src.portal_finder import find, probe, slugs

OMNIVOX = "<html><head><title>Omnivox - Cégep du Vieux Montréal</title></head>"


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
    assert probe("cvm", fetch=lambda url: OMNIVOX) == "Cégep du Vieux Montréal"


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

    assert find("Édouard-Montpetit", fetch=explode) == (
        "cegepmontpetit",
        "Cégep Édouard-Montpetit",
    )


def test_find_probes_when_the_college_is_not_one_it_knows():
    """Only cvm answers, so the earlier candidates have to be tried and
    rejected before it is reached."""

    def only_cvm(url):
        if url != "https://cvm.omnivox.ca/":
            raise OSError("no such host")
        return OMNIVOX

    assert find("Vieux Montréal", fetch=only_cvm) == ("cvm", "Cégep du Vieux Montréal")


def test_find_gives_up_rather_than_returning_a_guess():
    """Champlain Saint-Lambert is real and none of these spellings reach it.

    Returning the closest candidate would be worse than nothing: it would be
    written into config.yaml and fail later, at a point that looks unrelated.
    """

    def nothing(url):
        raise OSError("no such host")

    assert find("Champlain Saint-Lambert", fetch=nothing) is None
