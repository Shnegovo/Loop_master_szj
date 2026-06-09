"""Probe pure scope display algorithms without starting Qt."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui.scope_algorithms import (  # noqa: E402
    calculate_y_range,
    effective_plot_fps,
    peak_preserving_thin,
    point_budget_for_fps,
    process_display_data,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _span(y_range: tuple[float, float]) -> float:
    return float(y_range[1] - y_range[0])


def _run() -> None:
    _assert(effective_plot_fps(120, 1, 2) == 90, "single-pane FPS cap changed")
    _assert(effective_plot_fps(120, 3, 12) == 24, "dense multi-pane FPS cap changed")
    _assert(point_budget_for_fps(24) >= 700, "low-FPS point budget too small")

    t = np.linspace(0.0, 8.0, 8000)

    response = 60.0 * (1.0 - np.exp(-t * 1.35))
    response += 18.0 * np.exp(-np.maximum(t - 1.25, 0.0) * 1.3) * np.sin(
        np.maximum(t - 1.25, 0.0) * 8.5
    )
    response[t < 0.08] = 0.0
    response += np.where(t > 2.5, np.sin(t * 90.0) * 0.42, 0.0)
    y_range = calculate_y_range([response])
    _assert(y_range is not None, "step response range missing")
    _assert(y_range[0] < -1.0 and y_range[1] > 66.0, f"step response range not useful: {y_range}")

    micro = 60.0 + np.sin(t * 2.0 * math.pi * 120.0) * 0.0006
    y_range = calculate_y_range([micro])
    _assert(y_range is not None, "micro jitter range missing")
    _assert(0.45 <= _span(y_range) <= 2.5, f"micro jitter zoom is not PID-friendly: {y_range}")

    small = 60.0 + np.sin(t * 2.0 * math.pi * 85.0) * 0.75
    y_range = calculate_y_range([small])
    _assert(y_range is not None, "small jitter range missing")
    _assert(1.0 <= _span(y_range) <= 8.0, f"small jitter range not useful: {y_range}")

    spike = 60.0 + np.sin(t * 2.0 * math.pi * 8.0) * 0.25
    spike[np.argmin(np.abs(t - 3.25))] = 95.0
    thin_t, thin_y = peak_preserving_thin(t, spike, 700)
    _assert(len(thin_t) <= 700, "thinning exceeded point budget")
    _assert(float(np.nanmax(thin_y)) >= 95.0, "single-sample overshoot lost during thinning")
    y_range = calculate_y_range([thin_y])
    _assert(y_range is not None and y_range[1] > 90.0, f"overshoot not visible in Y range: {y_range}")

    glitch = 60.0 + np.sin(t * 2.0 * math.pi * 25.0) * 0.08
    glitch[np.argmin(np.abs(t - 4.1))] = 1_000_000.0
    y_range = calculate_y_range([glitch])
    _assert(y_range is not None, "glitch range missing")
    _assert(y_range[1] < 200.0, f"single read glitch destroyed scale: {y_range}")

    processed = process_display_data({"speed": (t, response)}, sample_rate=1000.0, display_fps=60)
    processed_t, processed_y = processed["speed"]
    _assert(0 < len(processed_t) < len(t), "high-rate series was not decimated")
    _assert(
        float(np.nanmax(processed_y)) >= float(np.nanmax(response)) - 1e-9,
        "decimation hid overshoot energy",
    )

    slow_t = np.array([0.0, 1.0, 2.0])
    slow_y = np.array([0.0, 1.0, 0.5])
    processed = process_display_data({"slow": (slow_t, slow_y)}, sample_rate=1.0, display_fps=60)
    processed_t, processed_y = processed["slow"]
    _assert(len(processed_t) > len(slow_t), "low-rate series was not interpolated")
    _assert(np.isclose(processed_y[0], 0.0) and np.isclose(processed_y[-1], 0.5), "interpolation endpoints changed")

    previous = (58.0, 62.0)
    stable = calculate_y_range([micro], previous)
    _assert(stable is not None, "stable range missing")
    _assert(stable[0] >= previous[0] and stable[1] <= previous[1], f"stable range expanded unnecessarily: {stable}")

    print("scope_algorithms_probe: PASS")


if __name__ == "__main__":
    _run()
