"""Safe live Keil breakpoint-sync smoke.

The probe uses the same KeilUvSockBackendAdapter.sync_breakpoints() path as the
Debug Workbench. It only sets a breakpoint after a read-only BL snapshot proves
that cleanup can be verified.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_snapshots import RemoteBreakpoint, RemoteBreakpointSnapshot  # noqa: E402
from src.core.keil.backend import KeilBackendConfig, KeilUvSockBackendAdapter  # noqa: E402
from src.core.keil.breakpoint_sync import build_keil_breakpoint_sync_request_from_state  # noqa: E402
from src.core.keil.commands import KeilBreakpointIntent  # noqa: E402
from src.core.keil.source_line_address import resolve_source_line_address  # noqa: E402


DEFAULT_PROJECT = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
DEFAULT_SOURCE = ROOT / "firmware" / "keil_f401_variable_probe" / "main.c"
DEFAULT_AXF = ROOT / "firmware" / "keil_f401_variable_probe" / "Objects" / "f401_variable_probe.axf"
DEFAULT_SOURCE_ROOT = ROOT / "firmware" / "keil_f401_variable_probe"
DEFAULT_TARGET = "STM32F401CCU6 Variable Probe"


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely smoke-test live Keil breakpoint sync.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--axf", default=str(DEFAULT_AXF))
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    parser.add_argument("--line", type=int, default=62)
    parser.add_argument("--execute", action="store_true", help="Actually connect to Keil and set/cleanup one breakpoint.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    source = Path(args.source).expanduser().resolve()
    axf = Path(args.axf).expanduser().resolve() if args.axf else None
    source_root = Path(args.source_root).expanduser().resolve()
    line = int(args.line)
    adapter = KeilUvSockBackendAdapter(KeilBackendConfig(root=Path(args.keil_root), port=int(args.port)))
    target_address = None
    if axf is not None and axf.exists():
        resolved = resolve_source_line_address(axf, source, line, source_roots=(source_root,), allow_nearest=False)
        if resolved.address is not None:
            target_address = int(resolved.address)

    record: dict[str, object] = {
        "project": str(project),
        "target": args.target,
        "source": str(source),
        "line": line,
        "axf": str(axf or ""),
        "target_address": f"0x{target_address:08X}" if target_address is not None else "",
        "execute": bool(args.execute),
        "safe_to_set": False,
        "set_attempted": False,
        "cleanup_attempted": False,
    }

    if not args.execute:
        record["conclusion"] = "dry-run only; pass --execute to touch the live Keil session"
        return _finish(record, args.json)

    before_snapshot = _read_breakpoints(adapter, project, args.target)
    record["before"] = _snapshot_record(before_snapshot)
    if not before_snapshot.complete:
        record["conclusion"] = "abort: remote breakpoint snapshot is incomplete, cleanup cannot be proven"
        return _finish(record, args.json, exit_code=2)
    if _matching_breakpoints(before_snapshot, source, line, target_address):
        record["conclusion"] = "abort: matching breakpoint already exists; not touching a user breakpoint"
        return _finish(record, args.json, exit_code=3)

    record["safe_to_set"] = True
    added_remote: RemoteBreakpoint | None = None
    add_result_record: dict[str, object] | None = None
    cleanup_result_record: dict[str, object] | None = None
    try:
        add_request = build_keil_breakpoint_sync_request_from_state(
            project_path=project,
            target_name=args.target,
            local_breakpoints=(KeilBreakpointIntent(source, line, enabled=True),),
            remote_breakpoints=before_snapshot.breakpoints,
            source_paths=(source,),
            transaction_id="live-breakpoint-sync-add",
            remote_snapshot_complete=True,
            axf_path=axf if axf is not None and axf.exists() else None,
        )
        record["set_attempted"] = True
        add_result = adapter.sync_breakpoints(add_request)
        add_result_record = add_result.to_record()
        record["add_result"] = add_result_record
        if not add_result.completed:
            record["conclusion"] = f"abort: add did not complete: {add_result.summary()}"
            return _finish(record, args.json, exit_code=4)
        after_add = add_result.remote_snapshot
        if after_add is None or not after_add.complete:
            record["conclusion"] = "abort: add completed but BL readback is incomplete, cleanup cannot be targeted"
            return _finish(record, args.json, exit_code=5)
        before_ids = {item.remote_id for item in before_snapshot.breakpoints if item.remote_id}
        added = [
            item for item in _matching_breakpoints(after_add, source, line, target_address)
            if item.remote_id and item.remote_id not in before_ids
        ]
        if not added:
            record["conclusion"] = "abort: could not identify the newly added remote breakpoint id"
            return _finish(record, args.json, exit_code=6)
        added_remote = added[0]
        record["added_remote"] = added_remote.to_record()
        cleanup_result = _cleanup_remote(adapter, project, args.target, source, added_remote)
        cleanup_result_record = cleanup_result.to_record()
        record["cleanup_result"] = cleanup_result_record
        record["cleanup_attempted"] = True
        if not cleanup_result.completed:
            record["conclusion"] = f"cleanup failed: {cleanup_result.summary()}"
            return _finish(record, args.json, exit_code=7)
        after_cleanup = cleanup_result.remote_snapshot or _read_breakpoints(adapter, project, args.target)
        record["after_cleanup"] = _snapshot_record(after_cleanup)
        leaked = [
            item.to_record()
            for item in after_cleanup.breakpoints
            if added_remote is not None and item.remote_id == added_remote.remote_id
        ] if after_cleanup.complete else ["cleanup snapshot incomplete"]
        record["leaked"] = leaked
        if leaked:
            record["conclusion"] = "cleanup verification failed: added remote breakpoint is still present"
            return _finish(record, args.json, exit_code=8)
        record["conclusion"] = "live breakpoint sync add/readback/cleanup passed"
        return _finish(record, args.json)
    finally:
        if added_remote is not None and cleanup_result_record is None:
            try:
                cleanup_result = _cleanup_remote(adapter, project, args.target, source, added_remote)
                record["emergency_cleanup"] = cleanup_result.to_record()
            except Exception as exc:
                record["emergency_cleanup_error"] = str(exc)


def _read_breakpoints(
    adapter: KeilUvSockBackendAdapter,
    project: Path,
    target_name: str,
) -> RemoteBreakpointSnapshot:
    snapshot = adapter.read_only_session_snapshot(
        project_path=project,
        target_name=target_name,
        attempt_connection=True,
        query_status=True,
    )
    remote = snapshot.remote_breakpoint_snapshot
    if remote is None:
        raise RuntimeError("Keil read-only snapshot did not include remote breakpoints")
    return remote


def _cleanup_remote(
    adapter: KeilUvSockBackendAdapter,
    project: Path,
    target_name: str,
    source: Path,
    remote: RemoteBreakpoint,
):
    request = build_keil_breakpoint_sync_request_from_state(
        project_path=project,
        target_name=target_name,
        local_breakpoints=(),
        remote_breakpoints=(remote,),
        source_paths=(source,),
        transaction_id="live-breakpoint-sync-cleanup",
        remote_snapshot_complete=True,
    )
    return adapter.sync_breakpoints(request)


def _matching_breakpoints(
    snapshot: RemoteBreakpointSnapshot,
    source: Path,
    line: int,
    address: int | None,
) -> tuple[RemoteBreakpoint, ...]:
    source_key = _path_key(source)
    matches: list[RemoteBreakpoint] = []
    for item in snapshot.breakpoints:
        if item.path is not None and _path_key(item.path) == source_key and int(item.line or 0) == int(line):
            matches.append(item)
            continue
        if address is not None and item.address is not None and int(item.address) == int(address):
            matches.append(item)
    return tuple(matches)


def _path_key(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve()).lower()


def _snapshot_record(snapshot: RemoteBreakpointSnapshot) -> dict[str, object]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "complete": snapshot.complete,
        "count": len(snapshot.breakpoints),
        "error": snapshot.error,
        "breakpoints": [item.to_record() for item in snapshot.breakpoints],
    }


def _finish(record: dict[str, object], json_output: bool, exit_code: int = 0) -> int:
    if json_output:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print(record.get("conclusion", "no conclusion"))
    if exit_code == 0:
        print("PASS Keil breakpoint sync live probe")
    sys.stdout.flush()
    sys.stderr.flush()
    return int(exit_code)


if __name__ == "__main__":
    exit_code = int(main())
    if "--execute" in sys.argv:
        # UVSOCK can crash during Python interpreter teardown after repeated
        # live sessions. Cleanup has already happened inside main(); hard-exit
        # preserves the real probe status instead of reporting a teardown AV.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)
    raise SystemExit(exit_code)
