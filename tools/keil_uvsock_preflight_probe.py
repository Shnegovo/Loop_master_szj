"""Dry-run UVSOCK preflight probe.

This probe loads the selected UVSOCK DLL and reports whether uVision is
running. It does not open a UVSOCK connection or send debug commands.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.uvsock import (  # noqa: E402
    attempt_existing_uvsock_connection,
    build_uvision_uvsock_command,
    check_uvsock_preflight,
    start_uvision_uvsock,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a dry UVSOCK preflight.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--require-running", action="store_true")
    parser.add_argument("--attempt-existing", action="store_true")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--project", default="")
    parser.add_argument("--target", default="")
    parser.add_argument("--plan-launch", action="store_true")
    parser.add_argument("--launch-uvsock", action="store_true")
    args = parser.parse_args()

    if args.plan_launch or args.launch_uvsock:
        _assert(args.port is not None, "--port is required for UVSOCK launch planning")
        if args.launch_uvsock:
            _assert(args.project, "--project is required when actually launching uVision")
            result = start_uvision_uvsock(
                root=args.keil_root,
                port=args.port,
                project=args.project,
                target=args.target or None,
            )
            print(f"UVSOCK launch command: {result.plan.display_command}")
            if result.plan.reasons:
                print(f"UVSOCK launch reasons: {'; '.join(result.plan.reasons)}")
            _assert(result.launched, result.error or "uVision was not launched")
            print(f"PASS keil uvsock launch pid={result.pid} port={args.port}")
            return 0

        plan = build_uvision_uvsock_command(
            root=args.keil_root,
            port=args.port,
            project=args.project or None,
            target=args.target or None,
        )
        print(f"UVSOCK launch command: {plan.display_command}")
        if plan.reasons:
            print(f"UVSOCK launch reasons: {'; '.join(plan.reasons)}")
        _assert(plan.command, "launch command was not generated")
        if args.project:
            _assert(plan.ready, "launch plan should be ready when a valid project is provided")
        else:
            _assert(not plan.ready, "launch plan without a project must remain guidance-only")
        print(f"PASS keil uvsock launch plan ready={plan.ready} port={args.port}")
        return 0

    if args.attempt_existing:
        preflight, connection = attempt_existing_uvsock_connection(
            root=args.keil_root,
            port=args.port,
            query_status=args.status,
        )
        print(preflight.summary())
        for process in preflight.processes:
            print(f"uVision process: pid={process.pid} name={process.name} path={process.path}")
        print(connection.summary())

        _assert(preflight.discovery.installed, "Keil/uVision discovery failed")
        _assert(preflight.load_result.loaded, f"UVSOCK DLL load failed: {preflight.load_result.error}")
        if args.require_running:
            _assert(preflight.uvision_running, "uVision is not running")
        if preflight.can_attempt_connection and args.port is not None:
            _assert(connection.attempted, "connection attempt was expected")
            _assert(connection.connected, connection.error or "UVSOCK open/close failed")
        else:
            _assert(not connection.connected, "connection succeeded despite failed preflight")
        print(
            "PASS keil uvsock connection probe "
            f"attempted={connection.attempted} connected={connection.connected} "
            f"port={args.port if args.port is not None else '--'}"
        )
        return 0

    preflight = check_uvsock_preflight(
        root=args.keil_root,
        require_running=args.require_running,
    )

    print(preflight.summary())
    for process in preflight.processes:
        print(f"uVision process: pid={process.pid} name={process.name} path={process.path}")

    _assert(preflight.discovery.installed, "Keil/uVision discovery failed")
    _assert(preflight.load_result.loaded, f"UVSOCK DLL load failed: {preflight.load_result.error}")
    _assert(
        preflight.load_result.dll is not None
        and preflight.load_result.dll.path.name.lower() == "uvsc64.dll",
        "64-bit Python should dry-load UVSC64.dll",
    )

    if args.require_running:
        _assert(preflight.uvision_running, "uVision is not running")
        _assert(preflight.can_attempt_connection, "preflight says connection should not be attempted")

    print(
        "PASS keil uvsock preflight "
        f"dll={preflight.load_result.dll.path.name} "
        f"running={len(preflight.processes)} "
        f"can_attempt={preflight.can_attempt_connection}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
