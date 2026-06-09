"""PID-oriented waveform probe for LoopMaster scope visuals.

This feeds synthetic high-rate signals into the real Qt scope and checks
whether PID-tuning cases remain readable: step response, high-frequency
jitter, tiny numeric noise, preserved overshoot spikes, and absurd read
glitches that should not destroy the Y scale.
"""

from __future__ import annotations

import argparse
import faulthandler
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication
import pyqtgraph as pg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import BaseType  # noqa: E402
from src.ui.gui import MainWindow  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


class FakeBackend:
    is_connected = True
    last_error = ""
    probe_kind = "CMSIS-DAP"
    probe_name = "PID Visual Probe"
    probe_uid = "PID-0001"
    target_name = "cortex_m"
    swd_freq_khz = 4000

    def target_state(self) -> str:
        return "Running"

    def read_batch(self, variables):
        return {name: 0.0 for name, _addr, _ti in variables}

    def disconnect(self) -> None:
        self.is_connected = False


def _pump(duration: float = 0.15) -> None:
    deadline = time.perf_counter() + duration
    while time.perf_counter() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)


def _fill_collector(window: MainWindow, t: np.ndarray, series: dict[str, np.ndarray], rate: int) -> None:
    float_type = BaseType("float", 4, "float")
    variables = [
        (name, 0x20001000 + index * 4, float_type)
        for index, name in enumerate(series)
    ]
    window._registry = {name: (addr, ti) for name, addr, ti in variables}
    window._monitored = set(series)
    window._collector.configure(rate, buffer_seconds=max(4.0, float(t[-1] - t[0] + 1.0)))
    window._collector.set_variables(variables)
    with window._collector._lock:
        timestamps = window._collector._timestamps
        buffers = window._collector._buffers
        if timestamps is None:
            raise RuntimeError("collector timestamps missing")
        for sample_index, timestamp in enumerate(t):
            timestamps.append(float(timestamp))
            for name, values in series.items():
                buffers[name].append(float(values[sample_index]))
    window._collector._actual_rate = float(rate)
    window._collector._sample_count = int(t.size)


def _show_series(
    window: MainWindow,
    output_dir: Path,
    name: str,
    series: dict[str, np.ndarray],
    assignments: dict[str, tuple[bool, bool, bool]],
    t: np.ndarray,
    pane_count: int = 1,
) -> tuple[Path, list[str]]:
    issues: list[str] = []
    print(f"[pid-wave] render {name}", flush=True)
    window._saved_scope_assignments = dict(assignments)
    _fill_collector(window, t, series, 1000)
    window._tabs.setCurrentIndex(1)
    window._toggle_scope_sidebar(False, save=False)
    window._set_scope_pane_count(pane_count, save=False)
    window._time_window_spin.setValue(8)
    window._x_scroll_btn.setChecked(True)
    window._y_auto_btn.setChecked(True)
    for pane in window._scope_panes:
        pane.y_range = None
    window._sync_value_table_placeholders()
    window._sync_plot_curve_state(force=True)
    window._update_plot(force=True)
    _pump(0.18)
    path = output_dir / f"{name}.png"
    window.grab().save(str(path))
    print(f"[pid-wave] saved {path.name}", flush=True)
    return path, issues


def _view_y_range(window: MainWindow, pane_index: int = 0) -> tuple[float, float]:
    return tuple(float(v) for v in window._scope_panes[pane_index].plot.getViewBox().viewRange()[1])


def _curve_values(window: MainWindow, curve_name: str, pane_index: int = 0) -> np.ndarray:
    curve = window._scope_panes[pane_index].curves[curve_name]
    _x, y = curve.getData()
    return np.asarray(y, dtype=float)


