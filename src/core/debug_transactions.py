"""Backend-neutral dry-run command transaction primitives."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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


def _stable_id(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"debug-txn-{digest[:16]}"
