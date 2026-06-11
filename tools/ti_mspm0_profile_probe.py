"""Probe read-only TI MSPM0G3507 profile discovery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.ti_mspm0.profile import default_ti_mspm0_profile  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ti-root", default="D:/ti", help="Local TI install root.")
    parser.add_argument("--json", action="store_true", help="Print profile summary as JSON.")
    args = parser.parse_args(argv)

    profile = default_ti_mspm0_profile(args.ti_root)

    _assert(profile.device == "MSPM0G3507", f"device mismatch: {profile.device!r}")
    _assert("Cortex M0+" in profile.cpu_description, f"CPU mismatch: {profile.cpu_description!r}")
    _assert(profile.default_toolchain == "TICLANG", f"default toolchain mismatch: {profile.default_toolchain!r}")
    _assert(profile.default_connection == "TIXDS110_Connection.xml", f"default connection mismatch: {profile.default_connection!r}")
    _assert(profile.access_port_designator == "0x02000000", f"AP designator mismatch: {profile.access_port_designator!r}")
    _assert(profile.ticlang_linker_cmd == "mspm0g3507.cmd", f"TICLANG linker mismatch: {profile.ticlang_linker_cmd!r}")
    _assert("__MSPM0G3507__" in profile.ticlang_compiler_options, "TICLANG device macro missing")
    _assert(profile.syscfg_device == "MSPM0G3507", f"SysConfig device mismatch: {profile.syscfg_device!r}")
    _assert(profile.syscfg_package == "LQFP-64(PM)", f"SysConfig package mismatch: {profile.syscfg_package!r}")
    _assert(profile.syscfg_spin == "MSPM0G3507", f"SysConfig spin mismatch: {profile.syscfg_spin!r}")

    _assert(profile.flash_range is not None, "FLASH linker range missing")
    _assert(profile.flash_range.origin == 0x00000000, f"FLASH origin mismatch: {profile.flash_range!r}")
    _assert(profile.flash_range.length == 0x00020000, f"FLASH length mismatch: {profile.flash_range!r}")
    _assert(profile.sram_range is not None, "SRAM linker range missing")
    _assert(profile.sram_range.origin == 0x20200000, f"SRAM origin mismatch: {profile.sram_range!r}")
    _assert(profile.sram_range.length == 0x00008000, f"SRAM length mismatch: {profile.sram_range!r}")
    _assert(profile.targetdb_sysmem_range is not None, "targetdb SYSMEM range missing")
    _assert(profile.targetdb_sysmem_range.origin == 0x20000000, f"targetdb SYSMEM origin mismatch: {profile.targetdb_sysmem_range!r}")
    _assert(any("地址不同" in warning for warning in profile.warnings), f"SRAM mismatch warning missing: {profile.warnings!r}")
    _assert(not profile.missing_tools, f"TI tools missing: {profile.missing_tools!r}")

    peripherals = {item.name: item.origin for item in profile.key_peripherals}
    _assert(peripherals.get("gpioa") == 0x400A0000, f"GPIOA origin mismatch: {peripherals!r}")
    _assert(peripherals.get("uart0") == 0x40108000, f"UART0 origin mismatch: {peripherals!r}")
    _assert(peripherals.get("canfd0") == 0x40508000, f"CANFD0 origin mismatch: {peripherals!r}")

    if args.json:
        print(json.dumps(profile.to_summary_record(), ensure_ascii=False, indent=2))
    else:
        print("PASS TI MSPM0G3507 profile probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
