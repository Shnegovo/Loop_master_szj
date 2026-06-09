"""Visual probe for LoopMaster combo popup edges."""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui.gui import MainWindow  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


class _RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


def _pump(app: QApplication, seconds: float = 0.25) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _widget_rect(widget) -> QRect:
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


def _widget_rect_in(widget, owner) -> QRect:
    return QRect(widget.mapTo(owner, QPoint(0, 0)), widget.size())


def _win_rect(widget, *, extended: bool = False) -> tuple[int, int, int, int]:
    hwnd = wintypes.HWND(int(widget.winId()))
    rect = _RECT()
    if extended:
        try:
            ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd,
                9,  # DWMWA_EXTENDED_FRAME_BOUNDS
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if ok == 0:
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    return rect.left, rect.top, rect.right, rect.bottom


def _tuple_to_qrect(rect: tuple[int, int, int, int]) -> QRect:
    left, top, right, bottom = rect
    return QRect(left, top, max(1, right - left), max(1, bottom - top))


def _capture_physical_screen_rect(path: Path, rect: QRect) -> bool:
    try:
        from PIL import ImageGrab
    except Exception:
        return False

    vx = ctypes.windll.user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    vy = ctypes.windll.user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    vw = ctypes.windll.user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
    vh = ctypes.windll.user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN

    left = max(vx, rect.x())
    top = max(vy, rect.y())
    right = min(vx + vw, rect.x() + rect.width())
    bottom = min(vy + vh, rect.y() + rect.height())
    if right <= left or bottom <= top:
        return False

    image = ImageGrab.grab(all_screens=True)
    crop = image.crop((left - vx, top - vy, right - vx, bottom - vy))
    crop.save(path)
    return True


def _capture_screen_rect(path: Path, rect: QRect) -> None:
    if _capture_physical_screen_rect(path, rect):
        return

    screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
    if screen is None:
        raise RuntimeError("no screen available for capture")
    available = screen.geometry()
    rect = rect.intersected(available)
    pixmap = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
    if pixmap.isNull() or not pixmap.save(str(path)):
        raise RuntimeError(f"failed to save screenshot: {path}")


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
    _pump(app, 0.25)
    tab = window._tab_serial
    tab.set_ports(["COM14 - USB Serial Loopback", "COM3 - Bluetooth"])
    tab.baud_combo.setCurrentText("115200")
    _pump(app, 0.15)

    combo = tab.baud_combo
    combo.showPopup()
    _pump(app, 0.35)

    popup = combo._popup_frame
    if not popup.isVisible():
        raise RuntimeError("custom combo popup did not open")
    if popup.window() is popup:
        raise RuntimeError("combo popup should be an in-window overlay, not a standalone window")

    combo_rect = _widget_rect_in(combo, window)
    popup_rect = _widget_rect_in(popup, window)
    capture_rect = combo_rect.united(popup_rect).adjusted(-36, -36, 36, 36).intersected(window.rect())
    screenshot = output_dir / "ui-combo-popup-baud-full.png"
    pixmap = window.grab(capture_rect)
    if pixmap.isNull() or not pixmap.save(str(screenshot)):
        raise RuntimeError(f"failed to save screenshot: {screenshot}")

    view_geo = popup.view.geometry()
    print(
        "PASS combo popup "
        f"screenshot={screenshot} "
        f"combo={combo_rect.getRect()} "
        f"popup={popup_rect.getRect()} "
        f"view={view_geo.getRect()}",
        flush=True,
    )

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "ui-combo-popup")
    args = parser.parse_args()
    run(args.output_dir)


if __name__ == "__main__":
    main()
