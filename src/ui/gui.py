"""LoopMaster Scope -- non-intrusive MCU variable oscilloscope."""

import sys
import csv
import html
import json
import logging
import subprocess
import os
import time
import threading
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QScrollArea,
    QPushButton, QSpinBox, QLabel, QFileDialog, QStatusBar, QMenuBar,
    QMenu, QApplication, QLineEdit, QHeaderView, QButtonGroup,
    QTreeWidget, QTreeWidgetItem, QFrame, QTabWidget, QAbstractItemView,
    QTableWidget, QTableWidgetItem, QComboBox, QSizePolicy, QColorDialog,
    QInputDialog, QStyledItemDelegate, QStyle,
    QGraphicsView,
)
from PySide6.QtCore import Qt, QTimer, QEvent, QRect
from PySide6.QtGui import QAction, QFont, QPalette, QColor, QPainter, QPen, QBrush, QIcon, QPixmap

import pyqtgraph as pg

from src.parser.readelf import parse_symbol_table, parse_debug_info
from src.parser.variable_inventory import VariableInventory
from src.parser.elf_parser import ELFParser
from src.parser.map_parser import parse_map_file
from src.core.collector import DataCollector
from src.core.debug_workbench import (
    DebugRuntimeState,
    debug_command_plans_for_status,
    make_debug_status,
)
from src.core.keil.commands import KeilCommandHistory, build_keil_debug_transactions, transaction_by_key
from src.core.keil.backend import KeilBackendConfig, KeilUvSockBackendAdapter
from src.core.mem_backend import DEFAULT_TARGET, SWDBackend, _extract_val
from src.core.models import (
    Variable, TypeInfo, BaseType, StructType, ArrayType,
    PointerType, EnumType, TypedefType, FuncType,
)
from src.ui.pcl_theme import (
    PclComboBox,
    PclHoverFilter,
    PclLoadingDialog,
    animate_page_enter,
    apply_pcl_theme,
    install_card_shadow,
    polish_combo_popup,
    show_pcl_message,
    start_status_pulse,
)
from src.ui.scope_algorithms import (
    calculate_y_range,
    effective_plot_fps,
    point_budget_for_fps,
    process_display_data,
    thin_display_series,
)
from src.ui.serial_controller import (
    SerialController,
    escape_serial_log_text,
    serial_payload_display,
    serial_protocol_key,
)
from src.ui.debug_workbench_tab import DebugWorkbenchTab
from src.ui.serial_tab import SerialTab

# ---- Constants ----

COLORS = [
    "#1d4ed8", "#0ea5a5", "#f97316", "#7c3aed", "#ef4444",
    "#0284c7", "#db2777", "#22c55e", "#475569", "#14b8a6",
]
PLOT_PEN_WIDTH = 2.0

PRESET_FRAME_RATES = [12, 24, 30, 60, 120]
FRAME_RATE_DEFAULT = 30
TIME_WINDOW_DEFAULT = 10
BUFFER_SECONDS = 300
MAX_STRUCT_DEPTH = 6

_STM32_TARGET_FALLBACKS = [
    "stm32f051", "stm32f103rc", "stm32f412xe", "stm32f412xg",
    "stm32f429xg", "stm32f429xi", "stm32f439xg", "stm32f439xi",
    "stm32f767zi", "stm32h723xx", "stm32h743xx", "stm32h750xx",
    "stm32h7b0xx", "stm32l031x6", "stm32l432kc", "stm32l475xc",
    "stm32l475xe", "stm32l475xg",
]


def _build_target_options() -> list[tuple[str, str]]:
    options = [("STM32 自动 / Cortex-M", DEFAULT_TARGET)]
    stm32_targets = list(_STM32_TARGET_FALLBACKS)
    try:
        from pyocd.target import TARGET
        stm32_targets = sorted(name for name in TARGET if name.startswith("stm32"))
    except Exception:
        pass
    for target_name in stm32_targets:
        options.append((target_name.upper(), target_name))
    options.extend([
        ("TI MSPM0G3507", "mspm0g3507"),
        ("TI LP-MSPM0G3507", "lp_mspm0g3507"),
    ])
    return options


TARGET_OPTIONS = _build_target_options()

ROLE_PATH = Qt.UserRole
ROLE_ADDR = Qt.UserRole + 1
ROLE_TYPE = Qt.UserRole + 2
ROLE_DISPLAYED = Qt.UserRole + 3
ROLE_SCOPE_2 = Qt.UserRole + 4
ROLE_SCOPE_3 = Qt.UserRole + 5

logger = logging.getLogger("loopmaster")


@dataclass
class WorkspacePage:
    key: str
    title: str
    widget: QWidget
    domain: str = "loopmaster"


@dataclass
class WorkspaceDomain:
    key: str
    title: str
    hint: str


def _asset_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "assets" / name


def _load_app_icon() -> QIcon:
    for name in ("app_icon.ico", "logo_rounded.png", "logo.png"):
        path = _asset_path(name)
        if path.exists():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return QIcon()


def _load_logo_pixmap() -> QPixmap:
    for name in ("logo_rounded.png", "logo.png"):
        path = _asset_path(name)
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap
    return QPixmap()


def _load_roxy_idle_frames() -> list[QPixmap]:
    candidate_paths = [
        _asset_path("roxy_sleep_idle_strip.png"),
        Path.home() / ".codex" / "pets" / "roxy-sleep" / "spritesheet.webp",
    ]
    for path in candidate_paths:
        if not path.exists():
            continue
        strip = QPixmap(str(path))
        if strip.isNull():
            continue
        frame_count = 6
        cell_w = strip.width() // frame_count
        cell_h = strip.height()
        if cell_w <= 0 or cell_h <= 0:
            continue
        frames: list[QPixmap] = []
        for index in range(frame_count):
            frame = strip.copy(index * cell_w, 0, cell_w, cell_h)
            if not frame.isNull():
                frames.append(frame)
        if frames:
            return frames
    return []


def setup_logging(log_path: str = "loopmaster.log"):
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)-7s] %(message)s',
        datefmt='%H:%M:%S',
    )
    # 文件输出，保留完整日志
    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # 控制台输出 INFO 及以上
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    sys.excepthook = lambda exc_type, exc, tb: logger.critical(
        "未处理异常",
        exc_info=(exc_type, exc, tb),
    )
    threading.excepthook = lambda args: logger.critical(
        "线程异常：%s",
        getattr(args.thread, "name", "未知线程"),
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )
    logger.info("LoopMaster 启动")


# ---- Helpers ----

def resolve_type(ti: Optional[TypeInfo]) -> Optional[TypeInfo]:
    while isinstance(ti, TypedefType):
        ti = ti.underlying_type
    return ti


def format_type(ti: Optional[TypeInfo]) -> str:
    if ti is None:
        return "?"
    if isinstance(ti, BaseType):
        return ti.name
    if isinstance(ti, StructType):
        prefix = "union " if ti.is_union else "struct "
        name = ti.name if ti.name else "<anonymous>"
        return f"{prefix}{name}"
    if isinstance(ti, ArrayType):
        elem = format_type(ti.element_type)
        return f"{elem}[{ti.count}]"
    if isinstance(ti, PointerType):
        pointed = format_type(ti.pointed_type) if ti.pointed_type else "void"
        return f"{pointed}*"
    if isinstance(ti, EnumType):
        return f"enum {ti.name}"
    if isinstance(ti, TypedefType):
        return ti.name
    if isinstance(ti, FuncType):
        return "func"
    return "?"


