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


@dataclass(frozen=True)
class DebugToolchainCommandPlan:
    key: str
    display_name: str
    stage: DebugToolchainStage
    preview_commands: tuple[str, ...]
    safety_gates: tuple[str, ...]
    may_start_process: bool = False
    may_connect_probe: bool = False
    may_write_target: bool = False

    @property
    def gate_label(self) -> str:
        active = []
        if self.may_start_process:
            active.append("可启动进程")
        if self.may_connect_probe:
            active.append("可连接探针")
        if self.may_write_target:
            active.append("可写目标")
        return " / ".join(active) if active else "仅预览，不执行"

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        return (
            ("工具链命令计划", f"{self.display_name} · {self.stage_label}"),
            ("预览命令", "；".join(self.preview_commands) if self.preview_commands else "--"),
            ("执行门", self.gate_label),
            ("命令安全条件", "；".join(self.safety_gates) if self.safety_gates else "--"),
        )

    @property
    def stage_label(self) -> str:
        return {
            DebugToolchainStage.LIVE: "已接入",
            DebugToolchainStage.PLACEHOLDER: "占位",
            DebugToolchainStage.PLANNED: "计划中",
        }[self.stage]

    def to_record(self) -> dict[str, object]:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "stage": self.stage.value,
            "preview_commands": list(self.preview_commands),
            "safety_gates": list(self.safety_gates),
            "may_start_process": self.may_start_process,
            "may_connect_probe": self.may_connect_probe,
            "may_write_target": self.may_write_target,
        }


def debug_toolchain_descriptor(key: str) -> DebugToolchainDescriptor:
    descriptors = {item.key: item for item in default_debug_toolchains()}
    try:
        return descriptors[str(key)]
    except KeyError as exc:
        raise KeyError(f"debug toolchain is not registered: {key}") from exc


def debug_toolchain_command_plan(key: str) -> DebugToolchainCommandPlan:
    descriptor = debug_toolchain_descriptor(key)
    preview_commands = _preview_commands_for(descriptor.key)
    safety_gates = _safety_gates_for(descriptor)
    may_start_process = descriptor.stage == DebugToolchainStage.LIVE
    return DebugToolchainCommandPlan(
        key=descriptor.key,
        display_name=descriptor.display_name,
        stage=descriptor.stage,
        preview_commands=preview_commands,
        safety_gates=safety_gates,
        may_start_process=may_start_process,
        may_connect_probe=False,
        may_write_target=False,
    )


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
            stage=DebugToolchainStage.LIVE,
            protocol="GDB/MI + OpenOCD monitor",
            target_scope="通用 Cortex-M，优先 ST-Link/CMSIS-DAP",
            executable_hint="openocd.exe + arm-none-eabi-gdb.exe",
            implemented_operations=(
                "profile_discovery",
                "dry_run_command_preview",
                "app_backend_attach",
                "pc_readback",
                "remote_breakpoint_readback",
                "sync_breakpoints",
                "live_readonly_smoke_pc",
                "live_breakpoint_smoke",
            ),
            planned_operations=(
                "halt/run/reset/step",
                "read/write_variable",
                "multi_target_profiles",
            ),
            safety_note="执行门关闭时只预览；执行门打开后会启动 OpenOCD/GDB、连接探针，断点同步会短暂停顿并改变目标断点状态。",
            next_step="补 halt/run/reset/step 和变量读写，再扩展到 pyOCD/TI。",
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


def _preview_commands_for(key: str) -> tuple[str, ...]:
    if key == "keil":
        return (
            "UVSC_OpenConnection(port)",
            'UVSC_DBG_EXEC_CMD("BS/BK/BE/BD/BL")',
            "UVSC_DBG_STATUS / variable read-write adapters",
        )
    if key == "openocd_gdb":
        return (
            "openocd -f interface/<probe>.cfg -f target/<mcu>.cfg",
            "arm-none-eabi-gdb --interpreter=mi2 <elf>",
            "target extended-remote :3333",
            "-break-insert / -exec-interrupt / -exec-continue",
        )
    if key == "pyocd":
        return (
            "pyocd list --probes",
            "pyocd gdbserver --target <cmsis-pack-target>",
            "arm-none-eabi-gdb --interpreter=mi2 <elf>",
            "pyOCD session read_memory/write_memory adapter",
        )
    if key == "ti_mspm0":
        return (
            r"D:\ti\ccs2041\ccs\ccs_base\DebugServer\bin\DSLite.exe <profile>",
            r"D:\ti\ccs2041\ccs\ccs_base\scripting\bin\dss.bat <debug-script.js>",
            "TI targetdb MSPM0G3507 memory-map validation",
            "breakpoint/run-control/variable adapter after hardware gate",
        )
    if key == "offline":
        return (
            "load_trace(recording)",
            "replay_scope_samples()",
            "apply_source_markers_from_recording()",
        )
    return ()


def _safety_gates_for(descriptor: DebugToolchainDescriptor) -> tuple[str, ...]:
    if descriptor.stage == DebugToolchainStage.LIVE:
        return (
            "显式按钮触发",
            "确认对话框",
            "审计日志",
            "后端状态回读",
        )
    return (
        "当前不启动外部进程",
        "当前不枚举或连接探针",
        "当前不写变量/内存/断点",
        "先通过 no-hardware probe 固化命令合同",
    )
