"""Probe dry-run Keil debug command transactions."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_workbench import (  # noqa: E402
    DebugRuntimeState,
    DebugWorkbenchSession,
)
from src.core.keil.commands import (  # noqa: E402
    KeilBreakpointRemoteSnapshot,
    KeilRemoteBreakpoint,
    KeilCommandHistory,
    KeilCommandGuardState,
    KeilVariableWriteIntent,
    append_keil_audit_log,
    build_keil_debug_transactions,
    transaction_by_key,
)


PROJECT = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project>
  <Targets>
    <Target>
      <TargetName>DebugDemo</TargetName>
      <Groups>
        <Group>
          <GroupName>App</GroupName>
          <Files>
            <File><FileName>main.c</FileName><FileType>1</FileType><FilePath>..\\Core\\Src\\main.c</FilePath></File>
          </Files>
        </Group>
      </Groups>
    </Target>
  </Targets>
</Project>
"""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _transaction_map(session: DebugWorkbenchSession, **kwargs):
    return {
        transaction.kind.value: transaction
        for transaction in build_keil_debug_transactions(
            session.status,
            session.command_plans(),
            port=4827,
            **kwargs,
        )
    }


def _remote_snapshot(project_path: Path, source_dir: Path, *, complete: bool = True) -> KeilBreakpointRemoteSnapshot:
    return KeilBreakpointRemoteSnapshot(
        schema_version=1,
        snapshot_id="keil-remote-breakpoint-snapshot-demo",
        project_path=project_path,
        target_name="DebugDemo",
        captured_at="2026-06-10T00:00:00+00:00",
        complete=complete,
        breakpoints=(
            KeilRemoteBreakpoint(path=source_dir / "main.c", line=3, enabled=False, condition="speed > 80", remote_id="bp-1", raw_location=f"{source_dir / 'main.c'}:3"),
            KeilRemoteBreakpoint(path=source_dir / "main.c", line=24, enabled=True, condition="speed_error < -24", remote_id="bp-2", raw_location=f"{source_dir / 'main.c'}:24"),
            KeilRemoteBreakpoint(path=source_dir / "main.c", line=12, enabled=True, condition="", remote_id="bp-3", raw_location=f"{source_dir / 'main.c'}:12"),
            KeilRemoteBreakpoint(path=source_dir / "main.c", line=48, enabled=True, condition="", remote_id="bp-4", raw_location=f"{source_dir / 'main.c'}:48"),
            KeilRemoteBreakpoint(path=source_dir / "main.c", line=96, enabled=True, condition="", remote_id="bp-5", raw_location=f"{source_dir / 'main.c'}:96"),
        )
        if complete
        else (),
    )


