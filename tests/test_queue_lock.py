"""state/upload_queue.json has two independent appenders and one drain.

The sync appends downloaded documents; the recorder appends a transcript. Both
did `write(read() + [...])` with nothing serialising them, so an interleave lost
one side entirely. Silently: StateStore writes atomically, so the file is never
corrupt, it just comes out missing an entry. The lost file is already recorded
as downloaded or already transcribed, so nothing ever re-queues it.

Two locks, and they must stay separate:

  queue.lock   milliseconds, around any read-modify-write of the queue
  upload.lock  the whole drain, so two drains cannot both upload every item

The drain must never hold queue.lock while uploading. `nlm source add --wait`
runs up to 900s per file, and the recorder appends through queue.lock; blocking
a recorder tick risks missing the start of a class.
"""

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from src.common import RunBusy, StateStore, run_lock

REPO = Path(__file__).resolve().parents[1]


def _appender(queue_path: Path, lock_path: Path, name: str, hold: float = 0.0) -> str:
    """A separate process that appends one record through the lock."""
    return textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(REPO)!r})
        from src.common import StateStore, run_lock
        store = StateStore({str(queue_path)!r})
        with run_lock({str(lock_path)!r}, wait=30):
            current = store.read()
            time.sleep({hold})           # widen the window on purpose
            store.write(current + [{{"filename": {name!r}}}])
        print("done", flush=True)
    """)


def test_two_concurrent_appends_both_survive(tmp_path):
    """The actual bug. Without the lock the slower writer wins and the other
    record is gone with no error."""
    queue = tmp_path / "upload_queue.json"
    lock = tmp_path / "queue.lock"
    StateStore(queue).write([])

    procs = [
        subprocess.Popen([sys.executable, "-c", _appender(queue, lock, name, hold=0.4)])
        for name in ("from-sync.pdf", "from-recorder.md")
    ]
    for proc in procs:
        assert proc.wait(timeout=30) == 0

    names = {r["filename"] for r in json.loads(queue.read_text())}
    assert names == {"from-sync.pdf", "from-recorder.md"}, (
        f"an append was lost: {names}. This is the silent failure the lock exists to stop."
    )


def test_without_the_lock_an_append_really_is_lost(tmp_path):
    """Proves the test above is not vacuous by reproducing the original bug:
    same two writers, each using its own lock file, so neither excludes the
    other."""
    queue = tmp_path / "upload_queue.json"
    StateStore(queue).write([])

    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _appender(queue, tmp_path / f"{name}.lock", name, hold=0.4)]
        )
        for name in ("from-sync.pdf", "from-recorder.md")
    ]
    for proc in procs:
        proc.wait(timeout=30)

    names = {r["filename"] for r in json.loads(queue.read_text())}
    assert len(names) == 1, f"expected a lost update without a shared lock, got {names}"


def test_an_append_waits_rather_than_failing(tmp_path):
    """Appends pass wait=30. The lock is only ever held for milliseconds, so
    waiting is free; failing would strand a transcript forever."""
    lock = tmp_path / "queue.lock"
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(REPO)!r})
        from src.common import run_lock
        with run_lock({str(lock)!r}):
            print("HELD", flush=True)
            time.sleep(1.0)
    """)
    holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "HELD"
    try:
        started = time.monotonic()
        with run_lock(lock, wait=30, poll=0.05):
            waited = time.monotonic() - started
        assert 0.5 < waited < 10
    finally:
        holder.kill()
        holder.wait()


def test_queue_and_upload_locks_are_independent(tmp_path):
    """A drain holding upload.lock for minutes must never block the recorder's
    append. If these were one lock, a slow NotebookLM upload could make the
    recorder miss a class."""
    with run_lock(tmp_path / "upload.lock"):
        with run_lock(tmp_path / "queue.lock", wait=0):
            pass  # must not raise


def test_a_second_drain_is_refused_not_queued(tmp_path):
    """Two drains would both read the queue and both upload every item, which
    surfaces as duplicate sources in NotebookLM. The second must give up, not
    wait and then do the redundant work anyway."""
    lock = tmp_path / "upload.lock"
    with run_lock(lock):
        started = time.monotonic()
        with pytest.raises(RunBusy):
            with run_lock(lock, wait=0):
                pass
        assert time.monotonic() - started < 1.0


def test_upload_queue_skips_when_another_drain_holds_the_lock(
    write_config, tmp_repo, monkeypatch
):
    """End to end through the real entry point."""
    from src.common import load_config
    from src.notebooklm_upload import upload_queue

    cfg = load_config(write_config(), repo_root=tmp_repo)
    StateStore(tmp_repo / "state" / "upload_queue.json").write(
        [{"course_code": "601-101-MQ", "notebook": "nb", "path": "/x.pdf", "filename": "x.pdf"}]
    )

    def explode(*a, **k):
        pytest.fail("a second drain uploaded while the first held upload.lock")

    with run_lock(tmp_repo / "state" / "upload.lock"):
        result = upload_queue(cfg, explode, logger=None)
    assert result.uploaded == [] if hasattr(result, "uploaded") else True
