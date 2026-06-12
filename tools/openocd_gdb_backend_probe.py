"""Probe the OpenOCD/GDB Debug Workbench backend adapter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_workbench import DebugBackendKind, DebugRuntimeState  # noqa: E402
from src.core.keil.breakpoint_sync import build_keil_breakpoint_sync_request_from_state  # noqa: E402
from src.core.keil.commands import KeilBreakpointIntent  # noqa: E402
from src.core.openocd_gdb.backend import OpenOcdGdbBackendAdapter, OpenOcdGdbBackendConfig  # noqa: E402


DEFAULT_PROJECT = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
DEFAULT_AXF = ROOT / "firmware" / "keil_f401_variable_probe" / "Objects" / "f401_variable_probe.axf"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Actually launch OpenOCD/GDB through the backend adapter.")
    parser.add_argument("--sync-breakpoint", default="", help="Optional source breakpoint to add/read/delete, e.g. main.c:62.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    adapter = OpenOcdGdbBackendAdapter(
        OpenOcdGdbBackendConfig(
            axf_path=DEFAULT_AXF,
            gdb_port=3334 if args.execute else 3333,
            telnet_port=4445 if args.execute else 4444,
            tcl_port=6667 if args.execute else 6666,
            execute_enabled=bool(args.execute),
            resume_after_halt=True,
        )
    )
    discover = adapter.discover(project_path=DEFAULT_PROJECT, target_name="STM32F401CCU6 Variable Probe")
    _assert(discover.backend == DebugBackendKind.OPENOCD_GDB, f"discover backend mismatch: {discover.backend!r}")
    _assert(discover.status.capabilities.can_attach, f"discover should enable attach: {discover.status!r}")
    _assert(not discover.connection_established, "discover must not connect")

    sync_add = None
    sync_cleanup = None
    try:
        snapshot = adapter.read_only_session_snapshot(
            project_path=DEFAULT_PROJECT,
            target_name="STM32F401CCU6 Variable Probe",
            attempt_connection=True,
            query_status=True,
        )
        rows = dict(snapshot.diagnostic_rows())
        _assert(snapshot.backend == DebugBackendKind.OPENOCD_GDB, f"snapshot backend mismatch: {snapshot.backend!r}")
        _assert(snapshot.read_only, "snapshot must remain read-only")
        _assert(snapshot.connection_attempted, "snapshot should record attempted attach")
        _assert(not snapshot.status.capabilities.can_write_variables, f"OpenOCD backend must not write variables: {snapshot.status!r}")
        _assert(not snapshot.status.capabilities.can_halt, f"OpenOCD backend must not expose halt yet: {snapshot.status!r}")
        _assert(rows.get("后端") == "OpenOCD / GDB", f"backend diagnostic mismatch: {rows!r}")
        _assert(rows.get("本机档案") == "OpenOCD/GDB 只读发现", f"profile diagnostic mismatch: {rows!r}")

        if args.execute:
            _assert(snapshot.connection_established, f"live backend attach failed: {rows!r}")
            _assert(snapshot.status.state in {DebugRuntimeState.RUNNING, DebugRuntimeState.PAUSED}, f"live state mismatch: {snapshot.status!r}")
            _assert(snapshot.pc_location is not None and snapshot.pc_location.complete, f"PC evidence missing: {snapshot.pc_location!r}")
            _assert(snapshot.status.capabilities.can_sync_breakpoints, f"live backend should expose breakpoint sync: {snapshot.status!r}")
            _assert(rows.get("OpenOCD/GDB 恢复") == "是", f"live backend should restore target: {rows!r}")
            if args.sync_breakpoint:
                source = DEFAULT_PROJECT.parent / "main.c"
                line = int(str(args.sync_breakpoint).split(":")[-1]) if ":" in args.sync_breakpoint else 62
                add_request = build_keil_breakpoint_sync_request_from_state(
                    project_path=DEFAULT_PROJECT,
                    target_name="STM32F401CCU6 Variable Probe",
                    local_breakpoints=(KeilBreakpointIntent(source, line),),
                    remote_breakpoints=(),
                    source_paths=(source,),
                    transaction_id="openocd-backend-probe-add",
                    remote_snapshot_complete=True,
                    axf_path=DEFAULT_AXF,
                )
                sync_add = adapter.sync_breakpoints(add_request)
                _assert(sync_add.completed, f"OpenOCD breakpoint add failed: {sync_add.to_record()!r}")
                _assert(sync_add.remote_snapshot is not None and sync_add.remote_snapshot.complete, "OpenOCD breakpoint add snapshot missing")
                _assert(len(sync_add.remote_snapshot.breakpoints) == 1, f"OpenOCD breakpoint count mismatch: {sync_add.remote_snapshot!r}")
                cleanup_request = build_keil_breakpoint_sync_request_from_state(
                    project_path=DEFAULT_PROJECT,
                    target_name="STM32F401CCU6 Variable Probe",
                    local_breakpoints=(),
                    remote_breakpoints=sync_add.remote_snapshot.breakpoints,
                    source_paths=(source,),
                    transaction_id="openocd-backend-probe-cleanup",
                    remote_snapshot_complete=True,
                    axf_path=DEFAULT_AXF,
                )
                sync_cleanup = adapter.sync_breakpoints(cleanup_request)
                _assert(sync_cleanup.completed, f"OpenOCD breakpoint cleanup failed: {sync_cleanup.to_record()!r}")
                _assert(
                    sync_cleanup.remote_snapshot is not None and not sync_cleanup.remote_snapshot.breakpoints,
                    f"OpenOCD breakpoint cleanup leaked: {sync_cleanup.to_record()!r}",
                )
        else:
            _assert(not snapshot.connection_established, "dry-run backend must not connect")
            _assert(snapshot.status.state == DebugRuntimeState.DISCONNECTED, f"dry-run state mismatch: {snapshot.status!r}")
            _assert(rows.get("OpenOCD/GDB 执行") == "dry-run", f"dry-run diagnostic mismatch: {rows!r}")
    finally:
        adapter.disconnect()

    record = {
        "discover": discover.to_record(),
        "snapshot": snapshot.to_record(),
        "sync_add": sync_add.to_record() if sync_add is not None else None,
        "sync_cleanup": sync_cleanup.to_record() if sync_cleanup is not None else None,
    }
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print("PASS OpenOCD/GDB backend probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
