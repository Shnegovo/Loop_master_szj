"""Read-only OpenOCD/GDB profile discovery."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


DEFAULT_OPENOCD_ROOTS = (
    Path("D:/openocd/xpack-openocd-0.12.0-7"),
    Path("D:/openocd"),
    Path("C:/OpenOCD"),
    Path("C:/Program Files/OpenOCD"),
)
DEFAULT_ST_GDB = Path("C:/ST/STM32CubeCLT_1.18.0/GNU-tools-for-STM32/bin/arm-none-eabi-gdb.exe")


@dataclass(frozen=True)
class OpenOcdToolAvailability:
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
class OpenOcdGdbProfile:
    openocd_path: Path | None
    gdb_path: Path | None
    scripts_dir: Path | None
    interface_cfg: Path | None
    target_cfg: Path | None
    probe: str = "stlink"
    target: str = "stm32f4x"
    gdb_port: int = 3333
    telnet_port: int = 4444
    tcl_port: int = 6666
    warnings: tuple[str, ...] = ()

    @property
    def tools(self) -> tuple[OpenOcdToolAvailability, ...]:
        return (
            OpenOcdToolAvailability("OpenOCD", self.openocd_path),
            OpenOcdToolAvailability("arm-none-eabi-gdb", self.gdb_path),
        )

    @property
    def missing_tools(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools if not tool.exists)

    @property
    def interface_name(self) -> str:
        return f"interface/{self.probe}.cfg"

    @property
    def target_name(self) -> str:
        return f"target/{self.target}.cfg"

    @property
    def ready_for_preview(self) -> bool:
        return bool(
            self.openocd_path
            and self.openocd_path.exists()
            and self.gdb_path
            and self.gdb_path.exists()
            and self.scripts_dir
            and self.scripts_dir.exists()
            and self.interface_cfg
            and self.interface_cfg.exists()
            and self.target_cfg
            and self.target_cfg.exists()
        )

    @property
    def safety_note(self) -> str:
        return "只读档案：不启动 OpenOCD，不连接 GDB，不枚举探针，不写目标。"

    @property
    def openocd_command_preview(self) -> str:
        executable = str(self.openocd_path) if self.openocd_path else "openocd"
        parts = [executable]
        if self.scripts_dir is not None:
            parts.extend(["-s", str(self.scripts_dir)])
        parts.extend(
            [
                "-f",
                self.interface_name,
                "-f",
                self.target_name,
                "-c",
                f"gdb_port {self.gdb_port}",
                "-c",
                f"telnet_port {self.telnet_port}",
                "-c",
                f"tcl_port {self.tcl_port}",
            ]
        )
        return " ".join(_quote(part) for part in parts)

    @property
    def gdb_command_preview(self) -> str:
        executable = str(self.gdb_path) if self.gdb_path else "arm-none-eabi-gdb"
        return " ".join(
            _quote(part)
            for part in (
                executable,
                "--interpreter=mi2",
                "<elf>",
                "-ex",
                f"target extended-remote :{self.gdb_port}",
            )
        )

    def command_preview(self) -> tuple[str, ...]:
        return (
            self.openocd_command_preview,
            self.gdb_command_preview,
            "-break-insert <file>:<line>",
            "-exec-interrupt / -exec-continue / -exec-next",
            "-data-evaluate-expression <symbol>",
        )

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        rows = [
            ("OpenOCD", str(self.openocd_path or "--")),
            ("GDB", str(self.gdb_path or "--")),
            ("OpenOCD scripts", str(self.scripts_dir or "--")),
            ("OpenOCD interface", self.interface_name),
            ("OpenOCD target", self.target_name),
            ("GDB port", str(self.gdb_port)),
            ("OpenOCD 工具", "；".join(tool.label for tool in self.tools)),
            ("OpenOCD 命令预览", self.openocd_command_preview),
            ("GDB 命令预览", self.gdb_command_preview),
            ("安全边界", self.safety_note),
        ]
        if self.warnings:
            rows.append(("警告", "；".join(self.warnings)))
        return tuple(rows)

    def to_summary_record(self) -> dict[str, object]:
        return {
            "openocd": str(self.openocd_path or ""),
            "gdb": str(self.gdb_path or ""),
            "scripts_dir": str(self.scripts_dir or ""),
            "interface": self.interface_name,
            "interface_cfg": str(self.interface_cfg or ""),
            "target": self.target_name,
            "target_cfg": str(self.target_cfg or ""),
            "gdb_port": self.gdb_port,
            "telnet_port": self.telnet_port,
            "tcl_port": self.tcl_port,
            "ready_for_preview": self.ready_for_preview,
            "safety_note": self.safety_note,
            "missing_tools": list(self.missing_tools),
            "warnings": list(self.warnings),
            "commands": list(self.command_preview()),
        }


def default_openocd_gdb_profile(
    openocd_root: str | Path | None = None,
    *,
    gdb_path: str | Path | None = None,
    probe: str = "stlink",
    target: str = "stm32f4x",
    gdb_port: int = 3333,
    telnet_port: int = 4444,
    tcl_port: int = 6666,
) -> OpenOcdGdbProfile:
    openocd = _discover_openocd(openocd_root)
    gdb = _discover_gdb(gdb_path)
    scripts = _discover_scripts_dir(openocd)
    interface_cfg = scripts / "interface" / f"{probe}.cfg" if scripts else None
    target_cfg = scripts / "target" / f"{target}.cfg" if scripts else None
    warnings = _warnings(openocd, gdb, scripts, interface_cfg, target_cfg)
    return OpenOcdGdbProfile(
        openocd_path=openocd,
        gdb_path=gdb,
        scripts_dir=scripts,
        interface_cfg=interface_cfg,
        target_cfg=target_cfg,
        probe=str(probe or "stlink"),
        target=str(target or "stm32f4x"),
        gdb_port=int(gdb_port),
        telnet_port=int(telnet_port),
        tcl_port=int(tcl_port),
        warnings=warnings,
    )


def _discover_openocd(root: str | Path | None) -> Path | None:
    from_path = shutil.which("openocd")
    if from_path:
        return Path(from_path).expanduser().resolve()
    roots = (Path(root).expanduser(),) if root else DEFAULT_OPENOCD_ROOTS
    candidates: list[Path] = []
    for item in roots:
        candidates.extend(
            (
                item / "bin" / "openocd.exe",
                item / "openocd.exe",
                item / "xpack-openocd-0.12.0-7" / "bin" / "openocd.exe",
            )
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _discover_gdb(path: str | Path | None) -> Path | None:
    if path:
        candidate = Path(path).expanduser()
        return candidate.resolve() if candidate.exists() else candidate
    from_path = shutil.which("arm-none-eabi-gdb")
    if from_path:
        return Path(from_path).expanduser().resolve()
    if DEFAULT_ST_GDB.exists():
        return DEFAULT_ST_GDB.resolve()
    return None


def _discover_scripts_dir(openocd: Path | None) -> Path | None:
    if openocd is None:
        return None
    roots = (
        openocd.parent.parent / "openocd" / "scripts",
        openocd.parent.parent / "scripts",
        openocd.parent.parent / "share" / "openocd" / "scripts",
    )
    for candidate in roots:
        if candidate.exists():
            return candidate.resolve()
    return None


def _warnings(
    openocd: Path | None,
    gdb: Path | None,
    scripts: Path | None,
    interface_cfg: Path | None,
    target_cfg: Path | None,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if openocd is None or not openocd.exists():
        warnings.append("未找到 openocd.exe，暂不能启动 OpenOCD server。")
    if gdb is None or not gdb.exists():
        warnings.append("未找到 arm-none-eabi-gdb.exe，暂不能建立 GDB/MI 会话。")
    if scripts is None or not scripts.exists():
        warnings.append("未找到 OpenOCD scripts 目录，interface/target cfg 需要手动配置。")
    if interface_cfg is None or not interface_cfg.exists():
        warnings.append("未找到 ST-Link interface cfg：interface/stlink.cfg。")
    if target_cfg is None or not target_cfg.exists():
        warnings.append("未找到 STM32F4 target cfg：target/stm32f4x.cfg。")
    return tuple(warnings)


def _quote(value: object) -> str:
    text = str(value)
    if not text:
        return '""'
    if any(char.isspace() for char in text):
        return f'"{text}"'
    return text
