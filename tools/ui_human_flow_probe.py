"""Human-flow UI probe for LoopMaster.

Runs the Qt app with a fake connected target and live synthetic signals, then
drives the same actions a user naturally tries: switching panes, dragging
splitters, resizing the window, hiding controls, and starting/stopping capture.
It saves screenshots and reports layout or interaction issues.
"""

from __future__ import annotations

import argparse
import faulthandler
import math
import os
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget
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
        self.probe_name = "Human Flow Probe"
        self.probe_uid = "SIM-0001"
        self.target_name = "cortex_m"
        self.swd_freq_khz = 4000
        self._start = time.perf_counter()
        self._halted = False
        self.halt_calls = 0
        self.resume_calls = 0

    def read_batch(self, variables):
        now = time.perf_counter() - self._start
        values = {}
        for index, (name, _addr, _ti) in enumerate(variables):
            phase = now * (0.9 + index * 0.28)
            if name.endswith("rpm"):
                values[name] = 2650 + math.sin(phase) * 320 + math.sin(phase * 0.37) * 80
            elif "temperature" in name:
                values[name] = 42 + math.sin(phase * 0.24) * 4
            elif "duty" in name:
                values[name] = 48 + math.sin(phase * 0.8) * 22
            elif "error" in name:
                values[name] = math.sin(phase * 1.7) * 0.35
            else:
                values[name] = math.sin(phase) * (index + 1)
        return values

    def halt_target(self) -> bool:
        self.halt_calls += 1
        self._halted = True
        return True

    def resume_target(self) -> bool:
        self.resume_calls += 1
        self._halted = False
        return True

    def is_target_halted(self) -> bool:
        return self._halted

    def target_state(self) -> str:
        return "Halted" if self._halted else "Running"

    def disconnect(self) -> None:
        self.is_connected = False


def _click(widget: QPushButton) -> None:
    widget.click()
    QApplication.processEvents()


def _visible_widgets(root: QWidget):
    for widget in root.findChildren(QWidget):
        if widget.isVisible() and widget.width() > 0 and widget.height() > 0:
            yield widget


def _check_text_fit(root: QWidget) -> list[str]:
    issues: list[str] = []
    for widget in _visible_widgets(root):
        if not isinstance(widget, (QPushButton, QLabel)):
            continue
        text = widget.text().strip()
        if not text or "\n" in text:
            continue
        hint = widget.sizeHint()
        if hint.width() > widget.width() + 10:
            issues.append(
                f"text clipped: {widget.__class__.__name__}#{widget.objectName()} "
                f"{widget.width()}px < hint {hint.width()}px text={text!r}"
            )
    return issues


def _check_pane_state(window: MainWindow) -> list[str]:
    issues: list[str] = []
    count = window._scope_pane_count
    visible = [pane.frame.isVisible() for pane in window._scope_panes]
    if sum(visible) != count:
        issues.append(f"pane visibility mismatch: count={count} visible={visible}")
    if count >= 2:
        sizes = window._scope_pane_splitter.sizes()
        if len(sizes) != 2 or min(sizes) < 160:
            issues.append(f"bad horizontal pane sizes: {sizes}")
    if count == 3:
        sizes = window._scope_right_splitter.sizes()
        if len(sizes) != 2 or min(sizes) < 120:
            issues.append(f"bad vertical pane sizes: {sizes}")
    return issues


def _save(window: MainWindow, output_dir: Path, name: str) -> Path:
    QApplication.processEvents()
    path = output_dir / f"{name}.png"
    screen = window.screen() or QApplication.primaryScreen()
    pixmap = screen.grabWindow(int(window.winId())) if screen is not None else None
    if pixmap is None or pixmap.isNull():
        pixmap = window.grab()
    pixmap.save(str(path))
    return path


def _step(name: str) -> None:
    print(f"[ui-flow] {name}", flush=True)


def _pump_events(duration: float = 0.2) -> None:
    deadline = time.perf_counter() + duration
    while time.perf_counter() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)


