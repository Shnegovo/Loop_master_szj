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
from src.core.openocd_gdb.backend import OpenOcdGdbBackendAdapter, OpenOcdGdbBackendConfig  # noqa: E402


DEFAULT_PROJECT = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
DEFAULT_AXF = ROOT / "firmware" / "keil_f401_variable_probe" / "Objects" / "f401_variable_probe.axf"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Actually launch OpenOCD/GDB through the backend adapter.")
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
        _assert(rows.get("OpenOCD/GDB 恢复") == "是", f"live backend should restore target: {rows!r}")
    else:
        _assert(not snapshot.connection_established, "dry-run backend must not connect")
        _assert(snapshot.status.state == DebugRuntimeState.DISCONNECTED, f"dry-run state mismatch: {snapshot.status!r}")
        _assert(rows.get("OpenOCD/GDB 执行") == "dry-run", f"dry-run diagnostic mismatch: {rows!r}")

    record = {
        "discover": discover.to_record(),
        "snapshot": snapshot.to_record(),
    }
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print("PASS OpenOCD/GDB backend probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
