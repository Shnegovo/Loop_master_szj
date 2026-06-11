"""Probe backend-neutral debug session contract models."""

from __future__ import annotations

import json
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_session_contract import (  # noqa: E402
    DebugCommandSafety,
    DebugSessionBackend,
    DebugSessionCapabilities,
    DebugSessionSafetyPolicy,
    DebugSessionState,
    DebugSessionSpec,
    DebugTargetState,
    command_matrix_for_session,
    default_session_capabilities,
    event_from_session,
    make_debug_session_snapshot,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_data_only(value: object, path: str = "contract") -> None:
    _assert(not callable(value), f"{path} must not be callable")
    forbidden = {
        "callback",
        "cdll",
        "dll",
        "dll_handle",
        "executor",
        "handle",
        "library",
        "pid",
        "popen",
        "process",
        "process_handle",
        "socket",
        "subprocess",
        "thread",
        "thread_handle",
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


def _command_map(snapshot) -> dict[str, object]:
    return {command.key: command for command in command_matrix_for_session(snapshot)}


def main() -> int:
    default_policy = DebugSessionSafetyPolicy()
    _assert(default_policy.dry_run and default_policy.read_only, "default policy should be dry-run/read-only")
    _assert(default_policy.permits(DebugCommandSafety.INFO), "info actions should be permitted for diagnostics")
    for safety in (DebugCommandSafety.READ_ONLY, DebugCommandSafety.RUN_CONTROL, DebugCommandSafety.TARGET_WRITE):
        _assert(not default_policy.permits(safety), f"default policy must block {safety.value}")

    spec = DebugSessionSpec(
        backend=DebugSessionBackend.KEIL,
        display_name="Keil / UVSOCK",
        project_path=Path("D:/demo/demo.uvprojx"),
        target_name="DebugDemo",
        source_provider="keil",
    )
    spec_record = spec.to_record()
    _assert(spec_record["backend"] == "keil", "spec backend mismatch")
    json.dumps(spec_record, ensure_ascii=False, sort_keys=True)
    _assert_data_only(spec)

    disconnected = make_debug_session_snapshot(
        backend="keil",
        display_name="Keil / UVSOCK",
        state=DebugSessionState.DISCONNECTED,
        project_path=spec.project_path,
        target_name=spec.target_name,
        diagnostics=(("后端", "Keil / UVSOCK"),),
    )
    commands = _command_map(disconnected)
    _assert(commands["discover"].enabled_by_state, "discover should be enabled while disconnected")
    _assert(not commands["attach"].enabled_by_state, "attach should be disabled while disconnected")
    _assert(commands["discover"].execution_enabled, "diagnostic discover should be executable by default")
    _assert(not commands["attach"].execution_enabled, "attach must not execute under default policy")
    json.dumps(disconnected.to_record(), ensure_ascii=False, sort_keys=True)
    _assert_data_only(disconnected)

    discovered = make_debug_session_snapshot(
        backend=DebugSessionBackend.OPENOCD_GDB,
        display_name="OpenOCD / GDB",
        state=DebugSessionState.DISCOVERED,
        project_path="D:/demo/build/demo.elf",
        target_name="stm32f401ccu6",
        capabilities=default_session_capabilities(DebugSessionState.DISCOVERED, can_attach=True),
        diagnostics=(("状态", "占位后端已发现"),),
    )
    commands = _command_map(discovered)
    _assert(commands["discover"].enabled_by_state, "rediscover should remain enabled")
    _assert(commands["attach"].enabled_by_state, "attach should be state-enabled after discovery")
    _assert(not commands["attach"].execution_enabled, "attach should remain blocked by default policy")
    _assert("不启动进程" in commands["attach"].reason, "attach should explain safety policy")
    _assert(discovered.target_state == DebugTargetState.UNKNOWN, "target state default mismatch")
    _assert_data_only(discovered.to_record())

    paused_caps = default_session_capabilities(
        DebugSessionState.PAUSED,
        read_variables=True,
        run_control=True,
        breakpoint_sync=True,
        variable_write=True,
    )
    paused = make_debug_session_snapshot(
        backend=DebugSessionBackend.PYOCD,
        display_name="pyOCD",
        state=DebugSessionState.PAUSED,
        target_state=DebugTargetState.PAUSED,
        connection_attempted=True,
        connection_established=True,
        capabilities=paused_caps,
        metadata={"fixture": "paused"},
    )
    commands = _command_map(paused)
    _assert(commands["disconnect"].enabled_by_state, "disconnect should be enabled while attached")
    _assert(commands["run"].enabled_by_state, "run should be enabled while paused")
    _assert(commands["step"].enabled_by_state, "step should be enabled while paused")
    _assert(commands["step_over"].enabled_by_state, "step_over should be enabled while paused")
    _assert(commands["sync_breakpoints"].enabled_by_state, "breakpoint sync should reflect capability")
    _assert(commands["write_variables"].enabled_by_state, "write variables should reflect declared capability")
    for key in ("run", "step", "step_over", "sync_breakpoints", "write_variables"):
        _assert(not commands[key].execution_enabled, f"{key} must stay execution-disabled under default policy")
        _assert(not commands[key].ready, f"{key} must not be ready under default policy")
    event = event_from_session(paused, kind="preview", detail="dry-run matrix generated")
    _assert(event.backend == DebugSessionBackend.PYOCD, "event backend mismatch")
    json.dumps(event.to_record(), ensure_ascii=False, sort_keys=True)
    _assert_data_only(event)

    live_read_policy = DebugSessionSafetyPolicy(
        dry_run=False,
        read_only=True,
        allow_start_process=False,
        allow_connect_probe=False,
        allow_write_target=False,
        allow_run_control=False,
        label="只读烟测",
        notes="只允许读取会话快照",
    )
    live_read = make_debug_session_snapshot(
        backend=DebugSessionBackend.OFFLINE,
        display_name="离线回放",
        state=DebugSessionState.DISCOVERED,
        capabilities=default_session_capabilities(DebugSessionState.DISCOVERED, can_attach=True),
        safety_policy=live_read_policy,
    )
    commands = _command_map(live_read)
    _assert(commands["attach"].execution_enabled, "read-only attach should execute only when policy allows")
    _assert(commands["attach"].ready, "read-only attach should be ready when dry_run is false")
    _assert(not commands["run"].execution_enabled, "run should remain disabled without run-control policy")

    unavailable = make_debug_session_snapshot(
        backend=DebugSessionBackend.OPENOCD_GDB,
        display_name="OpenOCD / GDB",
        state=DebugSessionState.UNAVAILABLE,
        detail="OpenOCD 后端尚未接入执行器",
        diagnostics=(("状态", "尚未接入"),),
    )
    commands = _command_map(unavailable)
    _assert(commands["discover"].enabled_by_state, "unavailable backend should still allow rediscover")
    _assert("尚未接入" in unavailable.diagnostic_rows()[0][1], "unavailable diagnostics mismatch")

    record = paused.to_record()
    _assert(record["capabilities"]["can_write_variables"], "capability record mismatch")
    _assert(record["safety_policy"]["dry_run"], "policy record mismatch")
    _assert(record["metadata"]["fixture"] == "paused", "metadata record mismatch")
    _assert_data_only(record)

    print("PASS debug session contract probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
