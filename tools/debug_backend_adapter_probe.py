"""Probe debugger backend adapter contracts without requiring hardware."""

from __future__ import annotations

import json
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_workbench import DebugRuntimeState  # noqa: E402
from src.core.keil.backend import KeilBackendConfig, KeilUvSockBackendAdapter  # noqa: E402
from src.core.keil.uvsock import UvscConnectionResult  # noqa: E402


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


def _fake_preflight(*, can_attempt: bool = True):
    discovery = SimpleNamespace(
        root=Path("D:/Keil/Keil_v5"),
        uv4_dir=Path("D:/Keil/Keil_v5/UV4"),
        installed=True,
        capability_flags=lambda: {
            "has_uv4": True,
            "has_uvsock_dll": True,
            "can_open_connection": True,
            "can_eval_expression": True,
            "can_read_memory": True,
            "can_write_memory": True,
            "can_control_target": True,
        },
    )
    dll = SimpleNamespace(path=Path("D:/Keil/Keil_v5/UV4/UVSC64.dll"))
    return SimpleNamespace(
        discovery=discovery,
        processes=(SimpleNamespace(pid=100, name="UV4.exe", path="D:/Keil/Keil_v5/UV4/UV4.exe"),) if can_attempt else (),
        load_result=SimpleNamespace(dll=dll, loaded=True, error=""),
        can_attempt_connection=can_attempt,
        reasons=() if can_attempt else ("uVision is not running",),
    )


def _fake_launch_plan():
    return SimpleNamespace(
        command=("D:/Keil/Keil_v5/UV4/UV4.exe", "D:/demo/demo.uvprojx", "-s", "4827"),
        display_command="D:/Keil/Keil_v5/UV4/UV4.exe D:/demo/demo.uvprojx -s 4827",
        ready=True,
        reasons=(),
    )


def main() -> int:
    import src.core.keil.backend as backend_module

    calls = {"preflight": 0, "connect": 0, "launch": 0}

    def fake_check(root=None, require_running=False):
        calls["preflight"] += 1
        return _fake_preflight(can_attempt=not require_running)

    def fake_launch(root=None, port=4827, project=None, target=None):
        calls["launch"] += 1
        return _fake_launch_plan()

    def fake_connect(root=None, port=None, query_status=False, connection_name="LoopMaster"):
        calls["connect"] += 1
        return (
            _fake_preflight(can_attempt=True),
            UvscConnectionResult(
                attempted=True,
                connected=True,
                port=int(port or 4827),
                handle=1234,
                status_code=0,
                status_name="UVSC_STATUS_SUCCESS",
                target_running=True,
            ),
        )

    original_check = backend_module.check_uvsock_preflight
    original_launch = backend_module.build_uvision_uvsock_command
    original_connect = backend_module.attempt_existing_uvsock_connection
    try:
        backend_module.check_uvsock_preflight = fake_check
        backend_module.build_uvision_uvsock_command = fake_launch
        backend_module.attempt_existing_uvsock_connection = fake_connect

        adapter = KeilUvSockBackendAdapter(KeilBackendConfig(root=Path("D:/Keil"), port=4827))
        discover = adapter.discover(project_path="D:/demo/demo.uvprojx", target_name="DebugDemo")
        _assert(calls["connect"] == 0, "discover must not open UVSOCK")
        _assert(discover.backend.value == "keil", "discover backend mismatch")
        _assert(discover.read_only, "discover snapshot should be read-only")
        _assert(not discover.connection_attempted, "discover should not attempt connection")
        _assert(discover.status.state == DebugRuntimeState.KEIL_DISCOVERED, "discover state mismatch")
        _assert(discover.remote_breakpoint_snapshot is not None, "discover should carry a remote breakpoint placeholder")
        _assert(not discover.remote_breakpoint_snapshot.complete, "discover remote breakpoint placeholder must be incomplete")
        _assert(discover.pc_location is not None and not discover.pc_location.complete, "discover PC placeholder must be incomplete")
        json.dumps(discover.to_record(), ensure_ascii=False, sort_keys=True)
        _assert_data_only(discover)

        snapshot = adapter.read_only_session_snapshot(
            project_path="D:/demo/demo.uvprojx",
            target_name="DebugDemo",
            attempt_connection=True,
            query_status=True,
        )
        _assert(calls["connect"] == 1, "read-only snapshot should call the explicit connection path once")
        _assert(snapshot.connection_attempted and snapshot.connection_established, "read-only snapshot connection state mismatch")
        _assert(snapshot.target_running is True, "target running status mismatch")
        _assert(snapshot.status.state == DebugRuntimeState.RUNNING, "snapshot status state mismatch")
        _assert(snapshot.remote_breakpoint_snapshot is not None, "read-only snapshot should carry a breakpoint placeholder")
        _assert(not snapshot.remote_breakpoint_snapshot.complete, "read-only breakpoint placeholder must be incomplete")
        _assert(snapshot.remote_breakpoint_snapshot_id == snapshot.remote_breakpoint_snapshot.snapshot_id, "breakpoint snapshot id mismatch")
        _assert(snapshot.pc_location is not None and not snapshot.pc_location.complete, "read-only PC placeholder must be incomplete")
        caps = snapshot.status.capabilities
        _assert(caps.can_read_variables, "read-only snapshot should allow read capability")
        _assert(not caps.can_write_variables, "read-only snapshot must not allow writes")
        _assert(not caps.can_halt and not caps.can_run and not caps.can_step, "read-only snapshot must not allow run control")
        _assert(not caps.can_sync_breakpoints, "read-only snapshot must not allow breakpoint sync")
        record = snapshot.to_record()
        _assert(record["connection_established"], "record should preserve connection state")
        json.dumps(record, ensure_ascii=False, sort_keys=True)
        _assert_data_only(snapshot)

        preview = adapter.read_only_session_snapshot(
            project_path="D:/demo/demo.uvprojx",
            target_name="DebugDemo",
            attempt_connection=False,
        )
        _assert(calls["connect"] == 1, "preview read-only snapshot should not connect")
        _assert(not preview.connection_attempted, "preview snapshot should remain no-connect")
        _assert(preview.remote_breakpoint_snapshot is not None and not preview.remote_breakpoint_snapshot.complete, "preview should carry incomplete remote breakpoints")
    finally:
        backend_module.check_uvsock_preflight = original_check
        backend_module.build_uvision_uvsock_command = original_launch
        backend_module.attempt_existing_uvsock_connection = original_connect

    print("PASS debug backend adapter probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
