"""Read-only pyOCD profile discovery.

The profile intentionally avoids `pyocd list --probes` or any other command
that would enumerate hardware. It only checks local executables, Python module
availability, and likely CMSIS-Pack cache locations.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TARGET_HINT = "stm32f401xc"


@dataclass(frozen=True)
class PyOcdToolAvailability:
    name: str
    path: Path | None

    @property
    def exists(self) -> bool:
        return bool(self.path and self.path.exists())

    @property
    def label(self) -> str:
        if self.path is None:
            return f"{self.name}: 未找到"
        state = "存在" if self.exists else "缺失"
        return f"{self.name}: {state} - {self.path}"


@dataclass(frozen=True)
class PyOcdPackLocation:
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def label(self) -> str:
        state = "存在" if self.exists else "缺失"
        return f"{state} - {self.path}"


@dataclass(frozen=True)
class PyOcdProfile:
    pyocd_path: Path | None
    module_origin: str
    pack_locations: tuple[PyOcdPackLocation, ...]
    target_hint: str = DEFAULT_TARGET_HINT
    gdb_port: int = 3333
    telnet_port: int = 4444
    warnings: tuple[str, ...] = ()

    @property
    def tools(self) -> tuple[PyOcdToolAvailability, ...]:
        return (PyOcdToolAvailability("pyocd", self.pyocd_path),)

    @property
    def missing_tools(self) -> tuple[str, ...]:
        missing: list[str] = [tool.name for tool in self.tools if not tool.exists]
        if not self.module_origin:
            missing.append("pyocd Python module")
        return tuple(missing)

    @property
    def has_python_module(self) -> bool:
        return bool(self.module_origin)

    @property
    def existing_pack_locations(self) -> tuple[Path, ...]:
        return tuple(item.path for item in self.pack_locations if item.exists)

    @property
    def ready_for_preview(self) -> bool:
        return bool((self.pyocd_path and self.pyocd_path.exists()) or self.has_python_module)

    @property
    def safety_note(self) -> str:
        return "只读档案：不运行 pyOCD，不枚举探针，不启动 GDB server，不写目标。"

    @property
    def executable_label(self) -> str:
        return str(self.pyocd_path) if self.pyocd_path else "pyocd"

    @property
    def gdbserver_command_preview(self) -> str:
        return " ".join(
            _quote(part)
            for part in (
                self.executable_label,
                "gdbserver",
                "--target",
                self.target_hint,
                "--port",
                self.gdb_port,
                "--telnet-port",
                self.telnet_port,
            )
        )

    @property
    def gdb_command_preview(self) -> str:
        return f"arm-none-eabi-gdb --interpreter=mi2 <elf> -ex {_quote(f'target extended-remote :{self.gdb_port}')}"

    def command_preview(self) -> tuple[str, ...]:
        return (
            self.gdbserver_command_preview,
            self.gdb_command_preview,
            "-break-insert <file>:<line>",
            "-exec-interrupt / -exec-continue / -exec-next",
            "pyOCD session read_memory/write_memory adapter",
        )

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        rows = [
            ("pyOCD", str(self.pyocd_path or "--")),
            ("pyOCD Python 模块", self.module_origin or "--"),
            ("目标提示", self.target_hint),
            ("GDB port", str(self.gdb_port)),
            ("Telnet port", str(self.telnet_port)),
            ("CMSIS-Pack 位置", "；".join(item.label for item in self.pack_locations) or "--"),
            ("pyOCD 工具", "；".join(tool.label for tool in self.tools)),
            ("pyOCD 命令预览", self.gdbserver_command_preview),
            ("GDB 命令预览", self.gdb_command_preview),
            ("安全边界", self.safety_note),
        ]
        if self.warnings:
            rows.append(("警告", "；".join(self.warnings)))
        return tuple(rows)

    def to_summary_record(self) -> dict[str, object]:
        return {
            "pyocd": str(self.pyocd_path or ""),
            "module_origin": self.module_origin,
            "target_hint": self.target_hint,
            "gdb_port": self.gdb_port,
            "telnet_port": self.telnet_port,
            "pack_locations": [str(item.path) for item in self.pack_locations],
            "existing_pack_locations": [str(item) for item in self.existing_pack_locations],
            "ready_for_preview": self.ready_for_preview,
            "safety_note": self.safety_note,
            "missing_tools": list(self.missing_tools),
            "warnings": list(self.warnings),
            "commands": list(self.command_preview()),
        }


def default_pyocd_profile(
    *,
    pyocd_path: str | Path | None = None,
    target_hint: str = DEFAULT_TARGET_HINT,
    gdb_port: int = 3333,
    telnet_port: int = 4444,
) -> PyOcdProfile:
    executable = _discover_pyocd(pyocd_path)
    module_origin = _discover_module_origin()
    packs = _default_pack_locations()
    warnings = _warnings(executable, module_origin, packs)
    return PyOcdProfile(
        pyocd_path=executable,
        module_origin=module_origin,
        pack_locations=packs,
        target_hint=str(target_hint or DEFAULT_TARGET_HINT),
        gdb_port=int(gdb_port),
        telnet_port=int(telnet_port),
        warnings=warnings,
    )


def _discover_pyocd(path: str | Path | None) -> Path | None:
    if path:
        candidate = Path(path).expanduser()
        return candidate.resolve() if candidate.exists() else candidate
    from_path = shutil.which("pyocd")
    if from_path:
        return Path(from_path).expanduser().resolve()
    return None


def _discover_module_origin() -> str:
    spec = importlib.util.find_spec("pyocd")
    origin = getattr(spec, "origin", None) if spec else None
    return str(origin or "")


def _default_pack_locations() -> tuple[PyOcdPackLocation, ...]:
    paths: list[Path] = [Path.home() / ".pyocd" / "packs"]
    local_appdata = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    if local_appdata:
        paths.append(Path(local_appdata) / "pyocd" / "packs")
    if appdata:
        paths.append(Path(appdata) / "pyocd" / "packs")
    seen: set[str] = set()
    unique: list[PyOcdPackLocation] = []
    for path in paths:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(PyOcdPackLocation(path))
    return tuple(unique)


def _warnings(
    executable: Path | None,
    module_origin: str,
    packs: tuple[PyOcdPackLocation, ...],
) -> tuple[str, ...]:
    warnings: list[str] = []
    if executable is None or not executable.exists():
        warnings.append("未找到 pyocd 命令，暂不能启动 pyOCD GDB server。")
    if not module_origin:
        warnings.append("未找到 pyocd Python 模块，暂不能使用 pyOCD Python session。")
    if not any(item.exists for item in packs):
        warnings.append("未找到 pyOCD CMSIS-Pack 缓存目录，具体芯片支持需要后续安装 pack。")
    return tuple(warnings)


def _quote(value: object) -> str:
    text = str(value)
    if not text:
        return '""'
    if any(char.isspace() for char in text):
        return f'"{text}"'
    return text
