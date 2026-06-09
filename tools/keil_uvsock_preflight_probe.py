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

from src.core.keil.uvsock import check_uvsock_preflight  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a dry UVSOCK preflight.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--require-running", action="store_true")
    args = parser.parse_args()

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
