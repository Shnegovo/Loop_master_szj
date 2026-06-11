"""Probe Keil backend explicit live-write adapter entry point without hardware."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.core.keil.backend as backend_module  # noqa: E402
from src.core.keil.backend import KeilBackendConfig, KeilUvSockBackendAdapter  # noqa: E402
from src.core.keil.live_write import (  # noqa: E402
    KeilLiveVariableReadRequest,
    KeilLiveVariableReadResult,
    KeilLiveVariableSmokeResult,
    KeilLiveVariableWriteRequest,
    KeilLiveVariableWriteResult,
    KeilResolvedVariable,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    calls: list[tuple[KeilLiveVariableWriteRequest, Path | None, int, bool]] = []
    read_calls: list[tuple[KeilLiveVariableReadRequest, Path | None, int, bool]] = []
    smoke_calls: list[tuple[KeilLiveVariableWriteRequest, Path | None, int, bool, bool]] = []

    def fake_write_existing(request, *, keil_root, port, require_debug=True):
        calls.append((request, Path(keil_root) if keil_root else None, int(port), bool(require_debug)))
        return KeilLiveVariableWriteResult(
            attempted=True,
            written=True,
            expression=request.expression,
            value_text=request.value_text,
            method="memory",
            resolved=KeilResolvedVariable(
                expression=request.expression,
                symbol=request.expression,
                address=0x20000008,
                size=4,
                type_name="int",
                source="probe",
                ram_checked=True,
            ),
            old_raw=(5000).to_bytes(4, "little", signed=True),
            new_raw=(6000).to_bytes(4, "little", signed=True),
            readback_raw=(6000).to_bytes(4, "little", signed=True),
            old_value="5000",
            readback_value="6000",
            attempts=("memory",),
        )

    def fake_read_existing(request, *, keil_root, port, require_debug=True):
        read_calls.append((request, Path(keil_root) if keil_root else None, int(port), bool(require_debug)))
        return KeilLiveVariableReadResult(
            attempted=True,
            read=True,
            expression=request.expression,
            method="memory",
            resolved=KeilResolvedVariable(
                expression=request.expression,
                symbol=request.expression,
                address=0x20000008,
                size=4,
                type_name="int",
                source="probe",
                ram_checked=True,
            ),
            raw=(5000).to_bytes(4, "little", signed=True),
            value="5000",
        )

    def fake_smoke_existing(request, *, keil_root, port, require_debug=True, read_before_write=True):
        smoke_calls.append(
            (
                request,
                Path(keil_root) if keil_root else None,
                int(port),
                bool(require_debug),
                bool(read_before_write),
            )
        )
        return KeilLiveVariableSmokeResult(
            read=KeilLiveVariableReadResult(
                attempted=True,
                read=True,
                expression=request.expression,
                method="memory",
                resolved=KeilResolvedVariable(
                    expression=request.expression,
                    symbol=request.expression,
                    address=0x20000008,
                    size=4,
                    type_name="int",
                    source="probe",
                    ram_checked=True,
                ),
                raw=(5000).to_bytes(4, "little", signed=True),
                value="5000",
            ),
            write=KeilLiveVariableWriteResult(
                attempted=True,
                written=True,
                expression=request.expression,
                value_text=request.value_text,
                method="memory",
                resolved=KeilResolvedVariable(
                    expression=request.expression,
                    symbol=request.expression,
                    address=0x20000008,
                    size=4,
                    type_name="int",
                    source="probe",
                    ram_checked=True,
                ),
                old_raw=(5000).to_bytes(4, "little", signed=True),
                new_raw=(6000).to_bytes(4, "little", signed=True),
                readback_raw=(6000).to_bytes(4, "little", signed=True),
                old_value="5000",
                readback_value="6000",
                attempts=("memory",),
            ),
        )

    original = backend_module.write_keil_live_variable_existing
    original_read = backend_module.read_keil_live_variable_existing
    original_smoke = backend_module.run_keil_live_variable_smoke_existing
    try:
        backend_module.write_keil_live_variable_existing = fake_write_existing
        backend_module.read_keil_live_variable_existing = fake_read_existing
        backend_module.run_keil_live_variable_smoke_existing = fake_smoke_existing
        adapter = KeilUvSockBackendAdapter(KeilBackendConfig(root=Path("D:/Keil"), port=4827))
        read_request = KeilLiveVariableReadRequest(
            expression="debug_setpoint",
            axf_path=Path("D:/demo/demo.axf"),
        )
        read_result = adapter.read_live_variable(read_request)
        request = KeilLiveVariableWriteRequest(
            expression="debug_setpoint",
            value_text="6000",
            axf_path=Path("D:/demo/demo.axf"),
        )
        result = adapter.write_live_variable(request)
        smoke_result = adapter.run_live_variable_smoke(request, read_before_write=True)
    finally:
        backend_module.write_keil_live_variable_existing = original
        backend_module.read_keil_live_variable_existing = original_read
        backend_module.run_keil_live_variable_smoke_existing = original_smoke

    _assert(read_result.read, read_result.summary())
    _assert(read_result.value == "5000", f"read value mismatch: {read_result.value}")
    _assert(result.written, result.summary())
    _assert(result.method == "memory", f"method mismatch: {result.method}")
    _assert(result.resolved is not None and result.resolved.address == 0x20000008, "resolved address mismatch")
    _assert(len(calls) == 1, f"expected one write call, got {len(calls)}")
    call_request, root, port, require_debug = calls[0]
    _assert(call_request is request, "adapter should pass request through")
    _assert(root == Path("D:/Keil"), f"root mismatch: {root}")
    _assert(port == 4827, f"port mismatch: {port}")
    _assert(require_debug, "live write should require debug by default")
    _assert(len(read_calls) == 1, f"expected one read call, got {len(read_calls)}")
    read_call_request, read_root, read_port, read_require_debug = read_calls[0]
    _assert(read_call_request is read_request, "adapter should pass read request through")
    _assert(read_root == Path("D:/Keil"), f"read root mismatch: {read_root}")
    _assert(read_port == 4827, f"read port mismatch: {read_port}")
    _assert(read_require_debug, "live read should require debug by default")
    _assert(smoke_result.succeeded, smoke_result.summary())
    _assert(smoke_result.read is not None and smoke_result.read.value == "5000", "smoke should include read-before-write")
    _assert(len(smoke_calls) == 1, f"expected one smoke call, got {len(smoke_calls)}")
    smoke_request, smoke_root, smoke_port, smoke_require_debug, smoke_read_before_write = smoke_calls[0]
    _assert(smoke_request is request, "adapter should pass smoke request through")
    _assert(smoke_root == Path("D:/Keil"), f"smoke root mismatch: {smoke_root}")
    _assert(smoke_port == 4827, f"smoke port mismatch: {smoke_port}")
    _assert(smoke_require_debug, "live smoke should require debug by default")
    _assert(smoke_read_before_write, "live smoke should keep read-before-write enabled")

    print("PASS keil backend live write adapter probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
