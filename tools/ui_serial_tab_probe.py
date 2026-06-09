"""Run the standalone serial assistant tab for local UI probing."""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui.serial_tab import SerialTab  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    widget = SerialTab()
    widget.resize(1120, 760)
    widget.setWindowTitle("Serial Assistant Probe")
    widget.set_ports(["COM1", "COM7 - USB Serial Demo"])
    widget.show()

    start = time.monotonic()

    def tick() -> None:
        t = time.monotonic() - start
        widget.add_sample(math.sin(t * 3.0) + math.sin(t * 0.7) * 0.25, t)
        if int(t * 2) % 12 == 0:
            widget.append_log(f"demo,{t:.3f},{math.sin(t):.4f}")

    timer = QTimer(widget)
    timer.timeout.connect(tick)
    timer.start(50)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