def run(output_dir: Path) -> None:
    faulthandler.enable()
    faulthandler.dump_traceback_later(30, repeat=False)
    print("[pid-wave] create app", flush=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_pcl_theme(app)
    pg.setConfigOptions(
        background=(255, 255, 255),
        foreground=(83, 101, 125),
        antialias=False,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    print("[pid-wave] create window", flush=True)
    window = MainWindow()
    window._config_path = output_dir / "probe-loopmaster.json"
    fake_backend = FakeBackend()
    window._backend = fake_backend
    window._collector.set_backend(fake_backend)
    window._elf_path = Path("pid_waveform_demo.axf")
    window._recent_elf_path = window._elf_path
    window._loaded_elf_this_session = True
    window._update_conn_status(True)
    window.resize(1500, 860)
    window.show()
    _pump(0.22)
    print("[pid-wave] start cases", flush=True)

    screenshots: list[Path] = []
    issues: list[str] = []
    t = np.linspace(0.0, 8.0, 8000)
    target = np.full_like(t, 60.0)

    response = 60.0 * (1.0 - np.exp(-t * 1.35))
    response += 18.0 * np.exp(-np.maximum(t - 1.25, 0.0) * 1.3) * np.sin(np.maximum(t - 1.25, 0.0) * 8.5)
    response[t < 0.08] = 0.0
    response += np.where(t > 2.5, np.sin(t * 90.0) * 0.42, 0.0)
    path, found = _show_series(
        window,
        output_dir,
        "01_pid_step_overshoot_jitter",
        {"speed.target": target, "speed.feedback": response},
        {"speed.target": (True, False, False), "speed.feedback": (True, False, False)},
        t,
    )
    screenshots.append(path)
    issues.extend(found)
    y_min, y_max = _view_y_range(window)
    if y_min > -5 or y_max < 66:
        issues.append(f"step overshoot range too tight: {(y_min, y_max)}")

    tiny = 60.0 + np.sin(t * 2.0 * math.pi * 120.0) * 0.0006
    path, found = _show_series(
        window,
        output_dir,
        "02_steady_micro_jitter_not_overzoomed",
        {"speed.micro_jitter": tiny},
        {"speed.micro_jitter": (True, False, False)},
        t,
    )
    screenshots.append(path)
    issues.extend(found)
    y_min, y_max = _view_y_range(window)
    if (y_max - y_min) < 0.45:
        issues.append(f"micro jitter over-zoomed: span={y_max - y_min:.6f}")
    if (y_max - y_min) > 2.5:
        issues.append(f"micro jitter under-readable: span={y_max - y_min:.6f}")

    small = 60.0 + np.sin(t * 2.0 * math.pi * 85.0) * 0.75 + np.sin(t * 2.0 * math.pi * 17.0) * 0.2
    path, found = _show_series(
        window,
        output_dir,
        "03_steady_highfreq_small_jitter",
        {"speed.small_jitter": small},
        {"speed.small_jitter": (True, False, False)},
        t,
    )
    screenshots.append(path)
    issues.extend(found)
    y_min, y_max = _view_y_range(window)
    if not (1.0 <= (y_max - y_min) <= 8.0):
        issues.append(f"small jitter range not useful: span={y_max - y_min:.3f}")

    spike = 60.0 + np.sin(t * 2.0 * math.pi * 8.0) * 0.25
    spike[np.argmin(np.abs(t - 3.25))] = 95.0
    path, found = _show_series(
        window,
        output_dir,
        "04_single_sample_overshoot_preserved",
        {"speed.one_sample_overshoot": spike},
        {"speed.one_sample_overshoot": (True, False, False)},
        t,
    )
    screenshots.append(path)
    issues.extend(found)
    displayed = _curve_values(window, "speed.one_sample_overshoot")
    y_min, y_max = _view_y_range(window)
    if displayed.size == 0 or float(np.nanmax(displayed)) < 90.0:
        issues.append("single-sample overshoot was lost by display thinning")
    if y_max < 90.0:
        issues.append(f"single-sample overshoot not in Y range: {(y_min, y_max)}")

    glitch = 60.0 + np.sin(t * 2.0 * math.pi * 25.0) * 0.08
    glitch[np.argmin(np.abs(t - 4.1))] = 1_000_000.0
    path, found = _show_series(
        window,
        output_dir,
        "05_absurd_read_glitch_clipped",
        {"speed.absurd_read_glitch": glitch},
        {"speed.absurd_read_glitch": (True, False, False)},
        t,
    )
    screenshots.append(path)
    issues.extend(found)
    y_min, y_max = _view_y_range(window)
    if y_max > 200.0:
        processed = window._process_display_data(window._collector.get_data())
        debug_vals = np.asarray(processed["speed.absurd_read_glitch"][1], dtype=float)
        issues.append(
            "absurd glitch destroyed Y scale: "
            f"{(y_min, y_max)} y_auto={window._y_auto_btn.isChecked()} "
            f"stored={window._scope_panes[0].y_range} "
            f"processed_n={debug_vals.size} p5={np.percentile(debug_vals, 5):.3g} "
            f"p95={np.percentile(debug_vals, 95):.3g} max={np.max(debug_vals):.3g}"
        )

    mixed = {
        "speed.target": target,
        "speed.feedback": response,
        "speed.small_jitter": small,
        "speed.micro_jitter": tiny,
        "speed.one_sample_overshoot": spike,
    }
    path, found = _show_series(
        window,
        output_dir,
        "06_three_pane_mixed_pid",
        mixed,
        {
            "speed.target": (True, False, False),
            "speed.feedback": (True, False, False),
            "speed.small_jitter": (False, True, False),
            "speed.micro_jitter": (False, True, False),
            "speed.one_sample_overshoot": (False, False, True),
        },
        t,
        pane_count=3,
    )
    screenshots.append(path)
    issues.extend(found)

    if issues:
        print("[pid-wave] issues found", flush=True)
        print("\n".join(dict.fromkeys(issues)))
        print("screenshots:")
        for path in screenshots:
            print(path)
        sys.stdout.flush()
        sys.stderr.flush()
        faulthandler.disable()
        os._exit(1)

    print("[pid-wave] all checks passed", flush=True)
    print("PASS pid-waveform probe")
    for path in screenshots:
        print(path)
    faulthandler.cancel_dump_traceback_later()
    faulthandler.disable()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "ui-pid-waveforms")
    args = parser.parse_args()
    run(args.output_dir)


if __name__ == "__main__":
    main()
