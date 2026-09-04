"""The Makefile is the interface to every launchd job, so a typo in it is a
production bug rather than a formatting nit.

GNU make keeps the LAST recipe for a duplicated target and only prints a warning
that is invisible under `make -s` and easy to miss otherwise. That is how
`make install-recorder` came to run the *uninstall* path: the target was defined
twice and the later copy had the wrong verb. Nothing failed, nothing was logged,
and the recorder would simply have stopped existing the next time it was run.
"""

import re
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"

# A target line: a name at column 0, then a colon. Excludes .PHONY and variables.
TARGET = re.compile(r"^([a-zA-Z][a-zA-Z0-9_-]*)\s*:(?!=)", re.M)


def _targets() -> list[str]:
    return TARGET.findall(MAKEFILE.read_text())


def test_no_duplicate_targets():
    """The bug: make silently keeps the last recipe and inverts the target."""
    seen, duplicated = set(), []
    for name in _targets():
        (duplicated.append(name) if name in seen else seen.add(name))
    assert not duplicated, (
        f"duplicated Makefile target(s): {sorted(set(duplicated))}. "
        "GNU make keeps the LAST recipe and only warns, so the earlier one is "
        "silently dead and the target may do the opposite of its name."
    )


def test_make_itself_reports_no_override_warnings():
    """Belt and braces: ask make, in case the regex misses a form."""
    proc = subprocess.run(
        ["make", "-n", "--warn-undefined-variables", "install-launchd"],
        cwd=MAKEFILE.parent, capture_output=True, text=True,
    )
    assert "overriding commands" not in proc.stderr, proc.stderr


def test_phony_matches_the_targets_that_exist():
    """`uninstall-recorder` was in .PHONY for weeks with no recipe behind it."""
    text = MAKEFILE.read_text()
    phony = set(re.search(r"^\.PHONY:\s*(.+)$", text, re.M).group(1).split())
    defined = set(_targets())
    assert not (phony - defined), f".PHONY names targets that do not exist: {sorted(phony - defined)}"
    assert not (defined - phony), f"targets missing from .PHONY: {sorted(defined - phony)}"


@pytest.mark.parametrize(
    "target,verb",
    [
        ("install-launchd", "install"),
        ("uninstall-launchd", "uninstall"),
        ("install-retry", "install"),
        ("uninstall-retry", "uninstall"),
        ("install-recorder", "install"),
        ("uninstall-recorder", "uninstall"),
    ],
)
def test_install_targets_install_and_uninstall_targets_uninstall(target, verb):
    """The actual regression: a target whose recipe contradicts its name."""
    proc = subprocess.run(
        ["make", "-n", target], cwd=MAKEFILE.parent, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    recipe = proc.stdout.strip()
    assert recipe.endswith(verb), f"`make {target}` runs: {recipe}"


@pytest.mark.parametrize(
    "target,label",
    [
        ("install-retry", "com.school.retry"),
        ("uninstall-retry", "com.school.retry"),
        ("install-recorder", "com.school.recorder"),
        ("uninstall-recorder", "com.school.recorder"),
    ],
)
def test_each_target_addresses_its_own_launchd_label(target, label):
    """A copy-paste that keeps the verb but not the label is just as silent."""
    proc = subprocess.run(
        ["make", "-n", target], cwd=MAKEFILE.parent, capture_output=True, text=True
    )
    assert f"LABEL_OVERRIDE={label}" in proc.stdout, proc.stdout
