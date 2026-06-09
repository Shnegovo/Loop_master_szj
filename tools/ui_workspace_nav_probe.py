"""Workspace navigation screenshot probe for LoopMaster.

The probe launches MainWindow with synthetic MCU and serial data, then captures:
LoopMaster / 变量, LoopMaster / 示波器, and 串口助手 / 串口收发. It checks
that navigation button text fits, selected states match the active page, nav
buttons do not overlap, and each content page renders non-blank.
"""

from __future__ import annotations

import argparse
import faulthandler
import math
import sys
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import BaseType, Variable  # noqa: E402
from src.ui.gui import MainWindow  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


class FakeBackend:
    def __init__(self) -> None:
        self.is_connected = True
        self.last_error = ""
        self.probe_kind = "CMSIS-DAP"
        self.probe_name = "Workspace Nav Probe"
        self.probe_uid = "NAV-0001"
        self.target_name = "cortex_m"
        self.swd_freq_khz = 4000
        self._start = time.perf_counter()
        self._halted = False

    def read_batch(self, variables):
        now = time.perf_counter() - self._start
        values = {}
        for index, (name, _addr, _ti) in enumerate(variables):
            phase = now * (1.0 + index * 0.19)
            if "target" in name:
                values[name] = 60.0
            elif "feedback" in name:
                values[name] = 60.0 + math.sin(phase * 8.0) * 1.8
            elif "output" in name:
                values[name] = 42.0 + math.sin(phase * 3.7) * 10.0
            else:
                values[name] = math.sin(phase * 2.3) * (index + 1)
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


def _pump(seconds: float = 0.2) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def _visible_buttons(buttons: list[QPushButton]) -> list[QPushButton]:
    return [
        button
        for button in buttons
        if button is not None and button.isVisible() and button.width() > 2 and button.height() > 2
    ]


def _global_rect(widget: QWidget) -> QRect:
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


def _check_buttons(buttons: list[QPushButton], label: str) -> list[str]:
    issues: list[str] = []
    visible = _visible_buttons(buttons)
    for button in visible:
        text = button.text().strip()
        if not text:
            issues.append(f"{label}: empty navigation button text")
            continue
        width_hint = button.sizeHint().width()
        if width_hint > button.width() + 12:
            issues.append(
                f"{label}: nav text may clip: text={text!r} width={button.width()} hint={width_hint}"
            )

    for left_index, left in enumerate(visible):
        left_rect = _global_rect(left)
        for right in visible[left_index + 1:]:
            intersection = left_rect.intersected(_global_rect(right))
            if intersection.isValid() and intersection.width() > 1 and intersection.height() > 1:
                issues.append(
                    f"{label}: nav buttons overlap: {left.text()!r} with {right.text()!r} "
                    f"area={intersection.width()}x{intersection.height()}"
                )
    return issues


def _checked_texts(buttons: list[QPushButton]) -> list[str]:
    return [button.text().strip() for button in _visible_buttons(buttons) if button.isChecked()]


def _page_button_texts(window: MainWindow) -> list[str]:
    return [button.text().strip() for button in _visible_buttons(list(getattr(window, "_nav_buttons", [])))]


def _domain_buttons(window: MainWindow) -> dict[str, QPushButton]:
    return dict(getattr(window, "_nav_domain_buttons", {}) or {})


def _check_navigation(window: MainWindow, expected_domain: str, expected_page: str) -> list[str]:
    issues: list[str] = []
    domain_buttons = _domain_buttons(window)
    if domain_buttons:
        issues.extend(_check_buttons(list(domain_buttons.values()), "domain"))
        checked_domains = [key for key, button in domain_buttons.items() if button.isVisible() and button.isChecked()]
        if checked_domains != [expected_domain]:
            issues.append(f"domain: checked={checked_domains!r} expected={[expected_domain]!r}")

    page_buttons = list(getattr(window, "_nav_buttons", []))
    issues.extend(_check_buttons(page_buttons, "page"))
    checked_pages = _checked_texts(page_buttons)
    if len(checked_pages) != 1 or expected_page not in checked_pages[0]:
        issues.append(f"page: checked={checked_pages!r} expected contains {expected_page!r}")
    if expected_page not in " | ".join(_page_button_texts(window)):
        issues.append(f"page: visible buttons={_page_button_texts(window)!r} missing {expected_page!r}")
    return issues


