"""Probe read-only pyOCD environment discovery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.pyocd import default_pyocd_profile  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="stm32f401xc", help="pyOCD target hint.")
    parser.add_argument("--json", action="store_true", help="Print profile summary as JSON.")
    args = parser.parse_args(argv)

    profile = default_pyocd_profile(target_hint=args.target)

    _assert(profile.target_hint == args.target, f"target hint mismatch: {profile.target_hint!r}")
    _assert(profile.gdb_port == 3333, f"GDB port mismatch: {profile.gdb_port!r}")
    _assert(profile.telnet_port == 4444, f"telnet port mismatch: {profile.telnet_port!r}")
    _assert(profile.pack_locations, "pack locations should be recorded")
    _assert("不运行 pyOCD" in profile.safety_note, f"safety note should block execution: {profile.safety_note!r}")
    _assert("不枚举探针" in profile.safety_note, f"safety note should block probe enumeration: {profile.safety_note!r}")
    _assert("不写目标" in profile.safety_note, f"safety note should block target writes: {profile.safety_note!r}")

    commands = profile.command_preview()
    _assert(any("pyocd" in command.lower() for command in commands), f"pyOCD preview missing: {commands!r}")
    _assert(any("gdbserver" in command.lower() for command in commands), f"GDB server preview missing: {commands!r}")
    _assert(any("target extended-remote" in command for command in commands), f"GDB remote preview missing: {commands!r}")
    _assert(any(args.target in command for command in commands), f"target hint missing from commands: {commands!r}")

    rows = dict(profile.diagnostic_rows())
    _assert("pyOCD 命令预览" in rows, f"diagnostics missing pyOCD command: {rows!r}")
    _assert("GDB 命令预览" in rows, f"diagnostics missing GDB command: {rows!r}")
    _assert("安全边界" in rows, f"diagnostics missing safety row: {rows!r}")

    if profile.pyocd_path is None or not profile.pyocd_path.exists():
        _assert("pyocd" in profile.missing_tools, f"missing executable should be recorded: {profile.missing_tools!r}")
        _assert(any("未找到 pyocd 命令" in warning for warning in profile.warnings), f"missing pyocd warning absent: {profile.warnings!r}")
    if not profile.has_python_module:
        _assert("pyocd Python module" in profile.missing_tools, f"missing module should be recorded: {profile.missing_tools!r}")
        _assert(any("未找到 pyocd Python 模块" in warning for warning in profile.warnings), f"missing module warning absent: {profile.warnings!r}")

    json.dumps(profile.to_summary_record(), ensure_ascii=False, sort_keys=True)

    if args.json:
        print(json.dumps(profile.to_summary_record(), ensure_ascii=False, indent=2))
    else:
        print("PASS pyOCD profile probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
