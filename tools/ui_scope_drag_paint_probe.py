"""Human-like drag/paint probe for the LoopMaster scope UI.

The probe starts the real MainWindow with synthetic live data, switches to the
Scope workspace, enables three panes, then performs mouse-driven splitter drags
and a native Windows corner resize. It records paint/resize events, plot timer
pause windows, _scope_resize_active intervals, processEvents cost, and saves
screenshots plus machine-readable metrics.
"""

from __future__ import annotations

import argparse
import csv
import faulthandler
import json
import math
import os
import statistics
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import MethodType
from typing import Any

from PySide6.QtCore import QObject, QPoint, Qt, QEvent, qVersion
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSplitter, QWidget
import pyqtgraph as pg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import BaseType  # noqa: E402
from src.ui.gui import MainWindow  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


def _now_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "avg_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "max_ms": None,
        }
    ordered = sorted(values)

    def pct(p: float) -> float:
        index = max(0, min(len(ordered) - 1, int(math.ceil(len(ordered) * p)) - 1))
        return ordered[index]

    return {
        "count": len(values),
        "avg_ms": statistics.fmean(values),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "max_ms": ordered[-1],
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


@dataclass
class OpenInterval:
    start_ms: float
    reason: str


class ProbeRecorder:
    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.events: list[dict[str, Any]] = []
        self.process_event_ms: list[float] = []
        self.process_event_rows: list[dict[str, Any]] = []
        self.paint_intervals: dict[str, list[float]] = {}
        self.paint_counts: dict[str, int] = {}
        self.resize_counts: dict[str, int] = {}
        self.plot_updates: list[dict[str, Any]] = []
        self.scope_active_intervals: list[dict[str, Any]] = []
        self.plot_pause_intervals: list[dict[str, Any]] = []
        self.drag_steps: list[dict[str, Any]] = []
        self.window_resize_steps: list[dict[str, Any]] = []
        self._last_paint_ms: dict[str, float] = {}
        self._scope_active: OpenInterval | None = None
        self._plot_pause: OpenInterval | None = None

    def t_ms(self) -> float:
        return _now_ms(self.started)

    def record(self, kind: str, **fields: Any) -> None:
        row = {"t_ms": self.t_ms(), "kind": kind}
        row.update({key: _jsonable(value) for key, value in fields.items()})
        self.events.append(row)

    def note_process_events(self, label: str, dt_ms: float) -> None:
        self.process_event_ms.append(dt_ms)
        row = {"t_ms": self.t_ms(), "label": label, "dt_ms": dt_ms}
        self.process_event_rows.append(row)
        if dt_ms >= 8.0:
            self.record("slow_process_events", label=label, dt_ms=dt_ms)

    def note_paint(self, label: str) -> None:
        now = self.t_ms()
        self.paint_counts[label] = self.paint_counts.get(label, 0) + 1
        previous = self._last_paint_ms.get(label)
        if previous is not None:
            interval = now - previous
            self.paint_intervals.setdefault(label, []).append(interval)
            if interval >= 40.0:
                self.record("slow_paint_interval", label=label, interval_ms=interval)
        self._last_paint_ms[label] = now

    def note_resize(self, label: str, width: int, height: int) -> None:
        self.resize_counts[label] = self.resize_counts.get(label, 0) + 1
        self.record("widget_resize", label=label, width=width, height=height)

    def note_scope_active(self, active: bool, reason: str) -> None:
        now = self.t_ms()
        if active and self._scope_active is None:
            self._scope_active = OpenInterval(now, reason)
            self.record("scope_resize_active_begin", reason=reason)
        elif not active and self._scope_active is not None:
            interval = {
                "start_ms": self._scope_active.start_ms,
                "end_ms": now,
                "duration_ms": now - self._scope_active.start_ms,
                "reason": self._scope_active.reason,
                "end_reason": reason,
            }
            self.scope_active_intervals.append(interval)
            self.record("scope_resize_active_end", **interval)
            self._scope_active = None

    def note_plot_pause(self, paused: bool, reason: str, timer_active: bool) -> None:
        now = self.t_ms()
        if paused and self._plot_pause is None:
            self._plot_pause = OpenInterval(now, reason)
            self.record("plot_timer_pause_begin", reason=reason, timer_active=timer_active)
        elif not paused and self._plot_pause is not None:
            interval = {
                "start_ms": self._plot_pause.start_ms,
                "end_ms": now,
                "duration_ms": now - self._plot_pause.start_ms,
                "reason": self._plot_pause.reason,
                "end_reason": reason,
                "timer_active_after": timer_active,
            }
            self.plot_pause_intervals.append(interval)
            self.record("plot_timer_pause_end", **interval)
            self._plot_pause = None

    def finalize(self) -> None:
        self.note_scope_active(False, "finalize")
        self.note_plot_pause(False, "finalize", False)

    def summary(self) -> dict[str, Any]:
        paint_summary = {
            label: {
                "paint_count": self.paint_counts.get(label, 0),
                "intervals": _stats(values),
            }
            for label, values in sorted(self.paint_intervals.items())
        }
        for label, count in sorted(self.paint_counts.items()):
            paint_summary.setdefault(label, {"paint_count": count, "intervals": _stats([])})

        plot_all = [float(row["dt_ms"]) for row in self.plot_updates]
        plot_during_resize = [
            float(row["dt_ms"])
            for row in self.plot_updates
            if row.get("scope_active_before") or row.get("scope_active_after")
        ]
        plot_forced = [float(row["dt_ms"]) for row in self.plot_updates if row.get("force")]
        scope_durations = [float(row["duration_ms"]) for row in self.scope_active_intervals]
        pause_durations = [float(row["duration_ms"]) for row in self.plot_pause_intervals]
        return {
            "elapsed_ms": self.t_ms(),
            "processEvents": _stats(self.process_event_ms),
            "paint": paint_summary,
            "resize_event_counts": dict(sorted(self.resize_counts.items())),
            "plot_update_all": _stats(plot_all),
            "plot_update_during_resize": _stats(plot_during_resize),
            "plot_update_forced": _stats(plot_forced),
            "scope_resize_active": {
                "interval_count": len(self.scope_active_intervals),
                "total_ms": sum(scope_durations),
                "longest_ms": max(scope_durations) if scope_durations else 0.0,
                "intervals": self.scope_active_intervals,
            },
            "plot_timer_pause": {
                "interval_count": len(self.plot_pause_intervals),
                "total_ms": sum(pause_durations),
                "longest_ms": max(pause_durations) if pause_durations else 0.0,
                "intervals": self.plot_pause_intervals,
            },
            "drag_steps": len(self.drag_steps),
            "window_resize_steps": len(self.window_resize_steps),
        }


class PaintResizeFilter(QObject):
    def __init__(self, recorder: ProbeRecorder) -> None:
        super().__init__()
        self._recorder = recorder
        self._labels: dict[QObject, str] = {}

    def watch(self, widget: QWidget | None, label: str) -> None:
        if widget is None:
            return
        self._labels[widget] = label
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        label = self._labels.get(watched)
        if label:
            event_type = event.type()
            if event_type == QEvent.Paint:
                self._recorder.note_paint(label)
            elif event_type == QEvent.Resize:
                size = getattr(event, "size", lambda: watched.size())()
                self._recorder.note_resize(label, int(size.width()), int(size.height()))
        return False


class FakeBackend:
    def __init__(self) -> None:
        self.is_connected = True
        self.last_error = ""
        self.probe_kind = "CMSIS-DAP"
        self.probe_name = "Human Drag Paint Probe"
        self.probe_uid = "DRAG-PAINT-0001"
        self.target_name = "cortex_m"
        self.swd_freq_khz = 4000
        self._start = time.perf_counter()
        self._halted = False
        self._sample_index = 0

    def _value_for(self, name: str, index: int, t: float) -> float:
        if "target" in name:
            return 120.0 + 28.0 * math.sin(t * 0.55)
        if "feedback" in name:
            return 116.0 + 24.0 * math.sin(t * 0.55 - 0.22) + 3.0 * math.sin(t * 11.0)
        if "error" in name:
            return 5.0 * math.sin(t * 2.6) + 1.6 * math.sin(t * 17.0)
        if "output" in name:
            return 48.0 + 30.0 * math.sin(t * 0.82) + 5.0 * math.sin(t * 23.0)
        if "gyro" in name:
            return 1.8 * math.sin(t * 45.0) + 0.35 * math.sin(t * 8.0)
        if "current" in name:
            return 12.0 + 1.8 * math.sin(t * 5.0) + 0.7 * math.sin(t * 31.0)
        if "temperature" in name:
            return 42.0 + 2.5 * math.sin(t * 0.18)
        return math.sin(t * (1.0 + index * 0.17)) * (index + 1)

    def read_batch(self, variables):
        now = time.perf_counter() - self._start
        return {
            name: self._value_for(name, index, now)
            for index, (name, _addr, _ti) in enumerate(variables)
        }

    def read_batch_samples(self, variables, batch_size: int):
        sample_rate = 1000.0
        rows = []
        base_index = self._sample_index
        for offset in range(max(1, int(batch_size))):
            t = (base_index + offset) / sample_rate
            rows.append({
                name: self._value_for(name, index, t)
                for index, (name, _addr, _ti) in enumerate(variables)
            })
        self._sample_index += len(rows)
        return rows

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


def _process_events(recorder: ProbeRecorder, label: str) -> float:
    started = time.perf_counter()
    QApplication.processEvents()
    dt_ms = (time.perf_counter() - started) * 1000.0
    recorder.note_process_events(label, dt_ms)
    return dt_ms


def _pump(recorder: ProbeRecorder, duration: float, label: str, sleep_s: float = 0.006) -> None:
    deadline = time.perf_counter() + max(0.0, duration)
    while time.perf_counter() < deadline:
        _process_events(recorder, label)
        time.sleep(sleep_s)


def _set_combo_by_data(combo, value: int) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _prepare_window(output_dir: Path, recorder: ProbeRecorder, fps: int) -> MainWindow:
    window = MainWindow()
    window._config_path = output_dir / "probe-loopmaster.json"
    fake_backend = FakeBackend()
    window._backend = fake_backend
    window._collector.set_backend(fake_backend)
    window._elf_path = Path("human_drag_paint_demo.axf")
    window._recent_elf_path = window._elf_path
    window._loaded_elf_this_session = True
    window._update_conn_status(True)

    float_type = BaseType("float", 4, "float")
    variables = {
        "speed.target": (0x20004000, float_type),
        "speed.feedback": (0x20004004, float_type),
        "speed.error": (0x20004008, float_type),
        "pid.output": (0x2000400C, float_type),
        "gyro.rate": (0x20004010, float_type),
        "motor.current": (0x20004014, float_type),
        "system.temperature_core": (0x20004018, float_type),
        "bus.voltage": (0x2000401C, float_type),
        "observer.velocity_estimate": (0x20004020, float_type),
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
        "system.temperature_core": (False, False, True),
        "bus.voltage": (True, False, False),
        "observer.velocity_estimate": (False, True, False),
    }

    window.resize(1500, 860)
    window.show()
    window.raise_()
    window.activateWindow()
    _process_events(recorder, "show")
    window._show_workspace_page("scope")
    window._toggle_scope_sidebar(True, save=False)
    window._set_scope_pane_count(3, save=False)
    _set_combo_by_data(window._rate_combo, 1000)
    window._set_frame_rate(fps)
    window._time_window_spin.setValue(8)
    window._sync_value_table_placeholders()
    _process_events(recorder, "scope_setup")
    window._on_start()
    _pump(recorder, 0.9, "warmup")
    return window


def _install_instrumentation(window: MainWindow, recorder: ProbeRecorder) -> None:
    original_begin = window._begin_resize_pause
    original_end = window._end_resize_pause
    original_set_pause = window._set_plot_timer_resize_suspended
    original_update_plot = window._update_plot

    def begin_wrapper(self, reason: str, suspend_scope: bool = False, suspend_window: bool = False):
        recorder.record(
            "begin_resize_pause_call",
            reason=reason,
            active_before=bool(getattr(self, "_scope_resize_active", False)),
            timer_active_before=bool(self._plot_timer.isActive()),
        )
        result = original_begin(reason, suspend_scope=suspend_scope, suspend_window=suspend_window)
        recorder.note_scope_active(bool(getattr(self, "_scope_resize_active", False)), f"begin:{reason}")
        recorder.record(
            "begin_resize_pause_return",
            reason=reason,
            active_after=bool(getattr(self, "_scope_resize_active", False)),
            reasons=sorted(getattr(self, "_resize_pause_reasons", set())),
            timer_active_after=bool(self._plot_timer.isActive()),
        )
        return result

    def end_wrapper(self, reason: str, resume_window: bool = False):
        recorder.record(
            "end_resize_pause_call",
            reason=reason,
            active_before=bool(getattr(self, "_scope_resize_active", False)),
            reasons_before=sorted(getattr(self, "_resize_pause_reasons", set())),
            timer_active_before=bool(self._plot_timer.isActive()),
        )
        result = original_end(reason, resume_window=resume_window)
        recorder.note_scope_active(bool(getattr(self, "_scope_resize_active", False)), f"end:{reason}")
        recorder.record(
            "end_resize_pause_return",
            reason=reason,
            active_after=bool(getattr(self, "_scope_resize_active", False)),
            reasons_after=sorted(getattr(self, "_resize_pause_reasons", set())),
            timer_active_after=bool(self._plot_timer.isActive()),
        )
        return result

    def set_pause_wrapper(self, suspended: bool):
        before = bool(getattr(self, "_plot_timer_resize_suspended", False))
        timer_before = bool(self._plot_timer.isActive())
        result = original_set_pause(suspended)
        after = bool(getattr(self, "_plot_timer_resize_suspended", False))
        timer_after = bool(self._plot_timer.isActive())
        if before != after:
            recorder.record(
                "plot_timer_suspend_state",
                requested=suspended,
                paused=after,
                timer_active_before=timer_before,
                timer_active_after=timer_after,
            )
        recorder.note_plot_pause(after, f"set_pause:{suspended}", timer_after)
        return result

    def update_plot_wrapper(self, *args, **kwargs):
        force = bool(kwargs.get("force", args[0] if args else False))
        active_before = bool(getattr(self, "_scope_resize_active", False))
        timer_active_before = bool(self._plot_timer.isActive())
        started = time.perf_counter()
        result = original_update_plot(*args, **kwargs)
        dt_ms = (time.perf_counter() - started) * 1000.0
        row = {
            "t_ms": recorder.t_ms(),
            "dt_ms": dt_ms,
            "force": force,
            "collector_running": bool(self._collector.is_running),
            "scope_active_before": active_before,
            "scope_active_after": bool(getattr(self, "_scope_resize_active", False)),
            "plot_timer_active_before": timer_active_before,
            "plot_timer_active_after": bool(self._plot_timer.isActive()),
        }
        recorder.plot_updates.append(row)
        if dt_ms >= 16.0 or active_before:
            recorder.record("plot_update_sample", **row)
        return result

    window._begin_resize_pause = MethodType(begin_wrapper, window)
    window._end_resize_pause = MethodType(end_wrapper, window)
    window._set_plot_timer_resize_suspended = MethodType(set_pause_wrapper, window)
    window._update_plot = MethodType(update_plot_wrapper, window)
    recorder.note_scope_active(bool(getattr(window, "_scope_resize_active", False)), "instrument_initial")
    recorder.note_plot_pause(
        bool(getattr(window, "_plot_timer_resize_suspended", False)),
        "instrument_initial",
        bool(window._plot_timer.isActive()),
    )


def _install_paint_filters(window: MainWindow, recorder: ProbeRecorder) -> PaintResizeFilter:
    event_filter = PaintResizeFilter(recorder)
    watched = [
        (window, "main_window"),
        (getattr(window, "_workspace_shell", None), "workspace_shell"),
        (getattr(window, "_scope_plot_area", None), "scope_plot_area"),
        (getattr(window, "_scope_pane_splitter", None), "scope_h_splitter"),
        (getattr(window, "_scope_right_splitter", None), "scope_v_splitter"),
    ]
    for pane in getattr(window, "_scope_panes", []):
        watched.extend([
            (pane.frame, f"pane_{pane.index}_frame"),
            (pane.plot_widget, f"pane_{pane.index}_plot_widget"),
            (pane.plot_widget.viewport(), f"pane_{pane.index}_plot_viewport"),
        ])
    for widget, label in watched:
        event_filter.watch(widget, label)
    return event_filter


def _save_screenshot(window: MainWindow, path: Path, recorder: ProbeRecorder, label: str) -> str:
    _process_events(recorder, f"screenshot:{label}")
    path.parent.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(path))
    recorder.record("screenshot", label=label, path=path)
    return str(path)


