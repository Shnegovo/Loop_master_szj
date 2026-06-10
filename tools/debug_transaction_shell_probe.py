"""Probe backend-neutral dry-run transactions for unavailable backends."""

from __future__ import annotations

import json
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_backend_registry import create_default_debug_backend_registry  # noqa: E402
from src.core.debug_transactions import (  # noqa: E402
    build_unavailable_debug_transactions,
    debug_transaction_by_key,
)
from src.core.debug_workbench import (  # noqa: E402
    DebugBackendKind,
    debug_command_plans_for_status,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_data_only(value: object, path: str = "transaction") -> None:
    _assert(not callable(value), f"{path} must not be callable")
    if is_dataclass(value):
        for field in fields(value):
            lower = field.name.lower()
            _assert(lower not in {"handle", "library", "executor", "callback", "thread", "process"}, f"{path}.{field.name} is forbidden")
            _assert_data_only(getattr(value, field.name), f"{path}.{field.name}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            lower = str(key).lower()
            _assert(lower not in {"handle", "library", "executor", "callback", "thread", "process"}, f"{path}.{key} is forbidden")
            _assert_data_only(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _assert_data_only(item, f"{path}[{index}]")


def main() -> int:
    registry = create_default_debug_backend_registry(include_placeholders=True)
    backend = registry.create(DebugBackendKind.OPENOCD_GDB)
    snapshot = backend.discover(
        project_path="D:/demo/build/demo.elf",
        target_name="stm32f401ccu6",
    )
    transactions = build_unavailable_debug_transactions(
        snapshot.status,
        debug_command_plans_for_status(snapshot.status),
        backend=snapshot.backend.value,
        backend_display_name=snapshot.adapter_name,
        reason="OpenOCD/GDB 后端尚未接入执行器",
        backend_snapshot=snapshot,
    )
    _assert(len(transactions) == 8, f"transaction count changed: {len(transactions)}")
    _assert(debug_transaction_by_key(transactions, "attach") is not None, "attach transaction missing")
    for transaction in transactions:
        _assert(transaction.backend == "openocd_gdb", "transaction backend mismatch")
        _assert(transaction.dry_run, f"{transaction.kind.value} must be dry-run")
        _assert(not transaction.execution_enabled, f"{transaction.kind.value} must not execute")
        _assert(not transaction.ready, f"{transaction.kind.value} must not be ready")
        _assert(transaction.backend_snapshot_id == snapshot.snapshot_id, f"{transaction.kind.value} snapshot id missing")
        rendered = " ".join(transaction.command_preview + transaction.blocked_reasons)
        for forbidden in ("Popen", "subprocess", "handle=", "执行成功", "已发送", "已写入"):
            _assert(forbidden not in rendered, f"{transaction.kind.value} contains forbidden text: {forbidden}")
        _assert("OpenOCD/GDB 后端尚未接入执行器" in rendered, f"{transaction.kind.value} missing backend reason")
        record = transaction.audit_record()
        _assert(record["backend"] == "openocd_gdb", "audit backend mismatch")
        _assert(record["backend_snapshot_id"] == snapshot.snapshot_id, "audit snapshot id missing")
        json.dumps(record, ensure_ascii=False, sort_keys=True)
        _assert_data_only(transaction)

    print("PASS debug transaction shell probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
