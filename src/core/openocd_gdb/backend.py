"""OpenOCD/GDB backend adapter for the Debug Workbench."""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import replace
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
from src.core.keil.breakpoint_sync import (
    KeilBreakpointCommandResult,
    KeilBreakpointSyncRequest,
    KeilBreakpointSyncResult,
)
from src.core.keil.commands import KeilBreakpointSyncAction
from src.core.openocd_gdb.readonly import (
    DEFAULT_AXF,
    OpenOcdGdbLiveSession,
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
    allow_halt: bool = True
    resume_after_halt: bool = True


@dataclass(frozen=True)
class OpenOcdGdbRuntimeControlResult:
    action: str
    attempted: bool
    succeeded: bool
    command: str
    output_lines: tuple[str, ...] = ()
    snapshot: DebugBackendSessionSnapshot | None = None
    error: str = ""

    @property
    def target_running(self) -> bool | None:
        if self.snapshot is not None:
            return self.snapshot.target_running
        return None

    def summary(self) -> str:
        label = _runtime_action_label(self.action)
        if self.error:
            return f"OpenOCD/GDB {label}失败：{self.error}"
        state = "运行中" if self.target_running is True else "已暂停" if self.target_running is False else "未知"
        return f"OpenOCD/GDB {label}完成：目标{state}"

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        return (
            ("OpenOCD/GDB 运行控制", _runtime_action_label(self.action)),
            ("OpenOCD/GDB 运行控制结果", "成功" if self.succeeded else "失败"),
            ("OpenOCD/GDB 运行控制命令", self.command or "--"),
            ("OpenOCD/GDB 运行控制输出", "\n".join(self.output_lines) if self.output_lines else "--"),
            ("OpenOCD/GDB 运行控制错误", self.error or "--"),
        )


class OpenOcdGdbBackendAdapter:
    kind = DebugBackendKind.OPENOCD_GDB
    display_name = "OpenOCD / GDB"

    def __init__(self, config: OpenOcdGdbBackendConfig | None = None) -> None:
        self.config = config or OpenOcdGdbBackendConfig()
        self._session: OpenOcdGdbLiveSession | None = None

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
        remote_snapshot = None
        if execute:
            try:
                session = self._ensure_session(project_path)
                if not session.pc_value:
                    session.read_pc()
                remote_snapshot = session.breakpoint_snapshot(project_path=project_path, target_name=target_name)
                result = self._result_from_session(session, detail="Connected through OpenOCD/GDB and read $pc.")
            except Exception as exc:
                result = run_openocd_gdb_readonly_probe(self._request(execute=False, project_path=project_path))
                result = replace(
                    result,
                    attempted=True,
                    succeeded=False,
                    stage="live_backend_attach",
                    detail=str(exc),
                )
        else:
            result = run_openocd_gdb_readonly_probe(self._request(execute=False, project_path=project_path))
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
            capabilities=DebugCapabilities(
                can_discover=True,
                can_attach=True,
                can_disconnect=bool(execute and result.succeeded),
                can_halt=bool(execute and result.succeeded),
                can_run=bool(execute and result.succeeded),
                can_reset=bool(execute and result.succeeded),
                can_sync_breakpoints=bool(execute and result.succeeded),
            ),
            error="" if result.succeeded or not execute else result.detail,
        )
        return self._snapshot(
            result,
            status=status,
            project_path=project_path,
            target_name=target_name,
            connection_attempted=bool(attempt_connection),
            connection_established=bool(execute and result.succeeded),
            remote_breakpoint_snapshot=remote_snapshot,
        )

    def sync_breakpoints(self, request: KeilBreakpointSyncRequest) -> KeilBreakpointSyncResult:
        commands: list[KeilBreakpointCommandResult] = []
        try:
            session = self._ensure_session(request.project_path)
            for operation in request.operations:
                command_text = _gdb_command_for_operation(operation)
                if operation.action == KeilBreakpointSyncAction.NOOP:
                    commands.append(
                        KeilBreakpointCommandResult(
                            operation=operation,
                            command=command_text,
                            attempted=False,
                            succeeded=True,
                            output="noop",
                        )
                    )
                    continue
                if not operation.valid:
                    commands.append(
                        KeilBreakpointCommandResult(
                            operation=operation,
                            command=command_text,
                            attempted=False,
                            succeeded=False,
                            error=operation.reason or "OpenOCD/GDB 断点操作受限，未发送",
                        )
                    )
                    continue
                try:
                    updated_operation = operation
                    if operation.action == KeilBreakpointSyncAction.ADD:
                        remote_id, output_lines = session.insert_breakpoint(_gdb_location(operation))
                        if remote_id:
                            updated_operation = replace(operation, remote_id=remote_id)
                        succeeded = bool(remote_id)
                    elif operation.action == KeilBreakpointSyncAction.REMOVE:
                        output_lines = session.delete_breakpoint(operation.remote_id)
                        succeeded = _mi_succeeded(output_lines)
                    elif operation.action == KeilBreakpointSyncAction.ENABLE:
                        output_lines = session.enable_breakpoint(operation.remote_id)
                        succeeded = _mi_succeeded(output_lines)
                    elif operation.action == KeilBreakpointSyncAction.DISABLE:
                        output_lines = session.disable_breakpoint(operation.remote_id)
                        succeeded = _mi_succeeded(output_lines)
                    else:
                        output_lines = ()
                        succeeded = False
                    commands.append(
                        KeilBreakpointCommandResult(
                            operation=updated_operation,
                            command=command_text,
                            attempted=True,
                            succeeded=succeeded,
                            output="\n".join(output_lines),
                            error="" if succeeded else _mi_error(output_lines) or "OpenOCD/GDB 未接受断点命令",
                        )
                    )
                except Exception as exc:
                    commands.append(
                        KeilBreakpointCommandResult(
                            operation=operation,
                            command=command_text,
                            attempted=True,
                            succeeded=False,
                            error=str(exc),
                        )
                    )
            snapshot = session.breakpoint_snapshot(
                project_path=request.project_path,
                target_name=request.target_name,
            )
            return KeilBreakpointSyncResult(request=request, commands=tuple(commands), remote_snapshot=snapshot)
        except Exception as exc:
            return KeilBreakpointSyncResult(request=request, commands=tuple(commands), remote_snapshot=None, error=str(exc))

    def halt_target(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
    ) -> OpenOcdGdbRuntimeControlResult:
        return self._runtime_control("halt", project_path=project_path, target_name=target_name)

    def run_target(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
    ) -> OpenOcdGdbRuntimeControlResult:
        return self._runtime_control("run", project_path=project_path, target_name=target_name)

    def reset_target(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
    ) -> OpenOcdGdbRuntimeControlResult:
        return self._runtime_control("reset", project_path=project_path, target_name=target_name)

    def disconnect(self, timeout: float | None = None) -> bool:
        session = self._session
        self._session = None
        if session is not None:
            session.close()
        return True

    def request_shutdown(self) -> None:
        self.disconnect()

    def _request(self, *, execute: bool, project_path: str | Path | None) -> OpenOcdGdbReadOnlyRequest:
        return OpenOcdGdbReadOnlyRequest(
            openocd_root=self.config.openocd_root,
            gdb_path=self.config.gdb_path,
            axf_path=_axf_from_project(project_path) or self.config.axf_path,
            gdb_port=self.config.gdb_port,
            telnet_port=self.config.telnet_port,
            tcl_port=self.config.tcl_port,
            execute=bool(execute),
            allow_halt=bool(self.config.allow_halt),
            resume_after_halt=bool(self.config.resume_after_halt),
        )

    def _ensure_session(self, project_path: str | Path | None) -> OpenOcdGdbLiveSession:
        if self._session is not None and self._session.alive:
            return self._session
        request = self._request(execute=True, project_path=project_path)
        session = OpenOcdGdbLiveSession(request)
        session.start()
        self._session = session
        return session

    def _runtime_control(
        self,
        action: str,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
    ) -> OpenOcdGdbRuntimeControlResult:
        command = _runtime_command(action)
        try:
            session = self._ensure_session(project_path)
            if action == "halt":
                succeeded, output_lines = session.halt_target()
            elif action == "run":
                succeeded, output_lines = session.run_target()
            elif action == "reset":
                succeeded, output_lines = session.reset_target()
            else:
                raise ValueError(f"unsupported OpenOCD/GDB runtime action: {action}")
            snapshot = self.read_only_session_snapshot(
                project_path=project_path,
                target_name=target_name,
                attempt_connection=True,
                query_status=True,
            )
            error = ""
            if not succeeded:
                error = _mi_error(output_lines) or f"OpenOCD/GDB 未完成{_runtime_action_label(action)}命令"
            elif action == "halt" and snapshot.target_running is not False:
                error = "暂停后状态回读仍不是已暂停"
            elif action == "run" and snapshot.target_running is not True:
                error = "运行后状态回读仍不是运行中"
            elif action == "reset" and snapshot.target_running is not False:
                error = "复位后状态回读仍不是已暂停"
            return OpenOcdGdbRuntimeControlResult(
                action=action,
                attempted=True,
                succeeded=bool(succeeded and not error),
                command=command,
                output_lines=tuple(output_lines),
                snapshot=snapshot,
                error=error,
            )
        except Exception as exc:
            return OpenOcdGdbRuntimeControlResult(
                action=action,
                attempted=True,
                succeeded=False,
                command=command,
                error=str(exc),
            )

    @staticmethod
    def _result_from_session(session: OpenOcdGdbLiveSession, *, detail: str) -> OpenOcdGdbReadOnlyResult:
        return OpenOcdGdbReadOnlyResult(
            attempted=True,
            succeeded=True,
            stage="live_backend_attach",
            detail=detail,
            profile=session.profile,
            openocd_command=session.openocd_command,
            gdb_command=session.gdb_command,
            openocd_lines=session.openocd_lines,
            gdb_lines=session.gdb_lines,
            target_state=session.target_state,
            pc_value=session.pc_value,
            halted_by_probe=session.halted_by_probe,
            resumed_after_halt=session.resumed_after_halt,
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
        remote_breakpoint_snapshot=None,
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
            remote_breakpoint_snapshot=remote_breakpoint_snapshot,
            remote_breakpoint_snapshot_id=getattr(remote_breakpoint_snapshot, "snapshot_id", "") if remote_breakpoint_snapshot is not None else "",
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


def _gdb_location(operation) -> str:
    if operation.path is not None and int(operation.line or 0) > 0:
        return f"{Path(operation.path).as_posix()}:{int(operation.line)}"
    if operation.address is not None:
        return f"*0x{int(operation.address):08X}"
    return f"{Path(operation.path).name}:{int(operation.line or 0)}"


def _gdb_command_for_operation(operation) -> str:
    if operation.action == KeilBreakpointSyncAction.ADD:
        return f"-break-insert {_gdb_location(operation)}"
    if operation.action == KeilBreakpointSyncAction.REMOVE:
        return f"-break-delete {operation.remote_id}"
    if operation.action == KeilBreakpointSyncAction.ENABLE:
        return f"-break-enable {operation.remote_id}"
    if operation.action == KeilBreakpointSyncAction.DISABLE:
        return f"-break-disable {operation.remote_id}"
    if operation.action == KeilBreakpointSyncAction.UPDATE_CONDITION:
        return f"# unsupported condition update {_gdb_location(operation)}"
    return f"# noop {_gdb_location(operation)}"


def _mi_succeeded(lines: tuple[str, ...]) -> bool:
    return any("^done" in line or "^running" in line for line in lines)


def _mi_error(lines: tuple[str, ...]) -> str:
    joined = "\n".join(lines)
    match = re.search(r'msg="([^"]+)"', joined)
    return match.group(1).replace("\\n", " ") if match else ""


def _runtime_command(action: str) -> str:
    return {
        "halt": "-exec-interrupt",
        "run": "-exec-continue",
        "reset": '-interpreter-exec console "monitor reset halt"',
    }.get(str(action or ""), "")


def _runtime_action_label(action: str) -> str:
    return {
        "halt": "暂停",
        "run": "运行",
        "reset": "复位",
    }.get(str(action or ""), str(action or "--"))
