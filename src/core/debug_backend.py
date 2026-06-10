"""Backend adapter contracts for the debugger workbench."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from src.core.debug_snapshots import DebugPcLocation, RemoteBreakpointSnapshot, to_record
from src.core.debug_session_contract import (
    DebugSessionBackend,
    DebugSessionCapabilities,
    DebugSessionDiagnostic,
    DebugSessionSafetyPolicy,
    DebugSessionSnapshot,
    DebugSessionState,
    DebugTargetState,
    make_debug_session_snapshot,
)

if TYPE_CHECKING:
    from src.core.debug_workbench import DebugBackendKind, DebugWorkbenchStatus


@dataclass(frozen=True)
class DebugBackendDiagnostic:
    key: str
    value: str


class DebugBackendWorkerState(str, Enum):
    REGISTERED = "registered"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True)
class DebugBackendWorkerLifecycleRegistration:
    worker_key: str
    state: DebugBackendWorkerState = DebugBackendWorkerState.REGISTERED
    autostart: bool = False
    read_only_first: bool = True
    may_start_process: bool = False
    may_connect_probe: bool = False
    may_write_target: bool = False
    notes: str = ""

    def to_record(self) -> dict[str, object]:
        return {
            "worker_key": self.worker_key,
            "state": self.state.value,
            "autostart": self.autostart,
            "read_only_first": self.read_only_first,
            "may_start_process": self.may_start_process,
            "may_connect_probe": self.may_connect_probe,
            "may_write_target": self.may_write_target,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class DebugBackendSessionSnapshot:
    schema_version: int
    backend: "DebugBackendKind"
    adapter_name: str
    snapshot_id: str
    captured_at: str
    status: "DebugWorkbenchStatus"
    diagnostics: tuple[DebugBackendDiagnostic, ...]
    capabilities: tuple[tuple[str, bool], ...]
    read_only: bool = True
    connection_attempted: bool = False
    connection_established: bool = False
    target_running: bool | None = None
    port: int | None = None
    project_path: Path | None = None
    target_name: str = ""
    pc_location: DebugPcLocation | None = None
    remote_breakpoint_snapshot: RemoteBreakpointSnapshot | None = None
    remote_breakpoint_snapshot_id: str = ""

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        return tuple((item.key, item.value) for item in self.diagnostics)

    def capability_enabled(self, key: str) -> bool:
        return dict(self.capabilities).get(str(key), False)

    def to_session_contract(self) -> DebugSessionSnapshot:
        return make_debug_session_snapshot(
            backend=_session_backend_from_kind(self.backend.value),
            display_name=self.adapter_name,
            state=_session_state_from_workbench_state(self.status.state.value),
            target_state=_target_state_from_snapshot(self),
            project_path=self.project_path,
            target_name=self.target_name,
            detail=self.status.detail,
            connection_attempted=self.connection_attempted,
            connection_established=self.connection_established,
            capabilities=DebugSessionCapabilities(
                can_discover=self.status.capabilities.can_discover,
                can_attach=self.status.capabilities.can_attach,
                can_disconnect=self.status.capabilities.can_disconnect,
                can_read_variables=self.status.capabilities.can_read_variables,
                can_write_variables=self.status.capabilities.can_write_variables,
                can_halt=self.status.capabilities.can_halt,
                can_run=self.status.capabilities.can_run,
                can_step=self.status.capabilities.can_step,
                can_sync_breakpoints=self.status.capabilities.can_sync_breakpoints,
            ),
            safety_policy=DebugSessionSafetyPolicy(
                dry_run=True,
                read_only=self.read_only,
                label="后端快照默认安全策略",
                notes="从后端快照导出的合同默认不启动进程、不连接探针、不写目标、不改变运行状态",
            ),
            diagnostics=tuple(
                DebugSessionDiagnostic(item.key, item.value)
                for item in self.diagnostics
            ),
            backend_snapshot_id=self.snapshot_id,
            metadata={
                "source": "DebugBackendSessionSnapshot",
                "status_state": self.status.state.value,
                "remote_breakpoint_snapshot_id": self.remote_breakpoint_snapshot_id,
            },
        )

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "backend": self.backend.value,
            "adapter_name": self.adapter_name,
            "snapshot_id": self.snapshot_id,
            "captured_at": self.captured_at,
            "read_only": self.read_only,
            "connection_attempted": self.connection_attempted,
            "connection_established": self.connection_established,
            "target_running": self.target_running,
            "port": self.port,
            "project_path": str(self.project_path) if self.project_path else "",
            "target_name": self.target_name,
            "pc_location": to_record(self.pc_location) if self.pc_location else None,
            "remote_breakpoint_snapshot": to_record(self.remote_breakpoint_snapshot) if self.remote_breakpoint_snapshot is not None else None,
            "remote_breakpoint_snapshot_id": self.remote_breakpoint_snapshot_id,
            "status": {
                "backend": self.status.backend.value,
                "state": self.status.state.value,
                "label": self.status.label,
                "detail": self.status.detail,
                "project_path": str(self.status.project_path) if self.status.project_path else "",
                "target_name": self.status.target_name,
                "current_pc_line": self.status.current_pc_line,
                "run_line": self.status.run_line,
                "error": self.status.error,
                "capabilities": {
                    "can_discover": self.status.capabilities.can_discover,
                    "can_attach": self.status.capabilities.can_attach,
                    "can_disconnect": self.status.capabilities.can_disconnect,
                    "can_read_variables": self.status.capabilities.can_read_variables,
                    "can_write_variables": self.status.capabilities.can_write_variables,
                    "can_halt": self.status.capabilities.can_halt,
                    "can_run": self.status.capabilities.can_run,
                    "can_step": self.status.capabilities.can_step,
                    "can_sync_breakpoints": self.status.capabilities.can_sync_breakpoints,
                },
            },
            "diagnostics": [
                {"key": item.key, "value": item.value}
                for item in self.diagnostics
            ],
            "capabilities": dict(self.capabilities),
        }


class DebugBackendAdapter(Protocol):
    kind: "DebugBackendKind"
    display_name: str

    def discover(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
        previous_status: "DebugWorkbenchStatus | None" = None,
    ) -> DebugBackendSessionSnapshot:
        ...

    def read_only_session_snapshot(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
        previous_status: "DebugWorkbenchStatus | None" = None,
        attempt_connection: bool = True,
        query_status: bool = True,
    ) -> DebugBackendSessionSnapshot:
        ...


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def backend_snapshot_id(payload: dict[str, object]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"debug-backend-{digest[:16]}"


def _session_backend_from_kind(value: str) -> DebugSessionBackend:
    try:
        return DebugSessionBackend(str(value))
    except ValueError:
        return DebugSessionBackend.NONE


def _session_state_from_workbench_state(value: str) -> DebugSessionState:
    mapping = {
        "disconnected": DebugSessionState.DISCONNECTED,
        "keil_discovered": DebugSessionState.DISCOVERED,
        "keil_attached": DebugSessionState.ATTACHED,
        "paused": DebugSessionState.PAUSED,
        "running": DebugSessionState.RUNNING,
        "error": DebugSessionState.ERROR,
    }
    return mapping.get(str(value), DebugSessionState.ERROR)


def _target_state_from_snapshot(snapshot: DebugBackendSessionSnapshot) -> DebugTargetState:
    if snapshot.status.state.value == "paused":
        return DebugTargetState.PAUSED
    if snapshot.status.state.value == "running":
        return DebugTargetState.RUNNING
    if snapshot.status.state.value == "error":
        return DebugTargetState.ERROR
    if snapshot.connection_established:
        return DebugTargetState.UNKNOWN
    return DebugTargetState.DISCONNECTED
