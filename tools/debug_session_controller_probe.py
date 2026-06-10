"""Probe dry-run debug session controller without live debugger access."""

from __future__ import annotations

import json
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_backend import (  # noqa: E402
    DebugBackendDiagnostic,
    DebugBackendSessionSnapshot,
    backend_snapshot_id,
    now_iso,
)
from src.core.debug_backend_registry import (  # noqa: E402
    DebugBackendDescriptor,
    DebugBackendRegistry,
    create_default_debug_backend_registry,
)
from src.core.debug_session_contract import (  # noqa: E402
    DebugSessionSafetyPolicy,
    DebugSessionState,
)
from src.core.debug_session_controller import DebugSessionController  # noqa: E402
from src.core.debug_workbench import (  # noqa: E402
    DebugBackendKind,
    DebugRuntimeState,
    default_debug_capabilities,
    make_debug_status,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_data_only(value: object, path: str = "controller") -> None:
    _assert(not callable(value), f"{path} must not be callable")
    forbidden = {
        "callback",
        "cdll",
        "dll_handle",
        "executor",
        "handle",
        "library",
        "pid",
        "popen",
        "process",
        "socket",
        "subprocess",
        "thread",
        "transport_handle",
        "usb_handle",
    }
    if is_dataclass(value):
        for field in fields(value):
            lower = field.name.lower()
            _assert(lower not in forbidden, f"{path}.{field.name} is forbidden")
            _assert_data_only(getattr(value, field.name), f"{path}.{field.name}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            lower = str(key).lower()
            _assert(lower not in forbidden, f"{path}.{key} is forbidden")
            _assert_data_only(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _assert_data_only(item, f"{path}[{index}]")


class _FakeBackend:
    kind = DebugBackendKind.OFFLINE
    display_name = "Offline Fake"

    def __init__(self) -> None:
        self.discover_calls = 0
        self.snapshot_calls = 0

    def discover(self, *, project_path=None, target_name="", previous_status=None):
        self.discover_calls += 1
        return _fake_snapshot(
            state=DebugRuntimeState.KEIL_DISCOVERED,
            project_path=project_path,
            target_name=target_name,
            attempted=False,
            connected=False,
        )

    def read_only_session_snapshot(
        self,
        *,
        project_path=None,
        target_name="",
        previous_status=None,
        attempt_connection=True,
        query_status=True,
    ):
        self.snapshot_calls += 1
        return _fake_snapshot(
            state=DebugRuntimeState.PAUSED,
            project_path=project_path,
            target_name=target_name,
            attempted=bool(attempt_connection),
            connected=bool(attempt_connection),
        )


def _fake_snapshot(
    *,
    state: DebugRuntimeState,
    project_path,
    target_name: str,
    attempted: bool,
    connected: bool,
) -> DebugBackendSessionSnapshot:
    project = Path(project_path).expanduser().resolve() if project_path else None
    payload = {
        "backend": "offline",
        "state": state.value,
        "project": str(project or ""),
        "target": target_name,
        "attempted": attempted,
        "connected": connected,
    }
    return DebugBackendSessionSnapshot(
        schema_version=1,
        backend=DebugBackendKind.OFFLINE,
        adapter_name="Offline Fake",
        snapshot_id=backend_snapshot_id(payload),
        captured_at=now_iso(),
        status=make_debug_status(
            state=state,
            backend=DebugBackendKind.OFFLINE,
            detail="fake no-hardware snapshot",
            project_path=project,
            target_name=target_name,
            capabilities=default_debug_capabilities(
                state,
                can_attach=state == DebugRuntimeState.KEIL_DISCOVERED,
                runtime_control=True,
                breakpoint_sync=True,
                variable_write=True,
            ),
        ),
        diagnostics=(
            DebugBackendDiagnostic("后端", "Offline Fake"),
            DebugBackendDiagnostic("模式", "无硬件假后端"),
        ),
        capabilities=(),
        read_only=True,
        connection_attempted=attempted,
        connection_established=connected,
        target_running=False if connected else None,
        project_path=project,
        target_name=target_name,
    )


def _command(state, key: str):
    command = state.command(key)
    _assert(command is not None, f"missing command {key}")
    return command


def main() -> int:
    placeholders = create_default_debug_backend_registry(include_placeholders=True)
    controller = DebugSessionController(placeholders)
    _assert(controller.backend_kind == DebugBackendKind.KEIL, "default backend should follow registry")
    _assert(controller.snapshot.state == DebugSessionState.DISCONNECTED, "initial controller state mismatch")
    _assert(_command(controller.state, "discover").execution_enabled, "diagnostic discover should be allowed")
    _assert(not _command(controller.state, "attach").execution_enabled, "attach must be blocked initially")
    json.dumps(controller.state.to_record(), ensure_ascii=False, sort_keys=True)
    _assert_data_only(controller.state.to_record())

    state = controller.set_backend(DebugBackendKind.OPENOCD_GDB)
    _assert(state.spec.backend.value == "openocd_gdb", "backend switch spec mismatch")
    _assert(state.snapshot.state == DebugSessionState.DISCONNECTED, "backend switch should reset snapshot")
    state = controller.preview_read_only_snapshot(
        project_path="D:/demo/build/demo.elf",
        target_name="stm32f401ccu6",
        attempt_connection=True,
    )
    _assert(state.snapshot.backend.value == "openocd_gdb", "placeholder snapshot backend mismatch")
    _assert(state.snapshot.backend_snapshot_id, "placeholder snapshot id should flow into contract")
    _assert(state.snapshot.connection_attempted, "attempt flag should flow through controller")
    _assert(not state.snapshot.connection_established, "placeholder must not connect")
    _assert(not any(command.execution_enabled for command in state.commands if command.key != "discover"), "placeholder must not enable live commands")

    fake_backend = _FakeBackend()
    fake_registry = DebugBackendRegistry()
    factory_calls = 0

    def fake_factory() -> _FakeBackend:
        nonlocal factory_calls
        factory_calls += 1
        return fake_backend

    fake_registry.register(
        DebugBackendDescriptor(
            kind=DebugBackendKind.OFFLINE,
            display_name="Offline Fake",
            factory=fake_factory,
            notes="controller probe fake",
        )
    )
    fake = DebugSessionController(fake_registry)
    _assert(factory_calls == 0, "controller construction must not create backend")
    state = fake.discover(project_path="D:/demo/demo.uvprojx", target_name="DebugDemo")
    _assert(factory_calls == 1 and fake_backend.discover_calls == 1, "discover should create and call fake backend once")
    _assert(state.snapshot.state == DebugSessionState.DISCOVERED, "fake discover contract state mismatch")
    _assert(_command(state, "attach").enabled_by_state, "attach should be state-enabled after fake discovery")
    _assert(not _command(state, "attach").execution_enabled, "attach should stay blocked by default dry-run policy")

    live_read_policy = DebugSessionSafetyPolicy(
        dry_run=False,
        read_only=True,
        label="只读烟测",
        notes="只允许读取会话快照",
    )
    state = fake.set_safety_policy(live_read_policy)
    _assert(_command(state, "attach").execution_enabled, "read-only attach should enable when policy allows")
    _assert(_command(state, "attach").ready, "read-only attach should be ready when not dry-run")
    _assert(not _command(state, "run").execution_enabled, "run should stay disabled before paused snapshot")

    state = fake.preview_read_only_snapshot(
        project_path="D:/demo/demo.uvprojx",
        target_name="DebugDemo",
        attempt_connection=True,
        query_status=True,
    )
    _assert(factory_calls == 2 and fake_backend.snapshot_calls == 1, "read-only preview should call fake backend once")
    _assert(state.snapshot.state == DebugSessionState.PAUSED, "fake paused snapshot state mismatch")
    _assert(state.snapshot.connection_established, "fake paused snapshot should preserve connection")
    _assert(_command(state, "disconnect").execution_enabled, "disconnect should be read-only permitted under live read policy")
    _assert(not _command(state, "run").execution_enabled, "run should require run-control policy")
    _assert(not _command(state, "write_variables").execution_enabled, "write should require target-write policy")
    event = fake.record_event("preview", "controller matrix generated")
    _assert(event["backend"] == "offline", "controller event backend mismatch")
    json.dumps(event, ensure_ascii=False, sort_keys=True)

    run_policy = DebugSessionSafetyPolicy(
        dry_run=False,
        read_only=False,
        allow_run_control=True,
        label="运行控制烟测",
        notes="允许运行控制但仍禁止写目标",
    )
    state = fake.set_safety_policy(run_policy)
    _assert(_command(state, "run").execution_enabled, "run should enable when run-control policy allows")
    _assert(_command(state, "step").execution_enabled, "step should enable when run-control policy allows")
    _assert(_command(state, "sync_breakpoints").execution_enabled, "breakpoint sync should follow run-control policy")
    _assert(not _command(state, "write_variables").execution_enabled, "write should remain blocked without write policy")
    _assert_data_only(state.to_record())

    state = fake.update_spec(
        project_path="D:/demo/other.uvprojx",
        target_name="OtherTarget",
        source_provider="compile_commands",
        metadata={"fixture": "updated"},
    )
    _assert(state.spec.source_provider == "compile_commands", "source provider should update")
    _assert(state.spec.metadata["fixture"] == "updated", "metadata should update")
    _assert(state.snapshot.state == DebugSessionState.DISCONNECTED, "spec update should reset to no-live state")

    print("PASS debug session controller probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