def _splitter_state(splitter: QSplitter) -> dict[str, Any]:
    return {
        "sizes": [int(size) for size in splitter.sizes()],
        "width": int(splitter.width()),
        "height": int(splitter.height()),
    }


def _human_drag_splitter(
    window: MainWindow,
    splitter: QSplitter,
    recorder: ProbeRecorder,
    label: str,
    dx: int,
    dy: int,
    steps: int,
    delay_ms: int,
    screenshot_path: Path | None = None,
) -> dict[str, Any]:
    handle = splitter.handle(1)
    if handle is None or not handle.isVisible():
        raise RuntimeError(f"{label} splitter handle is not visible")

    start_pos = handle.rect().center()
    start_state = _splitter_state(splitter)
    recorder.record("mouse_drag_begin", label=label, start_pos=[start_pos.x(), start_pos.y()], state=start_state)
    QTest.mouseMove(handle, start_pos, delay=delay_ms)
    _process_events(recorder, f"{label}:hover")
    QTest.mousePress(handle, Qt.LeftButton, Qt.NoModifier, start_pos, delay=delay_ms)
    _process_events(recorder, f"{label}:press")

    middle_index = max(1, steps // 2)
    for index in range(1, steps + 1):
        ratio = index / steps
        eased = 0.5 - math.cos(ratio * math.pi) / 2.0
        pos = QPoint(
            int(round(start_pos.x() + dx * eased)),
            int(round(start_pos.y() + dy * eased)),
        )
        before = time.perf_counter()
        QTest.mouseMove(handle, pos, delay=delay_ms)
        qtest_dt_ms = (time.perf_counter() - before) * 1000.0
        process_dt_ms = _process_events(recorder, f"{label}:move")
        row = {
            "t_ms": recorder.t_ms(),
            "label": label,
            "step": index,
            "local_x": pos.x(),
            "local_y": pos.y(),
            "qtest_dt_ms": qtest_dt_ms,
            "processEvents_ms": process_dt_ms,
            "sizes": [int(size) for size in splitter.sizes()],
            "window_size": [int(window.width()), int(window.height())],
            "scope_resize_active": bool(getattr(window, "_scope_resize_active", False)),
            "plot_timer_active": bool(window._plot_timer.isActive()),
        }
        recorder.drag_steps.append(row)
        if index == middle_index and screenshot_path is not None:
            _save_screenshot(window, screenshot_path, recorder, f"{label}_during_mouse_down")

    end_pos = QPoint(start_pos.x() + dx, start_pos.y() + dy)
    QTest.mouseRelease(handle, Qt.LeftButton, Qt.NoModifier, end_pos, delay=delay_ms)
    _process_events(recorder, f"{label}:release")
    _pump(recorder, 0.25, f"{label}:settle")
    end_state = _splitter_state(splitter)
    recorder.record("mouse_drag_end", label=label, end_pos=[end_pos.x(), end_pos.y()], state=end_state)
    return {"label": label, "start": start_state, "end": end_state}


def _native_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if sys.platform != "win32":
        return None
    import ctypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    rect = RECT()
    if not ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _native_drag_window_corner(
    window: MainWindow,
    recorder: ProbeRecorder,
    dx: int,
    dy: int,
    steps: int,
    delay_ms: int,
) -> dict[str, Any]:
    if sys.platform != "win32":
        raise RuntimeError("native window drag is only implemented on Windows")

    import ctypes

    user32 = ctypes.windll.user32
    hwnd = int(window.winId())
    start_rect = _native_window_rect(hwnd)
    if start_rect is None:
        raise RuntimeError("GetWindowRect failed")

    user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    window.raise_()
    window.activateWindow()
    _process_events(recorder, "native_resize:activate")

    left, top, right, bottom = start_rect
    start_x = right - 2
    start_y = bottom - 2
    recorder.record(
        "native_window_resize_begin",
        hwnd=hwnd,
        rect=start_rect,
        start_xy=[start_x, start_y],
        target_delta=[dx, dy],
    )

    done = threading.Event()
    failed: list[str] = []
    cursor_rows: list[dict[str, Any]] = []

    def driver() -> None:
        try:
            user32.SetCursorPos(start_x, start_y)
            time.sleep(delay_ms / 1000.0)
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            for index in range(1, steps + 1):
                ratio = index / steps
                eased = 0.5 - math.cos(ratio * math.pi) / 2.0
                x = int(round(start_x + dx * eased))
                y = int(round(start_y + dy * eased))
                user32.SetCursorPos(x, y)
                cursor_rows.append({"step": index, "cursor_xy": [x, y], "sent_t_ms": recorder.t_ms()})
                time.sleep(delay_ms / 1000.0)
        except Exception as exc:  # pragma: no cover - defensive native path
            failed.append(str(exc))
        finally:
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
            done.set()

    worker = threading.Thread(target=driver, name="native-window-resize-driver", daemon=True)
    worker.start()
    seen_steps = 0
    deadline = time.perf_counter() + max(5.0, steps * max(delay_ms, 1) / 1000.0 + 3.0)
    while not done.is_set() and time.perf_counter() < deadline:
        process_dt_ms = _process_events(recorder, "native_resize:drive")
        while seen_steps < len(cursor_rows):
            sent = cursor_rows[seen_steps]
            row = {
                "t_ms": recorder.t_ms(),
                "step": sent["step"],
                "cursor_xy": sent["cursor_xy"],
                "cursor_sent_t_ms": sent["sent_t_ms"],
                "processEvents_ms": process_dt_ms,
                "window_size": [int(window.width()), int(window.height())],
                "scope_resize_active": bool(getattr(window, "_scope_resize_active", False)),
                "plot_timer_active": bool(window._plot_timer.isActive()),
                "native_rect": _native_window_rect(hwnd),
            }
            recorder.window_resize_steps.append(row)
            recorder.record("native_window_resize_step", **row)
            seen_steps += 1
        time.sleep(0.004)

    if not done.is_set():
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        raise RuntimeError("native resize driver did not finish")
    worker.join(timeout=1.0)
    while seen_steps < len(cursor_rows):
        sent = cursor_rows[seen_steps]
        row = {
            "t_ms": recorder.t_ms(),
            "step": sent["step"],
            "cursor_xy": sent["cursor_xy"],
            "cursor_sent_t_ms": sent["sent_t_ms"],
            "processEvents_ms": None,
            "window_size": [int(window.width()), int(window.height())],
            "scope_resize_active": bool(getattr(window, "_scope_resize_active", False)),
            "plot_timer_active": bool(window._plot_timer.isActive()),
            "native_rect": _native_window_rect(hwnd),
        }
        recorder.window_resize_steps.append(row)
        recorder.record("native_window_resize_step", **row)
        seen_steps += 1
    if failed:
        raise RuntimeError(f"native resize driver failed: {failed[0]}")

    _process_events(recorder, "native_resize:release")
    _pump(recorder, 0.35, "native_resize:settle")
    end_rect = _native_window_rect(hwnd)
    recorder.record("native_window_resize_end", rect=end_rect, window_size=[window.width(), window.height()])
    if end_rect is not None:
        start_width = start_rect[2] - start_rect[0]
        start_height = start_rect[3] - start_rect[1]
        end_width = end_rect[2] - end_rect[0]
        end_height = end_rect[3] - end_rect[1]
        if abs(end_width - start_width) < 8 and abs(end_height - start_height) < 8:
            raise RuntimeError(f"native window drag did not resize: start={start_rect} end={end_rect}")
    return {
        "mode": "native_win32_mouse",
        "start_rect": start_rect,
        "end_rect": end_rect,
        "start_qt_size": [right - left, bottom - top],
        "end_qt_size": [int(window.width()), int(window.height())],
    }


def _programmatic_window_resize(
    window: MainWindow,
    recorder: ProbeRecorder,
    dx: int,
    dy: int,
    steps: int,
    delay_ms: int,
) -> dict[str, Any]:
    start_width = int(window.width())
    start_height = int(window.height())
    recorder.record("programmatic_window_resize_begin", start_size=[start_width, start_height], target_delta=[dx, dy])
    for index in range(1, steps + 1):
        ratio = index / steps
        eased = 0.5 - math.cos(ratio * math.pi) / 2.0
        width = int(round(start_width + dx * eased))
        height = int(round(start_height + dy * eased))
        window.resize(width, height)
        time.sleep(delay_ms / 1000.0)
        process_dt_ms = _process_events(recorder, "programmatic_resize:step")
        row = {
            "t_ms": recorder.t_ms(),
            "step": index,
            "window_size": [int(window.width()), int(window.height())],
            "processEvents_ms": process_dt_ms,
            "scope_resize_active": bool(getattr(window, "_scope_resize_active", False)),
            "plot_timer_active": bool(window._plot_timer.isActive()),
        }
        recorder.window_resize_steps.append(row)
        recorder.record("programmatic_window_resize_step", **row)
    _pump(recorder, 0.35, "programmatic_resize:settle")
    return {
        "mode": "programmatic_qwidget_resize",
        "start_qt_size": [start_width, start_height],
        "end_qt_size": [int(window.width()), int(window.height())],
    }


def _write_events_csv(path: Path, recorder: ProbeRecorder) -> None:
    fields = [
        "t_ms",
        "kind",
        "label",
        "reason",
        "dt_ms",
        "interval_ms",
        "width",
        "height",
        "active_before",
        "active_after",
        "timer_active_before",
        "timer_active_after",
        "scope_resize_active",
        "plot_timer_active",
        "sizes",
        "window_size",
        "rect",
        "native_rect",
        "path",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for event in recorder.events:
            row = {
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, tuple, dict)) else value
                for key, value in event.items()
            }
            writer.writerow(row)


