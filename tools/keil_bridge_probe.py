"""Read-only probe for the Keil UVSOCK discovery layer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.discovery import IMPORTANT_UVSC_EXPORTS, discover_keil  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Keil/uVision UVSOCK discovery.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--expect-missing", action="store_true")
    parser.add_argument("--show-exports", action="store_true")
    args = parser.parse_args()

    discovery = discover_keil(args.keil_root)
    for line in discovery.report_lines():
        print(line)

    if args.expect_missing:
        _assert(not discovery.installed, "Keil was discovered but this probe expected a missing root")
        _assert(discovery.preferred_uvsc is None, "missing root should not select a UVSOCK DLL")
        print(f"PASS keil bridge discovery missing-root root={args.keil_root}")
        return 0

    _assert(discovery.installed, "Keil UV4.exe was not discovered")
    _assert(discovery.uv4_dir is not None, "UV4 directory missing")
    _assert(discovery.uvision_com is not None and discovery.uvision_com.exists, "uVision.com missing")
    _assert(discovery.uvsc64_dll is not None and discovery.uvsc64_dll.exists, "UVSC64.dll missing")
    _assert(discovery.preferred_uvsc is not None and discovery.preferred_uvsc.exists, "preferred UVSOCK DLL missing")
    if discovery.python_bits >= 64:
        _assert(
            discovery.preferred_uvsc.path.name.lower() == "uvsc64.dll",
            "64-bit Python should prefer UVSC64.dll",
        )

    exports = discovery.preferred_exports
    _assert(exports, "preferred UVSOCK DLL exported no names")
    missing = discovery.missing_important_exports
    _assert(not missing, f"important UVSOCK exports missing: {', '.join(missing)}")

    flags = discovery.capability_flags()
    for name in (
        "can_open_connection",
        "can_enter_debug",
        "can_exec_command",
        "can_eval_expression",
        "can_read_memory",
        "can_write_memory",
        "can_control_target",
    ):
        _assert(flags.get(name, False), f"capability flag is false: {name}")

    _assert(
        all(file.path.name.lower() != "tools.ini" for file in discovery.docs),
        "TOOLS.INI must not be treated as a documentation artifact",
    )

    if args.show_exports:
        print("exports:")
        for name in exports:
            print(f"  {name}")

    print(
        "PASS keil bridge discovery "
        f"uv4={discovery.uv4_dir} "
        f"dll={discovery.preferred_uvsc.path.name} "
        f"exports={len(exports)} important={len(IMPORTANT_UVSC_EXPORTS)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
