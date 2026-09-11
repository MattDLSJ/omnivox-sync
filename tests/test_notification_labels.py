"""A course code must never reach a notification.

"1 new document(s): 109-321-EM" means nothing on a lock screen at 07:30, and
the fix shipped twice before it stuck: the first pass corrected the digest and
left the same bug in three other call sites, because each one looked the course
up independently instead of going through Config.labels().

So this file guards the property rather than the four known call sites. The
static check below walks every notify() in src/ and fails if a course code can
reach it, which means a fifth notification added later is covered too.
"""

import ast
import re
from pathlib import Path

import pytest

from src.common import human_courses, human_scope, load_config

SRC = Path(__file__).resolve().parents[1] / "src"

# "109-321-EM", "601-102-MQ": three digits, three alnum, two letters.
COURSE_CODE = re.compile(r"\b\d{3}-[A-Z0-9]{3}-[A-Z]{2}\b")

# Reading a code off a record is fine; emitting it is not. These are the ways a
# code is allowed to become human before it reaches a notification.
LABEL_HELPERS = ("human_courses", "human_scope", "labels()", ".label()")


@pytest.fixture
def cfg(write_config, tmp_repo):
    """A real Config, so a change to labels() breaks these tests too."""
    return load_config(
        write_config({
            "courses": [
                {"code": "109-321-EM", "omnivox_name": "BASKET", "folder": "Basketball",
                 "notebook": "nb", "short": "Basket"},
                {"code": "330-704-EM", "omnivox_name": "HIST", "folder": "Histoire du monde",
                 "notebook": "nb", "short": "Histoire"},
                # no `short`: the first word of the folder stands in
                {"code": "601-102-MQ", "omnivox_name": "LITT",
                 "folder": "Littérature et imaginaire", "notebook": "nb"},
            ],
        }),
        repo_root=tmp_repo,
    )


# ---------------------------------------------------------------------------
# The helpers themselves
# ---------------------------------------------------------------------------


def test_human_courses_replaces_every_code_with_a_label(cfg):
    out = human_courses(cfg, {"109-321-EM", "330-704-EM", "601-102-MQ"})
    assert not COURSE_CODE.search(out), out
    assert out == "Basket, Histoire, Littérature"


def test_human_courses_is_sorted_so_the_text_is_stable(cfg):
    assert human_courses(cfg, {"330-704-EM", "109-321-EM"}) == human_courses(
        cfg, {"109-321-EM", "330-704-EM"}
    )


def test_human_courses_falls_back_to_short_then_folder(cfg):
    # 601-102-MQ has no `short`, so the first word of the folder stands in.
    assert human_courses(cfg, {"601-102-MQ"}) == "Littérature"


def test_unknown_code_passes_through_rather_than_vanishing(cfg):
    """A course dropped from config.yaml must still be nameable in an error."""
    assert human_courses(cfg, {"999-999-XX"}) == "999-999-XX"


def test_human_scope_labels_the_course_and_keeps_the_filename(cfg):
    assert human_scope(cfg, "330-704-EM/notes.pdf") == "Histoire/notes.pdf"


def test_human_scope_leaves_a_non_course_scope_alone(cfg):
    assert human_scope(cfg, "notebooklm") == "notebooklm"


def test_human_scope_handles_a_bare_course_code(cfg):
    assert human_scope(cfg, "109-321-EM") == "Basket"


# ---------------------------------------------------------------------------
# The static guard: no code may reach any notify() in src/
# ---------------------------------------------------------------------------


def _notify_arguments(path: Path):
    """Yield (function, text) for every notify() argument in a module.

    Local variables referenced by the argument are expanded one level, because
    that is precisely what hid the bug: the code was read into `courses_touched`
    on one line and interpolated on the next.
    """
    # encoding, because Windows defaults to cp1252 and this file is full of
    # French accents: without it the test dies on UnicodeDecodeError there and
    # passes everywhere else.
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assigned: dict[str, str] = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned[target.id] = ast.get_source_segment(source, node.value) or ""
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "notify"):
                continue
            for arg in node.args:
                text = ast.get_source_segment(source, arg) or ""
                for name, rhs in assigned.items():
                    if re.search(rf"\b{re.escape(name)}\b", text):
                        text += " " + rhs
                yield f"{path.name}:{fn.name}", text


ALL_NOTIFY_ARGS = [
    pytest.param(where, text, id=f"{where}-{i}")
    for i, (where, text) in enumerate(
        arg for path in sorted(SRC.glob("*.py")) for arg in _notify_arguments(path)
    )
]


def test_the_guard_actually_found_the_notify_calls():
    """If a refactor renames notify(), this file would silently pass forever."""
    assert len(ALL_NOTIFY_ARGS) >= 10


@pytest.mark.parametrize("where,text", ALL_NOTIFY_ARGS)
def test_no_course_code_reaches_a_notification(where, text):
    mentions_code = "course_code" in text or ".code" in text
    if not mentions_code:
        return
    assert any(helper in text for helper in LABEL_HELPERS), (
        f"{where} builds a notification from a raw course code.\n"
        f"  {text.strip()[:200]}\n"
        f"Route it through one of {LABEL_HELPERS}: a code is unreadable on a phone."
    )


def test_guard_catches_a_raw_code(tmp_path):
    """The guard is only worth having if it fails on the original bug."""
    bad = tmp_path / "regression.py"
    bad.write_text(
        "def main(cfg, result):\n"
        '    courses = sorted({d["course_code"] for d in result.downloaded})\n'
        '    notify("School sync", f"new: {\', \'.join(courses)}")\n'
    )
    args = list(_notify_arguments(bad))
    leaked = [t for _, t in args if "course_code" in t]
    assert leaked, "the guard failed to follow the variable into the notify() call"
    assert not any(h in leaked[0] for h in LABEL_HELPERS)