def _image_stats(widget: QWidget) -> dict[str, float]:
    image = widget.grab().toImage().convertToFormat(QImage.Format_RGB32)
    width = image.width()
    height = image.height()
    step_x = max(1, width // 96)
    step_y = max(1, height // 72)
    colors: set[tuple[int, int, int]] = set()
    luminance_values: list[int] = []
    non_white = 0
    samples = 0
    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            color = image.pixelColor(x, y)
            r, g, b = color.red(), color.green(), color.blue()
            colors.add((r // 16, g // 16, b // 16))
            luminance = (299 * r + 587 * g + 114 * b) // 1000
            luminance_values.append(luminance)
            if not (r > 246 and g > 246 and b > 246):
                non_white += 1
            samples += 1
    contrast = float(max(luminance_values) - min(luminance_values)) if luminance_values else 0.0
    return {
        "width": float(width),
        "height": float(height),
        "unique": float(len(colors)),
        "contrast": contrast,
        "non_white_ratio": float(non_white / max(1, samples)),
    }


def _check_content(widget: QWidget, label: str) -> list[str]:
    stats = _image_stats(widget)
    issues: list[str] = []
    if stats["width"] < 200 or stats["height"] < 200:
        issues.append(f"{label}: content widget too small: {stats['width']:.0f}x{stats['height']:.0f}")
    if stats["unique"] < 10 or stats["contrast"] < 16 or stats["non_white_ratio"] < 0.015:
        issues.append(
            f"{label}: content looks blank: unique={stats['unique']:.0f} "
            f"contrast={stats['contrast']:.0f} non_white={stats['non_white_ratio']:.3f}"
        )
    return issues


def _save(window: MainWindow, output_dir: Path, name: str) -> Path:
    QApplication.processEvents()
    path = output_dir / f"{name}.png"
    window.grab().save(str(path))
    return path


def _seed_loopmaster(window: MainWindow, output_dir: Path) -> None:
    fake_backend = FakeBackend()
    window._config_path = output_dir / "probe-loopmaster.json"
    window._backend = fake_backend
    window._collector.set_backend(fake_backend)
    window._elf_path = Path("workspace_nav_demo.axf")
    window._recent_elf_path = window._elf_path
    window._loaded_elf_this_session = True
    window._update_conn_status(True)

    float_type = BaseType("float", 4, "float")
    int_type = BaseType("int32_t", 4, "signed")
    variables = [
        Variable("speed.target", 0x20002000, 4, float_type, file_name="control_loop.c"),
        Variable("speed.feedback", 0x20002004, 4, float_type, file_name="control_loop.c"),
        Variable("speed.error", 0x20002008, 4, float_type, file_name="control_loop.c"),
        Variable("pid.output", 0x2000200C, 4, float_type, file_name="pid.c"),
        Variable("motor.current", 0x20002010, 4, float_type, file_name="motor.c"),
        Variable("system.state", 0x20002014, 4, int_type, file_name="system.c"),
    ]
    selected = [variable.name for variable in variables[:5]]
    window._variables = variables
    window._saved_monitored_variables = selected
    window._saved_scope_assignments = {
        "speed.target": (True, False, False),
        "speed.feedback": (True, False, False),
        "speed.error": (False, True, False),
        "pid.output": (False, True, False),
        "motor.current": (False, False, True),
    }
    window._populate_tree()
    window._tree.expandAll()
    window._toggle_scope_sidebar(True, save=False)
    window._set_scope_pane_count(3, save=False)
    window._sync_value_table_placeholders()
    window._rate_combo.setCurrentIndex(3)
    window._time_window_spin.setValue(8)


def _seed_serial(window: MainWindow) -> None:
    tab = window._tab_serial
    tab.set_ports(["COM14 - USB Serial Loopback", "COM3 - Bluetooth"])
    index = tab.port_combo.findData("COM14 - USB Serial Loopback")
    if index >= 0:
        tab.port_combo.setCurrentIndex(index)
    tab.set_connected(True)
    tab.append_log("已打开 COM14 @ 115200, FireWater CSV")
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


def run(output_dir: Path, width: int, height: int) -> int:
    faulthandler.enable()
    faulthandler.dump_traceback_later(60, repeat=False)
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
    window.resize(width, height)
    window.show()
    _pump(0.25)
    _seed_loopmaster(window, output_dir)
    _seed_serial(window)
    _pump(0.2)

    screenshots: list[Path] = []
    issues: list[str] = []

    window._show_workspace_page("variables")
    _pump(0.2)
    screenshots.append(_save(window, output_dir, "01_loopmaster_variables"))
    issues.extend(_check_navigation(window, "loopmaster", "变量"))
    issues.extend(_check_content(window._tabs.currentWidget(), "LoopMaster/变量"))
    if window._tree.topLevelItemCount() <= 0:
        issues.append("LoopMaster/变量: variable tree is empty")

    window._show_workspace_page("scope")
    window._on_start()
    _pump(0.8)
    screenshots.append(_save(window, output_dir, "02_loopmaster_scope"))
    issues.extend(_check_navigation(window, "loopmaster", "示波器"))
    issues.extend(_check_content(window._tabs.currentWidget(), "LoopMaster/示波器"))
    if window._value_table.rowCount() <= 0:
        issues.append("LoopMaster/示波器: live value table is empty")

    window._show_workspace_page("serial")
    _pump(0.25)
    screenshots.append(_save(window, output_dir, "03_serial_assistant"))
    issues.extend(_check_navigation(window, "serial", "串口收发"))
    issues.extend(_check_content(window._tabs.currentWidget(), "串口助手/串口收发"))
    if not window._tab_serial.log_view.toPlainText().strip():
        issues.append("串口助手/串口收发: serial log is empty")

    window._on_stop()
    window.close()
    QApplication.processEvents()
    app.quit()
    faulthandler.cancel_dump_traceback_later()

    if issues:
        print("FAIL workspace-nav probe", flush=True)
        for issue in dict.fromkeys(issues):
            print(f"- {issue}", flush=True)
        print("screenshots:", flush=True)
        for path in screenshots:
            print(path, flush=True)
        return 1

    print("PASS workspace-nav probe", flush=True)
    for path in screenshots:
        print(path, flush=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "tools" / "ui-workspace-nav")
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=820)
    args = parser.parse_args()
    code = run(args.output_dir, max(1100, args.width), max(650, args.height))
    sys.stdout.flush()
    sys.stderr.flush()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
