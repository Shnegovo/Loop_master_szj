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
    KeilLiveVariableWriteRequest,
    KeilLiveVariableWriteResult,
    KeilResolvedVariable,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    calls: list[tuple[KeilLiveVariableWriteRequest, Path | None, int, bool]] = []

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

    original = backend_module.write_keil_live_variable_existing
    try:
        backend_module.write_keil_live_variable_existing = fake_write_existing
        adapter = KeilUvSockBackendAdapter(KeilBackendConfig(root=Path("D:/Keil"), port=4827))
        request = KeilLiveVariableWriteRequest(
            expression="debug_setpoint",
            value_text="6000",
            axf_path=Path("D:/demo/demo.axf"),
        )
        result = adapter.write_live_variable(request)
    finally:
        backend_module.write_keil_live_variable_existing = original

    _assert(result.written, result.summary())
    _assert(result.method == "memory", f"method mismatch: {result.method}")
    _assert(result.resolved is not None and result.resolved.address == 0x20000008, "resolved address mismatch")
    _assert(len(calls) == 1, f"expected one write call, got {len(calls)}")
    call_request, root, port, require_debug = calls[0]
    _assert(call_request is request, "adapter should pass request through")
    _assert(root == Path("D:/Keil"), f"root mismatch: {root}")
    _assert(port == 4827, f"port mismatch: {port}")
    _assert(require_debug, "live write should require debug by default")

    print("PASS keil backend live write adapter probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

