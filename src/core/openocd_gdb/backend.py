"""OpenOCD/GDB backend adapter for the Debug Workbench."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.core.debug_backend import (
    DebugBackendDiagnostic,
    DebugBackendSessionSnapshot,
    backend_snapshot_id,
    now_iso,
)
from src.core.debug_snapshots import DebugPcLocation
from src.core.debug_workbench import (
    DebugBackendKind,
    DebugCapabilities,
    DebugRuntimeState,
    make_debug_status,
)
from src.core.debug_toolchains import debug_toolchain_command_plan, debug_toolchain_descriptor
from src.core.openocd_gdb.readonly import (
    DEFAULT_AXF,
    OpenOcdGdbReadOnlyRequest,
    OpenOcdGdbReadOnlyResult,
    run_openocd_gdb_readonly_probe,
)


@dataclass(frozen=True)
class OpenOcdGdbBackendConfig:
    openocd_root: Path | None = Path("D:/openocd")
    gdb_path: Path | None = None
    axf_path: Path | None = DEFAULT_AXF
    gdb_port: int = 3333
    telnet_port: int = 4444
    tcl_port: int = 6666
    execute_enabled: bool = False
    resume_after_halt: bool = True


class OpenOcdGdbBackendAdapter:
    kind = DebugBackendKind.OPENOCD_GDB
    display_name = "OpenOCD / GDB"

    def __init__(self, config: OpenOcdGdbBackendConfig | None = None) -> None:
        self.config = config or OpenOcdGdbBackendConfig()

    def discover(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
        previous_status: object | None = None,
    ) -> DebugBackendSessionSnapshot:
        result = run_openocd_gdb_readonly_probe(self._request(execute=False, project_path=project_path))
        detail = (
            "OpenOCD/GDB 本机档案已发现，可执行只读连接。"
            if result.succeeded
            else "OpenOCD/GDB 本机档案不完整。"
        )
        status = make_debug_status(
            state=DebugRuntimeState.DISCONNECTED,
            backend=self.kind,
            detail=detail,
            project_path=project_path,
            target_name=target_name,
            capabilities=DebugCapabilities(can_discover=True, can_attach=result.succeeded),
        )
        return self._snapshot(
            result,
            status=status,
            project_path=project_path,
            target_name=target_name,
            connection_attempted=False,
            connection_established=False,
        )

    def read_only_session_snapshot(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
        previous_status: object | None = None,
        attempt_connection: bool = True,
        query_status: bool = True,
    ) -> DebugBackendSessionSnapshot:
        execute = bool(attempt_connection and self.config.execute_enabled)
        result = run_openocd_gdb_readonly_probe(self._request(execute=execute, project_path=project_path))
        if not execute:
            detail = "OpenOCD/GDB 应用执行门未开启，尚未接入 live session。"
            state = DebugRuntimeState.DISCONNECTED
        elif result.succeeded:
            detail = result.detail
            state = DebugRuntimeState.RUNNING if result.target_state == "running" else DebugRuntimeState.PAUSED
        else:
            detail = result.detail
            state = DebugRuntimeState.ERROR
        status = make_debug_status(
            state=state,
            backend=self.kind,
            detail=detail,
            project_path=project_path,
            target_name=target_name,
            capabilities=DebugCapabilities(can_discover=True, can_attach=True),
            error="" if result.succeeded or not execute else result.detail,
        )
        return self._snapshot(
            result,
            status=status,
            project_path=project_path,
            target_name=target_name,
            connection_attempted=bool(attempt_connection),
            connection_established=bool(execute and result.succeeded),
        )

    def _request(self, *, execute: bool, project_path: str | Path | None) -> OpenOcdGdbReadOnlyRequest:
        return OpenOcdGdbReadOnlyRequest(
            openocd_root=self.config.openocd_root,
            gdb_path=self.config.gdb_path,
            axf_path=_axf_from_project(project_path) or self.config.axf_path,
            gdb_port=self.config.gdb_port,
            telnet_port=self.config.telnet_port,
            tcl_port=self.config.tcl_port,
            execute=bool(execute),
            resume_after_halt=bool(self.config.resume_after_halt),
        )

    def _snapshot(
        self,
        result: OpenOcdGdbReadOnlyResult,
        *,
        status,
        project_path: str | Path | None,
        target_name: str,
        connection_attempted: bool,
        connection_established: bool,
    ) -> DebugBackendSessionSnapshot:
        project = Path(project_path).expanduser().resolve() if project_path else None
        pc_location = _pc_location_from_result(result)
        state_text = "已连接" if connection_established else "连接失败" if result.attempted else "尚未接入"
        diagnostic_rows = (
            ("后端", self.display_name),
            ("状态", state_text),
            ("说明", status.detail),
            ("本机档案", "OpenOCD/GDB 只读发现"),
            ("本机安全边界", result.profile.safety_note),
        ) + debug_toolchain_descriptor(self.kind.value).diagnostic_rows()
        diagnostic_rows += debug_toolchain_command_plan(self.kind.value).diagnostic_rows()
        diagnostic_rows += result.diagnostic_rows()
        diagnostics = tuple(DebugBackendDiagnostic(key, value) for key, value in diagnostic_rows)
        payload = {
            "backend": self.kind.value,
            "adapter": self.display_name,
            "project": str(project or ""),
            "target": str(target_name or ""),
            "attempted": connection_attempted,
            "established": connection_established,
            "stage": result.stage,
            "pc": result.pc_value,
        }
        return DebugBackendSessionSnapshot(
            schema_version=1,
            backend=self.kind,
            adapter_name=self.display_name,
            snapshot_id=backend_snapshot_id(payload),
            captured_at=now_iso(),
            status=status,
            diagnostics=diagnostics,
            capabilities=tuple(sorted(status.capabilities.__dict__.items())),
            read_only=True,
            connection_attempted=connection_attempted,
            connection_established=connection_established,
            target_running=True if result.target_state == "running" else False if result.target_state == "stopped" else None,
            project_path=project,
            target_name=str(target_name or ""),
            pc_location=pc_location,
        )


def _axf_from_project(project_path: str | Path | None) -> Path | None:
    if not project_path:
        return None
    path = Path(project_path).expanduser()
    if path.suffix.lower() in {".axf", ".elf"} and path.exists():
        return path.resolve()
    return None


def _pc_location_from_result(result: OpenOcdGdbReadOnlyResult) -> DebugPcLocation | None:
    if not result.pc_value:
        return None
    gdb_text = "\n".join(result.gdb_lines)
    frame = re.search(
        r'frame=\{addr="(?P<addr>0x[0-9a-fA-F]+)",func="(?P<func>[^"]*)".*?fullname="(?P<path>[^"]*)",line="(?P<line>\d+)"',
        gdb_text,
    )
    if frame:
        return DebugPcLocation(
            path=Path(frame.group("path").replace("\\\\", "\\")).expanduser(),
            line=int(frame.group("line")),
            address=int(frame.group("addr"), 16),
            function=frame.group("func"),
            source="openocd_gdb",
            complete=True,
            message="GDB/MI stopped frame readback",
        )
    pc_match = re.search(r"0x[0-9a-fA-F]+", result.pc_value)
    return DebugPcLocation(
        address=int(pc_match.group(0), 16) if pc_match else None,
        source="openocd_gdb",
        complete=True,
        message="GDB/MI $pc readback",
    )
