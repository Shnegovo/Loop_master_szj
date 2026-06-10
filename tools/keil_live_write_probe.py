"""Opt-in Keil UVSOCK live expression write probe.

Default mode is preflight only. Passing --write sends a real expression
assignment to an already running uVision UVSOCK debug session.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.uvsock import (  # noqa: E402
    KeilUvscLiveSession,
    UvscError,
    attempt_existing_uvsock_connection,
    build_uvision_uvsock_command,
    check_uvsock_preflight,
    start_uvision_uvsock,
)
from src.parser.readelf import parse_symbol_table  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _wait_for_uvsock_ready(keil_root: str, port: int, timeout: float) -> None:
    deadline = time.perf_counter() + max(0.0, float(timeout))
    last_summary = ""
    while time.perf_counter() <= deadline:
        preflight, connection = attempt_existing_uvsock_connection(
            root=keil_root,
            port=port,
            query_status=False,
            connection_name="LoopMasterReadyProbe",
        )
        last_summary = f"{preflight.summary()} | {connection.summary()}"
        if connection.connected:
            print(f"UVSOCK ready: {connection.summary()}")
            return
        time.sleep(0.25)
    raise AssertionError(f"uVision UVSOCK did not become ready within {timeout:g}s: {last_summary}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an explicit Keil UVSOCK live variable write probe.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--project", default="")
    parser.add_argument("--target", default="")
    parser.add_argument("--connection-name", default="LoopMaster")
    parser.add_argument("--plan-launch", action="store_true")
    parser.add_argument("--launch-uvsock", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=8.0)
    parser.add_argument("--expression", default="debug_setpoint")
    parser.add_argument("--value", default="5000")
    parser.add_argument("--axf", default="", help="Optional ELF/AXF used to resolve --symbol for direct memory writes.")
    parser.add_argument("--symbol", default="", help="Optional symbol name resolved from --axf for direct memory writes.")
    parser.add_argument("--address", default="", help="Optional target RAM address for direct memory write, e.g. 0x20000008.")
    parser.add_argument("--value-type", choices=("int32", "uint32", "float32"), default="int32")
    parser.add_argument("--prefer-memory", action="store_true", help="Use --address memory write instead of expression assignment.")
    parser.add_argument("--exec-command", default="", help="Experimental Keil Command Window command to execute instead of expression/memory write.")
    parser.add_argument("--extended-stack", action="store_true", help="Call UVSC_GEN_SET_OPTIONS before entering debug.")
    parser.add_argument("--write", action="store_true", help="Actually send assignment to Keil.")
    args = parser.parse_args()

    if args.plan_launch or args.launch_uvsock:
        _assert(args.project, "--project is required for launch planning")
        plan = build_uvision_uvsock_command(
            root=args.keil_root,
            port=args.port,
            project=args.project,
            target=args.target or None,
        )
        print(f"UVSOCK launch command: {plan.display_command}")
        if plan.reasons:
            print(f"UVSOCK launch reasons: {'; '.join(plan.reasons)}")
        _assert(plan.ready, "launch plan is not ready")
        if args.launch_uvsock:
            result = start_uvision_uvsock(
                root=args.keil_root,
                port=args.port,
                project=args.project,
                target=args.target or None,
            )
            _assert(result.launched, result.error or "uVision launch failed")
            print(f"uVision launched pid={result.pid}; waiting for UVSOCK up to {args.wait_seconds:g}s")
            _wait_for_uvsock_ready(args.keil_root, args.port, args.wait_seconds)
        if not args.write:
            print("PASS keil live write launch plan")
            return 0

    preflight = check_uvsock_preflight(root=args.keil_root, require_running=True)
    print(preflight.summary())
    for process in preflight.processes:
        print(f"uVision process: pid={process.pid} name={process.name} path={process.path}")
    _assert(preflight.discovery.installed, "Keil/uVision discovery failed")
    _assert(preflight.load_result.loaded, f"UVSOCK DLL load failed: {preflight.load_result.error}")

    if not args.write:
        print("PASS keil live write preflight only; add --write to modify target state")
        return 0

    _assert(preflight.uvision_running, "uVision is not running")
    _assert(preflight.can_attempt_connection, "UVSOCK connection preflight failed")
    try:
        with KeilUvscLiveSession.connect_existing(
            root=args.keil_root,
            port=args.port,
            connection_name=args.connection_name,
            require_debug=True,
            extended_stack=args.extended_stack,
        ) as session:
            if args.exec_command:
                session.execute_command(args.exec_command, echo=True)
                if args.address or (args.axf and args.symbol):
                    address = _resolve_memory_address(args.address, args.axf, args.symbol)
                    payload = _pack_value(args.value, args.value_type)
                    readback = session.read_memory(address, len(payload))
                    _assert(readback == payload, f"exec readback mismatch: expected={payload.hex()} read={readback.hex()}")
                else:
                    address = 0
                result = None
            elif args.prefer_memory:
                address = _resolve_memory_address(args.address, args.axf, args.symbol)
                payload = _pack_value(args.value, args.value_type)
                session.write_memory(address, payload)
                readback = session.read_memory(address, len(payload))
                _assert(readback == payload, f"memory readback mismatch: wrote={payload.hex()} read={readback.hex()}")
                result = None
            else:
                result = session.write_expression_value(args.expression, args.value)
    except UvscError as exc:
        raise AssertionError(str(exc)) from exc

    if result is not None:
        print(result.summary())
        _assert(result.attempted, "write was not attempted")
        _assert(result.written, result.error or "write failed")
        print(
            "PASS keil live variable write "
            f"expression={args.expression!r} value={args.value!r} readback={result.readback_text!r}"
        )
    else:
        if args.exec_command:
            print(f"PASS keil live exec command command={args.exec_command!r}")
        else:
            print(
                "PASS keil live memory write "
                f"address=0x{address:08X} type={args.value_type} value={args.value!r}"
            )
    return 0


def _resolve_memory_address(address: str, axf: str, symbol: str) -> int:
    if address:
        return int(str(address), 0)
    _assert(axf and symbol, "--address or both --axf and --symbol are required with --prefer-memory")
    axf_path = Path(axf).expanduser().resolve()
    _assert(axf_path.exists(), f"AXF does not exist: {axf_path}")
    for item in parse_symbol_table(axf_path):
        if item.name == symbol:
            _assert(item.address >= 0x20000000, f"symbol is not in RAM: {symbol} @ 0x{item.address:08X}")
            _assert(item.size in {1, 2, 4, 8}, f"unexpected symbol size: {symbol} size={item.size}")
            return int(item.address)
    raise AssertionError(f"symbol not found in AXF: {symbol}")


def _pack_value(value: str, value_type: str) -> bytes:
    if value_type == "int32":
        return struct.pack("<i", int(value, 0))
    if value_type == "uint32":
        return struct.pack("<I", int(value, 0))
    if value_type == "float32":
        return struct.pack("<f", float(value))
    raise ValueError(f"unsupported value type: {value_type}")


if __name__ == "__main__":
    raise SystemExit(main())
