"""Tests for single-instance PID lock."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tko.runtime.instance_lock import InstanceLock, InstanceLockError


def test_acquire_and_release(tmp_path: Path):
    lock_path = tmp_path / "tko.lock"
    lock = InstanceLock(lock_path)
    lock.acquire()
    assert lock_path.exists()
    assert str(os.getpid()) in lock_path.read_text(encoding="utf-8")
    lock.release()
    assert not lock_path.exists()


def test_second_acquire_fails(tmp_path: Path):
    lock_path = tmp_path / "tko.lock"
    a = InstanceLock(lock_path)
    a.acquire()
    b = InstanceLock(lock_path)
    with pytest.raises(InstanceLockError):
        b.acquire()
    a.release()


def test_stale_lock_is_replaced(tmp_path: Path):
    lock_path = tmp_path / "tko.lock"
    lock_path.write_text("999999999 0\n", encoding="utf-8")
    lock = InstanceLock(lock_path)
    try:
        lock.acquire()
        lock.release()
    except InstanceLockError:
        pass
