"""Probe read-only OpenOCD/GDB profile discovery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.openocd_gdb import default_openocd_gdb_profile  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openocd-root", default="D:/openocd", help="Local OpenOCD install root.")
    parser.add_argument("--json", action="store_true", help="Print profile summary as JSON.")
    args = parser.parse_args(argv)

    profile = default_openocd_gdb_profile(args.openocd_root)

    _assert(profile.probe == "stlink", f"probe mismatch: {profile.probe!r}")
    _assert(profile.target == "stm32f4x", f"target mismatch: {profile.target!r}")
    _assert(profile.interface_name == "interface/stlink.cfg", f"interface mismatch: {profile.interface_name!r}")
    _assert(profile.target_name == "target/stm32f4x.cfg", f"target cfg mismatch: {profile.target_name!r}")
    _assert(profile.gdb_port == 3333, f"GDB port mismatch: {profile.gdb_port!r}")
    _assert(profile.telnet_port == 4444, f"telnet port mismatch: {profile.telnet_port!r}")
    _assert(profile.tcl_port == 6666, f"TCL port mismatch: {profile.tcl_port!r}")

    _assert(profile.openocd_path is not None and profile.openocd_path.exists(), f"openocd missing: {profile.openocd_path!r}")
    _assert(profile.openocd_path.name.lower() == "openocd.exe", f"openocd name mismatch: {profile.openocd_path!r}")
    _assert(profile.gdb_path is not None and profile.gdb_path.exists(), f"GDB missing: {profile.gdb_path!r}")
    _assert(profile.gdb_path.name.lower() == "arm-none-eabi-gdb.exe", f"GDB name mismatch: {profile.gdb_path!r}")
    _assert(profile.scripts_dir is not None and profile.scripts_dir.exists(), f"scripts missing: {profile.scripts_dir!r}")
    _assert(profile.interface_cfg is not None and profile.interface_cfg.exists(), f"interface cfg missing: {profile.interface_cfg!r}")
    _assert(profile.target_cfg is not None and profile.target_cfg.exists(), f"target cfg missing: {profile.target_cfg!r}")
    _assert(profile.ready_for_preview, f"profile should be preview-ready: {profile!r}")
    _assert(not profile.missing_tools, f"profile missing tools: {profile.missing_tools!r}")

    commands = profile.command_preview()
    _assert(any("openocd" in command.lower() for command in commands), f"OpenOCD preview missing: {commands!r}")
    _assert(any("arm-none-eabi-gdb" in command.lower() for command in commands), f"GDB preview missing: {commands!r}")
    _assert(any("target extended-remote" in command for command in commands), f"GDB remote preview missing: {commands!r}")
    _assert("不启动 OpenOCD" in profile.safety_note, f"safety note should stay read-only: {profile.safety_note!r}")
    _assert("不写目标" in profile.safety_note, f"safety note should block writes: {profile.safety_note!r}")

    rows = dict(profile.diagnostic_rows())
    _assert("OpenOCD 命令预览" in rows, f"diagnostics missing OpenOCD command: {rows!r}")
    _assert("GDB 命令预览" in rows, f"diagnostics missing GDB command: {rows!r}")
    _assert("安全边界" in rows, f"diagnostics missing safety row: {rows!r}")
    json.dumps(profile.to_summary_record(), ensure_ascii=False, sort_keys=True)

    if args.json:
        print(json.dumps(profile.to_summary_record(), ensure_ascii=False, indent=2))
    else:
        print("PASS OpenOCD/GDB profile probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
