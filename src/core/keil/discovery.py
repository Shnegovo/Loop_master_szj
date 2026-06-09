"""Read-only Keil/uVision discovery for future UVSOCK integration."""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


IMPORTANT_UVSC_EXPORTS = (
    "UVSC_OpenConnection",
    "UVSC_CloseConnection",
    "UVSC_DBG_ENTER",
    "UVSC_DBG_EXIT",
    "UVSC_DBG_EXEC_CMD",
    "UVSC_DBG_ENUM_VARIABLES",
    "UVSC_DBG_VARIABLE_SET",
    "UVSC_DBG_EVAL_EXPRESSION_TO_STR",
    "UVSC_DBG_MEM_READ",
    "UVSC_DBG_MEM_WRITE",
    "UVSC_DBG_START_EXECUTION",
    "UVSC_DBG_STOP_EXECUTION",
    "UVSC_DBG_RESET",
)


PE_MACHINE_NAMES = {
    0x014C: "x86",
    0x8664: "x64",
    0xAA64: "arm64",
}


@dataclass(frozen=True)
class KeilFile:
    path: Path
    exists: bool
    size: int = 0
    machine: str = ""

    @classmethod
    def from_path(cls, path: Path) -> "KeilFile":
        try:
            stat = path.stat()
        except OSError:
            return cls(path=path, exists=False)
        machine = pe_machine_name(path) if path.suffix.lower() in {".dll", ".exe"} else ""
        return cls(path=path, exists=True, size=int(stat.st_size), machine=machine)


@dataclass(frozen=True)
class KeilDiscovery:
    root: Path | None
    uv4_dir: Path | None
    uv4_exe: KeilFile | None
    uvision_com: KeilFile | None
    uvsc_dll: KeilFile | None
    uvsc64_dll: KeilFile | None
    uvsc_wrapper_dll: KeilFile | None
    docs: tuple[KeilFile, ...]
    python_bits: int
    exports: dict[str, tuple[str, ...]]

    @property
    def installed(self) -> bool:
        return bool(self.uv4_dir and self.uv4_exe and self.uv4_exe.exists)

    @property
    def preferred_uvsc(self) -> KeilFile | None:
        if self.python_bits >= 64 and self.uvsc64_dll and self.uvsc64_dll.exists:
            return self.uvsc64_dll
        if self.uvsc_dll and self.uvsc_dll.exists:
            return self.uvsc_dll
        return self.uvsc64_dll or self.uvsc_dll

    @property
    def preferred_exports(self) -> tuple[str, ...]:
        preferred = self.preferred_uvsc
        if preferred is None:
            return ()
        return self.exports.get(preferred.path.name, ())

    @property
    def missing_important_exports(self) -> tuple[str, ...]:
        exported = set(self.preferred_exports)
        return tuple(name for name in IMPORTANT_UVSC_EXPORTS if name not in exported)

    def capability_flags(self) -> dict[str, bool]:
        exported = set(self.preferred_exports)
        return {
            "has_uv4": bool(self.uv4_exe and self.uv4_exe.exists),
            "has_uvision_cli": bool(self.uvision_com and self.uvision_com.exists),
            "has_uvsock_dll": bool(self.preferred_uvsc and self.preferred_uvsc.exists),
            "can_open_connection": "UVSC_OpenConnection" in exported,
            "can_enter_debug": "UVSC_DBG_ENTER" in exported,
            "can_exec_command": "UVSC_DBG_EXEC_CMD" in exported,
            "can_eval_expression": "UVSC_DBG_EVAL_EXPRESSION_TO_STR" in exported,
            "can_enum_variables": "UVSC_DBG_ENUM_VARIABLES" in exported,
            "can_read_memory": "UVSC_DBG_MEM_READ" in exported,
            "can_write_memory": "UVSC_DBG_MEM_WRITE" in exported,
            "can_control_target": {
                "UVSC_DBG_START_EXECUTION",
                "UVSC_DBG_STOP_EXECUTION",
                "UVSC_DBG_RESET",
            }.issubset(exported),
        }

    def report_lines(self) -> list[str]:
        lines = [
            f"Keil root: {self.root or '--'}",
            f"UV4 dir: {self.uv4_dir or '--'}",
            f"Python bits: {self.python_bits}",
        ]
        for label, item in (
            ("UV4.exe", self.uv4_exe),
            ("uVision.com", self.uvision_com),
            ("UVSC.dll", self.uvsc_dll),
            ("UVSC64.dll", self.uvsc64_dll),
            ("UVSCWrapper.dll", self.uvsc_wrapper_dll),
        ):
            if item is None:
                lines.append(f"{label}: --")
            else:
                machine = f", machine={item.machine}" if item.machine else ""
                lines.append(f"{label}: {'OK' if item.exists else 'missing'} {item.path}{machine}")
        preferred = self.preferred_uvsc
        lines.append(f"Preferred UVSOCK DLL: {preferred.path if preferred else '--'}")
        lines.append(f"Preferred export count: {len(self.preferred_exports)}")
        missing = self.missing_important_exports
        lines.append("Important exports: OK" if not missing else f"Important exports missing: {', '.join(missing)}")
        return lines


