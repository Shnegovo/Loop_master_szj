"""Core models for the future modern debugger workbench."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from src.core.debug_sources import (
    SOURCE_LANGUAGES,
    SourceEntry,
    SourceManifest,
    SourceTreeNode,
    source_entries_from_keil_project,
    source_tree_from_entries,
)
from src.core.keil.project import KeilProject, KeilTarget


@dataclass(frozen=True)
class CodeLine:
    number: int
    text: str


@dataclass(frozen=True)
class CodeDocument:
    path: Path
    language: str
    lines: tuple[CodeLine, ...]

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def line_text(self, number: int) -> str:
        if number < 1 or number > len(self.lines):
            return ""
        return self.lines[number - 1].text


@dataclass(frozen=True)
class SearchMatch:
    line: int
    column: int
    text: str


@dataclass(frozen=True)
class LineDecoration:
    line: int
    kind: str
    label: str = ""
    enabled: bool = True
    verified: bool = False
    message: str = ""


class DebugBackendKind(str, Enum):
    NONE = "none"
    KEIL = "keil"
    OPENOCD_GDB = "openocd_gdb"
    PYOCD = "pyocd"
    OFFLINE = "offline"


class DebugRuntimeState(str, Enum):
    DISCONNECTED = "disconnected"
    KEIL_DISCOVERED = "keil_discovered"
    KEIL_ATTACHED = "keil_attached"
    PAUSED = "paused"
    RUNNING = "running"
    ERROR = "error"


@dataclass(frozen=True)
class DebugCapabilities:
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
        return not self.can_write_variables


@dataclass(frozen=True)
class DebugWorkbenchStatus:
    backend: DebugBackendKind = DebugBackendKind.NONE
    state: DebugRuntimeState = DebugRuntimeState.DISCONNECTED
    label: str = "未连接"
    detail: str = "尚未连接调试后端"
    project_path: Path | None = None
    target_name: str = ""
    current_pc_line: int | None = None
    run_line: int | None = None
    error: str = ""
    capabilities: DebugCapabilities = field(default_factory=DebugCapabilities)


@dataclass(frozen=True)
class DebugAction:
    key: str
    title: str
    enabled: bool
    reason: str = ""


class DebugPlanRisk(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class DebugCommandPlan:
    key: str
    title: str
    intent: str
    status: str
    risk: DebugPlanRisk
    preconditions_met: bool
    execution_enabled: bool
    disabled_reason: str = ""
    requirements: tuple[str, ...] = ()
    safety_notes: tuple[str, ...] = ()
    preview_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class Breakpoint:
    path: Path
    line: int
    enabled: bool = True
    condition: str = ""
    hit_count: int = 0
    verified: bool = False
    message: str = ""


class BreakpointStore:
    """Pure breakpoint state for a future gutter/list UI."""

    def __init__(self) -> None:
        self._breakpoints: dict[tuple[str, int], Breakpoint] = {}

    def all(self) -> tuple[Breakpoint, ...]:
        return tuple(sorted(self._breakpoints.values(), key=lambda bp: (str(bp.path).lower(), bp.line)))

    def for_file(self, path: str | Path) -> tuple[Breakpoint, ...]:
        key_path = _normalise_key_path(path)
        return tuple(bp for bp in self.all() if _normalise_key_path(bp.path) == key_path)

    def get(self, path: str | Path, line: int) -> Breakpoint | None:
        return self._breakpoints.get(_breakpoint_key(path, line))

    def add(
        self,
        path: str | Path,
        line: int,
        *,
        enabled: bool = True,
        condition: str = "",
        message: str = "",
    ) -> Breakpoint:
        line = _valid_line(line)
        breakpoint = Breakpoint(
            path=Path(path).expanduser().resolve(),
            line=line,
            enabled=bool(enabled),
            condition=str(condition),
            message=str(message),
        )
        self._breakpoints[_breakpoint_key(breakpoint.path, line)] = breakpoint
        return breakpoint

    def toggle(self, path: str | Path, line: int) -> Breakpoint | None:
        key = _breakpoint_key(path, line)
        if key in self._breakpoints:
            del self._breakpoints[key]
            return None
        return self.add(path, line)

    def remove(self, path: str | Path, line: int) -> bool:
        return self._breakpoints.pop(_breakpoint_key(path, line), None) is not None

    def set_enabled(self, path: str | Path, line: int, enabled: bool) -> Breakpoint:
        return self._update(path, line, enabled=bool(enabled))

    def set_condition(self, path: str | Path, line: int, condition: str) -> Breakpoint:
        return self._update(path, line, condition=str(condition))

    def set_verified(self, path: str | Path, line: int, verified: bool, message: str = "") -> Breakpoint:
        return self._update(path, line, verified=bool(verified), message=str(message))

    def set_message(self, path: str | Path, line: int, message: str) -> Breakpoint:
        return self._update(path, line, message=str(message))

    def record_hit(self, path: str | Path, line: int, count: int = 1) -> Breakpoint:
        breakpoint = self.get(path, line)
        if breakpoint is None:
            raise KeyError(f"breakpoint does not exist: {path}:{line}")
        next_count = max(0, breakpoint.hit_count + int(count))
        return self._update(path, line, hit_count=next_count)

    def clear(self) -> None:
        self._breakpoints.clear()

    def _update(self, path: str | Path, line: int, **changes) -> Breakpoint:
        key = _breakpoint_key(path, line)
        breakpoint = self._breakpoints.get(key)
        if breakpoint is None:
            raise KeyError(f"breakpoint does not exist: {path}:{line}")
        updated = replace(breakpoint, **changes)
        self._breakpoints[key] = updated
        return updated


class DebugWorkbenchSession:
    """Pure debug-workbench state controller with no hardware side effects."""

    def __init__(self, backend: DebugBackendKind | str = DebugBackendKind.KEIL) -> None:
        backend_kind = _coerce_backend(backend)
        self._status = make_debug_status(
            state=DebugRuntimeState.DISCONNECTED,
            backend=backend_kind,
        )

    @property
    def status(self) -> DebugWorkbenchStatus:
        return self._status

    def set_project(self, project: KeilProject, target_name: str | None = None) -> DebugWorkbenchStatus:
        target = _select_target(project, target_name)
        self._status = replace(
            self._status,
            project_path=project.path,
            target_name=target.name if target is not None else "",
        )
        return self._status

    def mark_discovered(
        self,
        detail: str = "Keil/uVision 已发现，尚未连接调试会话",
        *,
        can_attach: bool = True,
    ) -> DebugWorkbenchStatus:
        capabilities = default_debug_capabilities(
            DebugRuntimeState.KEIL_DISCOVERED,
            can_attach=can_attach,
        )
        self._status = make_debug_status(
            state=DebugRuntimeState.KEIL_DISCOVERED,
            backend=DebugBackendKind.KEIL,
            detail=detail,
            project_path=self._status.project_path,
            target_name=self._status.target_name,
            capabilities=capabilities,
        )
        return self._status

    def mark_attached(
        self,
        *,
        running: bool | None = None,
        runtime_control: bool = False,
        breakpoint_sync: bool = False,
        variable_write: bool = False,
        detail: str = "Keil 调试会话已连接",
    ) -> DebugWorkbenchStatus:
        if running is True:
            state = DebugRuntimeState.RUNNING
        elif running is False:
            state = DebugRuntimeState.PAUSED
        else:
            state = DebugRuntimeState.KEIL_ATTACHED
        self._status = make_debug_status(
            state=state,
            backend=DebugBackendKind.KEIL,
            detail=detail,
            project_path=self._status.project_path,
            target_name=self._status.target_name,
            capabilities=default_debug_capabilities(
                state,
                runtime_control=runtime_control,
                breakpoint_sync=breakpoint_sync,
                variable_write=variable_write,
            ),
        )
        return self._status

    def update_runtime(
        self,
        *,
        running: bool,
        current_pc_line: int | None = None,
        run_line: int | None = None,
        detail: str = "",
    ) -> DebugWorkbenchStatus:
        state = DebugRuntimeState.RUNNING if running else DebugRuntimeState.PAUSED
        capabilities = default_debug_capabilities(
            state,
            runtime_control=(
                self._status.capabilities.can_halt
                or self._status.capabilities.can_run
                or self._status.capabilities.can_reset
                or self._status.capabilities.can_step
            ),
            breakpoint_sync=self._status.capabilities.can_sync_breakpoints,
            variable_write=self._status.capabilities.can_write_variables,
        )
        self._status = make_debug_status(
            state=state,
            backend=self._status.backend,
            detail=detail or ("目标运行中" if running else "目标已暂停"),
            project_path=self._status.project_path,
            target_name=self._status.target_name,
            current_pc_line=current_pc_line,
            run_line=run_line,
            capabilities=capabilities,
        )
        return self._status

    def mark_error(self, message: str) -> DebugWorkbenchStatus:
        self._status = make_debug_status(
            state=DebugRuntimeState.ERROR,
            backend=self._status.backend,
            detail=str(message),
            project_path=self._status.project_path,
            target_name=self._status.target_name,
            error=str(message),
        )
        return self._status

    def disconnect(self) -> DebugWorkbenchStatus:
        self._status = make_debug_status(
            state=DebugRuntimeState.DISCONNECTED,
            backend=self._status.backend,
            project_path=self._status.project_path,
            target_name=self._status.target_name,
        )
        return self._status

    def actions(self) -> tuple[DebugAction, ...]:
        return debug_actions_for_status(self._status)

    def command_plans(self) -> tuple[DebugCommandPlan, ...]:
        return debug_command_plans_for_status(self._status)

    def apply_status(self, status: DebugWorkbenchStatus) -> DebugWorkbenchStatus:
        self._status = status
        return self._status

    def apply_uvsock_preflight(self, preflight: object) -> DebugWorkbenchStatus:
        self._status = status_from_uvsock_preflight(preflight, self._status)
        return self._status

    def apply_uvsock_connection(self, connection: object) -> DebugWorkbenchStatus:
        self._status = status_from_uvsock_connection(connection, self._status)
        return self._status


def make_debug_status(
    *,
    state: DebugRuntimeState | str,
    backend: DebugBackendKind | str = DebugBackendKind.NONE,
    detail: str = "",
    project_path: str | Path | None = None,
    target_name: str = "",
    current_pc_line: int | None = None,
    run_line: int | None = None,
    error: str = "",
    capabilities: DebugCapabilities | None = None,
) -> DebugWorkbenchStatus:
    state_value = _coerce_state(state)
    backend_value = _coerce_backend(backend)
    return DebugWorkbenchStatus(
        backend=backend_value,
        state=state_value,
        label=debug_state_label(state_value),
        detail=detail or debug_state_detail(state_value),
        project_path=Path(project_path).expanduser().resolve() if project_path else None,
        target_name=str(target_name or ""),
        current_pc_line=_optional_positive_int(current_pc_line),
        run_line=_optional_positive_int(run_line),
        error=str(error or ""),
        capabilities=capabilities or default_debug_capabilities(state_value),
    )


def default_debug_capabilities(
    state: DebugRuntimeState | str,
    *,
    can_attach: bool = False,
    runtime_control: bool = False,
    breakpoint_sync: bool = False,
    variable_write: bool = False,
) -> DebugCapabilities:
    state_value = _coerce_state(state)
    can_discover = state_value in {
        DebugRuntimeState.DISCONNECTED,
        DebugRuntimeState.KEIL_DISCOVERED,
        DebugRuntimeState.ERROR,
    }
    attached = state_value in {
        DebugRuntimeState.KEIL_ATTACHED,
        DebugRuntimeState.PAUSED,
        DebugRuntimeState.RUNNING,
    }
    return DebugCapabilities(
        can_discover=can_discover,
        can_attach=can_attach and state_value == DebugRuntimeState.KEIL_DISCOVERED,
        can_disconnect=attached,
        can_read_variables=attached,
        can_write_variables=attached and variable_write,
        can_halt=attached and runtime_control and state_value != DebugRuntimeState.PAUSED,
        can_run=attached and runtime_control and state_value != DebugRuntimeState.RUNNING,
        can_reset=attached and runtime_control,
        can_step=attached and runtime_control and state_value == DebugRuntimeState.PAUSED,
        can_sync_breakpoints=attached and breakpoint_sync,
    )


def debug_actions_for_status(status: DebugWorkbenchStatus) -> tuple[DebugAction, ...]:
    caps = status.capabilities
    running = status.state == DebugRuntimeState.RUNNING
    paused = status.state == DebugRuntimeState.PAUSED
    attached = status.state in {
        DebugRuntimeState.KEIL_ATTACHED,
        DebugRuntimeState.PAUSED,
        DebugRuntimeState.RUNNING,
    }
    return (
        DebugAction("discover", "发现 Keil", caps.can_discover, _disabled_reason(caps.can_discover, status)),
        DebugAction("attach", "连接", caps.can_attach, _disabled_reason(caps.can_attach, status)),
        DebugAction("disconnect", "断开", caps.can_disconnect, _disabled_reason(caps.can_disconnect, status)),
        DebugAction("halt", "暂停", caps.can_halt and running, _disabled_reason(caps.can_halt and running, status)),
        DebugAction("run", "运行", caps.can_run and (paused or attached), _disabled_reason(caps.can_run and (paused or attached), status)),
        DebugAction("reset", "复位", caps.can_reset and attached, _disabled_reason(caps.can_reset and attached, status)),
        DebugAction("step", "单步", caps.can_step and paused, _disabled_reason(caps.can_step and paused, status)),
        DebugAction("step_over", "跨过", caps.can_step and paused, _disabled_reason(caps.can_step and paused, status)),
        DebugAction(
            "sync_breakpoints",
            "同步断点",
            caps.can_sync_breakpoints and attached,
            _disabled_reason(caps.can_sync_breakpoints and attached, status),
        ),
        DebugAction(
            "write_variables",
            "写变量",
            caps.can_write_variables and attached,
            _disabled_reason(caps.can_write_variables and attached, status),
        ),
    )


def debug_command_plans_for_status(status: DebugWorkbenchStatus) -> tuple[DebugCommandPlan, ...]:
    actions = {action.key: action for action in debug_actions_for_status(status)}
    is_keil = status.backend == DebugBackendKind.KEIL
    discover_title = "发现 Keil" if is_keil else "发现后端"
    discover_intent = "刷新 Keil 安装、UVSOCK DLL 与 uVision 进程状态" if is_keil else "刷新所选调试后端的可用性和连接预检状态"
    discover_safety = "不启动 Keil，不连接 UVSOCK，不访问 ST-Link 或目标板" if is_keil else "不启动外部进程，不连接探针，不访问目标板"
    discover_steps = ("定位 Keil 根目录", "检查 UVSOCK DLL", "读取 uVision 进程状态") if is_keil else ("检查后端配置", "生成只读诊断", "保持动作 dry-run")
    attach_intent = "通过 UVSOCK 连接已经处于 Debug 状态的 uVision" if is_keil else "读取所选调试后端的只读会话快照"
    attach_requirements = (
        "显式进入 UVSOCK 烟测/连接阶段",
        "确认 Keil 工程与 Target 和当前工作台一致",
        "连接失败只记录错误，不自动重试或复位目标",
    ) if is_keil else (
        "后端执行器已接入",
        "确认工程、target、探针和端口配置一致",
        "连接失败只记录错误，不自动重试或复位目标",
    )
    attach_steps = ("打开 UVSOCK 连接", "读取目标运行状态", "同步只读会话状态") if is_keil else ("请求只读快照", "读取目标状态", "同步诊断和能力")
    disconnect_intent = "关闭 LoopMaster 到 Keil 的调试桥接连接" if is_keil else "关闭 LoopMaster 到所选调试后端的会话"
    disconnect_requirements = ("已建立 UVSOCK 调试连接",) if is_keil else ("已建立调试后端会话",)
    disconnect_safety = "只释放桥接连接，不关闭 Keil，不复位目标" if is_keil else "只释放 LoopMaster 会话，不复位目标，不关闭用户手动启动的工具"
    disconnect_steps = ("停止状态轮询", "关闭 UVSOCK 连接", "保留本地断点与源码状态") if is_keil else ("停止状态轮询", "关闭只读连接", "保留本地断点与源码状态")
    control_backend = "Keil" if is_keil else "所选调试后端"
    breakpoint_intent = "把本地可视化断点同步到 Keil 调试会话" if is_keil else "把本地可视化断点同步到所选调试后端"
    breakpoint_requirements = (
        "断点 dry-run 映射到 Keil 支持的文件/行号",
        "逐条返回验证状态并允许失败项留在本地",
        "同步前显示新增、删除、禁用差异",
    ) if is_keil else (
        "断点 dry-run 映射到后端支持的位置格式",
        "逐条返回验证状态并允许失败项留在本地",
        "同步前显示新增、删除、禁用差异",
    )
    variable_intent = "从 LoopMaster 面板写入 Keil 可见的变量或内存" if is_keil else "从 LoopMaster 面板写入后端可见的变量或内存"
    return (
        _command_plan(
            actions,
            "discover",
            discover_title,
            DebugPlanRisk.INFO,
            discover_intent,
            requirements=("只读取本机路径与进程状态",),
            safety_notes=(discover_safety,),
            preview_steps=discover_steps,
            preview_only=False,
        ),
        _command_plan(
            actions,
            "attach",
            "连接调试会话",
            DebugPlanRisk.MEDIUM,
            attach_intent,
            requirements=attach_requirements,
            safety_notes=("连接本身不写变量，但会进入真实调试会话上下文",),
            preview_steps=attach_steps,
        ),
        _command_plan(
            actions,
            "disconnect",
            "断开调试会话",
            DebugPlanRisk.LOW,
            disconnect_intent,
            requirements=disconnect_requirements,
            safety_notes=(disconnect_safety,),
            preview_steps=disconnect_steps,
        ),
        _command_plan(
            actions,
            "halt",
            "暂停目标",
            DebugPlanRisk.HIGH,
            f"请求{control_backend}暂停正在运行的 MCU",
            requirements=(
                "已完成连接烟测并确认运行控制能力",
                "确认当前固件可被安全暂停",
                "暂停前记录时间戳和当前会话信息",
            ),
            safety_notes=("暂停会改变真实目标运行时序，可能影响电机/通信/控制闭环",),
            preview_steps=("发送 Halt 命令", "读取 PC 位置", "刷新源码运行标记"),
        ),
        _command_plan(
            actions,
            "run",
            "继续运行",
            DebugPlanRisk.HIGH,
            f"请求{control_backend}让暂停的 MCU 继续运行",
            requirements=(
                "已完成连接烟测并确认运行控制能力",
                "确认断点和变量写入计划不会造成意外停机",
                "Run 后继续轮询目标运行状态",
            ),
            safety_notes=("继续运行会恢复真实目标行为，需要确认外设和执行环境安全",),
            preview_steps=("发送 Run 命令", "确认运行状态", "更新工作台状态灯"),
        ),
        _command_plan(
            actions,
            "reset",
            "复位目标",
            DebugPlanRisk.HIGH,
            f"请求{control_backend}复位 MCU 并回读 PC",
            requirements=(
                "已完成连接烟测并确认运行控制能力",
                "确认复位不会损坏外设状态或当前实验流程",
                "复位后读取 PC 并重新定位启动源码",
            ),
            safety_notes=("复位会重启真实 MCU，所有外设、通信和控制状态都会被打断",),
            preview_steps=("发送 Reset 命令", "确认暂停状态", "读取 PC 和启动源码位置"),
        ),
        _command_plan(
            actions,
            "step",
            "单步",
            DebugPlanRisk.HIGH,
            f"请求{control_backend}执行一次源码/汇编单步",
            requirements=(
                "目标处于暂停状态",
                "已确认当前调用栈和中断状态适合单步",
                "单步后读取 PC 并重新定位源码",
            ),
            safety_notes=("单步可能进入中断、库函数或外设等待路径，需要清晰提示",),
            preview_steps=("发送 Step 命令", "读取 PC", "刷新当前行和调用上下文"),
        ),
        _command_plan(
            actions,
            "step_over",
            "跨过",
            DebugPlanRisk.HIGH,
            f"请求{control_backend}跨过当前源码行或函数调用",
            requirements=(
                "目标处于暂停状态",
                "当前 PC 已尽量回读到源码位置",
                "跨过后读取 PC 并重新定位源码",
            ),
            safety_notes=("跨过仍会真实执行当前语句，函数内副作用、外设访问和等待路径必须按真实目标处理",),
            preview_steps=("发送 Step Over 命令", "读取 PC", "刷新当前行和调用上下文"),
        ),
        _command_plan(
            actions,
            "sync_breakpoints",
            "同步断点",
            DebugPlanRisk.MEDIUM,
            breakpoint_intent,
            requirements=breakpoint_requirements,
            safety_notes=("断点会改变目标运行停顿位置，必须可撤销并显示验证结果",),
            preview_steps=("生成断点差异", "执行 Keil 断点命令", "回读验证状态"),
        ),
        _command_plan(
            actions,
            "write_variables",
            "写变量",
            DebugPlanRisk.HIGH,
            variable_intent,
            requirements=(
                "仅允许 RAM 符号或明确地址白名单",
                "完成类型、长度、对齐、数值范围和端序校验",
                "写入前展示旧值/新值差异并记录审计日志",
                "写入后立即回读校验，不一致则标红并停止批量写入",
            ),
            safety_notes=(
                "变量写入会直接改变 MCU 运行状态，PID/电机/通信变量必须有范围护栏",
                "禁止默认允许 Flash、寄存器控制位或未知指针地址写入",
            ),
            preview_steps=("解析符号和类型", "校验写入范围", "写入后回读并记录"),
        ),
    )


def status_from_uvsock_preflight(
    preflight: object,
    previous: DebugWorkbenchStatus | None = None,
) -> DebugWorkbenchStatus:
    previous_status = previous or make_debug_status(state=DebugRuntimeState.DISCONNECTED)
    discovery = getattr(preflight, "discovery", None)
    installed = bool(getattr(discovery, "installed", False))
    can_attempt = bool(getattr(preflight, "can_attempt_connection", False))
    reasons = tuple(_debug_reason_text(str(reason)) for reason in getattr(preflight, "reasons", ()) if reason)
    running = bool(getattr(preflight, "uvision_running", False))

    if not installed:
        message = "; ".join(reasons) or "未发现 Keil/uVision"
        return make_debug_status(
            state=DebugRuntimeState.ERROR,
            backend=DebugBackendKind.KEIL,
            detail=message,
            project_path=previous_status.project_path,
            target_name=previous_status.target_name,
            error=message,
        )

    if can_attempt:
        detail = "Keil/uVision 已发现，UVSOCK 可尝试连接"
    elif running:
        detail = "; ".join(reasons) or "Keil/uVision 正在运行，但 UVSOCK 尚不可连接"
    else:
        detail = "; ".join(reasons) or "Keil/uVision 已发现，等待启动调试会话"
    return make_debug_status(
        state=DebugRuntimeState.KEIL_DISCOVERED,
        backend=DebugBackendKind.KEIL,
        detail=detail,
        project_path=previous_status.project_path,
        target_name=previous_status.target_name,
        capabilities=default_debug_capabilities(
            DebugRuntimeState.KEIL_DISCOVERED,
            can_attach=can_attempt,
        ),
    )


def status_from_uvsock_connection(
    connection: object,
    previous: DebugWorkbenchStatus | None = None,
) -> DebugWorkbenchStatus:
    previous_status = previous or make_debug_status(state=DebugRuntimeState.DISCONNECTED)
    connected = bool(getattr(connection, "connected", False))
    error = str(getattr(connection, "error", "") or "")
    target_running = getattr(connection, "target_running", None)
    if not connected:
        message = error or "UVSOCK 调试连接失败"
        return make_debug_status(
            state=DebugRuntimeState.ERROR,
            backend=DebugBackendKind.KEIL,
            detail=message,
            project_path=previous_status.project_path,
            target_name=previous_status.target_name,
            error=message,
        )

    if target_running is True:
        state = DebugRuntimeState.RUNNING
        detail = "UVSOCK 已连接，目标运行中"
    elif target_running is False:
        state = DebugRuntimeState.PAUSED
        detail = "UVSOCK 已连接，目标已暂停"
    else:
        state = DebugRuntimeState.KEIL_ATTACHED
        detail = "UVSOCK 已连接，等待运行状态"
    return make_debug_status(
        state=state,
        backend=DebugBackendKind.KEIL,
        detail=detail,
        project_path=previous_status.project_path,
        target_name=previous_status.target_name,
        capabilities=default_debug_capabilities(
            state,
            runtime_control=True,
            breakpoint_sync=True,
            variable_write=False,
        ),
    )


def debug_state_label(state: DebugRuntimeState | str) -> str:
    labels = {
        DebugRuntimeState.DISCONNECTED: "未连接",
        DebugRuntimeState.KEIL_DISCOVERED: "已发现 Keil",
        DebugRuntimeState.KEIL_ATTACHED: "已连接 Keil",
        DebugRuntimeState.PAUSED: "目标已暂停",
        DebugRuntimeState.RUNNING: "目标运行中",
        DebugRuntimeState.ERROR: "调试异常",
    }
    return labels[_coerce_state(state)]


def debug_state_detail(state: DebugRuntimeState | str) -> str:
    details = {
        DebugRuntimeState.DISCONNECTED: "尚未连接调试后端",
        DebugRuntimeState.KEIL_DISCOVERED: "已发现 Keil，等待连接调试会话",
        DebugRuntimeState.KEIL_ATTACHED: "已连接调试会话，等待运行状态",
        DebugRuntimeState.PAUSED: "目标暂停，可查看当前位置",
        DebugRuntimeState.RUNNING: "目标正在运行",
        DebugRuntimeState.ERROR: "调试桥接出现错误",
    }
    return details[_coerce_state(state)]


def load_code_document(path: str | Path, max_bytes: int = 2_000_000) -> CodeDocument:
    source_path = Path(path).expanduser().resolve()
    size = source_path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"source file is too large: {size} bytes")
    text = source_path.read_text(encoding="utf-8-sig", errors="replace")
    raw_lines = text.splitlines()
    if text.endswith(("\n", "\r")):
        raw_lines.append("")
    lines = tuple(CodeLine(index + 1, line) for index, line in enumerate(raw_lines))
    return CodeDocument(
        path=source_path,
        language=SOURCE_LANGUAGES.get(source_path.suffix.lower(), "text"),
        lines=lines,
    )


def search_document(
    document: CodeDocument,
    query: str,
    *,
    case_sensitive: bool = False,
    max_matches: int = 500,
) -> tuple[SearchMatch, ...]:
    query = str(query)
    if not query:
        return ()
    needle = query if case_sensitive else query.lower()
    matches: list[SearchMatch] = []
    for line in document.lines:
        haystack = line.text if case_sensitive else line.text.lower()
        start = 0
        while True:
            column = haystack.find(needle, start)
            if column < 0:
                break
            matches.append(SearchMatch(line.number, column + 1, line.text[column:column + len(query)]))
            if len(matches) >= max_matches:
                return tuple(matches)
            start = column + max(1, len(needle))
    return tuple(matches)


def line_decorations(
    document: CodeDocument,
    breakpoints: BreakpointStore | None = None,
    *,
    current_pc_line: int | None = None,
    run_line: int | None = None,
    search_query: str = "",
) -> tuple[LineDecoration, ...]:
    decorations: list[LineDecoration] = []
    if breakpoints is not None:
        for breakpoint in breakpoints.for_file(document.path):
            decorations.append(
                LineDecoration(
                    line=breakpoint.line,
                    kind="breakpoint",
                    label=breakpoint.condition,
                    enabled=breakpoint.enabled,
                    verified=breakpoint.verified,
                    message=breakpoint.message,
                )
            )
    if current_pc_line is not None and 1 <= int(current_pc_line) <= document.line_count:
        decorations.append(LineDecoration(line=int(current_pc_line), kind="pc", label="PC"))
    if run_line is not None and 1 <= int(run_line) <= document.line_count:
        decorations.append(LineDecoration(line=int(run_line), kind="run", label="Run"))
    for match in search_document(document, search_query):
        decorations.append(LineDecoration(line=match.line, kind="search", label=match.text))
    return tuple(sorted(decorations, key=lambda item: (item.line, item.kind, item.label)))


def _select_target(project: KeilProject, target_name: str | None) -> KeilTarget | None:
    if not target_name:
        return project.default_target
    for target in project.targets:
        if target.name == target_name:
            return target
    return None


def _breakpoint_key(path: str | Path, line: int) -> tuple[str, int]:
    return _normalise_key_path(path), _valid_line(line)


def _normalise_key_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve()).lower()


def _valid_line(line: int) -> int:
    line = int(line)
    if line <= 0:
        raise ValueError("breakpoint line must be >= 1")
    return line


def _coerce_state(state: DebugRuntimeState | str) -> DebugRuntimeState:
    if isinstance(state, DebugRuntimeState):
        return state
    return DebugRuntimeState(str(state))


def _coerce_backend(backend: DebugBackendKind | str) -> DebugBackendKind:
    if isinstance(backend, DebugBackendKind):
        return backend
    return DebugBackendKind(str(backend))


def _optional_positive_int(value: int | None) -> int | None:
    if value is None:
        return None
    value = int(value)
    return value if value > 0 else None


def _debug_reason_text(reason: str) -> str:
    translations = {
        "Keil/uVision was not discovered": "未发现 Keil/uVision",
        "UVSOCK DLL could not be loaded": "UVSOCK DLL 加载失败",
        "uVision is not running": "uVision 未运行",
        "preflight failed": "预检失败",
    }
    return translations.get(reason, reason)


def _command_plan(
    actions: dict[str, DebugAction],
    key: str,
    title: str,
    risk: DebugPlanRisk,
    intent: str,
    *,
    requirements: tuple[str, ...],
    safety_notes: tuple[str, ...],
    preview_steps: tuple[str, ...],
    preview_only: bool = True,
) -> DebugCommandPlan:
    action = actions.get(key, DebugAction(key, title, False, "等待后端状态"))
    preconditions_met = bool(action.enabled)
    execution_enabled = bool(preconditions_met and not preview_only)
    if execution_enabled:
        status = "可执行"
        disabled_reason = ""
    elif preconditions_met:
        status = "计划就绪"
        disabled_reason = "条件满足，等待显式执行或后端控制器开放该动作"
    else:
        status = "等待条件"
        disabled_reason = action.reason or "当前状态不可用"
    return DebugCommandPlan(
        key=key,
        title=title,
        intent=intent,
        status=status,
        risk=risk,
        preconditions_met=preconditions_met,
        execution_enabled=execution_enabled,
        disabled_reason=disabled_reason,
        requirements=requirements,
        safety_notes=safety_notes,
        preview_steps=preview_steps,
    )


def _disabled_reason(enabled: bool, status: DebugWorkbenchStatus) -> str:
    if enabled:
        return ""
    if status.error:
        return status.error
    if status.state == DebugRuntimeState.DISCONNECTED:
        return "尚未连接调试后端"
    if status.state == DebugRuntimeState.KEIL_DISCOVERED:
        return "等待建立调试连接"
    if status.state == DebugRuntimeState.KEIL_ATTACHED:
        return "后端尚未声明该能力"
    return "当前状态不可用"
