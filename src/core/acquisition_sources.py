"""Waveform acquisition source descriptors.

The oscilloscope can read data through several very different paths. This file
keeps their capabilities explicit so UI code does not have to treat every
backend string as a special case.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


SCOPE_SOURCE_SWD = "swd"
SCOPE_SOURCE_KEIL_WATCH = "keil_watch"
SCOPE_SOURCE_SERIAL_WAVEFORM = "serial_waveform"
SCOPE_SOURCE_OPENOCD_GDB = "openocd_gdb"
SCOPE_SOURCE_PYOCD = "pyocd"
SCOPE_SOURCE_TI_MSPM0 = "ti_mspm0g3507"


class AcquisitionSourceState(str, Enum):
    ACTIVE = "active"
    READY = "ready"
    ROUTE_ONLY = "route_only"
    PLANNED = "planned"


@dataclass(frozen=True)
class AcquisitionSourceDescriptor:
    key: str
    label: str
    short_label: str
    state: AcquisitionSourceState
    domain: str
    transport: str
    intrusive_level: str
    read_capable: bool
    write_capable: bool
    implemented: bool
    selectable: bool
    recommended_hz: int | None = None
    max_hz: int | None = None
    rate_note: str = ""
    use_case: str = ""
    safety_note: str = ""
    route_note: str = ""

    @property
    def active(self) -> bool:
        return self.state == AcquisitionSourceState.ACTIVE

    @property
    def status_label(self) -> str:
        labels = {
            AcquisitionSourceState.ACTIVE: "当前",
            AcquisitionSourceState.READY: "可切换",
            AcquisitionSourceState.ROUTE_ONLY: "独立页",
            AcquisitionSourceState.PLANNED: "计划中",
        }
        return labels[self.state]

    @property
    def rate_label(self) -> str:
        if self.recommended_hz is None and self.max_hz is None:
            return self.rate_note or "--"
        if self.recommended_hz is not None and self.max_hz is not None:
            return f"建议 {self.recommended_hz} Hz，最高 {self.max_hz} Hz"
        if self.recommended_hz is not None:
            return f"建议 {self.recommended_hz} Hz"
        return f"最高 {self.max_hz} Hz"

    @property
    def write_label(self) -> str:
        return "可显式写入" if self.write_capable else "只读采集"

    @property
    def detail(self) -> str:
        parts = [
            self.use_case,
            f"侵入性：{self.intrusive_level}",
            f"速率：{self.rate_label}",
            f"写入：{self.write_label}",
        ]
        if self.route_note:
            parts.append(self.route_note)
        if self.safety_note:
            parts.append(self.safety_note)
        return "；".join(part for part in parts if part)

    def to_option(self) -> tuple[str, str, str, bool]:
        label = f"{self.label} · {self.status_label}"
        return self.key, label, self.detail, self.selectable

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        return (
            ("示波采集源", self.label),
            ("采集源状态", self.status_label),
            ("采集链路", self.transport),
            ("采集侵入性", self.intrusive_level),
            ("采集建议频率", self.rate_label),
            ("采集写入边界", self.write_label),
            ("采集用途", self.use_case or "--"),
        )


def build_acquisition_source_catalog(
    active_key: str = SCOPE_SOURCE_SWD,
    *,
    keil_watch_ready: bool = True,
    serial_ready: bool = True,
    include_planned: bool = True,
) -> tuple[AcquisitionSourceDescriptor, ...]:
    active = normalize_acquisition_source_key(active_key)
    descriptors = [
        AcquisitionSourceDescriptor(
            key=SCOPE_SOURCE_SWD,
            label="SWD 内存",
            short_label="SWD",
            state=_state_for(SCOPE_SOURCE_SWD, active, selectable=True),
            domain="LoopMaster",
            transport="CMSIS-DAP / SWD",
            intrusive_level="非/轻侵入式轮询",
            read_capable=True,
            write_capable=True,
            implemented=True,
            selectable=True,
            recommended_hz=200,
            max_hz=1000,
            rate_note="变量较少时可更高，最终受探针和目标运行状态影响",
            use_case="原 LoopMaster 变量示波，适合 PID 曲线和高速轮询",
            safety_note="写变量只通过显式确认入口执行。",
        ),
        AcquisitionSourceDescriptor(
            key=SCOPE_SOURCE_KEIL_WATCH,
            label="Keil Watch",
            short_label="Keil Watch",
            state=_state_for(SCOPE_SOURCE_KEIL_WATCH, active, selectable=keil_watch_ready),
            domain="Debug",
            transport="Keil UVSOCK Watch",
            intrusive_level="调试器表达式读取",
            read_capable=True,
            write_capable=True,
            implemented=True,
            selectable=keil_watch_ready,
            recommended_hz=10,
            max_hz=30,
            rate_note="表达式读取不适合作高速示波",
            use_case="Keil 调试会话内低频观察变量，适合联调改参后的确认",
            safety_note="写变量走调试器基线读取和显式确认。",
        ),
        AcquisitionSourceDescriptor(
            key=SCOPE_SOURCE_SERIAL_WAVEFORM,
            label="串口波形",
            short_label="串口",
            state=AcquisitionSourceState.ROUTE_ONLY,
            domain="Serial",
            transport="UART / VOFA-style stream",
            intrusive_level="固件主动上报",
            read_capable=True,
            write_capable=False,
            implemented=True,
            selectable=serial_ready,
            recommended_hz=100,
            max_hz=1000,
            rate_note="取决于波特率、通道数量和协议编码",
            use_case="串口助手波形页，适合环回、日志和固件主动输出曲线",
            route_note="选择后跳转到串口助手，不改变主示波器 SWD/Keil Watch 来源。",
        ),
    ]
    if include_planned:
        descriptors.extend(
            (
                AcquisitionSourceDescriptor(
                    key=SCOPE_SOURCE_OPENOCD_GDB,
                    label="OpenOCD/GDB",
                    short_label="OpenOCD",
                    state=AcquisitionSourceState.PLANNED,
                    domain="Debug",
                    transport="OpenOCD + GDB/MI",
                    intrusive_level="调试器读取",
                    read_capable=True,
                    write_capable=True,
                    implemented=False,
                    selectable=False,
                    recommended_hz=5,
                    max_hz=20,
                    rate_note="GDB 往返较慢，优先用于断点、变量和低频观察",
                    use_case="后续多工具链适配，补齐非 Keil MCU 调试路径",
                    safety_note="未接入真实执行器前不会启动 OpenOCD 或写目标。",
                ),
                AcquisitionSourceDescriptor(
                    key=SCOPE_SOURCE_PYOCD,
                    label="pyOCD",
                    short_label="pyOCD",
                    state=AcquisitionSourceState.PLANNED,
                    domain="Debug",
                    transport="pyOCD session",
                    intrusive_level="调试器读取",
                    read_capable=True,
                    write_capable=True,
                    implemented=False,
                    selectable=False,
                    recommended_hz=5,
                    max_hz=20,
                    rate_note="适合 CMSIS-Pack 目标的低频变量读取",
                    use_case="后续作为 Keil 之外的 Python 调试后端",
                    safety_note="未接入真实执行器前不会连接探针或写目标。",
                ),
                AcquisitionSourceDescriptor(
                    key=SCOPE_SOURCE_TI_MSPM0,
                    label="TI MSPM0G3507",
                    short_label="TI M0G3507",
                    state=AcquisitionSourceState.PLANNED,
                    domain="Debug",
                    transport="TI 工具链 / 后续 OCD 适配",
                    intrusive_level="调试器读取",
                    read_capable=True,
                    write_capable=True,
                    implemented=False,
                    selectable=False,
                    recommended_hz=5,
                    max_hz=20,
                    rate_note="当前先做资料与工程适配，无 TI 硬件时保持离线验证",
                    use_case="用户指定的 MSPM0G3507 优先适配目标",
                    safety_note="未接入真实芯片 debug 前不冒充在线链路。",
                ),
            )
        )
    return tuple(descriptors)


def acquisition_source_options(
    active_key: str = SCOPE_SOURCE_SWD,
    *,
    keil_watch_ready: bool = True,
    serial_ready: bool = True,
    include_planned: bool = True,
) -> tuple[tuple[str, str, str, bool], ...]:
    return tuple(
        source.to_option()
        for source in build_acquisition_source_catalog(
            active_key,
            keil_watch_ready=keil_watch_ready,
            serial_ready=serial_ready,
            include_planned=include_planned,
        )
    )


def active_acquisition_source(
    active_key: str = SCOPE_SOURCE_SWD,
    *,
    keil_watch_ready: bool = True,
    serial_ready: bool = True,
    include_planned: bool = True,
) -> AcquisitionSourceDescriptor:
    active = normalize_acquisition_source_key(active_key)
    for source in build_acquisition_source_catalog(
        active,
        keil_watch_ready=keil_watch_ready,
        serial_ready=serial_ready,
        include_planned=include_planned,
    ):
        if source.key == active:
            return source
    return build_acquisition_source_catalog(SCOPE_SOURCE_SWD)[0]


def normalize_acquisition_source_key(key: str | None) -> str:
    text = str(key or "").strip()
    if text in {SCOPE_SOURCE_SWD, SCOPE_SOURCE_KEIL_WATCH}:
        return text
    return SCOPE_SOURCE_SWD


def _state_for(key: str, active_key: str, *, selectable: bool) -> AcquisitionSourceState:
    if key == active_key:
        return AcquisitionSourceState.ACTIVE
    if selectable:
        return AcquisitionSourceState.READY
    return AcquisitionSourceState.PLANNED