def run(output_dir: Path) -> None:
    faulthandler.enable()
    faulthandler.dump_traceback_later(30, repeat=False)
    _step("create app")
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_pcl_theme(app)
    pg.setConfigOptions(
        background=(255, 255, 255),
        foreground=(83, 101, 125),
        antialias=False,
    )

    _step("create window")
    window = MainWindow()
    window._config_path = output_dir / "probe-loopmaster.json"
    fake_backend = FakeBackend()
    window._backend = fake_backend
    window._collector.set_backend(fake_backend)
    window._elf_path = Path("human_flow_demo.axf")
    window._recent_elf_path = window._elf_path
    window._loaded_elf_this_session = True
    window._update_conn_status(True)

    int_type = BaseType("int32_t", 4, "signed")
    float_type = BaseType("float", 4, "float")
    variables = {
        "motor.left.rpm": (0x20000010, float_type),
        "motor.right.rpm": (0x20000014, float_type),
        "control.loop.error": (0x20000018, float_type),
        "sensor.bus_voltage": (0x2000001C, float_type),
        "system.temperature_core": (0x20000020, float_type),
        "pwm.output_duty": (0x20000024, int_type),
        "very.long.namespace.controller.inner.filtered_velocity_reference": (0x20000028, float_type),
    }
    window._registry = dict(variables)
    window._monitored = set(variables)
    window._saved_scope_assignments = {
        "motor.left.rpm": (True, False, False),
        "motor.right.rpm": (True, False, False),
        "control.loop.error": (False, True, False),
        "sensor.bus_voltage": (False, True, False),
        "system.temperature_core": (False, False, True),
        "pwm.output_duty": (False, False, True),
        "very.long.namespace.controller.inner.filtered_velocity_reference": (True, False, False),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    screenshots: list[Path] = []
    issues: list[str] = []

    _step("idle 3-pane")
    window.resize(1500, 860)
    window.show()
    window._tabs.setCurrentIndex(1)
    window._toggle_scope_sidebar(True, save=False)
    window._set_scope_pane_count(3, save=False)
    window._sync_value_table_placeholders()
    QApplication.processEvents()
    screenshots.append(_save(window, output_dir, "01_scope_idle_3pane"))

    _step("start sampling")
    window._rate_combo.setCurrentIndex(3)
    window._time_window_spin.setValue(8)
    window._on_start()
    _step("wait live data")
    time.sleep(1.2)
    _pump_events(0.5)
    _step("save running screenshot")
    screenshots.append(_save(window, output_dir, "02_scope_running_3pane"))
    issues.extend(_check_pane_state(window))
    issues.extend(_check_text_fit(window))

    _step("halt target")
    _click(window._btn_halt_target)
    _pump_events(0.12)
    screenshots.append(_save(window, output_dir, "03_scope_halted"))
    if fake_backend.halt_calls != 1 or not fake_backend.is_target_halted():
        issues.append("halt button did not call backend or target did not enter Halted state")
    halt_state_text = {
        window._target_state_label.text(),
        getattr(window, "_scope_debug_title", QLabel()).text(),
    }
    if not any(("已暂停" in text or "Halted" in text) for text in halt_state_text):
        issues.append(f"halt label mismatch: {sorted(halt_state_text)!r}")
    issues.extend(_check_text_fit(window))

    _step("run target")
    _click(window._btn_resume_target)
    _pump_events(0.12)
    screenshots.append(_save(window, output_dir, "04_scope_resumed"))
    if fake_backend.resume_calls != 1 or fake_backend.is_target_halted():
        issues.append("run button did not call backend or target stayed halted")
    run_state_text = {
        window._target_state_label.text(),
        getattr(window, "_scope_debug_title", QLabel()).text(),
    }
    if not any(("运行中" in text or "Running" in text) for text in run_state_text):
        issues.append(f"run label mismatch: {sorted(run_state_text)!r}")
    issues.extend(_check_text_fit(window))

    _step("drag splitters")
    window._scope_pane_splitter.setSizes([760, 390])
    window._scope_right_splitter.setSizes([170, 280])
    QApplication.processEvents()
    screenshots.append(_save(window, output_dir, "05_scope_resized_splitters"))
    issues.extend(_check_pane_state(window))

    _step("hide sidebar")
    _click(window._btn_scope_sidebar)
    _pump_events(0.15)
    screenshots.append(_save(window, output_dir, "06_scope_sidebar_hidden"))
    issues.extend(_check_text_fit(window))

    _step("narrow window")
    window.resize(1120, 760)
    _pump_events(0.24)
    screenshots.append(_save(window, output_dir, "07_scope_narrow_running"))
    issues.extend(_check_pane_state(window))
    issues.extend(_check_text_fit(window))

    _step("switch 2-pane")
    window._set_scope_pane_count(2, save=False)
    _pump_events(0.16)
    screenshots.append(_save(window, output_dir, "08_scope_2pane_narrow"))
    issues.extend(_check_pane_state(window))

    _step("show sidebar")
    window._toggle_scope_sidebar(True, save=False)
    _pump_events(0.12)

    _step("toggle axes")
    window._x_scroll_btn.click()
    window._y_auto_btn.click()
    QApplication.processEvents()
    _pump_events(0.12)
    screenshots.append(_save(window, output_dir, "09_manual_axes_buttons"))
    issues.extend(_check_text_fit(window))

    _step("stop sampling")
    window._on_stop()
    _pump_events(0.12)
    screenshots.append(_save(window, output_dir, "10_stopped_placeholders"))
    issues.extend(_check_text_fit(window))

    _step("close")
    window.close()
    QApplication.processEvents()
    app.quit()

    if issues:
        print("\n".join(dict.fromkeys(issues)))
        print("screenshots:")
        for path in screenshots:
            print(path)
        raise SystemExit(1)

    print("PASS human-flow probe")
    for path in screenshots:
        print(path)
    faulthandler.cancel_dump_traceback_later()
    sys.stdout.flush()
    sys.stderr.flush()
    faulthandler.disable()
    os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "ui-human-flow")
    args = parser.parse_args()
    run(args.output_dir)


if __name__ == "__main__":
    main()
