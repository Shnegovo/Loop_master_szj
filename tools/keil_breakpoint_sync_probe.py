"""Probe guarded Keil breakpoint sync command execution with a fake session."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_snapshots import RemoteBreakpoint  # noqa: E402
from src.core.keil.breakpoint_sync import (  # noqa: E402
    build_keil_breakpoint_sync_request_from_state,
    execute_keil_breakpoint_sync,
    keil_breakpoint_command,
    remote_snapshot_from_operations,
)
from src.core.keil.commands import KeilBreakpointIntent, KeilBreakpointSyncAction  # noqa: E402
from src.core.keil.breakpoint_list import parse_keil_breakpoint_list  # noqa: E402


class FakeSession:
    def __init__(self, *, fail_on: str = "") -> None:
        self.fail_on = fail_on
        self.commands: list[str] = []

    def execute_command(self, command: str, *, echo: bool = False):
        self.commands.append(command)
        if self.fail_on and self.fail_on in command:
            raise RuntimeError("fake command failure")
        return "OK"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopmaster-bp-sync-") as tmp:
        root = Path(tmp)
        main_c = root / "main.c"
        pid_c = root / "pid.c"
        main_c.write_text("int main(void){return 0;}\n", encoding="utf-8")
        pid_c.write_text("void pid(void){}\n", encoding="utf-8")
        local = (
            KeilBreakpointIntent(main_c, 10, enabled=True),
            KeilBreakpointIntent(pid_c, 20, enabled=False),
            KeilBreakpointIntent(pid_c, 30, enabled=True),
            KeilBreakpointIntent(pid_c, 50, enabled=True, condition="angle > 2"),
        )
        remote = (
            RemoteBreakpoint(pid_c, 20, enabled=True, condition="", remote_id="2", raw_location=f"{pid_c}:20"),
            RemoteBreakpoint(pid_c, 30, enabled=False, condition="", remote_id="3", raw_location=f"{pid_c}:30"),
            RemoteBreakpoint(pid_c, 40, enabled=True, condition="", remote_id="4", raw_location=f"{pid_c}:40"),
        )
        request = build_keil_breakpoint_sync_request_from_state(
            project_path=root / "Project.uvprojx",
            target_name="Target 1",
            local_breakpoints=local,
            remote_breakpoints=remote,
            source_paths=(main_c, pid_c),
            transaction_id="fake-transaction",
        )
        actions = [operation.action for operation in request.operations]
        invalid = [operation for operation in request.operations if not operation.valid]
        _assert(KeilBreakpointSyncAction.DISABLE in actions, f"missing disable: {actions!r}")
        _assert(KeilBreakpointSyncAction.ENABLE in actions, f"missing enable: {actions!r}")
        _assert(KeilBreakpointSyncAction.REMOVE in actions, f"missing remove: {actions!r}")
        _assert(KeilBreakpointSyncAction.ADD in actions, f"missing add: {actions!r}")
        _assert(any(operation.action == KeilBreakpointSyncAction.ADD for operation in invalid), "conditional add should be guarded")
        command_text = "\n".join(keil_breakpoint_command(operation) for operation in request.operations)
        _assert("BS " in command_text and "BK 4" in command_text, f"command text mismatch: {command_text}")

        snapshot = remote_snapshot_from_operations(request, complete=True)
        session = FakeSession()
        result = execute_keil_breakpoint_sync(session, request, remote_snapshot=snapshot)
        _assert(not result.succeeded, "guarded conditional breakpoint should make the batch fail")
        _assert(result.attempted_count == 4, f"attempted count mismatch: {result.attempted_count}")
        _assert(result.remote_snapshot is not None and result.remote_snapshot.complete, "snapshot should be complete")
        _assert(any(command.startswith("BS ") for command in session.commands), f"commands missing BS: {session.commands!r}")

        safe_request = build_keil_breakpoint_sync_request_from_state(
            project_path=root / "Project.uvprojx",
            target_name="Target 1",
            local_breakpoints=local[:3],
            remote_breakpoints=remote,
            source_paths=(main_c, pid_c),
            transaction_id="safe-transaction",
        )
        safe_result = execute_keil_breakpoint_sync(FakeSession(), safe_request)
        _assert(safe_result.succeeded, safe_result.summary())
        _assert(safe_result.attempted_count == 4, f"safe attempted mismatch: {safe_result.attempted_count}")

        failing = execute_keil_breakpoint_sync(FakeSession(fail_on="BK"), safe_request)
        _assert(not failing.succeeded, "failure should mark sync unsuccessful")
        _assert(failing.failed_count == 1, f"failed count mismatch: {failing.failed_count}")
        rows = dict(failing.diagnostic_rows())
        _assert(rows.get("断点同步") == "失败", f"diagnostics mismatch: {rows!r}")

        parsed_remote = parse_keil_breakpoint_list(
            f"Breakpoints\n4 enabled {pid_c}:40\n5 disabled {pid_c}:30\n",
            project_path=root / "Project.uvprojx",
            target_name="Target 1",
        ).snapshot
        parsed_request = build_keil_breakpoint_sync_request_from_state(
            project_path=root / "Project.uvprojx",
            target_name="Target 1",
            local_breakpoints=local[:3],
            remote_breakpoints=parsed_remote.breakpoints,
            source_paths=(main_c, pid_c),
            transaction_id="parsed-remote",
        )
        parsed_command_text = "\n".join(keil_breakpoint_command(operation) for operation in parsed_request.operations)
        _assert("BK 4" in parsed_command_text, f"parsed remote remove id missing: {parsed_command_text}")
        _assert("BE 5" in parsed_command_text, f"parsed remote enable id missing: {parsed_command_text}")
        remove_order_remote = (
            RemoteBreakpoint(pid_c, 40, enabled=True, remote_id="4"),
            RemoteBreakpoint(pid_c, 50, enabled=True, remote_id="7"),
        )
        remove_order_request = build_keil_breakpoint_sync_request_from_state(
            project_path=root / "Project.uvprojx",
            target_name="Target 1",
            local_breakpoints=(),
            remote_breakpoints=remove_order_remote,
            source_paths=(pid_c,),
            transaction_id="remove-order",
        )
        remove_session = FakeSession()
        remove_result = execute_keil_breakpoint_sync(remove_session, remove_order_request)
        _assert(remove_result.succeeded, remove_result.summary())
        _assert(remove_session.commands == ["BK 7", "BK 4"], f"remove order must be descending: {remove_session.commands!r}")

        f401_axf = ROOT / "firmware" / "keil_f401_variable_probe" / "Objects" / "f401_variable_probe.axf"
        f401_main = ROOT / "firmware" / "keil_f401_variable_probe" / "main.c"
        if f401_axf.exists() and f401_main.exists():
            f401_request = build_keil_breakpoint_sync_request_from_state(
                project_path=ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx",
                target_name="STM32F401CCU6 Variable Probe",
                local_breakpoints=(KeilBreakpointIntent(f401_main, 62, enabled=True),),
                remote_breakpoints=(),
                source_paths=(f401_main,),
                transaction_id="f401-address-breakpoint",
                axf_path=f401_axf,
            )
            add_operation = next(operation for operation in f401_request.operations if operation.action == KeilBreakpointSyncAction.ADD)
            _assert(add_operation.address is not None, f"F401 breakpoint address was not resolved: {add_operation!r}")
            f401_command = keil_breakpoint_command(add_operation)
            _assert(f401_command.startswith("BS 0x0800"), f"F401 breakpoint should use flash address: {f401_command!r}")
            f401_result = execute_keil_breakpoint_sync(FakeSession(), f401_request)
            rows = dict(f401_result.diagnostic_rows())
            _assert("已解析" in rows.get("断点地址解析", ""), f"address diagnostics missing: {rows!r}")

    print("PASS Keil breakpoint sync probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
