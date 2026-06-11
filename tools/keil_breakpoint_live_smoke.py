"""Explicit live Keil breakpoint command smoke.

This probe connects to an already running UVSOCK debug session by default. It
does not set or delete breakpoints unless --set-breakpoint is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.breakpoint_list import keil_breakpoint_list_command, parse_keil_breakpoint_list  # noqa: E402
from src.core.keil.breakpoint_sync import build_keil_breakpoint_sync_request_from_state, keil_breakpoint_command  # noqa: E402
from src.core.keil.commands import KeilBreakpointIntent, KeilBreakpointSyncAction  # noqa: E402
from src.core.keil.uvsock import KeilUvscLiveSession  # noqa: E402


DEFAULT_PROJECT = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
DEFAULT_SOURCE = ROOT / "firmware" / "keil_f401_variable_probe" / "main.c"
DEFAULT_AXF = ROOT / "firmware" / "keil_f401_variable_probe" / "Objects" / "f401_variable_probe.axf"
DEFAULT_TARGET = "STM32F401CCU6 Variable Probe"


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect live Keil breakpoint command behavior.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--axf", default=str(DEFAULT_AXF))
    parser.add_argument("--line", type=int, default=62)
    parser.add_argument("--set-breakpoint", action="store_true", help="Actually execute BS at --source/--line.")
    parser.add_argument("--verify-hit", action="store_true", help="Run the target after setting the breakpoint and poll for a stop.")
    parser.add_argument("--capture-bl-log", action="store_true", help="Try Keil LOG capture around BL to inspect command-window output.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    source = Path(args.source).expanduser().resolve()
    axf = Path(args.axf).expanduser().resolve() if args.axf else None
    record: dict[str, object] = {
        "project": str(project),
        "target": args.target,
        "source": str(source),
        "axf": str(axf or ""),
        "line": int(args.line),
        "set_breakpoint": bool(args.set_breakpoint),
    }

    with KeilUvscLiveSession.connect_existing(
        root=Path(args.keil_root),
        port=int(args.port),
        connection_name="LoopMasterBreakpointLiveSmoke",
        require_debug=True,
        extended_stack=True,
    ) as session:
        before_text = session.execute_command(keil_breakpoint_list_command(), echo=True)
        before_parse = parse_keil_breakpoint_list(
            before_text,
            project_path=project,
            target_name=args.target,
            command=keil_breakpoint_list_command(),
        )
        record["before_raw"] = before_text
        record["before_text_available"] = _has_non_echo_text(before_text, keil_breakpoint_list_command())
        record["before_complete"] = before_parse.complete
        record["before_count"] = len(before_parse.snapshot.breakpoints)
        record["before_error"] = before_parse.snapshot.error

        if args.set_breakpoint:
            request = build_keil_breakpoint_sync_request_from_state(
                project_path=project,
                target_name=args.target,
                local_breakpoints=(KeilBreakpointIntent(source, int(args.line), enabled=True),),
                remote_breakpoints=(),
                source_paths=(source,),
                transaction_id="live-breakpoint-smoke",
                axf_path=axf if axf and axf.exists() else None,
            )
            operation = next(item for item in request.operations if item.action == KeilBreakpointSyncAction.ADD)
            command = keil_breakpoint_command(operation)
            output = session.execute_command(command, echo=True)
            record["set_command"] = command
            record["set_address"] = f"0x{operation.address:08X}" if operation.address is not None else ""
            record["set_address_source"] = operation.address_source
            record["set_address_exact"] = operation.address_exact
            record["set_output"] = output
            if args.verify_hit:
                run_result = session.run_target()
                record["run_after_set"] = run_result.succeeded
                record["run_after_set_error"] = run_result.error
                hit_detected = False
                status_samples: list[bool | None] = []
                deadline = time.perf_counter() + 5.0
                while time.perf_counter() < deadline:
                    try:
                        running = session.target_running()
                    except Exception:
                        running = None
                    status_samples.append(running)
                    if running is False:
                        hit_detected = True
                        break
                    time.sleep(0.15)
                record["hit_detected"] = hit_detected
                record["status_samples"] = status_samples[:12]

        after_text = session.execute_command(keil_breakpoint_list_command(), echo=True)
        after_parse = parse_keil_breakpoint_list(
            after_text,
            project_path=project,
            target_name=args.target,
            command=keil_breakpoint_list_command(),
        )
        record["after_raw"] = after_text
        record["after_text_available"] = _has_non_echo_text(after_text, keil_breakpoint_list_command())
        record["after_complete"] = after_parse.complete
        record["after_count"] = len(after_parse.snapshot.breakpoints)
        record["after_error"] = after_parse.snapshot.error
        record["conclusion"] = (
            "UVSC_DBG_EXEC_CMD returned BreakList text"
            if record["after_text_available"]
            else "UVSC_DBG_EXEC_CMD echoed BL only; remote breakpoint enumeration needs another capture path"
        )
        if args.capture_bl_log:
            log_path = ROOT / "tools" / "keil_breakpoint_live_bl.log"
            try:
                log_path.unlink()
            except OSError:
                pass
            session.execute_command(f"LOG > {log_path}", echo=True)
            session.execute_command(keil_breakpoint_list_command(), echo=True)
            session.execute_command("LOG OFF", echo=True)
            time.sleep(0.25)
            try:
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                log_text = ""
                record["bl_log_error"] = str(exc)
            log_parse = parse_keil_breakpoint_list(
                log_text,
                project_path=project,
                target_name=args.target,
                command="LOG+BL",
            )
            record["bl_log_path"] = str(log_path)
            record["bl_log_raw"] = log_text
            record["bl_log_complete"] = log_parse.complete
            record["bl_log_count"] = len(log_parse.snapshot.breakpoints)
            record["bl_log_error"] = log_parse.snapshot.error or record.get("bl_log_error", "")
            if log_parse.snapshot.breakpoints:
                record["conclusion"] = (
                    "LOG+BL captured remote breakpoint text; source mapping is complete"
                    if log_parse.complete
                    else "LOG+BL captured address-only remote breakpoint text; source mapping remains incomplete"
                )

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Before BL raw: {record['before_raw']!r}")
        if args.set_breakpoint:
            print(f"Set command: {record.get('set_command')} output={record.get('set_output')!r}")
        print(f"After BL raw: {record['after_raw']!r}")
        print(str(record["conclusion"]))
    print("PASS keil breakpoint live smoke")
    return 0


def _has_non_echo_text(text: str, command: str) -> bool:
    stripped = str(text or "").strip()
    return bool(stripped and stripped != str(command or "").strip())


if __name__ == "__main__":
    raise SystemExit(main())
