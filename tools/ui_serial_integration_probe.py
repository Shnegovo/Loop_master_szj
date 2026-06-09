"""Screenshot probe for the integrated Serial workspace page."""

from __future__ import annotations

import argparse
import ctypes
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

from src.ui.gui import MainWindow  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


def _pump(app: QApplication, seconds: float = 0.25) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.01)


def run(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_pcl_theme(app)
    pg.setConfigOptions(
        background=(255, 255, 255),
        foreground=(83, 101, 125),
        antialias=False,
    )

    window = MainWindow()
    window._config_path = output_dir / "probe-loopmaster.json"
    window.resize(1400, 820)
    window.show()
    _pump(app, 0.35)

    window._show_workspace_page("serial")
    _pump(app, 0.2)
    tab = window._tab_serial
    tab.set_ports(["COM14 - USB Serial Loopback", "COM3 - Bluetooth"])
    index = tab.port_combo.findData("COM14 - USB Serial Loopback")
    if index >= 0:
        tab.port_combo.setCurrentIndex(index)
    tab.set_connected(True)
    tab.append_log("已打开 COM14 @ 115200, firewater")
    tab.append_log("d:0.0,60.0,0.0")
    tab.append_log("d:0.2,60.9,0.4")
    tab.send_edit.setText("d:1,2,3")

    t = np.linspace(0.0, 10.0, 1500)
    speed = 60.0 + np.sin(t * 9.0) * 1.6 + np.sin(t * 41.0) * 0.18
    target = np.full_like(t, 60.0)
    output = np.clip(40.0 + np.sin(t * 3.4) * 8.0, 20.0, 80.0)
    tab.set_scope_data(
        {
            "speed.feedback": (t, speed),
            "speed.target": (t, target),
            "pid.output": (t, output),
        }
    )
    _pump(app, 0.25)

    screenshot = output_dir / "ui-serial-integration.png"
    window.grab().save(str(screenshot))
    print(f"PASS serial integration screenshot={screenshot}", flush=True)

    # This is a visual probe, not the lifecycle probe. ui_close_process_probe.py
    # owns close/WM_CLOSE behavior; exiting here avoids Qt teardown flakiness in
    # screenshot-only runs.
    if os.name == "nt":
        ctypes.windll.kernel32.ExitProcess(0)
    os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "ui-serial-integration")
    args = parser.parse_args()
    run(args.output_dir)


if __name__ == "__main__":
    main()
