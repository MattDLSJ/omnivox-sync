"""`_digest.md` is a permanent record that is only ever appended to.

Worth being precise about what changed, because the old `open("a").write()` was
not visibly broken. CPython hands a write larger than its 8 KB buffer straight
to one raw write(), and "a" mode is already O_APPEND, so in practice a block
landed in a single syscall and these tests pass against the old code too. They
are guards on a property, not a reproduction of a bug.

What the old version lacked was a *guarantee*: its atomicity was a consequence
of CPython's buffering, not something the writer asked for, and it never
fsynced, so a block could sit in the page cache while launchd let the Mac
sleep. The writer now states both intentions itself.

The one thing here that is a real regression test is the last one: a future
"fix" that reached for the temp-file-and-os.replace pattern used everywhere
else in this repo would be wrong for an append-only log, because it would
rewrite the whole semester on every sync.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

from src.digest import DigestItem, append_digest

REPO = Path(__file__).resolve().parents[1]


def _item(**kw):
    base = dict(kind="document", title="ch01.pdf", course="", date="2026-09-03")
    base.update(kw)
    return DigestItem(**base)


def test_a_block_lands_whole(tmp_path):
    path = append_digest(tmp_path, [_item(title="Examen final", important=True)])
    body = path.read_text(encoding="utf-8")
    assert body.startswith("\n## ")
    assert body.endswith("\n")
    assert "Examen final" in body


def test_accents_survive_the_byte_level_write(tmp_path):
    """The block is encoded by hand now, so the encoding is the writer's job
    and not open()'s. French course titles are the whole content of this file."""
    path = append_digest(tmp_path, [_item(title="Littérature — été, à l'œuvre")])
    assert "Littérature — été, à l'œuvre" in path.read_text(encoding="utf-8")


def test_concurrent_appends_do_not_interleave_or_overwrite(tmp_path):
    """The property O_APPEND buys. Six processes append at once; every block
    must be present and contiguous, none clobbered by another's offset."""
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(REPO)!r})
        from pathlib import Path
        from src.digest import DigestItem, append_digest
        tag = sys.argv[1]
        append_digest(Path({str(tmp_path)!r}),
                      [DigestItem(kind="document", title=f"bloc-{{tag}}-{{i}}",
                                  course="", date="") for i in range(400)])
    """)
    procs = [subprocess.Popen([sys.executable, "-c", script, str(n)]) for n in range(6)]
    for proc in procs:
        assert proc.wait(timeout=60) == 0

    body = (tmp_path / "_digest.md").read_text(encoding="utf-8")
    for tag in range(6):
        lines = [l for l in body.splitlines() if f"bloc-{tag}-" in l]
        assert len(lines) == 400, f"writer {tag} lost lines: {len(lines)} of 400"
        # Contiguous: its 40 lines must sit in one unbroken run.
        first = body.splitlines().index(lines[0])
        assert body.splitlines()[first:first + 400] == lines, (
            f"writer {tag}'s block was interleaved with another writer's"
        )


def test_the_file_is_never_rewritten_only_extended(tmp_path):
    """Paired with the above so the fix cannot drift into temp-file-and-replace,
    which would rewrite the whole semester on every sync."""
    first = append_digest(tmp_path, [_item(title="premier")])
    before = first.read_text(encoding="utf-8")
    after = append_digest(tmp_path, [_item(title="second")]).read_text(encoding="utf-8")
    assert after.startswith(before)
    assert "premier" in after and "second" in after
