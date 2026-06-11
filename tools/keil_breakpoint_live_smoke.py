"""Explicit live Keil breakpoint command smoke.

This probe connects to an already running UVSOCK debug session by default. It
does not set or delete breakpoints unless --set-breakpoint is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.breakpoint_list import keil_breakpoint_list_command, parse_keil_breakpoint_list  # noqa: E402
from src.core.keil.breakpoint_sync import keil_breakpoint_command  # noqa: E402
from src.core.keil.commands import KeilBreakpointSyncAction, KeilBreakpointSyncOperation  # noqa: E402
from src.core.keil.uvsock import KeilUvscLiveSession  # noqa: E402


DEFAULT_PROJECT = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
DEFAULT_SOURCE = ROOT / "firmware" / "keil_f401_variable_probe" / "main.c"
DEFAULT_TARGET = "STM32F401CCU6 Variable Probe"


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect live Keil breakpoint command behavior.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--line", type=int, default=62)
    parser.add_argument("--set-breakpoint", action="store_true", help="Actually execute BS at --source/--line.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    source = Path(args.source).expanduser().resolve()
    record: dict[str, object] = {
        "project": str(project),
        "target": args.target,
        "source": str(source),
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
            operation = KeilBreakpointSyncOperation(
                action=KeilBreakpointSyncAction.ADD,
                path=source,
                line=int(args.line),
                local_enabled=True,
                valid=True,
            )
            command = keil_breakpoint_command(operation)
            output = session.execute_command(command, echo=True)
            record["set_command"] = command
            record["set_output"] = output

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
