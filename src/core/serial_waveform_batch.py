"""Serial waveform conversion into backend-neutral acquisition batches."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TypeAlias

from src.core.acquisition_session import AcquisitionBatch, AcquisitionSample
from src.core.acquisition_sources import (
    SCOPE_SOURCE_SERIAL_WAVEFORM,
    normalize_known_acquisition_source_key,
)


SeriesPair: TypeAlias = tuple[Sequence[object], Sequence[object]]
SerialSampleRow: TypeAlias = Mapping[str, object] | Sequence[object]


def serial_waveform_batch_from_series(
    data: Mapping[str, SeriesPair],
    *,
    source_key: str = SCOPE_SOURCE_SERIAL_WAVEFORM,
    sample_interval_s: float = 0.0,
    sequence_start: int = 0,
) -> AcquisitionBatch:
    """Convert plotted serial channel series into an acquisition batch."""
    source = normalize_known_acquisition_source_key(source_key)
    channels = _normalise_series(data)
    names = tuple(name for name, _x_values, _y_values in channels)
    interval = max(0.0, float(sample_interval_s or 0.0))
    if not channels:
        return AcquisitionBatch(source, (), (), sample_interval_s=interval)

    sample_count = min(
        min(len(x_values), len(y_values))
        for _name, x_values, y_values in channels
    )
    if sample_count <= 0:
        return AcquisitionBatch(source, names, (), sample_interval_s=interval)

    timestamps = channels[0][1][:sample_count]
    if interval <= 0:
        interval = _infer_interval(timestamps)
    samples = tuple(
        AcquisitionSample(
            timestamp_s=float(timestamps[index]),
            values={
                name: y_values[index]
                for name, _x_values, y_values in channels
            },
            sequence=int(sequence_start) + index,
        )
        for index in range(sample_count)
    )
    return AcquisitionBatch(source, names, samples, sample_interval_s=interval)


def serial_samples_to_acquisition_batch(
    samples: Sequence[SerialSampleRow],
    *,
    source_key: str = SCOPE_SOURCE_SERIAL_WAVEFORM,
    channel_names: Sequence[str] | None = None,
    timestamps_s: Sequence[object] | None = None,
    start_time_s: float = 0.0,
    sample_interval_s: float = 0.0,
    sequence_start: int = 0,
) -> AcquisitionBatch:
    """Convert parser sample rows into an acquisition batch."""
    source = normalize_known_acquisition_source_key(source_key)
    rows = tuple(samples) if samples is not None else ()
    names = _names_from_rows(rows, channel_names)
    interval = max(0.0, float(sample_interval_s or 0.0))
    if not rows:
        return AcquisitionBatch(source, names, (), sample_interval_s=interval)

    timestamps = _timestamps_for_rows(
        len(rows),
        timestamps_s=timestamps_s,
        start_time_s=start_time_s,
        sample_interval_s=interval,
    )
    if interval <= 0:
        interval = _infer_interval(timestamps)
    batch_samples = tuple(
        AcquisitionSample(
            timestamp_s=timestamps[index],
            values=_row_values(row, names),
            sequence=int(sequence_start) + index,
        )
        for index, row in enumerate(rows)
    )
    return AcquisitionBatch(source, names, batch_samples, sample_interval_s=interval)


def _normalise_series(
    data: Mapping[str, SeriesPair],
) -> tuple[tuple[str, tuple[float, ...], tuple[float, ...]], ...]:
    result: list[tuple[str, tuple[float, ...], tuple[float, ...]]] = []
    used: set[str] = set()
    for raw_name, pair in (data or {}).items():
        name = _unique_name(raw_name, used, len(result) + 1)
        try:
            x_values, y_values = pair
        except (TypeError, ValueError):
            result.append((name, (), ()))
            continue
        result.append((name, _float_tuple(x_values), _float_tuple(y_values)))
    return tuple(result)


def _names_from_rows(
    rows: Sequence[SerialSampleRow],
    channel_names: Sequence[str] | None,
) -> tuple[str, ...]:
    used: set[str] = set()
    result: list[str] = []
    configured_names = tuple(channel_names) if channel_names is not None else ()
    for index, raw_name in enumerate(configured_names):
        result.append(_unique_name(raw_name, used, index + 1))

    max_positional_count = 0
    for row in rows:
        if isinstance(row, Mapping):
            for raw_name in row.keys():
                text = str(raw_name or "").strip()
                if text and text not in used:
                    used.add(text)
                    result.append(text)
        elif not isinstance(row, (str, bytes, bytearray)):
            try:
                max_positional_count = max(max_positional_count, len(row))
            except TypeError:
                continue

    while len(result) < max_positional_count:
        result.append(_unique_name("", used, len(result) + 1))
    return tuple(result)


def _row_values(row: SerialSampleRow, names: Sequence[str]) -> dict[str, float]:
    if isinstance(row, Mapping):
        return {name: _coerce_float(row.get(name, math.nan)) for name in names}
    if isinstance(row, (str, bytes, bytearray)):
        values: Sequence[object] = (row,)
    else:
        values = row
    return {
        name: _coerce_float(values[index] if index < len(values) else math.nan)
        for index, name in enumerate(names)
    }


def _timestamps_for_rows(
    count: int,
    *,
    timestamps_s: Sequence[object] | None,
    start_time_s: float,
    sample_interval_s: float,
) -> tuple[float, ...]:
    if timestamps_s is not None:
        values = _float_tuple(timestamps_s)
        if len(values) >= count:
            return values[:count]
    interval = max(0.0, float(sample_interval_s or 0.0))
    start = _coerce_float(start_time_s)
    return tuple(start + index * interval for index in range(count))


def _unique_name(raw_name: object, used: set[str], fallback_index: int) -> str:
    base = str(raw_name or "").strip() or f"ch{fallback_index}"
    if base not in used:
        used.add(base)
        return base
    suffix = 2
    while f"{base}_{suffix}" in used:
        suffix += 1
    name = f"{base}_{suffix}"
    used.add(name)
    return name


def _float_tuple(values: Sequence[object]) -> tuple[float, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        return (_coerce_float(values),)
    try:
        return tuple(_coerce_float(value) for value in values)
    except TypeError:
        return ()


def _infer_interval(timestamps: Sequence[float]) -> float:
    if len(timestamps) < 2:
        return 0.0
    duration = float(timestamps[-1]) - float(timestamps[0])
    if duration <= 0:
        return 0.0
    return duration / (len(timestamps) - 1)


def _coerce_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan
