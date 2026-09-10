"""Change one setting in config.yaml without flattening the rest of it.

The obvious way is yaml.safe_load, mutate, yaml.safe_dump. It works, and it
throws away every comment in the file. That matters more here than it usually
does, because config.example.yaml is mostly comments: what "staging" means,
why a topic has no password, which fields are macOS-only. Those comments are
the documentation, they are the only documentation at the point of use, and a
setup tool that silently deletes all of them the first time it runs is doing
more harm than the setting is worth.

So this edits the TEXT. It handles exactly two shapes, because those are the
only two this project's config has:

    key: value                    a top-level scalar
    parent:
      key: value                  one level down

Anything deeper raises rather than guessing. A config editor that quietly
writes to the wrong place is worse than one that refuses.
"""

from __future__ import annotations

import re

#: A "key: value" line, capturing indent, key, value and any trailing comment.
_LINE = re.compile(r"^(?P<indent>[ ]*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ ]*:(?P<rest>.*)$")


class ConfigEditError(Exception):
    """The file is not shaped the way this can safely edit."""


def _format(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '""'
    if isinstance(value, (int, float)):
        return str(value)
    # Always quoted. It matches how the rest of the file is written, and it
    # removes a whole category of accident: `portal: no` is the boolean False
    # in YAML, and `group: 1010` is an integer that then fails a string
    # comparison somewhere far away from here.
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _trailing_comment(rest: str) -> str:
    """Keep an inline comment, which is usually the only explanation there is.

    Only outside quotes: `topic: "a # b"` has no comment in it.
    """
    in_single = in_double = False
    for index, char in enumerate(rest):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return "  " + rest[index:].strip()
    return ""


def set_value(text: str, path: list[str], value) -> str:
    """Return `text` with `path` set to `value`, everything else untouched."""
    if not 1 <= len(path) <= 2:
        raise ConfigEditError(
            f"can only set a top-level key or one nested under it, not {path!r}"
        )
    lines = text.splitlines()

    if len(path) == 1:
        for index, line in enumerate(lines):
            found = _LINE.match(line)
            if found and not found.group("indent") and found.group("key") == path[0]:
                lines[index] = (
                    f"{path[0]}: {_format(value)}"
                    + _trailing_comment(found.group("rest"))
                )
                return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        lines.append(f"{path[0]}: {_format(value)}")
        return "\n".join(lines) + "\n"

    parent, key = path
    start = None
    for index, line in enumerate(lines):
        found = _LINE.match(line)
        if found and not found.group("indent") and found.group("key") == parent:
            start = index
            break
    if start is None:
        lines.append(f"{parent}:")
        lines.append(f"  {key}: {_format(value)}")
        return "\n".join(lines) + "\n"

    # The parent's block runs until the next line at column 0 that is not a
    # comment or blank. A comment sitting between two settings belongs to the
    # one below it, so it must not end the block.
    child_indent = None
    for index in range(start + 1, len(lines) + 1):
        if index == len(lines):
            break
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        found = _LINE.match(line)
        if found and not found.group("indent"):
            break
        if found:
            if child_indent is None:
                child_indent = found.group("indent")
            if found.group("key") == key and found.group("indent") == child_indent:
                lines[index] = (
                    f"{child_indent}{key}: {_format(value)}"
                    + _trailing_comment(found.group("rest"))
                )
                return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    # The parent exists but has no such child. Insert it directly under the
    # parent line, before its comments, so it cannot land in another block.
    lines.insert(start + 1, f"{child_indent or '  '}{key}: {_format(value)}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def set_many(text: str, settings: dict) -> str:
    """`{"notebooklm.mode": "staging", "transcribe": False}` in one pass."""
    for dotted, value in settings.items():
        text = set_value(text, dotted.split("."), value)
    return text
