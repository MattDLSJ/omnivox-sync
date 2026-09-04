

def test_a_bare_href_becomes_a_path_not_a_hostname():
    """LEA_HOME + ref is correct only when exactly one side carries the slash.

    LEA serves both shapes: course cards use "/Lea/...", document rows use a
    bare "VisualiseDocument.aspx?...". The bare ones produced
    "...omnivox.caVisualiseDocument.aspx", which is a hostname, so DNS failed
    with ENOTFOUND and six basketball documents silently never downloaded on
    2026-09-02. The error named the file and never the URL, so it read like a
    missing document rather than a broken link.
    """
    from src.omnivox import LEA_HOME, OmnivoxSession

    class _Page:
        url = "https://cegepmontpetit-lea.omnivox.ca/Lea/Documents.aspx"

    session = OmnivoxSession.__new__(OmnivoxSession)
    session.page = _Page()

    got = session._absolute("VisualiseDocument.aspx?IDDoc=abc")
    assert "omnivox.caVisualiseDocument" not in got
    assert got == "https://cegepmontpetit-lea.omnivox.ca/Lea/VisualiseDocument.aspx?IDDoc=abc"

    # A leading slash means the site root, not the current directory.
    assert session._absolute("/Lea/X.aspx") == "https://cegepmontpetit-lea.omnivox.ca/Lea/X.aspx"
    # An absolute URL is left alone.
    assert session._absolute("https://other.example/x") == "https://other.example/x"
    assert LEA_HOME.startswith("https://")


def test_absolute_still_works_before_any_page_is_loaded():
    """page.url is empty on a fresh context, and about:blank is not a base."""
    from src.omnivox import OmnivoxSession

    class _Page:
        url = "about:blank"

    session = OmnivoxSession.__new__(OmnivoxSession)
    session.page = _Page()
    got = session._absolute("VisualiseDocument.aspx?IDDoc=abc")
    assert got == "https://cegepmontpetit-lea.omnivox.ca/VisualiseDocument.aspx?IDDoc=abc"


def test_a_lea_path_never_resolves_against_the_omnivox_portal():
    """Omnivox and LEA are two different hosts.

    Course hrefs are LEA paths, and they are read while the browser may still be
    on the Omnivox portal. Resolving one against the other sends the request to
    cegepmontpetit.omnivox.ca/HttpError/HttpError.ovx?Code=404, which is what
    the first version of _absolute did to Histoire.
    """
    from src.omnivox import LEA_HOME, OmnivoxSession

    class _Portal:
        url = "https://cegepmontpetit.omnivox.ca/Login/Account/Login.aspx"

    session = OmnivoxSession.__new__(OmnivoxSession)
    session.page = _Portal()

    got = session._absolute("/Lea/Documents.aspx?Ref=123")
    assert got.startswith(LEA_HOME), got
    assert "cegepmontpetit.omnivox.ca/Lea" not in got
    assert got == "https://cegepmontpetit-lea.omnivox.ca/Lea/Documents.aspx?Ref=123"
