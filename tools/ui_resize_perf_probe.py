"""Resize-performance probe for the LoopMaster Qt UI.

The probe creates a live 3-pane scope, then repeatedly resizes the window as a
user would while dragging a corner. It measures how long Qt needs to process
each resize burst and fails if the UI spends too long in layout/repaint work.
"""

from __future__ import annotations

import argparse
import faulthandler
import math
import os
import statistics
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication
import pyqtgraph as pg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import BaseType  # noqa: E402
from src.ui.gui import MainWindow  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


class FakeBackend:
    def __init__(self) -> None:
        self.is_connected = True
        self.last_error = ""
        self.probe_kind = "CMSIS-DAP"
        self.probe_name = "Resize Perf Probe"
        self.probe_uid = "RESIZE-0001"
        self.target_name = "cortex_m"
        self.swd_freq_khz = 4000
        self._start = time.perf_counter()
        self._halted = False

    def read_batch(self, variables):
        now = time.perf_counter() - self._start
        values = {}
        for index, (name, _addr, _ti) in enumerate(variables):
            phase = now * (1.0 + index * 0.23)
            values[name] = 60.0 + math.sin(phase * 8.0) * (index + 1)
        return values

    def target_state(self) -> str:
        return "Halted" if self._halted else "Running"

    def halt_target(self) -> bool:
        self._halted = True
        return True

    def resume_target(self) -> bool:
        self._halted = False
        return True

    def is_target_halted(self) -> bool:
        return self._halted

    def disconnect(self) -> None:
        self.is_connected = False


def _pump(duration: float = 0.1) -> None:
    deadline = time.perf_counter() + duration
    while time.perf_counter() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)


def _prepare_window(output_dir: Path) -> MainWindow:
    window = MainWindow()
    window._config_path = output_dir / "probe-loopmaster.json"
    fake_backend = FakeBackend()
    window._backend = fake_backend
    window._collector.set_backend(fake_backend)
    window._elf_path = Path("resize_perf_demo.axf")
    window._recent_elf_path = window._elf_path
    window._loaded_elf_this_session = True
    window._update_conn_status(True)

    float_type = BaseType("float", 4, "float")
    variables = {
        "speed.target": (0x20002000, float_type),
        "speed.feedback": (0x20002004, float_type),
        "speed.error": (0x20002008, float_type),
        "pid.output": (0x2000200C, float_type),
        "gyro.rate": (0x20002010, float_type),
        "motor.current": (0x20002014, float_type),
    }
    window._registry = dict(variables)
    window._monitored = set(variables)
    window._saved_scope_assignments = {
        "speed.target": (True, False, False),
        "speed.feedback": (True, False, False),
        "speed.error": (False, True, False),
        "pid.output": (False, True, False),
        "gyro.rate": (False, False, True),
        "motor.current": (False, False, True),
    }
    window.resize(1500, 860)
    window.show()
    window._tabs.setCurrentIndex(1)
    window._toggle_scope_sidebar(True, save=False)
    window._set_scope_pane_count(3, save=False)
    window._sync_value_table_placeholders()
    window._rate_combo.setCurrentIndex(3)
    window._time_window_spin.setValue(8)
    window._on_start()
    _pump(0.35)
    return window


def run(output_dir: Path, iterations: int) -> None:
    faulthandler.enable()
    faulthandler.dump_traceback_later(45, repeat=False)
    output_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_pcl_theme(app)
    pg.setConfigOptions(
        background=(255, 255, 255),
        foreground=(83, 101, 125),
        antialias=False,
    )

    window = _prepare_window(output_dir)
    samples: list[tuple[int, int, int, float]] = []
    widths = [1500, 1440, 1380, 1320, 1260, 1200, 1140, 1100, 1160, 1240, 1320, 1400, 1500]
    heights = [860, 840, 810, 780, 760, 780, 810, 840]

    for width, height in ((1480, 850), (1500, 860)):
        window.resize(width, height)
        QApplication.processEvents()

    for index in range(iterations):
        width = widths[index % len(widths)]
        height = heights[index % len(heights)]
        started = time.perf_counter()
        window.resize(width, height)
        QApplication.processEvents()
        samples.append((index, width, height, (time.perf_counter() - started) * 1000.0))

    _pump(0.35)
    screenshot = output_dir / "ui-resize-perf-final.png"
    window.grab().save(str(screenshot))
    window.close()
    QApplication.processEvents()
    app.quit()

    durations = [sample[3] for sample in samples]
    ordered = sorted(durations)
    p95 = ordered[max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))]
    p99 = ordered[max(0, min(len(ordered) - 1, int(len(ordered) * 0.99) - 1))]
    avg = statistics.fmean(durations)
    worst = max(durations)
    slow = [sample for sample in samples if sample[3] > 24.0]
    top = sorted(samples, key=lambda sample: sample[3], reverse=True)[:5]
    print(
        f"resize steps={iterations} avg={avg:.2f}ms p95={p95:.2f}ms "
        f"p99={p99:.2f}ms max={worst:.2f}ms slow>24ms={len(slow)} "
        f"top={[(i, w, h, round(ms, 2)) for i, w, h, ms in top]} "
        f"screenshot={screenshot}",
        flush=True,
    )

    if p95 > 18.0 or worst > 60.0:
        raise SystemExit("resize performance budget exceeded")

    faulthandler.cancel_dump_traceback_later()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "ui-resize-perf")
    parser.add_argument("--iterations", type=int, default=220)
    args = parser.parse_args()
    run(args.output_dir, max(1, args.iterations))


if __name__ == "__main__":
    main()
