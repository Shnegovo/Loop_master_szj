"""Backend-neutral dry-run command transaction primitives."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


class DebugCommandKind(str, Enum):
    DISCOVER = "discover"
    ATTACH = "attach"
    DISCONNECT = "disconnect"
    HALT = "halt"
    RUN = "run"
    STEP = "step"
    SYNC_BREAKPOINTS = "sync_breakpoints"
    WRITE_VARIABLES = "write_variables"


class DebugCommandGuardState(str, Enum):
    PASS = "pass"
    WAIT = "wait"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DebugCommandGuard:
    key: str
    label: str
    state: DebugCommandGuardState
    detail: str = ""


@dataclass(frozen=True)
class DebugCommandTransaction:
    schema_version: int
    transaction_id: str
    backend: str
    kind: DebugCommandKind
    title: str
    intent: str
    risk: str
    project_path: Path | None
    target_name: str
    dry_run: bool
    preconditions_met: bool
    execution_enabled: bool
    command_preview: tuple[str, ...]
    expected_effect: str
    guards: tuple[DebugCommandGuard, ...]
    audit_summary: str
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
            and all(guard.state == DebugCommandGuardState.PASS for guard in self.guards)
        )

    @property
    def blocked_reasons(self) -> tuple[str, ...]:
        return tuple(
            guard.detail or guard.label
            for guard in self.guards
            if guard.state in {DebugCommandGuardState.WAIT, DebugCommandGuardState.BLOCKED}
        )

    def audit_record(self, event: str = "planned") -> dict[str, Any]:
        return {
            "event": str(event),
            "schema_version": self.schema_version,
            "transaction_id": self.transaction_id,
            "backend": self.backend,
            "kind": self.kind.value,
            "title": self.title,
            "intent": self.intent,
            "risk": self.risk,
            "project_path": str(self.project_path) if self.project_path else "",
            "target_name": self.target_name,
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
            "backend_snapshot_id": self.backend_snapshot_id,
            "backend_snapshot": self.backend_snapshot,
        }


@dataclass(frozen=True)
class DebugCommandHistoryEntry:
    schema_version: int
    entry_id: str
    sequence: int
    first_seen_at: str
    last_seen_at: str
    seen_count: int
    event: str
    source: str
    transaction_id: str
    backend: str
    kind: DebugCommandKind
    title: str
    risk: str
    dry_run: bool
    preconditions_met: bool
    execution_enabled: bool
    ready: bool
    project_path: str
    target_name: str
    command_preview: tuple[str, ...]
    expected_effect: str
    blocked_reasons: tuple[str, ...]
    guard_summary: dict[str, int]
    audit_summary: str
    dedupe_key: str
    backend_snapshot_id: str = ""
    backend_snapshot: dict[str, Any] | None = None
    port: int | None = None
    breakpoint_diff_summary: dict[str, Any] | None = None

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
            "backend": self.backend,
            "kind": self.kind.value,
            "title": self.title,
            "risk": self.risk,
            "dry_run": self.dry_run,
            "preconditions_met": self.preconditions_met,
            "execution_enabled": self.execution_enabled,
            "ready": self.ready,
            "project_path": self.project_path,
            "target_name": self.target_name,
            "command_preview": list(self.command_preview),
            "expected_effect": self.expected_effect,
            "blocked_reasons": list(self.blocked_reasons),
            "guard_summary": dict(self.guard_summary),
            "audit_summary": self.audit_summary,
            "dedupe_key": self.dedupe_key,
            "backend_snapshot_id": self.backend_snapshot_id,
            "backend_snapshot": self.backend_snapshot,
            "port": self.port,
            "breakpoint_diff": self.breakpoint_diff_summary,
        }


class DebugCommandHistory:
    def __init__(self, max_entries: int = 64) -> None:
        self._max_entries = max(1, int(max_entries))
        self._entries: list[DebugCommandHistoryEntry] = []
        self._next_sequence = 1

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def all(self) -> tuple[DebugCommandHistoryEntry, ...]:
        return tuple(self._entries)

    def record(
        self,
        transaction: object,
        *,
        event: str = "previewed",
        source: str = "ui_sync",
        timestamp: str | None = None,
    ) -> DebugCommandHistoryEntry:
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
        breakpoint_diff = getattr(transaction, "breakpoint_diff_summary", None)
        entry = DebugCommandHistoryEntry(
            schema_version=1,
            entry_id=_history_entry_id(sequence, dedupe_key),
            sequence=sequence,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            seen_count=1,
            event=str(event),
            source=str(source),
            transaction_id=str(getattr(transaction, "transaction_id", "")),
            backend=str(getattr(transaction, "backend", _backend_from_transaction_id(transaction))),
            kind=DebugCommandKind(str(getattr(getattr(transaction, "kind", ""), "value", getattr(transaction, "kind", "")))),
            title=str(getattr(transaction, "title", "")),
            risk=str(getattr(transaction, "risk", "")),
            dry_run=bool(getattr(transaction, "dry_run", True)),
            preconditions_met=bool(getattr(transaction, "preconditions_met", False)),
            execution_enabled=bool(getattr(transaction, "execution_enabled", False)),
            ready=bool(getattr(transaction, "ready", False)),
            project_path=str(getattr(transaction, "project_path", "") or ""),
            target_name=str(getattr(transaction, "target_name", "") or ""),
            command_preview=tuple(str(item) for item in getattr(transaction, "command_preview", ())),
            expected_effect=str(getattr(transaction, "expected_effect", "")),
            blocked_reasons=tuple(str(item) for item in getattr(transaction, "blocked_reasons", ())),
            guard_summary=_guard_summary(tuple(getattr(transaction, "guards", ()))),
            audit_summary=str(getattr(transaction, "audit_summary", "")),
            dedupe_key=dedupe_key,
            backend_snapshot_id=str(getattr(transaction, "backend_snapshot_id", "") or ""),
            backend_snapshot=getattr(transaction, "backend_snapshot", None),
            port=getattr(transaction, "port", None),
            breakpoint_diff_summary=breakpoint_diff.to_record() if breakpoint_diff is not None else None,
        )
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            del self._entries[0: len(self._entries) - self._max_entries]
        return entry

    def recent(
        self,
        limit: int = 5,
        *,
        kind: str | DebugCommandKind | None = None,
        risk: str | None = None,
        blocked: bool | None = None,
        backend: str | None = None,
    ) -> tuple[DebugCommandHistoryEntry, ...]:
        entries = list(reversed(self._entries))
        if kind is not None:
            wanted = kind.value if isinstance(kind, DebugCommandKind) else str(kind)
            entries = [entry for entry in entries if entry.kind.value == wanted]
        if risk is not None:
            wanted_risk = str(risk)
            entries = [entry for entry in entries if entry.risk == wanted_risk]
        if blocked is not None:
            want_blocked = bool(blocked)
            entries = [entry for entry in entries if bool(entry.blocked_reasons) == want_blocked]
        if backend is not None:
            wanted_backend = str(backend)
            entries = [entry for entry in entries if entry.backend == wanted_backend]
        return tuple(entries[:max(0, int(limit))])


def build_unavailable_debug_transactions(
    status: object,
    plans: Iterable[object],
    *,
    backend: str,
    backend_display_name: str = "",
    reason: str = "调试后端尚未接入执行器",
    backend_snapshot: dict[str, Any] | object | None = None,
) -> tuple[DebugCommandTransaction, ...]:
    snapshot_record = _backend_snapshot_record(backend_snapshot)
    return tuple(
        _unavailable_transaction(
            plan,
            status=status,
            backend=str(backend),
            backend_display_name=str(backend_display_name or backend),
            reason=str(reason),
            backend_snapshot=snapshot_record,
        )
        for plan in plans
    )


def debug_transaction_by_key(
    transactions: Iterable[DebugCommandTransaction],
    key: str | DebugCommandKind,
) -> DebugCommandTransaction | None:
    wanted = key.value if isinstance(key, DebugCommandKind) else str(key)
    for transaction in transactions:
        if transaction.kind.value == wanted:
            return transaction
    return None


def _unavailable_transaction(
    plan: object,
    *,
    status: object,
    backend: str,
    backend_display_name: str,
    reason: str,
    backend_snapshot: dict[str, Any] | None,
) -> DebugCommandTransaction:
    kind = DebugCommandKind(str(getattr(plan, "key", "")))
    preconditions_met = bool(getattr(plan, "preconditions_met", False))
    title = str(getattr(plan, "title", kind.value))
    project_path = _project_path(getattr(status, "project_path", None))
    target_name = str(getattr(status, "target_name", "") or "")
    command_preview = (
        f"# {backend_display_name}: {title}",
        f"# {reason}",
        "# dry_run=true execution_enabled=false",
    )
    guards = (
        _guard(
            "plan_preconditions",
            "动作条件",
            DebugCommandGuardState.PASS if preconditions_met else DebugCommandGuardState.WAIT,
            "" if preconditions_met else str(getattr(plan, "disabled_reason", "") or "等待调试状态"),
        ),
        _guard(
            "backend_adapter",
            "后端执行器",
            DebugCommandGuardState.BLOCKED,
            reason,
        ),
        _guard(
            "execution_gate",
            "执行门",
            DebugCommandGuardState.WAIT,
            "当前只允许 dry-run 审计，不连接探针、不启动进程、不写目标",
        ),
        _guard(
            "data_only",
            "数据对象",
            DebugCommandGuardState.PASS,
            "交易不携带 handle、subprocess、callable、线程或执行器对象",
        ),
    )
    payload = {
        "backend": backend,
        "kind": kind.value,
        "title": title,
        "project_path": str(project_path) if project_path else "",
        "target_name": target_name,
        "commands": command_preview,
        "guards": [(guard.key, guard.state.value, guard.detail) for guard in guards],
        "backend_snapshot_id": str((backend_snapshot or {}).get("snapshot_id", "")),
    }
    return DebugCommandTransaction(
        schema_version=1,
        transaction_id=_stable_id(payload),
        backend=backend,
        kind=kind,
        title=title,
        intent=str(getattr(plan, "intent", "")),
        risk=str(getattr(getattr(plan, "risk", ""), "value", getattr(plan, "risk", ""))),
        project_path=project_path,
        target_name=target_name,
        dry_run=True,
        preconditions_met=preconditions_met,
        execution_enabled=False,
        command_preview=command_preview,
        expected_effect="记录不可用后端的 dry-run 意图，不执行任何调试动作",
        guards=guards,
        audit_summary="dry-run only; backend executor unavailable",
        backend_snapshot_id=str((backend_snapshot or {}).get("snapshot_id", "")),
        backend_snapshot=backend_snapshot,
    )


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
    }


def _project_path(value: str | Path | None) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _guard(key: str, label: str, state: DebugCommandGuardState, detail: str = "") -> DebugCommandGuard:
    return DebugCommandGuard(key=key, label=label, state=state, detail=detail)


def _guard_summary(guards: tuple[object, ...]) -> dict[str, int]:
    counts = {
        DebugCommandGuardState.PASS.value: 0,
        DebugCommandGuardState.WAIT.value: 0,
        DebugCommandGuardState.BLOCKED.value: 0,
    }
    for guard in guards:
        state = str(getattr(getattr(guard, "state", ""), "value", getattr(guard, "state", "")))
        counts[state] = counts.get(state, 0) + 1
    return counts


def _stable_id(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"debug-txn-{digest[:16]}"


def _history_dedupe_key(event: str, source: str, transaction: object) -> str:
    return "|".join((str(event), str(source), str(getattr(transaction, "transaction_id", ""))))


def _history_entry_id(sequence: int, dedupe_key: str) -> str:
    digest = hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()
    return f"debug-history-{int(sequence):05d}-{digest[:8]}"


def _backend_from_transaction_id(transaction: object) -> str:
    transaction_id = str(getattr(transaction, "transaction_id", ""))
    if transaction_id.startswith("keil-"):
        return "keil"
    return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
