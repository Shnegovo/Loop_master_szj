"""Guarded Keil breakpoint sync execution helpers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Protocol

from src.core.keil.commands import (
    KeilBreakpointRemoteSnapshot,
    KeilBreakpointSyncAction,
    KeilBreakpointSyncOperation,
    KeilCommandTransaction,
    diff_keil_breakpoints,
)
from src.core.debug_snapshots import RemoteBreakpoint
from src.core.keil.source_line_address import resolve_address_source_line, resolve_source_line_address


@dataclass(frozen=True)
class KeilBreakpointSyncRequest:
    project_path: Path | None
    target_name: str = ""
    operations: tuple[KeilBreakpointSyncOperation, ...] = ()
    transaction_id: str = ""
    connection_name: str = "LoopMasterBreakpointSync"
    verify_after: bool = True
    remote_snapshot_complete: bool = True
    axf_path: Path | None = None


@dataclass(frozen=True)
class KeilBreakpointCommandResult:
    operation: KeilBreakpointSyncOperation
    command: str
    attempted: bool
    succeeded: bool
    status_code: int | None = None
    output: str = ""
    error: str = ""


@dataclass(frozen=True)
class KeilBreakpointCommandPlanItem:
    operation: KeilBreakpointSyncOperation
    command: str
    executable: bool
    will_change_target: bool
    reason: str = ""
    order: int = 0

    @property
    def status_label(self) -> str:
        if self.executable:
            return "将发送"
        if self.operation.action == KeilBreakpointSyncAction.NOOP:
            return "无变化"
        return "受限"

    def to_record(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "action": self.operation.action.value,
            "path": str(self.operation.path),
            "line": self.operation.line,
            "remote_id": self.operation.remote_id,
            "command": self.command,
            "address": f"0x{self.operation.address:08X}" if self.operation.address is not None else "",
            "address_source": self.operation.address_source,
            "address_exact": self.operation.address_exact,
            "executable": self.executable,
            "will_change_target": self.will_change_target,
            "status": self.status_label,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class KeilBreakpointSyncResult:
    request: KeilBreakpointSyncRequest
    commands: tuple[KeilBreakpointCommandResult, ...]
    remote_snapshot: KeilBreakpointRemoteSnapshot | None = None
    error: str = ""

    @property
    def attempted_count(self) -> int:
        return sum(1 for item in self.commands if item.attempted)

    @property
    def succeeded_count(self) -> int:
        return sum(1 for item in self.commands if item.attempted and item.succeeded)

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.commands if item.attempted and not item.succeeded)

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.commands if not item.attempted and not item.succeeded)

    @property
    def noop_count(self) -> int:
        return sum(1 for item in self.commands if not item.attempted and item.succeeded)

    @property
    def succeeded(self) -> bool:
        return not self.error and self.failed_count == 0 and self.skipped_count == 0

    @property
    def completed(self) -> bool:
        return not self.error and self.failed_count == 0 and not self.blocked_by_limits

    @property
    def partial(self) -> bool:
        return not self.error and self.failed_count == 0 and self.skipped_count > 0 and self.attempted_count > 0

    @property
    def blocked_by_limits(self) -> bool:
        return not self.error and self.failed_count == 0 and self.skipped_count > 0 and self.attempted_count == 0

    @property
    def limited_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        for item in self.commands:
            if item.attempted or item.succeeded:
                continue
            reason = item.error or item.operation.reason or "断点操作受限，未发送"
            if reason not in reasons:
                reasons.append(reason)
        return tuple(reasons)

    @property
    def status_label(self) -> str:
        if self.succeeded:
            return "成功"
        if self.partial:
            return "部分完成"
        if self.blocked_by_limits:
            return "受限未执行"
        return "失败"

    def summary(self) -> str:
        if self.succeeded:
            return f"Keil 断点同步完成：{self.succeeded_count}/{self.attempted_count} 条命令成功"
        if self.partial:
            reason = _join_limited_reasons(self.limited_reasons)
            suffix = f"，原因：{reason}" if reason else ""
            return f"Keil 断点同步部分完成：{self.succeeded_count}/{self.attempted_count} 条命令成功，{self.skipped_count} 条受限未发送{suffix}"
        if self.blocked_by_limits:
            reason = _join_limited_reasons(self.limited_reasons)
            suffix = f"，原因：{reason}" if reason else ""
            return f"Keil 断点同步未执行：{self.skipped_count} 条操作受限未发送{suffix}"
        detail = self.error or _first_error(self.commands) or "断点同步失败"
        return f"Keil 断点同步失败：{detail}"

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        rows = [
            ("断点同步", self.status_label),
            ("断点命令", f"{self.succeeded_count}/{self.attempted_count} 成功"),
            ("断点同步模式", "完整差分" if self.request.remote_snapshot_complete else "推送本地"),
        ]
        if self.noop_count:
            rows.append(("断点无变化", str(self.noop_count)))
        if self.skipped_count:
            rows.append(("断点受限", f"{self.skipped_count} 未发送"))
            reason = _join_limited_reasons(self.limited_reasons)
            if reason:
                rows.append(("断点受限原因", reason))
        plan = plan_keil_breakpoint_commands(self.request)
        if plan:
            executable_count = sum(1 for item in plan if item.executable)
            rows.append(("断点命令计划", f"{executable_count}/{len(plan)} 可发送"))
        command_samples = _command_samples(self.commands)
        if command_samples:
            rows.append(("断点命令样例", "；".join(command_samples)))
        address_resolved = sum(1 for item in self.commands if item.operation.address is not None)
        address_unresolved = sum(
            1 for item in self.commands
            if item.attempted and item.operation.action in {KeilBreakpointSyncAction.ADD, KeilBreakpointSyncAction.UPDATE_CONDITION}
            and item.operation.address is None
        )
        if self.request.axf_path is not None:
            address_samples = _address_samples(self.commands)
            rows.extend(
                [
                    ("断点 AXF", str(self.request.axf_path)),
                    ("断点地址解析", f"{address_resolved} 已解析 / {address_unresolved} 未解析"),
                    ("断点地址样例", "；".join(address_samples) if address_samples else "--"),
                ]
            )
        if self.remote_snapshot is not None:
            rows.extend(
                [
                    ("断点快照", self.remote_snapshot.snapshot_id),
                    ("断点快照完整", "是" if self.remote_snapshot.complete else "否"),
                    ("远端断点数", str(len(self.remote_snapshot.breakpoints))),
                ]
            )
        if self.error:
            rows.append(("断点同步错误", self.error))
        return tuple(rows)

    def to_record(self) -> dict[str, Any]:
        return {
            "transaction_id": self.request.transaction_id,
            "project_path": str(self.request.project_path or ""),
            "target_name": self.request.target_name,
            "remote_snapshot_complete": self.request.remote_snapshot_complete,
            "axf_path": str(self.request.axf_path or ""),
            "succeeded": self.succeeded,
            "completed": self.completed,
            "partial": self.partial,
            "blocked_by_limits": self.blocked_by_limits,
            "status": self.status_label,
            "attempted_count": self.attempted_count,
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "noop_count": self.noop_count,
            "limited_reasons": list(self.limited_reasons),
            "commands": [
                {
                    "action": item.operation.action.value,
                    "path": str(item.operation.path),
                    "line": item.operation.line,
                    "remote_id": item.operation.remote_id,
                    "command": item.command,
                    "address": f"0x{item.operation.address:08X}" if item.operation.address is not None else "",
                    "address_source": item.operation.address_source,
                    "address_exact": item.operation.address_exact,
                    "attempted": item.attempted,
                    "succeeded": item.succeeded,
                    "status_code": item.status_code,
                    "output": item.output,
                    "error": item.error,
                }
                for item in self.commands
            ],
            "command_plan": [item.to_record() for item in plan_keil_breakpoint_commands(self.request)],
            "remote_snapshot": _snapshot_record(self.remote_snapshot),
            "error": self.error,
        }


@dataclass(frozen=True)
class KeilRemoteBreakpointSourceMapResult:
    breakpoints: tuple[RemoteBreakpoint, ...]
    mapped_count: int
    unresolved_count: int
    axf_path: Path | None = None

    @property
    def complete(self) -> bool:
        return self.unresolved_count == 0

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        return (
            ("远端断点源码映射", f"{self.mapped_count} 已映射 / {self.unresolved_count} 未映射"),
            ("远端断点映射 AXF", str(self.axf_path or "--")),
        )


class KeilBreakpointCommandSession(Protocol):
    def execute_command(self, command: str, *, echo: bool = False) -> Any:
        ...


def build_keil_breakpoint_sync_request(
    transaction: KeilCommandTransaction,
    *,
    connection_name: str = "LoopMasterBreakpointSync",
) -> KeilBreakpointSyncRequest:
    summary = transaction.breakpoint_diff_summary
    if transaction.kind.value != "sync_breakpoints":
        raise ValueError("transaction is not a breakpoint sync transaction")
    if summary is None:
        raise ValueError("transaction has no breakpoint diff summary")
    operations = tuple(_active_operations_from_transaction(transaction))
    return KeilBreakpointSyncRequest(
        project_path=transaction.project_path,
        target_name=transaction.target_name,
        operations=operations,
        transaction_id=transaction.transaction_id,
        connection_name=connection_name,
    )


def build_keil_breakpoint_sync_request_from_state(
    *,
    project_path: str | Path | None,
    target_name: str,
    local_breakpoints: Iterable[object],
    remote_breakpoints: Iterable[object] = (),
    source_paths: Iterable[str | Path] = (),
    transaction_id: str = "",
    connection_name: str = "LoopMasterBreakpointSync",
    remote_snapshot_complete: bool = True,
    axf_path: str | Path | None = None,
) -> KeilBreakpointSyncRequest:
    operations = diff_keil_breakpoints(
        local_breakpoints,
        remote_breakpoints,
        source_paths=source_paths,
    )
    source_path_tuple = tuple(source_paths)
    axf = Path(axf_path).expanduser().resolve() if axf_path else None
    operations = _with_source_line_addresses(operations, axf, source_path_tuple)
    return KeilBreakpointSyncRequest(
        project_path=Path(project_path).expanduser().resolve() if project_path else None,
        target_name=target_name,
        operations=tuple(operations),
        transaction_id=transaction_id,
        connection_name=connection_name,
        remote_snapshot_complete=bool(remote_snapshot_complete),
        axf_path=axf,
    )


def execute_keil_breakpoint_sync(
    session: KeilBreakpointCommandSession,
    request: KeilBreakpointSyncRequest,
    *,
    remote_snapshot: KeilBreakpointRemoteSnapshot | None = None,
) -> KeilBreakpointSyncResult:
    results: list[KeilBreakpointCommandResult] = []
    for plan_item in plan_keil_breakpoint_commands(request):
        operation = plan_item.operation
        command = plan_item.command
        if not plan_item.executable and operation.action != KeilBreakpointSyncAction.NOOP:
            results.append(
                KeilBreakpointCommandResult(
                    operation=operation,
                    command=command,
                    attempted=False,
                    succeeded=False,
                    error=plan_item.reason or operation.reason or "invalid breakpoint operation",
                )
            )
            continue
        if operation.action == KeilBreakpointSyncAction.NOOP:
            results.append(
                KeilBreakpointCommandResult(
                    operation=operation,
                    command=command,
                    attempted=False,
                    succeeded=True,
                    output="noop",
                )
            )
            continue
        try:
            output = session.execute_command(command, echo=False)
        except Exception as exc:
            results.append(
                KeilBreakpointCommandResult(
                    operation=operation,
                    command=command,
                    attempted=True,
                    succeeded=False,
                    error=str(exc),
                )
            )
            continue
        results.append(
            KeilBreakpointCommandResult(
                operation=operation,
                command=command,
                attempted=True,
                succeeded=True,
                output="" if output is None else str(output),
            )
        )
    snapshot = remote_snapshot if request.verify_after else None
    return KeilBreakpointSyncResult(request=request, commands=tuple(results), remote_snapshot=snapshot)


def map_remote_breakpoint_sources_from_axf(
    remote_breakpoints: Iterable[RemoteBreakpoint | object],
    axf_path: str | Path | None,
    *,
    source_roots: Iterable[str | Path] = (),
) -> KeilRemoteBreakpointSourceMapResult:
    axf = Path(axf_path).expanduser().resolve() if axf_path else None
    if axf is None or not axf.exists():
        items = tuple(RemoteBreakpoint.from_record(item) for item in remote_breakpoints)
        unresolved = sum(1 for item in items if item.path is None or item.line <= 0)
        return KeilRemoteBreakpointSourceMapResult(
            breakpoints=items,
            mapped_count=0,
            unresolved_count=unresolved,
            axf_path=axf,
        )
    mapped: list[RemoteBreakpoint] = []
    mapped_count = 0
    unresolved_count = 0
    for raw_item in remote_breakpoints:
        item = RemoteBreakpoint.from_record(raw_item)
        if item.path is not None and item.line > 0:
            mapped.append(item)
            continue
        if item.address is None:
            unresolved_count += 1
            mapped.append(item)
            continue
        result = resolve_address_source_line(
            axf,
            item.address,
            source_roots=source_roots,
            allow_nearest=False,
        )
        if not result.resolved:
            unresolved_count += 1
            mapped.append(item)
            continue
        mapped_count += 1
        mapped.append(
            replace(
                item,
                path=result.path,
                line=result.line,
                raw_location=f"{result.path}:{result.line} @ 0x{int(item.address):08X}",
                verified=True,
                message="Keil 地址断点已映射到源码行",
            )
        )
    return KeilRemoteBreakpointSourceMapResult(
        breakpoints=tuple(mapped),
        mapped_count=mapped_count,
        unresolved_count=unresolved_count,
        axf_path=axf,
    )


def build_keil_breakpoint_audit_record(
    transaction: KeilCommandTransaction,
    result: KeilBreakpointSyncResult,
) -> dict[str, Any]:
    record = transaction.audit_record(event="executed" if result.succeeded else "failed")
    record["breakpoint_sync_result"] = result.to_record()
    return record


def keil_breakpoint_command(operation: KeilBreakpointSyncOperation) -> str:
    location = _location(operation)
    if operation.action == KeilBreakpointSyncAction.ADD:
        return f"BS {location}"
    if operation.action == KeilBreakpointSyncAction.REMOVE:
        return f"BK {operation.remote_id}"
    if operation.action == KeilBreakpointSyncAction.ENABLE:
        return f"BE {operation.remote_id}"
    if operation.action == KeilBreakpointSyncAction.DISABLE:
        return f"BD {operation.remote_id}"
    if operation.action == KeilBreakpointSyncAction.UPDATE_CONDITION:
        return f"BS {location}"
    return f"# noop {location}"


def plan_keil_breakpoint_commands(
    request: KeilBreakpointSyncRequest,
) -> tuple[KeilBreakpointCommandPlanItem, ...]:
    plan: list[KeilBreakpointCommandPlanItem] = []
    for index, operation in enumerate(_ordered_operations(request.operations), start=1):
        executable = operation.valid and operation.action != KeilBreakpointSyncAction.NOOP
        will_change = executable and operation.action in {
            KeilBreakpointSyncAction.ADD,
            KeilBreakpointSyncAction.REMOVE,
            KeilBreakpointSyncAction.ENABLE,
            KeilBreakpointSyncAction.DISABLE,
            KeilBreakpointSyncAction.UPDATE_CONDITION,
        }
        reason = ""
        if operation.action == KeilBreakpointSyncAction.NOOP:
            reason = "本地和远端断点一致"
        elif not operation.valid:
            reason = operation.reason or "断点操作受限，未发送"
        plan.append(
            KeilBreakpointCommandPlanItem(
                operation=operation,
                command=keil_breakpoint_command(operation),
                executable=executable,
                will_change_target=will_change,
                reason=reason,
                order=index,
            )
        )
    return tuple(plan)


def _ordered_operations(
    operations: tuple[KeilBreakpointSyncOperation, ...],
) -> tuple[KeilBreakpointSyncOperation, ...]:
    def sort_key(operation: KeilBreakpointSyncOperation) -> tuple[int, int, str, int]:
        if operation.action == KeilBreakpointSyncAction.NOOP or not operation.valid:
            group = 3
        elif operation.action in {KeilBreakpointSyncAction.ENABLE, KeilBreakpointSyncAction.DISABLE}:
            group = 0
        elif operation.action in {KeilBreakpointSyncAction.ADD, KeilBreakpointSyncAction.UPDATE_CONDITION}:
            group = 1
        elif operation.action == KeilBreakpointSyncAction.REMOVE:
            group = 2
        else:
            group = 1
        remote_number = _remote_id_number(operation.remote_id)
        if operation.action == KeilBreakpointSyncAction.REMOVE:
            remote_number = -remote_number
        return group, remote_number, str(operation.path).lower(), int(operation.line or 0)

    return tuple(sorted(operations, key=sort_key))


def _remote_id_number(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(text, 0)
    except ValueError:
        return 0


def remote_snapshot_from_operations(
    request: KeilBreakpointSyncRequest,
    *,
    complete: bool,
    error: str = "",
) -> KeilBreakpointRemoteSnapshot:
    breakpoints = []
    for operation in request.operations:
        if not operation.valid:
            continue
        if operation.action == KeilBreakpointSyncAction.REMOVE:
            continue
        if operation.action in {
            KeilBreakpointSyncAction.ADD,
            KeilBreakpointSyncAction.ENABLE,
            KeilBreakpointSyncAction.DISABLE,
            KeilBreakpointSyncAction.UPDATE_CONDITION,
            KeilBreakpointSyncAction.NOOP,
        }:
            enabled = operation.local_enabled
            if enabled is None:
                enabled = operation.remote_enabled
            breakpoints.append(
                RemoteBreakpoint(
                    path=operation.path,
                    line=operation.line,
                    address=operation.address,
                    enabled=True if enabled is None else bool(enabled),
                    condition=operation.local_condition or operation.remote_condition,
                    remote_id=operation.remote_id,
                    raw_location=(
                        f"0x{operation.address:08X}"
                        if operation.address is not None
                        else f"{operation.path}:{operation.line}"
                    ),
                )
            )
    return KeilBreakpointRemoteSnapshot(
        schema_version=1,
        snapshot_id=f"keil-sync-{abs(hash(tuple((str(bp.path), bp.line, bp.address, bp.enabled, bp.condition) for bp in breakpoints))):x}",
        project_path=request.project_path,
        target_name=request.target_name,
        captured_at="",
        complete=complete,
        breakpoints=tuple(breakpoints),
        error=error,
    )


def _active_operations_from_transaction(transaction: KeilCommandTransaction) -> Iterable[KeilBreakpointSyncOperation]:
    backend = transaction.backend_snapshot or {}
    operations = backend.get("breakpoint_ops")
    if isinstance(operations, (list, tuple)):
        for item in operations:
            if isinstance(item, KeilBreakpointSyncOperation):
                yield item
    # Current transactions do not serialize operations, so callers should pass
    # operations directly through backend_snapshot in the next UI/backend layer.


def _location(operation: KeilBreakpointSyncOperation) -> str:
    if operation.address is not None:
        return f"0x{int(operation.address):08X}"
    return f"\\{operation.path}\\{operation.line}"


def _with_source_line_addresses(
    operations: tuple[KeilBreakpointSyncOperation, ...],
    axf_path: Path | None,
    source_paths: tuple[str | Path, ...],
) -> tuple[KeilBreakpointSyncOperation, ...]:
    if axf_path is None or not axf_path.exists():
        return operations
    resolved: list[KeilBreakpointSyncOperation] = []
    for operation in operations:
        if operation.action not in {KeilBreakpointSyncAction.ADD, KeilBreakpointSyncAction.UPDATE_CONDITION}:
            resolved.append(operation)
            continue
        if not operation.valid:
            resolved.append(operation)
            continue
        result = resolve_source_line_address(
            axf_path,
            operation.path,
            operation.line,
            source_roots=source_paths,
        )
        if result.address is None:
            resolved.append(
                replace(
                    operation,
                    address=None,
                    address_source=f"unresolved:{result.error}",
                    address_exact=False,
                )
            )
            continue
        resolved.append(
            replace(
                operation,
                address=int(result.address),
                address_source=result.resolved_from or "readelf_debug_line",
                address_exact=bool(result.exact),
            )
        )
    return tuple(resolved)


def _snapshot_record(snapshot: KeilBreakpointRemoteSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "snapshot_id": snapshot.snapshot_id,
        "project_path": str(snapshot.project_path or ""),
        "target_name": snapshot.target_name,
        "complete": snapshot.complete,
        "error": snapshot.error,
        "breakpoints": [
            {
                "path": str(item.path),
                "line": item.line,
                "address": f"0x{item.address:08X}" if item.address is not None else "",
                "enabled": item.enabled,
                "condition": item.condition,
            }
            for item in snapshot.breakpoints
        ],
    }


def _first_error(commands: tuple[KeilBreakpointCommandResult, ...]) -> str:
    for command in commands:
        if command.error:
            return command.error
    return ""


def _join_limited_reasons(reasons: tuple[str, ...]) -> str:
    if not reasons:
        return ""
    text = "；".join(reasons[:2])
    if len(reasons) > 2:
        text += f" 等 {len(reasons)} 类"
    return text


def _address_samples(commands: tuple[KeilBreakpointCommandResult, ...]) -> tuple[str, ...]:
    samples: list[str] = []
    for item in commands:
        operation = item.operation
        if operation.address is None:
            continue
        suffix = "" if operation.address_exact else "~"
        samples.append(f"{Path(operation.path).name}:{operation.line}->{suffix}0x{int(operation.address):08X}")
        if len(samples) >= 3:
            break
    return tuple(samples)


def _command_samples(commands: tuple[KeilBreakpointCommandResult, ...]) -> tuple[str, ...]:
    samples: list[str] = []
    for item in commands:
        if not item.command or item.operation.action == KeilBreakpointSyncAction.NOOP:
            continue
        if item.attempted and item.succeeded:
            prefix = "成功"
        elif item.attempted:
            prefix = "失败"
        else:
            prefix = "受限"
        samples.append(f"{prefix} {item.command}")
        if len(samples) >= 3:
            break
    return tuple(samples)
