"""Keil .uvoptx debug-adapter option parsing."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KeilDebugOptionsSummary:
    project_path: Path
    options_path: Path | None
    target_name: str = ""
    exists: bool = False
    device: str = ""
    vendor: str = ""
    pack_id: str = ""
    svd_file: str = ""
    invalid_flash: bool = False
    toolset_name: str = ""
    run_target: bool | None = None
    target_debug: bool | None = None
    target_selector: str = ""
    monitor_dll: str = ""
    registry_key: str = ""
    registry_options: str = ""
    protocol: str = ""
    debug_clock_hz: int | None = None
    flash_algorithm: str = ""
    flash_start: int | None = None
    flash_length: int | None = None
    ram_start: int | None = None
    ram_length: int | None = None
    warnings: tuple[str, ...] = ()

    @property
    def adapter_label(self) -> str:
        text = f"{self.monitor_dll} {self.registry_key} {self.registry_options}".lower()
        if "st-link" in text or "stlink" in text:
            return "ST-Link"
        if self.registry_key:
            return self.registry_key
        if self.monitor_dll:
            return Path(self.monitor_dll.replace("\\", "/")).stem
        return "--"

    @property
    def protocol_label(self) -> str:
        if self.protocol == "2":
            return "SWD"
        if self.protocol == "1":
            return "JTAG"
        return f"Protocol {self.protocol}" if self.protocol else "--"

    @property
    def debug_clock_label(self) -> str:
        if self.debug_clock_hz is None:
            return "--"
        value = int(self.debug_clock_hz)
        if value >= 1_000_000 and value % 1_000_000 == 0:
            return f"{value // 1_000_000} MHz"
        if value >= 1_000:
            return f"{value / 1_000:.1f} kHz"
        return f"{value} Hz"

    @property
    def flash_range_label(self) -> str:
        if self.flash_start is None or self.flash_length is None:
            return "--"
        return f"0x{self.flash_start:08X}+0x{self.flash_length:X}"

    @property
    def ram_range_label(self) -> str:
        if self.ram_start is None or self.ram_length is None:
            return "--"
        return f"0x{self.ram_start:08X}+0x{self.ram_length:X}"

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        if not self.exists:
            return (("调试配置", "未找到 .uvoptx"),)
        rows = [
            ("调试配置", str(self.options_path or "--")),
            ("芯片", self.device or "--"),
            ("调试器", self.adapter_label),
            ("调试接口", self.protocol_label),
            ("调试时钟", self.debug_clock_label),
            ("调试 DLL", self.monitor_dll or "--"),
            ("Flash 算法", self.flash_algorithm or "--"),
            ("Flash 范围", self.flash_range_label),
            ("RAM 范围", self.ram_range_label),
            ("SVD", self.svd_file or "--"),
        ]
        if self.invalid_flash:
            rows.append(("Flash 配置", "Keil 标记 InvalidFlash=1"))
        if self.warnings:
            rows.append(("调试配置警告", "；".join(self.warnings)))
        return tuple(rows)


def parse_keil_debug_options(
    project_path: str | Path,
    target_name: str = "",
) -> KeilDebugOptionsSummary:
    project = Path(project_path).expanduser().resolve()
    options_path = _options_path_for_project(project)
    project_data = _parse_project_debug_bits(project, target_name)
    if options_path is None or not options_path.exists():
        return KeilDebugOptionsSummary(
            project_path=project,
            options_path=options_path,
            target_name=target_name,
            exists=False,
            **project_data,
            warnings=tuple(_warnings(project_data, {})),
        )
    try:
        root = ET.parse(options_path).getroot()
    except Exception as exc:
        data = dict(project_data)
        data["warnings"] = tuple([*list(_warnings(project_data, {})), f".uvoptx 解析失败：{exc}"])
        return KeilDebugOptionsSummary(
            project_path=project,
            options_path=options_path,
            target_name=target_name,
            exists=False,
            **data,
        )

    target = _select_target_node(root.findall(".//Target"), target_name)
    opt_data = _parse_option_target(target)
    combined = {**project_data, **opt_data}
    combined["warnings"] = tuple(_warnings(project_data, opt_data))
    return KeilDebugOptionsSummary(
        project_path=project,
        options_path=options_path,
        target_name=combined.get("target_name") or target_name,
        exists=True,
        **{key: value for key, value in combined.items() if key != "target_name"},
    )


def _options_path_for_project(project_path: Path) -> Path | None:
    if project_path.suffix.lower() in {".uvprojx", ".uvproj"}:
        candidate = project_path.with_suffix(".uvoptx")
        if candidate.exists():
            return candidate.resolve()
        legacy = project_path.with_suffix(".uvopt")
        return legacy.resolve()
    return None


def _parse_project_debug_bits(project_path: Path, target_name: str) -> dict[str, object]:
    try:
        root = ET.parse(project_path).getroot()
    except Exception:
        return {}
    target = _select_target_node(root.findall(".//Target"), target_name)
    if target is None:
        return {}
    return {
        "target_name": _text(target, "TargetName") or target_name,
        "device": _text(target, ".//TargetCommonOption/Device"),
        "vendor": _text(target, ".//TargetCommonOption/Vendor"),
        "pack_id": _text(target, ".//TargetCommonOption/PackID"),
        "svd_file": _text(target, ".//TargetCommonOption/SFDFile"),
        "invalid_flash": _text(target, ".//TargetCommonOption/TargetStatus/InvalidFlash") == "1",
    }


def _parse_option_target(target: ET.Element | None) -> dict[str, object]:
    if target is None:
        return {}
    registry = _registry_entry(target)
    registry_options = registry[1]
    flash_start = _hex_option(registry_options, "FS")
    flash_length = _hex_option(registry_options, "FL")
    ram_start = _hex_option(registry_options, "FD")
    ram_length = _hex_option(registry_options, "FC")
    return {
        "target_name": _text(target, "TargetName"),
        "toolset_name": _text(target, "ToolsetName"),
        "run_target": _bool_text(_text(target, ".//OPTTT/RunTarget")),
        "target_debug": _bool_text(_text(target, ".//DebugOpt/uTrg")),
        "target_selector": _text(target, ".//DebugOpt/nTsel"),
        "monitor_dll": _text(target, ".//DebugOpt/pMon"),
        "registry_key": registry[0],
        "registry_options": registry_options,
        "protocol": _text(target, ".//DebugDescription/Protocol") or _option_value(registry_options, "P"),
        "debug_clock_hz": _int_text(_text(target, ".//DebugDescription/DbgClock")) or _decimal_option(registry_options, "TC"),
        "flash_algorithm": _flash_algorithm(registry_options),
        "flash_start": flash_start,
        "flash_length": flash_length,
        "ram_start": ram_start,
        "ram_length": ram_length,
    }


def _select_target_node(nodes: list[ET.Element], target_name: str) -> ET.Element | None:
    if target_name:
        for node in nodes:
            if _text(node, "TargetName") == target_name:
                return node
    return nodes[0] if nodes else None


def _registry_entry(target: ET.Element) -> tuple[str, str]:
    entries = target.findall(".//TargetDriverDllRegistry/SetRegEntry")
    preferred: tuple[str, str] | None = None
    fallback: tuple[str, str] | None = None
    for entry in entries:
        key = _text(entry, "Key")
        name = _text(entry, "Name")
        pair = (key, name)
        if fallback is None:
            fallback = pair
        if "st-link" in key.lower() or "stlink" in key.lower() or "st-link" in name.lower():
            preferred = pair
            break
    return preferred or fallback or ("", "")


def _flash_algorithm(text: str) -> str:
    matches = re.findall(r"([A-Za-z0-9_+\-.]+\.FLM)", text)
    return matches[-1] if matches else ""


def _option_value(text: str, key: str) -> str:
    match = re.search(rf"(?:^|\s)-{re.escape(key)}([A-Za-z0-9]+)", text)
    return match.group(1) if match else ""


def _decimal_option(text: str, key: str) -> int | None:
    value = _option_value(text, key)
    if not value:
        return None
    try:
        return int(value, 10)
    except ValueError:
        return None


def _hex_option(text: str, key: str) -> int | None:
    value = _option_value(text, key)
    if not value:
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def _warnings(project_data: dict[str, object], opt_data: dict[str, object]) -> list[str]:
    warnings: list[str] = []
    if project_data.get("invalid_flash"):
        warnings.append("Keil 标记 InvalidFlash=1，需在 uVision 中确认 Flash 配置")
    device = str(project_data.get("device") or "")
    flash_algorithm = str(opt_data.get("flash_algorithm") or "")
    flash_length = opt_data.get("flash_length")
    if "STM32F103C8" in device and (
        "128" in flash_algorithm or (isinstance(flash_length, int) and flash_length > 0x10000)
    ):
        warnings.append("Device 为 STM32F103C8，但 Flash 算法/范围看起来是 128KB 配置")
    return warnings


def _text(node: ET.Element | None, path: str) -> str:
    if node is None:
        return ""
    found = node.find(path)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _bool_text(text: str) -> bool | None:
    if text == "":
        return None
    return text == "1"


def _int_text(text: str) -> int | None:
    if not text:
        return None
    try:
        return int(text, 10)
    except ValueError:
        return None
