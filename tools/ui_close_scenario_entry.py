#!/usr/bin/env python3
"""Scenario entry used by ui_close_process_probe.

It runs a real MainWindow with synthetic backends so the parent probe can close
the process through WM_CLOSE while sampling or worker threads are active.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import pyqtgraph as pg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import BaseType  # noqa: E402
from src.ui.gui import MainWindow  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


class CloseProbeBackend:
    def __init__(self, delay: float = 0.0) -> None:
        self.is_connected = True
        self.last_error = ""
        self.probe_kind = "CloseProbe"
        self.probe_name = "Close Scenario Probe"
        self.target_name = "cortex_m"
        self.swd_freq_khz = 4000
        self._delay = max(0.0, float(delay))
        self._start = time.perf_counter()
        self._shutdown_requested = False

    def request_shutdown(self) -> None:
        self._shutdown_requested = True
        self.is_connected = False

    def disconnect(self, timeout: float | None = None) -> None:
        self.request_shutdown()

    def target_state(self) -> str:
        return "Disconnected" if self._shutdown_requested else "Running"

    def read_batch_rows(self, variables, num_samples: int):
        if self._delay:
            time.sleep(self._delay)
        if self._shutdown_requested:
            raise RuntimeError("backend shutting down")
        rows = []
        now = time.perf_counter() - self._start
        for sample_index in range(max(1, int(num_samples))):
            row = []
            for index, (_name, _addr, _ti) in enumerate(variables):
                row.append(60.0 + math.sin(now * 6 + sample_index * 0.05 + index) * (index + 1))
            rows.append(row)
        return rows

    def read_batch(self, variables):
        return {
            name: row_value
            for (name, _addr, _ti), row_value in zip(variables, self.read_batch_rows(variables, 1)[0])
        }


def _seed_scope(window: MainWindow) -> None:
    float_type = BaseType("float", 4, "float")
    variables = {
        "close_probe.speed.target": (0x20000000, float_type),
        "close_probe.speed.feedback": (0x20000004, float_type),
        "close_probe.pid.output": (0x20000008, float_type),
    }
    window._registry = dict(variables)
    window._monitored = set(variables)
    window._elf_path = Path("close_probe.axf")
    window._recent_elf_path = window._elf_path
    window._loaded_elf_this_session = True
    window._set_scope_pane_count(2, save=False)
    window._show_workspace_page("scope")


def _start_serial_worker(window: MainWindow, seconds: float, *, ignore_shutdown: bool = False) -> None:
    def worker():
        deadline = time.perf_counter() + max(0.0, float(seconds))
        while time.perf_counter() < deadline:
            if not ignore_shutdown and getattr(window, "_shutting_down", False):
                break
            time.sleep(0.02)

    window._start_serial_worker(worker, "LoopMaster-close-probe-serial-worker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a LoopMaster close scenario window.")
    parser.add_argument(
        "--scenario",
        choices=("idle", "sampling", "slow-sampling", "serial-worker", "stuck-serial-worker"),
        default="idle",
    )
    args = parser.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)
    app.setStyle("Fusion")
    apply_pcl_theme(app)
    pg.setConfigOptions(background=(255, 255, 255), foreground=(83, 101, 125), antialias=False)

    delay = 0.25 if args.scenario == "slow-sampling" else 0.0
    window = MainWindow()
    window._config_path = ROOT / "tools" / "ui-close-scenario-runtime" / "loopmaster.json"
    window._config_path.parent.mkdir(parents=True, exist_ok=True)
    backend = CloseProbeBackend(delay=delay)
    window._backend = backend
    window._collector.set_backend(backend)
    window._update_conn_status(True)
    _seed_scope(window)

    if args.scenario in {"sampling", "slow-sampling"}:
        window._rate_combo.setCurrentIndex(window._rate_combo.findText("1000 Hz"))
        QTimer.singleShot(100, window._on_start)
    elif args.scenario == "serial-worker":
        QTimer.singleShot(100, lambda: _start_serial_worker(window, 12.0))
    elif args.scenario == "stuck-serial-worker":
        QTimer.singleShot(100, lambda: _start_serial_worker(window, 12.0, ignore_shutdown=True))

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
