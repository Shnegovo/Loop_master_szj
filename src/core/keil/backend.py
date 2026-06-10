"""Keil/uVision debugger backend adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.core.debug_backend import (
    DebugBackendDiagnostic,
    DebugBackendSessionSnapshot,
    backend_snapshot_id,
    now_iso,
)
from src.core.debug_workbench import (
    DebugBackendKind,
    DebugCapabilities,
    DebugRuntimeState,
    DebugWorkbenchStatus,
    make_debug_status,
    status_from_uvsock_preflight,
)
from src.core.keil.uvsock import (
    UvscConnectionResult,
    UvscLaunchPlan,
    UvscPreflight,
    attempt_existing_uvsock_connection,
    build_uvision_uvsock_command,
    check_uvsock_preflight,
)


@dataclass(frozen=True)
class KeilBackendConfig:
    root: Path | None = None
    port: int = 4827
    connection_name: str = "LoopMaster"


class KeilUvSockBackendAdapter:
    kind = DebugBackendKind.KEIL
    display_name = "Keil / UVSOCK"

    def __init__(self, config: KeilBackendConfig | None = None) -> None:
        self.config = config or KeilBackendConfig()

    def discover(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
        previous_status: DebugWorkbenchStatus | None = None,
    ) -> DebugBackendSessionSnapshot:
        preflight = check_uvsock_preflight(root=self.config.root, require_running=False)
        status = status_from_uvsock_preflight(preflight, previous_status)
        launch_plan = build_uvision_uvsock_command(
            root=self.config.root,
            port=self.config.port,
            project=project_path or status.project_path,
            target=target_name or status.target_name or None,
        )
        return self._snapshot(
            status=status,
            preflight=preflight,
            launch_plan=launch_plan,
            connection=None,
            project_path=project_path or status.project_path,
            target_name=target_name or status.target_name,
            connection_attempted=False,
        )

    def read_only_session_snapshot(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
        previous_status: DebugWorkbenchStatus | None = None,
        attempt_connection: bool = True,
        query_status: bool = True,
    ) -> DebugBackendSessionSnapshot:
        launch_plan: UvscLaunchPlan | None = None
        if attempt_connection:
            preflight, connection = attempt_existing_uvsock_connection(
                root=self.config.root,
                port=self.config.port,
                query_status=query_status,
                connection_name=self.config.connection_name,
            )
            status = _status_from_read_only_connection(connection, previous_status)
        else:
            preflight = check_uvsock_preflight(root=self.config.root, require_running=True)
            connection = None
            status = status_from_uvsock_preflight(preflight, previous_status)

        launch_plan = build_uvision_uvsock_command(
            root=self.config.root,
            port=self.config.port,
            project=project_path or status.project_path,
            target=target_name or status.target_name or None,
        )
        if not attempt_connection and preflight.can_attempt_connection:
            status = make_debug_status(
                state=DebugRuntimeState.KEIL_DISCOVERED,
                backend=DebugBackendKind.KEIL,
                detail="Keil/uVision 已发现，只读连接快照尚未尝试",
                project_path=project_path or status.project_path,
                target_name=target_name or status.target_name,
                capabilities=status.capabilities,
            )
        return self._snapshot(
            status=status,
            preflight=preflight,
            launch_plan=launch_plan,
            connection=connection,
            project_path=project_path or status.project_path,
            target_name=target_name or status.target_name,
            connection_attempted=bool(connection and connection.attempted),
        )

    def _snapshot(
        self,
        *,
        status: DebugWorkbenchStatus,
        preflight: UvscPreflight,
        launch_plan: UvscLaunchPlan,
        connection: UvscConnectionResult | None,
        project_path: str | Path | None,
        target_name: str,
        connection_attempted: bool,
    ) -> DebugBackendSessionSnapshot:
        project = Path(project_path).expanduser().resolve() if project_path else status.project_path
        diagnostics = _diagnostics(preflight, launch_plan, connection, self.config.port)
        capabilities = tuple(sorted(preflight.discovery.capability_flags().items()))
        payload = {
            "backend": self.kind.value,
            "root": str(preflight.discovery.root or self.config.root or ""),
            "port": self.config.port,
            "state": status.state.value,
            "detail": status.detail,
            "project": str(project or ""),
            "target": target_name,
            "connection_attempted": connection_attempted,
            "connection_established": bool(connection and connection.connected),
            "target_running": connection.target_running if connection else None,
            "diagnostics": [(item.key, item.value) for item in diagnostics],
        }
        return DebugBackendSessionSnapshot(
            schema_version=1,
            backend=self.kind,
            adapter_name=self.display_name,
            snapshot_id=backend_snapshot_id(payload),
            captured_at=now_iso(),
            status=status,
            diagnostics=diagnostics,
            capabilities=capabilities,
            read_only=True,
            connection_attempted=connection_attempted,
            connection_established=bool(connection and connection.connected),
            target_running=connection.target_running if connection else None,
            port=self.config.port,
            project_path=project,
            target_name=str(target_name or status.target_name or ""),
            remote_breakpoint_snapshot_id="",
        )


def _diagnostics(
    preflight: UvscPreflight,
    launch_plan: UvscLaunchPlan,
    connection: UvscConnectionResult | None,
    port: int,
) -> tuple[DebugBackendDiagnostic, ...]:
    discovery = preflight.discovery
    dll = preflight.load_result.dll.path if preflight.load_result.dll else "--"
    reasons = "；".join(_reason_text(str(reason)) for reason in preflight.reasons) if preflight.reasons else "OK"
    command = launch_plan.display_command if launch_plan.command else "--"
    launch_status = "可启动" if launch_plan.ready else "仅预览"
    if launch_plan.reasons:
        launch_status = "；".join(_reason_text(str(reason)) for reason in launch_plan.reasons)
    rows = [
        DebugBackendDiagnostic("后端", "Keil / UVSOCK"),
        DebugBackendDiagnostic("模式", "只读快照" if connection else "预检"),
        DebugBackendDiagnostic("uVision 进程", f"{len(preflight.processes)} 个" if preflight.processes else "未运行"),
        DebugBackendDiagnostic("可尝试连接", "是" if preflight.can_attempt_connection else "否"),
        DebugBackendDiagnostic("预检原因", reasons),
        DebugBackendDiagnostic("UVSOCK 端口", str(port)),
        DebugBackendDiagnostic("Keil 根目录", str(discovery.root or "--")),
        DebugBackendDiagnostic("UVSOCK DLL", str(dll)),
        DebugBackendDiagnostic("DLL 加载", "已加载" if preflight.load_result.loaded else preflight.load_result.error or "失败"),
        DebugBackendDiagnostic("UV4 目录", str(discovery.uv4_dir or "--")),
        DebugBackendDiagnostic("启动预览", launch_status),
        DebugBackendDiagnostic("启动命令", command),
    ]
    if connection is not None:
        rows.extend(
            [
                DebugBackendDiagnostic("连接尝试", "是" if connection.attempted else "否"),
                DebugBackendDiagnostic("连接结果", "已连接" if connection.connected else "未连接"),
                DebugBackendDiagnostic("目标运行", _target_running_text(connection.target_running)),
                DebugBackendDiagnostic("UVSC 状态", connection.status_name or (str(connection.status_code) if connection.status_code is not None else "--")),
                DebugBackendDiagnostic("连接错误", connection.error or "--"),
            ]
        )
    return tuple(rows)


def _reason_text(reason: str) -> str:
    translations = {
        "Keil/uVision was not discovered": "未发现 Keil/uVision",
        "UVSOCK DLL could not be loaded": "UVSOCK DLL 加载失败",
        "uVision is not running": "uVision 未运行",
        "UVSOCK port must be in range 1..65535": "UVSOCK 端口必须在 1..65535 范围内",
        "uVision executable is missing": "uVision 可执行文件缺失",
        "No Keil project was provided; launch is guidance-only": "未选择 Keil 工程，启动命令仅作预览",
    }
    if reason.startswith("Keil project does not exist:"):
        return "Keil 工程不存在：" + reason.split(":", 1)[1].strip()
    return translations.get(reason, reason)


def _target_running_text(value: bool | None) -> str:
    if value is True:
        return "运行中"
    if value is False:
        return "已暂停"
    return "未知"


def _status_from_read_only_connection(
    connection: UvscConnectionResult,
    previous: DebugWorkbenchStatus | None,
) -> DebugWorkbenchStatus:
    previous_status = previous or make_debug_status(state=DebugRuntimeState.DISCONNECTED)
    if not connection.connected:
        message = connection.error or "UVSOCK 只读连接失败"
        return make_debug_status(
            state=DebugRuntimeState.ERROR,
            backend=DebugBackendKind.KEIL,
            detail=message,
            project_path=previous_status.project_path,
            target_name=previous_status.target_name,
            error=message,
        )
    if connection.target_running is True:
        state = DebugRuntimeState.RUNNING
        detail = "UVSOCK 只读快照已连接，目标运行中"
    elif connection.target_running is False:
        state = DebugRuntimeState.PAUSED
        detail = "UVSOCK 只读快照已连接，目标已暂停"
    else:
        state = DebugRuntimeState.KEIL_ATTACHED
        detail = "UVSOCK 只读快照已连接，等待运行状态"
    return make_debug_status(
        state=state,
        backend=DebugBackendKind.KEIL,
        detail=detail,
        project_path=previous_status.project_path,
        target_name=previous_status.target_name,
        capabilities=DebugCapabilities(
            can_discover=True,
            can_attach=True,
            can_disconnect=False,
            can_read_variables=True,
            can_write_variables=False,
            can_halt=False,
            can_run=False,
            can_step=False,
            can_sync_breakpoints=False,
        ),
    )
