"""Probe shared debugger variable access models."""

from __future__ import annotations

import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_variable_access import (  # noqa: E402
    DebugResolvedVariable,
    DebugVariableReadRequest,
    DebugVariableReadResult,
    DebugVariableSmokeResult,
    DebugVariableWriteRequest,
    DebugVariableWriteResult,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    read_request = DebugVariableReadRequest(
        expression="debug_setpoint",
        binary_path=Path("demo.axf"),
        connection_name="ProbeRead",
    )
    write_request = DebugVariableWriteRequest(
        expression=read_request.expression,
        value_text="6000",
        binary_path=read_request.binary_path,
        connection_name="ProbeWrite",
    )
    _assert(read_request.binary_path == write_request.binary_path, "request binary path mismatch")
    resolved = DebugResolvedVariable(
        expression="debug_setpoint",
        symbol="debug_setpoint",
        address=0x20000008,
        size=4,
        type_name="int",
        source="probe",
        ram_checked=True,
    )
    read = DebugVariableReadResult(
        attempted=True,
        read=True,
        expression="debug_setpoint",
        method="memory",
        backend="keil",
        resolved=resolved,
        raw=(5000).to_bytes(4, "little", signed=True),
        value="5000",
    )
    write = DebugVariableWriteResult(
        attempted=True,
        written=True,
        expression="debug_setpoint",
        value_text="6000",
        method="memory",
        backend="keil",
        resolved=resolved,
        old_raw=read.raw,
        new_raw=(6000).to_bytes(4, "little", signed=True),
        readback_raw=(6000).to_bytes(4, "little", signed=True),
        old_value="5000",
        readback_value="6000",
        attempts=("memory",),
    )
    smoke = DebugVariableSmokeResult(read=read, write=write)
    _assert(smoke.succeeded, smoke.summary())
    record = smoke.write.to_record()
    _assert(record["backend"] == "keil", f"backend missing: {record!r}")
    _assert(record["resolved"]["address"] == "0x20000008", f"address record mismatch: {record!r}")
    _assert("6000" in smoke.summary() and "写前 5000" in smoke.summary(), smoke.summary())

    print("PASS debug variable access probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
