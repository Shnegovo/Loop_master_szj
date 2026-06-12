"""Run an explicit OpenOCD/GDB read-only smoke probe."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.openocd_gdb import OpenOcdGdbReadOnlyRequest, run_openocd_gdb_readonly_probe  # noqa: E402


DEFAULT_AXF = ROOT / "firmware" / "keil_f401_variable_probe" / "Objects" / "f401_variable_probe.axf"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openocd-root", default="D:/openocd")
    parser.add_argument("--gdb-path", default="")
    parser.add_argument("--axf", default=str(DEFAULT_AXF))
    parser.add_argument("--gdb-port", type=int, default=3333)
    parser.add_argument("--telnet-port", type=int, default=4444)
    parser.add_argument("--tcl-port", type=int, default=6666)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--gdb-timeout", type=float, default=8.0)
    parser.add_argument("--allow-halt", action="store_true", help="Allow GDB to interrupt the target if PC is not readable.")
    parser.add_argument("--resume-after-halt", action="store_true", help="Resume the target if this probe halted it.")
    parser.add_argument("--execute", action="store_true", help="Actually start OpenOCD and GDB/MI.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    request = OpenOcdGdbReadOnlyRequest(
        openocd_root=Path(args.openocd_root).expanduser() if args.openocd_root else None,
        gdb_path=Path(args.gdb_path).expanduser() if args.gdb_path else None,
        axf_path=Path(args.axf).expanduser().resolve() if args.axf else None,
        gdb_port=int(args.gdb_port),
        telnet_port=int(args.telnet_port),
        tcl_port=int(args.tcl_port),
        execute=bool(args.execute),
        allow_halt=bool(args.allow_halt),
        resume_after_halt=bool(args.resume_after_halt),
        connect_timeout_s=float(args.connect_timeout),
        gdb_timeout_s=float(args.gdb_timeout),
    )
    result = run_openocd_gdb_readonly_probe(request)
    record = result.to_record()
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print(result.detail)
        for key, value in result.diagnostic_rows():
            print(f"{key}: {value}")
    if result.succeeded:
        print("PASS OpenOCD/GDB read-only probe")
        return 0
    return 1 if args.execute else 0


if __name__ == "__main__":
    raise SystemExit(main())