def _assert_data_only(value: object, path: str = "transaction") -> None:
    _assert(not callable(value), f"{path} must not be callable")
    if is_dataclass(value):
        for field in fields(value):
            lower = field.name.lower()
            _assert(lower not in {"handle", "library", "executor", "callback"}, f"{path}.{field.name} is forbidden")
            _assert_data_only(getattr(value, field.name), f"{path}.{field.name}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            lower = str(key).lower()
            _assert(lower not in {"handle", "library", "executor", "callback"}, f"{path}.{key} is forbidden")
            _assert_data_only(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _assert_data_only(item, f"{path}[{index}]")


def _assert_all_dry_run(transactions: dict[str, object]) -> None:
    for key, transaction in transactions.items():
        _assert(transaction.dry_run, f"{key} should be dry-run")
        _assert(not transaction.execution_enabled, f"{key} must not be execution-enabled")
        _assert(not transaction.ready, f"{key} must not be ready for execution")
        _assert(transaction.audit_record()["dry_run"], f"{key} audit record should preserve dry-run")
        json.dumps(transaction.audit_record(), ensure_ascii=False, sort_keys=True)
        _assert_data_only(transaction)
        rendered = " ".join(transaction.command_preview + tuple(transaction.blocked_reasons))
        for forbidden in ("Popen", "subprocess", "已发送", "已写入", "执行成功"):
            _assert(forbidden not in rendered, f"{key} rendered forbidden completion/execution text: {forbidden}")


def _guard_state(transaction, key: str):
    for guard in transaction.guards:
        if guard.key == key:
            return guard.state
    raise AssertionError(f"missing guard {key} in {transaction.kind.value}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopmaster-keil-txn-") as tmp:
        root = Path(tmp)
        project_dir = root / "MDK-ARM"
        source_dir = root / "Core" / "Src"
        project_dir.mkdir(parents=True)
        source_dir.mkdir(parents=True)
        (source_dir / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
        project_path = project_dir / "DebugDemo.uvprojx"
        project_path.write_text(PROJECT, encoding="utf-8")

        session = DebugWorkbenchSession()
        disconnected = _transaction_map(session, project_path=project_path, target_name="DebugDemo")
        expected = {
            "discover",
            "attach",
            "disconnect",
            "halt",
            "run",
            "step",
            "sync_breakpoints",
            "write_variables",
        }
        _assert(set(disconnected) == expected, f"transaction keys changed: {sorted(disconnected)}")
        _assert_all_dry_run(disconnected)
        for key in ("attach", "halt", "run", "step", "sync_breakpoints", "write_variables"):
            _assert(not disconnected[key].preconditions_met, f"{key} should be blocked while disconnected")

        session.mark_discovered(can_attach=True)
        discovered = _transaction_map(session, project_path=project_path, target_name="DebugDemo")
        _assert_all_dry_run(discovered)
        _assert(discovered["attach"].preconditions_met, "attach should be precondition-ready after discovery")
        _assert(_guard_state(discovered["attach"], "uvsock_port") == KeilCommandGuardState.PASS, "attach port guard should pass")
        _assert("UVSC_OpenConnection" in " ".join(discovered["attach"].command_preview), "attach preview missing UVSOCK open")
        _assert("UV4.exe" not in " ".join(discovered["attach"].command_preview), "attach preview must not include launch command")
        history = KeilCommandHistory(max_entries=3)
        first = history.record(discovered["attach"], timestamp="2026-06-10T00:00:00+00:00")
        duplicate = history.record(discovered["attach"], timestamp="2026-06-10T00:00:01+00:00")
        _assert(len(history) == 1 and duplicate.seen_count == 2, "adjacent duplicate history entry should merge")
        _assert(first.entry_id == duplicate.entry_id, "merged history should keep entry identity")

        session.mark_attached(running=True, runtime_control=True, breakpoint_sync=True)
        breakpoints = (
            SimpleNamespace(path=source_dir / "main.c", line=3, enabled=True, condition="speed > 60"),
            SimpleNamespace(path=source_dir / "main.c", line=12, enabled=True, condition="speed_error > 40"),
            SimpleNamespace(path=source_dir / "main.c", line=24, enabled=False, condition="speed_error < -12"),
            SimpleNamespace(path=source_dir / "main.c", line=48, enabled=True, condition=""),
            SimpleNamespace(path=source_dir / "main.c", line=72, enabled=True, condition=""),
            SimpleNamespace(path=source_dir / "main.c", line=0, enabled=True, condition=""),
        )
        running = _transaction_map(
            session,
            project_path=project_path,
            target_name="DebugDemo",
            breakpoints=breakpoints,
            remote_breakpoint_snapshot=_remote_snapshot(project_path, source_dir),
            source_paths=(source_dir / "main.c",),
        )
        _assert_all_dry_run(running)
        _assert(running["halt"].preconditions_met, "halt should be precondition-ready while running")
        _assert(not running["run"].preconditions_met, "run should be blocked while already running")
        _assert(not running["step"].preconditions_met, "step should be blocked while running")
        _assert(running["sync_breakpoints"].preconditions_met, "breakpoint sync capability should surface readiness")
        _assert(running["sync_breakpoints"].breakpoint_diff_summary is not None, "sync transaction should carry breakpoint diff summary")
        _assert(running["attach"].breakpoint_diff_summary is None, "non-sync transactions should not carry breakpoint diff summary")
        _assert("diff_breakpoints(add=2" in " ".join(running["sync_breakpoints"].command_preview), "breakpoint diff summary missing add count")
        _assert(running["sync_breakpoints"].breakpoint_diff_summary.add_count == 2, "add count mismatch")
        _assert(running["sync_breakpoints"].breakpoint_diff_summary.remove_count == 1, "remove count mismatch")
        _assert(running["sync_breakpoints"].breakpoint_diff_summary.enable_count == 1, "enable count mismatch")
        _assert(running["sync_breakpoints"].breakpoint_diff_summary.disable_count == 1, "disable count mismatch")
        _assert(running["sync_breakpoints"].breakpoint_diff_summary.update_condition_count == 1, "update-condition count mismatch")
        _assert(running["sync_breakpoints"].breakpoint_diff_summary.noop_count == 1, "noop count mismatch")
        _assert(running["sync_breakpoints"].breakpoint_diff_summary.snapshot_complete, "snapshot should be complete")
        _assert(
            _guard_state(running["sync_breakpoints"], "breakpoint_locations") == KeilCommandGuardState.BLOCKED,
            "invalid breakpoint line should block sync transaction",
        )
        _assert(
            _guard_state(running["sync_breakpoints"], "breakpoint_diff") == KeilCommandGuardState.PASS,
            "diff guard should be ready even when one breakpoint item is invalid",
        )
        history.record(running["halt"], timestamp="2026-06-10T00:00:02+00:00")
        history.record(discovered["attach"], timestamp="2026-06-10T00:00:03+00:00")
        _assert(len(history) == 3, "A/B/A history should preserve three segments")
        _assert([entry.title for entry in history.all()] == ["连接调试会话", "暂停目标", "连接调试会话"], "history order changed")
        _assert(history.recent(limit=1)[0].title == "连接调试会话", "recent should return newest first")
        _assert(history.recent(limit=3, kind="halt")[0].title == "暂停目标", "kind filter failed")
        _assert(history.recent(limit=3, blocked=True), "blocked filter should find dry-run guarded entries")

        empty_snapshot = KeilBreakpointRemoteSnapshot(
            schema_version=1,
            snapshot_id="keil-remote-breakpoint-empty",
            project_path=project_path,
            target_name="DebugDemo",
            captured_at="2026-06-10T00:00:00+00:00",
            complete=True,
            breakpoints=(),
            error="",
        )
        empty_sync = _transaction_map(
            session,
            project_path=project_path,
            target_name="DebugDemo",
            breakpoints=(),
            remote_breakpoint_snapshot=empty_snapshot,
        )
        _assert(empty_sync["sync_breakpoints"].breakpoint_diff_summary is not None, "empty sync should still carry summary")
        _assert(empty_sync["sync_breakpoints"].breakpoint_diff_summary.operation_count == 0, "empty sync should be noop")
        _assert(
            _guard_state(empty_sync["sync_breakpoints"], "breakpoint_diff") == KeilCommandGuardState.PASS,
            "complete empty remote snapshot should pass breakpoint diff guard",
        )
        _assert(
            "diff_breakpoints(add=0, remove=0, enable=0, disable=0, update_condition=0, noop=0)"
            in " ".join(empty_sync["sync_breakpoints"].command_preview),
            "empty sync should render zero-count diff preview",
        )

        session.update_runtime(running=False, current_pc_line=2, run_line=3)
        paused = _transaction_map(session, project_path=project_path, target_name="DebugDemo")
        _assert_all_dry_run(paused)
        _assert(paused["run"].preconditions_met, "run should be precondition-ready while paused")
        _assert(paused["step"].preconditions_met, "step should be precondition-ready while paused")
        _assert(not paused["halt"].preconditions_met, "halt should be blocked while paused")

        session.mark_attached(running=False, runtime_control=True, breakpoint_sync=True, variable_write=True)
        writes = (
            KeilVariableWriteIntent(
                symbol="g_speed_loop.target",
                value_text="60.0",
                type_name="",
                ram_checked=False,
            ),
        )
        write_ready = _transaction_map(
            session,
            project_path=project_path,
            target_name="DebugDemo",
            variable_writes=writes,
        )
        _assert_all_dry_run(write_ready)
        write_transaction = write_ready["write_variables"]
        _assert(write_transaction.preconditions_met, "write variable precondition should reflect declared capability")
        _assert(_guard_state(write_transaction, "ram_whitelist") == KeilCommandGuardState.WAIT, "RAM guard should wait")
        _assert(_guard_state(write_transaction, "readback") == KeilCommandGuardState.WAIT, "readback guard should wait")
        write_text = " ".join(write_transaction.blocked_reasons + write_transaction.command_preview)
        for phrase in ("RAM", "类型", "范围", "回读"):
            _assert(phrase in write_text, f"write transaction missing safety phrase: {phrase}")
        history.record(paused["run"], timestamp="2026-06-10T00:00:04+00:00")
        _assert(len(history) == 3, "history should enforce bounded length")
        _assert(history.all()[0].title == "暂停目标", "oldest segment eviction changed unexpectedly")
        for entry in history.all():
            json.dumps(entry.to_record(), ensure_ascii=False, sort_keys=True)
            _assert_data_only(entry)

        with tempfile.TemporaryDirectory(prefix="loopmaster-keil-audit-") as audit_tmp:
            audit_path = Path(audit_tmp) / "debug-audit.jsonl"
            _assert(not audit_path.exists(), "history recording should not auto-create an audit log")
            append_keil_audit_log(audit_path, (transaction_by_key(write_ready.values(), "write_variables"),))
            lines = audit_path.read_text(encoding="utf-8").splitlines()
            _assert(len(lines) == 1, "audit log should contain one record")
            record = json.loads(lines[0])
            _assert(record["kind"] == "write_variables" and record["dry_run"], "audit record shape mismatch")

    print("PASS keil command transaction probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