def discover_keil(root: str | os.PathLike[str] | None = None) -> KeilDiscovery:
    uv4_dir = _find_uv4_dir(root)
    if uv4_dir is None:
        return KeilDiscovery(
            root=Path(root).expanduser() if root else None,
            uv4_dir=None,
            uv4_exe=None,
            uvision_com=None,
            uvsc_dll=None,
            uvsc64_dll=None,
            uvsc_wrapper_dll=None,
            docs=(),
            python_bits=_python_bits(),
            exports={},
        )

    root_path = _infer_root_from_uv4(uv4_dir)
    uv4_exe = KeilFile.from_path(uv4_dir / "UV4.exe")
    uvision_com = KeilFile.from_path(uv4_dir / "uVision.com")
    uvsc_dll = KeilFile.from_path(uv4_dir / "UVSC.dll")
    uvsc64_dll = KeilFile.from_path(uv4_dir / "UVSC64.dll")
    uvsc_wrapper_dll = KeilFile.from_path(uv4_dir / "UVSCWrapper.dll")
    docs = tuple(
        KeilFile.from_path(path)
        for path in sorted(uv4_dir.glob("*.chm"))
        if path.name.lower() != "tools.ini"
    )
    exports = {}
    for item in (uvsc_dll, uvsc64_dll, uvsc_wrapper_dll):
        if item.exists:
            exports[item.path.name] = read_pe_exports(item.path)

    return KeilDiscovery(
        root=root_path,
        uv4_dir=uv4_dir,
        uv4_exe=uv4_exe,
        uvision_com=uvision_com,
        uvsc_dll=uvsc_dll,
        uvsc64_dll=uvsc64_dll,
        uvsc_wrapper_dll=uvsc_wrapper_dll,
        docs=docs,
        python_bits=_python_bits(),
        exports=exports,
    )


def candidate_keil_roots(root: str | os.PathLike[str] | None = None) -> tuple[Path, ...]:
    candidates: list[Path] = []
    if root:
        candidates.append(Path(root).expanduser())
        return _unique_paths(candidates)
    env_root = os.environ.get("LOOPMASTER_KEIL_ROOT") or os.environ.get("KEIL_ROOT")
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.extend(
        [
            Path("D:/Keil/Keil_v5"),
            Path("D:/Keil"),
            Path("C:/Keil_v5"),
            Path("C:/Keil"),
            Path("C:/Program Files/Keil_v5"),
            Path("C:/Program Files (x86)/Keil_v5"),
        ]
    )
    return _unique_paths(candidates)