class PclScopeToggleDelegate(QStyledItemDelegate):
    """Render and toggle a compact scope-assignment cell."""

    def __init__(self, role: int, accent: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._role = role
        self._accent = QColor(accent)

    def paint(self, painter, option, index):  # noqa: N802 - Qt override
        checked = bool(index.data(self._role))
        hovered = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)
        size = 18
        rect = QRect(
            option.rect.center().x() - size // 2,
            option.rect.center().y() - size // 2,
            size,
            size,
        )

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        base_color = QColor("#f7fbff" if index.row() % 2 else "#ffffff")
        painter.fillRect(option.rect, base_color)
        if selected:
            painter.fillRect(option.rect, QColor("#eef6ff"))

        if checked:
            painter.setBrush(self._accent)
            painter.setPen(QPen(self._accent.darker(125 if hovered else 145), 1.4))
        else:
            painter.setBrush(QColor("#ffffff" if not hovered else "#eef6ff"))
            painter.setPen(QPen(QColor("#94a3b8" if not hovered else self._accent), 1.4))
        painter.drawRoundedRect(rect, 5, 5)

        if checked:
            painter.setPen(QPen(QColor("#ffffff"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(rect.left() + 4, rect.center().y(), rect.left() + 7, rect.bottom() - 4)
            painter.drawLine(rect.left() + 7, rect.bottom() - 4, rect.right() - 3, rect.top() + 4)
        painter.restore()

    def editorEvent(self, event, model, option, index):  # noqa: N802 - Qt override
        if not bool(index.flags() & Qt.ItemIsEnabled):
            return super().editorEvent(event, model, option, index)

        event_type = event.type()
        if event_type == QEvent.MouseButtonRelease:
            button = getattr(event, "button", lambda: None)()
            if button == Qt.LeftButton:
                current = bool(index.data(self._role))
                return model.setData(index, not current, self._role)
        if event_type == QEvent.KeyPress:
            key = getattr(event, "key", lambda: None)()
            if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
                current = bool(index.data(self._role))
                return model.setData(index, not current, self._role)

        return super().editorEvent(event, model, option, index)


class ScopePane:
    def __init__(self, index: int, title: str, accent: str):
        self.index = index
        self.title = title
        self.accent = accent
        self.frame = QFrame()
        self.frame.setObjectName("scopePane")
        self.frame.setMinimumSize(220, 150)
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("scopePaneTitle")
        self.count_label = QLabel("0 个变量")
        self.count_label.setObjectName("scopePaneCount")
        head.addWidget(self.title_label)
        head.addStretch(1)
        head.addWidget(self.count_label)
        layout.addLayout(head)

        self.legend_label = QLabel("")
        self.legend_label.setObjectName("scopePaneLegend")
        self.legend_label.setTextFormat(Qt.RichText)
        self.legend_label.setWordWrap(False)
        self.legend_label.setVisible(False)
        layout.addWidget(self.legend_label)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setMinimumSize(180, 110)
        try:
            self.plot_widget.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
            self.plot_widget.setCacheMode(QGraphicsView.CacheNone)
            self.plot_widget.viewport().setAttribute(Qt.WA_OpaquePaintEvent, True)
        except Exception:
            pass
        self.plot = self.plot_widget.getPlotItem()
        self.plot_widget.setBackground("#fcfdff")
        self.plot.setLabel("left", "值", color="#42526a")
        self.plot.setLabel("bottom", "时间", units="s", color="#42526a")
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.plot.setAutoVisible(y=False)
        self.plot.getAxis("left").setPen(pg.mkPen(color="#e4ebf3"))
        self.plot.getAxis("bottom").setPen(pg.mkPen(color="#e4ebf3"))
        self.plot.getAxis("left").setTextPen(pg.mkPen(color="#516174"))
        self.plot.getAxis("bottom").setTextPen(pg.mkPen(color="#516174"))
        axis_font = QFont("Microsoft YaHei UI", 9)
        for axis_name in ("left", "bottom"):
            axis = self.plot.getAxis(axis_name)
            try:
                axis.enableAutoSIPrefix(False)
            except Exception:
                pass
            axis.setTickFont(axis_font)
            axis.label.setFont(axis_font)
        self.plot.setMouseEnabled(x=True, y=True)
        self.plot.enableAutoRange(y=False)
        self.plot.setLimits(xMin=0)
        layout.addWidget(self.plot_widget, stretch=1)
        self.curves: dict[str, pg.PlotDataItem] = {}
        self.names: tuple[str, ...] = ()
        self.y_range: tuple[float, float] | None = None
        self.last_x_range: tuple[float, float] | None = None


# ---- Main Window ----

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LoopMaster v2.1 - MCU 变量示波器")
        icon = _load_app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(1500, 860)
        self.setMinimumSize(1100, 600)

        self._elf_path: Optional[Path] = None
        self._variables: list[Variable] = []
        self._monitored: set[str] = set()
        self._monitor_list: list[tuple[str, int, object]] = []
        self._registry: dict[str, tuple[int, TypeInfo]] = {}

        self._backend = SWDBackend()
        self._collector = DataCollector()
        self._collector.set_backend(self._backend)
        self._serial_controller = SerialController(self)
        self._unlimited_mode = False
        self._frame_rate = FRAME_RATE_DEFAULT
        self._probe_list: list[dict] = []
        self._pack_path: Optional[Path] = None
        self._config_path = Path("loopmaster.json")
        self._recent_elf_path: Optional[Path] = None
        self._loaded_elf_this_session = False
        self._saved_monitored_variables: list[str] = []
        self._hidden_displayed_variables: set[str] = set()
        self._saved_scope_assignments: dict[str, tuple[bool, bool, bool]] = {}
        self._scope_pane_count = 1
        self._scope_sidebar_visible = True
        self._scope_plot_split_sizes: list[int] = []
        self._scope_plot_right_split_sizes: list[int] = []
        self._curve_color_overrides: dict[str, str] = {}
        self._temporary_writes: dict[str, tuple[int, TypeInfo, bytes]] = {}
        self._hero_roxy_frames: list[QPixmap] = []
        self._hero_roxy_index = 0
        self._shutdown_complete = False
        self._keil_root = Path(os.environ.get("LOOPMASTER_KEIL_ROOT") or "D:\\Keil")
        self._debug_uvsock_port = 4827
        self._debug_command_history = KeilCommandHistory(max_entries=64)
        self._debug_remote_breakpoint_snapshot = None
        self._debug_backend_snapshot_record = None
        cfg = self._load_config()
        if cfg:
            keil_root = cfg.get("keil_root", "")
            if keil_root:
                self._keil_root = Path(str(keil_root))
            elf = cfg.get("elf_path", "")
            if elf and Path(elf).exists():
                self._recent_elf_path = Path(elf)
            self._saved_monitored_variables = cfg.get("monitored_variables", []) or []
            self._hidden_displayed_variables = set(cfg.get("hidden_displayed_variables", []) or [])
            self._scope_pane_count = max(1, min(3, int(cfg.get("scope_pane_count", 1) or 1)))
            self._scope_sidebar_visible = bool(cfg.get("scope_sidebar_visible", True))
            self._scope_plot_split_sizes = self._coerce_splitter_sizes(cfg.get("scope_plot_splitter_sizes", []), 2)
            self._scope_plot_right_split_sizes = self._coerce_splitter_sizes(cfg.get("scope_plot_right_splitter_sizes", []), 2)
            saved_assignments = cfg.get("scope_assignments", {}) or {}
            if isinstance(saved_assignments, dict):
                self._saved_scope_assignments = {
                    str(name): tuple(bool(v) for v in list(values)[:3])
                    for name, values in saved_assignments.items()
                    if isinstance(values, (list, tuple)) and len(values) >= 3
                }
            saved_colors = cfg.get("curve_colors", {}) or {}
            if isinstance(saved_colors, dict):
                self._curve_color_overrides = {
                    str(name): QColor(str(color)).name()
                    for name, color in saved_colors.items()
                    if QColor(str(color)).isValid()
                }
        self._debug_backend = KeilUvSockBackendAdapter(
            KeilBackendConfig(root=self._keil_root, port=self._debug_uvsock_port)
        )

        self._plot_panes: list[ScopePane] = []
        self._workspace_pages: list[WorkspacePage] = []
        self._page_indices: dict[str, int] = {}
        self._workspace_domains: list[WorkspaceDomain] = [
            WorkspaceDomain("loopmaster", "LoopMaster", "变量调试与 MCU 示波"),
            WorkspaceDomain("debug", "调试工作台", "Keil 工程源码与断点预览"),
            WorkspaceDomain("serial", "串口助手", "串口收发与 VOFA 示波"),
        ]
        self._current_workspace_domain = "loopmaster"
        self._shutting_down = False
        self._plot_curve_names: tuple[tuple[str, ...], ...] = ()
        self._pulse_anims = []
        self._rate_display_last_count = 0
        self._rate_display_last_time = time.perf_counter()
        self._rate_display_value = 0.0
        self._rate_display_last_text = ""
        self._target_is_halted = False
        self._target_state_value = "--"

        # Timers 必须在 _setup_ui 之前创建，因为 setValue 会触发 valueChanged 信号
        self._plot_timer = QTimer(self)
        self._plot_timer.timeout.connect(self._update_plot)

        self._sample_timer = QTimer(self)
        self._sample_timer.setTimerType(Qt.PreciseTimer)
        self._sample_timer.timeout.connect(self._on_sample_tick)

        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._idle_read)

        self._debug_state_timer = QTimer(self)
        self._debug_state_timer.timeout.connect(self._refresh_debug_state)

        self._resize_pause_reasons: set[str] = set()
        self._scope_resize_active = False
        self._scope_render_suspended = False
        self._scope_viewport_update_modes: dict[int, object] = {}
        self._window_render_suspended = False
        self._window_render_suspended_attrs: list[str] = []
        self._plot_timer_resize_suspended = False
        self._plot_timer_active_before_resize = False
        self._scope_resize_last_plot = 0.0
        self._last_plot_draw_time = 0.0
        self._scope_layout_mode = None
        self._conn_layout_mode = None
        self._hero_roxy_visible = None
        self._scope_layout_syncing = False
        self._programmatic_plot_range_update = False
        self._scope_resize_sync_timer = QTimer(self)
        self._scope_resize_sync_timer.setSingleShot(True)
        self._scope_resize_sync_timer.timeout.connect(self._sync_scope_layout_to_window)
        self._scope_resize_settle_timer = QTimer(self)
        self._scope_resize_settle_timer.setSingleShot(True)
        self._scope_resize_settle_timer.timeout.connect(self._on_scope_splitter_settled)
        self._window_resize_sync_timer = QTimer(self)
        self._window_resize_sync_timer.setSingleShot(True)
        self._window_resize_sync_timer.timeout.connect(self._sync_window_layout_to_resize)
        self._resize_sync_interval_ms = 140

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._setup_motion()

        self._idle_timer.start(250)  # 绌洪棽璇诲彇淇濇寔 4Hz 鍥哄畾
        self._debug_state_timer.start(750)

        # Restore lightweight UI settings. ELF remains only a recent file on startup.
        if cfg:
            if "sample_rate" in cfg:
                self._set_rate_combo(cfg["sample_rate"])
            if "frame_rate" in cfg:
                self._set_frame_rate(cfg["frame_rate"])
            if "swd_freq_index" in cfg:
                idx = cfg["swd_freq_index"]
                if 0 <= idx < self._swd_freq_combo.count():
                    self._swd_freq_combo.setCurrentIndex(idx)
            # Always start in attach mode. Reset remains an explicit per-session choice.
            self._set_connect_mode_index(0)
            if "target_name" in cfg:
                self._set_target_name(cfg["target_name"])
            if "y_auto" in cfg:
                self._y_auto_btn.setChecked(cfg["y_auto"])
            if "x_scroll" in cfg:
                self._x_scroll_btn.setChecked(bool(cfg["x_scroll"]))
            self._restore_serial_config(cfg)
        self._refresh_recent_elf_button()
        self._refresh_hero()
        self._refresh_debug_state()
        self._window_resize_sync_timer.start(0)

    def _bind_button_motion(self, *buttons: QPushButton):
        if not hasattr(self, "_button_motion_filter"):
            self._button_motion_filter = PclHoverFilter(self)
        for button in buttons:
            if button is not None:
                self._button_motion_filter.bind(button)

    def _setup_motion(self):
        self._tabs.currentChanged.connect(self._animate_current_tab)
        self._pulse_anims = [
            start_status_pulse(self._conn_indicator, self),
            start_status_pulse(self._led, self),
        ]
        self._setup_roxy_mascot()

    def _animate_current_tab(self, index: int):
        page = self._tabs.widget(index)
        if page:
            animate_page_enter(page, self)

    def _register_workspace_page(
        self,
        key: str,
        title: str,
        widget: QWidget,
        domain: str = "loopmaster",
    ) -> int:
        if key in self._page_indices:
            return self._page_indices[key]
        index = self._tabs.addTab(widget, title)
        page = WorkspacePage(key=key, title=title, widget=widget, domain=domain)
        self._workspace_pages.append(page)
        self._page_indices[key] = index
        self._rebuild_nav_buttons()
        return index

    def _workspace_page_index(self, key: str, default: int = 0) -> int:
        return int(self._page_indices.get(key, default))

    def _show_workspace_page(self, key: str):
        page = self._workspace_page(key)
        if page is not None:
            self._current_workspace_domain = page.domain
        index = self._workspace_page_index(key, -1)
        if index >= 0:
            self._tabs.setCurrentIndex(index)
        self._rebuild_nav_buttons()
        self._refresh_hero()

    def _workspace_page(self, key: str) -> WorkspacePage | None:
        for page in getattr(self, "_workspace_pages", []):
            if page.key == key:
                return page
        return None

    def _set_workspace_domain(self, domain: str):
        if domain == getattr(self, "_current_workspace_domain", "loopmaster"):
            return
        self._current_workspace_domain = domain
        pages = [
            page
            for page in getattr(self, "_workspace_pages", [])
            if page.domain == domain
        ]
        if pages:
            self._tabs.setCurrentIndex(self._workspace_page_index(pages[0].key))
        self._rebuild_nav_buttons()
        self._refresh_hero()

    def _setup_roxy_mascot(self):
        if not hasattr(self, "_hero_roxy"):
            return
        self._hero_roxy_frames = _load_roxy_idle_frames()
        if not self._hero_roxy_frames:
            self._hero_roxy.setVisible(False)
            return
        self._hero_roxy_index = 0
        self._update_roxy_mascot_frame()
        self._hero_roxy_timer = QTimer(self)
        self._hero_roxy_timer.timeout.connect(self._advance_roxy_mascot_frame)
        self._hero_roxy_timer.start(220)
        self._sync_hero_layout()

    def _update_roxy_mascot_frame(self):
        if not self._hero_roxy_frames or not hasattr(self, "_hero_roxy"):
            return
        frame = self._hero_roxy_frames[self._hero_roxy_index % len(self._hero_roxy_frames)]
        scaled = frame.scaled(self._hero_roxy.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._hero_roxy.setPixmap(scaled)

    def _advance_roxy_mascot_frame(self):
        if not self._hero_roxy_frames:
            return
        self._hero_roxy_index = (self._hero_roxy_index + 1) % len(self._hero_roxy_frames)
        self._update_roxy_mascot_frame()

    def _sync_hero_layout(self):
        if not hasattr(self, "_hero_roxy"):
            return
        visible = bool(self._hero_roxy_frames) and self.width() >= 1280
        if visible != getattr(self, "_hero_roxy_visible", None):
            self._hero_roxy.setVisible(visible)
            self._hero_roxy_visible = visible

    def _sync_domain_chrome(self):
        domain = getattr(self, "_current_workspace_domain", "loopmaster")
        loopmaster_visible = domain == "loopmaster"
        if hasattr(self, "_conn_bar_widget"):
            self._conn_bar_widget.setVisible(loopmaster_visible)
        if hasattr(self, "_probe_menu_btn"):
            self._probe_menu_btn.setVisible(loopmaster_visible)

    def _setup_nav_rail(self) -> QFrame:
        nav = QFrame()
        nav.setObjectName("navRail")
        nav.setFixedWidth(156)
        install_card_shadow(nav, blur_radius=14, y_offset=4, alpha=14)

        layout = QVBoxLayout(nav)
        layout.setContentsMargins(10, 14, 10, 12)
        layout.setSpacing(7)

        title = QLabel("工作区")
        title.setObjectName("navTitle")
        layout.addWidget(title)

        hint = QLabel("选择功能")
        hint.setObjectName("navHint")
        layout.addWidget(hint)

        self._nav_domain_group = QButtonGroup(self)
        self._nav_domain_group.setExclusive(True)
        self._nav_domain_buttons: dict[str, QPushButton] = {}
        self._nav_buttons: list[QPushButton] = []
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        self._nav_domain_layout = QVBoxLayout()
        self._nav_domain_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_domain_layout.setSpacing(7)
        layout.addLayout(self._nav_domain_layout)

        self._nav_section_label = QLabel("LoopMaster")
        self._nav_section_label.setObjectName("navSection")
        layout.addWidget(self._nav_section_label)

        self._nav_button_layout = QVBoxLayout()
        self._nav_button_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_button_layout.setSpacing(7)
        layout.addLayout(self._nav_button_layout)
        self._rebuild_nav_buttons()

        layout.addStretch(1)

        self._nav_rate = QLabel("显示 60 FPS")
        self._nav_rate.setObjectName("navMetric")
        self._nav_rate.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._nav_rate)
        return nav

    def _rebuild_nav_buttons(self):
        if not hasattr(self, "_nav_button_layout"):
            return
        while self._nav_domain_layout.count():
            item = self._nav_domain_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                self._nav_domain_group.removeButton(widget)
                widget.setParent(None)
                widget.deleteLater()
        while self._nav_button_layout.count():
            item = self._nav_button_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                self._nav_group.removeButton(widget)
                widget.setParent(None)
                widget.deleteLater()

        self._nav_domain_buttons = {}
        self._nav_domain_group = QButtonGroup(self)
        self._nav_domain_group.setExclusive(True)
        for index, domain in enumerate(getattr(self, "_workspace_domains", [])):
            button = QPushButton(domain.title)
            button.setObjectName("navDomainButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(38)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setToolTip(domain.hint)
            button.clicked.connect(lambda checked=False, key=domain.key: self._set_workspace_domain(key))
            self._nav_domain_group.addButton(button, index)
            self._nav_domain_buttons[domain.key] = button
            self._nav_domain_layout.addWidget(button)
            self._bind_button_motion(button)

        self._nav_buttons = []
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        domain_key = getattr(self, "_current_workspace_domain", "loopmaster")
        pages = [
            page
            for page in getattr(self, "_workspace_pages", [])
            if page.domain == domain_key
        ]
        for index, page in enumerate(pages):
            button = QPushButton(page.title)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(36)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.clicked.connect(lambda checked=False, key=page.key: self._show_workspace_page(key))
            self._nav_group.addButton(button, index)
            self._nav_buttons.append(button)
            self._nav_button_layout.addWidget(button)
            self._bind_button_motion(button)
        self._refresh_nav_buttons()

    def _refresh_nav_buttons(self, *_args):
        if not hasattr(self, "_nav_buttons"):
            return
        current = self._tabs.currentIndex() if hasattr(self, "_tabs") else 0
        current_page = None
        for page in getattr(self, "_workspace_pages", []):
            if self._workspace_page_index(page.key, -1) == current:
                current_page = page
                break
        if current_page is not None:
            self._current_workspace_domain = current_page.domain
        domain_key = getattr(self, "_current_workspace_domain", "loopmaster")
        for key, button in getattr(self, "_nav_domain_buttons", {}).items():
            button.setChecked(key == domain_key)
        domain = next(
            (item for item in getattr(self, "_workspace_domains", []) if item.key == domain_key),
            None,
        )
        if hasattr(self, "_nav_section_label") and domain is not None:
            self._nav_section_label.setText(domain.title)

        pages = [
            page
            for page in getattr(self, "_workspace_pages", [])
            if page.domain == domain_key
        ]
        for index, (button, page) in enumerate(zip(self._nav_buttons, pages), 1):
            tab_index = self._workspace_page_index(page.key, -1)
            text = self._tabs.tabText(tab_index) if tab_index >= 0 else page.title
            button.setText(text)
            button.setChecked(tab_index == current)
        if hasattr(self, "_nav_rate"):
            self._nav_rate.setText(f"显示 {self._frame_rate} FPS")
        self._sync_domain_chrome()

    def _setup_title_panel(self) -> QFrame:
        hero = QFrame()
        hero.setObjectName("pclHero")
        install_card_shadow(hero, blur_radius=22, y_offset=6, alpha=42)

        layout = QHBoxLayout(hero)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(14)

        logo = QLabel()
        logo.setObjectName("heroLogo")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(50, 50)
        logo_pixmap = _load_logo_pixmap()
        if logo_pixmap.isNull():
            logo.setText("LM")
        else:
            logo.setPixmap(logo_pixmap)
            logo.setScaledContents(True)
        layout.addWidget(logo)

        title_stack = QVBoxLayout()
        title_stack.setSpacing(1)
        title = QLabel("LoopMaster")
        title.setObjectName("heroTitle")
        subtitle = QLabel("非侵入式 MCU 变量示波器")
        subtitle.setObjectName("heroSubtitle")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        layout.addLayout(title_stack)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self._file_menu_btn = QPushButton("文件")
        self._file_menu_btn.setObjectName("heroMenuButton")
        self._file_menu_btn.setMenu(self._file_menu)
        self._probe_menu_btn = QPushButton("调试器")
        self._probe_menu_btn.setObjectName("heroMenuButton")
        self._probe_menu_btn.setMenu(self._probe_menu)
        self._display_menu_btn = QPushButton(f"显示 {FRAME_RATE_DEFAULT} FPS")
        self._display_menu_btn.setObjectName("heroMenuButton")
        self._display_menu_btn.setMenu(self._display_menu)
        self._display_menu_btn.setMinimumWidth(126)
        for button in (self._file_menu_btn, self._probe_menu_btn, self._display_menu_btn):
            button.setFixedHeight(34)
        nav.addWidget(self._file_menu_btn)
        nav.addWidget(self._probe_menu_btn)
        nav.addWidget(self._display_menu_btn)
        layout.addLayout(nav)

        layout.addStretch(1)

        self._hero_roxy = QLabel()
        self._hero_roxy.setObjectName("heroMascot")
        self._hero_roxy.setAlignment(Qt.AlignCenter)
        self._hero_roxy.setFixedSize(58, 63)
        self._hero_roxy.setToolTip("Roxy 睡眠动画")
        layout.addWidget(self._hero_roxy)

        self._hero_file = QLabel("未加载 ELF/AXF")
        self._hero_file.setObjectName("heroPill")
        self._hero_probe = QLabel("调试器未连接")
        self._hero_probe.setObjectName("heroPill")
        self._hero_vars = QLabel("0 个变量")
        self._hero_vars.setObjectName("heroPill")
        layout.addWidget(self._hero_file)
        layout.addWidget(self._hero_probe)
        layout.addWidget(self._hero_vars)
        return hero

    def _refresh_hero(self):
        if not hasattr(self, "_hero_file"):
            return
        self._sync_hero_layout()
        self._sync_domain_chrome()
        if getattr(self, "_current_workspace_domain", "loopmaster") == "serial":
            port_text = "未选择串口"
            baud_text = "波特率 --"
            connected = False
            if hasattr(self, "_tab_serial"):
                current_port = self._tab_serial.port_combo.currentData() or self._tab_serial.port_combo.currentText()
                if current_port and not str(current_port).lower().startswith("no serial") and "未发现" not in str(current_port):
                    port_text = str(current_port)
                baud_text = f"{self._tab_serial.baud_combo.currentText()} 波特"
                connected = bool(self._tab_serial.is_connected)
            channel_count = self._serial_controller.channel_count(10.0)
            self._hero_file.setText(port_text)
            self._hero_probe.setText("串口已连接" if connected else "串口空闲")
            self._hero_vars.setText(f"{baud_text} / {channel_count} 路波形")
            return
        if getattr(self, "_current_workspace_domain", "loopmaster") == "debug":
            if hasattr(self, "_tab_debug_workbench"):
                project_text, source_text, breakpoint_text = self._tab_debug_workbench.hero_summary()
            else:
                project_text, source_text, breakpoint_text = "未打开 Keil 工程", "只读预览", "0 个断点"
            self._hero_file.setText(project_text)
            self._hero_probe.setText(source_text)
            self._hero_vars.setText(breakpoint_text)
            return

        self._hero_file.setText(self._elf_path.name if self._elf_path else "未加载 ELF/AXF")
        if self._backend.is_connected:
            target = self._backend.target_name or "已连接"
            probe = self._backend.probe_kind or self._backend.probe_name or "SWD"
            self._hero_probe.setText(f"{probe} → {target}")
        else:
            self._hero_probe.setText("调试器未连接")
        self._hero_vars.setText(f"{len(self._monitored)} 个变量")

    def _refresh_recent_elf_button(self):
        if not hasattr(self, "_btn_recent_elf"):
            return
        has_recent = bool(self._recent_elf_path and self._recent_elf_path.exists())
        self._btn_recent_elf.setVisible(has_recent)
        if hasattr(self, "_act_recent_elf"):
            self._act_recent_elf.setEnabled(has_recent)
        if has_recent:
            recent_name = self._recent_elf_path.name
            self._btn_recent_elf.setText(f"加载最近 {recent_name}")
            self._btn_recent_elf.setToolTip(f"加载最近文件：{self._recent_elf_path}")
            if hasattr(self, "_act_recent_elf"):
                self._act_recent_elf.setText(f"加载最近 ELF：{recent_name}")
        else:
            self._btn_recent_elf.setText("加载最近 ELF")
            self._btn_recent_elf.setToolTip("")
            if hasattr(self, "_act_recent_elf"):
                self._act_recent_elf.setText("加载最近 ELF")

    def _load_elf_path(self, path: Path):
        self._elf_path = Path(path)
        self._recent_elf_path = self._elf_path
        self._loaded_elf_this_session = True
        self._temporary_writes.clear()
        self._load_variables()
        self.setWindowTitle(f"LoopMaster v2.1 - {self._elf_path.name}")
        self._refresh_recent_elf_button()
        self._refresh_hero()

    def _set_parse_controls_enabled(self, enabled: bool):
        for attr in ("_btn_import_elf", "_btn_recent_elf", "_filter_edit", "_tree"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _show_parse_dialog(self, elf_path: Path) -> PclLoadingDialog:
        dialog = PclLoadingDialog(
            self,
            "正在加载 ELF/AXF",
            f"正在解析 {elf_path.name}，请稍候…",
        )
        dialog.show()
        QApplication.processEvents()
        return dialog

    def _show_info(self, title: str, message: str):
        show_pcl_message(self, title, message, "info")

    def _show_warning(self, title: str, message: str):
        show_pcl_message(self, title, message, "warning")

    # ================================================================
    #  Menu
    # ================================================================

    def _setup_menu(self):
        self._file_menu = QMenu("文件", self)
        act_elf = QAction("导入 ELF/AXF…", self)
        act_elf.triggered.connect(self._on_import_elf)
        self._file_menu.addAction(act_elf)

        self._act_recent_elf = QAction("加载最近 ELF", self)
        self._act_recent_elf.triggered.connect(self._on_load_recent_elf)
        self._act_recent_elf.setEnabled(False)
        self._file_menu.addAction(self._act_recent_elf)

        act_pack = QAction("导入 CMSIS-Pack…", self)
        act_pack.triggered.connect(self._on_import_pack)
        self._file_menu.addAction(act_pack)

        self._file_menu.addSeparator()
        act_log = QAction("查看日志", self)
        act_log.triggered.connect(self._on_view_log)
        self._file_menu.addAction(act_log)

        act_exit = QAction("退出", self)
        act_exit.triggered.connect(self.close)
        self._file_menu.addAction(act_exit)

        self._probe_menu = QMenu("调试器", self)
        act_scan = QAction("扫描调试器", self)
        act_scan.triggered.connect(self._on_scan_probes_ui)
        self._probe_menu.addAction(act_scan)

        act_connect = QAction("连接", self)
        act_connect.triggered.connect(self._on_connect_ui)
        self._probe_menu.addAction(act_connect)

        act_disconnect = QAction("断开", self)
        act_disconnect.triggered.connect(self._on_disconnect_ui)
        self._probe_menu.addAction(act_disconnect)

        # 显示菜单：帧率选择
        self._display_menu = QMenu("显示", self)
        self._fps_actions = []
        self._fps_group = QAction(self)  # dummy action group holder
        for fps in PRESET_FRAME_RATES:
            act = QAction(f"{fps} FPS", self)
            act.setCheckable(True)
            act.setChecked(fps == FRAME_RATE_DEFAULT)
            act.setData(fps)
            act.triggered.connect(lambda checked, f=fps: self._set_frame_rate(f))
            self._display_menu.addAction(act)
            self._fps_actions.append(act)

    # ================================================================
    #  Status Bar
    # ================================================================

    def _setup_statusbar(self):
        # Status is rendered inside the PCL-style connection card.
        pass

    def _polish_combo_popup(self, combo: QComboBox):
        """Keep combo popups tight and fully themed."""
        combo.setMaxVisibleItems(max(1, combo.count()))
        polish_combo_popup(combo)
        view = combo.view()
        popup_width = max(combo.minimumWidth(), combo.width(), combo.sizeHint().width())
        if popup_width > 0:
            view.setMinimumWidth(popup_width)

    def _choose_open_file(self, title: str, name_filter: str) -> str:
        dialog = QFileDialog(self, title)
        dialog.setFileMode(QFileDialog.ExistingFile)
        dialog.setNameFilter(name_filter)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setFont(QFont("Microsoft YaHei UI", 10))
        dialog.setLabelText(QFileDialog.Accept, "打开")
        dialog.setLabelText(QFileDialog.Reject, "取消")
        if dialog.exec() == QFileDialog.Accepted:
            selected = dialog.selectedFiles()
            return selected[0] if selected else ""
        return ""

    def _choose_save_file(self, title: str, default_name: str, name_filter: str) -> str:
        dialog = QFileDialog(self, title)
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        dialog.setNameFilter(name_filter)
        dialog.selectFile(default_name)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setFont(QFont("Microsoft YaHei UI", 10))
        dialog.setLabelText(QFileDialog.Accept, "保存")
        dialog.setLabelText(QFileDialog.Reject, "取消")
        if dialog.exec() == QFileDialog.Accepted:
            selected = dialog.selectedFiles()
            return selected[0] if selected else ""
        return ""

    # ================================================================
    #  Connection Bar
    # ================================================================

    def _setup_connection_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("connectionBar")
        install_card_shadow(bar, blur_radius=20, y_offset=5, alpha=24)
        layout = QHBoxLayout(bar)
        self._conn_layout = layout
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        self._icon_label = QLabel("SWD")
        self._icon_label.setObjectName("chipLabel")
        layout.addWidget(self._icon_label)

        self._probe_combo = PclComboBox()
        self._probe_combo.setFixedWidth(320)
        self._probe_combo.setPlaceholderText("扫描以查找调试器…")
        self._polish_combo_popup(self._probe_combo)
        layout.addWidget(self._probe_combo)

        self._btn_scan = QPushButton("扫描")
        self._btn_scan.setObjectName("scanBtn")
        self._btn_scan.setFixedSize(88, 44)
        self._btn_scan.clicked.connect(self._on_scan_probes_ui)
        layout.addWidget(self._btn_scan)

        self._target_label = QLabel("目标芯片")
        self._target_label.setObjectName("barTitle")
        layout.addWidget(self._target_label)
        self._target_combo = PclComboBox()
        self._target_combo.setFixedWidth(230)
        self._target_combo.setEditable(True)
        self._target_combo.setInsertPolicy(QComboBox.NoInsert)
        self._target_combo.setToolTip(
            "STM32 通常选择 Cortex-M 即可，也可以输入 pyOCD 目标名，例如 stm32f103rc。"
            "TI MSPM0G3507 可使用 mspm0g3507 或 m0g3507。")
        if self._target_combo.lineEdit():
            self._target_combo.lineEdit().setPlaceholderText("cortex_m / stm32f103rc / mspm0g3507")
        for label, target_name in TARGET_OPTIONS:
            self._target_combo.addItem(label, target_name)
        self._target_combo.setCurrentIndex(0)
        self._target_combo.setEditText(DEFAULT_TARGET)
        self._target_combo.currentIndexChanged.connect(self._on_target_index_changed)
        self._polish_combo_popup(self._target_combo)
        layout.addWidget(self._target_combo)

        self._conn_sep1 = QFrame()
        self._conn_sep1.setFrameShape(QFrame.NoFrame)
        self._conn_sep1.setObjectName("connSeparator")
        self._conn_sep1.setFixedWidth(1)
        layout.addWidget(self._conn_sep1)

        self._mode_label = QLabel("模式")
        self._mode_label.setObjectName("barTitle")
        layout.addWidget(self._mode_label)
        self._mode_segment = QFrame()
        self._mode_segment.setObjectName("segmentedControl")
        self._mode_segment.setFixedWidth(132)
        mode_layout = QHBoxLayout(self._mode_segment)
        mode_layout.setContentsMargins(1, 1, 1, 1)
        mode_layout.setSpacing(0)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_attach_btn = QPushButton("附加")
        self._mode_reset_btn = QPushButton("复位")
        for button, mode_id in (
            (self._mode_attach_btn, 0),
            (self._mode_reset_btn, 1),
        ):
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.setFixedSize(64, 32)
            self._mode_group.addButton(button, mode_id)
            mode_layout.addWidget(button)
        self._mode_attach_btn.setChecked(True)
        layout.addWidget(self._mode_segment)

        self._conn_sep2 = QFrame()
        self._conn_sep2.setFrameShape(QFrame.NoFrame)
        self._conn_sep2.setObjectName("connSeparator")
        self._conn_sep2.setFixedWidth(1)
        layout.addWidget(self._conn_sep2)

        self._swd_label = QLabel("速度")
        self._swd_label.setObjectName("barTitle")
        layout.addWidget(self._swd_label)
        self._swd_freq_combo = PclComboBox()
        self._swd_freq_combo.setFixedWidth(112)
        self._swd_freq_combo.addItems(["1 MHz", "4 MHz", "10 MHz", "20 MHz", "40 MHz"])
        self._swd_freq_combo.setCurrentIndex(1)
        self._polish_combo_popup(self._swd_freq_combo)
        layout.addWidget(self._swd_freq_combo)

        self._conn_sep3 = QFrame()
        self._conn_sep3.setFrameShape(QFrame.NoFrame)
        self._conn_sep3.setObjectName("connSeparator")
        self._conn_sep3.setFixedWidth(1)
        layout.addWidget(self._conn_sep3)

        self._btn_connect = QPushButton("连接")
        self._btn_connect.setObjectName("connectBtn")
        self._btn_connect.setFixedSize(92, 44)
        self._btn_connect.clicked.connect(self._on_connect_ui)
        layout.addWidget(self._btn_connect)

        self._conn_indicator = QLabel("●")
        self._conn_indicator.setStyleSheet("color: #e04040; font-size: 16px; padding: 0px 4px;")
        layout.addWidget(self._conn_indicator)

        self._conn_label = QLabel("未连接")
        self._conn_label.setStyleSheet("color: #737373; font-size: 9pt;")
        self._conn_label.setMinimumWidth(92)
        self._conn_label.setMaximumWidth(120)
        layout.addWidget(self._conn_label)

        layout.addStretch()
        self._conn_info = QLabel("")
        self._conn_info.setObjectName("statusText")
        layout.addWidget(self._conn_info)

        self._status_cluster = QFrame()
        self._status_cluster.setObjectName("statusCluster")
        self._status_cluster.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._status_cluster.setFixedWidth(235)
        status_layout = QHBoxLayout(self._status_cluster)
        status_layout.setContentsMargins(10, 5, 10, 5)
        status_layout.setSpacing(7)

        self._led = QLabel("●")
        self._led.setObjectName("statusLed")
        self._led.setStyleSheet("color: #e04040;")
        status_layout.addWidget(self._led)

        self._sb_label = QLabel("调试器：未连接")
        self._sb_label.setObjectName("statusText")
        self._sb_label.setMinimumWidth(88)
        status_layout.addWidget(self._sb_label)

        self._status_sep = QFrame()
        self._status_sep.setFrameShape(QFrame.NoFrame)
        self._status_sep.setObjectName("connSeparator")
        self._status_sep.setFixedWidth(1)
        status_layout.addWidget(self._status_sep)

        self._sb_rate = QLabel("实际：-- Hz")
        self._sb_rate.setObjectName("statusRate")
        self._sb_rate.setMinimumWidth(118)
        status_layout.addWidget(self._sb_rate)
        layout.addWidget(self._status_cluster)

        self._bind_button_motion(self._btn_scan, self._btn_connect)
        QTimer.singleShot(0, lambda: self._fit_connection_bar(force=True))
        return bar

    def _fit_connection_bar(self, force: bool = False):
        if not hasattr(self, "_conn_bar_widget"):
            return
        width = self._conn_bar_widget.width()
        if width >= 2100:
            mode = "wide"
        elif width >= 1680:
            mode = "compact"
        elif width >= 1320:
            mode = "narrow"
        elif width >= 1040:
            mode = "tight"
        elif width >= 720:
            mode = "mini"
        else:
            mode = "micro"

        if not force and mode == getattr(self, "_conn_layout_mode", None):
            return
        self._conn_layout_mode = mode

        show_icon = mode == "wide"
        show_probe = mode in {"wide", "compact", "narrow"}
        show_scan = mode != "micro"
        show_target = mode in {"wide", "compact", "narrow"}
        show_mode = mode == "wide"
        show_swd = mode in {"wide", "compact", "narrow", "tight"}
        show_conn_text = mode == "wide"
        show_detail = mode == "wide"
        show_probe_text = mode == "wide"

        self._conn_layout.setSpacing(10 if mode in {"wide", "compact"} else 8 if mode == "narrow" else 6)
        self._icon_label.setVisible(show_icon)
        self._probe_combo.setVisible(show_probe)
        self._btn_scan.setVisible(show_scan)
        self._target_label.setVisible(mode == "wide")
        self._target_combo.setVisible(show_target)
        self._conn_sep1.setVisible(show_mode)
        self._mode_label.setVisible(show_mode)
        self._mode_segment.setVisible(show_mode)
        self._conn_sep2.setVisible(show_swd and show_mode)
        self._swd_label.setVisible(mode == "wide")
        self._swd_freq_combo.setVisible(show_swd)
        self._conn_sep3.setVisible(show_swd)
        self._conn_indicator.setVisible(show_conn_text)
        self._conn_label.setVisible(show_conn_text and mode in {"wide", "compact"})
        self._conn_info.setVisible(show_detail)
        self._status_cluster.setVisible(True)
        self._sb_label.setVisible(show_probe_text)
        self._status_sep.setVisible(show_probe_text)

        self._probe_combo.setFixedWidth(
            340 if mode == "wide" else 210 if mode == "compact"
            else 190 if mode == "narrow" else 168 if mode == "tight"
            else 170 if mode == "mini" else 160
        )
        self._target_combo.setFixedWidth(
            230 if mode == "wide" else 160 if mode == "compact"
            else 156 if mode == "narrow" else 136 if mode == "tight"
            else 136 if mode == "mini" else 124
        )
        self._swd_freq_combo.setFixedWidth(
            116 if mode == "wide" else 112 if mode == "compact"
            else 112 if mode == "narrow" else 108 if mode == "tight"
            else 84 if mode == "mini" else 80
        )
        self._btn_scan.setFixedSize(90 if mode in {"wide", "compact"} else 84 if mode == "narrow" else 80 if mode == "tight" else 68, 44)
        self._btn_connect.setFixedSize(116 if mode in {"wide", "compact"} else 112 if mode == "narrow" else 110 if mode == "tight" else 92, 44)
        self._status_cluster.setFixedWidth(
            280 if show_probe_text else 188 if mode == "compact"
            else 176 if mode == "narrow" else 172 if mode == "tight"
            else 158 if mode == "mini" else 148
        )
        self._sb_rate.setMinimumWidth(122 if mode == "wide" else 116 if mode in {"compact", "narrow"} else 108)
        self._polish_combo_popup(self._probe_combo)
        self._polish_combo_popup(self._target_combo)
        self._polish_combo_popup(self._swd_freq_combo)
        self._conn_layout.invalidate()
        self._conn_layout.activate()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_hero_layout()
        if hasattr(self, "_window_resize_sync_timer"):
            self._begin_resize_pause("window", suspend_scope=True, suspend_window=True)
            self._window_resize_sync_timer.start(self._resize_sync_interval_ms)

    def _sync_window_layout_to_resize(self):
        try:
            self._fit_connection_bar()
            self._fit_scope_layout(sync_splitter=False)
        finally:
            self._end_resize_pause("window", resume_window=True)

    def _begin_resize_pause(
        self,
        reason: str,
        suspend_scope: bool = False,
        suspend_window: bool = False,
    ):
        reasons = getattr(self, "_resize_pause_reasons", set())
        reasons.add(reason)
        self._resize_pause_reasons = reasons
        self._scope_resize_active = True
        self._set_plot_timer_resize_suspended(True)
        if suspend_scope:
            self._set_scope_rendering_suspended(True)
        if suspend_window:
            self._set_window_rendering_suspended(True)

    def _end_resize_pause(self, reason: str, resume_window: bool = False):
        reasons = getattr(self, "_resize_pause_reasons", set())
        reasons.discard(reason)
        self._resize_pause_reasons = reasons
        self._scope_resize_active = bool(reasons)
        if resume_window and "window" not in reasons:
            self._set_window_rendering_suspended(False)
        if reasons:
            return
        self._set_scope_rendering_suspended(False)
        self._last_plot_draw_time = 0.0
        self._set_plot_timer_resize_suspended(False)

    def _set_plot_timer_resize_suspended(self, suspended: bool):
        if suspended == getattr(self, "_plot_timer_resize_suspended", False):
            return
        self._plot_timer_resize_suspended = suspended
        if suspended:
            self._plot_timer_active_before_resize = bool(self._plot_timer.isActive())
            if self._plot_timer_active_before_resize:
                self._plot_timer.stop()
            return

        should_restart = bool(getattr(self, "_plot_timer_active_before_resize", False))
        self._plot_timer_active_before_resize = False
        if should_restart and not self._plot_timer.isActive():
            self._apply_plot_timer_interval()
            self._plot_timer.start()

    def _set_window_rendering_suspended(self, suspended: bool):
        if suspended == getattr(self, "_window_render_suspended", False):
            return
        self._window_render_suspended = suspended
        if suspended:
            attrs = [
                "_hero_widget",
                "_conn_bar_widget",
                "_workspace_shell",
            ]
            self._window_render_suspended_attrs = attrs
        else:
            attrs = list(getattr(self, "_window_render_suspended_attrs", []))
            self._window_render_suspended_attrs = []
        for attr in attrs:
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setUpdatesEnabled(not suspended)
        if not suspended:
            for attr in attrs:
                widget = getattr(self, attr, None)
                if widget is not None:
                    widget.update()

    def _set_scope_rendering_suspended(self, suspended: bool):
        if suspended == getattr(self, "_scope_render_suspended", False):
            return
        self._scope_render_suspended = suspended
        modes = getattr(self, "_scope_viewport_update_modes", {})
        for pane in getattr(self, "_scope_panes", []):
            plot_widget = getattr(pane, "plot_widget", None)
            if plot_widget is None:
                continue
            key = id(plot_widget)
            if suspended:
                modes.setdefault(key, plot_widget.viewportUpdateMode())
                plot_widget.setViewportUpdateMode(QGraphicsView.NoViewportUpdate)
            else:
                old_mode = modes.pop(key, QGraphicsView.BoundingRectViewportUpdate)
                plot_widget.setViewportUpdateMode(old_mode)
            plot_widget.setUpdatesEnabled(not suspended)
            viewport = plot_widget.viewport()
            if viewport is not None:
                viewport.setUpdatesEnabled(not suspended)
            if not suspended:
                plot_widget.update()
        self._scope_viewport_update_modes = modes

    def _update_conn_status(self, connected: bool):
        if connected:
            self._conn_indicator.setStyleSheet("color: #2fb344; font-size: 16px; padding: 0px 4px;")
            self._led.setStyleSheet("color: #2fb344;")
            self._conn_label.setText("已连接")
            self._conn_label.setStyleSheet("color: #2fb344; font-weight: 600; font-size: 9pt;")
            if self._probe_combo.count() == 0:
                probe_kind = self._backend.probe_kind or "调试器"
                probe_name = self._backend.probe_name or "已连接"
                self._probe_combo.addItem(f"{probe_kind} - {probe_name}")
            self._btn_connect.setText("断开")
            self._btn_connect.setObjectName("disconnectBtn")
            self._conn_info.setText(
                f"调试器：{self._backend.probe_kind or self._backend.probe_name}  |  "
                f"目标：{self._backend.target_name}  |  "
                f"SWD: ~{self._backend.swd_freq_khz} kHz"
            )
            self._sb_label.setText(
                f"调试器：{self._backend.probe_kind or '已连接'}")
            self._probe_combo.setEnabled(False)
            self._target_combo.setEnabled(False)
            self._btn_scan.setEnabled(False)
            self._set_connect_mode_enabled(False)
            self._swd_freq_combo.setEnabled(False)
        else:
            self._conn_indicator.setStyleSheet("color: #e04040; font-size: 16px; padding: 0px 4px;")
            self._led.setStyleSheet("color: #e04040;")
            self._conn_label.setText("未连接")
            self._conn_label.setStyleSheet("color: #737373; font-size: 9pt;")
            self._btn_connect.setText("连接")
            self._btn_connect.setObjectName("connectBtn")
            self._conn_info.setText("")
            self._sb_label.setText("调试器：未连接")
            self._probe_combo.setEnabled(True)
            self._target_combo.setEnabled(True)
            self._btn_scan.setEnabled(True)
            self._set_connect_mode_enabled(True)
            self._swd_freq_combo.setEnabled(True)
        # 强制样式刷新
        self._btn_connect.style().unpolish(self._btn_connect)
        self._btn_connect.style().polish(self._btn_connect)
        self._refresh_hero()
        self._refresh_debug_buttons()

    def _connect_mode_index(self) -> int:
        if hasattr(self, "_mode_group"):
            checked = self._mode_group.checkedId()
            return checked if checked in (0, 1) else 0
        return 0

    def _set_connect_mode_index(self, index: int):
        if not hasattr(self, "_mode_group"):
            return
        if index == 1:
            self._mode_reset_btn.setChecked(True)
        else:
            self._mode_attach_btn.setChecked(True)

    def _connect_mode(self) -> str:
        return "reset" if self._connect_mode_index() == 1 else "attach"

    def _set_connect_mode_enabled(self, enabled: bool):
        for attr in ("_mode_segment", "_mode_attach_btn", "_mode_reset_btn"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _selected_target_name(self) -> str:
        if hasattr(self, "_target_combo"):
            text = self._target_combo.currentText().strip()
            index = self._target_combo.currentIndex()
            target_name = self._target_combo.currentData()
            if target_name and index >= 0 and text == self._target_combo.itemText(index):
                return str(target_name)
            if text:
                return text.lower().replace("-", "_")
        return DEFAULT_TARGET

    def _on_target_index_changed(self, index: int):
        if not hasattr(self, "_target_combo") or index < 0:
            return
        target_name = self._target_combo.itemData(index)
        if target_name:
            self._target_combo.setEditText(str(target_name))

    def _set_target_name(self, target_name: str):
        if not hasattr(self, "_target_combo") or not target_name:
            return
        normalised = str(target_name).strip().lower().replace("-", "_")
        for index in range(self._target_combo.count()):
            if self._target_combo.itemData(index) == normalised:
                self._target_combo.setCurrentIndex(index)
                self._target_combo.setEditText(normalised)
                return
        if self._target_combo.isEditable():
            self._target_combo.setEditText(normalised)

    # ================================================================
    #  Main UI
    # ================================================================

    def _setup_ui(self):
        central = QWidget()
        central.setObjectName("root")
        self._root_widget = central
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        self._hero_widget = self._setup_title_panel()
        root.addWidget(self._hero_widget)

        # 杩炴帴鏍忔彃鍦ㄦ渶椤堕儴
        self._conn_bar_widget = self._setup_connection_bar()
        root.addWidget(self._conn_bar_widget)

        # Cockpit-style workspace: custom left rail + hidden tab stack.
        tab_container = QFrame()
        tab_container.setObjectName("workspaceShell")
        self._workspace_shell = tab_container
        install_card_shadow(tab_container, blur_radius=24, y_offset=7, alpha=24)
        tab_container_layout = QHBoxLayout(tab_container)
        tab_container_layout.setContentsMargins(5, 5, 5, 5)
        tab_container_layout.setSpacing(6)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("workspaceTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().hide()
        self._tabs.currentChanged.connect(self._refresh_nav_buttons)
        nav_rail = self._setup_nav_rail()
        tab_container_layout.addWidget(nav_rail)
        tab_container_layout.addWidget(self._tabs, stretch=1)
        root.addWidget(tab_container, stretch=1)
        self._tabs.currentChanged.connect(lambda _: self._refresh_hero())

        # ---- Tab 1: Variable Selection ----
        self._tab_vars = QWidget()
        self._register_workspace_page("variables", "变量", self._tab_vars)

        tab1_layout = QVBoxLayout(self._tab_vars)
        tab1_layout.setContentsMargins(0, 0, 0, 0)
        tab1_layout.setSpacing(12)

        # Import + search bar
        toolbar = QFrame()
        toolbar.setObjectName("toolbarCard")
        install_card_shadow(toolbar, blur_radius=18, y_offset=4, alpha=26)
        top_bar = QHBoxLayout(toolbar)
        top_bar.setContentsMargins(12, 10, 12, 10)
        top_bar.setSpacing(8)

        self._btn_import_elf = QPushButton("导入 ELF/AXF")
        self._btn_import_elf.setObjectName("importBtn")
        self._btn_import_elf.clicked.connect(self._on_import_elf)
        top_bar.addWidget(self._btn_import_elf)

        self._btn_recent_elf = QPushButton("加载上次 ELF")
        self._btn_recent_elf.setObjectName("smallBtn")
        self._btn_recent_elf.clicked.connect(self._on_load_recent_elf)
        top_bar.addWidget(self._btn_recent_elf)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("按名称搜索变量…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self._filter_edit, stretch=1)

        btn_collapse = QPushButton("全部折叠")
        btn_collapse.setObjectName("smallBtn")
        btn_collapse.clicked.connect(lambda: self._tree.collapseAll())
        top_bar.addWidget(btn_collapse)

        self._bind_button_motion(self._btn_import_elf, self._btn_recent_elf, btn_collapse)
        self._refresh_recent_elf_button()
        tab1_layout.addWidget(toolbar)

        # Variable tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["变量", "地址", "类型"])
        self._tree.setColumnWidth(1, 110)
        self._tree.setColumnWidth(2, 200)
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(18)
        self._tree.setAnimated(True)
        self._tree.setAllColumnsShowFocus(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QAbstractItemView.MultiSelection)
        self._tree.itemClicked.connect(self._on_tree_item_clicked)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        tab1_layout.addWidget(self._tree)

        hint = QLabel("提示：展开结构体可选择成员；按 Ctrl/Shift 多选。")
        hint.setObjectName("hintLabel")
        tab1_layout.addWidget(hint)

        # ---- Tab 2: Scope ----
        self._tab_scope = QWidget()
        self._register_workspace_page("scope", "示波器", self._tab_scope)

        tab2_layout = QVBoxLayout(self._tab_scope)
        tab2_layout.setContentsMargins(0, 0, 0, 0)
        tab2_layout.setSpacing(0)

        self._scope_splitter = QSplitter(Qt.Horizontal)
        self._scope_splitter.setObjectName("scopeMainSplitter")
        self._scope_splitter.setChildrenCollapsible(False)
        self._scope_splitter.setHandleWidth(2)
        self._scope_splitter.setOpaqueResize(False)
        self._scope_splitter.splitterMoved.connect(self._on_scope_splitter_moved)
        tab2_layout.addWidget(self._scope_splitter, stretch=1)

        sidebar_scroll = QScrollArea()
        sidebar_scroll.setObjectName("scopeSidebarScroll")
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QFrame.NoFrame)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sidebar_scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        sidebar_scroll.setMinimumWidth(240)
        self._scope_sidebar_scroll = sidebar_scroll

        sidebar_host = QWidget()
        sidebar_host.setObjectName("scopeSidebar")
        sidebar_layout = QVBoxLayout(sidebar_host)
        self._scope_sidebar_layout = sidebar_layout
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(10)

        # -- Real-time value table --
        table_frame = QFrame()
        table_frame.setObjectName("panel")
        install_card_shadow(table_frame, blur_radius=18, y_offset=4, alpha=24)
        table_frame.setMaximumHeight(250)
        table_layout = QVBoxLayout(table_frame)
        self._scope_table_layout = table_layout
        table_layout.setContentsMargins(12, 9, 12, 10)

        table_header = QLabel("实时数值")
        table_header.setObjectName("sectionTitle")
        table_layout.addWidget(table_header)

        self._value_table = QTableWidget()
        self._value_table.setColumnCount(7)
        self._value_table.setHorizontalHeaderLabels(["变量", "值", "地址", "类型", "窗1", "窗2", "窗3"])
        self._value_table.setAlternatingRowColors(True)
        self._value_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._value_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._value_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._value_table.verticalHeader().setDefaultSectionSize(34)
        self._value_table.verticalHeader().setMinimumSectionSize(34)
        self._value_table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self._value_scope_delegates = [
            PclScopeToggleDelegate(ROLE_DISPLAYED, "#1d4ed8", self._value_table),
            PclScopeToggleDelegate(ROLE_SCOPE_2, "#0ea5a5", self._value_table),
            PclScopeToggleDelegate(ROLE_SCOPE_3, "#f97316", self._value_table),
        ]
        for column, delegate in zip((4, 5, 6), self._value_scope_delegates):
            self._value_table.setItemDelegateForColumn(column, delegate)
        self._value_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._value_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._value_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._value_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        for column in (4, 5, 6):
            self._value_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Fixed)
            self._value_table.setColumnWidth(column, 40)
        self._value_table.setMinimumHeight(172)
        self._value_table.setMaximumHeight(202)
        self._value_table.setRowCount(0)
        self._value_table.itemSelectionChanged.connect(self._on_value_selection_changed)
        self._value_table.itemChanged.connect(self._on_value_table_item_changed)
        table_layout.addWidget(self._value_table)
        sidebar_layout.addWidget(table_frame)

        plot_tools_frame = QFrame()
        plot_tools_frame.setObjectName("plotToolBar")
        install_card_shadow(plot_tools_frame, blur_radius=18, y_offset=4, alpha=22)
        plot_tools_layout = QVBoxLayout(plot_tools_frame)
        self._scope_plot_tools_layout = plot_tools_layout
        plot_tools_layout.setContentsMargins(12, 10, 12, 10)
        plot_tools_layout.setSpacing(8)

        row_curve = QHBoxLayout()
        row_curve.setSpacing(6)
        color_label = QLabel("曲线颜色")
        color_label.setObjectName("barTitle")
        row_curve.addWidget(color_label)

        self._curve_color_combo = PclComboBox()
        self._curve_color_combo.setFixedWidth(170)
        self._curve_color_combo.currentIndexChanged.connect(self._on_curve_color_target_changed)
        self._polish_combo_popup(self._curve_color_combo)
        row_curve.addWidget(self._curve_color_combo)

        self._btn_curve_color = QPushButton("选择")
        self._btn_curve_color.setObjectName("colorSwatchBtn")
        self._btn_curve_color.setFixedSize(58, 36)
        self._btn_curve_color.clicked.connect(self._on_choose_curve_color)
        row_curve.addWidget(self._btn_curve_color)

        self._btn_next_color = QPushButton("下一个")
        self._btn_next_color.setObjectName("plotToolBtn")
        self._btn_next_color.setFixedSize(64, 36)
        self._btn_next_color.clicked.connect(self._on_next_curve_color)
        row_curve.addWidget(self._btn_next_color)

        self._btn_reset_colors = QPushButton("重置")
        self._btn_reset_colors.setObjectName("plotToolBtn")
        self._btn_reset_colors.setFixedSize(54, 36)
        self._btn_reset_colors.clicked.connect(self._on_reset_curve_colors)
        row_curve.addWidget(self._btn_reset_colors)
        row_curve.addStretch()
        plot_tools_layout.addLayout(row_curve)

        row_axis = QHBoxLayout()
        row_axis.setSpacing(6)
        row_axis.addStretch()
        self._x_scroll_btn = QPushButton("X 跟随")
        self._x_scroll_btn.setObjectName("plotToolBtn")
        self._x_scroll_btn.setCheckable(True)
        self._x_scroll_btn.setChecked(True)
        self._x_scroll_btn.setFixedSize(86, 36)
        self._x_scroll_btn.toggled.connect(self._on_x_scroll_toggled)
        row_axis.addWidget(self._x_scroll_btn)

        self._y_auto_btn = QPushButton("Y 自动")
        self._y_auto_btn.setObjectName("yAutoBtn")
        self._y_auto_btn.setCheckable(True)
        self._y_auto_btn.setChecked(True)
        self._y_auto_btn.setFixedSize(80, 36)
        self._y_auto_btn.toggled.connect(self._on_y_auto_toggled)
        row_axis.addWidget(self._y_auto_btn)
        plot_tools_layout.addLayout(row_axis)
        sidebar_layout.addWidget(plot_tools_frame)
        self._refresh_curve_color_controls()

        debug_frame = QFrame()
        debug_frame.setObjectName("debugBar")
        install_card_shadow(debug_frame, blur_radius=18, y_offset=4, alpha=22)
        debug_layout = QVBoxLayout(debug_frame)
        self._scope_debug_layout = debug_layout
        debug_layout.setContentsMargins(12, 10, 12, 10)
        debug_layout.setSpacing(8)

        debug_top = QHBoxLayout()
        debug_top.setSpacing(8)
        debug_title = QLabel("调试控制")
        debug_title.setObjectName("barTitle")
        self._scope_debug_title = debug_title
        debug_top.addWidget(debug_title)

        self._target_state_label = QLabel("目标：--")
        self._target_state_label.setObjectName("debugState")
        self._target_state_label.setMinimumWidth(112)
        debug_top.addWidget(self._target_state_label)

        self._scope_rate_label = QLabel("实际：-- Hz")
        self._scope_rate_label.setObjectName("statusRate")
        self._scope_rate_label.setMinimumWidth(124)
        self._scope_rate_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        debug_top.addWidget(self._scope_rate_label)
        debug_top.addStretch()

        self._btn_halt_target = QPushButton("暂停")
        self._btn_halt_target.setObjectName("debugBtn")
        self._btn_halt_target.setFixedSize(72, 38)
        self._btn_halt_target.clicked.connect(self._on_halt_target)
        debug_top.addWidget(self._btn_halt_target)

        self._btn_resume_target = QPushButton("运行")
        self._btn_resume_target.setObjectName("debugBtn")
        self._btn_resume_target.setFixedSize(72, 38)
        self._btn_resume_target.clicked.connect(self._on_resume_target)
        debug_top.addWidget(self._btn_resume_target)

        debug_bottom = QHBoxLayout()
        debug_bottom.setSpacing(8)
        self._selected_value_label = QLabel("已选：--")
        self._selected_value_label.setObjectName("statusText")
        self._selected_value_label.setMinimumWidth(180)
        debug_bottom.addWidget(self._selected_value_label, stretch=1)

        self._btn_write_value = QPushButton("写入")
        self._btn_write_value.setObjectName("debugPrimaryBtn")
        self._btn_write_value.setToolTip("临时写入")
        self._btn_write_value.setFixedSize(108, 38)
        self._btn_write_value.clicked.connect(self._on_write_value)
        debug_bottom.addWidget(self._btn_write_value)

        self._btn_restore_value = QPushButton("恢复")
        self._btn_restore_value.setObjectName("debugBtn")
        self._btn_restore_value.setFixedSize(84, 38)
        self._btn_restore_value.clicked.connect(self._on_restore_value)
        debug_bottom.addWidget(self._btn_restore_value)

        debug_layout.addLayout(debug_top)
        debug_layout.addLayout(debug_bottom)
        sidebar_layout.addWidget(debug_frame)
        self._rate_label = QLabel("采样率")
        ctrl = QFrame()
        ctrl.setObjectName("controlBar")
        install_card_shadow(ctrl, blur_radius=18, y_offset=4, alpha=24)
        ctrl_layout = QVBoxLayout(ctrl)
        self._scope_ctrl_layout = ctrl_layout
        self._ctrl_layout = ctrl_layout
        ctrl_layout.setContentsMargins(12, 10, 12, 10)
        ctrl_layout.setSpacing(8)

        ctrl_top = QHBoxLayout()
        self._ctrl_top_layout = ctrl_top
        ctrl_top.setSpacing(8)

        rate_group = QHBoxLayout()
        rate_group.setSpacing(6)
        self._rate_label = QLabel("采样率")
        rate_group.addWidget(self._rate_label)
        self._rate_combo = PclComboBox()
        self._time_window_label = QLabel("窗口：")
        self._rate_combo.addItem("1 Hz", 1)
        self._rate_combo.addItem("10 Hz", 10)
        self._rate_combo.addItem("50 Hz", 50)
        self._rate_combo.addItem("100 Hz", 100)
        self._rate_combo.addItem("200 Hz", 200)
        self._rate_combo.addItem("500 Hz", 500)
        self._rate_combo.addItem("1000 Hz", 1000)
        self._rate_combo.addItem("最大", 0)
        self._rate_combo.setCurrentIndex(3)
        self._polish_combo_popup(self._rate_combo)
        self._rate_combo.currentIndexChanged.connect(self._on_rate_combo_changed)
        rate_group.addWidget(self._rate_combo)
        ctrl_top.addLayout(rate_group)

        ctrl_top.addSpacing(12)
        self._time_window_label = QLabel("窗口：")
        ctrl_top.addWidget(self._time_window_label)
        self._time_window_spin = QSpinBox()
        self._time_window_spin.setRange(1, 120)
        self._time_window_spin.setSuffix(" s")
        self._time_window_spin.setFixedWidth(72)
        self._time_window_spin.setValue(TIME_WINDOW_DEFAULT)
        ctrl_top.addWidget(self._time_window_spin)
        ctrl_top.addStretch()
        ctrl_layout.addLayout(ctrl_top)

        ctrl_bottom = QHBoxLayout()
        self._ctrl_bottom_layout = ctrl_bottom
        ctrl_bottom.setSpacing(6)
        self._time_window_presets: list[QPushButton] = []
        for tw in [5, 10, 30, 60]:
            btn = QPushButton(f"{tw}s")
            btn.setObjectName("presetBtn")
            btn.setFixedWidth(34)
            btn.clicked.connect(lambda checked, r=tw: self._time_window_spin.setValue(r))
            ctrl_bottom.addWidget(btn)
            self._time_window_presets.append(btn)
        ctrl_bottom.addStretch()

        self._btn_start = QPushButton("开始")
        self._btn_start.setObjectName("startBtn")
        self._btn_start.clicked.connect(self._on_start_stop)
        ctrl_bottom.addWidget(self._btn_start)

        self._btn_snapshot = QPushButton("导出 CSV")
        self._btn_snapshot.setObjectName("exportBtn")
        self._btn_snapshot.clicked.connect(self._on_export)
        ctrl_bottom.addWidget(self._btn_snapshot)

        ctrl_layout.addLayout(ctrl_bottom)
        self._bind_button_motion(self._btn_start, self._btn_snapshot)
        sidebar_layout.addWidget(ctrl)
        sidebar_layout.addStretch(1)

        sidebar_scroll.setWidget(sidebar_host)

        plot_frame = QFrame()
        plot_frame.setObjectName("plotPanel")
        install_card_shadow(plot_frame, blur_radius=20, y_offset=5, alpha=24)
        plot_frame.setMinimumWidth(480)
        plot_layout = QVBoxLayout(plot_frame)
        plot_layout.setContentsMargins(8, 8, 8, 8)
        plot_layout.setSpacing(7)

        plot_header = QHBoxLayout()
        plot_header.setSpacing(8)
        title = QLabel("示波器")
        title.setObjectName("sectionTitle")
        plot_header.addWidget(title)
        plot_header.addSpacing(8)
        view_label = QLabel("视图")
        view_label.setObjectName("barTitle")
        plot_header.addWidget(view_label)

        self._scope_view_group = QButtonGroup(self)
        self._scope_view_buttons: list[QPushButton] = []
        for count in (1, 2, 3):
            btn = QPushButton(str(count))
            btn.setObjectName("segmentButton")
            btn.setCheckable(True)
            btn.setFixedWidth(34)
            btn.clicked.connect(lambda checked=False, c=count: self._set_scope_pane_count(c))
            self._scope_view_group.addButton(btn, count)
            self._scope_view_buttons.append(btn)
            plot_header.addWidget(btn)

        plot_header.addSpacing(10)
        self._btn_scope_sidebar = QPushButton("设置")
        self._btn_scope_sidebar.setObjectName("plotToolBtn")
        self._btn_scope_sidebar.setCheckable(True)
        self._btn_scope_sidebar.setChecked(True)
        self._btn_scope_sidebar.setFixedSize(84, 32)
        self._btn_scope_sidebar.clicked.connect(self._toggle_scope_sidebar)
        plot_header.addWidget(self._btn_scope_sidebar)
        plot_header.addStretch()
        plot_layout.addLayout(plot_header)

        self._scope_plot_area = QWidget()
        self._scope_plot_area.setObjectName("scopePlotArea")
        self._scope_plot_layout = QVBoxLayout(self._scope_plot_area)
        self._scope_plot_layout.setContentsMargins(0, 0, 0, 0)
        self._scope_plot_layout.setSpacing(0)
        self._scope_pane_splitter = QSplitter(Qt.Horizontal)
        self._scope_pane_splitter.setObjectName("scopePaneSplitter")
        self._scope_pane_splitter.setChildrenCollapsible(False)
        self._scope_pane_splitter.setHandleWidth(4)
        self._scope_pane_splitter.setOpaqueResize(False)
        self._scope_pane_splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._scope_pane_splitter.splitterMoved.connect(self._on_scope_pane_splitter_moved)
        self._scope_right_splitter = QSplitter(Qt.Vertical)
        self._scope_right_splitter.setObjectName("scopePaneSplitter")
        self._scope_right_splitter.setChildrenCollapsible(False)
        self._scope_right_splitter.setHandleWidth(4)
        self._scope_right_splitter.setOpaqueResize(False)
        self._scope_right_splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._scope_right_splitter.splitterMoved.connect(self._on_scope_pane_splitter_moved)
        self._scope_plot_layout.addWidget(self._scope_pane_splitter, stretch=1)
        self._scope_panes = [
            ScopePane(0, "窗格 1", "#1d4ed8"),
            ScopePane(1, "窗格 2", "#0ea5a5"),
            ScopePane(2, "窗格 3", "#f97316"),
        ]
        for pane in self._scope_panes:
            install_card_shadow(pane.frame, blur_radius=16, y_offset=3, alpha=18)
            pane.plot.getViewBox().sigRangeChangedManually.connect(self._on_user_interact)
        self._plot = self._scope_panes[0].plot
        self._auto_scroll = True
        plot_layout.addWidget(self._scope_plot_area, stretch=1)

        self._scope_splitter.addWidget(sidebar_scroll)
        self._scope_splitter.addWidget(plot_frame)
        self._scope_splitter.setStretchFactor(0, 0)
        self._scope_splitter.setStretchFactor(1, 1)
        QTimer.singleShot(0, self._apply_initial_scope_layout)
        self._set_scope_pane_count(self._scope_pane_count, save=False)
        self._toggle_scope_sidebar(self._scope_sidebar_visible, save=False)

        self._tab_debug_workbench = DebugWorkbenchTab()
        self._tab_debug_workbench.summaryChanged.connect(self._refresh_hero)
        self._tab_debug_workbench.summaryChanged.connect(self._sync_debug_command_preview)
        self._setup_debug_workbench_connections()
        self._register_workspace_page("debug_sources", "源码", self._tab_debug_workbench, domain="debug")

        self._tab_serial = SerialTab()
        self._register_workspace_page("serial", "串口收发", self._tab_serial, domain="serial")
        self._setup_serial_connections()

        self._refresh_hero()
        self._refresh_nav_buttons()
        self._bind_button_motion(*self.findChildren(QPushButton))
        self._refresh_debug_buttons()

    # ================================================================
    #  Debug Workbench
    # ================================================================

    def _setup_debug_workbench_connections(self):
        self._tab_debug_workbench.debugActionRequested.connect(self._on_debug_workbench_action)
        self._tab_debug_workbench.set_debug_controls_ready(True)
        self._tab_debug_workbench.set_backend_diagnostics(self._debug_workbench_idle_diagnostics())
        self._sync_debug_command_preview()

    def _on_debug_workbench_action(self, action_key: str):
        if action_key == "discover":
            self._discover_keil_for_debug_workbench()
            return
        if action_key == "attach":
            self._connect_keil_read_only_for_debug_workbench()
            return
        if hasattr(self, "_sb_label"):
            self._sb_label.setText("该调试动作尚未接入后端。")

    def _discover_keil_for_debug_workbench(self):
        if not hasattr(self, "_tab_debug_workbench"):
            return
        tab = self._tab_debug_workbench
        previous = tab.debug_status
        tab.set_debug_controls_ready(False)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText("正在发现 Keil/UVSOCK...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        diagnostics = []
        try:
            snapshot = self._debug_backend.discover(
                project_path=previous.project_path,
                target_name=tab.debug_status.target_name,
                previous_status=previous,
            )
            status = snapshot.status
            diagnostics = snapshot.diagnostic_rows()
            self._debug_remote_breakpoint_snapshot = snapshot.remote_breakpoint_snapshot
            self._debug_backend_snapshot_record = snapshot.to_record()
        except Exception as exc:
            message = f"Keil 预检失败：{exc}"
            status = make_debug_status(
                state=DebugRuntimeState.ERROR,
                backend="keil",
                detail=message,
                project_path=previous.project_path,
                target_name=previous.target_name,
                error=message,
            )
            diagnostics = self._debug_workbench_error_diagnostics(message)
        finally:
            QApplication.restoreOverrideCursor()
        tab.set_debug_status(status, controls_ready=True)
        tab.set_backend_diagnostics(diagnostics)
        self._sync_debug_command_preview()
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(status.detail)
        self._refresh_hero()

    def _connect_keil_read_only_for_debug_workbench(self):
        if not hasattr(self, "_tab_debug_workbench"):
            return
        tab = self._tab_debug_workbench
        previous = tab.debug_status
        tab.set_debug_controls_ready(False)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText("正在读取 Keil 只读会话快照...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        diagnostics = []
        try:
            snapshot = self._debug_backend.read_only_session_snapshot(
                project_path=previous.project_path,
                target_name=tab.debug_status.target_name,
                previous_status=previous,
                attempt_connection=True,
                query_status=True,
            )
            status = snapshot.status
            diagnostics = snapshot.diagnostic_rows()
            self._debug_remote_breakpoint_snapshot = snapshot.remote_breakpoint_snapshot
            self._debug_backend_snapshot_record = snapshot.to_record()
        except Exception as exc:
            message = f"Keil 只读连接失败：{exc}"
            status = make_debug_status(
                state=DebugRuntimeState.ERROR,
                backend="keil",
                detail=message,
                project_path=previous.project_path,
                target_name=previous.target_name,
                error=message,
            )
            diagnostics = self._debug_workbench_error_diagnostics(message)
        finally:
            QApplication.restoreOverrideCursor()
        tab.set_debug_status(status, controls_ready=True)
        tab.set_backend_diagnostics(diagnostics)
        self._sync_debug_command_preview()
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(status.detail)
        self._refresh_hero()

    def _sync_debug_command_preview(self):
        if not hasattr(self, "_tab_debug_workbench"):
            return
        tab = self._tab_debug_workbench
        status = tab.debug_status
        transactions = build_keil_debug_transactions(
            status,
            debug_command_plans_for_status(status),
            port=self._debug_uvsock_port,
            project_path=status.project_path,
            target_name=status.target_name,
            breakpoints=tab.local_breakpoints(),
            source_paths=tab.local_source_paths(),
            remote_breakpoint_snapshot=getattr(self, "_debug_remote_breakpoint_snapshot", None),
            backend_snapshot=getattr(self, "_debug_backend_snapshot_record", None),
            execution_gate=False,
        )
        tab.set_command_transactions(transactions)
        focused = self._focused_debug_transaction(transactions)
        if focused is not None:
            self._debug_command_history.record(focused, event="previewed", source="ui_sync")
        tab.set_command_history_entries(self._debug_command_history.recent(limit=5))

    def set_debug_remote_breakpoint_snapshot(self, snapshot):
        self._debug_remote_breakpoint_snapshot = snapshot
        self._sync_debug_command_preview()

    def _focused_debug_transaction(self, transactions):
        priority = (
            "attach",
            "halt",
            "run",
            "step",
            "sync_breakpoints",
            "write_variables",
            "disconnect",
            "discover",
        )
        ready = {transaction.kind.value for transaction in transactions if transaction.preconditions_met}
        for key in priority:
            if key in ready:
                return transaction_by_key(transactions, key)
        return transactions[0] if transactions else None

    def _debug_workbench_idle_diagnostics(self) -> tuple[tuple[str, str], ...]:
        return (
            ("Keil 根目录", str(self._keil_root)),
            ("UVSOCK 端口", str(self._debug_uvsock_port)),
            ("状态", "等待发现 Keil"),
        )

    def _debug_workbench_error_diagnostics(self, message: str) -> tuple[tuple[str, str], ...]:
        return (
            ("Keil 根目录", str(self._keil_root)),
            ("UVSOCK 端口", str(self._debug_uvsock_port)),
            ("错误", message),
        )

    # ================================================================
    #  Serial Assistant
    # ================================================================

    def _setup_serial_connections(self):
        self._tab_serial.set_ports_managed(True)
        self._tab_serial.connectRequested.connect(self._serial_controller.connect_serial)
        self._tab_serial.disconnectRequested.connect(self._serial_controller.disconnect_serial)
        self._tab_serial.refreshPortsRequested.connect(self._serial_controller.refresh_ports)
        self._tab_serial.clearRequested.connect(self._serial_controller.clear)
        self._tab_serial.sendRequested.connect(self._serial_controller.send)

        self._serial_controller.portsChanged.connect(self._tab_serial.set_ports)
        self._serial_controller.logReceived.connect(self._tab_serial.append_log)
        self._serial_controller.connectedChanged.connect(self._tab_serial.set_connected)
        self._serial_controller.connectedChanged.connect(lambda _connected: self._refresh_hero())
        self._serial_controller.busyChanged.connect(self._set_serial_busy)
        self._serial_controller.sendEnabledChanged.connect(self._tab_serial.send_button.setEnabled)
        self._serial_controller.sendAccepted.connect(self._tab_serial.send_edit.clear)
        self._serial_controller.scopeDataChanged.connect(self._tab_serial.set_scope_data)
        self._serial_controller.refresh_ports()

    def _refresh_serial_ports(self):
        self._serial_controller.refresh_ports()

    def _on_serial_connect(self, options):
        self._serial_controller.connect_serial(options)

    def _finish_serial_connect(self, ok: bool, config: object, error: str):
        if ok:
            self._tab_serial.set_connected(True)
            self._tab_serial.append_log(
                f"已打开 {config.port} @ {config.baudrate}, {config.protocol}",
                "rx",
            )
        else:
            self._tab_serial.set_connected(False)
            self._tab_serial.append_log(f"连接失败：{error}", "rx")
        self._refresh_hero()

    def _on_serial_disconnect(self, sync: bool = False):
        self._serial_controller.disconnect_serial(sync=sync)

    def _finish_serial_disconnect(self, error: str):
        self._tab_serial.set_connected(False)
        if error:
            self._tab_serial.append_log(f"断开失败：{error}", "rx")
        self._tab_serial.append_log("串口已断开。", "rx")
        self._refresh_hero()

    def _on_serial_clear(self):
        self._serial_controller.clear()

    def _on_serial_send(self, payload, mode: str):
        self._serial_controller.send(payload, mode)

    def _finish_serial_send(self, ok: bool, display: str, error: str):
        if ok:
            self._tab_serial.append_log(display, "tx")
            self._tab_serial.send_edit.clear()
            return
        self._tab_serial.append_log(f"发送失败：{error}", "tx")

    @staticmethod
    def _serial_payload_display(payload: object, mode: str) -> str:
        return serial_payload_display(payload, mode)

    @staticmethod
    def _escape_serial_log_text(text: str) -> str:
        return escape_serial_log_text(text)

    def _refresh_serial_tab(self):
        self._serial_controller.refresh_runtime()

    def _set_serial_busy(self, busy: bool, label: str = ""):
        if not hasattr(self, "_tab_serial"):
            return
        self._tab_serial.connect_button.setEnabled(not busy)
        if label:
            self._tab_serial.connect_button.setText(label)
        else:
            self._tab_serial.connect_button.setText(
                "断开" if self._tab_serial.is_connected else "连接"
            )

    def _start_serial_worker(self, target, name: str):
        self._serial_controller.start_worker(target, name)

    def _join_serial_workers(self, timeout: float = 0.6) -> bool:
        return self._serial_controller.join_workers(timeout)

    @staticmethod
    def _serial_protocol_key(label: str) -> str:
        return serial_protocol_key(label)

    def _restore_serial_config(self, cfg: dict):
        if not hasattr(self, "_tab_serial"):
            return
        port = str(cfg.get("serial_port", "") or "")
        if port:
            index = self._tab_serial.port_combo.findData(port)
            if index < 0:
                index = self._tab_serial.port_combo.findText(port)
            if index >= 0:
                self._tab_serial.port_combo.setCurrentIndex(index)
        baudrate = str(cfg.get("serial_baudrate", "") or "")
        if baudrate:
            index = self._tab_serial.baud_combo.findText(baudrate)
            if index >= 0:
                self._tab_serial.baud_combo.setCurrentIndex(index)
        protocol = str(cfg.get("serial_protocol", "") or "")
        if protocol:
            index = self._tab_serial.protocol_combo.findText(protocol)
            if index >= 0:
                self._tab_serial.protocol_combo.setCurrentIndex(index)

    # ================================================================
    #  Import & Tree Population
    # ================================================================

    def _on_import_elf(self):
        path = self._choose_open_file(
            "打开 ELF/AXF",
            "ELF/AXF (*.elf *.axf *.out);;全部 (*.*)",
        )
        if path:
            self._load_elf_path(Path(path))

    def _on_load_recent_elf(self):
        if not self._recent_elf_path:
            return
        if not self._recent_elf_path.exists():
            self._show_info("最近 ELF", "最近的 ELF 文件已不存在。")
            self._recent_elf_path = None
            self._refresh_recent_elf_button()
            return
        self._load_elf_path(self._recent_elf_path)

    def _load_variables(self):
        dialog = self._show_parse_dialog(self._elf_path)
        self._set_parse_controls_enabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        error = None
        try:
            dialog.set_message(f"正在读取 {self._elf_path.name} 的调试信息…")
            QApplication.processEvents()
            elf = ELFParser(self._elf_path)
            elf.open()
            dwarf_db = parse_debug_info(self._elf_path)

            # Try to find a linker map file for accurate source file mapping
            dialog.set_message("正在匹配变量来源和结构体布局…")
            QApplication.processEvents()
            symbol_to_file = {}
            map_path = self._find_map_file()
            if map_path:
                try:
                    symbol_to_file = parse_map_file(map_path)
                except Exception:
                    pass

            dialog.set_message("正在生成变量列表…")
            QApplication.processEvents()
            inventory = VariableInventory(elf, dwarf_db, symbol_to_file)
            self._variables = inventory.generate()
            elf.close()
        except Exception as e:
            error = e
        finally:
            QApplication.restoreOverrideCursor()
            self._set_parse_controls_enabled(True)
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()
        if error is not None:
            self._show_warning("错误", f"ELF 解析失败：{error}")
            return
        self._populate_tree()

    def _find_map_file(self) -> Optional[Path]:
        """Look for a .map file near the ELF."""
        elf_dir = self._elf_path.parent
        elf_stem = self._elf_path.stem

        # Same directory, same basename
        candidates = [
            elf_dir / f"{elf_stem}.map",
            elf_dir.parent / f"{elf_stem}.map",
            elf_dir.parent.parent / f"{elf_stem}.map",
        ]
        # Also check common build directories relative to elf location
        for up in range(4):
            prefix = Path(*([".."] * (up + 1)))
            candidates.append(elf_dir / prefix / "build" / "Debug" / f"{elf_stem}.map")
            candidates.append(elf_dir / prefix / "build" / f"{elf_stem}.map")
            candidates.append(elf_dir / prefix / f"{elf_stem}.map")

        for c in candidates:
            try:
                resolved = c.resolve()
                if resolved.exists():
                    return resolved
            except (OSError, ValueError):
                pass
        return None

    def _populate_tree(self):
        text = self._filter_edit.text().lower()
        self._tree.clear()
        self._registry.clear()

        # Filter variables
        display = []
        for v in self._variables:
            if text and text not in v.name.lower():
                concrete = resolve_type(v.type_info)
                if isinstance(concrete, StructType):
                    if not self._any_member_matches(text, concrete, v.name):
                        continue
                else:
                    continue
            display.append(v)

        # Group by source file
        from collections import defaultdict
        file_groups: dict[str, list] = defaultdict(list)
        for v in display:
            fn = v.file_name or ""
            file_groups[fn].append(v)

        group_keys = sorted([k for k in file_groups if k]) + ([""] if "" in file_groups else [])

        self._tree.blockSignals(True)
        for fname in group_keys:
            vars_in_file = sorted(file_groups[fname], key=lambda v: (v.address, v.name))
            if fname:
                short_name = Path(fname).name
                folder = QTreeWidgetItem(self._tree)
                folder.setText(0, short_name)
                folder.setText(1, f"{len(vars_in_file)} 个变量")
                folder.setText(2, fname)
                folder.setToolTip(0, "点击展开或折叠")
                folder.setFlags(folder.flags() & ~Qt.ItemIsSelectable)
                font = folder.font(0)
                font.setBold(True)
                folder.setFont(0, font)
                folder.setForeground(0, QColor("#0b5bcb"))
                for v in vars_in_file:
                    self._add_variable_item(v, parent_item=folder)
            else:
                for v in vars_in_file:
                    self._add_variable_item(v)

        self._tree.blockSignals(False)
        self._tree.collapseAll()

        # 恢复上次选中的变量
        self._monitored = set()
        saved_vars = self._saved_monitored_variables
        if saved_vars:
            self._restore_selected_items(self._tree.invisibleRootItem(), saved_vars)
        self._update_selected_list()
        self._refresh_hero()
        self._sync_value_table_placeholders()

    def _any_member_matches(self, text: str, st: StructType, parent_path: str) -> bool:
        for m in st.members:
            full = f"{parent_path}.{m.name}"
            if text in m.name.lower() or text in full.lower():
                return True
            inner = resolve_type(m.type_info)
            if isinstance(inner, StructType):
                if self._any_member_matches(text, inner, full):
                    return True
        return False

    def _add_variable_item(self, v: Variable, depth: int = 0,
                           parent_item: Optional[QTreeWidgetItem] = None,
                           path_prefix: str = ""):
        concrete = resolve_type(v.type_info)
        is_struct = isinstance(concrete, StructType)
        full_path = f"{path_prefix}.{v.name}" if path_prefix else v.name

        if is_struct and depth < MAX_STRUCT_DEPTH and concrete.members:
            item = QTreeWidgetItem() if parent_item is None else QTreeWidgetItem(parent_item)
            item.setText(0, v.name)
            item.setText(1, f"0x{v.address:08X}")
            item.setText(2, format_type(v.type_info))
            item.setData(0, ROLE_PATH, full_path)
            item.setData(0, ROLE_ADDR, v.address)
            item.setData(0, ROLE_TYPE, v.type_info)
            item.setFlags(item.flags() | Qt.ItemIsSelectable)

            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)

            if full_path in self._monitored:
                item.setSelected(True)

            if parent_item is None:
                self._tree.addTopLevelItem(item)

            sorted_members = sorted(concrete.members, key=lambda m: m.offset)

            for member in sorted_members:
                member_addr = v.address + member.offset
                member_concrete = resolve_type(member.type_info)
                member_is_struct = isinstance(member_concrete, StructType)

                if member_is_struct and depth + 1 < MAX_STRUCT_DEPTH and member_concrete.members:
                    display_ti = member.type_info if isinstance(member.type_info, TypedefType) else member_concrete
                    pseudo = Variable(
                        name=member.name, address=member_addr,
                        size=member_concrete.size, type_info=display_ti,
                    )
                    self._add_variable_item(pseudo, depth + 1, item, full_path)
                else:
                    member_path = f"{full_path}.{member.name}"
                    child = QTreeWidgetItem(item)
                    child.setText(0, member.name)
                    child.setText(1, f"0x{member_addr:08X}")

                    type_str = format_type(member.type_info)
                    if member.bit_size > 0:
                        type_str += f"  [:{member.bit_size}]"
                    child.setText(2, type_str)

                    if member.bit_size > 0:
                        child.setFlags(Qt.ItemIsEnabled)
                        child.setForeground(2, QColor("#8888a0"))
                    else:
                        child.setFlags(child.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                        if member_path in self._monitored:
                            child.setSelected(True)

                    child.setData(0, ROLE_PATH, member_path)
                    child.setData(0, ROLE_ADDR, member_addr)
                    child.setData(0, ROLE_TYPE, member.type_info)

                    self._registry[member_path] = (member_addr, member.type_info)

        else:
            item = QTreeWidgetItem() if parent_item is None else QTreeWidgetItem(parent_item)
            item.setText(0, v.name)
            item.setText(1, f"0x{v.address:08X}")
            item.setText(2, format_type(v.type_info))
            item.setData(0, ROLE_PATH, full_path)
            item.setData(0, ROLE_ADDR, v.address)
            item.setData(0, ROLE_TYPE, v.type_info)
            item.setFlags(item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled)

            if full_path in self._monitored:
                item.setSelected(True)

            self._registry[full_path] = (v.address, v.type_info)

            if parent_item is None:
                self._tree.addTopLevelItem(item)

    # ================================================================
    #  Multi-selection via Qt selection model
    # ================================================================

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Single-click file groups to reveal their variables."""
        if item.childCount() <= 0:
            return
        if item.data(0, ROLE_PATH) is not None:
            return
        item.setExpanded(not item.isExpanded())

    def _on_selection_changed(self):
        """Sync Qt selection state with self._monitored."""
        selected_paths = set()
        for item in self._tree.selectedItems():
            path = item.data(0, ROLE_PATH)
            if path is not None:
                selected_paths.add(path)

        self._monitored = selected_paths
        self._refresh_hero()
        self._sync_value_table_placeholders()
        self._update_selected_list()
        if not self._collector.is_running:
            self._sync_plot_curve_state(force=True)
            self._update_plot(force=True)
        self._idle_read()

    def _update_selected_list(self):
        """Update tab title with selected count."""
        count = len(self._monitored)
        visible = len({name for name, _, _ in self._visible_monitor_items()})
        if count == 0:
            title = "示波器"
        elif visible == count:
            title = f"示波器 ({count})"
        else:
            title = f"示波器 ({visible}/{count})"
        scope_index = self._workspace_page_index("scope", -1)
        if scope_index >= 0:
            self._tabs.setTabText(scope_index, title)
        self._refresh_nav_buttons()
        self._refresh_curve_color_controls()

    def _selected_monitor_items(self) -> list[tuple[str, int, TypeInfo]]:
        items = []
        for path in sorted(self._monitored):
            info = self._registry.get(path)
            if info is not None:
                addr, ti = info
                items.append((path, addr, ti))
        return items

    def _scope_source_items(self) -> list[tuple[str, int, TypeInfo]]:
        return self._monitor_list if self._collector.is_running and self._monitor_list else self._selected_monitor_items()

    def _scope_pane_items(self) -> list[list[tuple[str, int, TypeInfo]]]:
        pane_count = max(1, min(3, int(getattr(self, "_scope_pane_count", 1) or 1)))
        panes: list[list[tuple[str, int, TypeInfo]]] = [[] for _ in range(pane_count)]
        items = self._scope_source_items()
        table = getattr(self, "_value_table", None)
        roles = (ROLE_DISPLAYED, ROLE_SCOPE_2, ROLE_SCOPE_3)

        for row, (name, addr, ti) in enumerate(items):
            assigned = False
            has_assignment_cells = False
            if table is not None:
                for pane_index in range(pane_count):
                    item = table.item(row, 4 + pane_index)
                    has_assignment_cells = has_assignment_cells or item is not None
                    if item is not None and bool(item.data(roles[pane_index])):
                        panes[pane_index].append((name, addr, ti))
                        assigned = True
            if not has_assignment_cells and not assigned and name not in self._hidden_displayed_variables:
                panes[0].append((name, addr, ti))
        return panes

    def _visible_monitor_items(self) -> list[tuple[str, int, TypeInfo]]:
        seen: set[str] = set()
        visible: list[tuple[str, int, TypeInfo]] = []
        for pane_items in self._scope_pane_items():
            for item in pane_items:
                if item[0] in seen:
                    continue
                seen.add(item[0])
                visible.append(item)
        return visible

    def _curve_names(self) -> list[str]:
        return [name for name, _, _ in self._visible_monitor_items()]

    def _scope_assignment_map(self) -> dict[str, tuple[bool, bool, bool]]:
        table = getattr(self, "_value_table", None)
        result: dict[str, tuple[bool, bool, bool]] = dict(getattr(self, "_saved_scope_assignments", {}))
        if table is None:
            return result
        roles = (ROLE_DISPLAYED, ROLE_SCOPE_2, ROLE_SCOPE_3)
        for row in range(table.rowCount()):
            path_item = table.item(row, 0)
            if path_item is None:
                continue
            path = path_item.data(ROLE_PATH) or path_item.text()
            if not path:
                continue
            values = []
            for col, role in zip((4, 5, 6), roles):
                item = table.item(row, col)
                values.append(bool(item.data(role)) if item is not None else False)
            result[str(path)] = tuple(values)
        self._saved_scope_assignments.update(result)
        return result

    def _sync_value_table_placeholders(self):
        """List selected variables immediately, before live reads arrive."""
        if not hasattr(self, "_value_table") or self._collector.is_running:
            return
        items = self._selected_monitor_items()
        table = self._value_table
        old_path = self._selected_value_path()
        assignments = self._scope_assignment_map()
        table.blockSignals(True)
        table.setRowCount(len(items))
        for row, (name, addr, ti) in enumerate(items):
            row_assignment = assignments.get(name, (name not in self._hidden_displayed_variables, False, False))
            name_item = table.item(row, 0)
            if name_item is None:
                name_item = QTableWidgetItem(name)
                table.setItem(row, 0, name_item)
            else:
                name_item.setText(name)
            name_item.setData(ROLE_PATH, name)

            value_item = table.item(row, 1)
            if value_item is None:
                value_item = QTableWidgetItem("--")
                value_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, 1, value_item)
            else:
                value_item.setText("--")

            addr_item = table.item(row, 2)
            addr_text = f"0x{addr:08X}"
            if addr_item is None:
                addr_item = QTableWidgetItem(addr_text)
                addr_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, 2, addr_item)
            else:
                addr_item.setText(addr_text)

            type_item = table.item(row, 3)
            type_text = format_type(ti)
            if type_item is None:
                type_item = QTableWidgetItem(type_text)
                table.setItem(row, 3, type_item)
            else:
                type_item.setText(type_text)

            for col, role in ((4, ROLE_DISPLAYED), (5, ROLE_SCOPE_2), (6, ROLE_SCOPE_3)):
                display_item = table.item(row, col)
                if display_item is None:
                    display_item = QTableWidgetItem()
                    display_item.setTextAlignment(Qt.AlignCenter)
                    display_item.setFlags(
                        (display_item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable) & ~Qt.ItemIsUserCheckable
                    )
                    table.setItem(row, col, display_item)
                display_item.setToolTip(f"显示在窗格 {col - 3}")
                display_item.setData(role, row_assignment[col - 4])
        table.blockSignals(False)
        self._restore_value_selection(old_path)
        self._refresh_debug_buttons()

    def _selected_value_path(self) -> Optional[str]:
        if not hasattr(self, "_value_table"):
            return None
        table = self._value_table
        row = table.currentRow()
        if row < 0 and table.rowCount() == 1:
            row = 0
        if row < 0:
            return None
        item = table.item(row, 0)
        if item is None:
            return None
        path = item.data(ROLE_PATH)
        return str(path) if path else item.text()

    def _restore_value_selection(self, path: Optional[str]):
        if not path or not hasattr(self, "_value_table"):
            return
        table = self._value_table
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and (item.data(ROLE_PATH) == path or item.text() == path):
                table.selectRow(row)
                return

    def _on_value_selection_changed(self):
        self._refresh_debug_buttons()

    def _on_value_table_cell_clicked(self, row: int, column: int):
        role_by_column = {4: ROLE_DISPLAYED, 5: ROLE_SCOPE_2, 6: ROLE_SCOPE_3}
        if column not in role_by_column:
            return
        item = self._value_table.item(row, column)
        if item is None:
            return
        role = role_by_column[column]
        item.setData(role, not bool(item.data(role)))

    def _on_value_table_item_changed(self, item: QTableWidgetItem):
        if item is None or item.column() not in (4, 5, 6):
            return
        if not hasattr(self, "_value_table"):
            return
        path_item = self._value_table.item(item.row(), 0)
        if path_item is None:
            return
        path = path_item.data(ROLE_PATH) or path_item.text()
        if not path:
            return
        path = str(path)
        assigned = False
        assignment_values = []
        for col, role in ((4, ROLE_DISPLAYED), (5, ROLE_SCOPE_2), (6, ROLE_SCOPE_3)):
            role_item = self._value_table.item(item.row(), col)
            value = bool(role_item.data(role)) if role_item is not None else False
            assignment_values.append(value)
            if value:
                assigned = True
        self._saved_scope_assignments[path] = tuple(assignment_values)
        if assigned:
            self._hidden_displayed_variables.discard(path)
        else:
            self._hidden_displayed_variables.add(path)
        self._update_selected_list()
        self._refresh_curve_color_controls()
        self._sync_plot_curve_state(force=True)
        self._update_plot(force=not self._collector.is_running)

    def _refresh_debug_state(self):
        if getattr(self, "_shutting_down", False) or not hasattr(self, "_target_state_label"):
            return
        if not self._backend.is_connected:
            self._target_is_halted = False
            self._target_state_value = "--"
            self._apply_target_state_label_text()
            return
        state = self._backend.target_state()
        self._target_state_value = state
        self._apply_target_state_label_text()
        self._target_is_halted = state.lower() == "halted"
        self._refresh_debug_buttons()

    def _apply_target_state_label_text(self):
        if not hasattr(self, "_target_state_label"):
            return
        state = getattr(self, "_target_state_value", "--") or "--"
        state_display = {
            "halted": "已暂停",
            "running": "运行中",
            "reset": "复位",
        }.get(state.lower(), state)
        compact = getattr(self, "_scope_layout_mode", "") in {"compact", "tight"}
        text = state_display if compact else f"目标：{state_display}"
        self._target_state_label.setText(text)
        self._target_state_label.setToolTip(f"pyOCD 目标状态：{state}" if state != "--" else "")
        if hasattr(self, "_scope_debug_title"):
            if compact and state != "--":
                self._scope_debug_title.setText(f"调试：{state_display}")
            else:
                self._scope_debug_title.setText("调试控制")

    def _refresh_debug_buttons(self):
        if not hasattr(self, "_btn_write_value"):
            return
        connected = self._backend.is_connected
        halted = bool(self._target_is_halted)
        selected = self._selected_value_path()
        has_variable = bool(selected and selected in self._registry)

        for attr in ("_btn_halt_target", "_btn_resume_target"):
            button = getattr(self, attr, None)
            if button is not None:
                button.setEnabled(connected)

        self._btn_write_value.setEnabled(connected and halted and has_variable)
        self._btn_restore_value.setEnabled(connected and halted and has_variable and selected in self._temporary_writes)

        if not selected:
            self._selected_value_label.setText("已选：--")
        elif selected in self._temporary_writes:
            self._selected_value_label.setText(f"已选：{self._short_value_name(selected)}（已修改）")
        else:
            self._selected_value_label.setText(f"已选：{self._short_value_name(selected)}")

    @staticmethod
    def _scope_sidebar_target_width(total_width: int) -> int:
        if total_width <= 0:
            return 320
        return max(320, min(420, int(total_width * 0.24)))

    @staticmethod
    def _scope_layout_mode_for_width(left_width: int) -> str:
        if left_width >= 620:
            return "wide"
        if left_width >= 500:
            return "cozy"
        if left_width >= 340:
            return "compact"
        if left_width >= 260:
            return "tight"
        return "mini"

    def _on_scope_splitter_moved(self, *_args):
        if self._scope_layout_syncing:
            return
        self._begin_scope_splitter_resize()
        self._scope_resize_settle_timer.start(120)

    def _on_scope_splitter_settled(self):
        try:
            if self._scope_layout_syncing:
                return
            self._remember_scope_pane_splitter_sizes()
            self._fit_scope_layout(sync_splitter=False)
        finally:
            self._end_resize_pause("scope_splitter")

    def _begin_scope_splitter_resize(self):
        self._begin_resize_pause("scope_splitter", suspend_scope=True)

    def _apply_initial_scope_layout(self):
        self._scope_resize_sync_timer.start(0)

    def _sync_scope_layout_to_window(self):
        self._fit_scope_layout(sync_splitter=True)

    def _fit_scope_layout(self, sync_splitter: bool = False):
        if not hasattr(self, "_scope_splitter"):
            return
        sizes = self._scope_splitter.sizes()
        total_width = self._scope_splitter.width() or sum(sizes) or 0
        current_left = sizes[0] if sizes else 0
        target_left = current_left
        if sync_splitter:
            target_left = self._scope_sidebar_target_width(total_width)
            mode = self._scope_layout_mode_for_width(target_left)
        else:
            mode = self._scope_layout_mode_for_width(current_left or self._scope_sidebar_target_width(total_width))
        mode_changed = mode != self._scope_layout_mode
        if not mode_changed and not sync_splitter:
            return
        self._scope_layout_mode = mode

        if hasattr(self, "_scope_sidebar_layout"):
            self._scope_sidebar_layout.setSpacing(10 if mode in {"wide", "cozy"} else 8 if mode == "compact" else 6)
        if hasattr(self, "_scope_table_layout"):
            self._scope_table_layout.setContentsMargins(
                12 if mode in {"wide", "cozy"} else 10 if mode == "compact" else 8,
                9 if mode in {"wide", "cozy"} else 8 if mode == "compact" else 6,
                12 if mode in {"wide", "cozy"} else 10 if mode == "compact" else 8,
                10 if mode in {"wide", "cozy"} else 8 if mode == "compact" else 6,
            )
        if hasattr(self, "_scope_plot_tools_layout"):
            self._scope_plot_tools_layout.setContentsMargins(
                12 if mode in {"wide", "cozy"} else 10 if mode == "compact" else 8,
                10 if mode in {"wide", "cozy"} else 8 if mode == "compact" else 6,
                12 if mode in {"wide", "cozy"} else 10 if mode == "compact" else 8,
                10 if mode in {"wide", "cozy"} else 8 if mode == "compact" else 6,
            )
        if hasattr(self, "_scope_debug_layout"):
            self._scope_debug_layout.setContentsMargins(
                12 if mode in {"wide", "cozy"} else 10 if mode == "compact" else 8,
                10 if mode in {"wide", "cozy"} else 8 if mode == "compact" else 6,
                12 if mode in {"wide", "cozy"} else 10 if mode == "compact" else 8,
                10 if mode in {"wide", "cozy"} else 8 if mode == "compact" else 6,
            )
        if hasattr(self, "_scope_ctrl_layout"):
            self._scope_ctrl_layout.setContentsMargins(
                12 if mode in {"wide", "cozy"} else 10 if mode == "compact" else 8,
                10 if mode in {"wide", "cozy"} else 8 if mode == "compact" else 6,
                12 if mode in {"wide", "cozy"} else 10 if mode == "compact" else 8,
                10 if mode in {"wide", "cozy"} else 8 if mode == "compact" else 6,
            )

        compact = mode in {"compact", "tight", "mini"}
        tiny = mode in {"tight", "mini"}

        if hasattr(self, "_value_table"):
            header_item = self._value_table.horizontalHeaderItem(0)
            if header_item is not None:
                header_item.setText("变量" if compact else "变量")
            self._value_table.setColumnHidden(2, compact)
            self._value_table.setColumnHidden(3, compact)
            for column in (4, 5, 6):
                self._value_table.setColumnHidden(column, column - 3 > self._scope_pane_count)
                self._value_table.setColumnWidth(column, 34 if tiny else 38 if compact else 42)

        if hasattr(self, "_curve_color_combo"):
            self._curve_color_combo.setFixedWidth(
                200 if mode == "wide" else 180 if mode == "cozy"
                else 150 if mode == "compact" else 128 if mode == "tight" else 112
            )
        if hasattr(self, "_btn_curve_color"):
            self._btn_curve_color.setFixedSize(
                66 if mode == "wide" else 60 if mode == "cozy"
                else 56 if mode == "compact" else 50 if mode == "tight" else 46, 36
            )
        if hasattr(self, "_btn_next_color"):
            self._btn_next_color.setVisible(mode in {"wide", "cozy"})
        if hasattr(self, "_btn_reset_colors"):
            self._btn_reset_colors.setVisible(mode in {"wide", "cozy"})
        if hasattr(self, "_x_scroll_btn"):
            self._x_scroll_btn.setFixedSize(
                86 if mode == "wide" else 82 if mode == "cozy"
                else 82 if mode == "compact" else 78 if mode == "tight" else 76, 36
            )
        if hasattr(self, "_y_auto_btn"):
            self._y_auto_btn.setFixedSize(
                86 if mode == "wide" else 84 if mode == "cozy"
                else 84 if mode == "compact" else 82 if mode == "tight" else 80, 36
            )

        if hasattr(self, "_target_state_label"):
            self._target_state_label.setVisible(mode in {"wide", "cozy"})
            self._target_state_label.setMinimumWidth(
                112 if mode == "wide" else 108 if mode == "cozy"
                else 88 if mode == "compact" else 82
            )
            self._apply_target_state_label_text()
        if hasattr(self, "_scope_rate_label"):
            self._scope_rate_label.setVisible(mode in {"wide", "cozy"})
            self._scope_rate_label.setMinimumWidth(
                124 if mode == "wide" else 116 if mode == "cozy"
                else 108 if mode == "compact" else 96
            )
        if hasattr(self, "_selected_value_label"):
            self._selected_value_label.setVisible(not tiny)
            self._selected_value_label.setMinimumWidth(
                200 if mode == "wide" else 184 if mode == "cozy"
                else 164 if mode == "compact" else 144
            )
        if hasattr(self, "_btn_halt_target"):
            self._btn_halt_target.setFixedSize(
                72 if mode == "wide" else 68 if mode == "cozy"
                else 64 if mode == "compact" else 58 if mode == "tight" else 54, 36
            )
        if hasattr(self, "_btn_resume_target"):
            self._btn_resume_target.setFixedSize(
                72 if mode == "wide" else 68 if mode == "cozy"
                else 64 if mode == "compact" else 58 if mode == "tight" else 54, 36
            )
        if hasattr(self, "_btn_write_value"):
            self._btn_write_value.setFixedSize(
                108 if mode == "wide" else 100 if mode == "cozy"
                else 92 if mode == "compact" else 84 if mode == "tight" else 78, 38
            )
        if hasattr(self, "_btn_restore_value"):
            self._btn_restore_value.setFixedSize(
                84 if mode == "wide" else 80 if mode == "cozy"
                else 76 if mode == "compact" else 70 if mode == "tight" else 66, 38
            )

        if hasattr(self, "_rate_combo"):
            self._rate_combo.setFixedWidth(
                112 if mode == "wide" else 104 if mode == "cozy"
                else 96 if mode == "compact" else 88 if mode == "tight" else 80
            )
        if hasattr(self, "_rate_label"):
            self._rate_label.setVisible(mode in {"wide", "cozy", "compact"})
        if hasattr(self, "_time_window_label"):
            self._time_window_label.setVisible(mode in {"wide", "cozy", "compact"})
        if hasattr(self, "_time_window_presets"):
            presets_visible = mode in {"wide", "cozy"}
            for btn in self._time_window_presets:
                btn.setVisible(presets_visible)
        if hasattr(self, "_time_window_spin"):
            self._time_window_spin.setFixedWidth(
                80 if mode == "wide" else 76 if mode == "cozy"
                else 68 if mode == "compact" else 62 if mode == "tight" else 56
            )
        if hasattr(self, "_ctrl_layout"):
            self._ctrl_layout.setSpacing(8 if mode in {"wide", "cozy"} else 6 if mode == "compact" else 4)
        if hasattr(self, "_ctrl_top_layout"):
            self._ctrl_top_layout.setSpacing(8 if mode in {"wide", "cozy"} else 6 if mode == "compact" else 4)
        if hasattr(self, "_ctrl_bottom_layout"):
            self._ctrl_bottom_layout.setSpacing(6 if mode in {"wide", "cozy"} else 4)
        for attr in ("_btn_start", "_btn_snapshot"):
            button = getattr(self, attr, None)
            if button is not None:
                if attr == "_btn_start":
                    button.setMinimumWidth(78 if mode in {"wide", "cozy"} else 72 if mode == "compact" else 66 if mode == "tight" else 62)
                else:
                    button.setMinimumWidth(92 if mode in {"wide", "cozy"} else 84 if mode == "compact" else 78 if mode == "tight" else 74)

        for btn_name in ("_btn_start", "_btn_snapshot"):
            btn = getattr(self, btn_name, None)
            if btn is not None:
                btn.setToolTip(btn.text())

        if hasattr(self, "_btn_next_color"):
            self._btn_next_color.setToolTip("切换到下一种曲线颜色")
        if hasattr(self, "_btn_reset_colors"):
            self._btn_reset_colors.setToolTip("重置曲线颜色")

        self._refresh_debug_buttons()

        if sync_splitter and not self._scope_resize_active and not self._scope_layout_syncing:
            if not getattr(self, "_scope_sidebar_visible", True):
                self._scope_splitter.setSizes([0, max(1, total_width)])
                return
            if abs(current_left - target_left) > 10:
                self._scope_layout_syncing = True
                try:
                    self._scope_splitter.setSizes([target_left, max(1, total_width - target_left)])
                finally:
                    self._scope_layout_syncing = False

    def _visible_scope_panes(self) -> list[ScopePane]:
        count = max(1, min(3, int(getattr(self, "_scope_pane_count", 1) or 1)))
        return list(getattr(self, "_scope_panes", [])[:count])

    @staticmethod
    def _coerce_splitter_sizes(value, expected: int) -> list[int]:
        if not isinstance(value, (list, tuple)):
            return []
        sizes: list[int] = []
        for item in value[:expected]:
            try:
                number = int(item)
            except (TypeError, ValueError):
                return []
            if number <= 0:
                return []
            sizes.append(number)
        return sizes if len(sizes) == expected else []

    @staticmethod
    def _detach_splitter_children(splitter: QSplitter):
        while splitter.count():
            widget = splitter.widget(0)
            if widget is None:
                break
            widget.hide()
            widget.setParent(None)

    def _remember_scope_pane_splitter_sizes(self):
        count = max(1, min(3, int(getattr(self, "_scope_pane_count", 1) or 1)))
        splitter = getattr(self, "_scope_pane_splitter", None)
        if splitter is not None and count >= 2:
            sizes = [int(size) for size in splitter.sizes()]
            if len(sizes) == 2 and sum(sizes) > 0 and all(size > 0 for size in sizes):
                self._scope_plot_split_sizes = sizes

        right_splitter = getattr(self, "_scope_right_splitter", None)
        if right_splitter is not None and count == 3:
            sizes = [int(size) for size in right_splitter.sizes()]
            if len(sizes) == 2 and sum(sizes) > 0 and all(size > 0 for size in sizes):
                self._scope_plot_right_split_sizes = sizes

    def _apply_scope_pane_splitter_sizes(self, count: int):
        if count != max(1, min(3, int(getattr(self, "_scope_pane_count", 1) or 1))):
            return
        if count == 2:
            sizes = self._scope_plot_split_sizes if len(self._scope_plot_split_sizes) == 2 else [1, 1]
            self._scope_pane_splitter.setSizes(sizes)
            return
        if count == 3:
            h_sizes = self._scope_plot_split_sizes if len(self._scope_plot_split_sizes) == 2 else [1, 1]
            v_sizes = self._scope_plot_right_split_sizes if len(self._scope_plot_right_split_sizes) == 2 else [1, 1]
            self._scope_pane_splitter.setSizes(h_sizes)
            self._scope_right_splitter.setSizes(v_sizes)

    def _on_scope_pane_splitter_moved(self, *_args):
        self._begin_scope_splitter_resize()
        self._scope_resize_settle_timer.start(120)

    def _layout_scope_panes(self):
        if not hasattr(self, "_scope_pane_splitter") or not hasattr(self, "_scope_panes"):
            return
        splitter = self._scope_pane_splitter
        right_splitter = self._scope_right_splitter
        self._detach_splitter_children(right_splitter)
        self._detach_splitter_children(splitter)

        for pane in self._scope_panes:
            pane.frame.setVisible(False)

        count = max(1, min(3, int(getattr(self, "_scope_pane_count", 1) or 1)))
        if count == 1:
            splitter.addWidget(self._scope_panes[0].frame)
            splitter.setStretchFactor(0, 1)
        elif count == 2:
            splitter.addWidget(self._scope_panes[0].frame)
            splitter.addWidget(self._scope_panes[1].frame)
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 1)
        else:
            right_splitter.addWidget(self._scope_panes[1].frame)
            right_splitter.addWidget(self._scope_panes[2].frame)
            right_splitter.setStretchFactor(0, 1)
            right_splitter.setStretchFactor(1, 1)
            splitter.addWidget(self._scope_panes[0].frame)
            splitter.addWidget(right_splitter)
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 1)

        for pane in self._scope_panes[:count]:
            pane.frame.setVisible(True)
        right_splitter.setVisible(count == 3)
        QTimer.singleShot(0, lambda c=count: self._apply_scope_pane_splitter_sizes(c))

    def _set_scope_pane_count(self, count: int, save: bool = True):
        count = max(1, min(3, int(count or 1)))
        self._remember_scope_pane_splitter_sizes()
        self._scope_pane_count = count
        if hasattr(self, "_scope_view_buttons"):
            for button in self._scope_view_buttons:
                button.setChecked(button.text() == str(count))
        self._layout_scope_panes()
        if hasattr(self, "_value_table"):
            for column in (4, 5, 6):
                self._value_table.setColumnHidden(column, column - 3 > count)
        self._sync_plot_curve_state(force=True)
        self._apply_plot_timer_interval()
        if not self._collector.is_running:
            self._update_plot(force=True)
        self._update_selected_list()
        if save:
            self._save_config()

    def _toggle_scope_sidebar(self, checked: bool | None = None, save: bool = True):
        visible = bool(checked) if checked is not None else not self._scope_sidebar_visible
        self._scope_sidebar_visible = visible
        if hasattr(self, "_scope_sidebar_scroll"):
            self._scope_sidebar_scroll.setVisible(visible)
        if hasattr(self, "_btn_scope_sidebar"):
            self._btn_scope_sidebar.setChecked(visible)
            self._btn_scope_sidebar.setToolTip("隐藏设置侧栏" if visible else "显示设置侧栏")
        if hasattr(self, "_scope_splitter"):
            total_width = self._scope_splitter.width() or sum(self._scope_splitter.sizes()) or 1
            if visible:
                self._scope_splitter.setHandleWidth(2)
                self._fit_scope_layout(sync_splitter=True)
            else:
                self._scope_splitter.setHandleWidth(0)
                self._scope_splitter.setSizes([0, total_width])
        if save:
            self._save_config()

    @staticmethod
    def _short_value_name(name: str) -> str:
        return name if len(name) <= 42 else "…" + name[-39:]

    @staticmethod
    def _format_debug_value(value: float) -> str:
        if value != value:
            return "nan"
        if value in (float("inf"), float("-inf")):
            return str(value)
        if abs(value - round(value)) < 1e-9 and abs(value) < 9_000_000_000:
            return str(int(round(value)))
        return f"{value:.7g}"

    def _on_halt_target(self):
        if not self._backend.is_connected:
            self._show_warning("暂停失败", "请先连接调试器。")
            return
        if not self._backend.halt_target():
            self._show_warning("暂停失败", self._backend.last_error or "目标暂停失败。")
            return
        self._refresh_debug_state()
        self._idle_read()

    def _on_resume_target(self):
        if not self._backend.is_connected:
            self._show_warning("运行失败", "请先连接调试器。")
            return
        if not self._backend.resume_target():
            self._show_warning("运行失败", self._backend.last_error or "目标运行失败。")
            return
        self._refresh_debug_state()

    def _on_write_value(self):
        path = self._selected_value_path()
        if not path or path not in self._registry:
            self._show_warning("临时写入", "请先在实时数值表中选择变量。")
            return
        if not self._backend.is_connected:
            self._show_warning("临时写入", "请先连接调试器。")
            return

        addr, ti = self._registry[path]
        current_text = ""
        row = self._value_table.currentRow()
        if row >= 0 and self._value_table.item(row, 1):
            current_text = self._value_table.item(row, 1).text()
            if current_text == "--":
                current_text = ""

        value, ok = QInputDialog.getText(
            self,
            "临时写入变量",
            f"{path}\n地址：0x{addr:08X}\n类型：{format_type(ti)}\n\n新值：",
            text=current_text,
        )
        if not ok:
            return

        try:
            result = self._backend.write_variable_value(addr, ti, value)
        except Exception as e:
            self._show_warning("临时写入失败", str(e))
            return

        if path not in self._temporary_writes:
            self._temporary_writes[path] = (addr, ti, result["old_raw"])
        self._idle_read()
        self._refresh_debug_buttons()
        self._show_info(
            "临时写入完成",
            f"{self._short_value_name(path)}\n"
            f"{self._format_debug_value(result['old_value'])} -> "
            f"{self._format_debug_value(result['new_value'])}",
        )

    def _on_restore_value(self):
        path = self._selected_value_path()
        if not path or path not in self._temporary_writes:
            self._show_warning("恢复变量", "这个变量没有可恢复的临时写入。")
            return
        if not self._backend.is_connected:
            self._show_warning("恢复变量", "请先连接调试器。")
            return

        addr, ti, raw = self._temporary_writes[path]
        try:
            result = self._backend.restore_variable_raw(addr, ti, raw)
        except Exception as e:
            self._show_warning("恢复失败", str(e))
            return

        self._temporary_writes.pop(path, None)
        self._idle_read()
        self._refresh_debug_buttons()
        self._show_info(
            "恢复完成",
            f"{self._short_value_name(path)} 已恢复为 {self._format_debug_value(result['value'])}",
        )

    def _curve_color_names(self) -> list[str]:
        return self._curve_names()

    def _curve_display_name(self, name: str) -> str:
        if len(name) <= 34:
            return name
        return "…" + name[-31:]

    def _curve_default_index(self, name: str) -> int:
        names = self._curve_color_names()
        try:
            return names.index(name)
        except ValueError:
            return 0

    def _curve_color_for(self, name: str, index: int | None = None) -> str:
        color = self._curve_color_overrides.get(name)
        if color and QColor(color).isValid():
            return QColor(color).name()
        if index is None:
            index = self._curve_default_index(name)
        return COLORS[index % len(COLORS)]

    @staticmethod
    def _legend_curve_name(name: str) -> str:
        if len(name) <= 16:
            return name
        parts = name.split(".")
        if len(parts) >= 2:
            compact = f"{parts[-2]}.{parts[-1]}"
            if len(compact) <= 18:
                return compact
        return "…" + name[-13:]

    def _update_scope_pane_legend(self, pane: ScopePane, names: tuple[str, ...]):
        if not hasattr(pane, "legend_label"):
            return
        if not names:
            pane.legend_label.clear()
            pane.legend_label.setVisible(False)
            return

        chunks = []
        for index, name in enumerate(names[:2]):
            color = self._curve_color_for(name, index)
            label = html.escape(self._legend_curve_name(name))
            chunks.append(
                f'<span style="color:{color}; font-size:13px;">&#9632;</span> '
                f'<span>{label}</span>'
            )
        if len(names) > 2:
            chunks.append(f'<span style="color:#64748b;">+{len(names) - 2}</span>')
        pane.legend_label.setText("&nbsp;".join(chunks))
        pane.legend_label.setVisible(True)

    def _sync_plot_curve_state(self, force: bool = False):
        if not hasattr(self, "_scope_panes"):
            return

        pane_items = self._scope_pane_items()
        signature = tuple(tuple(name for name, _, _ in items) for items in pane_items)
        if not force and signature == self._plot_curve_names:
            return

        self._plot_curve_names = signature

        for pane_index, pane in enumerate(self._scope_panes):
            active = pane_index < len(pane_items)
            items = pane_items[pane_index] if active else []
            names = tuple(name for name, _, _ in items)
            if not force and names == pane.names:
                continue

            pane.names = names
            pane.y_range = None
            pane.curves.clear()
            pane.plot.clear()
            pane.count_label.setText(f"{len(names)} 个变量")
            self._update_scope_pane_legend(pane, names)
            if not active:
                continue

            for index, (name, _, _) in enumerate(items):
                color = self._curve_color_for(name, index)
                curve = pane.plot.plot(
                    [], [],
                    pen=pg.mkPen(color=color, width=PLOT_PEN_WIDTH),
                    name=name,
                    autoDownsample=True,
                    downsampleMethod="peak",
                    clipToView=True,
                )
                if hasattr(curve, "setSkipFiniteCheck"):
                    curve.setSkipFiniteCheck(True)
                pane.curves[name] = curve

    def _refresh_curve_color_controls(self):
        if not hasattr(self, "_curve_color_combo"):
            return

        current = self._curve_color_combo.currentData()
        names = self._curve_color_names()

        self._curve_color_combo.blockSignals(True)
        self._curve_color_combo.clear()
        if names:
            for name in names:
                self._curve_color_combo.addItem(self._curve_display_name(name), name)
            if current in names:
                self._curve_color_combo.setCurrentIndex(names.index(current))
            else:
                self._curve_color_combo.setCurrentIndex(0)
        else:
            self._curve_color_combo.addItem("未选择", "")
        self._curve_color_combo.blockSignals(False)
        self._curve_color_combo.setEnabled(bool(names))
        self._polish_combo_popup(self._curve_color_combo)

        enabled = bool(names)
        self._btn_curve_color.setEnabled(enabled)
        self._btn_next_color.setEnabled(enabled)
        self._btn_reset_colors.setEnabled(enabled)
        self._update_curve_color_button()

    def _update_curve_color_button(self):
        if not hasattr(self, "_btn_curve_color"):
            return
        name = self._curve_color_combo.currentData() if hasattr(self, "_curve_color_combo") else ""
        if not name:
            color = "#ffffff"
            text_color = "#8a96a8"
        else:
            color = self._curve_color_for(str(name))
            qcolor = QColor(color)
            luminance = (0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue())
            text_color = "#ffffff" if luminance < 145 else "#1f2a38"
        self._btn_curve_color.setStyleSheet(f"""
            QPushButton#colorSwatchBtn {{
                background: {color};
                border: 1px solid {color};
                border-radius: 8px;
                color: {text_color};
                font-weight: 600;
                padding: 3px 6px;
            }}
            QPushButton#colorSwatchBtn:hover {{
                border-color: #1f74e8;
            }}
            QPushButton#colorSwatchBtn:disabled {{
                background: #f2f4f8;
                border-color: #e1e6ee;
                color: #a6a6a6;
            }}
        """)

    def _apply_curve_color(self, name: str, color: str, save: bool = True):
        qcolor = QColor(color)
        if not name or not qcolor.isValid():
            return
        normalized = qcolor.name()
        self._curve_color_overrides[name] = normalized
        for pane in getattr(self, "_scope_panes", []):
            curve = pane.curves.get(name)
            if curve is not None:
                curve.setPen(pg.mkPen(color=normalized, width=PLOT_PEN_WIDTH))
        self._update_curve_color_button()
        if save:
            self._save_config()

    def _apply_all_curve_colors(self):
        for pane in getattr(self, "_scope_panes", []):
            for index, (name, curve) in enumerate(pane.curves.items()):
                curve.setPen(pg.mkPen(color=self._curve_color_for(name, index), width=PLOT_PEN_WIDTH))
        self._update_curve_color_button()

    def _selected_curve_color_name(self) -> str:
        if not hasattr(self, "_curve_color_combo"):
            return ""
        data = self._curve_color_combo.currentData()
        return str(data) if data else ""

    def _on_curve_color_target_changed(self, index: int):
        self._update_curve_color_button()

    def _on_choose_curve_color(self):
        name = self._selected_curve_color_name()
        if not name:
            return
        initial = QColor(self._curve_color_for(name))
        color = QColorDialog.getColor(initial, self, "选择曲线颜色")
        if color.isValid():
            self._apply_curve_color(name, color.name())

    def _on_next_curve_color(self):
        name = self._selected_curve_color_name()
        if not name:
            return
        palette = [QColor(color).name() for color in COLORS]
        current = QColor(self._curve_color_for(name)).name()
        try:
            index = palette.index(current)
        except ValueError:
            index = -1
        self._apply_curve_color(name, palette[(index + 1) % len(palette)])

    def _on_reset_curve_colors(self):
        self._curve_color_overrides.clear()
        self._apply_all_curve_colors()
        self._save_config()

    def _on_clear_selection(self):
        self._tree.clearSelection()

    def _on_filter_changed(self):
        self._populate_tree()

    # ================================================================
    #  调试器操作
    # ================================================================

    def _on_view_log(self):
        """Open the log file in the default system editor."""
        log_path = os.path.abspath("loopmaster.log")
        if os.path.exists(log_path):
            try:
                os.startfile(log_path)
            except Exception:
                subprocess.Popen(["notepad.exe", log_path])
        else:
            self._show_info("日志", "日志文件尚未创建。")

    def _on_import_pack(self):
        path = self._choose_open_file(
            "打开 CMSIS-Pack",
            "Pack (*.pack *.pdsc);;全部 (*.*)",
        )
        if path:
            self._pack_path = path
            if Path(path).suffix.lower() == ".pdsc":
                self._show_info(
                    "CMSIS-Pack",
                    "已记录 PDSC 文件。pyOCD 可能需要 .pack 文件；MSPM0G3507 使用内置只读目标。")

    def _on_scan_probes(self):
        self._on_scan_probes_ui()

    def _on_connect(self):
        self._on_connect_ui()

    def _on_disconnect(self):
        self._on_disconnect_ui()

    def _on_scan_probes_ui(self):
        self._btn_scan.setEnabled(False)
        self._btn_scan.setText("正在扫描…")
        QApplication.processEvents()
        try:
            self._probe_list = SWDBackend.scan_probes()
        except Exception as e:
            self._show_warning("错误", f"扫描失败：{e}")
            self._btn_scan.setEnabled(True)
            self._btn_scan.setText("扫描")
            return

        self._probe_combo.clear()
        if not self._probe_list:
            self._probe_combo.addItem("未找到调试器")
            self._conn_label.setText("未找到调试器")
        else:
            for i, p in enumerate(self._probe_list):
                uid = p.get("uid") or ""
                uid_short = uid[:8] if uid else "未知"
                kind = p.get("kind") or "调试器"
                name = p.get("name") or p.get("vendor") or "未知调试器"
                vendor = p.get("vendor") or ""
                detail = f"{name} ({vendor})" if vendor and vendor not in name else name
                label = f"{kind} - {detail} [{uid_short}]"
                self._probe_combo.addItem(label)
            self._probe_combo.setCurrentIndex(0)
            self._conn_label.setText(f"找到 {len(self._probe_list)} 个调试器")

        self._polish_combo_popup(self._probe_combo)
        self._btn_scan.setEnabled(True)
        self._btn_scan.setText("扫描")

    def _on_connect_ui(self):
        if self._backend.is_connected:
            self._on_disconnect_ui()
            return

        if not self._probe_list or self._probe_combo.currentIndex() < 0:
            self._show_warning("错误", "请先扫描调试器。")
            return

        probe_index = self._probe_combo.currentIndex()
        if probe_index >= len(self._probe_list):
            probe_index = 0
        selected_probe = self._probe_list[probe_index] if self._probe_list else {}

        connect_mode = self._connect_mode()
        target_name = self._selected_target_name()

        freq_text = self._swd_freq_combo.currentText()
        freq = int(freq_text.split()[0]) * 1_000_000

        pack = getattr(self, '_pack_path', None)
        try:
            ok = self._backend.connect(
                target=target_name, pack=pack, connect_mode=connect_mode,
                probe_index=probe_index, probe_uid=selected_probe.get("uid"),
                freq=freq)
        except Exception as e:
            self._show_warning("连接失败", str(e))
            return

        if ok:
            self._update_conn_status(True)
            self._sb_label.setText(
                f"调试器：{self._backend.probe_kind or '已连接'}")
            self._led.setStyleSheet("color: #2fb344;")
            logger.info("调试器已连接 (probe=%s, mode=%s, target=%s, SWD=%dkHz)",
                self._backend.probe_kind, connect_mode, self._backend.target_name,
                self._backend.swd_freq_khz)
            self._refresh_debug_state()
            self._idle_read()
        else:
            reason = self._backend.last_error or "未找到目标芯片，请检查接线和供电。"
            logger.warning("连接失败：%s", reason)
            self._show_warning("连接失败",
                reason)

    def _on_disconnect_ui(self):
        logger.info("调试器已断开")
        self._on_stop()
        self._backend.disconnect()
        self._temporary_writes.clear()
        self._update_conn_status(False)
        self._sb_label.setText("调试器：未连接")
        self._led.setStyleSheet("color: #e04040;")
        self._refresh_debug_state()

    # ================================================================
    #  Start / Stop
    # ================================================================

    def _on_start_stop(self):
        if self._collector.is_running:
            self._on_stop()
        else:
            self._on_start()

    def _on_start(self):
        if not self._elf_path:
            self._show_warning("错误", "请先导入 ELF 文件。")
            return
        if not self._backend.is_connected:
            self._show_warning("错误", "请先连接调试器。")
            return

        self._monitor_list = []
        for path in sorted(self._monitored):
            info = self._registry.get(path)
            if info is not None:
                addr, ti = info
                self._monitor_list.append((path, addr, ti))

        if not self._monitor_list:
            self._show_warning("错误",
                "请先在变量页选择变量。")
            return

        rate = self._rate_combo.currentData()
        if rate == 0:
            rate = 1000  # 最大模式使用高频采样

        self._collector.configure(rate, BUFFER_SECONDS)
        self._collector.set_variables(self._monitor_list)
        self._collector._sample_count = 0
        self._collector._t0 = 0.0
        self._collector._actual_rate = 0.0
        self._reset_rate_display()
        self._update_sample_rate_label()

        # Set up value table for monitored variables
        assignments = self._scope_assignment_map()
        self._value_table.blockSignals(True)
        self._value_table.setRowCount(len(self._monitor_list))
        for row, (name, _, ti) in enumerate(self._monitor_list):
            row_assignment = assignments.get(name, (name not in self._hidden_displayed_variables, False, False))
            addr = self._monitor_list[row][1]
            name_item = QTableWidgetItem(name)
            name_item.setData(ROLE_PATH, name)
            self._value_table.setItem(row, 0, name_item)
            value_item = QTableWidgetItem("--")
            value_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._value_table.setItem(row, 1, value_item)
            addr_item = QTableWidgetItem(f"0x{addr:08X}")
            addr_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._value_table.setItem(row, 2, addr_item)
            self._value_table.setItem(row, 3, QTableWidgetItem(format_type(ti)))
            for col, role in ((4, ROLE_DISPLAYED), (5, ROLE_SCOPE_2), (6, ROLE_SCOPE_3)):
                display_item = QTableWidgetItem()
                display_item.setTextAlignment(Qt.AlignCenter)
                display_item.setFlags(
                    (display_item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable) & ~Qt.ItemIsUserCheckable
                )
                display_item.setToolTip(f"显示在窗格 {col - 3}")
                display_item.setData(role, row_assignment[col - 4])
                self._value_table.setItem(row, col, display_item)
        self._value_table.blockSignals(False)
        self._value_update_counter = 0
        self._refresh_curve_color_controls()
        self._refresh_debug_buttons()

        self._sync_plot_curve_state(force=True)

        # Reset the view state without changing sampling.
        self._y_auto_btn.setChecked(True)
        self._x_scroll_btn.setChecked(True)
        self._auto_scroll = True

        self._unlimited_mode = False
        self._sample_timer.stop()
        try:
            self._collector.start()
        except Exception as e:
            self._show_warning("采样失败", str(e))
            return
        self._set_frame_rate(self._frame_rate)
        logger.info("后台采样已启动：%d 个变量，目标 %d Hz", len(self._monitor_list), rate)

        self._btn_start.setText("停止")
        self._btn_start.setObjectName("stopBtn")
        self._btn_start.setStyleSheet(self._btn_start.styleSheet())

        # Switch to Scope tab
        self._show_workspace_page("scope")

    def _on_stop(self):
        logger.info("采样已停止（%d 个样本）", self._collector._sample_count)
        self._unlimited_mode = False
        self._sample_timer.stop()
        self._collector.stop()
        self._collector._actual_rate = 0.0
        self._rate_display_value = 0.0
        self._update_sample_rate_label()
        self._btn_start.setText("开始")
        self._btn_start.setObjectName("startBtn")
        self._btn_start.setStyleSheet(self._btn_start.styleSheet())
        # Clear value table
        self._value_table.setRowCount(0)
        self._sync_value_table_placeholders()
        self._refresh_debug_buttons()
        # Reset plot auto-range
        for pane in self._visible_scope_panes():
            pane.plot.enableAutoRange(x=True)

    def _on_rate_combo_changed(self, index: int):
        """Handle sample-rate combo changes."""
        try:
            rate = self._rate_combo.currentData()
            if rate is None:
                return
            if rate == 0:
                rate = 1000  # 最大模式使用高频批量采样
            self._apply_rate(int(rate))
        except Exception as e:
            logger.exception("采样率切换失败")
            self._show_warning("采样率切换失败", str(e))

    def _apply_rate(self, rate: int):
        """Apply sample-rate changes to the collector and timer."""
        self._collector.set_sample_rate(rate)
        self._reset_rate_display()
        self._update_sample_rate_label()
        if not self._collector.is_running:
            return
        logger.info("采样率已切换：%d Hz", rate)

    def _set_rate_combo(self, rate: int):
        """Set combo to match a saved rate value."""
        if rate >= 500:
            rate = 0  # 最大
        idx = self._rate_combo.findData(rate)
        if idx >= 0:
            self._rate_combo.setCurrentIndex(idx)

    def _setup_fast_path(self):
        """Precompute the hot-path sampling plan."""
        c = self._collector
        ap = self._backend._ap
        decoder = self._backend._decoder
        if decoder is None:
            from src.core.mem_backend import _TypeDecoder
            decoder = _TypeDecoder(self._backend)
            self._backend._decoder = decoder

        self._fast_ap = ap
        self._fast_bufs = c._buffers  # {name: deque}
        self._fast_ts = c._timestamps  # deque

        # 分类：直接读取 uint32 或交给 _extract_val 处理
        self._fast_direct = []   # [(deque, word_addr), ...]
        self._fast_complex = []  # [(deque, word_addr, bo, w, sgn, flt), ...]
        self._fast_names = []    # [name, ...]

        # 构建所有变量的读取计划
        all_plans = []  # [(wa, bo, w, wc, sgn, flt, buf), ...]
        for name, addr, ti in self._monitor_list:
            wa, bo, w, wc, sgn, flt = decoder.make_plan(addr, ti)
            buf = c._buffers.get(name)
            if buf is None:
                continue
            self._fast_names.append(name)
            all_plans.append((wa, bo, w, wc, sgn, flt, buf))
            if wc <= 1 and bo == 0 and w == 4 and not sgn and not flt:
                self._fast_direct.append((buf, wa))
            elif wc <= 1:
                self._fast_complex.append((buf, wa, bo, w, sgn, flt))
                logger.debug(f"Variable '{name}' -> complex: bo={bo} w={w} sgn={sgn} flt={flt}")
            else:
                self._fast_complex.append((buf, addr, 0, w, sgn, flt, True))
                logger.debug(f"Variable '{name}' -> cross-word: bo={bo} w={w} wc={wc}")

        # 尝试合并为单次块读取，将 N 次 USB 事务变为 1 次
        self._fast_block = None
        if len(all_plans) >= 2:
            all_plans.sort(key=lambda x: x[0])  # 按 word_addr 排序
            first_wa = all_plans[0][0]
            last_plan = all_plans[-1]
            last_end = last_plan[0] + last_plan[3] * 4
            total_words = (last_end - first_wa) // 4
            if 2 <= total_words <= 64:
                self._fast_block = (first_wa, total_words, all_plans)
                logger.info(f"块读取模式：{len(all_plans)} 个变量，"
                            f"范围 0x{first_wa:08X}, {total_words} words")

    def _on_sample_tick(self):
        """Fast sampling path with inline references."""
        c = self._collector
        if not c._running:
            return

        tick_start = time.perf_counter()
        ap = self._fast_ap
        ts_deque = self._fast_ts
        t0 = c._t0

        if t0 == 0.0:
            t0 = tick_start
            c._t0 = t0

        # 尝试单次块读取，将 N 次 USB 事务合并为 1 次
        block = self._fast_block
        if block is not None:
            block_start, block_words, block_plans = block
            try:
                words = ap.read_memory_block32(block_start, block_words)
                if not isinstance(words, list):
                    words = list(words)
                for wa, bo, w, wc, sgn, flt, buf in block_plans:
                    idx = (wa - block_start) // 4
                    if wc <= 1:
                        buf.append(_extract_val(words, word_idx=idx, byte_offset=bo,
                                                width=w, is_signed=sgn, is_float=flt))
                    else:
                        # 跨字变量回退到 read()
                        buf.append(float(self._backend.read(wa + bo, w)))
                ts_deque.append(tick_start - t0)
                c._sample_count += 1
                if c._sample_count % 50 == 0:
                    c._actual_rate = c._sample_count / (tick_start - t0) if tick_start > t0 else 0
                    tick_ms = (time.perf_counter() - tick_start) * 1000
                    logger.debug(f"[BLOCK] tick: {tick_ms:.1f}ms | "
                                 f"rate: {c._actual_rate:.0f}Hz | {len(block_plans)} vars -> {block_words} words")
                return
            except Exception as e:
                logger.warning(f"块读取失败：{e}；回退到逐变量读取")
                self._fast_block = None  # 不再尝试块读取
                # 继续执行逐变量读取路径

        try:
            # 直接读取路径：对齐 uint32 是最常见情况
            for buf, wa in self._fast_direct:
                buf.append(float(ap.read_memory(wa, transfer_size=32)))

            # 复杂类型路径：偏移、有符号数或浮点数
            for item in self._fast_complex:
                if len(item) == 7:  # 跨字变量
                    buf, addr, _, w, sgn, flt, _ = item
                    buf.append(float(self._backend.read(addr, w)))
                else:
                    buf, wa, bo, w, sgn, flt = item
                    raw = ap.read_memory(wa, transfer_size=32)
                    buf.append(_extract_val(raw, byte_offset=bo, width=w,
                                            is_signed=sgn, is_float=flt))
        except Exception:
            pass  # USB 偶发错误，跳过本次采样

        ts_deque.append(tick_start - t0)

        c._sample_count += 1
        if c._sample_count % 50 == 0:
            elapsed = tick_start - t0
            c._actual_rate = c._sample_count / elapsed if elapsed > 0 else 0
            tick_ms = (time.perf_counter() - tick_start) * 1000
            logger.debug(f"Sample tick: {tick_ms:.1f}ms | rate: {c._actual_rate:.0f}Hz | "
                         f"vars: direct={len(self._fast_direct)} complex={len(self._fast_complex)}")

    def _tight_sample_loop(self):
        """Unlimited mode uses adaptive batched reads and yields to the event loop."""
        if getattr(self, "_shutting_down", False):
            self._unlimited_mode = False
            return

        c = self._collector
        if not c._running:
            self._unlimited_mode = False
            return

        ap = self._fast_ap
        ts_deque = self._fast_ts
        t0 = c._t0
        if t0 == 0.0:
            t0 = time.perf_counter()
            c._t0 = t0

        block = self._fast_block
        BACKEND = self._backend

        if block is not None:
            block_start, block_words, block_plans = block
            # 自适应流水线深度：块越小深度越大，均摊 USB 开销
            if block_words <= 8:
                pipe_depth = 48
            elif block_words <= 16:
                pipe_depth = 32
            elif block_words <= 32:
                pipe_depth = 16
            else:
                pipe_depth = 8

            try:
                batch_start = time.perf_counter()
                all_sample_vals = BACKEND.read_block_pipelined(
                    block_start, block_words, block_plans, pipe_depth)
                sample_dt = 1.0 / max(c._sample_rate, 1)
                for i, sample_vals in enumerate(all_sample_vals):
                    ts_deque.append(batch_start - t0 + i * sample_dt)
                    c._sample_count += 1
                    for (_, _, _, _, _, _, buf), val in zip(block_plans, sample_vals):
                        buf.append(val)
            except Exception:
                # 流水线失败时回退到单次块读取
                try:
                    words = ap.read_memory_block32(block_start, block_words)
                    if not isinstance(words, list):
                        words = list(words)
                    now = time.perf_counter()
                    for wa, bo, w, wc, sgn, flt, buf in block_plans:
                        idx = (wa - block_start) // 4
                        if wc <= 1:
                            buf.append(_extract_val(words, word_idx=idx, byte_offset=bo,
                                                    width=w, is_signed=sgn, is_float=flt))
                        else:
                            buf.append(float(BACKEND.read(wa + bo, w)))
                    ts_deque.append(now - t0)
                    c._sample_count += 1
                except Exception:
                    pass
        else:
            # 非块读取路径：逐个变量读取
            batch_deadline = time.perf_counter() + 0.020
            while c._running and time.perf_counter() < batch_deadline:
                try:
                    for buf, wa in self._fast_direct:
                        buf.append(float(ap.read_memory(wa, transfer_size=32)))
                    for item in self._fast_complex:
                        if len(item) == 7:
                            buf, addr, _, w, sgn, flt, _ = item
                            buf.append(float(BACKEND.read(addr, w)))
                        else:
                            buf, wa, bo, w, sgn, flt = item
                            raw = ap.read_memory(wa, transfer_size=32)
                            buf.append(_extract_val(raw, byte_offset=bo, width=w,
                                                    is_signed=sgn, is_float=flt))
                    ts_deque.append(time.perf_counter() - t0)
                    c._sample_count += 1
                except Exception:
                    pass

        # 鏇存柊瀹為檯閫熺巼
        if c._sample_count > 0:
            elapsed = time.perf_counter() - t0
            if elapsed > 0:
                c._actual_rate = c._sample_count / elapsed

        # 閲嶆柊璋冨害
        if c._running and self._unlimited_mode:
            QTimer.singleShot(0, self._tight_sample_loop)
        else:
            if not c._running:
                final_elapsed = time.perf_counter() - t0
                final_rate = c._sample_count / final_elapsed if final_elapsed > 0 else 0
                logger.info("Unlimited sampling stopped: %d samples, %.0f Hz", c._sample_count, final_rate)
            self._unlimited_mode = False

    def _reset_rate_display(self):
        self._rate_display_last_count = self._collector._sample_count
        self._rate_display_last_time = time.perf_counter()
        self._rate_display_value = 0.0

    def _measure_recent_sample_rate(self) -> float:
        """Return a smoothed recent sample rate based on actual completed samples."""
        now = time.perf_counter()
        count = self._collector._sample_count
        elapsed = now - self._rate_display_last_time

        if count < self._rate_display_last_count or elapsed <= 0:
            self._rate_display_last_count = count
            self._rate_display_last_time = now
            self._rate_display_value = 0.0
            return 0.0

        # Use a short rolling window so the UI reflects current throughput, not
        # only the average since sampling started. Low-rate modes need a little
        # more patience before the number becomes meaningful.
        min_window = 1.05 if self._collector._sample_rate <= 10 else 0.35
        if elapsed < min_window:
            return self._rate_display_value

        delta = count - self._rate_display_last_count
        measured = delta / elapsed if delta > 0 else 0.0
        if self._rate_display_value <= 0 or measured <= 0:
            smoothed = measured
        else:
            smoothed = self._rate_display_value * 0.55 + measured * 0.45

        self._rate_display_last_count = count
        self._rate_display_last_time = now
        self._rate_display_value = smoothed
        self._collector._actual_rate = smoothed
        return smoothed

    @staticmethod
    def _format_sample_rate(rate: float) -> str:
        if rate <= 0:
            return "--"
        if rate < 10:
            return f"{rate:.1f}"
        return f"{rate:.0f}"

    def _configured_sample_rate_text(self) -> str:
        selected = self._rate_combo.currentData() if hasattr(self, "_rate_combo") else None
        if selected == 0:
            return "最大"
        configured = self._collector._sample_rate
        return f"{configured} Hz" if configured else "--"

    def _update_sample_rate_label(self):
        if not hasattr(self, "_sb_rate"):
            return

        requested_fps = int(getattr(self, "_frame_rate", FRAME_RATE_DEFAULT) or FRAME_RATE_DEFAULT)
        effective_fps = self._effective_plot_fps()
        display_text = (
            f"{requested_fps} FPS"
            if effective_fps == requested_fps
            else f"{effective_fps} FPS 自适应（请求 {requested_fps}）"
        )
        tw = self._time_window_spin.value() if hasattr(self, "_time_window_spin") else TIME_WINDOW_DEFAULT
        scroll_mark = "" if self._auto_scroll else "（X 固定）"

        if self._collector.is_running:
            actual = self._measure_recent_sample_rate()
            if actual <= 0:
                actual = getattr(self._collector, "actual_rate", 0.0) or 0.0
            detail = (
                f"实际：{self._format_sample_rate(actual)} Hz  |  "
                f"目标：{self._configured_sample_rate_text()}  |  "
                f"显示：{display_text}  |  窗口：{tw}s{scroll_mark}"
            )
            text = f"实际：{self._format_sample_rate(actual)} Hz"
        else:
            detail = (
                f"实际：-- Hz  |  目标：{self._configured_sample_rate_text()}  |  显示：{display_text}"
            )
            text = "实际：-- Hz"
        self._sb_rate.setToolTip(detail)
        if hasattr(self, "_scope_rate_label"):
            self._scope_rate_label.setToolTip(detail)
        if text != self._rate_display_last_text:
            self._rate_display_last_text = text
            self._sb_rate.setText(text)
            if hasattr(self, "_scope_rate_label"):
                self._scope_rate_label.setText(text)

    def _update_plot(self, force: bool = False):
        if getattr(self, "_shutting_down", False):
            return
        running = self._collector.is_running
        if not running and not force:
            self._update_sample_rate_label()
            return

        if self._scope_resize_active:
            self._update_sample_rate_label()
            return

        if running and not force:
            now = time.perf_counter()
            min_interval = 1.0 / max(1, self._effective_plot_fps())
            if now - getattr(self, "_last_plot_draw_time", 0.0) < min_interval:
                self._update_sample_rate_label()
                return
            self._last_plot_draw_time = now

        time_window = self._time_window_spin.value() if self._time_window_spin else TIME_WINDOW_DEFAULT
        self._update_sample_rate_label()

        if running and self._auto_scroll:
            # 滚动模式：只取时间窗口加少量边距的数据，保证高效
            raw_data = self._collector.get_data(tail_seconds=time_window * 1.5)
        else:
            # 用户手动缩放或平移时取全部历史数据
            raw_data = self._collector.get_data()

        if not raw_data:
            return

        # 插值和抽取处理
        data = self._process_display_data(raw_data)

        # 批量更新曲线
        latest_ts = 0.0
        effective_fps = self._effective_plot_fps()
        point_budget = point_budget_for_fps(effective_fps)
        visible_panes = self._visible_scope_panes()
        self._programmatic_plot_range_update = True
        for pane in visible_panes:
            pane.plot.enableAutoRange(x=False, y=False)
            for name, curve in pane.curves.items():
                if name in data:
                    ts, vals = data[name]
                    if len(ts) > 0:
                        ts, vals = thin_display_series(ts, vals, point_budget)
                        curve.setData(ts, vals)
                        if ts[-1] > latest_ts:
                            latest_ts = ts[-1]

        # 自动滚动：显示最近 time_window 秒
        if latest_ts > 0 and self._auto_scroll:
            x_min = max(0, latest_ts - time_window)
            for pane in visible_panes:
                previous = getattr(pane, "last_x_range", None)
                min_delta = max(0.002, (latest_ts - x_min) * 0.00025)
                if (
                    previous is not None
                    and abs(previous[0] - x_min) < min_delta
                    and abs(previous[1] - latest_ts) < min_delta
                ):
                    continue
                pane.plot.setXRange(x_min, latest_ts, padding=0.02)
                pane.last_x_range = (x_min, latest_ts)

        if self._y_auto_btn.isChecked():
            self._update_scope_y_ranges(data)
        self._programmatic_plot_range_update = False

        # Update value table (throttled to ~5Hz to reduce flicker)
        fps = self._effective_plot_fps()
        self._value_update_counter = getattr(self, '_value_update_counter', 0) + 1
        if self._value_update_counter % max(1, fps // 5) == 0:
            self._update_value_table(data)

    def _on_user_interact(self, vb):
        """Manual view changes only fix the affected axes; sampling keeps running."""
        if getattr(self, "_programmatic_plot_range_update", False):
            return
        x_changed = True
        y_changed = True
        try:
            x_changed = bool(vb[0])
            y_changed = bool(vb[1])
        except Exception:
            pass

        if x_changed and self._x_scroll_btn.isChecked():
            self._x_scroll_btn.setChecked(False)
        if y_changed and self._y_auto_btn.isChecked():
            self._y_auto_btn.setChecked(False)

    def _on_x_scroll_toggled(self, checked: bool):
        """Toggle X-axis following. Data collection continues either way."""
        self._auto_scroll = checked
        self._x_scroll_btn.setText("X 跟随" if checked else "X 固定")
        self._update_sample_rate_label()
        if checked:
            self._snap_x_to_latest()

    def _on_y_auto_toggled(self, checked: bool):
        """Toggle Y-axis auto range."""
        for pane in self._visible_scope_panes():
            pane.plot.enableAutoRange(y=False)
        if checked:
            for pane in self._visible_scope_panes():
                pane.y_range = None
            self._y_auto_btn.setText("Y 自动")
            self._update_plot(force=True)
        else:
            self._y_auto_btn.setText("Y 手动")

    def _update_scope_y_ranges(self, data: dict):
        """Keep each visible pane's Y range stable even on near-flat signals."""
        if not data:
            return

        for pane in self._visible_scope_panes():
            values = []
            for name in pane.curves.keys():
                series = data.get(name)
                if not series:
                    continue
                values.append(series[1])

            next_range = calculate_y_range(values, pane.y_range)
            if next_range is None:
                continue
            y_min, y_max = next_range
            pane.y_range = next_range
            pane.plot.setYRange(y_min, y_max, padding=0)

    def _snap_x_to_latest(self):
        """Move the X view to the newest data without touching collection."""
        raw_data = self._collector.get_data(tail_seconds=0.05)
        latest_ts = 0.0
        for ts, _vals in raw_data.values():
            if len(ts) > 0 and ts[-1] > latest_ts:
                latest_ts = ts[-1]
        if latest_ts > 0:
            time_window = self._time_window_spin.value() if self._time_window_spin else TIME_WINDOW_DEFAULT
            self._programmatic_plot_range_update = True
            for pane in self._visible_scope_panes():
                pane.plot.setXRange(max(0, latest_ts - time_window), latest_ts, padding=0.02)
            self._programmatic_plot_range_update = False

    def _set_frame_rate(self, fps: int):
        """Update plot timer and menu state when FPS changes."""
        self._frame_rate = max(1, int(fps))
        self._apply_plot_timer_interval()
        if not self._plot_timer.isActive() and not getattr(self, "_plot_timer_resize_suspended", False):
            self._plot_timer.start()
        for act in self._fps_actions:
            act.setChecked(act.data() == self._frame_rate)
        if hasattr(self, "_display_menu_btn"):
            self._display_menu_btn.setText(f"显示 {self._frame_rate} FPS")
        self._refresh_nav_buttons()

    def _effective_plot_fps(self) -> int:
        pane_count = max(1, min(3, int(getattr(self, "_scope_pane_count", 1) or 1)))
        curve_count = sum(len(pane.curves) for pane in self._visible_scope_panes()) if hasattr(self, "_scope_panes") else 0
        frame_rate = int(getattr(self, "_frame_rate", FRAME_RATE_DEFAULT) or FRAME_RATE_DEFAULT)
        return effective_plot_fps(frame_rate, pane_count, curve_count)

    def _apply_plot_timer_interval(self):
        if not hasattr(self, "_plot_timer"):
            return
        fps = self._effective_plot_fps()
        self._plot_timer.setInterval(max(8, int(1000 / fps)))

    def _process_display_data(self, data: dict) -> dict:
        return process_display_data(data, self._collector.actual_rate, self._effective_plot_fps())

    def _update_value_table(self, data: dict):
        """Show latest value for each monitored variable."""
        names = sorted(data.keys())
        t = self._value_table
        old_path = self._selected_value_path()
        assignments = self._scope_assignment_map()
        t.blockSignals(True)
        if t.rowCount() != len(names):
            t.setRowCount(len(names))
        for row, name in enumerate(names):
            row_assignment = assignments.get(name, (name not in self._hidden_displayed_variables, False, False))
            ts, vals = data[name]
            latest = f"{vals[-1]:.4g}" if len(vals) > 0 else "--"
            # Name column
            name_item = t.item(row, 0)
            if name_item is None:
                name_item = QTableWidgetItem(name)
                t.setItem(row, 0, name_item)
            else:
                name_item.setText(name)
            name_item.setData(ROLE_PATH, name)
            # Value column
            val_item = t.item(row, 1)
            if val_item is None:
                val_item = QTableWidgetItem(latest)
                val_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(row, 1, val_item)
            else:
                val_item.setText(latest)
            # Address column
            info = self._registry.get(name)
            addr_text = f"0x{info[0]:08X}" if info else ""
            addr_item = t.item(row, 2)
            if addr_item is None:
                addr_item = QTableWidgetItem(addr_text)
                addr_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(row, 2, addr_item)
            else:
                addr_item.setText(addr_text)
            # Type column from registry
            type_item = t.item(row, 3)
            if type_item is None:
                type_str = format_type(info[1]) if info else ""
                type_item = QTableWidgetItem(type_str)
                t.setItem(row, 3, type_item)
            elif info:
                type_item.setText(format_type(info[1]))
            for col, role in ((4, ROLE_DISPLAYED), (5, ROLE_SCOPE_2), (6, ROLE_SCOPE_3)):
                display_item = t.item(row, col)
                if display_item is None:
                    display_item = QTableWidgetItem()
                    display_item.setTextAlignment(Qt.AlignCenter)
                    display_item.setFlags(
                        (display_item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable) & ~Qt.ItemIsUserCheckable
                    )
                    display_item.setToolTip(f"显示在窗格 {col - 3}")
                    t.setItem(row, col, display_item)
                display_item.setData(role, row_assignment[col - 4])
        t.blockSignals(False)
        self._restore_value_selection(old_path)
        self._refresh_debug_buttons()

    def _idle_read(self):
        """Single-shot read when scope is not actively sampling.
        Runs on _idle_timer (~4Hz) so values display even without pressing START.
        """
        if getattr(self, "_shutting_down", False):
            return
        if not self._backend.is_connected:
            return
        if self._collector.is_running:
            return
        if not self._monitored:
            if self._value_table.rowCount() > 0:
                self._value_table.setRowCount(0)
            return

        # Build monitor list from current selections
        monitor_list = []
        for path in sorted(self._monitored):
            info = self._registry.get(path)
            if info is not None:
                addr, ti = info
                monitor_list.append((path, addr, ti))

        if not monitor_list:
            return

        try:
            raw = self._backend.read_batch(monitor_list)
        except Exception:
            return

        # Convert to plot-compatible format for _update_value_table
        data = {}
        for name, val in raw.items():
            data[name] = ([0.0], [val])
        self._update_value_table(data)

    # ================================================================
    #  Export
    # ================================================================

    def _on_export(self):
        if not self._elf_path:
            self._show_warning("错误", "没有可导出的数据。")
            return
        path = self._choose_save_file("导出 CSV", "scope_data.csv", "CSV (*.csv)")
        if not path:
            return

        data = self._collector.get_data()
        if not data:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["变量", "地址", "类型"])
                for p, (addr, ti) in self._registry.items():
                    writer.writerow([p, f"0x{addr:08X}", format_type(ti)])
            return

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            names = list(data.keys())
            writer.writerow(["时间戳"] + names)
            max_len = max(len(v[1]) for v in data.values()) if data else 0
            ts_all = list(list(data.values())[0][0]) if data else []
            for i in range(max_len):
                row = [ts_all[i] if i < len(ts_all) else ""]
                for n in names:
                    vals = data[n][1]
                    row.append(vals[i] if i < len(vals) else "")
                writer.writerow(row)

        self._sb_label.setText(f"已导出到 {path}")

    # ================================================================
    #  Config persistence
    # ================================================================

    def _load_config(self) -> dict:
        try:
            if self._config_path.exists():
                with open(self._config_path, "r", encoding="utf-8-sig") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_config(self):
        self._remember_scope_pane_splitter_sizes()
        remembered_elf = self._elf_path or self._recent_elf_path
        remembered_variables = (
            sorted(self._monitored)
            if self._loaded_elf_this_session
            else self._saved_monitored_variables
        )
        cfg = {
            "elf_path": str(remembered_elf) if remembered_elf else "",
            "sample_rate": self._rate_combo.currentData() or 1000,
            "frame_rate": self._frame_rate,
            "swd_freq_index": self._swd_freq_combo.currentIndex(),
            "connect_mode_index": self._connect_mode_index(),
            "target_name": self._selected_target_name(),
            "y_auto": self._y_auto_btn.isChecked(),
            "x_scroll": self._x_scroll_btn.isChecked(),
            "scope_pane_count": self._scope_pane_count,
            "scope_sidebar_visible": self._scope_sidebar_visible,
            "scope_plot_splitter_sizes": list(self._scope_plot_split_sizes),
            "scope_plot_right_splitter_sizes": list(self._scope_plot_right_split_sizes),
            "scope_assignments": {
                name: list(values)
                for name, values in self._scope_assignment_map().items()
            },
            "curve_colors": self._curve_color_overrides,
            "hidden_displayed_variables": sorted(self._hidden_displayed_variables),
            "monitored_variables": remembered_variables,
            "keil_root": str(getattr(self, "_keil_root", Path("D:\\Keil"))),
            "serial_port": self._tab_serial.port_combo.currentData() if hasattr(self, "_tab_serial") else "",
            "serial_baudrate": int(self._tab_serial.baud_combo.currentText()) if hasattr(self, "_tab_serial") else 115200,
            "serial_protocol": self._tab_serial.protocol_combo.currentText() if hasattr(self, "_tab_serial") else "FireWater CSV",
        }
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _restore_selected_items(self, parent: QTreeWidgetItem, saved: list[str]):
        for i in range(parent.childCount()):
            item = parent.child(i)
            path = item.data(0, ROLE_PATH)
            if path in saved:
                item.setSelected(True)
                self._monitored.add(path)
            self._restore_selected_items(item, saved)

    def _shutdown(self):
        if getattr(self, "_shutdown_complete", False):
            return
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True

        def step(label: str, fn):
            try:
                fn()
            except Exception:
                logger.exception("关闭步骤失败：%s", label)

        def stop_timers():
            for name in (
                "_plot_timer",
                "_sample_timer",
                "_idle_timer",
                "_debug_state_timer",
                "_scope_resize_sync_timer",
                "_scope_resize_settle_timer",
                "_window_resize_sync_timer",
                "_hero_roxy_timer",
            ):
                timer = getattr(self, name, None)
                if timer is not None:
                    timer.stop()

        def request_backend_shutdown():
            request = getattr(self._backend, "request_shutdown", None)
            if callable(request):
                request()

        def stop_sampling():
            self._unlimited_mode = False
            stopped = self._collector.stop(timeout=0.8)
            if not stopped:
                logger.warning("Sampling thread did not stop before shutdown timeout")

        def stop_serial():
            if not self._serial_controller.shutdown(timeout=0.6):
                logger.warning("Serial worker did not stop before shutdown timeout")
            if hasattr(self, "_tab_serial"):
                self._tab_serial.set_connected(False)

        def disconnect_backend():
            try:
                self._backend.disconnect(timeout=0.45)
            except TypeError:
                self._backend.disconnect()

        step("stop timers", stop_timers)
        step("request backend shutdown", request_backend_shutdown)
        step("stop sampling", stop_sampling)
        step("stop serial", stop_serial)
        step("save config", self._save_config)
        step("disconnect backend", disconnect_backend)
        self._shutdown_complete = True

    def closeEvent(self, event):
        self._shutdown()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)


