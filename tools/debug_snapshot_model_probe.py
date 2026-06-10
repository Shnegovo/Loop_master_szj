"""Probe backend-neutral debug snapshot data models."""

from __future__ import annotations

import json
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_backend import DebugBackendDiagnostic, DebugBackendSessionSnapshot  # noqa: E402
from src.core.debug_snapshots import (  # noqa: E402
    DebugPcLocation,
    RemoteBreakpoint,
    RemoteBreakpointSnapshot,
    TargetSnapshot,
    from_record,
    to_record,
)
from src.core.debug_workbench import DebugBackendKind, DebugRuntimeState, make_debug_status  # noqa: E402
from src.core.keil.commands import KeilBreakpointRemoteSnapshot, KeilRemoteBreakpoint  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_data_only(value: object, path: str = "snapshot") -> None:
    _assert(not callable(value), f"{path} must not be callable")
    if is_dataclass(value):
        for field in fields(value):
            lower = field.name.lower()
            _assert(lower not in {"handle", "library", "executor", "callback", "thread", "process"}, f"{path}.{field.name} is forbidden")
            _assert_data_only(getattr(value, field.name), f"{path}.{field.name}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            lower = str(key).lower()
            _assert(lower not in {"handle", "library", "executor", "callback", "thread", "process"}, f"{path}.{key} is forbidden")
            _assert_data_only(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _assert_data_only(item, f"{path}[{index}]")


def main() -> int:
    remote = RemoteBreakpoint(
        path=Path("D:/demo/main.c"),
        line=42,
        enabled=True,
        condition="speed > 10",
        remote_id="bp-42",
        raw_location="D:/demo/main.c:42",
        verified=False,
        message="waiting for backend verify",
    )
    snapshot = RemoteBreakpointSnapshot(
        schema_version=1,
        snapshot_id="remote-demo",
        project_path=Path("D:/demo/demo.uvprojx"),
        target_name="DebugDemo",
        captured_at="2026-06-10T00:00:00+00:00",
        complete=True,
        breakpoints=(remote,),
    )
    pc_location = DebugPcLocation(
        path=Path("D:/demo/main.c"),
        line=42,
        address=0x08001234,
        function="main",
        source="probe",
        complete=True,
    )
    target = TargetSnapshot(
        schema_version=1,
        backend="keil",
        adapter_name="Keil / UVSOCK",
        snapshot_id="target-demo",
        captured_at=snapshot.captured_at,
        state="paused",
        label="Paused",
        detail="Probe target snapshot",
        read_only=True,
        connection_attempted=True,
        connection_established=True,
        target_running=False,
        port=4827,
        project_path=snapshot.project_path,
        target_name=snapshot.target_name,
        pc_location=pc_location,
        remote_breakpoint_snapshot=snapshot,
        remote_breakpoint_snapshot_id=snapshot.snapshot_id,
        diagnostics=(("backend", "probe"),),
        capabilities=(("can_read_variables", True), ("can_write_variables", False)),
    )

    record = target.to_record()
    json.dumps(record, ensure_ascii=False, sort_keys=True)
    restored = TargetSnapshot.from_record(record)
    _assert(restored.remote_breakpoint_snapshot is not None, "target snapshot should restore remote breakpoints")
    _assert(restored.remote_breakpoint_snapshot.breakpoints[0].remote_id == "bp-42", "remote breakpoint round-trip failed")
    _assert(restored.pc_location is not None and restored.pc_location.address == 0x08001234, "PC location round-trip failed")
    _assert_data_only(target)
    _assert_data_only(record)

    generic_bp = from_record(RemoteBreakpoint, remote.to_record())
    _assert(generic_bp.path == Path("D:/demo/main.c"), "generic from_record should preserve path")
    _assert(to_record(snapshot)["breakpoints"][0]["line"] == 42, "generic to_record should recurse into breakpoints")

    keil_remote = KeilRemoteBreakpoint(path=Path("D:/demo/main.c"), line=7, enabled=False, remote_id="keil-bp")
    keil_snapshot = KeilBreakpointRemoteSnapshot(
        schema_version=1,
        snapshot_id="keil-remote-demo",
        project_path=Path("D:/demo/demo.uvprojx"),
        target_name="DebugDemo",
        captured_at=snapshot.captured_at,
        complete=True,
        breakpoints=(keil_remote,),
    )
    _assert(isinstance(keil_remote, RemoteBreakpoint), "Keil breakpoint alias must be a generic RemoteBreakpoint")
    _assert(isinstance(keil_snapshot, RemoteBreakpointSnapshot), "Keil snapshot alias must be a generic RemoteBreakpointSnapshot")
    _assert(KeilBreakpointRemoteSnapshot.from_record(keil_snapshot.to_record()).breakpoints[0].remote_id == "keil-bp", "Keil snapshot JSON compatibility failed")

    backend_snapshot = DebugBackendSessionSnapshot(
        schema_version=1,
        backend=DebugBackendKind.KEIL,
        adapter_name="Keil / UVSOCK",
        snapshot_id="backend-demo",
        captured_at=snapshot.captured_at,
        status=make_debug_status(
            state=DebugRuntimeState.PAUSED,
            backend=DebugBackendKind.KEIL,
            detail="Probe backend snapshot",
            project_path=Path("D:/demo/demo.uvprojx"),
            target_name="DebugDemo",
        ),
        diagnostics=(DebugBackendDiagnostic("backend", "probe"),),
        capabilities=(("can_read_variables", True),),
        pc_location=pc_location,
        remote_breakpoint_snapshot=keil_snapshot,
        remote_breakpoint_snapshot_id=keil_snapshot.snapshot_id,
    )
    backend_record = backend_snapshot.to_record()
    json.dumps(backend_record, ensure_ascii=False, sort_keys=True)
    flattened = TargetSnapshot.from_record(backend_record)
    _assert(flattened.backend == "keil", "backend snapshot should flatten to generic target snapshot")
    _assert(flattened.state == "paused", "backend status state should be preserved")
    _assert(flattened.remote_breakpoint_snapshot_id == "keil-remote-demo", "remote snapshot id should be preserved")
    _assert_data_only(backend_snapshot)

    print("PASS debug snapshot model probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
