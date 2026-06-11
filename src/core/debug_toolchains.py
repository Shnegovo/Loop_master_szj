"""Debugger toolchain metadata used by backend placeholders."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DebugToolchainStage(str, Enum):
    LIVE = "live"
    PLACEHOLDER = "placeholder"
    PLANNED = "planned"


@dataclass(frozen=True)
class DebugToolchainDescriptor:
    key: str
    display_name: str
    stage: DebugToolchainStage
    protocol: str
    target_scope: str
    executable_hint: str
    implemented_operations: tuple[str, ...]
    planned_operations: tuple[str, ...]
    safety_note: str
    next_step: str

    @property
    def stage_label(self) -> str:
        labels = {
            DebugToolchainStage.LIVE: "已接入",
            DebugToolchainStage.PLACEHOLDER: "占位",
            DebugToolchainStage.PLANNED: "计划中",
        }
        return labels[self.stage]

    @property
    def combo_note(self) -> str:
        return f"{self.stage_label} · {self.protocol} · {self.target_scope}。{self.safety_note}"

    @property
    def unavailable_detail(self) -> str:
        if self.stage == DebugToolchainStage.LIVE:
            return f"{self.display_name} 已有真实后端。"
        return f"{self.display_name} 后端{self.stage_label}，{self.next_step}"

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        return (
            ("工具链阶段", self.stage_label),
            ("工具链协议", self.protocol),
            ("适配目标", self.target_scope),
            ("可执行入口", self.executable_hint or "--"),
            ("已实现操作", ", ".join(self.implemented_operations) if self.implemented_operations else "无"),
            ("计划操作", ", ".join(self.planned_operations) if self.planned_operations else "无"),
            ("安全边界", self.safety_note),
            ("后续动作", self.next_step),
        )


def debug_toolchain_descriptor(key: str) -> DebugToolchainDescriptor:
    descriptors = {item.key: item for item in default_debug_toolchains()}
    try:
        return descriptors[str(key)]
    except KeyError as exc:
        raise KeyError(f"debug toolchain is not registered: {key}") from exc


def default_debug_toolchains() -> tuple[DebugToolchainDescriptor, ...]:
    return (
        DebugToolchainDescriptor(
            key="keil",
            display_name="Keil / UVSOCK",
            stage=DebugToolchainStage.LIVE,
            protocol="UVSOCK + Keil Debug Commands",
            target_scope="Keil µVision 工程，当前已实测 STM32F401",
            executable_hint="UV4.exe / UVSOCK",
            implemented_operations=(
                "discover",
                "attach",
                "halt",
                "run",
                "reset",
                "step",
                "step_over",
                "run_to_cursor",
                "sync_breakpoints",
                "read_variable",
                "write_variable",
            ),
            planned_operations=("更完整 PC/源码状态", "多工程调试档案"),
            safety_note="所有真实动作仍由显式按钮和确认触发。",
            next_step="作为 OpenOCD、pyOCD 和 TI 适配的参考后端。",
        ),
        DebugToolchainDescriptor(
            key="openocd_gdb",
            display_name="OpenOCD / GDB",
            stage=DebugToolchainStage.PLACEHOLDER,
            protocol="GDB/MI + OpenOCD monitor",
            target_scope="通用 Cortex-M，优先 ST-Link/CMSIS-DAP",
            executable_hint="openocd.exe + arm-none-eabi-gdb.exe",
            implemented_operations=(),
            planned_operations=(
                "launch_server",
                "attach_gdb",
                "halt/run/reset/step",
                "breakpoint_sync",
                "read/write_variable",
            ),
            safety_note="占位阶段不会启动 OpenOCD、连接探针或写目标。",
            next_step="先接入 no-process 配置档案和 GDB/MI 命令预览，再开放显式启动。",
        ),
        DebugToolchainDescriptor(
            key="pyocd",
            display_name="pyOCD",
            stage=DebugToolchainStage.PLACEHOLDER,
            protocol="pyOCD Python session / GDB server",
            target_scope="CMSIS-Pack 支持的 Cortex-M",
            executable_hint="pyocd",
            implemented_operations=(),
            planned_operations=(
                "list_probes",
                "attach_session",
                "halt/run/reset/step",
                "read/write_memory",
                "read/write_variable",
            ),
            safety_note="占位阶段不会枚举或连接探针。",
            next_step="接入只读 probe/config 发现，再映射到统一变量访问接口。",
        ),
        DebugToolchainDescriptor(
            key="ti_mspm0",
            display_name="TI MSPM0G3507",
            stage=DebugToolchainStage.PLANNED,
            protocol="TI CCS DebugServer / XDS110 / 后续 GDB 桥接",
            target_scope="TI MSPM0G3507 Cortex-M0+",
            executable_hint="D:\\ti\\ccs2041\\ccs\\ccs_base\\DebugServer\\bin\\DSLite.exe",
            implemented_operations=(),
            planned_operations=(
                "project_profile",
                "targetdb_memory_map",
                "symbol/ELF_source",
                "halt/run/reset/step",
                "breakpoint_sync",
                "read/write_variable",
            ),
            safety_note="当前没有 TI 芯片 debug 硬件，不连接探针、不写目标。",
            next_step="本机档案已能只读识别 CCS/SDK/targetdb，下一步做 no-hardware 命令预览和真实硬件门禁。",
        ),
        DebugToolchainDescriptor(
            key="offline",
            display_name="离线回放",
            stage=DebugToolchainStage.PLACEHOLDER,
            protocol="record/replay",
            target_scope="无硬件记录回放",
            executable_hint="LoopMaster 本地记录",
            implemented_operations=(),
            planned_operations=("load_trace", "replay_scope", "source_marker_replay"),
            safety_note="离线模式不连接硬件、不改变目标。",
            next_step="后续用于现场记录复现和无硬件 UI 调试。",
        ),
    )
