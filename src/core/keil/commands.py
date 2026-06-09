"""Dry-run Keil debug command transactions.

The objects in this module are intentionally data-only. They do not carry a
UVSOCK DLL handle, subprocess command, callback, or any callable execution path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


class KeilCommandKind(str, Enum):
    DISCOVER = "discover"
    ATTACH = "attach"
    DISCONNECT = "disconnect"
    HALT = "halt"
    RUN = "run"
    STEP = "step"
    SYNC_BREAKPOINTS = "sync_breakpoints"
    WRITE_VARIABLES = "write_variables"


class KeilCommandGuardState(str, Enum):
    PASS = "pass"
    WAIT = "wait"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class KeilCommandGuard:
    key: str
    label: str
    state: KeilCommandGuardState
    detail: str = ""


@dataclass(frozen=True)
class KeilBreakpointIntent:
    path: Path
    line: int
    enabled: bool = True
    condition: str = ""


@dataclass(frozen=True)
class KeilVariableWriteIntent:
    symbol: str
    value_text: str
    type_name: str = ""
    address: int | None = None
    ram_checked: bool = False


@dataclass(frozen=True)
class KeilCommandTransaction:
    schema_version: int
    transaction_id: str
    kind: KeilCommandKind
    title: str
    intent: str
    risk: str
    project_path: Path | None
    target_name: str
    port: int | None
    dry_run: bool
    preconditions_met: bool
    execution_enabled: bool
    command_preview: tuple[str, ...]
    expected_effect: str
    guards: tuple[KeilCommandGuard, ...]
    audit_summary: str

    @property
    def action_key(self) -> str:
        return self.kind.value

    @property
    def ready(self) -> bool:
        return bool(
            self.preconditions_met
            and not self.dry_run
            and self.execution_enabled
            and all(guard.state == KeilCommandGuardState.PASS for guard in self.guards)
        )

    @property
    def blocked_reasons(self) -> tuple[str, ...]:
        return tuple(
            guard.detail or guard.label
            for guard in self.guards
            if guard.state in {KeilCommandGuardState.WAIT, KeilCommandGuardState.BLOCKED}
        )

    def audit_record(self, event: str = "planned") -> dict[str, Any]:
        return {
            "event": str(event),
            "schema_version": self.schema_version,
            "transaction_id": self.transaction_id,
            "kind": self.kind.value,
            "title": self.title,
            "intent": self.intent,
            "risk": self.risk,
            "project_path": str(self.project_path) if self.project_path else "",
            "target_name": self.target_name,
            "port": self.port,
            "dry_run": self.dry_run,
            "preconditions_met": self.preconditions_met,
            "execution_enabled": self.execution_enabled,
            "ready": self.ready,
            "command_preview": list(self.command_preview),
            "expected_effect": self.expected_effect,
            "blocked_reasons": list(self.blocked_reasons),
            "guards": [
                {
                    "key": guard.key,
                    "label": guard.label,
                    "state": guard.state.value,
                    "detail": guard.detail,
                }
                for guard in self.guards
            ],
            "audit_summary": self.audit_summary,
        }


def build_keil_debug_transactions(
    status: object,
    plans: Iterable[object],
    *,
    port: int | None,
    project_path: str | Path | None = None,
    target_name: str = "",
    breakpoints: Iterable[object] = (),
    variable_writes: Iterable[KeilVariableWriteIntent | object] = (),
    execution_gate: bool = False,
) -> tuple[KeilCommandTransaction, ...]:
    project = _project_path(project_path if project_path is not None else getattr(status, "project_path", None))
    target = str(target_name or getattr(status, "target_name", "") or "")
    breakpoint_intents = tuple(_breakpoint_intent(item) for item in breakpoints)
    write_intents = tuple(_variable_write_intent(item) for item in variable_writes)
    return tuple(
        _build_transaction(
            plan,
            port=port,
            project_path=project,
            target_name=target,
            breakpoints=breakpoint_intents,
            variable_writes=write_intents,
            execution_gate=execution_gate,
        )
        for plan in plans
    )


def append_keil_audit_log(path: str | Path, transactions: Iterable[KeilCommandTransaction], *, event: str = "planned") -> Path:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        for transaction in transactions:
            handle.write(json.dumps(transaction.audit_record(event=event), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return output_path


def transaction_by_key(
    transactions: Iterable[KeilCommandTransaction],
    key: str | KeilCommandKind,
) -> KeilCommandTransaction | None:
    wanted = key.value if isinstance(key, KeilCommandKind) else str(key)
    for transaction in transactions:
        if transaction.kind.value == wanted:
            return transaction
    return None


def _build_transaction(
    plan: object,
    *,
    port: int | None,
    project_path: Path | None,
    target_name: str,
    breakpoints: tuple[KeilBreakpointIntent, ...],
    variable_writes: tuple[KeilVariableWriteIntent, ...],
    execution_gate: bool,
) -> KeilCommandTransaction:
    kind = KeilCommandKind(str(getattr(plan, "key", "")))
    preconditions_met = bool(getattr(plan, "preconditions_met", False))
    guards = _guards_for(kind, plan, port, project_path, target_name, breakpoints, variable_writes, execution_gate)
    execution_enabled = False
    dry_run = True
    command_preview = _command_preview(kind, port, target_name, breakpoints, variable_writes)
    payload = {
        "kind": kind.value,
        "title": str(getattr(plan, "title", kind.value)),
        "project_path": str(project_path) if project_path else "",
        "target_name": target_name,
        "port": port,
        "commands": command_preview,
        "guards": [(guard.key, guard.state.value, guard.detail) for guard in guards],
    }
    return KeilCommandTransaction(
        schema_version=1,
        transaction_id=_stable_id(payload),
        kind=kind,
        title=str(getattr(plan, "title", kind.value)),
        intent=str(getattr(plan, "intent", "")),
        risk=str(getattr(getattr(plan, "risk", ""), "value", getattr(plan, "risk", ""))),
        project_path=project_path,
        target_name=target_name,
        port=int(port) if port is not None else None,
        dry_run=dry_run,
        preconditions_met=preconditions_met,
        execution_enabled=execution_enabled,
        command_preview=command_preview,
        expected_effect=_expected_effect(kind),
        guards=guards,
        audit_summary=_audit_summary(kind, command_preview, dry_run),
    )


def _guards_for(
    kind: KeilCommandKind,
    plan: object,
    port: int | None,
    project_path: Path | None,
    target_name: str,
    breakpoints: tuple[KeilBreakpointIntent, ...],
    variable_writes: tuple[KeilVariableWriteIntent, ...],
    execution_gate: bool,
) -> tuple[KeilCommandGuard, ...]:
    guards = [
        _guard(
            "plan_preconditions",
            "动作条件",
            KeilCommandGuardState.PASS if bool(getattr(plan, "preconditions_met", False)) else KeilCommandGuardState.WAIT,
            "" if bool(getattr(plan, "preconditions_met", False)) else str(getattr(plan, "disabled_reason", "") or "等待调试状态"),
        ),
        _guard(
            "execution_gate",
            "执行门",
            KeilCommandGuardState.PASS if execution_gate else KeilCommandGuardState.WAIT,
            "" if execution_gate else "当前阶段只允许干跑预览，不发送 UVSOCK 命令",
        ),
        _guard(
            "data_only",
            "数据对象",
            KeilCommandGuardState.PASS,
            "交易不携带 DLL handle、subprocess、callable 或线程对象",
        ),
        _guard(
            "no_launch",
            "不启动 Keil",
            KeilCommandGuardState.PASS,
            "Stage 20 不生成 uVision 启动进程，只描述未来 UVSOCK 调试意图",
        ),
    ]
    if kind in {KeilCommandKind.ATTACH, KeilCommandKind.DISCOVER}:
        guards.append(_port_guard(port))
        guards.append(_project_guard(project_path))
        guards.append(_target_guard(target_name))
    if kind in {KeilCommandKind.HALT, KeilCommandKind.RUN, KeilCommandKind.STEP, KeilCommandKind.DISCONNECT}:
        guards.append(
            _guard(
                "attached_session",
                "已连接会话",
                KeilCommandGuardState.PASS if bool(getattr(plan, "preconditions_met", False)) else KeilCommandGuardState.WAIT,
                "" if bool(getattr(plan, "preconditions_met", False)) else "等待 UVSOCK 连接状态",
            )
        )
    if kind == KeilCommandKind.SYNC_BREAKPOINTS:
        guards.extend(_breakpoint_guards(breakpoints))
    if kind == KeilCommandKind.WRITE_VARIABLES:
        guards.extend(_write_guards(variable_writes))
    return tuple(guards)


def _command_preview(
    kind: KeilCommandKind,
    port: int | None,
    target_name: str,
    breakpoints: tuple[KeilBreakpointIntent, ...],
    variable_writes: tuple[KeilVariableWriteIntent, ...],
) -> tuple[str, ...]:
    if kind == KeilCommandKind.DISCOVER:
        return ("discover_keil()", "check_uvsock_preflight(require_running=False)")
    if kind == KeilCommandKind.ATTACH:
        return (
            "UVSC_Init(1, 65535)",
            f'UVSC_OpenConnection(name="LoopMaster", port={port if port is not None else "--"})',
            f"select_target({target_name or '--'})",
            "UVSC_DBG_STATUS(handle)",
        )
    if kind == KeilCommandKind.DISCONNECT:
        return ("UVSC_CloseConnection(handle, 0)", "UVSC_UnInit()")
    if kind == KeilCommandKind.HALT:
        return ("UVSC_DBG_STOP_EXECUTION(handle)", "UVSC_DBG_STATUS(handle)", "read_pc_location()")
    if kind == KeilCommandKind.RUN:
        return ("UVSC_DBG_START_EXECUTION(handle)", "UVSC_DBG_STATUS(handle)")
    if kind == KeilCommandKind.STEP:
        return ('UVSC_DBG_EXEC_CMD(handle, "<single-step debug command>")', "read_pc_location()")
    if kind == KeilCommandKind.SYNC_BREAKPOINTS:
        if not breakpoints:
            return ("diff_local_breakpoints(count=0)", "no Keil breakpoint command will be emitted")
        commands = [f"diff_local_breakpoints(count={len(breakpoints)})"]
        for item in breakpoints[:8]:
            state = "enable" if item.enabled else "disable"
            condition = f", condition={item.condition!r}" if item.condition else ""
            commands.append(f"UVSC_DBG_EXEC_CMD(handle, \"breakpoint {state} {item.path}:{item.line}{condition}\")")
        if len(breakpoints) > 8:
            commands.append(f"... {len(breakpoints) - 8} more breakpoint intents")
        return tuple(commands)
    if kind == KeilCommandKind.WRITE_VARIABLES:
        if not variable_writes:
            return ("validate_variable_write_batch(count=0)", "no variable write command will be emitted")
        commands = [f"validate_variable_write_batch(count={len(variable_writes)})"]
        for item in variable_writes[:8]:
            target = item.symbol or (f"0x{item.address:08X}" if item.address is not None else "<unknown>")
            type_name = f":{item.type_name}" if item.type_name else ""
            commands.append(f'UVSC_DBG_VARIABLE_SET(handle, "{target}{type_name}", {item.value_text!r})')
            commands.append(f'UVSC_DBG_EVAL_EXPRESSION_TO_STR(handle, "{target}")')
        if len(variable_writes) > 8:
            commands.append(f"... {len(variable_writes) - 8} more variable write intents")
        return tuple(commands)
    return (f"{kind.value}()",)


def _breakpoint_guards(breakpoints: tuple[KeilBreakpointIntent, ...]) -> tuple[KeilCommandGuard, ...]:
    if not breakpoints:
        return (
            _guard("breakpoint_batch", "断点批次", KeilCommandGuardState.WAIT, "当前没有本地断点可同步"),
        )
    invalid = [item for item in breakpoints if item.line <= 0 or not str(item.path)]
    return (
        _guard("breakpoint_batch", "断点批次", KeilCommandGuardState.PASS, f"{len(breakpoints)} 个本地断点待 dry-run"),
        _guard(
            "breakpoint_locations",
            "断点位置",
            KeilCommandGuardState.BLOCKED if invalid else KeilCommandGuardState.PASS,
            f"{len(invalid)} 个断点行号或路径无效" if invalid else "路径/行号具备基础格式",
        ),
    )


def _write_guards(variable_writes: tuple[KeilVariableWriteIntent, ...]) -> tuple[KeilCommandGuard, ...]:
    if not variable_writes:
        return (
            _guard("write_batch", "写入批次", KeilCommandGuardState.WAIT, "当前没有变量写入请求"),
            _guard("ram_whitelist", "RAM 白名单", KeilCommandGuardState.WAIT, "等待符号解析后确认 RAM 范围"),
            _guard("readback", "写后回读", KeilCommandGuardState.WAIT, "等待接入回读校验"),
        )
    missing_target = [item for item in variable_writes if not item.symbol and item.address is None]
    unchecked_ram = [item for item in variable_writes if not item.ram_checked]
    missing_type = [item for item in variable_writes if not item.type_name]
    return (
        _guard(
            "write_batch",
            "写入批次",
            KeilCommandGuardState.BLOCKED if missing_target else KeilCommandGuardState.PASS,
            f"{len(missing_target)} 项缺少符号或地址" if missing_target else f"{len(variable_writes)} 项写入请求待 dry-run",
        ),
        _guard(
            "type_check",
            "类型校验",
            KeilCommandGuardState.WAIT if missing_type else KeilCommandGuardState.PASS,
            f"{len(missing_type)} 项等待类型解析" if missing_type else "类型字段已具备基础信息",
        ),
        _guard(
            "ram_whitelist",
            "RAM 白名单",
            KeilCommandGuardState.WAIT if unchecked_ram else KeilCommandGuardState.PASS,
            f"{len(unchecked_ram)} 项等待 RAM 范围确认" if unchecked_ram else "写入目标已声明 RAM 检查通过",
        ),
        _guard("range_check", "数值范围", KeilCommandGuardState.WAIT, "等待类型范围和用户护栏"),
        _guard("readback", "写后回读", KeilCommandGuardState.WAIT, "必须回读确认后才可标记成功"),
    )


def _expected_effect(kind: KeilCommandKind) -> str:
    effects = {
        KeilCommandKind.DISCOVER: "刷新 Keil/UVSOCK 预检状态",
        KeilCommandKind.ATTACH: "建立 LoopMaster 到 uVision 的 UVSOCK 调试桥接",
        KeilCommandKind.DISCONNECT: "关闭桥接连接但不关闭 Keil 或复位目标",
        KeilCommandKind.HALT: "让目标暂停并刷新 PC/source marker",
        KeilCommandKind.RUN: "让目标继续运行并刷新运行状态",
        KeilCommandKind.STEP: "执行一次单步并刷新 PC/source marker",
        KeilCommandKind.SYNC_BREAKPOINTS: "将本地断点差异同步到 Keil 并回读验证",
        KeilCommandKind.WRITE_VARIABLES: "写入变量后立即回读验证并记录审计",
    }
    return effects[kind]


def _audit_summary(kind: KeilCommandKind, command_preview: tuple[str, ...], dry_run: bool) -> str:
    prefix = "dry-run" if dry_run else "execution"
    first = command_preview[0] if command_preview else kind.value
    return f"{prefix}:{kind.value}:{first}"


def _port_guard(port: int | None) -> KeilCommandGuard:
    if port is None:
        return _guard("uvsock_port", "UVSOCK 端口", KeilCommandGuardState.WAIT, "等待指定 UVSOCK 端口")
    try:
        value = int(port)
    except (TypeError, ValueError):
        return _guard("uvsock_port", "UVSOCK 端口", KeilCommandGuardState.BLOCKED, "端口不是整数")
    if not (1 <= value <= 65535):
        return _guard("uvsock_port", "UVSOCK 端口", KeilCommandGuardState.BLOCKED, "端口必须在 1..65535 范围内")
    return _guard("uvsock_port", "UVSOCK 端口", KeilCommandGuardState.PASS, str(value))


def _project_guard(project_path: Path | None) -> KeilCommandGuard:
    if project_path is None:
        return _guard("project", "Keil 工程", KeilCommandGuardState.WAIT, "等待打开 Keil 工程")
    return _guard("project", "Keil 工程", KeilCommandGuardState.PASS, str(project_path))


def _target_guard(target_name: str) -> KeilCommandGuard:
    if not target_name:
        return _guard("target", "Keil Target", KeilCommandGuardState.WAIT, "等待选择 Target")
    return _guard("target", "Keil Target", KeilCommandGuardState.PASS, target_name)


def _guard(key: str, label: str, state: KeilCommandGuardState, detail: str = "") -> KeilCommandGuard:
    return KeilCommandGuard(key=key, label=label, state=state, detail=detail)


def _breakpoint_intent(item: object) -> KeilBreakpointIntent:
    return KeilBreakpointIntent(
        path=Path(getattr(item, "path", "")),
        line=int(getattr(item, "line", 0) or 0),
        enabled=bool(getattr(item, "enabled", True)),
        condition=str(getattr(item, "condition", "") or ""),
    )


def _variable_write_intent(item: KeilVariableWriteIntent | object) -> KeilVariableWriteIntent:
    if isinstance(item, KeilVariableWriteIntent):
        return item
    return KeilVariableWriteIntent(
        symbol=str(getattr(item, "symbol", "") or ""),
        value_text=str(getattr(item, "value_text", getattr(item, "value", "")) or ""),
        type_name=str(getattr(item, "type_name", "") or ""),
        address=getattr(item, "address", None),
        ram_checked=bool(getattr(item, "ram_checked", False)),
    )


def _project_path(value: str | Path | None) -> Path | None:
    if value is None or str(value) == "":
        return None
    return Path(value).expanduser().resolve()


def _stable_id(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"keil-{digest[:16]}"
