"""Guarded Keil breakpoint sync execution helpers."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class KeilBreakpointSyncRequest:
    project_path: Path | None
    target_name: str = ""
    operations: tuple[KeilBreakpointSyncOperation, ...] = ()
    transaction_id: str = ""
    connection_name: str = "LoopMasterBreakpointSync"
    verify_after: bool = True
    remote_snapshot_complete: bool = True


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
        return sum(1 for item in self.commands if item.succeeded)

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.commands if not item.succeeded)

    @property
    def succeeded(self) -> bool:
        return not self.error and all(item.succeeded for item in self.commands)

    def summary(self) -> str:
        if self.succeeded:
            return f"Keil 断点同步完成：{self.succeeded_count}/{self.attempted_count} 条命令成功"
        detail = self.error or _first_error(self.commands) or "断点同步失败"
        return f"Keil 断点同步失败：{detail}"

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        rows = [
            ("断点同步", "成功" if self.succeeded else "失败"),
            ("断点命令", f"{self.succeeded_count}/{self.attempted_count} 成功"),
            ("断点同步模式", "完整差分" if self.request.remote_snapshot_complete else "推送本地"),
        ]
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
            "succeeded": self.succeeded,
            "attempted_count": self.attempted_count,
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "commands": [
                {
                    "action": item.operation.action.value,
                    "path": str(item.operation.path),
                    "line": item.operation.line,
                    "command": item.command,
                    "attempted": item.attempted,
                    "succeeded": item.succeeded,
                    "status_code": item.status_code,
                    "output": item.output,
                    "error": item.error,
                }
                for item in self.commands
            ],
            "remote_snapshot": _snapshot_record(self.remote_snapshot),
            "error": self.error,
        }


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
) -> KeilBreakpointSyncRequest:
    operations = diff_keil_breakpoints(
        local_breakpoints,
        remote_breakpoints,
        source_paths=source_paths,
    )
    return KeilBreakpointSyncRequest(
        project_path=Path(project_path).expanduser().resolve() if project_path else None,
        target_name=target_name,
        operations=tuple(operations),
        transaction_id=transaction_id,
        connection_name=connection_name,
        remote_snapshot_complete=bool(remote_snapshot_complete),
    )


def execute_keil_breakpoint_sync(
    session: KeilBreakpointCommandSession,
    request: KeilBreakpointSyncRequest,
    *,
    remote_snapshot: KeilBreakpointRemoteSnapshot | None = None,
) -> KeilBreakpointSyncResult:
    results: list[KeilBreakpointCommandResult] = []
    for operation in request.operations:
        command = keil_breakpoint_command(operation)
        if not operation.valid:
            results.append(
                KeilBreakpointCommandResult(
                    operation=operation,
                    command=command,
                    attempted=False,
                    succeeded=False,
                    error=operation.reason or "invalid breakpoint operation",
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
        return f"BreakSet {location}"
    if operation.action == KeilBreakpointSyncAction.REMOVE:
        return f"BreakKill {operation.remote_id or location}"
    if operation.action == KeilBreakpointSyncAction.ENABLE:
        return f"BreakEnable {operation.remote_id or location}"
    if operation.action == KeilBreakpointSyncAction.DISABLE:
        return f"BreakDisable {operation.remote_id or location}"
    if operation.action == KeilBreakpointSyncAction.UPDATE_CONDITION:
        return f"BreakSet {location}"
    return f"# noop {location}"


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
                    enabled=True if enabled is None else bool(enabled),
                    condition=operation.local_condition or operation.remote_condition,
                    raw_location=f"{operation.path}:{operation.line}",
                )
            )
    return KeilBreakpointRemoteSnapshot(
        schema_version=1,
        snapshot_id=f"keil-sync-{abs(hash(tuple((str(bp.path), bp.line, bp.enabled, bp.condition) for bp in breakpoints))):x}",
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
    return f"\\{operation.path}\\{operation.line}"


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
