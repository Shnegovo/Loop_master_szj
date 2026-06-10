"""No-hardware probe for SWDBackend disconnect result reporting."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.mem_backend import SWDBackend  # noqa: E402


class FakeSession:
    def __init__(self, delay: float = 0.0, fail: bool = False) -> None:
        self.delay = max(0.0, float(delay))
        self.fail = bool(fail)
        self.closed = False

    def close(self) -> None:
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("synthetic close failure")
        self.closed = True


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _backend_with_session(session: FakeSession) -> SWDBackend:
    backend = SWDBackend()
    backend._session = session
    backend._connected = True
    backend._ap = object()
    backend._target = object()
    return backend


def main() -> int:
    ok_session = FakeSession()
    ok_backend = _backend_with_session(ok_session)
    _assert(ok_backend.disconnect(timeout=0.2), "successful close should return true")
    _assert(ok_session.closed, "successful close did not call session.close")
    _assert(not ok_backend.is_connected, "backend should be disconnected after close")

    fail_backend = _backend_with_session(FakeSession(fail=True))
    _assert(not fail_backend.disconnect(timeout=0.2), "failed close should return false")
    _assert("synthetic close failure" in fail_backend.last_error, "close failure should be reported")

    slow_backend = _backend_with_session(FakeSession(delay=0.4))
    _assert(not slow_backend.disconnect(timeout=0.02), "slow close should return false on timeout")
    _assert("超时" in slow_backend.last_error, "timeout should be reported")

    locked_backend = _backend_with_session(FakeSession())
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock() -> None:
        locked_backend._io_lock.acquire()
        try:
            lock_acquired.set()
            release_lock.wait(1.0)
        finally:
            locked_backend._io_lock.release()

    holder = threading.Thread(target=hold_lock, name="swd-disconnect-lock-holder")
    holder.start()
    _assert(lock_acquired.wait(1.0), "lock holder did not acquire the backend lock")
    try:
        _assert(not locked_backend.disconnect(timeout=0.01), "lock timeout should return false")
        _assert("超时" in locked_backend.last_error, "lock timeout should be reported")
    finally:
        release_lock.set()
        holder.join(1.0)

    # Give any daemon close worker from the timeout path a moment to finish so the probe exits quietly.
    threading.Event().wait(0.05)
    print("PASS swd disconnect probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
