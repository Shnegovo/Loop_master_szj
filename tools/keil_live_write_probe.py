"""Opt-in Keil UVSOCK live expression write probe.

Default mode is preflight only. Passing --write sends a real expression
assignment to an already running uVision UVSOCK debug session.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.uvsock import (  # noqa: E402
    KeilUvscLiveSession,
    UvscError,
    build_uvision_uvsock_command,
    check_uvsock_preflight,
    start_uvision_uvsock,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


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
            print(f"uVision launched pid={result.pid}; waiting {args.wait_seconds:g}s")
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
            extended_stack=True,
        ) as session:
            result = session.write_expression_value(args.expression, args.value)
    except UvscError as exc:
        raise AssertionError(str(exc)) from exc

    print(result.summary())
    _assert(result.attempted, "write was not attempted")
    _assert(result.written, result.error or "write failed")
    print(
        "PASS keil live variable write "
        f"expression={args.expression!r} value={args.value!r} readback={result.readback_text!r}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
