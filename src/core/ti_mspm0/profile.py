"""Read-only TI MSPM0G3507 debugger profile discovery."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TI_ROOT = Path("D:/ti")


@dataclass(frozen=True)
class TiMemoryRange:
    name: str
    origin: int
    length: int
    access: str = ""
    source: str = ""

    @property
    def end(self) -> int:
        return self.origin + max(0, self.length) - 1

    @property
    def label(self) -> str:
        if self.length <= 0:
            text = f"{self.name} base 0x{self.origin:08X}"
        else:
            text = f"{self.name} 0x{self.origin:08X}-0x{self.end:08X}"
        if self.access:
            text += f" {self.access}"
        if self.source:
            text += f" ({self.source})"
        return text


@dataclass(frozen=True)
class TiToolAvailability:
    name: str
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def label(self) -> str:
        state = "存在" if self.exists else "缺失"
        return f"{self.name}: {state} - {self.path}"


@dataclass(frozen=True)
class TiMspm0DebugProfile:
    ti_root: Path
    ccs_root: Path
    sdk_root: Path
    sysconfig_root: Path
    device_xml: Path
    linker_cmd: Path
    syscfg_path: Path
    debug_server: Path
    dslite: Path
    gdb_agent: Path
    dss: Path
    compiler: Path
    device: str = ""
    description: str = ""
    cpu_id: str = ""
    cpu_description: str = ""
    default_toolchain: str = ""
    default_connection: str = ""
    access_port_designator: str = ""
    device_gel: str = ""
    dap_gel: str = ""
    ticlang_linker_cmd: str = ""
    ticlang_compiler_options: str = ""
    ticlang_linker_options: str = ""
    ticlang_startup_file: str = ""
    gnu_linker_cmd: str = ""
    gnu_compiler_options: str = ""
    is_elf_default: str = ""
    endianness: str = ""
    rtslib: str = ""
    min_codegen_version: str = ""
    syscfg_device: str = ""
    syscfg_package: str = ""
    syscfg_spin: str = ""
    linker_ranges: tuple[TiMemoryRange, ...] = ()
    targetdb_ranges: tuple[TiMemoryRange, ...] = ()
    key_peripherals: tuple[TiMemoryRange, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def tools(self) -> tuple[TiToolAvailability, ...]:
        return (
            TiToolAvailability("DebugServer", self.debug_server),
            TiToolAvailability("DSLite", self.dslite),
            TiToolAvailability("GDB Agent", self.gdb_agent),
            TiToolAvailability("DSS", self.dss),
            TiToolAvailability("TI ARM LLVM", self.compiler),
        )

    @property
    def missing_tools(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools if not tool.exists)

    @property
    def flash_range(self) -> TiMemoryRange | None:
        return _range_by_name(self.linker_ranges, "FLASH")

    @property
    def sram_range(self) -> TiMemoryRange | None:
        return _range_by_name(self.linker_ranges, "SRAM")

    @property
    def targetdb_sysmem_range(self) -> TiMemoryRange | None:
        return _range_by_name(self.targetdb_ranges, "sysmem")

    @property
    def safety_note(self) -> str:
        return "只读本地档案：不启动 CCS/DSLite，不枚举探针，不连接目标，不写芯片。"

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        rows = [
            ("TI 根目录", str(self.ti_root)),
            ("芯片", self.device or "--"),
            ("内核", self.cpu_description or self.cpu_id or "--"),
            ("默认连接", self.default_connection or "--"),
            ("默认工具链", self.default_toolchain or "--"),
            ("AP Designator", self.access_port_designator or "--"),
            ("SysConfig", _join_present(self.syscfg_device, self.syscfg_package, self.syscfg_spin)),
            ("FLASH", self.flash_range.label if self.flash_range else "--"),
            ("SRAM", self.sram_range.label if self.sram_range else "--"),
            ("targetdb SYSMEM", self.targetdb_sysmem_range.label if self.targetdb_sysmem_range else "--"),
            ("TI 工具", "；".join(tool.label for tool in self.tools)),
            ("安全边界", self.safety_note),
        ]
        if self.warnings:
            rows.append(("警告", "；".join(self.warnings)))
        return tuple(rows)

    def to_summary_record(self) -> dict[str, object]:
        return {
            "device": self.device,
            "description": self.description,
            "cpu": self.cpu_description or self.cpu_id,
            "default_toolchain": self.default_toolchain,
            "default_connection": self.default_connection,
            "access_port_designator": self.access_port_designator,
            "syscfg_device": self.syscfg_device,
            "syscfg_package": self.syscfg_package,
            "flash": self.flash_range.label if self.flash_range else "",
            "sram": self.sram_range.label if self.sram_range else "",
            "targetdb_sysmem": self.targetdb_sysmem_range.label if self.targetdb_sysmem_range else "",
            "missing_tools": list(self.missing_tools),
            "warnings": list(self.warnings),
        }


def default_ti_mspm0_profile(ti_root: str | Path = DEFAULT_TI_ROOT) -> TiMspm0DebugProfile:
    root = Path(ti_root).expanduser()
    ccs_root = root / "ccs2041" / "ccs"
    sdk_root = root / "mspm0_sdk_2_09_00_01"
    sysconfig_root = root / "sysconfig_1.27.1"
    device_xml = ccs_root / "ccs_base" / "common" / "targetdb" / "devices" / "MSPM0G3507.xml"
    linker_cmd = ccs_root / "ccs_base" / "arm" / "include" / "mspm0g3507.cmd"
    syscfg_path = (
        sdk_root
        / "examples"
        / "nortos"
        / "CUSTOM_BOARD"
        / "driverlib"
        / "empty_mspm0g3507"
        / "empty_mspm0g3507.syscfg"
    )
    debug_server = ccs_root / "ccs_base" / "DebugServer" / "bin" / "DSLite.exe"
    dslite = debug_server
    gdb_agent = ccs_root / "ccs_base" / "common" / "uscif" / "gdb_agent_console.exe"
    dss = ccs_root / "ccs_base" / "scripting" / "bin" / "dss.bat"
    compiler = ccs_root / "tools" / "compiler" / "ti-cgt-armllvm_4.0.4.LTS" / "bin" / "tiarmclang.exe"

    xml_data = _parse_targetdb_xml(device_xml)
    linker_ranges = _parse_linker_ranges(linker_cmd)
    syscfg_data = _parse_syscfg(syscfg_path)
    warnings = _profile_warnings(xml_data, linker_ranges)

    return TiMspm0DebugProfile(
        ti_root=root,
        ccs_root=ccs_root,
        sdk_root=sdk_root,
        sysconfig_root=sysconfig_root,
        device_xml=device_xml,
        linker_cmd=linker_cmd,
        syscfg_path=syscfg_path,
        debug_server=debug_server,
        dslite=dslite,
        gdb_agent=gdb_agent,
        dss=dss,
        compiler=compiler,
        device=xml_data.get("device", ""),
        description=xml_data.get("description", ""),
        cpu_id=xml_data.get("cpu_id", ""),
        cpu_description=xml_data.get("cpu_description", ""),
        default_toolchain=xml_data.get("DefaultToolChain", ""),
        default_connection=xml_data.get("DefaultConnection", ""),
        access_port_designator=xml_data.get("Access Port Designator", ""),
        device_gel=xml_data.get("device_gel", ""),
        dap_gel=xml_data.get("dap_gel", ""),
        ticlang_linker_cmd=xml_data.get("TICLANGLinkerCmd", ""),
        ticlang_compiler_options=xml_data.get("TICLANGCompilerBuildOptions", ""),
        ticlang_linker_options=xml_data.get("TICLANGLinkerBuildOptions", ""),
        ticlang_startup_file=xml_data.get("TICLANGFilesToCopy", ""),
        gnu_linker_cmd=xml_data.get("GNULinkerCmd", ""),
        gnu_compiler_options=xml_data.get("GNUCompilerBuildOptions", ""),
        is_elf_default=xml_data.get("IsElfDefault", ""),
        endianness=xml_data.get("Endianness", ""),
        rtslib=xml_data.get("RTSlib", ""),
        min_codegen_version=xml_data.get("MinCodegenVersion", ""),
        syscfg_device=syscfg_data.get("device", ""),
        syscfg_package=syscfg_data.get("package", ""),
        syscfg_spin=syscfg_data.get("spin", ""),
        linker_ranges=linker_ranges,
        targetdb_ranges=xml_data.get("targetdb_ranges", ()),
        key_peripherals=xml_data.get("key_peripherals", ()),
        warnings=warnings,
    )


def _parse_targetdb_xml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    root = ET.parse(path).getroot()
    properties = _targetdb_properties(root)
    cpu = _targetdb_cpu(root)
    ranges = _targetdb_ranges(root, ("flash", "sysmem"))
    peripherals = _targetdb_ranges(root, ("gpioa", "gpiob", "uart0", "uart1", "uart2", "uart3", "canfd0", "debugss"))
    return {
        "device": str(root.attrib.get("partnum") or root.attrib.get("id") or ""),
        "description": str(root.attrib.get("description") or root.attrib.get("desc") or ""),
        "cpu_id": str(cpu.attrib.get("id", "")) if cpu is not None else "",
        "cpu_description": str(cpu.attrib.get("description", "")) if cpu is not None else "",
        "device_gel": _last_property_value(root, "GEL File"),
        "dap_gel": _first_property_value(root, "GEL File"),
        "targetdb_ranges": ranges,
        "key_peripherals": peripherals,
        **properties,
    }


def _targetdb_properties(root: ET.Element) -> dict[str, str]:
    wanted = {
        "DefaultToolChain",
        "DefaultConnection",
        "Access Port Designator",
        "TICLANGLinkerCmd",
        "TICLANGCompilerBuildOptions",
        "TICLANGLinkerBuildOptions",
        "TICLANGFilesToCopy",
        "GNULinkerCmd",
        "GNUCompilerBuildOptions",
        "IsElfDefault",
        "Endianness",
        "RTSlib",
        "MinCodegenVersion",
    }
    values: dict[str, str] = {}
    for item in root.findall(".//property"):
        key = str(item.attrib.get("id", ""))
        if key in wanted and key not in values:
            values[key] = str(item.attrib.get("Value", ""))
    return values


def _targetdb_cpu(root: ET.Element) -> ET.Element | None:
    for item in root.findall(".//cpu"):
        if str(item.attrib.get("id", "")).upper() == "CORTEX_M0P":
            return item
    return None


def _targetdb_ranges(root: ET.Element, ids: tuple[str, ...]) -> tuple[TiMemoryRange, ...]:
    wanted = {item.lower() for item in ids}
    ranges: list[TiMemoryRange] = []
    for item in root.findall(".//instance"):
        key = str(item.attrib.get("id", "")).lower()
        if key not in wanted:
            continue
        origin = _parse_int(item.attrib.get("baseaddr"))
        if origin is None:
            continue
        ranges.append(
            TiMemoryRange(
                name=key,
                origin=origin,
                length=0,
                access=str(item.attrib.get("permissions", "")),
                source="targetdb base",
            )
        )
    return tuple(ranges)


def _parse_linker_ranges(path: Path) -> tuple[TiMemoryRange, ...]:
    if not path.exists():
        return ()
    text = path.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(
        r"^\s*(?P<name>[A-Za-z0-9_]+)\s+\((?P<access>[^)]+)\)\s*:\s*origin\s*=\s*(?P<origin>0x[0-9A-Fa-f]+)\s*,\s*length\s*=\s*(?P<length>0x[0-9A-Fa-f]+)",
        re.MULTILINE,
    )
    ranges = []
    for match in pattern.finditer(text):
        ranges.append(
            TiMemoryRange(
                name=match.group("name"),
                origin=int(match.group("origin"), 16),
                length=int(match.group("length"), 16),
                access=match.group("access"),
                source="linker",
            )
        )
    return tuple(ranges)


def _parse_syscfg(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    result: dict[str, str] = {}
    args = re.search(r"//@v2CliArgs\s+--device\s+\"([^\"]+)\"\s+--package\s+\"([^\"]+)\"", text)
    if args:
        result["device"] = args.group(1)
        result["package"] = args.group(2)
    spin = re.search(r"ProjectConfig\.deviceSpin\s*=\s*\"([^\"]+)\"", text)
    if spin:
        result["spin"] = spin.group(1)
    return result


def _profile_warnings(xml_data: dict[str, object], linker_ranges: tuple[TiMemoryRange, ...]) -> tuple[str, ...]:
    warnings: list[str] = []
    sysmem = _range_by_name(xml_data.get("targetdb_ranges", ()), "sysmem")
    sram = _range_by_name(linker_ranges, "SRAM")
    if sysmem is not None and sram is not None and sysmem.origin != sram.origin:
        warnings.append(
            "targetdb SYSMEM 起始地址与 linker SRAM 起始地址不同，真实写变量前需要确认调试地址别名。"
        )
    if str(xml_data.get("DefaultConnection", "")) != "TIXDS110_Connection.xml":
        warnings.append("默认连接不是 TIXDS110，需要人工复核 TI 调试器配置。")
    return tuple(warnings)


def _range_by_name(ranges: object, name: str) -> TiMemoryRange | None:
    wanted = str(name).lower()
    for item in ranges or ():
        if isinstance(item, TiMemoryRange) and item.name.lower() == wanted:
            return item
    return None


def _first_property_value(root: ET.Element, property_id: str) -> str:
    for item in root.findall(".//property"):
        if str(item.attrib.get("id", "")) == property_id:
            return str(item.attrib.get("Value", ""))
    return ""


def _last_property_value(root: ET.Element, property_id: str) -> str:
    value = ""
    for item in root.findall(".//property"):
        if str(item.attrib.get("id", "")) == property_id:
            value = str(item.attrib.get("Value", ""))
    return value


def _parse_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


def _join_present(*items: str) -> str:
    values = [str(item) for item in items if str(item)]
    return " / ".join(values) if values else "--"
