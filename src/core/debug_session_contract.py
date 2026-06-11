"""Backend-neutral debug session contract.

The objects in this module are data-only. They describe session intent,
capabilities, safety policy and dry-run command availability without carrying
Qt widgets, subprocesses, DLL handles, sockets, probe handles or callables.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable


class DebugSessionBackend(str, Enum):
    NONE = "none"
    KEIL = "keil"
    OPENOCD_GDB = "openocd_gdb"
    PYOCD = "pyocd"
    OFFLINE = "offline"


class DebugSessionState(str, Enum):
    DISCONNECTED = "disconnected"
    DISCOVERED = "discovered"
    ATTACHED = "attached"
    PAUSED = "paused"
    RUNNING = "running"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class DebugTargetState(str, Enum):
    UNKNOWN = "unknown"
    DISCONNECTED = "disconnected"
    PAUSED = "paused"
    RUNNING = "running"
    ERROR = "error"


class DebugCommandSafety(str, Enum):
    INFO = "info"
    READ_ONLY = "read_only"
    RUN_CONTROL = "run_control"
    TARGET_WRITE = "target_write"


@dataclass(frozen=True)
class DebugSessionDiagnostic:
    key: str
    value: str

    def to_record(self) -> dict[str, str]:
        return {"key": self.key, "value": self.value}


@dataclass(frozen=True)
class DebugSessionCapabilities:
    can_discover: bool = False
    can_attach: bool = False
    can_disconnect: bool = False
    can_read_variables: bool = False
    can_write_variables: bool = False
    can_halt: bool = False
    can_run: bool = False
    can_reset: bool = False
    can_step: bool = False
    can_sync_breakpoints: bool = False

    @property
    def read_only(self) -> bool:
        return (
            not self.can_write_variables
            and not self.can_halt
            and not self.can_run
            and not self.can_reset
            and not self.can_step
        )

    def enabled_commands(self) -> tuple[str, ...]:
        commands: list[str] = []
        if self.can_discover:
            commands.append("discover")
        if self.can_attach:
            commands.append("attach")
        if self.can_disconnect:
            commands.append("disconnect")
        if self.can_halt:
            commands.append("halt")
        if self.can_run:
            commands.append("run")
        if self.can_reset:
            commands.append("reset")
        if self.can_step:
            commands.append("step")
            commands.append("step_over")
            commands.append("run_to_cursor")
        if self.can_sync_breakpoints:
            commands.append("sync_breakpoints")
        if self.can_write_variables:
            commands.append("write_variables")
        return tuple(commands)

    def to_record(self) -> dict[str, bool]:
        return {
            "can_discover": self.can_discover,
            "can_attach": self.can_attach,
            "can_disconnect": self.can_disconnect,
            "can_read_variables": self.can_read_variables,
            "can_write_variables": self.can_write_variables,
            "can_halt": self.can_halt,
            "can_run": self.can_run,
            "can_reset": self.can_reset,
            "can_step": self.can_step,
            "can_sync_breakpoints": self.can_sync_breakpoints,
        }


@dataclass(frozen=True)
class DebugSessionSafetyPolicy:
    dry_run: bool = True
    read_only: bool = True
    allow_start_process: bool = False
    allow_connect_probe: bool = False
    allow_write_target: bool = False
    allow_run_control: bool = False
    label: str = "默认干跑策略"
    notes: str = "不启动进程、不连接探针、不写目标、不改变运行状态"

    def permits(self, safety: DebugCommandSafety | str) -> bool:
        safety_value = _coerce_command_safety(safety)
        if safety_value == DebugCommandSafety.INFO:
            return True
        if safety_value == DebugCommandSafety.READ_ONLY:
            return not self.dry_run
        if safety_value == DebugCommandSafety.RUN_CONTROL:
            return not self.dry_run and self.allow_run_control
        if safety_value == DebugCommandSafety.TARGET_WRITE:
            return not self.dry_run and self.allow_write_target
        return False

    def to_record(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "read_only": self.read_only,
            "allow_start_process": self.allow_start_process,
            "allow_connect_probe": self.allow_connect_probe,
            "allow_write_target": self.allow_write_target,
            "allow_run_control": self.allow_run_control,
            "label": self.label,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class DebugSessionSpec:
    backend: DebugSessionBackend
    display_name: str
    project_path: Path | None = None
    target_name: str = ""
    source_provider: str = ""
    safety_policy: DebugSessionSafetyPolicy = field(default_factory=DebugSessionSafetyPolicy)
    metadata: dict[str, str] = field(default_factory=dict)

    def to_record(self) -> dict[str, object]:
        return {
            "backend": self.backend.value,
            "display_name": self.display_name,
            "project_path": str(self.project_path) if self.project_path else "",
            "target_name": self.target_name,
            "source_provider": self.source_provider,
            "safety_policy": self.safety_policy.to_record(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DebugSessionSnapshot:
    schema_version: int
    session_id: str
    captured_at: str
    backend: DebugSessionBackend
    display_name: str
    state: DebugSessionState
    target_state: DebugTargetState = DebugTargetState.UNKNOWN
    label: str = ""
    detail: str = ""
    project_path: Path | None = None
    target_name: str = ""
    connection_attempted: bool = False
    connection_established: bool = False
    capabilities: DebugSessionCapabilities = field(default_factory=DebugSessionCapabilities)
    safety_policy: DebugSessionSafetyPolicy = field(default_factory=DebugSessionSafetyPolicy)
    diagnostics: tuple[DebugSessionDiagnostic, ...] = ()
    backend_snapshot_id: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def read_only(self) -> bool:
        return self.safety_policy.read_only or self.capabilities.read_only

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        return tuple((item.key, item.value) for item in self.diagnostics)

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "captured_at": self.captured_at,
            "backend": self.backend.value,
            "display_name": self.display_name,
            "state": self.state.value,
            "target_state": self.target_state.value,
            "label": self.label,
            "detail": self.detail,
            "project_path": str(self.project_path) if self.project_path else "",
            "target_name": self.target_name,
            "connection_attempted": self.connection_attempted,
            "connection_established": self.connection_established,
            "capabilities": self.capabilities.to_record(),
            "safety_policy": self.safety_policy.to_record(),
            "diagnostics": [item.to_record() for item in self.diagnostics],
            "backend_snapshot_id": self.backend_snapshot_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DebugSessionCommand:
    key: str
    title: str
    safety: DebugCommandSafety
    enabled_by_state: bool
    execution_enabled: bool
    dry_run: bool
    reason: str = ""
    preview_steps: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return bool(self.enabled_by_state and self.execution_enabled and not self.dry_run)

    def to_record(self) -> dict[str, object]:
        return {
            "key": self.key,
            "title": self.title,
            "safety": self.safety.value,
            "enabled_by_state": self.enabled_by_state,
            "execution_enabled": self.execution_enabled,
            "dry_run": self.dry_run,
            "ready": self.ready,
            "reason": self.reason,
            "preview_steps": list(self.preview_steps),
        }


@dataclass(frozen=True)
class DebugSessionEvent:
    event_id: str
    captured_at: str
    backend: DebugSessionBackend
    session_id: str
    kind: str
    detail: str
    dry_run: bool = True

    def to_record(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "captured_at": self.captured_at,
            "backend": self.backend.value,
            "session_id": self.session_id,
            "kind": self.kind,
            "detail": self.detail,
            "dry_run": self.dry_run,
        }


def default_session_capabilities(
    state: DebugSessionState | str,
    *,
    can_attach: bool = False,
    read_variables: bool = False,
    run_control: bool = False,
    breakpoint_sync: bool = False,
    variable_write: bool = False,
) -> DebugSessionCapabilities:
    state_value = _coerce_session_state(state)
    attached = state_value in {
        DebugSessionState.ATTACHED,
        DebugSessionState.PAUSED,
        DebugSessionState.RUNNING,
    }
    return DebugSessionCapabilities(
        can_discover=state_value in {
            DebugSessionState.DISCONNECTED,
            DebugSessionState.DISCOVERED,
            DebugSessionState.ERROR,
            DebugSessionState.UNAVAILABLE,
        },
        can_attach=bool(can_attach and state_value == DebugSessionState.DISCOVERED),
        can_disconnect=attached,
        can_read_variables=attached and read_variables,
        can_write_variables=attached and variable_write,
        can_halt=attached and run_control and state_value != DebugSessionState.PAUSED,
        can_run=attached and run_control and state_value != DebugSessionState.RUNNING,
        can_reset=attached and run_control,
        can_step=attached and run_control and state_value == DebugSessionState.PAUSED,
        can_sync_breakpoints=attached and breakpoint_sync,
    )


def make_debug_session_snapshot(
    *,
    backend: DebugSessionBackend | str,
    display_name: str = "",
    state: DebugSessionState | str,
    target_state: DebugTargetState | str = DebugTargetState.UNKNOWN,
    project_path: str | Path | None = None,
    target_name: str = "",
    detail: str = "",
    connection_attempted: bool = False,
    connection_established: bool = False,
    capabilities: DebugSessionCapabilities | None = None,
    safety_policy: DebugSessionSafetyPolicy | None = None,
    diagnostics: Iterable[DebugSessionDiagnostic | tuple[str, str]] = (),
    backend_snapshot_id: str = "",
    metadata: dict[str, str] | None = None,
) -> DebugSessionSnapshot:
    backend_value = _coerce_session_backend(backend)
    state_value = _coerce_session_state(state)
    target_state_value = _coerce_target_state(target_state)
    policy = safety_policy or DebugSessionSafetyPolicy()
    rows = tuple(
        item if isinstance(item, DebugSessionDiagnostic) else DebugSessionDiagnostic(str(item[0]), str(item[1]))
        for item in diagnostics
    )
    project = Path(project_path).expanduser().resolve() if project_path else None
    payload = {
        "backend": backend_value.value,
        "display_name": display_name or backend_value.value,
        "state": state_value.value,
        "target_state": target_state_value.value,
        "project_path": str(project or ""),
        "target_name": str(target_name or ""),
        "backend_snapshot_id": str(backend_snapshot_id or ""),
        "metadata": dict(metadata or {}),
    }
    return DebugSessionSnapshot(
        schema_version=1,
        session_id=debug_session_id(payload),
        captured_at=now_iso(),
        backend=backend_value,
        display_name=display_name or backend_value.value,
        state=state_value,
        target_state=target_state_value,
        label=debug_session_state_label(state_value),
        detail=detail or debug_session_state_detail(state_value),
        project_path=project,
        target_name=str(target_name or ""),
        connection_attempted=bool(connection_attempted),
        connection_established=bool(connection_established),
        capabilities=capabilities or default_session_capabilities(state_value),
        safety_policy=policy,
        diagnostics=rows,
        backend_snapshot_id=str(backend_snapshot_id or ""),
        metadata=dict(metadata or {}),
    )


def command_matrix_for_session(snapshot: DebugSessionSnapshot) -> tuple[DebugSessionCommand, ...]:
    caps = snapshot.capabilities
    policy = snapshot.safety_policy
    return (
        _session_command(snapshot, "discover", "发现后端", DebugCommandSafety.INFO, caps.can_discover, ("读取配置", "生成诊断")),
        _session_command(snapshot, "attach", "连接会话", DebugCommandSafety.READ_ONLY, caps.can_attach, ("请求只读快照", "同步状态")),
        _session_command(snapshot, "disconnect", "断开会话", DebugCommandSafety.READ_ONLY, caps.can_disconnect, ("停止轮询", "释放会话")),
        _session_command(snapshot, "halt", "暂停目标", DebugCommandSafety.RUN_CONTROL, caps.can_halt, ("发送 Halt", "读取 PC")),
        _session_command(snapshot, "run", "继续运行", DebugCommandSafety.RUN_CONTROL, caps.can_run, ("发送 Run", "轮询状态")),
        _session_command(snapshot, "reset", "复位目标", DebugCommandSafety.RUN_CONTROL, caps.can_reset, ("发送 Reset", "读取 PC")),
        _session_command(snapshot, "step", "单步", DebugCommandSafety.RUN_CONTROL, caps.can_step, ("发送 Step", "读取 PC")),
        _session_command(snapshot, "step_over", "跨过", DebugCommandSafety.RUN_CONTROL, caps.can_step, ("发送 Step Over", "读取 PC")),
        _session_command(snapshot, "run_to_cursor", "运行到光标", DebugCommandSafety.RUN_CONTROL, caps.can_step, ("解析光标行", "临时断点运行", "回读 PC")),
        _session_command(snapshot, "sync_breakpoints", "同步断点", DebugCommandSafety.RUN_CONTROL, caps.can_sync_breakpoints, ("生成断点差异", "回读验证")),
        _session_command(snapshot, "write_variables", "写变量", DebugCommandSafety.TARGET_WRITE, caps.can_write_variables, ("校验类型/RAM", "写入后回读")),
    )


def event_from_session(
    snapshot: DebugSessionSnapshot,
    *,
    kind: str,
    detail: str,
) -> DebugSessionEvent:
    payload = {
        "backend": snapshot.backend.value,
        "session_id": snapshot.session_id,
        "kind": str(kind),
        "detail": str(detail),
        "captured_at": snapshot.captured_at,
    }
    return DebugSessionEvent(
        event_id=debug_session_id(payload).replace("debug-session-", "debug-event-"),
        captured_at=now_iso(),
        backend=snapshot.backend,
        session_id=snapshot.session_id,
        kind=str(kind),
        detail=str(detail),
        dry_run=snapshot.safety_policy.dry_run,
    )


def debug_session_state_label(state: DebugSessionState | str) -> str:
    labels = {
        DebugSessionState.DISCONNECTED: "未连接",
        DebugSessionState.DISCOVERED: "已发现",
        DebugSessionState.ATTACHED: "已连接",
        DebugSessionState.PAUSED: "目标已暂停",
        DebugSessionState.RUNNING: "目标运行中",
        DebugSessionState.ERROR: "调试异常",
        DebugSessionState.UNAVAILABLE: "后端不可用",
    }
    return labels[_coerce_session_state(state)]


def debug_session_state_detail(state: DebugSessionState | str) -> str:
    details = {
        DebugSessionState.DISCONNECTED: "尚未连接调试后端",
        DebugSessionState.DISCOVERED: "已发现后端，等待显式连接",
        DebugSessionState.ATTACHED: "已连接会话，等待目标状态",
        DebugSessionState.PAUSED: "目标暂停，可查看上下文",
        DebugSessionState.RUNNING: "目标正在运行",
        DebugSessionState.ERROR: "调试会话出现错误",
        DebugSessionState.UNAVAILABLE: "后端尚未接入或当前不可用",
    }
    return details[_coerce_session_state(state)]


def debug_session_id(payload: dict[str, object]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"debug-session-{digest[:16]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session_command(
    snapshot: DebugSessionSnapshot,
    key: str,
    title: str,
    safety: DebugCommandSafety,
    enabled_by_state: bool,
    preview_steps: tuple[str, ...],
) -> DebugSessionCommand:
    policy = snapshot.safety_policy
    execution_enabled = bool(enabled_by_state and policy.permits(safety))
    reason = ""
    if not enabled_by_state:
        reason = snapshot.detail or "当前会话状态不允许该动作"
    elif not execution_enabled:
        reason = policy.notes
    return DebugSessionCommand(
        key=key,
        title=title,
        safety=safety,
        enabled_by_state=bool(enabled_by_state),
        execution_enabled=execution_enabled,
        dry_run=policy.dry_run,
        reason=reason,
        preview_steps=preview_steps,
    )


def _coerce_session_backend(value: DebugSessionBackend | str) -> DebugSessionBackend:
    if isinstance(value, DebugSessionBackend):
        return value
    return DebugSessionBackend(str(value))


def _coerce_session_state(value: DebugSessionState | str) -> DebugSessionState:
    if isinstance(value, DebugSessionState):
        return value
    return DebugSessionState(str(value))


def _coerce_target_state(value: DebugTargetState | str) -> DebugTargetState:
    if isinstance(value, DebugTargetState):
        return value
    return DebugTargetState(str(value))


def _coerce_command_safety(value: DebugCommandSafety | str) -> DebugCommandSafety:
    if isinstance(value, DebugCommandSafety):
        return value
    return DebugCommandSafety(str(value))
