"""Backend adapter contracts for the debugger workbench."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from src.core.debug_workbench import DebugBackendKind, DebugWorkbenchStatus
    from src.core.keil.commands import KeilBreakpointRemoteSnapshot


@dataclass(frozen=True)
class DebugBackendDiagnostic:
    key: str
    value: str


@dataclass(frozen=True)
class DebugPcLocation:
    path: Path | None = None
    line: int | None = None
    address: int | None = None
    function: str = ""
    source: str = ""
    complete: bool = False
    message: str = ""

    def to_record(self) -> dict[str, object]:
        return {
            "path": str(self.path) if self.path else "",
            "line": self.line,
            "address": self.address,
            "function": self.function,
            "source": self.source,
            "complete": self.complete,
            "message": self.message,
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
    remote_breakpoint_snapshot: "KeilBreakpointRemoteSnapshot | None" = None
    remote_breakpoint_snapshot_id: str = ""

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        return tuple((item.key, item.value) for item in self.diagnostics)

    def capability_enabled(self, key: str) -> bool:
        return dict(self.capabilities).get(str(key), False)

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
            "pc_location": self.pc_location.to_record() if self.pc_location else None,
            "remote_breakpoint_snapshot": (
                self.remote_breakpoint_snapshot.to_record()
                if self.remote_breakpoint_snapshot is not None
                else None
            ),
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
