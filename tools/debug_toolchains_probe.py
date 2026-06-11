"""Probe debugger toolchain metadata."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_toolchains import (  # noqa: E402
    DebugToolchainStage,
    debug_toolchain_descriptor,
    default_debug_toolchains,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    by_key = {item.key: item for item in default_debug_toolchains()}
    expected = {"keil", "openocd_gdb", "pyocd", "ti_mspm0", "offline"}
    _assert(expected <= set(by_key), f"toolchain keys missing: {set(by_key)!r}")

    keil = by_key["keil"]
    _assert(keil.stage == DebugToolchainStage.LIVE, f"Keil should be live: {keil!r}")
    _assert("write_variable" in keil.implemented_operations, f"Keil write op missing: {keil!r}")
    _assert("run_to_cursor" in keil.implemented_operations, f"Keil run-to-cursor op missing: {keil!r}")

    openocd = by_key["openocd_gdb"]
    _assert(openocd.stage == DebugToolchainStage.PLACEHOLDER, f"OpenOCD stage mismatch: {openocd!r}")
    _assert("GDB" in openocd.protocol, f"OpenOCD protocol mismatch: {openocd!r}")
    _assert("不会启动 OpenOCD" in openocd.safety_note, f"OpenOCD safety note mismatch: {openocd!r}")

    pyocd = by_key["pyocd"]
    _assert(pyocd.stage == DebugToolchainStage.PLACEHOLDER, f"pyOCD stage mismatch: {pyocd!r}")
    _assert("CMSIS-Pack" in pyocd.target_scope, f"pyOCD target scope mismatch: {pyocd!r}")

    ti = by_key["ti_mspm0"]
    _assert(ti.stage == DebugToolchainStage.PLANNED, f"TI should be planned: {ti!r}")
    _assert("MSPM0G3507" in ti.display_name + ti.target_scope, f"TI target mismatch: {ti!r}")
    _assert("D:\\ti" in ti.executable_hint, f"TI path hint missing: {ti!r}")
    _assert("不连接探针" in ti.safety_note and "不写目标" in ti.safety_note, f"TI safety note mismatch: {ti!r}")

    for key in expected:
        descriptor = debug_toolchain_descriptor(key)
        rows = dict(descriptor.diagnostic_rows())
        _assert(rows.get("工具链协议"), f"{key} diagnostic protocol missing")
        _assert(rows.get("安全边界"), f"{key} diagnostic safety missing")
        _assert(descriptor.combo_note, f"{key} combo note missing")

    print("PASS debug toolchains probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