# ================================================================
#  Entry Point
# ================================================================

def run_scope(elf_path: str = None, pack_path: str = None, target: str = None):
    # Windows: 将系统定时器精度从约 15.6ms 提升到 1ms
    import platform
    timer_precision_active = False
    if platform.system() == "Windows":
        os.environ.setdefault("QT_QPA_PLATFORM", "windows")
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
            timer_precision_active = True
        except Exception:
            pass

    setup_logging()
    logger.info("初始化 QApplication")
    app = QApplication(sys.argv)
    try:
        logger.info("Qt platform: %s", QApplication.platformName())
    except Exception:
        pass
    app.setQuitOnLastWindowClosed(True)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    icon = _load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    logger.info("应用主题")
    apply_pcl_theme(app)

    pg.setConfigOptions(
        background=(255, 255, 255),
        foreground=(83, 101, 125),
        antialias=False,
    )

    logger.info("创建主窗口")
    window = MainWindow()
    logger.info("主窗口创建完成")
    app.aboutToQuit.connect(window._shutdown)
    if elf_path:
        window._elf_path = Path(elf_path)
        window._recent_elf_path = window._elf_path
        window._loaded_elf_this_session = True
        window._load_variables()
        window.setWindowTitle(f"LoopMaster v2.1 - {Path(elf_path).name}")
        window._refresh_recent_elf_button()
        window._refresh_hero()

    if pack_path:
        window._pack_path = pack_path

    if target:
        window._set_target_name(target)
        if window._backend.connect(target=target, pack=pack_path):
            window._update_conn_status(True)
            window._refresh_debug_state()

    exit_code = 0
    try:
        logger.info("显示主窗口")
        window.show()
        logger.info("进入 Qt 事件循环")
        exit_code = app.exec()
    finally:
        window._shutdown()
        if timer_precision_active:
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
    sys.exit(exit_code)