def read_pe_exports(path: str | os.PathLike[str]) -> tuple[str, ...]:
    data = Path(path).read_bytes()
    if len(data) < 0x40 or data[:2] != b"MZ":
        return ()

    pe_offset = _u32(data, 0x3C)
    if pe_offset <= 0 or pe_offset + 24 > len(data) or data[pe_offset:pe_offset + 4] != b"PE\0\0":
        return ()

    file_header = pe_offset + 4
    section_count = _u16(data, file_header + 2)
    optional_size = _u16(data, file_header + 16)
    optional = file_header + 20
    if optional + optional_size > len(data):
        return ()

    magic = _u16(data, optional)
    data_directory = optional + (112 if magic == 0x20B else 96 if magic == 0x10B else -1)
    if data_directory < optional or data_directory + 8 > len(data):
        return ()

    export_rva = _u32(data, data_directory)
    export_size = _u32(data, data_directory + 4)
    if export_rva == 0 or export_size == 0:
        return ()

    sections = _read_sections(data, optional + optional_size, section_count)
    export_offset = _rva_to_offset(export_rva, sections)
    if export_offset is None or export_offset + 40 > len(data):
        return ()

    name_count = _u32(data, export_offset + 24)
    names_rva = _u32(data, export_offset + 32)
    names_offset = _rva_to_offset(names_rva, sections)
    if names_offset is None:
        return ()

    names = []
    for index in range(name_count):
        entry_offset = names_offset + index * 4
        if entry_offset + 4 > len(data):
            break
        name_rva = _u32(data, entry_offset)
        name_offset = _rva_to_offset(name_rva, sections)
        if name_offset is None:
            continue
        name = _read_c_string(data, name_offset)
        if name:
            names.append(name)
    return tuple(sorted(set(names)))


def pe_machine_name(path: str | os.PathLike[str]) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    if len(data) < 0x40 or data[:2] != b"MZ":
        return ""
    pe_offset = _u32(data, 0x3C)
    if pe_offset <= 0 or pe_offset + 8 > len(data) or data[pe_offset:pe_offset + 4] != b"PE\0\0":
        return ""
    machine = _u16(data, pe_offset + 4)
    return PE_MACHINE_NAMES.get(machine, f"0x{machine:04x}")


def _find_uv4_dir(root: str | os.PathLike[str] | None) -> Path | None:
    for candidate in candidate_keil_roots(root):
        for uv4_dir in _uv4_dir_candidates(candidate):
            try:
                if (uv4_dir / "UV4.exe").is_file():
                    return uv4_dir.resolve()
            except OSError:
                continue
    return None


def _uv4_dir_candidates(candidate: Path) -> Iterable[Path]:
    yield candidate
    yield candidate / "UV4"
    yield candidate / "Keil_v5" / "UV4"


def _infer_root_from_uv4(uv4_dir: Path) -> Path:
    parent = uv4_dir.parent
    if uv4_dir.name.lower() == "uv4":
        return parent
    return uv4_dir


def _read_sections(data: bytes, offset: int, count: int) -> tuple[tuple[int, int, int], ...]:
    sections = []
    for index in range(count):
        section_offset = offset + index * 40
        if section_offset + 40 > len(data):
            break
        virtual_size = _u32(data, section_offset + 8)
        virtual_address = _u32(data, section_offset + 12)
        raw_size = _u32(data, section_offset + 16)
        raw_pointer = _u32(data, section_offset + 20)
        size = max(virtual_size, raw_size)
        sections.append((virtual_address, size, raw_pointer))
    return tuple(sections)


def _rva_to_offset(rva: int, sections: tuple[tuple[int, int, int], ...]) -> int | None:
    for virtual_address, size, raw_pointer in sections:
        if virtual_address <= rva < virtual_address + size:
            return raw_pointer + (rva - virtual_address)
    return None


def _read_c_string(data: bytes, offset: int) -> str:
    end = data.find(b"\0", offset)
    if end < 0:
        return ""
    try:
        return data[offset:end].decode("ascii")
    except UnicodeDecodeError:
        return ""


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    unique = []
    seen = set()
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return tuple(unique)


def _python_bits() -> int:
    return struct.calcsize("P") * 8


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]