def _write_process_csv(path: Path, recorder: ProbeRecorder) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["t_ms", "label", "dt_ms"])
        writer.writeheader()
        writer.writerows(recorder.process_event_rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    faulthandler.enable()
    faulthandler.dump_traceback_later(args.timeout, repeat=False)
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    recorder = ProbeRecorder()
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_pcl_theme(app)
    pg.setConfigOptions(
        background=(255, 255, 255),
        foreground=(83, 101, 125),
        antialias=False,
    )

    window = _prepare_window(output_dir, recorder, args.fps)
    _install_instrumentation(window, recorder)
    event_filter = _install_paint_filters(window, recorder)
    screenshots: dict[str, str] = {}
    window_resize_result: dict[str, Any] = {}

    try:
        screenshots["baseline"] = _save_screenshot(
            window,
            output_dir / "01_scope_drag_paint_baseline.png",
            recorder,
            "baseline",
        )
        _pump(recorder, args.baseline_seconds, "baseline")

        horizontal_during = output_dir / "02_scope_horizontal_drag_mouse_down.png"
        horizontal_result = _human_drag_splitter(
            window,
            window._scope_pane_splitter,
            recorder,
            "scope_horizontal_splitter",
            dx=args.horizontal_drag_dx,
            dy=0,
            steps=args.drag_steps,
            delay_ms=args.drag_delay_ms,
            screenshot_path=horizontal_during,
        )
        screenshots["horizontal_drag_mouse_down"] = str(horizontal_during)
        vertical_during = output_dir / "03_scope_vertical_drag_mouse_down.png"
        vertical_result = _human_drag_splitter(
            window,
            window._scope_right_splitter,
            recorder,
            "scope_vertical_splitter",
            dx=0,
            dy=args.vertical_drag_dy,
            steps=args.drag_steps,
            delay_ms=args.drag_delay_ms,
            screenshot_path=vertical_during,
        )
        screenshots["vertical_drag_mouse_down"] = str(vertical_during)
        screenshots["after_splitter_drags"] = _save_screenshot(
            window,
            output_dir / "04_after_splitter_drags.png",
            recorder,
            "after_splitter_drags",
        )

        if args.window_resize_mode == "native":
            window_resize_result = _native_drag_window_corner(
                window,
                recorder,
                dx=args.window_drag_dx,
                dy=args.window_drag_dy,
                steps=args.resize_steps,
                delay_ms=args.resize_delay_ms,
            )
        elif args.window_resize_mode == "programmatic":
            window_resize_result = _programmatic_window_resize(
                window,
                recorder,
                dx=args.window_drag_dx,
                dy=args.window_drag_dy,
                steps=args.resize_steps,
                delay_ms=args.resize_delay_ms,
            )
        else:
            window_resize_result = {"mode": "off"}

        screenshots["after_window_resize"] = _save_screenshot(
            window,
            output_dir / "05_after_window_resize.png",
            recorder,
            "after_window_resize",
        )
        _pump(recorder, args.tail_seconds, "tail")

        recorder.record(
            "final_state",
            h_splitter=_splitter_state(window._scope_pane_splitter),
            v_splitter=_splitter_state(window._scope_right_splitter),
            window_size=[int(window.width()), int(window.height())],
            scope_resize_active=bool(getattr(window, "_scope_resize_active", False)),
            plot_timer_active=bool(window._plot_timer.isActive()),
            plot_timer_suspended=bool(getattr(window, "_plot_timer_resize_suspended", False)),
        )
        recorder.finalize()
        metrics_path = output_dir / "probe-metrics.json"
        events_path = output_dir / "probe-events.csv"
        process_path = output_dir / "process-events.csv"
        payload = {
            "probe": "ui_scope_drag_paint_probe",
            "python": sys.version,
            "platform": sys.platform,
            "qt_version": qVersion(),
            "pyqtgraph_version": getattr(pg, "__version__", None),
            "output_dir": str(output_dir),
            "config": {
                "fps": args.fps,
                "baseline_seconds": args.baseline_seconds,
                "tail_seconds": args.tail_seconds,
                "drag_steps": args.drag_steps,
                "drag_delay_ms": args.drag_delay_ms,
                "resize_steps": args.resize_steps,
                "resize_delay_ms": args.resize_delay_ms,
                "window_resize_mode": args.window_resize_mode,
            },
            "screenshots": screenshots,
            "splitter_drags": [horizontal_result, vertical_result],
            "window_resize": window_resize_result,
            "summary": recorder.summary(),
            "events_count": len(recorder.events),
            "metrics_path": str(metrics_path),
            "events_path": str(events_path),
            "process_events_path": str(process_path),
        }

        metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_events_csv(events_path, recorder)
        _write_process_csv(process_path, recorder)
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        return payload
    finally:
        recorder.finalize()
        try:
            window._on_stop()
        except Exception:
            pass
        window.close()
        _process_events(recorder, "close")
        event_filter.deleteLater()
        app.quit()
        faulthandler.cancel_dump_traceback_later()
        sys.stdout.flush()
        sys.stderr.flush()
        if args.hard_exit:
            os._exit(0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a human-like LoopMaster scope drag/paint probe.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "tools" / "ui-scope-drag-paint-probe",
        help="Directory for screenshots, JSON, and CSV output.",
    )
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--baseline-seconds", type=float, default=0.6)
    parser.add_argument("--tail-seconds", type=float, default=0.45)
    parser.add_argument("--drag-steps", type=int, default=32)
    parser.add_argument("--drag-delay-ms", type=int, default=9)
    parser.add_argument("--resize-steps", type=int, default=22)
    parser.add_argument("--resize-delay-ms", type=int, default=14)
    parser.add_argument("--horizontal-drag-dx", type=int, default=-180)
    parser.add_argument("--vertical-drag-dy", type=int, default=130)
    parser.add_argument("--window-drag-dx", type=int, default=-190)
    parser.add_argument("--window-drag-dy", type=int, default=-120)
    parser.add_argument(
        "--window-resize-mode",
        choices=("native", "programmatic", "off"),
        default="native",
        help="native uses Win32 mouse input on the real window corner.",
    )
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--hard-exit", action="store_true", help="Use os._exit(0) after cleanup.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:
        print(f"FAIL ui_scope_drag_paint_probe: {exc}", file=sys.stderr, flush=True)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
