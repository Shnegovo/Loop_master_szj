"""Backend-neutral acquisition session sample contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from src.core.acquisition_sources import (
    SCOPE_SOURCE_SWD,
    AcquisitionSourceDescriptor,
    acquisition_source_descriptor,
    normalize_known_acquisition_source_key,
)
from src.core.transports import VariableSpec


class AcquisitionSessionState(str, Enum):
    IDLE = "idle"
    READY = "ready"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"

    @property
    def label(self) -> str:
        labels = {
            AcquisitionSessionState.IDLE: "空闲",
            AcquisitionSessionState.READY: "就绪",
            AcquisitionSessionState.RUNNING: "采集中",
            AcquisitionSessionState.STOPPED: "已停止",
            AcquisitionSessionState.ERROR: "错误",
        }
        return labels[self]


@dataclass(frozen=True)
class AcquisitionSessionContract:
    source: AcquisitionSourceDescriptor
    state: AcquisitionSessionState
    sample_rate_hz: float = 0.0
    variable_count: int = 0
    detail: str = ""

    @property
    def source_key(self) -> str:
        return self.source.key

    @property
    def source_label(self) -> str:
        return self.source.label

    @property
    def rate_label(self) -> str:
        if self.sample_rate_hz <= 0:
            return self.source.rate_label
        return f"{self.sample_rate_hz:g} Hz"

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        rows = [
            ("采集会话", self.state.label),
            ("采集来源", self.source.label),
            ("采集模式", self.source.mode.label),
            ("采集频率", self.rate_label),
            ("采集变量数", str(max(0, int(self.variable_count)))),
            ("示波能力", self.source.capabilities.waveform_label),
            ("变量读取", self.source.capabilities.variable_read_label),
            ("变量写入", self.source.capabilities.variable_write_label),
            ("调试接管", self.source.capabilities.session_label),
        ]
        if self.detail:
            rows.append(("采集说明", self.detail))
        return tuple(rows)

    def to_record(self) -> dict[str, object]:
        return {
            "source_key": self.source.key,
            "source_label": self.source.label,
            "mode": self.source.mode.value,
            "mode_label": self.source.mode.label,
            "state": self.state.value,
            "state_label": self.state.label,
            "sample_rate_hz": self.sample_rate_hz,
            "variable_count": self.variable_count,
            "waveform_read": self.source.capabilities.waveform_read,
            "variable_read": self.source.capabilities.variable_read,
            "variable_write": self.source.capabilities.variable_write,
            "debugger_backed": self.source.capabilities.owns_debug_session,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AcquisitionSample:
    timestamp_s: float
    values: Mapping[str, float]
    sequence: int = 0

    def to_record(self) -> dict[str, object]:
        return {
            "t": self.timestamp_s,
            "sequence": self.sequence,
            "values": dict(self.values),
        }


@dataclass(frozen=True)
class AcquisitionBatch:
    source_key: str
    variable_names: tuple[str, ...]
    samples: tuple[AcquisitionSample, ...]
    sample_interval_s: float = 0.0

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def duration_s(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return max(0.0, self.samples[-1].timestamp_s - self.samples[0].timestamp_s)

    @property
    def actual_rate_hz(self) -> float:
        duration = self.duration_s
        if duration <= 0:
            return 0.0
        return (len(self.samples) - 1) / duration

    def series(self) -> dict[str, tuple[tuple[float, ...], tuple[float, ...]]]:
        timestamps = tuple(sample.timestamp_s for sample in self.samples)
        return {
            name: (
                timestamps,
                tuple(sample.values.get(name, math.nan) for sample in self.samples),
            )
            for name in self.variable_names
        }

    def to_record(self) -> dict[str, object]:
        return {
            "source_key": self.source_key,
            "variable_names": list(self.variable_names),
            "sample_count": self.sample_count,
            "sample_interval_s": self.sample_interval_s,
            "actual_rate_hz": self.actual_rate_hz,
            "samples": [sample.to_record() for sample in self.samples],
        }


def acquisition_session_contract(
    source_key: str | None = SCOPE_SOURCE_SWD,
    *,
    state: AcquisitionSessionState = AcquisitionSessionState.IDLE,
    sample_rate_hz: float = 0.0,
    variable_count: int = 0,
    detail: str = "",
) -> AcquisitionSessionContract:
    source = acquisition_source_descriptor(source_key)
    return AcquisitionSessionContract(
        source=source,
        state=state,
        sample_rate_hz=max(0.0, float(sample_rate_hz or 0.0)),
        variable_count=max(0, int(variable_count or 0)),
        detail=str(detail or ""),
    )


def acquisition_batch_from_rows(
    source_key: str,
    variables: Sequence[VariableSpec],
    rows: Sequence[Mapping[str, object] | Sequence[object]],
    *,
    start_time_s: float,
    sample_interval_s: float,
    sequence_start: int = 0,
) -> AcquisitionBatch:
    names = tuple(str(name) for name, _address, _type_info in variables)
    interval = max(0.0, float(sample_interval_s or 0.0))
    samples = tuple(
        AcquisitionSample(
            timestamp_s=float(start_time_s) + index * interval,
            values=normalise_sample_values(variables, row),
            sequence=int(sequence_start) + index,
        )
        for index, row in enumerate(rows)
    )
    return AcquisitionBatch(
        source_key=normalize_known_acquisition_source_key(source_key),
        variable_names=names,
        samples=samples,
        sample_interval_s=interval,
    )


def normalise_sample_values(
    variables: Sequence[VariableSpec],
    values: Mapping[str, object] | Sequence[object],
) -> dict[str, float]:
    names = tuple(str(name) for name, _address, _type_info in variables)
    if isinstance(values, Mapping):
        return {name: _coerce_float(values.get(name, math.nan)) for name in names}
    return {
        name: _coerce_float(values[index] if index < len(values) else math.nan)
        for index, name in enumerate(names)
    }


def _coerce_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan
