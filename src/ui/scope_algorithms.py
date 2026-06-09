"""Pure scope display algorithms used by the Qt UI."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np


SeriesData = tuple[Any, Any]


def effective_plot_fps(frame_rate: int, pane_count: int, curve_count: int) -> int:
    pane_count = max(1, min(3, int(pane_count or 1)))
    curve_count = max(0, int(curve_count or 0))
    if curve_count <= 4:
        cap = 90 if pane_count == 1 else 60 if pane_count == 2 else 36
    elif curve_count <= 8:
        cap = 72 if pane_count == 1 else 45 if pane_count == 2 else 30
    else:
        cap = 54 if pane_count == 1 else 36 if pane_count == 2 else 24
    return max(1, min(int(frame_rate or 1), cap))


def point_budget_for_fps(fps: int) -> int:
    return max(700, int(max(1, int(fps or 1)) * 36))


def process_display_data(
    data: Mapping[str, SeriesData],
    sample_rate: float,
    display_fps: int,
) -> dict[str, SeriesData]:
    if sample_rate <= 0 or display_fps <= 0:
        return dict(data)

    ratio = sample_rate / display_fps
    if 0.5 <= ratio <= 2.0:
        return dict(data)

    result: dict[str, SeriesData] = {}
    for name, (ts, vals) in data.items():
        if len(ts) < 2:
            result[name] = (ts, vals)
            continue
        try:
            if ratio < 0.5:
                result[name] = interpolate_data(ts, vals, display_fps)
            else:
                result[name] = decimate_data(ts, vals, int(ratio))
        except Exception:
            result[name] = (ts, vals)
    return result


def interpolate_data(ts, vals, display_fps: int):
    t_min, t_max = ts[0], ts[-1]
    duration = t_max - t_min
    if duration <= 0:
        return ts, vals
    num_points = max(2, int(duration * display_fps))
    num_points = min(num_points, len(ts) * 10)
    if num_points <= len(ts):
        return ts, vals
    ts_arr = np.asarray(ts, dtype=float)
    vals_arr = np.asarray(vals, dtype=float)
    display_ts = np.linspace(t_min, t_max, num_points)
    display_vals = np.interp(display_ts, ts_arr, vals_arr)
    return display_ts, display_vals


def decimate_data(ts, vals, factor: int):
    step = max(1, int(factor or 1))
    if step <= 1:
        return ts, vals
    max_points = max(2, int(np.ceil(len(ts) / step)))
    return peak_preserving_thin(ts, vals, max_points)


def thin_display_series(ts, vals, max_points: int):
    return peak_preserving_thin(ts, vals, max_points)


def peak_preserving_thin(ts, vals, max_points: int):
    if len(ts) <= max_points or max_points <= 0:
        return ts, vals
    ts_arr = np.asarray(ts, dtype=float)
    vals_arr = np.asarray(vals, dtype=float)
    if ts_arr.size != vals_arr.size or ts_arr.size == 0:
        return ts, vals

    max_points = max(3, int(max_points))
    bucket_count = max(1, max_points // 2)
    edges = np.linspace(0, ts_arr.size, bucket_count + 1, dtype=int)
    indices = [0]

    for start, end in zip(edges[:-1], edges[1:]):
        if end <= start:
            continue
        segment = vals_arr[start:end]
        finite = np.flatnonzero(np.isfinite(segment))
        if finite.size == 0:
            indices.append(start)
            continue
        segment_finite = segment[finite]
        min_index = start + int(finite[int(np.argmin(segment_finite))])
        max_index = start + int(finite[int(np.argmax(segment_finite))])
        if min_index <= max_index:
            indices.extend((min_index, max_index))
        else:
            indices.extend((max_index, min_index))

    indices.append(ts_arr.size - 1)
    indices = sorted(set(index for index in indices if 0 <= index < ts_arr.size))
    if len(indices) > max_points:
        indices = indices[:max_points - 1] + [ts_arr.size - 1]
    return ts_arr[indices], vals_arr[indices]


def calculate_y_range(
    series_values: Iterable[Any],
    previous: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    values = []
    for series in series_values:
        vals = np.asarray(series, dtype=float)
        if vals.size == 0:
            continue
        vals = vals[np.isfinite(vals)]
        if vals.size:
            values.append(vals)

    if not values:
        return None

    flat = np.concatenate(values)
    if flat.size == 0:
        return None

    full_low = float(np.min(flat))
    full_high = float(np.max(flat))

    if flat.size >= 32:
        robust_low = float(np.percentile(flat, 5))
        robust_high = float(np.percentile(flat, 95))
        robust_span = robust_high - robust_low
        robust_peak = max(abs(robust_low), abs(robust_high), 1.0)
        allowance = max(robust_span * 3.0, robust_peak * 0.75, 0.01)
        low = max(full_low, robust_low - allowance)
        high = min(full_high, robust_high + allowance)
    elif flat.size >= 16:
        low = float(np.percentile(flat, 2))
        high = float(np.percentile(flat, 98))
    else:
        low = full_low
        high = full_high

    if not np.isfinite(low) or not np.isfinite(high):
        return None

    center = (low + high) / 2.0
    span = high - low
    if not np.isfinite(span) or span <= 0:
        center = float(np.median(flat))
        span = max(abs(center) * 0.02, 0.02)
    else:
        central = flat[(flat >= low) & (flat <= high)]
        if central.size == 0:
            central = flat
        spread = float(np.std(central)) if central.size >= 4 else 0.0
        peak = max(abs(low), abs(high), abs(center), 1.0)
        min_span = max(spread * 6.0, peak * 0.01, 0.01)
        if span < min_span:
            span = min_span

    margin = max(span * 0.12, 0.01)
    y_min = center - span / 2.0 - margin
    y_max = center + span / 2.0 + margin
    if y_min == y_max:
        y_min -= 0.5
        y_max += 0.5
    return stabilize_y_range(previous, y_min, y_max)


def stabilize_y_range(
    previous: tuple[float, float] | None,
    y_min: float,
    y_max: float,
) -> tuple[float, float]:
    if previous is None:
        return y_min, y_max

    old_min, old_max = previous
    if not all(np.isfinite(value) for value in (old_min, old_max, y_min, y_max)):
        return y_min, y_max

    old_span = max(old_max - old_min, 1e-9)
    expands = y_min < old_min or y_max > old_max
    if expands:
        return min(y_min, old_min), max(y_max, old_max)

    alpha = 0.18
    next_min = old_min + (y_min - old_min) * alpha
    next_max = old_max + (y_max - old_max) * alpha
    if (next_max - next_min) < old_span * 0.25:
        center = (next_min + next_max) / 2.0
        half = old_span * 0.125
        next_min = center - half
        next_max = center + half
    return next_min, next_max
