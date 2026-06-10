"""Validate the F401 Keil live-write probe debug configuration."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PROJECT = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
OPTIONS = PROJECT.with_suffix(".uvoptx")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _text(node: ET.Element, path: str) -> str:
    found = node.find(path)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _target(root: ET.Element) -> ET.Element:
    target = root.find(".//Target")
    _assert(target is not None, "Keil target is missing")
    return target


def main() -> int:
    _assert(PROJECT.exists(), f"missing project: {PROJECT}")
    _assert(OPTIONS.exists(), f"missing option file: {OPTIONS}")

    project_target = _target(ET.parse(PROJECT).getroot())
    option_root = ET.parse(OPTIONS).getroot()
    option_target = _target(option_root)

    _assert(_text(project_target, "TargetName") == "STM32F401CCU6 Variable Probe", "unexpected target name")
    _assert(_text(project_target, ".//TargetCommonOption/Device") == "STM32F401CCUx", "unexpected device")
    _assert("STM32F4xx_DFP" in _text(project_target, ".//TargetCommonOption/PackID"), "unexpected Keil pack")
    _assert(
        _text(project_target, ".//TargetCommonOption/SFDFile").endswith(r"CMSIS\SVD\STM32F401.svd"),
        "F401 SVD path is missing",
    )

    _assert(_text(option_target, "TargetName") == "STM32F401CCU6 Variable Probe", "option target mismatch")
    _assert(
        _text(option_target, ".//DebugOpt/pMon") == r"STLink\ST-LINKIII-KEIL_SWO.dll",
        "Keil monitor is not ST-Link",
    )
    _assert(_text(option_target, ".//DebugOpt/nTsel") == "6", "ST-Link target selector is not the local F401 value")
    _assert(_text(option_target, ".//DebugDescription/Protocol") == "2", "debug protocol is not SWD")
    _assert(_text(option_target, ".//DebugDescription/DbgClock") == "10000000", "debug clock mismatch")

    entries = option_target.findall(".//TargetDriverDllRegistry/SetRegEntry")
    registry = {_text(entry, "Key"): _text(entry, "Name") for entry in entries}
    stlink = registry.get("ST-LINKIII-KEIL_SWO", "")
    _assert(stlink, "ST-Link registry entry is missing")
    _assert("STM32F4xx_256.FLM" in stlink, "ST-Link entry does not use the F401/F4 flash algorithm")
    _assert("STM32F103" not in stlink and "STM32F10x" not in stlink, "F103 flash algorithm leaked into F401 config")
    _assert("-FS08000000" in stlink and "-FL040000" in stlink, "F401 flash base/length mismatch")

    option_files = {
        _text(file_node, "PathWithFileName").replace("/", "\\")
        for file_node in option_root.findall("./Group/File")
    }
    for expected in (r".\main.c", r".\startup_stm32f401ccux.s", r".\f401_variable_probe.sct"):
        _assert(expected in option_files, f"option file entry missing: {expected}")

    print("PASS keil F401 debug config uses ST-Link/SWD with STM32F4xx_256 flash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
