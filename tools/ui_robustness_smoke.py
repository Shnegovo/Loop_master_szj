"""Random UI smoke test for LoopMaster.

This test does not require hardware. It monkey-patches probe scanning, then
clicks and types through the main controls while checking that key layout
bands do not overlap or spill outside their parent.
"""

from __future__ import annotations

import argparse
import random
import string
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QWidget

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.mem_backend import SWDBackend  # noqa: E402
from src.ui.gui import MainWindow  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


FAKE_PROBES = [
    {
        "uid": "STLINKV3-12345678",
        "name": "STLINK-V3",
        "vendor": "STMicroelectronics",
        "kind": "ST-Link",
    },
    {
        "uid": "CMSISDAP-87654321",
        "name": "CMSIS-DAP",
        "vendor": "Arm",
        "kind": "DAPLink/CMSIS-DAP",
    },
]


def _layout_widgets(parent: QWidget) -> list[QWidget]:
    layout = parent.layout()
    if layout is None:
        return []
    widgets: list[QWidget] = []
    for index in range(layout.count()):
        widget = layout.itemAt(index).widget()
        if widget is not None and widget.isVisible():
            widgets.append(widget)
    return widgets


def _check_band(parent: QWidget, name: str) -> list[str]:
    errors: list[str] = []
    parent_rect = parent.rect().adjusted(-1, -1, 1, 1)
    widgets = [
        widget for widget in _layout_widgets(parent)
        if widget.width() > 2 and widget.height() > 2
    ]

    for widget in widgets:
        if not parent_rect.contains(widget.geometry()):
            errors.append(
                f"{name}: {widget.objectName() or widget.__class__.__name__} "
                f"outside parent: {widget.geometry().getRect()}"
            )

    for left_index, left in enumerate(widgets):
        for right in widgets[left_index + 1:]:
            intersection = left.geometry().intersected(right.geometry())
            if intersection.isValid() and intersection.width() > 1 and intersection.height() > 1:
                errors.append(
                    f"{name}: overlap "
                    f"{left.objectName() or left.__class__.__name__}={left.geometry().getRect()} "
                    f"with {right.objectName() or right.__class__.__name__}={right.geometry().getRect()}"
                )
    return errors


def _check_layout(window: MainWindow) -> list[str]:
    errors: list[str] = []
    errors.extend(_check_band(window._conn_bar_widget, "connectionBar"))
    for object_name in ("toolbarCard", "controlBar", "plotToolBar", "debugBar"):
        for frame in window.findChildren(QFrame, object_name):
            if frame.isVisible():
                errors.extend(_check_band(frame, object_name))
    return errors


def _random_target(rng: random.Random) -> str:
    samples = [
        "cortex_m",
        "stm32f103rc",
        "stm32h743xx",
        "mspm0g3507",
        "m0g3507",
        "STM32F103RC",
        "",
        "x" * rng.randint(1, 48),
    ]
    return rng.choice(samples)


def _click(widget: QPushButton) -> None:
    QTest.mouseClick(widget, Qt.LeftButton)
    QApplication.processEvents()


def run(iterations: int, seed: int, widths: list[int], output_dir: Path) -> None:
    rng = random.Random(seed)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_pcl_theme(app)

    scan_payloads = [[], FAKE_PROBES]

    def fake_scan():
        QApplication.processEvents()
        return rng.choice(scan_payloads)

    SWDBackend.scan_probes = staticmethod(fake_scan)

    window = MainWindow()
    window.show()
    QApplication.processEvents()
    window._monitored = {
        "motor.speed",
        "sensor.filtered_voltage",
        "very.long.variable.path.for.color.robustness.value",
    }
    window._update_selected_list()
    QApplication.processEvents()

    failures: list[str] = []
    for width in widths:
        window.resize(width, 820)
        QApplication.processEvents()
        failures.extend(_check_layout(window))

    buttons = [
        button for button in (
            window._btn_scan,
            window._mode_attach_btn,
            window._mode_reset_btn,
            window._x_scroll_btn,
            window._y_auto_btn,
            window._btn_next_color,
            window._btn_reset_colors,
            window._btn_halt_target,
            window._btn_resume_target,
            getattr(window, "_btn_step_target", None),
        )
        if button is not None
    ]

    for step in range(iterations):
        action = rng.randrange(10)
        if action == 0:
            _click(window._btn_scan)
        elif action == 1:
            window._tabs.setCurrentIndex(rng.randrange(window._tabs.count()))
        elif action == 2:
            window._target_combo.setEditText(_random_target(rng))
        elif action == 3:
            if window._target_combo.lineEdit():
                window._target_combo.lineEdit().selectAll()
                text = "".join(rng.choice(string.ascii_letters + string.digits + "_-") for _ in range(rng.randint(1, 32)))
                QTest.keyClicks(window._target_combo.lineEdit(), text)
        elif action == 4:
            window._target_combo.setCurrentIndex(rng.randrange(max(1, window._target_combo.count())))
        elif action == 5:
            window._rate_combo.setCurrentIndex(rng.randrange(window._rate_combo.count()))
        elif action == 6:
            _click(rng.choice(buttons))
        elif action == 7:
            if window._curve_color_combo.count() > 0:
                window._curve_color_combo.setCurrentIndex(rng.randrange(window._curve_color_combo.count()))
        elif action == 8:
            window.resize(rng.choice(widths), rng.randint(720, 940))
        else:
            _click(window._btn_next_color)

        QApplication.processEvents()
        layout_errors = _check_layout(window)
        if layout_errors:
            failures.extend([f"step {step}: {err}" for err in layout_errors])
            break

    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot = output_dir / "ui-robustness-final.png"
    QTest.qWait(180)
    QApplication.processEvents()
    window.grab().save(str(screenshot))
    window.close()

    if failures:
        print("\n".join(failures))
        print(f"screenshot={screenshot}")
        raise SystemExit(1)

    print(f"PASS iterations={iterations} seed={seed} screenshot={screenshot}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20260521)
    parser.add_argument("--widths", type=str, default="1100,1280,1500,1878")
    parser.add_argument("--output-dir", type=Path, default=Path(r"D:\LoopMaster_v2.0"))
    args = parser.parse_args()
    widths = [int(item.strip()) for item in args.widths.split(",") if item.strip()]
    run(args.iterations, args.seed, widths, args.output_dir)


if __name__ == "__main__":
    main()
