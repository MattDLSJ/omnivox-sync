"""The run lock is the one primitive where a subtle mistake is silent.

A lock that does not actually lock looks identical to a lock that does, right
up until two Chromiums are writing one browser profile. So these tests use real
subprocesses rather than mocks: flock is a kernel property and mocking it would
only test the mock.
"""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from src.common import RunBusy, run_lock

REPO = Path(__file__).resolve().parents[1]


def _holder(lock_path: Path, hold_seconds: float) -> subprocess.Popen:
    """A separate process that takes the lock and holds it."""
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(REPO)!r})
        from src.common import run_lock
        with run_lock({str(lock_path)!r}):
            print("HELD", flush=True)
            time.sleep({hold_seconds})
    """)
    proc = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True
    )
    assert proc.stdout.readline().strip() == "HELD", "holder never acquired"
    return proc


def test_acquires_and_releases(tmp_path):
    lock = tmp_path / "run.lock"
    with run_lock(lock):
        pass
    with run_lock(lock):  # released, so immediately available again
        pass


def test_lock_file_is_never_deleted(tmp_path):
    """Unlinking it would let the next process lock a different inode, so two
    runs would both believe they hold the lock."""
    lock = tmp_path / "run.lock"
    with run_lock(lock):
        inode = lock.stat().st_ino
    assert lock.exists()
    with run_lock(lock):
        assert lock.stat().st_ino == inode


def test_lock_file_records_the_holding_pid(tmp_path):
    lock = tmp_path / "run.lock"
    with run_lock(lock):
        assert lock.read_text().strip() == str(os.getpid())


def test_second_process_is_refused(tmp_path):
    lock = tmp_path / "run.lock"
    holder = _holder(lock, hold_seconds=5)
    try:
        started = time.monotonic()
        with pytest.raises(RunBusy):
            with run_lock(lock):
                pass
        assert time.monotonic() - started < 1.0, "wait=0 must fail fast, not block"
    finally:
        holder.kill()
        holder.wait()


def test_not_reentrant_within_one_process(tmp_path):
    """Documents the trap: taking the same lock twice self-blocks. Callers must
    never nest two regions that hold the same lock file."""
    lock = tmp_path / "run.lock"
    with run_lock(lock):
        with pytest.raises(RunBusy):
            with run_lock(lock):
                pass


def test_wait_acquires_once_the_holder_finishes(tmp_path):
    lock = tmp_path / "run.lock"
    holder = _holder(lock, hold_seconds=1.0)
    try:
        started = time.monotonic()
        with run_lock(lock, wait=10, poll=0.05):
            waited = time.monotonic() - started
        assert 0.5 < waited < 8, f"expected to wait about a second, waited {waited:.2f}s"
    finally:
        holder.kill()
        holder.wait()


def test_wait_gives_up_and_raises(tmp_path):
    lock = tmp_path / "run.lock"
    holder = _holder(lock, hold_seconds=10)
    try:
        started = time.monotonic()
        with pytest.raises(RunBusy):
            with run_lock(lock, wait=0.4, poll=0.05):
                pass
        assert time.monotonic() - started >= 0.4
    finally:
        holder.kill()
        holder.wait()


def test_sigkill_releases_the_lock(tmp_path):
    """The reason for flock over a pidfile: a killed holder leaves nothing to
    clean up, so there is no stale-lock recovery path that could go wrong."""
    lock = tmp_path / "run.lock"
    holder = _holder(lock, hold_seconds=60)
    os.kill(holder.pid, signal.SIGKILL)
    holder.wait()
    started = time.monotonic()
    with run_lock(lock):  # must succeed with no manual cleanup
        pass
    assert time.monotonic() - started < 1.0


def test_creates_the_parent_directory(tmp_path):
    lock = tmp_path / "state" / "nested" / "run.lock"
    with run_lock(lock):
        assert lock.exists()


def test_lock_is_released_when_the_body_raises(tmp_path):
    lock = tmp_path / "run.lock"
    with pytest.raises(ValueError):
        with run_lock(lock):
            raise ValueError("boom")
    with run_lock(lock):  # a failed run must not wedge the next one
        pass


def test_two_distinct_locks_do_not_block_each_other(tmp_path):
    """omnivox.lock and queue.lock must stay independent: one global lock would
    make a recorder tick wait on a five-minute sync, and miss a class."""
    holder = _holder(tmp_path / "omnivox.lock", hold_seconds=5)
    try:
        with run_lock(tmp_path / "queue.lock"):
            pass
    finally:
        holder.kill()
        holder.wait()
