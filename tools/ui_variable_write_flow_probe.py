"""Focused probe for LoopMaster UI variable write/restore flow.

The probe exercises MainWindow's existing write controls with a fake backend and
monkeypatched dialogs, so it can run without hardware or user interaction.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication, QTableWidgetItem

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import BaseType, TypeInfo  # noqa: E402
import src.ui.gui as gui_module  # noqa: E402
from src.ui.gui import MainWindow, ROLE_PATH, format_type  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


class FakeBackend:
    def __init__(self) -> None:
        self.is_connected = True
        self.last_error = ""
        self.probe_kind = "CMSIS-DAP"
        self.probe_name = "Variable Write Flow Probe"
        self.probe_uid = "WRITE-FLOW-0001"
        self.target_name = "cortex_m"
        self.swd_freq_khz = 4000
        self._halted = False
        self.write_calls: list[tuple[int, TypeInfo, str]] = []
        self.restore_calls: list[tuple[int, TypeInfo, bytes]] = []

    def target_state(self) -> str:
        return "Halted" if self._halted else "Running"

    def is_target_halted(self) -> bool:
        return self._halted

    def halt_target(self) -> bool:
        self._halted = True
        return True

    def resume_target(self) -> bool:
        self._halted = False
        return True

    def disconnect(self) -> None:
        self.is_connected = False

    def read_batch(self, variables):
        return {name: 0.0 for name, _addr, _ti in variables}

    def write_variable_value(self, addr: int, ti: TypeInfo, value: str) -> dict:
        self.write_calls.append((addr, ti, value))
        return {
            "old_raw": b"\x11\x22\x33\x44",
            "old_value": 12.5,
            "new_value": float(value),
        }

    def restore_variable_raw(self, addr: int, ti: TypeInfo, raw: bytes) -> dict:
        self.restore_calls.append((addr, ti, raw))
        return {"value": 12.5}


def _pump(app: QApplication) -> None:
    app.processEvents()


def _patch_input(value: str, ok: bool) -> Callable[[], None]:
    original = gui_module.ask_pcl_text

    def fake_ask_text(*_args, **_kwargs):
        return value, ok

    gui_module.ask_pcl_text = fake_ask_text

    def restore() -> None:
        gui_module.ask_pcl_text = original

    return restore


def _patch_confirmation(ok: bool) -> Callable[[], None]:
    original = gui_module.ask_pcl_confirmation

    def fake_confirm(*_args, **_kwargs):
        return ok

    gui_module.ask_pcl_confirmation = fake_confirm

    def restore() -> None:
        gui_module.ask_pcl_confirmation = original

    return restore


def _seed_window(window: MainWindow, backend: FakeBackend, output_dir: Path) -> BaseType:
    window._config_path = output_dir / "probe-loopmaster.json"
    window._variable_write_audit_path = output_dir / "variable-write-audit.jsonl"
    window._backend = backend
    window._collector.set_backend(backend)
    window._elf_path = Path("variable_write_flow_demo.axf")
    window._recent_elf_path = window._elf_path
    window._loaded_elf_this_session = True
    window._monitored = {"motor.pid.output"}
    ti = BaseType("float", 4, "float")
    window._registry = {"motor.pid.output": (0x20001020, ti)}

    window._show_info = lambda title, message: window._probe_infos.append((title, message))
    window._show_warning = lambda title, message: window._probe_warnings.append((title, message))
    window._probe_infos = []
    window._probe_warnings = []

    table = window._value_table
    table.blockSignals(True)
    table.setRowCount(2)

    name_item = QTableWidgetItem("motor.pid.output")
    name_item.setData(ROLE_PATH, "motor.pid.output")
    table.setItem(0, 0, name_item)

    value_item = QTableWidgetItem("12.5")
    value_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    table.setItem(0, 1, value_item)
    table.setItem(0, 2, QTableWidgetItem("0x20001020"))
    table.setItem(0, 3, QTableWidgetItem(format_type(ti)))
    for col in (4, 5, 6):
        table.setItem(0, col, QTableWidgetItem())

    other_item = QTableWidgetItem("unregistered.placeholder")
    other_item.setData(ROLE_PATH, "unregistered.placeholder")
    table.setItem(1, 0, other_item)
    table.setItem(1, 1, QTableWidgetItem("--"))
    table.setItem(1, 2, QTableWidgetItem("0x2000FFFF"))
    table.setItem(1, 3, QTableWidgetItem("float"))
    for col in (4, 5, 6):
        table.setItem(1, col, QTableWidgetItem())
    table.blockSignals(False)
    return ti


def _select_variable(window: MainWindow, selected: bool) -> None:
    table = window._value_table
    if selected:
        table.selectRow(0)
        table.setCurrentCell(0, 0)
    else:
        table.clearSelection()
        selection_model = table.selectionModel()
        if selection_model is not None:
            selection_model.clear()
            selection_model.clearCurrentIndex()
        table.setCurrentIndex(QModelIndex())
    window._refresh_debug_buttons()


def _check(
    issues: list[str],
    label: str,
    expected: bool,
    actual: bool,
) -> None:
    if actual != expected:
        issues.append(f"{label}: expected {expected}, got {actual}")


def run(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_pcl_theme(app)

    issues: list[str] = []
    window = MainWindow()
    backend = FakeBackend()
    ti = _seed_window(window, backend, output_dir)
    window.show()
    _pump(app)

    try:
        _select_variable(window, selected=True)

        backend.is_connected = False
        backend._halted = True
        window._target_is_halted = True
        window._refresh_debug_buttons()
        _check(issues, "write disabled when disconnected", False, window._btn_write_value.isEnabled())

        backend.is_connected = True
        backend._halted = False
        window._target_is_halted = False
        window._refresh_debug_buttons()
        _check(issues, "write disabled while running", False, window._btn_write_value.isEnabled())

        backend._halted = True
        window._target_is_halted = True
        _select_variable(window, selected=False)
        _check(issues, "write disabled without selection", False, window._btn_write_value.isEnabled())

        _select_variable(window, selected=True)
        _check(issues, "write enabled only when connected halted selected", True, window._btn_write_value.isEnabled())
        _check(issues, "restore disabled before write", False, window._btn_restore_value.isEnabled())

        restore_input = _patch_input("27.75", True)
        restore_confirm = _patch_confirmation(True)
        try:
            window._on_write_value()
        finally:
            restore_input()
            restore_confirm()
        _pump(app)

        expected_write = (0x20001020, ti, "27.75")
        if backend.write_calls != [expected_write]:
            issues.append(f"write call mismatch: {backend.write_calls!r}")
        expected_temp = (0x20001020, ti, b"\x11\x22\x33\x44")
        if window._temporary_writes.get("motor.pid.output") != expected_temp:
            issues.append(f"temporary write snapshot mismatch: {window._temporary_writes!r}")
        _check(issues, "restore enabled after temporary write", True, window._btn_restore_value.isEnabled())

        restore_input = _patch_input("99.0", False)
        try:
            window._on_write_value()
        finally:
            restore_input()
        _pump(app)

        if backend.write_calls != [expected_write]:
            issues.append(f"cancelled write should not call backend again: {backend.write_calls!r}")
        if window._temporary_writes.get("motor.pid.output") != expected_temp:
            issues.append("cancelled write should not change temporary write state")

        restore_confirm = _patch_confirmation(True)
        try:
            window._on_restore_value()
        finally:
            restore_confirm()
        _pump(app)

        expected_restore = (0x20001020, ti, b"\x11\x22\x33\x44")
        if backend.restore_calls != [expected_restore]:
            issues.append(f"restore call mismatch: {backend.restore_calls!r}")
        if window._temporary_writes:
            issues.append(f"restore should clear temporary writes: {window._temporary_writes!r}")
        _check(issues, "restore disabled after restore", False, window._btn_restore_value.isEnabled())

        # A restore confirmation cancel should leave the temporary snapshot intact.
        window._temporary_writes["motor.pid.output"] = expected_temp
        window._refresh_debug_buttons()
        restore_confirm = _patch_confirmation(False)
        try:
            window._on_restore_value()
        finally:
            restore_confirm()
        _pump(app)
        if backend.restore_calls != [expected_restore]:
            issues.append("cancelled restore should not call backend again")
        if window._temporary_writes.get("motor.pid.output") != expected_temp:
            issues.append("cancelled restore should keep temporary write state")

        restore_confirm = _patch_confirmation(True)
        try:
            window._on_restore_value()
        finally:
            restore_confirm()
        _pump(app)

        if backend.restore_calls != [expected_restore, expected_restore]:
            issues.append(f"second restore call mismatch: {backend.restore_calls!r}")
        if window._temporary_writes:
            issues.append("second restore should clear temporary writes")

        restore_input = _patch_input("77.0", True)
        restore_confirm = _patch_confirmation(False)
        try:
            window._on_write_value()
        finally:
            restore_input()
            restore_confirm()
        _pump(app)
        if backend.write_calls != [expected_write]:
            issues.append("cancelled confirmation should not call backend")

        warnings_before = len(window._probe_warnings)
        infos_before = len(window._probe_infos)
        restore_input = _patch_input("123.0", False)
        try:
            window._on_write_value()
        finally:
            restore_input()
        _pump(app)

        if backend.write_calls != [expected_write]:
            issues.append("post-restore cancellation should not call backend")
        if len(window._probe_warnings) != warnings_before:
            issues.append("post-restore cancellation should not warn")
        if len(window._probe_infos) != infos_before:
            issues.append("post-restore cancellation should not show success")
    finally:
        window.close()
        _pump(app)
        app.quit()

    if issues:
        print("FAIL variable write flow probe", flush=True)
        for issue in dict.fromkeys(issues):
            print(f"- {issue}", flush=True)
        return 1

    print("PASS variable write flow probe", flush=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "tools" / "ui-variable-write-flow")
    args = parser.parse_args()
    raise SystemExit(run(args.output_dir))


if __name__ == "__main__":
    main()
