"""Probe backend-neutral debug toolchain command plans."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_toolchains import (  # noqa: E402
    DebugToolchainStage,
    debug_toolchain_command_plan,
    debug_toolchain_descriptor,
    default_debug_toolchains,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    descriptors = default_debug_toolchains()
    keys = tuple(item.key for item in descriptors)
    _assert(keys == ("keil", "openocd_gdb", "pyocd", "ti_mspm0", "offline"), f"toolchain order mismatch: {keys!r}")

    for descriptor in descriptors:
        plan = debug_toolchain_command_plan(descriptor.key)
        rows = dict(plan.diagnostic_rows())
        _assert(plan.key == descriptor.key, f"{descriptor.key} plan key mismatch")
        _assert(plan.display_name == descriptor.display_name, f"{descriptor.key} display mismatch")
        _assert(rows.get("工具链命令计划", "").startswith(descriptor.display_name), f"{descriptor.key} plan diagnostics mismatch: {rows!r}")
        _assert(rows.get("预览命令"), f"{descriptor.key} preview commands missing: {rows!r}")
        _assert(rows.get("执行门"), f"{descriptor.key} gate diagnostics missing: {rows!r}")
        json.dumps(plan.to_record(), ensure_ascii=False, sort_keys=True)

    keil = debug_toolchain_command_plan("keil")
    _assert(keil.stage == DebugToolchainStage.LIVE, f"Keil should stay live: {keil!r}")
    _assert(keil.may_start_process and not keil.may_connect_probe and not keil.may_write_target, f"Keil gates mismatch: {keil!r}")
    _assert(any("UVSC_DBG_EXEC_CMD" in command for command in keil.preview_commands), f"Keil command preview mismatch: {keil.preview_commands!r}")

    openocd = debug_toolchain_command_plan("openocd_gdb")
    _assert(not openocd.may_start_process and not openocd.may_connect_probe and not openocd.may_write_target, f"OpenOCD gates mismatch: {openocd!r}")
    _assert(any("openocd" in command for command in openocd.preview_commands), f"OpenOCD preview mismatch: {openocd.preview_commands!r}")
    _assert(any("target extended-remote" in command for command in openocd.preview_commands), f"OpenOCD GDB preview mismatch: {openocd.preview_commands!r}")
    openocd_descriptor = debug_toolchain_descriptor("openocd_gdb")
    _assert("live_readonly_smoke_pc" in openocd_descriptor.implemented_operations, f"OpenOCD live smoke capability missing: {openocd_descriptor!r}")
    _assert("live_breakpoint_smoke" in openocd_descriptor.implemented_operations, f"OpenOCD breakpoint smoke capability missing: {openocd_descriptor!r}")
    _assert("app_backend_attach" in openocd_descriptor.planned_operations, f"OpenOCD app backend next step missing: {openocd_descriptor!r}")

    pyocd = debug_toolchain_command_plan("pyocd")
    _assert(not pyocd.may_start_process and not pyocd.may_connect_probe and not pyocd.may_write_target, f"pyOCD gates mismatch: {pyocd!r}")
    _assert(any("pyocd" in command.lower() for command in pyocd.preview_commands), f"pyOCD preview mismatch: {pyocd.preview_commands!r}")

    ti = debug_toolchain_command_plan("ti_mspm0")
    _assert(ti.stage == DebugToolchainStage.PLANNED, f"TI stage mismatch: {ti!r}")
    _assert(not ti.may_start_process and not ti.may_connect_probe and not ti.may_write_target, f"TI gates mismatch: {ti!r}")
    _assert(any("DSLite.exe" in command for command in ti.preview_commands), f"TI DSLite preview missing: {ti.preview_commands!r}")
    _assert(any("MSPM0G3507" in command for command in ti.preview_commands), f"TI device preview missing: {ti.preview_commands!r}")

    descriptor = debug_toolchain_descriptor("ti_mspm0")
    _assert("MSPM0G3507" in descriptor.target_scope, f"TI descriptor target mismatch: {descriptor!r}")

    print("PASS debug toolchain plan probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
