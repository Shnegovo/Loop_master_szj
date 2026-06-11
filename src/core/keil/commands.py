"""Dry-run Keil debug command transactions.

The objects in this module are intentionally data-only. They do not carry a
UVSOCK DLL handle, subprocess command, callback, or any callable execution path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from src.core.debug_snapshots import RemoteBreakpoint, RemoteBreakpointSnapshot


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


class KeilBreakpointSyncAction(str, Enum):
    ADD = "add"
    REMOVE = "remove"
    ENABLE = "enable"
    DISABLE = "disable"
    UPDATE_CONDITION = "update_condition"
    NOOP = "noop"


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
    verified: bool = False
    message: str = ""


KeilRemoteBreakpoint = RemoteBreakpoint
KeilBreakpointRemoteSnapshot = RemoteBreakpointSnapshot


@dataclass(frozen=True)
class KeilBreakpointSyncOperation:
    action: KeilBreakpointSyncAction
    path: Path
    line: int
    local_enabled: bool | None = None
    remote_enabled: bool | None = None
    local_condition: str = ""
    remote_condition: str = ""
    valid: bool = True
    reason: str = ""
    remote_id: str = ""
    address: int | None = None
    address_source: str = ""
    address_exact: bool = False


@dataclass(frozen=True)
class KeilBreakpointDiffSummary:
    schema_version: int
    snapshot_id: str
    local_count: int
    remote_count: int
    matched_count: int
    add_count: int
    remove_count: int
    enable_count: int
    disable_count: int
    update_condition_count: int
    noop_count: int
    invalid_count: int
    duplicate_count: int
    conflict_count: int
    pending_verify_count: int
    verified_count: int
    unverified_count: int
    changed_location_count: int
    operation_count: int
    snapshot_complete: bool
    snapshot_stale: bool
    digest: str
    reason: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "local_count": self.local_count,
            "remote_count": self.remote_count,
            "matched_count": self.matched_count,
            "add_count": self.add_count,
            "remove_count": self.remove_count,
            "enable_count": self.enable_count,
            "disable_count": self.disable_count,
            "update_condition_count": self.update_condition_count,
            "noop_count": self.noop_count,
            "invalid_count": self.invalid_count,
            "duplicate_count": self.duplicate_count,
            "conflict_count": self.conflict_count,
            "pending_verify_count": self.pending_verify_count,
            "verified_count": self.verified_count,
            "unverified_count": self.unverified_count,
            "changed_location_count": self.changed_location_count,
            "operation_count": self.operation_count,
            "snapshot_complete": self.snapshot_complete,
            "snapshot_stale": self.snapshot_stale,
            "digest": self.digest,
            "reason": self.reason,
        }


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
    breakpoint_diff_summary: KeilBreakpointDiffSummary | None = None
    backend_snapshot_id: str = ""
    backend_snapshot: dict[str, Any] | None = None

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
            "breakpoint_diff": self.breakpoint_diff_summary.to_record() if self.breakpoint_diff_summary is not None else None,
            "backend_snapshot_id": self.backend_snapshot_id,
            "backend_snapshot": self.backend_snapshot,
        }


@dataclass(frozen=True)
class KeilCommandHistoryEntry:
    schema_version: int
    entry_id: str
    sequence: int
    first_seen_at: str
    last_seen_at: str
    seen_count: int
    event: str
    source: str
    transaction_id: str
    kind: KeilCommandKind
    title: str
    risk: str
    dry_run: bool
    preconditions_met: bool
    execution_enabled: bool
    ready: bool
    project_path: str
    target_name: str
    port: int | None
    command_preview: tuple[str, ...]
    expected_effect: str
    blocked_reasons: tuple[str, ...]
    guard_summary: dict[str, int]
    audit_summary: str
    dedupe_key: str
    breakpoint_diff_summary: dict[str, Any] | None = None
    backend_snapshot_id: str = ""
    backend_snapshot: dict[str, Any] | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "entry_id": self.entry_id,
            "sequence": self.sequence,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "seen_count": self.seen_count,
            "event": self.event,
            "source": self.source,
            "transaction_id": self.transaction_id,
            "kind": self.kind.value,
            "title": self.title,
            "risk": self.risk,
            "dry_run": self.dry_run,
            "preconditions_met": self.preconditions_met,
            "execution_enabled": self.execution_enabled,
            "ready": self.ready,
            "project_path": self.project_path,
            "target_name": self.target_name,
            "port": self.port,
            "command_preview": list(self.command_preview),
            "expected_effect": self.expected_effect,
            "blocked_reasons": list(self.blocked_reasons),
            "guard_summary": dict(self.guard_summary),
            "audit_summary": self.audit_summary,
            "dedupe_key": self.dedupe_key,
            "breakpoint_diff": self.breakpoint_diff_summary,
            "backend_snapshot_id": self.backend_snapshot_id,
            "backend_snapshot": self.backend_snapshot,
        }


class KeilCommandHistory:
    def __init__(self, max_entries: int = 64) -> None:
        self._max_entries = max(1, int(max_entries))
        self._entries: list[KeilCommandHistoryEntry] = []
        self._next_sequence = 1

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def all(self) -> tuple[KeilCommandHistoryEntry, ...]:
        return tuple(self._entries)

    def record(
        self,
        transaction: KeilCommandTransaction,
        *,
        event: str = "previewed",
        source: str = "ui_sync",
        timestamp: str | None = None,
    ) -> KeilCommandHistoryEntry:
        seen_at = timestamp or _now_iso()
        dedupe_key = _history_dedupe_key(event, source, transaction)
        if self._entries and self._entries[-1].dedupe_key == dedupe_key:
            updated = replace(
                self._entries[-1],
                last_seen_at=seen_at,
                seen_count=self._entries[-1].seen_count + 1,
            )
            self._entries[-1] = updated
            return updated

        sequence = self._next_sequence
        self._next_sequence += 1
        entry = KeilCommandHistoryEntry(
            schema_version=1,
            entry_id=_history_entry_id(sequence, dedupe_key),
            sequence=sequence,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            seen_count=1,
            event=str(event),
            source=str(source),
            transaction_id=transaction.transaction_id,
            kind=transaction.kind,
            title=transaction.title,
            risk=transaction.risk,
            dry_run=transaction.dry_run,
            preconditions_met=transaction.preconditions_met,
            execution_enabled=transaction.execution_enabled,
            ready=transaction.ready,
            project_path=str(transaction.project_path) if transaction.project_path else "",
            target_name=transaction.target_name,
            port=transaction.port,
            command_preview=transaction.command_preview,
            expected_effect=transaction.expected_effect,
            blocked_reasons=transaction.blocked_reasons,
            guard_summary=_guard_summary(transaction.guards),
            audit_summary=transaction.audit_summary,
            dedupe_key=dedupe_key,
            breakpoint_diff_summary=transaction.breakpoint_diff_summary.to_record() if transaction.breakpoint_diff_summary is not None else None,
            backend_snapshot_id=transaction.backend_snapshot_id,
            backend_snapshot=transaction.backend_snapshot,
        )
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            del self._entries[0: len(self._entries) - self._max_entries]
        return entry

    def recent(
        self,
        limit: int = 5,
        *,
        kind: str | KeilCommandKind | None = None,
        risk: str | None = None,
        blocked: bool | None = None,
    ) -> tuple[KeilCommandHistoryEntry, ...]:
        entries = list(reversed(self._entries))
        if kind is not None:
            wanted = kind.value if isinstance(kind, KeilCommandKind) else str(kind)
            entries = [entry for entry in entries if entry.kind.value == wanted]
        if risk is not None:
            wanted_risk = str(risk)
            entries = [entry for entry in entries if entry.risk == wanted_risk]
        if blocked is not None:
            want_blocked = bool(blocked)
            entries = [entry for entry in entries if bool(entry.blocked_reasons) == want_blocked]
        return tuple(entries[:max(0, int(limit))])


def build_keil_debug_transactions(
    status: object,
    plans: Iterable[object],
    *,
    port: int | None,
    project_path: str | Path | None = None,
    target_name: str = "",
    breakpoints: Iterable[object] = (),
    remote_breakpoints: Iterable[object] = (),
    remote_breakpoint_snapshot: KeilBreakpointRemoteSnapshot | object | None = None,
    backend_snapshot: dict[str, Any] | object | None = None,
    source_paths: Iterable[str | Path] = (),
    variable_writes: Iterable[KeilVariableWriteIntent | object] = (),
    execution_gate: bool = False,
) -> tuple[KeilCommandTransaction, ...]:
    project = _project_path(project_path if project_path is not None else getattr(status, "project_path", None))
    target = str(target_name or getattr(status, "target_name", "") or "")
    breakpoint_intents = tuple(_breakpoint_intent(item) for item in breakpoints)
    remote_snapshot = _coerce_remote_breakpoint_snapshot(
        remote_breakpoint_snapshot,
        remote_breakpoints=remote_breakpoints,
        project_path=project,
        target_name=target,
    )
    remote_breakpoint_intents = tuple(_breakpoint_intent(item) for item in remote_snapshot.breakpoints)
    breakpoint_ops = diff_keil_breakpoints(
        breakpoint_intents,
        remote_breakpoint_intents,
        source_paths=source_paths,
    )
    breakpoint_diff_summary = build_keil_breakpoint_diff_summary(
        breakpoint_intents,
        remote_breakpoint_intents,
        breakpoint_ops,
        snapshot=remote_snapshot,
        source_paths=source_paths,
    )
    write_intents = tuple(_variable_write_intent(item) for item in variable_writes)
    backend_snapshot_record = _backend_snapshot_record(backend_snapshot)
    return tuple(
        _build_transaction(
            plan,
            port=port,
            project_path=project,
            target_name=target,
            breakpoints=breakpoint_intents,
            breakpoint_ops=breakpoint_ops,
            breakpoint_diff_summary=breakpoint_diff_summary,
            variable_writes=write_intents,
            execution_gate=execution_gate,
            backend_snapshot=backend_snapshot_record,
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


def _backend_snapshot_record(snapshot: dict[str, Any] | object | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    if isinstance(snapshot, dict):
        record = dict(snapshot)
    elif hasattr(snapshot, "to_record") and callable(getattr(snapshot, "to_record")):
        record = dict(snapshot.to_record())
    else:
        record = {
            "snapshot_id": str(getattr(snapshot, "snapshot_id", "") or ""),
            "backend": str(getattr(getattr(snapshot, "backend", ""), "value", getattr(snapshot, "backend", ""))),
            "adapter_name": str(getattr(snapshot, "adapter_name", "") or ""),
            "captured_at": str(getattr(snapshot, "captured_at", "") or ""),
        }
    status = record.get("status") if isinstance(record.get("status"), dict) else {}
    pc = record.get("pc_location") if isinstance(record.get("pc_location"), dict) else None
    remote = record.get("remote_breakpoint_snapshot") if isinstance(record.get("remote_breakpoint_snapshot"), dict) else None
    return {
        "schema_version": int(record.get("schema_version", 1) or 1),
        "snapshot_id": str(record.get("snapshot_id", "") or ""),
        "backend": str(record.get("backend", "") or ""),
        "adapter_name": str(record.get("adapter_name", "") or ""),
        "captured_at": str(record.get("captured_at", "") or ""),
        "read_only": bool(record.get("read_only", True)),
        "connection_attempted": bool(record.get("connection_attempted", False)),
        "connection_established": bool(record.get("connection_established", False)),
        "target_running": record.get("target_running"),
        "project_path": str(record.get("project_path", "") or ""),
        "target_name": str(record.get("target_name", "") or ""),
        "state": str(status.get("state", "") or ""),
        "detail": str(status.get("detail", "") or ""),
        "pc_location": pc,
        "remote_breakpoint_snapshot_id": str(record.get("remote_breakpoint_snapshot_id", "") or ""),
        "remote_breakpoint_complete": bool(remote.get("complete", False)) if remote else False,
        "remote_breakpoint_error": str(remote.get("error", "") or "") if remote else "",
    }


def diff_keil_breakpoints(
    local_breakpoints: Iterable[KeilBreakpointIntent | object],
    remote_breakpoints: Iterable[KeilBreakpointIntent | object] = (),
    *,
    source_paths: Iterable[str | Path] = (),
) -> tuple[KeilBreakpointSyncOperation, ...]:
    local_breakpoint_items = tuple(local_breakpoints)
    remote_breakpoint_items = tuple(remote_breakpoints)
    local = {_breakpoint_key(item): _breakpoint_intent(item) for item in local_breakpoint_items}
    remote = {_breakpoint_key(item): _breakpoint_intent(item) for item in remote_breakpoint_items}
    remote_ids = {_breakpoint_key(item): getattr(_remote_breakpoint(item), "remote_id", "") for item in remote_breakpoint_items}
    source_keys = {_normalise_path(path) for path in source_paths}
    operations: list[KeilBreakpointSyncOperation] = []
    for key in sorted(set(local) | set(remote)):
        local_item = local.get(key)
        remote_item = remote.get(key)
        item = local_item or remote_item
        if item is None:
            continue
        valid, reason = _breakpoint_validity(item, source_keys)
        remote_id = str(remote_ids.get(key, "") or "")
        if local_item is not None and remote_item is None:
            action = KeilBreakpointSyncAction.ADD
        elif local_item is None and remote_item is not None:
            action = KeilBreakpointSyncAction.REMOVE
        elif local_item is not None and remote_item is not None and local_item.enabled != remote_item.enabled:
            action = KeilBreakpointSyncAction.ENABLE if local_item.enabled else KeilBreakpointSyncAction.DISABLE
        elif local_item is not None and remote_item is not None and local_item.condition != remote_item.condition:
            action = KeilBreakpointSyncAction.UPDATE_CONDITION
        else:
            action = KeilBreakpointSyncAction.NOOP
        if action in {
            KeilBreakpointSyncAction.REMOVE,
            KeilBreakpointSyncAction.ENABLE,
            KeilBreakpointSyncAction.DISABLE,
            KeilBreakpointSyncAction.UPDATE_CONDITION,
        } and not remote_id:
            valid = False
            reason = "Keil 远端断点缺少编号，不能安全删除、启停或改条件"
        if action == KeilBreakpointSyncAction.ADD and local_item is not None and local_item.condition:
            valid = False
            reason = "Keil 源码行条件断点命令尚未验证，暂不自动同步条件"
        if action == KeilBreakpointSyncAction.ADD and local_item is not None and not local_item.enabled:
            valid = False
            reason = "Keil 新增后立即禁用需要远端编号回读，暂不自动同步禁用的新断点"
        if action == KeilBreakpointSyncAction.UPDATE_CONDITION:
            valid = False
            reason = "Keil 条件断点更新需要编号回读和重建，暂不自动同步条件"
        operations.append(
            KeilBreakpointSyncOperation(
                action=action,
                path=item.path,
                line=item.line,
                local_enabled=local_item.enabled if local_item is not None else None,
                remote_enabled=remote_item.enabled if remote_item is not None else None,
                local_condition=local_item.condition if local_item is not None else "",
                remote_condition=remote_item.condition if remote_item is not None else "",
                valid=valid,
                reason=reason,
                remote_id=remote_id,
            )
        )
    return tuple(operations)


def build_keil_breakpoint_diff_summary(
    local_breakpoints: Iterable[KeilBreakpointIntent | object],
    remote_breakpoints: Iterable[KeilBreakpointIntent | object] = (),
    breakpoint_ops: Iterable[KeilBreakpointSyncOperation] = (),
    *,
    snapshot: KeilBreakpointRemoteSnapshot | object | None = None,
    source_paths: Iterable[str | Path] = (),
) -> KeilBreakpointDiffSummary:
    local_items = tuple(_breakpoint_intent(item) for item in local_breakpoints)
    remote_items = tuple(_breakpoint_intent(item) for item in remote_breakpoints)
    operations = tuple(breakpoint_ops) or diff_keil_breakpoints(local_items, remote_items, source_paths=source_paths)
    counts = _breakpoint_operation_counts(operations)
    source_keys = {_normalise_path(path) for path in source_paths}
    local_keys = [_breakpoint_key(item) for item in local_items]
    remote_keys = [_breakpoint_key(item) for item in remote_items]
    snapshot_complete = bool(getattr(snapshot, "complete", False)) if snapshot is not None else bool(remote_items)
    snapshot_error = str(getattr(snapshot, "error", "") or "")
    snapshot_id = str(getattr(snapshot, "snapshot_id", "") or "")
    if not snapshot_id:
        snapshot_id = _breakpoint_summary_digest(local_items, remote_items, operations, snapshot_complete=snapshot_complete, snapshot_error=snapshot_error)
    reason = snapshot_error or ("等待远端断点快照" if not snapshot_complete else "")
    invalid_count = sum(1 for op in operations if not op.valid)
    duplicate_count = _duplicate_count(local_keys) + _duplicate_count(remote_keys)
    matched_count = len(set(local_keys) & set(remote_keys))
    valid_local_items = tuple(item for item in local_items if _breakpoint_validity(item, source_keys)[0])
    verified_count = sum(1 for item in valid_local_items if item.verified)
    unverified_count = sum(1 for item in valid_local_items if item.message and not item.verified)
    pending_verify_count = len(valid_local_items) - verified_count - unverified_count
    operation_count = sum(counts[action.value] for action in (
        KeilBreakpointSyncAction.ADD,
        KeilBreakpointSyncAction.REMOVE,
        KeilBreakpointSyncAction.ENABLE,
        KeilBreakpointSyncAction.DISABLE,
        KeilBreakpointSyncAction.UPDATE_CONDITION,
    ))
    summary = KeilBreakpointDiffSummary(
        schema_version=1,
        snapshot_id=snapshot_id,
        local_count=len(local_items),
        remote_count=len(remote_items),
        matched_count=matched_count,
        add_count=counts[KeilBreakpointSyncAction.ADD.value],
        remove_count=counts[KeilBreakpointSyncAction.REMOVE.value],
        enable_count=counts[KeilBreakpointSyncAction.ENABLE.value],
        disable_count=counts[KeilBreakpointSyncAction.DISABLE.value],
        update_condition_count=counts[KeilBreakpointSyncAction.UPDATE_CONDITION.value],
        noop_count=counts[KeilBreakpointSyncAction.NOOP.value],
        invalid_count=invalid_count,
        duplicate_count=duplicate_count,
        conflict_count=invalid_count,
        pending_verify_count=pending_verify_count,
        verified_count=verified_count,
        unverified_count=unverified_count,
        changed_location_count=0,
        operation_count=operation_count,
        snapshot_complete=snapshot_complete,
        snapshot_stale=not snapshot_complete or bool(snapshot_error),
        digest=_breakpoint_summary_digest(local_items, remote_items, operations, snapshot_complete=snapshot_complete, snapshot_error=snapshot_error),
        reason=reason,
    )
    return summary


def _coerce_remote_breakpoint_snapshot(
    snapshot: KeilBreakpointRemoteSnapshot | object | None,
    *,
    remote_breakpoints: Iterable[object],
    project_path: Path | None,
    target_name: str,
) -> KeilBreakpointRemoteSnapshot:
    if isinstance(snapshot, KeilBreakpointRemoteSnapshot):
        return snapshot
    if snapshot is not None:
        breakpoints = tuple(_remote_breakpoint(item) for item in getattr(snapshot, "breakpoints", ()))
        return KeilBreakpointRemoteSnapshot(
            schema_version=int(getattr(snapshot, "schema_version", 1) or 1),
            snapshot_id=str(getattr(snapshot, "snapshot_id", "") or _breakpoint_summary_digest((), breakpoints, (), snapshot_complete=bool(getattr(snapshot, "complete", False)), snapshot_error=str(getattr(snapshot, "error", "") or ""))),
            project_path=_project_path(getattr(snapshot, "project_path", project_path)),
            target_name=str(getattr(snapshot, "target_name", target_name) or target_name),
            captured_at=str(getattr(snapshot, "captured_at", _now_iso()) or _now_iso()),
            complete=bool(getattr(snapshot, "complete", True)),
            breakpoints=breakpoints,
            error=str(getattr(snapshot, "error", "") or ""),
        )
    breakpoints = tuple(_remote_breakpoint(item) for item in remote_breakpoints)
    snapshot_id = _breakpoint_summary_digest((), breakpoints, (), snapshot_complete=bool(breakpoints), snapshot_error="")
    return KeilBreakpointRemoteSnapshot(
        schema_version=1,
        snapshot_id=snapshot_id,
        project_path=project_path,
        target_name=target_name,
        captured_at=_now_iso(),
        complete=bool(breakpoints),
        breakpoints=breakpoints,
        error="",
    )


def _build_transaction(
    plan: object,
    *,
    port: int | None,
    project_path: Path | None,
    target_name: str,
    breakpoints: tuple[KeilBreakpointIntent, ...],
    breakpoint_ops: tuple[KeilBreakpointSyncOperation, ...],
    breakpoint_diff_summary: KeilBreakpointDiffSummary | None,
    variable_writes: tuple[KeilVariableWriteIntent, ...],
    execution_gate: bool,
    backend_snapshot: dict[str, Any] | None,
) -> KeilCommandTransaction:
    kind = KeilCommandKind(str(getattr(plan, "key", "")))
    preconditions_met = bool(getattr(plan, "preconditions_met", False))
    transaction_breakpoint_diff_summary = breakpoint_diff_summary if kind == KeilCommandKind.SYNC_BREAKPOINTS else None
    guards = _guards_for(kind, plan, port, project_path, target_name, breakpoints, breakpoint_ops, transaction_breakpoint_diff_summary, variable_writes, execution_gate)
    execution_enabled = False
    dry_run = True
    command_preview = _command_preview(kind, port, target_name, breakpoints, breakpoint_ops, transaction_breakpoint_diff_summary, variable_writes)
    payload = {
        "kind": kind.value,
        "title": str(getattr(plan, "title", kind.value)),
        "project_path": str(project_path) if project_path else "",
        "target_name": target_name,
        "port": port,
        "commands": command_preview,
        "guards": [(guard.key, guard.state.value, guard.detail) for guard in guards],
        "breakpoint_diff": transaction_breakpoint_diff_summary.to_record() if transaction_breakpoint_diff_summary is not None else None,
        "backend_snapshot_id": str((backend_snapshot or {}).get("snapshot_id", "")),
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
        breakpoint_diff_summary=transaction_breakpoint_diff_summary,
        backend_snapshot_id=str((backend_snapshot or {}).get("snapshot_id", "")),
        backend_snapshot=backend_snapshot,
    )


def _guards_for(
    kind: KeilCommandKind,
    plan: object,
    port: int | None,
    project_path: Path | None,
    target_name: str,
    breakpoints: tuple[KeilBreakpointIntent, ...],
    breakpoint_ops: tuple[KeilBreakpointSyncOperation, ...],
    breakpoint_diff_summary: KeilBreakpointDiffSummary | None,
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
        guards.extend(_breakpoint_guards(breakpoints, breakpoint_ops, breakpoint_diff_summary))
    if kind == KeilCommandKind.WRITE_VARIABLES:
        guards.extend(_write_guards(variable_writes))
    return tuple(guards)


def _command_preview(
    kind: KeilCommandKind,
    port: int | None,
    target_name: str,
    breakpoints: tuple[KeilBreakpointIntent, ...],
    breakpoint_ops: tuple[KeilBreakpointSyncOperation, ...],
    breakpoint_diff_summary: KeilBreakpointDiffSummary | None,
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
        counts = _breakpoint_operation_counts(breakpoint_ops)
        summary = breakpoint_diff_summary or build_keil_breakpoint_diff_summary(breakpoints, (), breakpoint_ops)
        count_text = ", ".join(
            f"{key}={counts[key]}"
            for key in ("add", "remove", "enable", "disable", "update_condition", "noop")
        )
        verify_text = (
            f", verified={summary.verified_count}, unverified={summary.unverified_count}, "
            f"pending_verify={summary.pending_verify_count}"
        )
        mode_text = "full_diff" if summary.snapshot_complete else "push_local_only"
        commands = [f"diff_breakpoints({count_text}{verify_text}, mode={mode_text})"]
        for op in breakpoint_ops[:8]:
            if op.action == KeilBreakpointSyncAction.NOOP:
                continue
            commands.append(_breakpoint_operation_command(op))
        if len(breakpoint_ops) > 8:
            commands.append(f"... {len(breakpoint_ops) - 8} more breakpoint diff operations")
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


def _breakpoint_guards(
    breakpoints: tuple[KeilBreakpointIntent, ...],
    breakpoint_ops: tuple[KeilBreakpointSyncOperation, ...],
    breakpoint_diff_summary: KeilBreakpointDiffSummary | None,
) -> tuple[KeilCommandGuard, ...]:
    if breakpoint_diff_summary is None:
        if not breakpoints and not breakpoint_ops:
            return (
                _guard("breakpoint_batch", "断点批次", KeilCommandGuardState.WAIT, "当前没有本地断点可同步"),
            )
        invalid = [op for op in breakpoint_ops if not op.valid]
        active_ops = [op for op in breakpoint_ops if op.action != KeilBreakpointSyncAction.NOOP]
        return (
            _guard("breakpoint_batch", "断点批次", KeilCommandGuardState.PASS, f"{len(breakpoints)} 个本地断点待 dry-run"),
            _guard(
                "breakpoint_diff",
                "断点差异",
                KeilCommandGuardState.WAIT if not active_ops else KeilCommandGuardState.PASS,
                "本地和远端断点无差异" if not active_ops else f"{len(active_ops)} 个差异操作待 dry-run",
            ),
            _guard(
                "breakpoint_locations",
                "断点位置",
                KeilCommandGuardState.BLOCKED if invalid else KeilCommandGuardState.PASS,
                f"{len(invalid)} 个断点位置无效或不在工程源码中" if invalid else "路径/行号具备基础格式",
            ),
        )
    invalid = breakpoint_diff_summary.invalid_count
    active_ops = breakpoint_diff_summary.operation_count
    if not breakpoint_diff_summary.snapshot_complete:
        invalid = breakpoint_diff_summary.invalid_count
        return (
            _guard("breakpoint_batch", "断点批次", KeilCommandGuardState.PASS, f"{len(breakpoints)} 个本地断点待推送"),
            _guard(
                "breakpoint_diff",
                "断点差异",
                KeilCommandGuardState.PASS,
                "远端断点枚举未完成，将只推送本地断点，不删除远端断点",
            ),
            _guard(
                "breakpoint_locations",
                "断点位置",
                KeilCommandGuardState.BLOCKED if invalid else KeilCommandGuardState.PASS,
                f"{invalid} 个断点位置无效或不在工程源码中" if invalid else "路径/行号具备基础格式",
            ),
        )
    return (
        _guard("breakpoint_batch", "断点批次", KeilCommandGuardState.PASS, f"{len(breakpoints)} 个本地断点待 dry-run"),
        _guard(
            "breakpoint_diff",
            "断点差异",
            KeilCommandGuardState.PASS,
            "本地和远端断点无差异"
            if active_ops == 0
            else (
                f"新增{breakpoint_diff_summary.add_count} 删除{breakpoint_diff_summary.remove_count} 启用{breakpoint_diff_summary.enable_count} "
                f"禁用{breakpoint_diff_summary.disable_count} 条件{breakpoint_diff_summary.update_condition_count} 无变化{breakpoint_diff_summary.noop_count}"
            ),
        ),
        _guard(
            "breakpoint_verify",
            "本地验证",
            KeilCommandGuardState.PASS,
            (
                f"已验证{breakpoint_diff_summary.verified_count} 未验证{breakpoint_diff_summary.unverified_count} "
                f"待验证{breakpoint_diff_summary.pending_verify_count}，仅作为 Keil 回读状态提示"
            ),
        ),
        _guard(
            "breakpoint_locations",
            "断点位置",
            KeilCommandGuardState.BLOCKED if invalid else KeilCommandGuardState.PASS,
            f"{invalid} 个断点位置无效或不在工程源码中" if invalid else "路径/行号具备基础格式",
        ),
    )


def _breakpoint_operation_counts(operations: tuple[KeilBreakpointSyncOperation, ...]) -> dict[str, int]:
    counts = {action.value: 0 for action in KeilBreakpointSyncAction}
    for operation in operations:
        counts[operation.action.value] = counts.get(operation.action.value, 0) + 1
    return counts


def _breakpoint_summary_digest(
    local_breakpoints: tuple[KeilBreakpointIntent, ...],
    remote_breakpoints: tuple[KeilBreakpointIntent, ...],
    operations: tuple[KeilBreakpointSyncOperation, ...],
    *,
    snapshot_complete: bool,
    snapshot_error: str,
) -> str:
    payload = {
        "local": [(str(item.path), item.line, item.enabled, item.condition, item.verified, item.message) for item in local_breakpoints],
        "remote": [(str(item.path), item.line, item.enabled, item.condition) for item in remote_breakpoints],
        "ops": [(op.action.value, str(op.path), op.line, op.valid, op.reason, op.address, op.address_exact) for op in operations],
        "complete": snapshot_complete,
        "error": snapshot_error,
    }
    return _stable_id(payload)


def _breakpoint_snapshot_digest(
    local_breakpoints: tuple[KeilBreakpointIntent, ...],
    remote_breakpoints: tuple[KeilBreakpointIntent, ...],
    operations: tuple[KeilBreakpointSyncOperation, ...],
    *,
    snapshot_complete: bool,
    snapshot_error: str,
) -> str:
    return _breakpoint_summary_digest(
        local_breakpoints,
        remote_breakpoints,
        operations,
        snapshot_complete=snapshot_complete,
        snapshot_error=snapshot_error,
    )


def _breakpoint_operation_command(operation: KeilBreakpointSyncOperation) -> str:
    line = _keil_source_line_expression(operation.path, operation.line)
    if operation.address is not None:
        line = f"0x{int(operation.address):08X}"
    if operation.action == KeilBreakpointSyncAction.ADD:
        return f'UVSC_DBG_EXEC_CMD(handle, "BS {line}")'
    if operation.action == KeilBreakpointSyncAction.REMOVE:
        return f'UVSC_DBG_EXEC_CMD(handle, "BK {operation.remote_id}")'
    if operation.action == KeilBreakpointSyncAction.ENABLE:
        return f'UVSC_DBG_EXEC_CMD(handle, "BE {operation.remote_id}")'
    if operation.action == KeilBreakpointSyncAction.DISABLE:
        return f'UVSC_DBG_EXEC_CMD(handle, "BD {operation.remote_id}")'
    if operation.action == KeilBreakpointSyncAction.UPDATE_CONDITION:
        return f'UVSC_DBG_EXEC_CMD(handle, "BS {line}")'
    return f'# noop {line}'


def _keil_source_line_expression(path: str | Path, line: int) -> str:
    return f"\\{Path(path)}\\{int(line)}"


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
    path = getattr(item, "path", "")
    if path is None or str(path) == "":
        path = f"remote-{getattr(item, 'remote_id', '') or 'unknown'}"
    return KeilBreakpointIntent(
        path=Path(path),
        line=int(getattr(item, "line", 0) or 0),
        enabled=bool(getattr(item, "enabled", True)),
        condition=str(getattr(item, "condition", "") or ""),
        verified=bool(getattr(item, "verified", False)),
        message=str(getattr(item, "message", "") or ""),
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


def _breakpoint_key(item: KeilBreakpointIntent | object) -> tuple[str, int]:
    intent = item if isinstance(item, KeilBreakpointIntent) else _breakpoint_intent(item)
    return _normalise_path(intent.path), int(intent.line)


def _remote_breakpoint(item: object) -> KeilRemoteBreakpoint:
    if isinstance(item, KeilRemoteBreakpoint):
        return item
    path = getattr(item, "path", None)
    if path is None:
        raw_location = str(getattr(item, "raw_location", "") or "")
        path_value = Path(raw_location or f"remote-{getattr(item, 'remote_id', '') or 'unknown'}")
        if raw_location == "":
            path_value = Path(f"remote-{getattr(item, 'remote_id', '') or 'unknown'}")
    else:
        path_value = Path(path)
    return KeilRemoteBreakpoint(
        path=path_value,
        line=int(getattr(item, "line", 0) or 0),
        enabled=getattr(item, "enabled", None),
        condition=None if getattr(item, "condition", None) is None else str(getattr(item, "condition", "") or ""),
        remote_id=str(getattr(item, "remote_id", "") or ""),
        raw_location=str(getattr(item, "raw_location", "") or ""),
        verified=bool(getattr(item, "verified", True)),
        message=str(getattr(item, "message", "") or ""),
    )


def _breakpoint_validity(
    item: KeilBreakpointIntent,
    source_keys: set[str],
) -> tuple[bool, str]:
    if item.line <= 0:
        return False, "断点行号必须大于 0"
    normalized = _normalise_path(item.path)
    if source_keys and normalized not in source_keys:
        return False, f"断点文件不在工程源码中: {item.path}"
    return True, ""


def _normalise_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve()).lower()


def _duplicate_count(keys: list[tuple[str, int]]) -> int:
    return max(0, len(keys) - len(set(keys)))


def _stable_id(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"keil-{digest[:16]}"


def _guard_summary(guards: tuple[KeilCommandGuard, ...]) -> dict[str, int]:
    counts = {
        KeilCommandGuardState.PASS.value: 0,
        KeilCommandGuardState.WAIT.value: 0,
        KeilCommandGuardState.BLOCKED.value: 0,
    }
    for guard in guards:
        counts[guard.state.value] = counts.get(guard.state.value, 0) + 1
    return counts


def _history_dedupe_key(event: str, source: str, transaction: KeilCommandTransaction) -> str:
    return "|".join((str(event), str(source), transaction.transaction_id))


def _history_entry_id(sequence: int, dedupe_key: str) -> str:
    digest = hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()
    return f"keil-history-{int(sequence):05d}-{digest[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
