"""Probe shutdown lifecycle sequencing without Qt or hardware."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.lifecycle import ShutdownSequence  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    calls: list[str] = []
    sequence = ShutdownSequence()
    sequence.run("first", lambda: calls.append("first"))
    sequence.run("soft timeout", lambda: (False, "worker still alive"))

    def broken() -> None:
        calls.append("broken")
        raise RuntimeError("synthetic shutdown failure")

    sequence.run("exception", broken)
    sequence.run("after exception", lambda: calls.append("after"))
    report = sequence.report()
    record = report.to_record()
    json.dumps(record, ensure_ascii=False, sort_keys=True)

    _assert(calls == ["first", "broken", "after"], f"shutdown sequence stopped unexpectedly: {calls!r}")
    _assert(not report.ok, "report should be non-ok after false/exception steps")
    _assert(report.step("first") is not None and report.step("first").ok, "first step missing")
    soft = report.step("soft timeout")
    _assert(soft is not None and not soft.ok and "worker" in soft.detail, "soft timeout detail missing")
    failure = report.step("exception")
    _assert(failure is not None and not failure.ok and "synthetic" in failure.error, "exception detail missing")
    _assert(record["elapsed_ms"] >= 0, "elapsed time missing")

    print("PASS lifecycle shutdown probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
