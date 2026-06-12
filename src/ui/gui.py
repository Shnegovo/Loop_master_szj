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
from src.core.acquisition_sources import (
    SCOPE_SOURCE_KEIL_WATCH,
    SCOPE_SOURCE_SERIAL_WAVEFORM,
    SCOPE_SOURCE_SWD,
    acquisition_source_options,
    active_acquisition_source,
    normalize_acquisition_source_key,
)
from src.core.debug_workbench import (
    DebugBackendKind,
    DebugRuntimeState,
    debug_command_plans_for_status,
    make_debug_status,
)
from src.core.debug_backend_registry import (
    create_default_debug_backend_registry,
    debug_backend_local_profile_diagnostic_rows,
)
from src.core.debug_session_controller import DebugSessionController
from src.core.debug_variable_access import (
    DebugVariableReadRequest,
    DebugVariableReadResult,
    DebugVariableWriteRequest,
    DebugVariableWriteResult,
)
from src.core.debug_sources import (
    SourceManifest,
    SourcePathRemapPreview,
    preview_source_manifest_path_remap,
    source_manifest_from_compile_commands,
    source_manifest_from_gdb_sources,
    source_manifest_missing_path_hints,
    source_manifest_from_readelf_line_table_text,
    source_manifest_from_roots,
)
from src.core.lifecycle import ShutdownSequence
from src.core.debug_transactions import (
    DebugCommandHistory,
    build_unavailable_debug_transactions,
    debug_transaction_by_key,
)
from src.core.keil.commands import build_keil_debug_transactions, transaction_by_key
from src.core.keil.breakpoint_sync import (
    KeilBreakpointSyncAction,
    KeilBreakpointSyncRequest,
    KeilBreakpointSyncResult,
    build_keil_breakpoint_sync_request_from_state,
)
from src.core.keil.auto_debug import (
    KeilAutoDebugRequest,
    KeilAutoDebugResult,
    run_keil_auto_debug_transaction,
)
from src.core.keil.live_write import (
    KeilLiveVariableReadRequest,
    KeilLiveVariableReadResult,
    KeilResolvedVariable,
    KeilLiveVariableWriteRequest,
    KeilLiveVariableWriteResult,
)
from src.core.keil.presets import (
    KeilVariablePresetProfile,
    keil_live_write_prompt_hint,
    keil_live_write_seed,
    keil_variable_preset_profile,
)
from src.core.keil.watch import (
    KEIL_WATCH_MAX_HZ,
    KeilUvSockWatchBackend,
    keil_watch_rate_warning,
    make_keil_watch_type,
)
from src.core.keil.profile import KeilBuildResult, KeilDebugProfile, make_keil_debug_profile
from src.core.keil.profile_store import (
    KeilDebugProfileStore,
    load_keil_profile_store,
    profile_record_from_debug_profile,
    save_keil_profile_store,
)
from src.core.keil.project import parse_keil_project
from src.core.keil.run_to_cursor import KeilRunToCursorRequest, KeilRunToCursorResult
from src.core.keil.uvsock import UvscLaunchResult
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
    ask_pcl_confirmation,
    ask_pcl_text,
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
        self._scope_read_source = SCOPE_SOURCE_SWD
        self._keil_watch_backend: KeilUvSockWatchBackend | None = None
        self._keil_watch_registry: dict[str, tuple[int, TypeInfo]] = {}
        self._keil_watch_next_idle_connect = 0.0
        self._collector = DataCollector()
        self._collector.set_backend(self._backend)
        self._serial_controller = SerialController(self)
        self._unlimited_mode = False
        self._frame_rate = FRAME_RATE_DEFAULT
        self._probe_list: list[dict] = []
        self._pack_path: Optional[Path] = None
        self._config_path = Path("loopmaster.json")
        self._variable_write_audit_path = Path("loopmaster_variable_writes.jsonl")
        self._keil_profile_store_path = Path("loopmaster_keil_profiles.json")
        self._keil_profile_store = load_keil_profile_store(self._keil_profile_store_path)
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
        self._shutdown_report = None
        self._keil_root = Path(os.environ.get("LOOPMASTER_KEIL_ROOT") or "D:\\Keil")
        self._debug_uvsock_port = 4827
        self._debug_command_history = DebugCommandHistory(max_entries=64)
        self._debug_remote_breakpoint_snapshot = None
        self._debug_backend_snapshot_record = None
        self._debug_backend_diagnostics: tuple[tuple[str, str], ...] = ()
        self._debug_last_live_read_result: KeilLiveVariableReadResult | None = None
        self._debug_last_live_write_result: KeilLiveVariableWriteResult | None = None
        self._debug_last_breakpoint_sync_result: KeilBreakpointSyncResult | None = None
        self._debug_last_runtime_control_result = None
        self._debug_last_run_to_cursor_result: KeilRunToCursorResult | None = None
        self._debug_keil_profile: KeilDebugProfile | None = None
        self._debug_keil_build_result: KeilBuildResult | None = None
        self._debug_keil_launch_result = None
        self._debug_source_preview_manifest: SourceManifest | None = None
        self._debug_command_preview_suspended = False
        self._debug_source_provider_key = "auto"
        self._debug_compile_commands_path: Path | None = None
        self._debug_manual_source_roots: tuple[Path, ...] = ()
        self._debug_gdb_sources_text = ""
        self._debug_dwarf_line_table_text = ""
        self._debug_dwarf_text_elf_path: Path | None = None
        self._debug_source_remap_preview: SourcePathRemapPreview | None = None
        self._debug_source_remaps: list[dict[str, str]] = []
        cfg = self._load_config()
        if cfg:
            debug_keil_cfg = cfg.get("debug_keil", {}) if isinstance(cfg.get("debug_keil", {}), dict) else {}
            keil_root = debug_keil_cfg.get("root", "") or cfg.get("keil_root", "")
            if keil_root:
                self._keil_root = Path(str(keil_root))
            try:
                self._debug_uvsock_port = max(1, min(65535, int(debug_keil_cfg.get("uvsock_port", self._debug_uvsock_port))))
            except Exception:
                pass
            elf = cfg.get("elf_path", "")
            if elf and Path(elf).exists():
                self._recent_elf_path = Path(elf)
            self._saved_monitored_variables = cfg.get("monitored_variables", []) or []
            self._hidden_displayed_variables = set(cfg.get("hidden_displayed_variables", []) or [])
            saved_scope_source = str(cfg.get("scope_read_source", SCOPE_SOURCE_SWD) or SCOPE_SOURCE_SWD)
            self._scope_read_source = normalize_acquisition_source_key(saved_scope_source)
            saved_keil_watch = cfg.get("keil_watch_variables", {}) or {}
            if isinstance(saved_keil_watch, dict):
                for expression, type_name in saved_keil_watch.items():
                    expr = str(expression).strip()
                    if expr:
                        self._keil_watch_registry[expr] = (0, make_keil_watch_type(str(type_name or "float")))
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
            self._restore_debug_source_config(cfg)
        self._debug_backend_registry = create_default_debug_backend_registry(
            keil_root=self._keil_root,
            uvsock_port=self._debug_uvsock_port,
            include_placeholders=True,
        )
        self._debug_backend_kind = self._debug_backend_registry.default_kind()
        self._debug_backend = self._debug_backend_registry.create(self._debug_backend_kind)
        self._debug_session_controller = DebugSessionController(
            self._debug_backend_registry,
            backend=self._debug_backend_kind,
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
        self._scope_rate_note = ""
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
        if getattr(self, "_scope_read_source", "swd") == "keil_watch":
            self._restore_keil_watch_scope_selection()

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

    def _restore_keil_watch_scope_selection(self) -> None:
        if not getattr(self, "_keil_watch_registry", None):
            return
        saved = [
            name for name in self._saved_monitored_variables
            if name in self._keil_watch_registry
        ]
        self._monitored = set(saved or self._keil_watch_registry.keys())
        self._collector.set_backend(self._active_scope_backend(connect=False))
        if hasattr(self, "_value_table"):
            self._sync_value_table_placeholders()
            self._sync_plot_curve_state(force=True)
            self._update_selected_list()

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

        if getattr(self, "_scope_read_source", "swd") == "keil_watch":
            status = self._tab_debug_workbench.debug_status if hasattr(self, "_tab_debug_workbench") else None
            project_text = Path(status.project_path).name if status is not None and status.project_path else "Keil Watch"
            connected = self._scope_backend_connected()
            self._hero_file.setText(project_text)
            self._hero_probe.setText("UVSOCK 已连接" if connected else "UVSOCK 待连接")
            self._hero_vars.setText(f"{len(self._monitored)} 个 Watch")
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

        act_write_audit = QAction("查看变量写入记录", self)
        act_write_audit.triggered.connect(self._on_view_variable_write_audit)
        self._file_menu.addAction(act_write_audit)

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

    def _choose_existing_directory(self, title: str) -> str:
        dialog = QFileDialog(self, title)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setFont(QFont("Microsoft YaHei UI", 10))
        dialog.setLabelText(QFileDialog.Accept, "选择")
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
        self._tab_debug_workbench.summaryChanged.connect(self._refresh_debug_variable_presets)
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
        self._tab_debug_workbench.backendSelectionChanged.connect(self._on_debug_backend_selected)
        self._tab_debug_workbench.sourceProviderSelectionChanged.connect(self._on_debug_source_provider_selected)
        self._tab_debug_workbench.sourceProviderConfigureRequested.connect(self._on_debug_source_provider_configure_requested)
        self._tab_debug_workbench.sourceRemapRequested.connect(self._on_debug_source_remap_requested)
        self._tab_debug_workbench.scopeAcquisitionSelectionChanged.connect(self._on_scope_acquisition_selected)
        self._tab_debug_workbench.variablePresetWriteRequested.connect(self._write_keil_live_variable_from_preset)
        self._tab_debug_workbench.variablePresetWatchRequested.connect(self._add_keil_watch_preset_to_scope)
        self._tab_debug_workbench.profileSaveRequested.connect(self._save_current_keil_debug_profile)
        self._tab_debug_workbench.profileLoadRequested.connect(self._load_default_keil_debug_profile)
        self._tab_debug_workbench.keilProfileConfigureRequested.connect(self._configure_keil_debug_runtime)
        self._tab_debug_workbench.remoteBreakpointRefreshRequested.connect(self._refresh_remote_breakpoints_from_workbench)
        self._refresh_debug_backend_options()
        self._refresh_debug_source_provider_options()
        self._refresh_debug_scope_acquisition_status()
        self._tab_debug_workbench.set_debug_controls_ready(True)
        self._tab_debug_workbench.set_backend_diagnostics(self._with_debug_session_contract_diagnostics(self._debug_workbench_idle_diagnostics()))
        self._refresh_debug_live_loop_status()
        self._refresh_debug_variable_presets()
        self._sync_debug_source_manifest_preview()
        self._sync_debug_command_preview()

    def _on_debug_workbench_action(self, action_key: str):
        if action_key == "discover":
            self._discover_debug_backend_for_workbench()
            return
        if action_key == "build_project":
            self._build_keil_project_for_workbench()
            return
        if action_key == "launch_uvsock":
            self._launch_keil_uvsock_for_workbench()
            return
        if action_key == "auto_debug":
            self._run_keil_auto_debug_from_workbench()
            return
        if action_key == "attach":
            self._connect_debug_backend_read_only_for_workbench()
            return
        if action_key in {"halt", "run", "reset", "step", "step_over"}:
            self._control_keil_runtime_from_workbench(action_key)
            return
        if action_key == "run_to_cursor":
            self._run_keil_to_cursor_from_workbench()
            return
        if action_key == "sync_breakpoints":
            self._sync_keil_breakpoints_from_workbench()
            return
        if action_key == "write_variables":
            self._write_keil_live_variable_from_workbench()
            return
        if hasattr(self, "_sb_label"):
            self._sb_label.setText("该调试动作尚未接入后端。")

    def _refresh_debug_backend_options(self):
        if not hasattr(self, "_tab_debug_workbench"):
            return
        options = tuple(
            (descriptor.kind.value, descriptor.display_name, descriptor.notes)
            for descriptor in self._debug_backend_registry.descriptors()
        )
        self._tab_debug_workbench.set_backend_options(options, self._debug_backend_kind.value)

    def _on_debug_backend_selected(self, backend_key: str):
        try:
            kind = DebugBackendKind(str(backend_key))
            descriptor = self._debug_backend_registry.descriptor(kind)
            backend = self._debug_backend_registry.create(kind)
        except Exception as exc:
            if hasattr(self, "_sb_label"):
                self._sb_label.setText(f"调试后端切换失败：{exc}")
            return
        self._debug_backend_kind = kind
        self._debug_backend = backend
        self._debug_session_controller.set_backend(kind)
        self._debug_remote_breakpoint_snapshot = None
        self._debug_backend_snapshot_record = None
        self._debug_backend_diagnostics = ()
        self._debug_last_live_read_result = None
        self._debug_last_live_write_result = None
        self._debug_last_breakpoint_sync_result = None
        self._debug_last_run_to_cursor_result = None
        self._debug_keil_profile = None
        self._debug_keil_build_result = None
        self._debug_keil_launch_result = None
        tab = self._tab_debug_workbench
        status = make_debug_status(
            state=DebugRuntimeState.DISCONNECTED,
            backend=kind,
            detail=f"{descriptor.display_name} 已选择，等待发现后端",
            project_path=tab.debug_status.project_path,
            target_name=tab.debug_status.target_name,
        )
        self._debug_command_preview_suspended = True
        try:
            tab.set_debug_status(status, controls_ready=True)
            tab.set_pc_evidence(None)
            self._sync_debug_source_manifest_preview()
            tab.set_backend_diagnostics(self._with_debug_session_contract_diagnostics(self._debug_workbench_idle_diagnostics()))
            self._refresh_debug_variable_presets()
        finally:
            self._debug_command_preview_suspended = False
        self._debug_command_history.clear()
        self._sync_debug_command_preview()
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(f"已切换调试后端：{descriptor.display_name}")
        self._refresh_hero()

    def _refresh_debug_source_provider_options(self):
        if not hasattr(self, "_tab_debug_workbench"):
            return
        options = [
            ("auto", "自动", "根据当前后端和已加载工程选择最安全的源码预览"),
            ("keil", "Keil 工程", "使用当前 Keil 工程中的分组和文件路径"),
            ("compile_commands", "编译数据库", "从当前 ELF 附近的 compile_commands.json 读取源码列表"),
            ("manual_roots", "源码根", "从当前 ELF 附近目录轻量扫描源码文件"),
            ("elf_dwarf", "ELF/DWARF", "后续显式触发 readelf -wl，不在切换后端时自动启动外部进程"),
            ("gdb_text", "GDB 文本", "后续粘贴或导入 GDB info sources 文本"),
        ]
        self._tab_debug_workbench.set_source_provider_options(options, self._debug_source_provider_key)

    def _refresh_debug_scope_acquisition_status(self) -> None:
        if not hasattr(self, "_tab_debug_workbench"):
            return
        source = normalize_acquisition_source_key(getattr(self, "_scope_read_source", SCOPE_SOURCE_SWD))
        descriptor = active_acquisition_source(
            source,
            keil_watch_ready=True,
            serial_ready=hasattr(self, "_tab_serial"),
            include_planned=True,
        )
        self._tab_debug_workbench.set_scope_acquisition_options(
            acquisition_source_options(
                source,
                keil_watch_ready=True,
                serial_ready=hasattr(self, "_tab_serial"),
                include_planned=True,
            ),
            source,
        )
        self._tab_debug_workbench.set_scope_acquisition_status(descriptor.short_label, descriptor.detail)

    def _on_scope_acquisition_selected(self, source: str) -> None:
        source = str(source or "").strip()
        if source == SCOPE_SOURCE_SERIAL_WAVEFORM:
            self._show_workspace_page("serial")
            self._refresh_debug_scope_acquisition_status()
            if hasattr(self, "_sb_label"):
                self._sb_label.setText("已切换到串口助手波形页。")
            return
        if source in {SCOPE_SOURCE_SWD, SCOPE_SOURCE_KEIL_WATCH}:
            self._set_scope_read_source(source)
            if hasattr(self, "_sb_label"):
                self._sb_label.setText(f"示波采集源：{self._scope_read_source_label()}")
            self._refresh_hero()
            return
        self._refresh_debug_scope_acquisition_status()
        if hasattr(self, "_sb_label"):
            self._sb_label.setText("该示波采集源仍在计划中，尚未接入真实执行器。")

    def _on_debug_source_provider_selected(self, provider_key: str):
        provider_key = str(provider_key or "auto")
        if provider_key == self._debug_source_provider_key:
            return
        self._debug_source_provider_key = provider_key
        self._sync_debug_source_manifest_preview()
        self._sync_debug_command_preview()
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(f"源码来源：{self._debug_source_provider_label(provider_key)}")
        self._refresh_hero()

    def _on_debug_source_provider_configure_requested(self, provider_key: str):
        provider_key = str(provider_key or self._debug_source_provider_key or "auto")
        if provider_key == "auto":
            provider_key = self._debug_source_provider_key
        if provider_key == "compile_commands":
            path = self._choose_open_file("选择 compile_commands.json", "编译数据库 (compile_commands.json);;JSON 文件 (*.json);;所有文件 (*.*)")
            if not path:
                return
            self.configure_debug_compile_commands(path)
            return
        if provider_key == "manual_roots":
            root = self._choose_existing_directory("选择源码根目录")
            if not root:
                return
            self.configure_debug_manual_source_roots((root,))
            return
        if provider_key == "gdb_text":
            text, ok = QInputDialog.getMultiLineText(
                self,
                "导入 GDB info sources",
                "粘贴已捕获的 GDB info sources 输出：",
                self._debug_gdb_sources_text,
            )
            if not ok:
                return
            self.configure_debug_gdb_sources_text(text)
            return
        if provider_key == "elf_dwarf":
            text, ok = QInputDialog.getMultiLineText(
                self,
                "导入 readelf -wl 文本",
                "粘贴已捕获的 readelf -wl 行号表文本（不会自动启动 readelf）：",
                self._debug_dwarf_line_table_text,
            )
            if not ok:
                return
            self.configure_debug_dwarf_line_table_text(text)
            return
        if hasattr(self, "_sb_label"):
            self._sb_label.setText("当前源码来源不需要额外配置。")

    def _on_debug_source_remap_requested(self):
        manifest = self._debug_source_preview_manifest
        if manifest is None and hasattr(self, "_tab_debug_workbench"):
            manifest = self._tab_debug_workbench.source_manifest
        if manifest is None:
            if hasattr(self, "_sb_label"):
                self._sb_label.setText("当前没有可映射的源码清单。")
            return
        hints = source_manifest_missing_path_hints(manifest)
        if not hints:
            if hasattr(self, "_sb_label"):
                self._sb_label.setText("当前源码路径正常，没有可映射的缺失项。")
            return
        root = self._choose_existing_directory("选择本地源码根目录")
        if not root:
            return
        self.preview_debug_source_remap(
            missing_dir=hints[0].missing_dir,
            local_root=root,
            persist=True,
        )

    def configure_debug_compile_commands(self, path: str | Path) -> SourceManifest:
        self._debug_compile_commands_path = Path(path).expanduser()
        self._debug_source_provider_key = "compile_commands"
        manifest = self._sync_configured_debug_source_provider()
        self._refresh_debug_source_provider_options()
        self._save_config()
        return manifest

    def configure_debug_manual_source_roots(self, roots: tuple[str | Path, ...] | list[str | Path]) -> SourceManifest:
        self._debug_manual_source_roots = tuple(Path(root).expanduser() for root in roots)
        self._debug_source_provider_key = "manual_roots"
        manifest = self._sync_configured_debug_source_provider()
        self._refresh_debug_source_provider_options()
        self._save_config()
        return manifest

    def configure_debug_gdb_sources_text(
        self,
        text: str,
        *,
        root: str | Path | None = None,
    ) -> SourceManifest:
        self._debug_gdb_sources_text = str(text or "")
        if root is not None:
            self._debug_manual_source_roots = (Path(root).expanduser(),)
        self._debug_source_provider_key = "gdb_text"
        manifest = self._sync_configured_debug_source_provider()
        self._refresh_debug_source_provider_options()
        self._save_config()
        return manifest

    def configure_debug_dwarf_line_table_text(
        self,
        text: str,
        *,
        elf_path: str | Path | None = None,
        source_roots: tuple[str | Path, ...] | list[str | Path] | None = None,
    ) -> SourceManifest:
        self._debug_dwarf_line_table_text = str(text or "")
        if elf_path is not None:
            self._debug_dwarf_text_elf_path = Path(elf_path).expanduser()
        elif self._elf_path is not None:
            self._debug_dwarf_text_elf_path = self._elf_path
        if source_roots is not None:
            self._debug_manual_source_roots = tuple(Path(root).expanduser() for root in source_roots)
        self._debug_source_provider_key = "elf_dwarf"
        manifest = self._sync_configured_debug_source_provider()
        self._refresh_debug_source_provider_options()
        self._save_config()
        return manifest

    def _sync_configured_debug_source_provider(self) -> SourceManifest:
        self._sync_debug_source_manifest_preview()
        self._sync_debug_command_preview()
        self._refresh_hero()
        self._refresh_debug_variable_presets()
        manifest = self._debug_source_preview_manifest or self._empty_debug_source_manifest(
            self._debug_backend_display_name(),
            "源码配置未生成有效清单",
            (),
        )
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(f"源码来源：{self._debug_source_provider_label(self._debug_source_provider_key)}")
        return manifest

    def preview_debug_source_remap(
        self,
        *,
        missing_dir: str | Path,
        local_root: str | Path,
        persist: bool = False,
    ) -> SourcePathRemapPreview:
        manifest = self._debug_source_preview_manifest
        if manifest is None and hasattr(self, "_tab_debug_workbench"):
            manifest = self._tab_debug_workbench.source_manifest
        if manifest is None:
            raise ValueError("当前没有可重映射的源码清单")
        preview = preview_source_manifest_path_remap(
            manifest,
            missing_dir=missing_dir,
            local_root=local_root,
        )
        self._debug_source_remap_preview = preview
        self._debug_source_preview_manifest = preview.manifest
        if hasattr(self, "_tab_debug_workbench"):
            self._tab_debug_workbench.set_source_manifest(
                preview.manifest,
                mode=preview.manifest.provider,
            )
        self._sync_debug_command_preview()
        self._refresh_hero()
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(
                f"源码重映射预览：缺失 {preview.before_missing} -> {preview.after_missing}"
            )
        if persist:
            self._remember_debug_source_remap(preview)
            self._save_config()
        return preview

    def _remember_debug_source_remap(self, preview: SourcePathRemapPreview):
        record = {
            "provider_key": self._debug_source_provider_key,
            "missing_dir": preview.missing_dir,
            "local_root": str(preview.local_root),
        }
        self._debug_source_remaps = [
            item
            for item in self._debug_source_remaps
            if not (
                item.get("provider_key") == record["provider_key"]
                and item.get("missing_dir") == record["missing_dir"]
            )
        ]
        self._debug_source_remaps.append(record)

    def _apply_saved_debug_source_remaps(self, manifest: SourceManifest) -> SourceManifest:
        if not self._debug_source_remaps or not manifest.entries:
            return manifest
        current = manifest
        applied: list[SourcePathRemapPreview] = []
        skipped: list[str] = []
        for record in list(self._debug_source_remaps):
            if not self._debug_source_remap_record_matches(str(record.get("provider_key", "") or "")):
                continue
            missing_dir = str(record.get("missing_dir", "") or "")
            local_root = str(record.get("local_root", "") or "")
            if not missing_dir or not local_root:
                continue
            missing_dirs = {
                self._debug_source_remap_match_key(hint.missing_dir): hint.missing_dir
                for hint in source_manifest_missing_path_hints(current, max_hints=max(4, len(current.entries)))
            }
            matched_missing_dir = missing_dirs.get(self._debug_source_remap_match_key(missing_dir))
            label = self._debug_source_remap_label(missing_dir)
            if not matched_missing_dir:
                skipped.append(f"{label} 未命中")
                continue
            local_root_path = Path(local_root).expanduser()
            if not local_root_path.exists():
                skipped.append(f"{label} 本地根不存在")
                continue
            try:
                preview = preview_source_manifest_path_remap(
                    current,
                    missing_dir=matched_missing_dir,
                    local_root=local_root_path,
                )
            except Exception as exc:
                skipped.append(f"{label} 失败：{exc}")
                continue
            if preview.resolved_count <= 0:
                skipped.append(f"{label} 未找到文件")
                continue
            current = preview.manifest
            applied.append(preview)
        if not applied and not skipped:
            return manifest
        if applied:
            self._debug_source_remap_preview = applied[-1]
        return self._debug_source_manifest_with_remap_replay_diagnostics(current, applied, skipped)

    def _debug_source_remap_record_matches(self, provider_key: str) -> bool:
        current_key = str(self._debug_source_provider_key or "auto")
        if provider_key == current_key:
            return True
        keil_keys = {"auto", "keil"}
        return (
            self._debug_backend_kind == DebugBackendKind.KEIL
            and provider_key in keil_keys
            and current_key in keil_keys
        )

    def _debug_source_remap_match_key(self, value: str | Path) -> str:
        try:
            text = str(Path(value).expanduser())
        except (OSError, RuntimeError, ValueError):
            text = str(value)
        return os.path.normcase(text)

    def _debug_source_remap_label(self, value: str | Path) -> str:
        try:
            name = Path(value).name
        except (OSError, RuntimeError, ValueError):
            name = ""
        return name or str(value)

    def _debug_source_manifest_with_remap_replay_diagnostics(
        self,
        manifest: SourceManifest,
        applied: list[SourcePathRemapPreview],
        skipped: list[str],
    ) -> SourceManifest:
        remap_keys = {"重映射", "重映射命中", "重映射重放", "重映射跳过"}
        diagnostics = tuple((key, value) for key, value in manifest.diagnostics if key not in remap_keys)
        if applied:
            before_missing = applied[0].before_missing
            after_missing = sum(1 for entry in manifest.entries if not entry.exists)
            roots = tuple(dict.fromkeys(str(preview.local_root) for preview in applied))
            root_text = roots[0] if len(roots) == 1 else f"{len(roots)} 个源码根"
            diagnostics += (
                ("重映射重放", f"{len(applied)} 条，缺失 {before_missing} -> {after_missing}"),
                ("重映射", f"{sum(preview.remapped_count for preview in applied)} 项 -> {root_text}"),
                ("重映射命中", str(sum(preview.resolved_count for preview in applied))),
            )
        if skipped:
            skip_text = "；".join(skipped[:2])
            if len(skipped) > 2:
                skip_text += f" 等 {len(skipped)} 条"
            diagnostics += (("重映射跳过", skip_text),)
        metadata = dict(manifest.metadata or {})
        metadata.update(
            {
                "remap_replay_count": str(len(applied)),
                "remap_replay_skipped": str(len(skipped)),
                "remap_replay_resolved": str(sum(preview.resolved_count for preview in applied)),
            }
        )
        return SourceManifest(
            name=manifest.name,
            root=manifest.root,
            provider=manifest.provider,
            entries=manifest.entries,
            target_name=manifest.target_name,
            project_path=manifest.project_path,
            diagnostics=diagnostics,
            metadata=metadata,
        )

    def _sync_debug_source_manifest_preview(self):
        if not hasattr(self, "_tab_debug_workbench"):
            return
        tab = self._tab_debug_workbench
        if self._debug_backend_kind == DebugBackendKind.KEIL and self._debug_source_provider_key in {"auto", "keil"}:
            tab.restore_project_source_manifest()
            manifest = tab.source_manifest
            if manifest is not None:
                manifest = self._apply_saved_debug_source_remaps(manifest)
                self._debug_source_preview_manifest = manifest
                tab.set_source_manifest(manifest, mode=manifest.provider)
            else:
                self._debug_source_preview_manifest = None
            return
        manifest = self._build_debug_source_preview_manifest()
        manifest = self._apply_saved_debug_source_remaps(manifest)
        self._debug_source_preview_manifest = manifest
        tab.set_source_manifest(manifest, mode=manifest.provider)

    def _build_debug_source_preview_manifest(self) -> SourceManifest:
        provider_name = self._debug_backend_display_name()
        diagnostics: list[tuple[str, str]] = []
        provider_key = self._debug_source_provider_key
        if provider_key == "keil":
            project = getattr(self._tab_debug_workbench, "_project", None)
            if project is not None:
                from src.core.debug_sources import source_manifest_from_keil_project

                return source_manifest_from_keil_project(project)
            return self._empty_debug_source_manifest(
                provider_name,
                "当前没有可复用的 Keil 工程源码清单",
                diagnostics,
            )
        if provider_key == "compile_commands":
            return self._build_compile_commands_source_preview(provider_name, diagnostics)
        if provider_key == "manual_roots":
            return self._build_manual_roots_source_preview(provider_name, diagnostics)
        if provider_key == "elf_dwarf":
            if self._debug_dwarf_line_table_text.strip():
                return self._build_dwarf_text_source_preview(provider_name, diagnostics)
            return self._empty_debug_source_manifest(
                provider_name,
                "ELF/DWARF 需要显式导入 readelf -wl 文本，当前不会自动运行 readelf",
                diagnostics,
                provider="elf_dwarf_pending",
            )
        if provider_key == "gdb_text":
            if self._debug_gdb_sources_text.strip():
                return self._build_gdb_text_source_preview(provider_name, diagnostics)
            return self._empty_debug_source_manifest(
                provider_name,
                "GDB info sources 文本导入尚未接入",
                diagnostics,
                provider="gdb_text_pending",
            )
        existing = getattr(self._tab_debug_workbench, "source_manifest", None)
        if existing is not None and existing.source_count:
            entries = existing.entries
            return SourceManifest(
                name=f"{provider_name} 复用源码预览",
                root=existing.root,
                provider=f"{self._debug_backend_kind.value}_preview",
                entries=entries,
                project_path=existing.project_path,
                diagnostics=(
                    ("来源", existing.provider),
                    ("说明", "复用当前工作台源码树，仅用于非 Keil 后端占位预览"),
                    ("安全边界", "不启动进程、不连接探针、不写目标"),
                ) + tuple(diagnostics),
                metadata={"backend": self._debug_backend_kind.value, "preview": "reuse"},
            )
        compile_manifest = self._try_compile_commands_source_preview(provider_name, diagnostics)
        if compile_manifest.source_count:
            return compile_manifest
        roots_manifest = self._try_manual_roots_source_preview(provider_name)
        if roots_manifest.source_count:
            return roots_manifest
        return self._empty_debug_source_manifest(provider_name, "未找到 ELF/DWARF、compile_commands 或源码根", diagnostics)

    def _build_compile_commands_source_preview(
        self,
        provider_name: str,
        diagnostics: list[tuple[str, str]],
    ) -> SourceManifest:
        manifest = self._try_compile_commands_source_preview(provider_name, diagnostics)
        if manifest.source_count:
            return manifest
        return self._empty_debug_source_manifest(
            provider_name,
            "当前 ELF 附近未找到可用 compile_commands.json",
            diagnostics,
            provider="compile_commands_missing",
        )

    def _try_compile_commands_source_preview(
        self,
        provider_name: str,
        diagnostics: list[tuple[str, str]],
    ) -> SourceManifest:
        compile_commands = self._debug_compile_commands_path or self._find_compile_commands_file()
        if compile_commands is not None:
            try:
                return source_manifest_from_compile_commands(
                    compile_commands,
                    name=f"{provider_name} 编译数据库预览",
                )
            except Exception as exc:
                diagnostics.append(("compile_commands", f"不可用：{exc}"))
        return SourceManifest(
            name=f"{provider_name} 编译数据库预览",
            root=self._elf_path.parent if self._elf_path else None,
            provider="compile_commands_missing",
            entries=(),
            diagnostics=tuple(diagnostics),
            metadata={"backend": self._debug_backend_kind.value, "preview": "compile_commands"},
        )

    def _build_manual_roots_source_preview(
        self,
        provider_name: str,
        diagnostics: list[tuple[str, str]],
    ) -> SourceManifest:
        manifest = self._try_manual_roots_source_preview(provider_name)
        if manifest.source_count:
            return manifest
        return self._empty_debug_source_manifest(
            provider_name,
            "当前 ELF 附近未找到源码根",
            diagnostics,
            provider="manual_roots_missing",
        )

    def _try_manual_roots_source_preview(self, provider_name: str) -> SourceManifest:
        roots = self._debug_source_roots()
        if roots:
            return source_manifest_from_roots(
                roots,
                name=f"{provider_name} 源码根预览",
                provider=f"{self._debug_backend_kind.value}_roots_preview",
                max_files=1200,
            )
        return SourceManifest(
            name=f"{provider_name} 源码根预览",
            root=None,
            provider=f"{self._debug_backend_kind.value}_roots_preview",
            entries=(),
            diagnostics=(("源码根", "未发现"),),
            metadata={"backend": self._debug_backend_kind.value, "preview": "manual_roots"},
        )

    def _build_gdb_text_source_preview(
        self,
        provider_name: str,
        diagnostics: list[tuple[str, str]],
    ) -> SourceManifest:
        root = self._debug_source_roots()[0] if self._debug_source_roots() else None
        try:
            manifest = source_manifest_from_gdb_sources(
                self._debug_gdb_sources_text,
                root=root,
                name=f"{provider_name} GDB 文本预览",
            )
        except Exception as exc:
            return self._empty_debug_source_manifest(
                provider_name,
                f"GDB info sources 文本解析失败：{exc}",
                diagnostics,
                provider="gdb_text_error",
            )
        return SourceManifest(
            name=manifest.name,
            root=manifest.root,
            provider=manifest.provider,
            entries=manifest.entries,
            target_name=manifest.target_name,
            project_path=manifest.project_path,
            diagnostics=manifest.diagnostics + (
                ("输入方式", "显式 GDB 文本"),
                ("安全边界", "只解析已粘贴文本，不启动 GDB/OpenOCD/pyOCD"),
            ),
            metadata=dict(manifest.metadata or {}) | {
                "backend": self._debug_backend_kind.value,
                "preview": "gdb_text",
            },
        )

    def _build_dwarf_text_source_preview(
        self,
        provider_name: str,
        diagnostics: list[tuple[str, str]],
    ) -> SourceManifest:
        elf_path = self._debug_dwarf_text_elf_path or self._elf_path
        roots = self._debug_source_roots()
        try:
            manifest = source_manifest_from_readelf_line_table_text(
                self._debug_dwarf_line_table_text,
                elf_path=elf_path,
                source_roots=roots,
                name=f"{provider_name} DWARF 文本预览",
            )
        except Exception as exc:
            return self._empty_debug_source_manifest(
                provider_name,
                f"readelf -wl 文本解析失败：{exc}",
                diagnostics,
                provider="elf_dwarf_error",
            )
        return SourceManifest(
            name=manifest.name,
            root=manifest.root,
            provider=manifest.provider,
            entries=manifest.entries,
            target_name=manifest.target_name,
            project_path=manifest.project_path,
            diagnostics=manifest.diagnostics + (
                ("输入方式", "显式 readelf -wl 文本"),
                ("安全边界", "只解析已导入文本，不自动运行 readelf"),
            ),
            metadata=dict(manifest.metadata or {}) | {
                "backend": self._debug_backend_kind.value,
                "preview": "elf_dwarf_text",
            },
        )

    def _empty_debug_source_manifest(
        self,
        provider_name: str,
        reason: str,
        diagnostics: list[tuple[str, str]] | tuple[tuple[str, str], ...],
        *,
        provider: str | None = None,
    ) -> SourceManifest:
        return SourceManifest(
            name=f"{provider_name} 源码预览",
            root=self._elf_path.parent if self._elf_path else None,
            provider=provider or f"{self._debug_backend_kind.value}_preview",
            entries=(),
            diagnostics=(
                ("后端", provider_name),
                ("源码来源", reason),
                ("安全边界", "不会启动进程、连接探针或写目标"),
            ) + tuple(diagnostics),
            metadata={
                "backend": self._debug_backend_kind.value,
                "provider_key": self._debug_source_provider_key,
                "preview": "empty",
            },
        )

    def _debug_source_provider_label(self, provider_key: str) -> str:
        labels = {
            "auto": "自动",
            "keil": "Keil 工程",
            "compile_commands": "编译数据库",
            "manual_roots": "源码根",
            "elf_dwarf": "ELF/DWARF",
            "gdb_text": "GDB 文本",
        }
        return labels.get(str(provider_key), str(provider_key))

    def _debug_source_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        for root in self._debug_manual_source_roots:
            if root not in roots:
                roots.append(root)
        if self._elf_path is not None:
            for candidate in (
                self._elf_path.parent,
                self._elf_path.parent.parent,
                self._elf_path.parent.parent.parent,
            ):
                if candidate.exists() and candidate not in roots:
                    roots.append(candidate)
        return tuple(roots)

    def _find_compile_commands_file(self) -> Path | None:
        for root in self._debug_source_roots():
            for candidate in (
                root / "compile_commands.json",
                root / "build" / "compile_commands.json",
                root / "cmake-build-debug" / "compile_commands.json",
            ):
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if resolved.exists():
                    return resolved
        return None

    def _discover_debug_backend_for_workbench(self):
        if not hasattr(self, "_tab_debug_workbench"):
            return
        tab = self._tab_debug_workbench
        previous = tab.debug_status
        tab.set_debug_controls_ready(False)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(f"正在发现 {self._debug_backend_display_name()}...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        diagnostics = []
        pc_location = None
        try:
            snapshot = self._debug_backend.discover(
                project_path=previous.project_path,
                target_name=tab.debug_status.target_name,
                previous_status=previous,
            )
            status = snapshot.status
            pc_location = snapshot.pc_location
            diagnostics = snapshot.diagnostic_rows()
            self._debug_backend_diagnostics = tuple(diagnostics)
            self._debug_remote_breakpoint_snapshot = snapshot.remote_breakpoint_snapshot
            self._debug_backend_snapshot_record = snapshot.to_record()
            self._debug_session_controller.apply_backend_snapshot(snapshot)
            self._mark_local_breakpoints_from_remote_snapshot(snapshot.remote_breakpoint_snapshot)
        except Exception as exc:
            message = f"{self._debug_backend_display_name()} 预检失败：{exc}"
            status = make_debug_status(
                state=DebugRuntimeState.ERROR,
                backend=self._debug_backend_kind,
                detail=message,
                project_path=previous.project_path,
                target_name=previous.target_name,
                error=message,
            )
            diagnostics = self._debug_workbench_error_diagnostics(message)
            self._debug_backend_diagnostics = tuple(diagnostics)
            self._debug_session_controller.mark_error(
                message,
                project_path=previous.project_path,
                target_name=previous.target_name,
            )
        finally:
            QApplication.restoreOverrideCursor()
        tab.set_debug_status(status, controls_ready=True)
        tab.set_pc_evidence(pc_location)
        tab.set_backend_diagnostics(self._with_debug_session_contract_diagnostics(diagnostics))
        self._refresh_debug_live_loop_status()
        self._sync_debug_command_preview()
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(status.detail)
        self._refresh_hero()

    def _discover_keil_for_debug_workbench(self):
        self._discover_debug_backend_for_workbench()

    def _apply_debug_backend_snapshot(self, snapshot) -> None:
        self._debug_backend_diagnostics = tuple(snapshot.diagnostic_rows())
        self._debug_remote_breakpoint_snapshot = snapshot.remote_breakpoint_snapshot
        self._debug_backend_snapshot_record = snapshot.to_record()
        self._debug_session_controller.apply_backend_snapshot(snapshot)
        self._tab_debug_workbench.set_debug_status(snapshot.status, controls_ready=True)
        self._tab_debug_workbench.set_pc_evidence(snapshot.pc_location)
        self._mark_local_breakpoints_from_remote_snapshot(snapshot.remote_breakpoint_snapshot)
        self._refresh_debug_live_loop_status()

    def _connect_debug_backend_read_only_for_workbench(self):
        if not hasattr(self, "_tab_debug_workbench"):
            return
        tab = self._tab_debug_workbench
        previous = tab.debug_status
        tab.set_debug_controls_ready(False)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(f"正在读取 {self._debug_backend_display_name()} 只读会话快照...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        diagnostics = []
        pc_location = None
        try:
            snapshot = self._debug_backend.read_only_session_snapshot(
                project_path=previous.project_path,
                target_name=tab.debug_status.target_name,
                previous_status=previous,
                attempt_connection=True,
                query_status=True,
            )
            status = snapshot.status
            pc_location = snapshot.pc_location
            diagnostics = snapshot.diagnostic_rows()
            self._debug_backend_diagnostics = tuple(diagnostics)
            self._debug_remote_breakpoint_snapshot = snapshot.remote_breakpoint_snapshot
            self._debug_backend_snapshot_record = snapshot.to_record()
            self._debug_session_controller.apply_backend_snapshot(snapshot)
            self._mark_local_breakpoints_from_remote_snapshot(snapshot.remote_breakpoint_snapshot)
        except Exception as exc:
            message = f"{self._debug_backend_display_name()} 只读连接失败：{exc}"
            status = make_debug_status(
                state=DebugRuntimeState.ERROR,
                backend=self._debug_backend_kind,
                detail=message,
                project_path=previous.project_path,
                target_name=previous.target_name,
                error=message,
            )
            diagnostics = self._debug_workbench_error_diagnostics(message)
            self._debug_backend_diagnostics = tuple(diagnostics)
            self._debug_session_controller.mark_error(
                message,
                project_path=previous.project_path,
                target_name=previous.target_name,
            )
        finally:
            QApplication.restoreOverrideCursor()
        tab.set_debug_status(status, controls_ready=True)
        tab.set_pc_evidence(pc_location)
        tab.set_backend_diagnostics(self._with_debug_session_contract_diagnostics(diagnostics))
        self._refresh_debug_live_loop_status()
        self._sync_debug_command_preview()
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(status.detail)
        self._refresh_hero()

    def _connect_keil_read_only_for_debug_workbench(self):
        self._connect_debug_backend_read_only_for_workbench()

    def _refresh_remote_breakpoints_from_workbench(self):
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            self._show_warning("远端断点刷新", "当前调试后端不是 Keil / UVSOCK。")
            return
        if not hasattr(self._debug_backend, "read_only_session_snapshot"):
            self._show_warning("远端断点刷新", "当前后端尚未提供只读断点快照。")
            return
        self._connect_debug_backend_read_only_for_workbench()

    def _control_keil_runtime_from_workbench(self, action_key: str):
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            self._show_warning("Keil 运行控制", "当前调试后端不是 Keil / UVSOCK。")
            return
        method_name = {
            "halt": "halt_target",
            "run": "run_target",
            "reset": "reset_target",
            "step": "step_target",
            "step_over": "step_over_target",
        }.get(action_key, "")
        if not hasattr(self._debug_backend, method_name):
            self._show_warning("Keil 运行控制", "当前 Keil 后端尚未提供运行控制执行器。")
            return
        tab = self._tab_debug_workbench
        status = tab.debug_status
        if action_key == "halt" and status.state != DebugRuntimeState.RUNNING:
            self._show_warning("Keil 暂停", "目标当前不是运行中状态。")
            return
        if action_key == "run" and status.state != DebugRuntimeState.PAUSED:
            self._show_warning("Keil 运行", "目标当前不是暂停状态。")
            return
        if action_key == "reset" and status.state not in {
            DebugRuntimeState.KEIL_ATTACHED,
            DebugRuntimeState.PAUSED,
            DebugRuntimeState.RUNNING,
        }:
            self._show_warning("Keil 复位", "目标当前尚未连接调试会话。")
            return
        if action_key == "step" and status.state != DebugRuntimeState.PAUSED:
            self._show_warning("Keil 单步", "目标当前不是暂停状态。")
            return
        if action_key == "step_over" and status.state != DebugRuntimeState.PAUSED:
            self._show_warning("Keil 跨过", "目标当前不是暂停状态。")
            return
        label = {
            "halt": "暂停",
            "run": "运行",
            "reset": "复位",
            "step": "单步",
            "step_over": "跨过",
        }.get(action_key, action_key)
        message = (
            f"工程：{status.project_path or '--'}\n"
            f"Target：{status.target_name or '--'}\n"
            f"端口：{self._debug_uvsock_port}\n\n"
            f"这会通过 Keil/UVSOCK 真实{label} MCU，并在执行后读取目标状态。"
        )
        if not ask_pcl_confirmation(
            self,
            f"确认 Keil {label}",
            message,
            confirm_text=label,
            cancel_text="取消",
            kind="warning",
        ):
            return

        tab.set_debug_controls_ready(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(f"正在通过 Keil {label}目标...")
        try:
            result = getattr(self._debug_backend, method_name)(
                project_path=status.project_path,
                target_name=status.target_name,
            )
        except Exception as exc:
            result = None
            error = str(exc)
        else:
            error = ""
        finally:
            QApplication.restoreOverrideCursor()
            tab.set_debug_controls_ready(True)

        if result is None:
            self._debug_last_runtime_control_result = None
            diagnostics = tuple(getattr(self, "_debug_backend_diagnostics", ())) + ((f"Keil {label}", f"失败：{error}"),)
            self._debug_backend_diagnostics = diagnostics
            self._refresh_debug_workbench_diagnostics()
            self._show_warning(f"Keil {label}失败", error)
            if hasattr(self, "_sb_label"):
                self._sb_label.setText(error)
            return

        self._debug_last_runtime_control_result = result
        snapshot = getattr(result, "snapshot", None)
        if snapshot is not None:
            status = snapshot.status
            diagnostics = snapshot.diagnostic_rows()
            self._debug_backend_diagnostics = tuple(diagnostics) + self._keil_runtime_control_diagnostics(result)
            self._debug_remote_breakpoint_snapshot = snapshot.remote_breakpoint_snapshot
            self._debug_backend_snapshot_record = snapshot.to_record()
            self._debug_session_controller.apply_backend_snapshot(snapshot)
            tab.set_debug_status(status, controls_ready=True)
            tab.set_pc_evidence(snapshot.pc_location)
            self._mark_local_breakpoints_from_remote_snapshot(snapshot.remote_breakpoint_snapshot)
        else:
            self._debug_backend_diagnostics = tuple(getattr(self, "_debug_backend_diagnostics", ())) + self._keil_runtime_control_diagnostics(result)
        self._refresh_debug_workbench_diagnostics()
        self._sync_debug_command_preview()
        summary = result.summary()
        if getattr(result, "succeeded", False):
            self._show_info(f"Keil {label}完成", summary)
        else:
            self._show_warning(f"Keil {label}失败", summary)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(summary)
        self._refresh_hero()

    def _run_keil_to_cursor_from_workbench(self):
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            self._show_warning("Keil 运行到光标", "当前调试后端不是 Keil / UVSOCK。")
            return
        if not hasattr(self._debug_backend, "run_to_cursor"):
            self._show_warning("Keil 运行到光标", "当前 Keil 后端尚未提供运行到光标执行器。")
            return
        tab = self._tab_debug_workbench
        status = tab.debug_status
        if status.state != DebugRuntimeState.PAUSED:
            self._show_warning("Keil 运行到光标", "目标必须先暂停，才能运行到当前光标行。")
            return
        cursor_location = tab.current_cursor_location()
        if cursor_location is None:
            self._show_warning("Keil 运行到光标", "请先在源码视图中选择一个有效源码行。")
            return
        source_path, line = cursor_location
        transaction = transaction_by_key(getattr(tab, "_command_transactions", ()), "run_to_cursor")
        profile = self._make_current_keil_profile()
        axf_path = profile.axf_path if profile is not None and profile.axf_exists else self._current_debug_axf_path()
        if not self._confirm_keil_run_to_cursor(source_path, line, status, axf_path=axf_path):
            return

        tab.set_debug_controls_ready(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(f"正在通过 Keil 运行到 {source_path.name}:{line}...")
        try:
            result = self._debug_backend.run_to_cursor(
                source_path=source_path,
                line=line,
                project_path=status.project_path,
                target_name=status.target_name,
                timeout_s=5.0,
                reset_before_run=False,
            )
        except Exception as exc:
            result = KeilRunToCursorResult(
                request=KeilRunToCursorRequest(
                    project_path=status.project_path,
                    target_name=status.target_name,
                    source_path=source_path,
                    line=line,
                    axf_path=axf_path,
                ),
                attempted=True,
                succeeded=False,
                error=str(exc),
            )
        finally:
            QApplication.restoreOverrideCursor()
            tab.set_debug_controls_ready(True)

        self._debug_last_run_to_cursor_result = result
        if result.hit_pc is not None:
            hit_path = result.hit_pc.path or source_path
            if result.hit_pc.line is not None:
                tab.show_source_location(hit_path, int(result.hit_pc.line))
            tab.set_debug_status(
                make_debug_status(
                    state=DebugRuntimeState.PAUSED,
                    backend=DebugBackendKind.KEIL,
                    detail=result.summary(),
                    project_path=status.project_path,
                    target_name=status.target_name,
                    current_pc_line=result.hit_pc.line,
                    capabilities=status.capabilities,
                ),
                controls_ready=True,
            )
            tab.set_pc_evidence(result.hit_pc)
            if not isinstance(self._debug_backend_snapshot_record, dict):
                self._debug_backend_snapshot_record = {
                    "snapshot_id": f"ui-run-to-cursor-{int(time.time() * 1000)}",
                    "backend": DebugBackendKind.KEIL.value,
                    "adapter_name": self._debug_backend_display_name(),
                    "state": DebugRuntimeState.PAUSED.value,
                    "connection_established": True,
                    "read_only": False,
                    "target_running": False,
                    "project_path": str(status.project_path or ""),
                    "target_name": status.target_name,
                }
            self._debug_backend_snapshot_record["pc_location"] = result.hit_pc.to_record()
        snapshot = result.after_cleanup_snapshot or result.after_set_snapshot or result.before_snapshot
        if snapshot is not None:
            self._debug_remote_breakpoint_snapshot = snapshot
            self._mark_local_breakpoints_from_remote_snapshot(snapshot)
            if isinstance(self._debug_backend_snapshot_record, dict):
                self._debug_backend_snapshot_record["remote_breakpoint_snapshot"] = snapshot.to_record()
                self._debug_backend_snapshot_record["remote_breakpoint_snapshot_id"] = snapshot.snapshot_id
                self._debug_backend_snapshot_record["remote_breakpoint_complete"] = snapshot.complete
        self._append_keil_run_to_cursor_audit(result, transaction)
        if transaction is not None:
            self._debug_command_history.record(
                transaction,
                event="executed" if result.succeeded else "failed",
                source="ui_run_to_cursor",
            )
            tab.set_command_history_entries(self._debug_command_history.recent(limit=5))
        self._refresh_debug_workbench_diagnostics()
        self._sync_debug_command_preview()
        summary = result.summary()
        if result.succeeded:
            self._show_info("Keil 运行到光标完成", summary)
        else:
            self._show_warning("Keil 运行到光标失败", summary)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(summary)
        self._refresh_hero()

    def _confirm_keil_run_to_cursor(
        self,
        source_path: Path,
        line: int,
        status,
        *,
        axf_path: Path | None,
    ) -> bool:
        axf_text = str(axf_path) if axf_path else "未找到 AXF，无法解析源码行地址"
        message = (
            f"工程：{status.project_path or '--'}\n"
            f"Target：{status.target_name or '--'}\n"
            f"源码：{source_path}\n"
            f"行号：{line}\n"
            f"AXF：{axf_text}\n"
            f"端口：{self._debug_uvsock_port}\n\n"
            "这会通过 Keil/UVSOCK 真实运行 MCU 到当前源码行。"
            "LoopMaster 会优先复用已有断点，否则创建临时断点，命中后回读 PC 并清理临时断点。"
        )
        return ask_pcl_confirmation(
            self,
            "确认 Keil 运行到光标",
            message,
            confirm_text="运行到光标",
            cancel_text="取消",
            kind="warning",
        )

    def _build_keil_project_for_workbench(self):
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            self._show_warning("Keil 构建", "当前调试后端不是 Keil / UVSOCK。")
            return
        if not hasattr(self._debug_backend, "build_project"):
            self._show_warning("Keil 构建", "当前 Keil 后端尚未提供构建执行器。")
            return
        tab = self._tab_debug_workbench
        status = tab.debug_status
        if not status.project_path:
            self._show_warning("Keil 构建", "请先打开 Keil 工程。")
            return
        profile = self._make_current_keil_profile()
        if profile is None:
            self._show_warning("Keil 构建", "无法生成 Keil 调试档案。")
            return
        message = (
            f"工程：{profile.project_path or '--'}\n"
            f"Target：{profile.target_name or '--'}\n"
            f"AXF：{profile.axf_path or '--'}\n"
            f"日志：{profile.build_plan.log_path or '--'}\n\n"
            f"将执行：{profile.build_plan.display_command or '--'}"
        )
        if not ask_pcl_confirmation(
            self,
            "确认 Keil 构建",
            message,
            confirm_text="构建",
            cancel_text="取消",
            kind="warning",
        ):
            return

        tab.set_debug_controls_ready(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText("正在调用 Keil 构建工程...")
        try:
            result = self._debug_backend.build_project(
                project_path=profile.project_path,
                target_name=profile.target_name,
                timeout=180.0,
            )
        except Exception as exc:
            result = KeilBuildResult(
                plan=profile.build_plan,
                attempted=True,
                succeeded=False,
                log_path=profile.build_plan.log_path,
                axf_path=profile.axf_path,
                axf_exists=bool(profile.axf_path and profile.axf_path.exists()),
                error=str(exc),
            )
        finally:
            QApplication.restoreOverrideCursor()
            tab.set_debug_controls_ready(True)

        self._debug_keil_build_result = result
        self._debug_keil_profile = self._make_current_keil_profile()
        self._refresh_debug_workbench_diagnostics()
        if result.succeeded:
            if result.axf_path and result.axf_path.exists():
                self._recent_elf_path = result.axf_path
                self._refresh_recent_elf_button()
            self._show_info("Keil 构建完成", result.summary())
        else:
            self._show_warning("Keil 构建失败", result.summary())
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(result.summary())

    def _launch_keil_uvsock_for_workbench(self):
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            self._show_warning("启动 Keil", "当前调试后端不是 Keil / UVSOCK。")
            return
        if not hasattr(self._debug_backend, "launch_uvsock"):
            self._show_warning("启动 Keil", "当前 Keil 后端尚未提供启动执行器。")
            return
        tab = self._tab_debug_workbench
        status = tab.debug_status
        if not status.project_path:
            self._show_warning("启动 Keil", "请先打开 Keil 工程。")
            return
        profile = self._make_current_keil_profile()
        if profile is None:
            self._show_warning("启动 Keil", "无法生成 Keil 调试档案。")
            return
        message = (
            f"工程：{profile.project_path or '--'}\n"
            f"Target：{profile.target_name or '--'}\n"
            f"端口：{profile.port}\n\n"
            f"将执行：{profile.launch_plan.display_command or '--'}"
        )
        if not ask_pcl_confirmation(
            self,
            "确认启动 Keil / UVSOCK",
            message,
            confirm_text="启动",
            cancel_text="取消",
            kind="warning",
        ):
            return

        tab.set_debug_controls_ready(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText("正在启动 Keil / UVSOCK...")
        try:
            result = self._debug_backend.launch_uvsock(
                project_path=profile.project_path,
                target_name=profile.target_name,
            )
        except Exception as exc:
            result = UvscLaunchResult(plan=profile.launch_plan, launched=False, error=str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            tab.set_debug_controls_ready(True)

        self._debug_keil_launch_result = result
        self._debug_keil_profile = self._make_current_keil_profile()
        self._refresh_debug_workbench_diagnostics()
        if result.launched:
            message = f"已拉起 uVision 进程，PID={result.pid or '--'}。这只表示进程启动，需点击连接确认 UVSOCK/Debug 状态。"
            self._show_info("Keil 已启动", message)
        else:
            message = result.error or "uVision 启动失败。"
            self._show_warning("Keil 启动失败", message)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(message)

    def _run_keil_auto_debug_from_workbench(self):
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            self._show_warning("Keil 自动调试", "当前调试后端不是 Keil / UVSOCK。")
            return
        required = (
            "debug_profile",
            "build_project",
            "launch_uvsock",
            "read_only_session_snapshot",
            "read_live_variable",
            "write_live_variable",
        )
        missing = [name for name in required if not hasattr(self._debug_backend, name)]
        if missing:
            self._show_warning("Keil 自动调试", f"当前 Keil 后端缺少执行器：{', '.join(missing)}。")
            return
        tab = self._tab_debug_workbench
        status = tab.debug_status
        if not status.project_path:
            if not self._load_default_keil_debug_profile(silent=True):
                self._show_warning("Keil 自动调试", "请先打开 Keil 工程，或先保存一个 Keil 调试档案。")
                return
            status = tab.debug_status
        profile = self._make_current_keil_profile()
        if profile is None:
            self._show_warning("Keil 自动调试", "无法生成 Keil 调试档案。")
            return
        preset_profile = self._current_keil_variable_preset_profile()
        expression, value_text = keil_live_write_seed(preset_profile)
        message = (
            f"工程：{profile.project_path or '--'}\n"
            f"Target：{profile.target_name or '--'}\n"
            f"AXF：{profile.axf_path or '--'}\n"
            f"变量：{expression}\n"
            f"写入值：{value_text or '--'}\n\n"
            "将按顺序执行：构建缺失 AXF -> 启动或复用 Keil/UVSOCK -> 等待连接 -> 写前读取 -> 写入并回读变量。\n"
            "这会启动外部 Keil 进程，并可能改变真实 MCU 调试会话中的 RAM 变量。"
        )
        if not ask_pcl_confirmation(
            self,
            "确认 Keil 自动调试",
            message,
            confirm_text="开始",
            cancel_text="取消",
            kind="warning",
        ):
            return

        request = KeilAutoDebugRequest(
            project_path=profile.project_path or status.project_path,
            target_name=profile.target_name or status.target_name,
            build_if_missing=True,
            launch_if_needed=True,
            write_smoke=True,
            expression=expression,
            value_text=value_text,
            connection_name="LoopMasterAutoDebug",
        )
        tab.set_debug_controls_ready(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText("正在执行 Keil 自动调试事务...")
        try:
            result = run_keil_auto_debug_transaction(self._debug_backend, request)
        except Exception as exc:
            result = KeilAutoDebugResult(request=request, profile=profile, error=str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            tab.set_debug_controls_ready(True)

        self._debug_keil_auto_debug_result = result
        self._debug_keil_profile = result.profile or self._make_current_keil_profile()
        self._debug_keil_build_result = result.build or getattr(self, "_debug_keil_build_result", None)
        self._debug_keil_launch_result = result.launch or getattr(self, "_debug_keil_launch_result", None)
        if result.snapshot is not None:
            self._apply_debug_backend_snapshot(result.snapshot)
        if result.read is not None:
            self._debug_last_live_read_result = result.read
        if result.write is not None:
            self._debug_last_live_write_result = result.write
            self._append_keil_live_write_audit(result.write, baseline_read=result.read)
        self._refresh_debug_workbench_diagnostics()
        self._sync_debug_command_preview()
        summary = result.summary()
        if result.succeeded:
            self._show_info("Keil 自动调试完成", summary)
        else:
            self._show_warning("Keil 自动调试失败", summary)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(summary)
        self._refresh_hero()

    def _sync_keil_breakpoints_from_workbench(self):
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            self._show_warning("Keil 断点同步", "当前调试后端不是 Keil / UVSOCK。")
            return
        if not hasattr(self._debug_backend, "sync_breakpoints"):
            self._show_warning("Keil 断点同步", "当前 Keil 后端尚未提供断点同步执行器。")
            return
        tab = self._tab_debug_workbench
        status = tab.debug_status
        if status.state not in {
            DebugRuntimeState.KEIL_ATTACHED,
            DebugRuntimeState.PAUSED,
            DebugRuntimeState.RUNNING,
        }:
            self._show_warning("Keil 断点同步", "请先连接 Keil 调试会话。")
            return
        remote_snapshot = getattr(self, "_debug_remote_breakpoint_snapshot", None)
        remote_complete = bool(remote_snapshot is not None and getattr(remote_snapshot, "complete", False))
        remote_breakpoints = getattr(remote_snapshot, "breakpoints", ()) if remote_complete else ()
        local_breakpoints = tab.local_breakpoints()
        if not local_breakpoints and not remote_breakpoints:
            if remote_snapshot is not None and not remote_complete:
                self._show_warning("Keil 断点同步", "当前没有本地断点，且 Keil 远端断点快照不完整，不能安全清理远端断点。")
            else:
                self._show_warning("Keil 断点同步", "当前没有本地断点，也没有可同步的 Keil 远端断点。")
            return
        transaction = transaction_by_key(getattr(tab, "_command_transactions", ()), "sync_breakpoints")
        profile = self._make_current_keil_profile()
        axf_path = profile.axf_path if profile is not None and profile.axf_exists else None
        request = build_keil_breakpoint_sync_request_from_state(
            project_path=status.project_path,
            target_name=status.target_name,
            local_breakpoints=local_breakpoints,
            remote_breakpoints=remote_breakpoints,
            source_paths=tab.local_source_paths(),
            transaction_id=getattr(transaction, "transaction_id", ""),
            connection_name="LoopMasterBreakpointSync",
            remote_snapshot_complete=remote_complete,
            axf_path=axf_path,
        )
        if not self._confirm_keil_breakpoint_sync(request, status):
            return

        tab.set_debug_controls_ready(False)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText("正在通过 Keil 同步断点...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self._debug_backend.sync_breakpoints(request)
        except Exception as exc:
            result = KeilBreakpointSyncResult(
                request=request,
                commands=(),
                remote_snapshot=None,
                error=str(exc),
            )
        finally:
            QApplication.restoreOverrideCursor()
            tab.set_debug_controls_ready(True)

        self._debug_last_breakpoint_sync_result = result
        if result.remote_snapshot is not None:
            self._debug_remote_breakpoint_snapshot = result.remote_snapshot
            if isinstance(self._debug_backend_snapshot_record, dict):
                self._debug_backend_snapshot_record["remote_breakpoint_snapshot"] = result.remote_snapshot.to_record()
                self._debug_backend_snapshot_record["remote_breakpoint_snapshot_id"] = result.remote_snapshot.snapshot_id
        self._mark_keil_breakpoint_sync_result(result)
        self._append_keil_breakpoint_sync_audit(result, transaction)
        if transaction is not None:
            self._debug_command_history.record(
                transaction,
                event="executed" if getattr(result, "completed", result.succeeded) else "failed",
                source="ui_breakpoint_sync",
            )
            tab.set_command_history_entries(self._debug_command_history.recent(limit=5))
        self._refresh_debug_workbench_diagnostics()
        self._sync_debug_command_preview()
        summary = result.summary()
        if result.succeeded:
            self._show_info("Keil 断点同步完成", summary)
        elif getattr(result, "partial", False):
            self._show_warning("Keil 断点同步部分完成", summary)
        elif getattr(result, "blocked_by_limits", False):
            self._show_warning("Keil 断点同步受限未执行", summary)
        else:
            self._show_warning("Keil 断点同步失败", summary)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(summary)
        self._refresh_hero()

    def _confirm_keil_breakpoint_sync(self, request: KeilBreakpointSyncRequest, status) -> bool:
        counts = self._keil_breakpoint_operation_counts(request)
        invalid = counts.get("invalid", 0)
        active = counts.get("active", 0)
        mode = "完整差分同步" if request.remote_snapshot_complete else "推送本地断点"
        detail = (
            f"工程：{status.project_path or '--'}\n"
            f"Target：{status.target_name or '--'}\n"
            f"模式：{mode}\n"
            f"新增：{counts.get('add', 0)}  删除：{counts.get('remove', 0)}  启用：{counts.get('enable', 0)}\n"
            f"禁用：{counts.get('disable', 0)}  条件更新：{counts.get('update_condition', 0)}  无变化：{counts.get('noop', 0)}\n"
            f"受限：{invalid}\n\n"
        )
        detail += self._keil_breakpoint_address_confirmation_text(request)
        detail += self._keil_breakpoint_limited_confirmation_text(request)
        if not request.remote_snapshot_complete:
            detail += "当前 Keil 远端断点枚举还未完成，本次不会删除远端断点，只把本地断点提交到 Keil。\n"
        if active == 0:
            detail += "本地和远端断点没有需要修改的差异，将只刷新本地验证状态。"
        else:
            detail += "这会通过 UVSOCK 修改真实 Keil 调试会话里的断点。"
        return ask_pcl_confirmation(
            self,
            "确认 Keil 断点同步",
            detail,
            confirm_text="同步",
            cancel_text="取消",
            kind="warning",
        )

    @staticmethod
    def _keil_breakpoint_address_confirmation_text(request: KeilBreakpointSyncRequest) -> str:
        if request.axf_path is None:
            return ""
        address_ops = [
            operation for operation in request.operations
            if operation.action in {KeilBreakpointSyncAction.ADD, KeilBreakpointSyncAction.UPDATE_CONDITION}
        ]
        resolved = [operation for operation in address_ops if operation.address is not None]
        unresolved = [operation for operation in address_ops if operation.address is None]
        samples = [
            f"{Path(operation.path).name}:{operation.line} -> 0x{int(operation.address):08X}"
            for operation in resolved[:3]
        ]
        text = (
            f"AXF：{request.axf_path}\n"
            f"地址解析：{len(resolved)} 已解析 / {len(unresolved)} 未解析\n"
        )
        if samples:
            text += f"地址样例：{'；'.join(samples)}\n"
        if unresolved:
            text += "存在未解析源码行，将回退到 Keil 源码行表达式。\n"
        return text + "\n"

    @staticmethod
    def _keil_breakpoint_limited_confirmation_text(request: KeilBreakpointSyncRequest) -> str:
        limited = [operation for operation in request.operations if not operation.valid]
        if not limited:
            return ""
        action_labels = {
            KeilBreakpointSyncAction.ADD: "新增",
            KeilBreakpointSyncAction.REMOVE: "删除",
            KeilBreakpointSyncAction.ENABLE: "启用",
            KeilBreakpointSyncAction.DISABLE: "停用",
            KeilBreakpointSyncAction.UPDATE_CONDITION: "条件",
            KeilBreakpointSyncAction.NOOP: "无变化",
        }
        lines = [f"受限操作：{len(limited)} 条不会发送到 Keil。"]
        for operation in limited[:4]:
            label = action_labels.get(operation.action, operation.action.value)
            reason = operation.reason or "该断点操作尚未验证"
            lines.append(f"- {Path(operation.path).name}:{operation.line} {label}：{reason}")
        if len(limited) > 4:
            lines.append(f"- 另有 {len(limited) - 4} 条受限操作")
        return "\n".join(lines) + "\n\n"

    @staticmethod
    def _keil_breakpoint_operation_counts(request: KeilBreakpointSyncRequest) -> dict[str, int]:
        counts = {
            "add": 0,
            "remove": 0,
            "enable": 0,
            "disable": 0,
            "update_condition": 0,
            "noop": 0,
            "invalid": 0,
            "active": 0,
        }
        for operation in request.operations:
            key = operation.action.value
            counts[key] = counts.get(key, 0) + 1
            if not operation.valid:
                counts["invalid"] += 1
            if operation.action != KeilBreakpointSyncAction.NOOP:
                counts["active"] += 1
        return counts

    def _mark_keil_breakpoint_sync_result(self, result: KeilBreakpointSyncResult) -> None:
        tab = self._tab_debug_workbench
        failed_by_key: dict[tuple[str, int], str] = {}
        succeeded_by_key: set[tuple[str, int]] = set()
        succeeded_address_by_key: dict[tuple[str, int], int] = {}
        for command in result.commands:
            key = self._keil_breakpoint_sync_key(command.operation.path, command.operation.line)
            if command.succeeded:
                succeeded_by_key.add(key)
                if command.operation.address is not None:
                    succeeded_address_by_key[key] = int(command.operation.address)
                continue
            failed_by_key[key] = command.error or command.operation.reason or "Keil 未接受该断点命令"
        remote_complete = bool(getattr(result.remote_snapshot, "complete", False))
        remote_by_key = {
            self._keil_breakpoint_sync_key(item.path, item.line): item
            for item in getattr(result.remote_snapshot, "breakpoints", ()) or ()
            if remote_complete and getattr(item, "path", None) is not None and getattr(item, "line", 0)
        }
        remote_by_address = {
            int(address): item
            for item in getattr(result.remote_snapshot, "breakpoints", ()) or ()
            for address in (getattr(item, "address", None),)
            if address is not None
        }
        completed = bool(getattr(result, "completed", result.succeeded))
        for breakpoint in tab.local_breakpoints():
            key = self._keil_breakpoint_sync_key(breakpoint.path, breakpoint.line)
            if key in failed_by_key:
                tab.set_breakpoint_verification(
                    breakpoint.line,
                    path=breakpoint.path,
                    verified=False,
                    message=failed_by_key[key],
                )
            elif completed and key in remote_by_key:
                tab.set_breakpoint_verification(
                    breakpoint.line,
                    path=breakpoint.path,
                    verified=True,
                    message=self._keil_remote_breakpoint_evidence_text(remote_by_key[key], "Keil 已回读该断点"),
                )
            elif completed and succeeded_address_by_key.get(key) in remote_by_address:
                remote = remote_by_address[int(succeeded_address_by_key[key])]
                tab.set_breakpoint_verification(
                    breakpoint.line,
                    path=breakpoint.path,
                    verified=True,
                    message=self._keil_remote_breakpoint_evidence_text(remote, "Keil 已按地址回读该断点"),
                )
            elif completed and key in succeeded_by_key:
                tab.set_breakpoint_verification(
                    breakpoint.line,
                    path=breakpoint.path,
                    verified=remote_complete,
                    message="Keil 已接受命令，等待断点列表回读" if not remote_complete else "Keil 已同步",
                )

    def _mark_local_breakpoints_from_remote_snapshot(self, snapshot) -> None:
        if snapshot is None or not getattr(snapshot, "complete", False):
            return
        if not hasattr(self, "_tab_debug_workbench"):
            return
        tab = self._tab_debug_workbench
        remote_by_key = {
            self._keil_breakpoint_sync_key(item.path, item.line): item
            for item in getattr(snapshot, "breakpoints", ()) or ()
            if getattr(item, "path", None) is not None and getattr(item, "line", 0)
        }
        if not remote_by_key:
            return
        for breakpoint in tab.local_breakpoints():
            key = self._keil_breakpoint_sync_key(breakpoint.path, breakpoint.line)
            remote = remote_by_key.get(key)
            if remote is None:
                continue
            verified = bool(getattr(remote, "verified", True))
            message = str(getattr(remote, "message", "") or "")
            if verified:
                message = message or "Keil 快照已回读该断点"
            else:
                message = message or "Keil 快照回读该断点但标记异常"
            tab.set_breakpoint_verification(
                breakpoint.line,
                path=breakpoint.path,
                verified=verified,
                message=message,
            )

    @staticmethod
    def _keil_breakpoint_sync_key(path: str | Path | None, line: int) -> tuple[str, int]:
        try:
            path_text = str(Path(path).expanduser().resolve()).lower() if path is not None else ""
        except OSError:
            path_text = str(path or "").lower()
        return path_text, int(line or 0)

    @staticmethod
    def _keil_remote_breakpoint_evidence_text(remote, fallback: str) -> str:
        parts = [fallback]
        remote_id = str(getattr(remote, "remote_id", "") or "")
        if remote_id:
            parts.append(f"id {remote_id}")
        address = getattr(remote, "address", None)
        if address is not None:
            try:
                parts.append(f"0x{int(address):08X}")
            except (TypeError, ValueError):
                parts.append(str(address))
        raw_location = str(getattr(remote, "raw_location", "") or "")
        if raw_location and raw_location not in parts:
            parts.append(raw_location)
        return " · ".join(parts)

    def _append_keil_breakpoint_sync_audit(self, result: KeilBreakpointSyncResult, transaction=None) -> None:
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "action": "keil_breakpoint_sync",
            "transaction": transaction.audit_record(event="executed" if getattr(result, "completed", result.succeeded) else "failed") if transaction is not None else None,
            "result": result.to_record(),
        }
        try:
            with open(self._variable_write_audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str))
                f.write("\n")
        except Exception as exc:
            logger.warning("Keil 断点同步审计日志保存失败：%s", exc)

    def _append_keil_run_to_cursor_audit(self, result: KeilRunToCursorResult, transaction=None) -> None:
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "action": "keil_run_to_cursor",
            "transaction": transaction.audit_record(event="executed" if result.succeeded else "failed") if transaction is not None else None,
            "result": result.to_record(),
        }
        try:
            with open(self._variable_write_audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str))
                f.write("\n")
        except Exception as exc:
            logger.warning("Keil 运行到光标审计日志保存失败：%s", exc)

    def _write_keil_live_variable_from_preset(self, expression: str, value_text: str) -> None:
        self._write_keil_live_variable_from_workbench(default_expression=expression, default_value=value_text)

    def _write_keil_live_variable_from_workbench(self, default_expression: str = "", default_value: str = ""):
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            self._show_warning("Keil 写变量", "当前调试后端不是 Keil / UVSOCK。")
            return
        missing = [
            label
            for label, available in (
                ("read_variable/read_live_variable", hasattr(self._debug_backend, "read_variable") or hasattr(self._debug_backend, "read_live_variable")),
                ("write_variable/write_live_variable", hasattr(self._debug_backend, "write_variable") or hasattr(self._debug_backend, "write_live_variable")),
            )
            if not available
        ]
        if missing:
            self._show_warning("Keil 写变量", f"当前 Keil 后端缺少执行器：{', '.join(missing)}。")
            return
        tab = self._tab_debug_workbench
        status = tab.debug_status
        if status.state not in {
            DebugRuntimeState.KEIL_ATTACHED,
            DebugRuntimeState.PAUSED,
            DebugRuntimeState.RUNNING,
        }:
            self._show_warning("Keil 写变量", "请先连接 Keil 调试会话。")
            return

        preset_profile = self._current_keil_variable_preset_profile()
        default_expr, preset_value = keil_live_write_seed(preset_profile)
        if default_expression:
            default_expr = str(default_expression)
        if default_value:
            preset_value = str(default_value)
        prompt_hint = keil_live_write_prompt_hint(preset_profile)
        text, ok = ask_pcl_text(
            self,
            "Keil Live 写变量",
            (
                "第一行填写变量或表达式，第二行填写新值。\n"
                f"{prompt_hint}"
            ),
            text=f"{default_expr}\n{preset_value}".strip(),
            placeholder="变量名\n新值",
            confirm_text="下一步",
            cancel_text="取消",
            kind="warning",
        )
        if not ok:
            return
        try:
            expression, value_text = self._parse_keil_live_write_text(text)
        except ValueError as exc:
            self._show_warning("Keil 写变量", str(exc))
            return

        axf = self._current_debug_axf_path()
        request = KeilLiveVariableWriteRequest(
            expression=expression,
            value_text=value_text,
            axf_path=axf,
            prefer_memory=bool(axf),
            allow_command_fallback=True,
            connection_name="LoopMasterLiveWrite",
        )

        tab.set_debug_controls_ready(False)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(f"正在读取 {expression} 当前值...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            baseline_read = self._read_debug_variable_baseline(
                expression=expression,
                axf=axf,
                connection_name="LoopMasterLiveWriteRead",
            )
        except Exception as exc:
            baseline_read = KeilLiveVariableReadResult(
                attempted=True,
                read=False,
                expression=expression,
                method="backend",
                error=str(exc),
            )
        finally:
            QApplication.restoreOverrideCursor()
            tab.set_debug_controls_ready(True)

        self._debug_last_live_read_result = baseline_read
        self._debug_last_live_write_result = None
        self._refresh_debug_workbench_diagnostics()
        QApplication.processEvents()
        if not baseline_read.read:
            self._show_warning("Keil 写变量", f"{baseline_read.summary()}。\n已停止写入，避免盲写。")
            if hasattr(self, "_sb_label"):
                self._sb_label.setText(baseline_read.summary())
            return

        if not self._confirm_keil_live_write(request, status, baseline_read=baseline_read):
            return

        tab.set_debug_controls_ready(False)
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(f"正在通过 Keil 写入 {expression}...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self._write_debug_variable(
                expression=expression,
                value_text=value_text,
                axf=axf,
                prefer_memory=bool(axf),
                allow_command_fallback=True,
                connection_name="LoopMasterLiveWrite",
            )
        except Exception as exc:
            result = KeilLiveVariableWriteResult(
                attempted=True,
                written=False,
                expression=expression,
                value_text=value_text,
                method="backend",
                error=str(exc),
            )
        finally:
            QApplication.restoreOverrideCursor()
            tab.set_debug_controls_ready(True)

        self._debug_last_live_write_result = result
        self._append_keil_live_write_audit(result, baseline_read=baseline_read)
        self._refresh_debug_workbench_diagnostics()
        self._sync_debug_command_preview()
        if result.written:
            self._show_info("Keil 写变量完成", result.summary())
            if hasattr(self, "_sb_label"):
                self._sb_label.setText(result.summary())
            self._connect_debug_backend_read_only_for_workbench()
        else:
            self._show_warning("Keil 写变量失败", result.summary())
            if hasattr(self, "_sb_label"):
                self._sb_label.setText(result.summary())

    def _read_debug_variable_baseline(
        self,
        *,
        expression: str,
        axf: Path | None,
        connection_name: str,
    ) -> KeilLiveVariableReadResult:
        if hasattr(self._debug_backend, "read_variable"):
            generic = self._debug_backend.read_variable(
                DebugVariableReadRequest(
                    expression=expression,
                    binary_path=axf,
                    connection_name=connection_name,
                )
            )
            return self._keil_read_result_from_generic(generic)
        return self._debug_backend.read_live_variable(
            KeilLiveVariableReadRequest(
                expression=expression,
                axf_path=axf,
                connection_name=connection_name,
            )
        )

    def _write_debug_variable(
        self,
        *,
        expression: str,
        value_text: str,
        axf: Path | None,
        prefer_memory: bool,
        allow_command_fallback: bool,
        connection_name: str,
    ) -> KeilLiveVariableWriteResult:
        if hasattr(self._debug_backend, "write_variable"):
            generic = self._debug_backend.write_variable(
                DebugVariableWriteRequest(
                    expression=expression,
                    value_text=value_text,
                    binary_path=axf,
                    prefer_memory=prefer_memory,
                    allow_command_fallback=allow_command_fallback,
                    connection_name=connection_name,
                )
            )
            return self._keil_write_result_from_generic(generic)
        return self._debug_backend.write_live_variable(
            KeilLiveVariableWriteRequest(
                expression=expression,
                value_text=value_text,
                axf_path=axf,
                prefer_memory=prefer_memory,
                allow_command_fallback=allow_command_fallback,
                connection_name=connection_name,
            )
        )

    @staticmethod
    def _keil_resolved_from_generic(resolved) -> KeilResolvedVariable | None:
        if resolved is None:
            return None
        return KeilResolvedVariable(
            expression=resolved.expression,
            symbol=resolved.symbol,
            address=int(resolved.address or 0),
            size=int(resolved.size or 0),
            type_name=resolved.type_name,
            source=resolved.source,
            ram_checked=resolved.ram_checked,
        )

    def _keil_read_result_from_generic(self, result: DebugVariableReadResult) -> KeilLiveVariableReadResult:
        return KeilLiveVariableReadResult(
            attempted=result.attempted,
            read=result.read,
            expression=result.expression,
            method=result.method,
            resolved=self._keil_resolved_from_generic(result.resolved),
            raw=result.raw,
            value=result.value,
            diagnostics=result.diagnostics,
            error=result.error,
        )

    def _keil_write_result_from_generic(self, result: DebugVariableWriteResult) -> KeilLiveVariableWriteResult:
        return KeilLiveVariableWriteResult(
            attempted=result.attempted,
            written=result.written,
            expression=result.expression,
            value_text=result.value_text,
            method=result.method,
            resolved=self._keil_resolved_from_generic(result.resolved),
            old_raw=result.old_raw,
            new_raw=result.new_raw,
            readback_raw=result.readback_raw,
            old_value=result.old_value,
            readback_value=result.readback_value,
            command=result.command,
            attempts=result.attempts,
            diagnostics=result.diagnostics,
            error=result.error,
        )

    def _parse_keil_live_write_text(self, text: str) -> tuple[str, str]:
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        if len(lines) < 2:
            raise ValueError("请至少填写两行：变量名和新值。")
        expression = lines[0]
        value_text = lines[1]
        if not expression:
            raise ValueError("变量名不能为空。")
        if not value_text:
            raise ValueError("新值不能为空。")
        return expression, value_text

    def _default_keil_live_write_expression(self) -> str:
        expression, _value = keil_live_write_seed(self._current_keil_variable_preset_profile())
        return expression

    def _current_keil_variable_preset_profile(self) -> KeilVariablePresetProfile:
        status = self._tab_debug_workbench.debug_status if hasattr(self, "_tab_debug_workbench") else None
        project_path = status.project_path if status is not None else None
        target_name = status.target_name if status is not None else ""
        return keil_variable_preset_profile(project_path, target_name)

    def _current_debug_axf_path(self) -> Path | None:
        profile = self._make_current_keil_profile()
        if profile is not None and profile.axf_path is not None and profile.axf_path.exists():
            return profile.axf_path
        candidates: list[Path] = []
        for path in (getattr(self, "_elf_path", None), getattr(self, "_recent_elf_path", None)):
            if path:
                candidates.append(Path(path))
        status = self._tab_debug_workbench.debug_status if hasattr(self, "_tab_debug_workbench") else None
        project_path = status.project_path if status is not None else None
        target_name = status.target_name if status is not None else ""
        if project_path:
            candidates.extend(self._debug_axf_candidates_from_project(project_path, target_name))
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except OSError:
                continue
            if resolved.exists() and resolved.suffix.lower() in {".axf", ".elf", ".out"}:
                return resolved
        return None

    def _debug_axf_candidates_from_project(self, project_path: Path, target_name: str = "") -> list[Path]:
        try:
            project = parse_keil_project(project_path)
        except Exception:
            return []
        candidates: list[Path] = []
        target = None
        if target_name:
            for item in project.targets:
                if item.name == target_name:
                    target = item
                    break
        target = target or project.default_target
        if target is not None and target.output_path is not None:
            candidates.append(target.output_path)
        project_dir = Path(project_path).expanduser().resolve().parent
        for name in (project.name, "Project", "project"):
            candidates.append(project_dir / "Objects" / f"{name}.axf")
        return candidates

    def _make_current_keil_profile(self) -> KeilDebugProfile | None:
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            return None
        status = self._tab_debug_workbench.debug_status if hasattr(self, "_tab_debug_workbench") else None
        if status is None or not status.project_path:
            return None
        try:
            if hasattr(self._debug_backend, "debug_profile"):
                profile = self._debug_backend.debug_profile(
                    project_path=status.project_path,
                    target_name=status.target_name,
                )
            else:
                profile = make_keil_debug_profile(
                    root=self._keil_root,
                    project_path=status.project_path,
                    target_name=status.target_name,
                    port=self._debug_uvsock_port,
                )
        except Exception:
            return None
        self._debug_keil_profile = profile
        return profile

    def _confirm_keil_live_write(
        self,
        request: KeilLiveVariableWriteRequest,
        status,
        *,
        baseline_read: KeilLiveVariableReadResult | None = None,
    ) -> bool:
        axf_text = str(request.axf_path) if request.axf_path else "未找到，将尝试 Keil 命令窗口赋值"
        current_text = baseline_read.value if baseline_read is not None and baseline_read.value else "--"
        message = (
            f"变量：{request.expression}\n"
            f"当前值：{current_text}\n"
            f"新值：{request.value_text}\n"
            f"工程：{status.project_path or '--'}\n"
            f"AXF：{axf_text}\n\n"
            "这会通过 UVSOCK 改变真实 MCU 调试会话中的变量。"
            "优先走 RAM 内存写入并回读校验；解析失败时会尝试 Keil 命令窗口赋值，"
            "但无 AXF/地址时只能确认命令已提交，不能独立回读。"
        )
        return ask_pcl_confirmation(
            self,
            "确认 Keil Live 写变量",
            message,
            confirm_text="写入",
            cancel_text="取消",
            kind="warning",
        )

    def _append_keil_live_write_audit(
        self,
        result: KeilLiveVariableWriteResult,
        *,
        baseline_read: KeilLiveVariableReadResult | None = None,
    ) -> None:
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "action": "keil_live_write",
            "baseline_read": baseline_read.to_record() if baseline_read is not None else None,
            "result": result.to_record(),
        }
        try:
            with open(self._variable_write_audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                f.write("\n")
        except Exception as exc:
            logger.warning("Keil 写变量审计日志保存失败：%s", exc)

    def _refresh_debug_workbench_diagnostics(self) -> None:
        if not hasattr(self, "_tab_debug_workbench"):
            return
        diagnostics = tuple(getattr(self, "_debug_backend_diagnostics", ()) or self._debug_workbench_idle_diagnostics())
        self._tab_debug_workbench.set_backend_diagnostics(self._with_debug_session_contract_diagnostics(diagnostics))
        self._refresh_debug_live_loop_status()
        self._refresh_debug_variable_presets()

    def _refresh_debug_live_loop_status(self) -> None:
        if not hasattr(self, "_tab_debug_workbench"):
            return
        self._tab_debug_workbench.set_live_loop_status(
            (
                self._live_loop_session_item(),
                self._live_loop_pc_item(),
                self._live_loop_breakpoint_item(),
                self._live_loop_write_item(),
                self._live_loop_scope_item(),
            )
        )

    def _live_loop_session_item(self) -> tuple[str, str, str]:
        status = self._tab_debug_workbench.debug_status
        labels = {
            DebugRuntimeState.DISCONNECTED: "未连接",
            DebugRuntimeState.KEIL_DISCOVERED: "已发现",
            DebugRuntimeState.KEIL_ATTACHED: "已连接",
            DebugRuntimeState.PAUSED: "已暂停",
            DebugRuntimeState.RUNNING: "运行中",
            DebugRuntimeState.ERROR: "错误",
        }
        value = labels.get(status.state, status.label or "--")
        detail = status.detail or status.label or "等待调试会话"
        runtime_result = getattr(self, "_debug_last_runtime_control_result", None)
        if runtime_result is not None:
            runtime_label = self._runtime_action_label(getattr(runtime_result, "action", ""))
            runtime_state = "成功" if getattr(runtime_result, "succeeded", False) else "失败"
            value = f"{value} · {runtime_label}"
            detail = f"最后运行控制：{runtime_label}{runtime_state}；{runtime_result.summary()}"
        return "会话", value, detail

    def _live_loop_pc_item(self) -> tuple[str, str, str]:
        pc = getattr(self._tab_debug_workbench, "_pc_evidence", None)
        if pc is None:
            return "PC", "--", "等待 PC 回读"
        value = "已回读" if getattr(pc, "complete", False) else "未验证"
        parts = []
        if getattr(pc, "address", None) is not None:
            parts.append(f"0x{int(pc.address):08X}")
        if getattr(pc, "path", None) is not None and getattr(pc, "line", None):
            parts.append(f"{Path(pc.path).name}:{pc.line}")
        if getattr(pc, "message", ""):
            parts.append(str(pc.message))
        return "PC", value, "；".join(parts) or value

    def _live_loop_breakpoint_item(self) -> tuple[str, str, str]:
        breakpoints = self._tab_debug_workbench.local_breakpoints()
        snapshot = getattr(self, "_debug_remote_breakpoint_snapshot", None)
        if not breakpoints:
            remote_count = len(getattr(snapshot, "breakpoints", ()) or ()) if snapshot is not None else 0
            detail = f"远端断点 {remote_count}" if snapshot is not None else "当前没有本地断点"
            return "断点", "无", detail
        verified = sum(1 for item in breakpoints if item.verified)
        failed = sum(1 for item in breakpoints if not item.verified and item.message)
        pending = max(0, len(breakpoints) - verified - failed)
        remote_detail = "--"
        if snapshot is not None:
            remote_detail = (
                f"远端 {len(getattr(snapshot, 'breakpoints', ()) or ())}；"
                f"{'完整' if getattr(snapshot, 'complete', False) else '不完整'}"
            )
        detail = f"已验证 {verified}；未验证 {failed}；待验证 {pending}；{remote_detail}"
        return "断点", f"{verified}/{len(breakpoints)}", detail

    def _live_loop_write_item(self) -> tuple[str, str, str]:
        read = getattr(self, "_debug_last_live_read_result", None)
        write = getattr(self, "_debug_last_live_write_result", None)
        if write is not None:
            if getattr(write, "written", False):
                value = str(getattr(write, "readback_value", "") or getattr(write, "value_text", "") or "成功")
                detail = (
                    f"{getattr(write, 'expression', '--')} 写入成功；"
                    f"基线 {getattr(read, 'value', '--') if read is not None else '--'}；"
                    f"回读 {getattr(write, 'readback_value', '--') or '--'}"
                )
                return "写入", value, detail
            return "写入", "失败", getattr(write, "error", "") or "写入失败"
        if read is not None:
            value = str(getattr(read, "value", "") or ("成功" if getattr(read, "read", False) else "失败"))
            detail = f"{getattr(read, 'expression', '--')} 写前读取"
            if getattr(read, "error", ""):
                detail += f"；{read.error}"
            return "写入", value, detail
        return "写入", "--", "尚未执行变量读取或写入"

    def _live_loop_scope_item(self) -> tuple[str, str, str]:
        descriptor = active_acquisition_source(getattr(self, "_scope_read_source", SCOPE_SOURCE_SWD))
        return "示波", descriptor.short_label, descriptor.detail

    def _keil_profile_diagnostics(self) -> tuple[tuple[str, str], ...]:
        profile = self._make_current_keil_profile() or getattr(self, "_debug_keil_profile", None)
        if profile is None:
            return ()
        return profile.diagnostic_rows()

    def _keil_profile_store_diagnostics(self) -> tuple[tuple[str, str], ...]:
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            return ()
        store = getattr(self, "_keil_profile_store", KeilDebugProfileStore())
        default = store.default
        rows = [
            ("调试档案数", str(len(store.records))),
        ]
        if default is not None:
            rows.extend(
                [
                    ("默认调试档案", default.display_name),
                    ("档案工程", str(default.project_path)),
                    ("档案 Target", default.target_name or "--"),
                    ("档案端口", str(default.uvsock_port)),
                ]
            )
            rows.extend(default.metadata.diagnostic_rows())
        else:
            rows.append(("默认调试档案", "未保存"))
        return tuple(rows)

    def _keil_variable_preset_diagnostics(self) -> tuple[tuple[str, str], ...]:
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            return ()
        return self._current_keil_variable_preset_profile().diagnostic_rows()

    def _refresh_debug_variable_presets(self) -> None:
        if not hasattr(self, "_tab_debug_workbench"):
            return
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            self._tab_debug_workbench.set_variable_presets(())
            return
        profile = self._current_keil_variable_preset_profile()
        rows = []
        for preset in profile.write_presets:
            rows.append(
                (
                    preset.expression,
                    preset.label,
                    preset.value_type,
                    preset.default_value,
                    preset.purpose or preset.range_hint,
                    True,
                )
            )
        for preset in profile.scope_presets:
            rows.append(
                (
                    preset.expression,
                    preset.label,
                    preset.value_type,
                    preset.default_value,
                    preset.purpose or preset.range_hint,
                    False,
                )
            )
        self._tab_debug_workbench.set_variable_presets(tuple(rows))

    def _save_current_keil_debug_profile(self) -> None:
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            self._show_warning("Keil 调试档案", "当前调试后端不是 Keil / UVSOCK。")
            return
        profile = self._make_current_keil_profile()
        if profile is None or profile.project_path is None:
            self._show_warning("Keil 调试档案", "请先打开 Keil 工程。")
            return
        record = profile_record_from_debug_profile(profile)
        self._keil_profile_store = self._keil_profile_store.upsert(record)
        if not save_keil_profile_store(self._keil_profile_store_path, self._keil_profile_store):
            self._show_warning("Keil 调试档案", "保存调试档案失败。")
            return
        self._save_config()
        self._refresh_debug_workbench_diagnostics()
        message = f"已保存默认调试档案：{record.display_name}"
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(message)
        self._show_info("Keil 调试档案", message)

    def _load_default_keil_debug_profile(self, *, silent: bool = False) -> bool:
        self._keil_profile_store = load_keil_profile_store(self._keil_profile_store_path)
        record = self._keil_profile_store.default
        if record is None:
            if not silent:
                self._show_warning("Keil 调试档案", "还没有保存过 Keil 调试档案。")
            return False
        if not record.project_path.exists():
            if not silent:
                self._show_warning("Keil 调试档案", f"工程不存在：{record.project_path}")
            return False
        try:
            self._tab_debug_workbench.load_project(record.project_path)
        except Exception as exc:
            if not silent:
                self._show_warning("Keil 调试档案", f"载入工程失败：{exc}")
            return False
        if record.target_name:
            index = self._tab_debug_workbench.target_combo.findText(record.target_name)
            if index >= 0:
                self._tab_debug_workbench.target_combo.setCurrentIndex(index)
        if record.keil_root is not None:
            self._keil_root = record.keil_root
        self._debug_uvsock_port = int(record.uvsock_port)
        self._debug_backend_registry = create_default_debug_backend_registry(
            keil_root=self._keil_root,
            uvsock_port=self._debug_uvsock_port,
            include_placeholders=True,
        )
        self._debug_backend = self._debug_backend_registry.create(self._debug_backend_kind)
        self._debug_session_controller.set_backend(self._debug_backend_kind)
        self._debug_keil_profile = self._make_current_keil_profile()
        self._sync_debug_source_manifest_preview()
        self._sync_debug_command_preview()
        self._refresh_debug_workbench_diagnostics()
        self._refresh_debug_variable_presets()
        self._refresh_debug_backend_options()
        self._save_config()
        message = f"已载入默认调试档案：{record.display_name}"
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(message)
        self._refresh_hero()
        return True

    def _configure_keil_debug_runtime(self) -> None:
        root = self._choose_existing_directory("选择 Keil 根目录")
        if not root:
            return
        text, ok = ask_pcl_text(
            self,
            "Keil / UVSOCK 配置",
            "填写 UVSOCK 端口，通常保持 4827。",
            text=str(self._debug_uvsock_port),
            placeholder="4827",
            confirm_text="应用",
            cancel_text="取消",
            kind="info",
        )
        if not ok:
            return
        try:
            port = int(str(text).strip())
        except ValueError:
            self._show_warning("Keil 配置", "UVSOCK 端口必须是数字。")
            return
        if not (1 <= port <= 65535):
            self._show_warning("Keil 配置", "UVSOCK 端口必须在 1..65535 范围内。")
            return
        if self._scope_read_source == "keil_watch" and self._keil_watch_backend is not None:
            if not ask_pcl_confirmation(
                self,
                "应用 Keil 配置",
                "当前 Keil Watch 会话会断开，配置更新后需要重新连接。",
                confirm_text="应用",
                cancel_text="取消",
                kind="warning",
            ):
                return
        self._apply_debug_keil_config(Path(root), port)

    def _apply_debug_keil_config(self, root: Path, port: int) -> None:
        self._keil_root = Path(root).expanduser()
        self._debug_uvsock_port = int(port)
        if self._keil_watch_backend is not None:
            self._keil_watch_backend.disconnect()
            self._keil_watch_backend = None
        self._debug_backend_registry = create_default_debug_backend_registry(
            keil_root=self._keil_root,
            uvsock_port=self._debug_uvsock_port,
            include_placeholders=True,
        )
        self._debug_backend = self._debug_backend_registry.create(self._debug_backend_kind)
        self._debug_session_controller = DebugSessionController(
            self._debug_backend_registry,
            backend=self._debug_backend_kind,
        )
        self._debug_remote_breakpoint_snapshot = None
        self._debug_backend_snapshot_record = None
        self._debug_backend_diagnostics = ()
        self._debug_last_live_read_result = None
        self._debug_last_live_write_result = None
        self._debug_last_breakpoint_sync_result = None
        self._debug_last_runtime_control_result = None
        self._debug_last_run_to_cursor_result = None
        self._debug_keil_profile = None
        self._debug_keil_build_result = None
        self._debug_keil_launch_result = None
        if hasattr(self, "_tab_debug_workbench"):
            tab = self._tab_debug_workbench
            previous = tab.debug_status
            status = make_debug_status(
                state=DebugRuntimeState.DISCONNECTED,
                backend=self._debug_backend_kind,
                detail="Keil 配置已更新，等待重新发现后端",
                project_path=previous.project_path,
                target_name=previous.target_name,
            )
            tab.set_debug_status(status, controls_ready=True)
            tab.set_pc_evidence(None)
        self._refresh_debug_backend_options()
        self._refresh_debug_workbench_diagnostics()
        self._sync_debug_command_preview()
        self._refresh_debug_variable_presets()
        self._save_config()
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(f"Keil 配置已更新：{self._keil_root} / {self._debug_uvsock_port}")
        self._refresh_hero()

    def _add_keil_watch_preset_to_scope(self, expression: str, label: str, value_type: str) -> None:
        expression = str(expression or "").strip()
        if not expression:
            return
        self._set_scope_read_source("keil_watch")
        type_info = make_keil_watch_type(value_type)
        self._keil_watch_registry[expression] = (0, type_info)
        self._monitored.add(expression)
        self._saved_scope_assignments.setdefault(expression, (True, False, False))
        self._sync_value_table_placeholders()
        self._sync_plot_curve_state(force=True)
        self._update_selected_list()
        self._refresh_debug_buttons()
        self._show_workspace_page("scope")
        display = label or expression
        if hasattr(self, "_sb_label"):
            self._sb_label.setText(f"已加入 Keil Watch：{display}")
        self._refresh_hero()
        self._idle_read()

    def _set_scope_read_source(self, source: str) -> None:
        source = normalize_acquisition_source_key(source)
        if source == getattr(self, "_scope_read_source", SCOPE_SOURCE_SWD):
            self._refresh_debug_scope_acquisition_status()
            if hasattr(self, "_tab_debug_workbench"):
                self._refresh_debug_workbench_diagnostics()
            return
        if self._collector.is_running:
            self._on_stop()
        self._scope_read_source = source
        self._collector.set_backend(self._active_scope_backend(connect=False))
        self._monitored = set()
        self._monitor_list = []
        self._sync_value_table_placeholders()
        self._refresh_debug_scope_acquisition_status()
        if hasattr(self, "_tab_debug_workbench"):
            self._refresh_debug_workbench_diagnostics()

    def _scope_registry(self) -> dict[str, tuple[int, TypeInfo]]:
        if getattr(self, "_scope_read_source", SCOPE_SOURCE_SWD) == SCOPE_SOURCE_KEIL_WATCH:
            return self._keil_watch_registry
        return self._registry

    def _active_scope_backend(self, *, connect: bool = False):
        if getattr(self, "_scope_read_source", SCOPE_SOURCE_SWD) != SCOPE_SOURCE_KEIL_WATCH:
            return self._backend
        backend = self._keil_watch_backend
        if backend is None:
            if hasattr(self._debug_backend, "create_watch_transport"):
                backend = self._debug_backend.create_watch_transport(
                    connection_name="LoopMasterScopeWatch",
                )
            else:
                backend = KeilUvSockWatchBackend(
                    root=self._keil_root,
                    port=self._debug_uvsock_port,
                    connection_name="LoopMasterScopeWatch",
                )
            self._keil_watch_backend = backend
        if connect and not backend.is_connected and not backend.connect():
            raise RuntimeError(backend.last_error or "Keil Watch 连接失败")
        return backend

    def _scope_backend_connected(self) -> bool:
        if getattr(self, "_scope_read_source", SCOPE_SOURCE_SWD) == SCOPE_SOURCE_KEIL_WATCH:
            backend = self._keil_watch_backend
            return bool(backend and backend.is_connected)
        return bool(self._backend.is_connected)

    def _scope_read_source_label(self) -> str:
        descriptor = active_acquisition_source(getattr(self, "_scope_read_source", SCOPE_SOURCE_SWD))
        return descriptor.short_label

    def _keil_build_diagnostics(self) -> tuple[tuple[str, str], ...]:
        result = getattr(self, "_debug_keil_build_result", None)
        if result is None:
            return ()
        rows = [
            ("构建结果", "成功" if result.succeeded else "失败"),
            ("构建返回码", str(result.returncode) if result.returncode is not None else "--"),
            ("构建日志", str(result.log_path or "--")),
            ("构建 AXF", str(result.axf_path or "--")),
            ("构建 AXF 状态", "已存在" if result.axf_exists else "未生成"),
        ]
        if result.error:
            rows.append(("构建错误", result.error))
        elif result.output_tail:
            rows.append(("构建输出", result.output_tail))
        return tuple(rows)

    def _keil_launch_diagnostics(self) -> tuple[tuple[str, str], ...]:
        result = getattr(self, "_debug_keil_launch_result", None)
        if result is None:
            return ()
        rows = [
            ("启动结果", "成功" if result.launched else "失败"),
            ("uVision PID", str(result.pid or "--")),
        ]
        if result.error:
            rows.append(("启动错误", result.error))
        return tuple(rows)

    def _keil_live_write_diagnostics(self) -> tuple[tuple[str, str], ...]:
        read = getattr(self, "_debug_last_live_read_result", None)
        result = getattr(self, "_debug_last_live_write_result", None)
        if read is None and result is None:
            return ()
        rows = []
        if read is not None:
            rows.extend(
                [
                    ("写前读取变量", read.expression),
                    ("写前读取结果", "成功" if read.read else "失败"),
                ]
            )
            if read.value:
                rows.append(("写前基线值", read.value))
            if read.resolved is not None:
                rows.append(("写前读取地址", f"0x{read.resolved.address:08X}"))
            if read.error:
                rows.append(("写前读取错误", read.error))
        if result is None:
            return tuple(rows)
        rows.extend(
            [
                ("Keil 写变量", "成功" if result.written else "失败"),
                ("写入目标", result.expression),
                ("写入方法", result.method or "--"),
            ]
        )
        if result.resolved is not None:
            rows.extend(
                [
                    ("写入地址", f"0x{result.resolved.address:08X}"),
                    ("写入类型", result.resolved.type_name or "--"),
                ]
            )
        if result.readback_value:
            rows.append(("写后回读", result.readback_value))
        elif result.written:
            rows.append(("写后回读", "未独立回读"))
        if result.error:
            rows.append(("写入错误", result.error))
        return tuple(rows)

    def _keil_breakpoint_sync_diagnostics(self) -> tuple[tuple[str, str], ...]:
        result = getattr(self, "_debug_last_breakpoint_sync_result", None)
        if result is None:
            return ()
        return result.diagnostic_rows()

    def _keil_run_to_cursor_diagnostics(self) -> tuple[tuple[str, str], ...]:
        result = getattr(self, "_debug_last_run_to_cursor_result", None)
        if result is None:
            return ()
        return result.diagnostic_rows()

    def _keil_auto_debug_diagnostics(self) -> tuple[tuple[str, str], ...]:
        result = getattr(self, "_debug_keil_auto_debug_result", None)
        if result is None:
            return ()
        return result.diagnostic_rows()

    @staticmethod
    def _keil_runtime_control_diagnostics(result) -> tuple[tuple[str, str], ...]:
        if result is None:
            return ()
        uvsc = getattr(result, "uvsc", None)
        action = getattr(result, "action", "")
        label = {
            "halt": "暂停",
            "run": "运行",
            "reset": "复位",
            "step": "单步",
            "step_over": "跨过",
        }.get(str(action or ""), str(action or "--"))
        target_running = getattr(result, "target_running", None)
        target_text = "运行中" if target_running is True else "已暂停" if target_running is False else "未知"
        rows = [
            ("运行控制", label),
            ("运行控制结果", "成功" if getattr(result, "succeeded", False) else "失败"),
            ("运行控制状态", target_text),
            ("运行控制摘要", result.summary() if hasattr(result, "summary") else "--"),
        ]
        if uvsc is not None and getattr(uvsc, "status_name", ""):
            rows.append(("运行控制 UVSC", uvsc.status_name))
        error = getattr(result, "error", "") or (getattr(uvsc, "error", "") if uvsc is not None else "")
        if error:
            rows.append(("运行控制错误", str(error)))
        return tuple(rows)

    @staticmethod
    def _runtime_action_label(action: str) -> str:
        return {
            "halt": "暂停",
            "run": "运行",
            "reset": "复位",
            "step": "单步",
            "step_over": "跨过",
        }.get(str(action or ""), str(action or "--"))

    def _sync_debug_command_preview(self):
        if not hasattr(self, "_tab_debug_workbench"):
            return
        if getattr(self, "_debug_command_preview_suspended", False):
            return
        tab = self._tab_debug_workbench
        status = tab.debug_status
        if self._debug_backend_kind == DebugBackendKind.KEIL:
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
        else:
            transactions = build_unavailable_debug_transactions(
                status,
                debug_command_plans_for_status(status),
                backend=self._debug_backend_kind.value,
                backend_display_name=self._debug_backend_display_name(),
                reason=f"{self._debug_backend_display_name()} 后端尚未接入执行器",
                backend_snapshot=getattr(self, "_debug_backend_snapshot_record", None),
            )
        if hasattr(tab, "set_remote_breakpoint_snapshot"):
            tab.set_remote_breakpoint_snapshot(getattr(self, "_debug_remote_breakpoint_snapshot", None))
        tab.set_command_transactions(transactions)
        focused = self._focused_debug_transaction(transactions)
        if focused is not None:
            self._debug_command_history.record(focused, event="previewed", source="ui_sync")
        tab.set_command_history_entries(self._debug_command_history.recent(limit=5))

    def set_debug_remote_breakpoint_snapshot(self, snapshot):
        self._debug_remote_breakpoint_snapshot = snapshot
        if hasattr(self, "_tab_debug_workbench") and hasattr(self._tab_debug_workbench, "set_remote_breakpoint_snapshot"):
            self._tab_debug_workbench.set_remote_breakpoint_snapshot(snapshot)
        self._mark_local_breakpoints_from_remote_snapshot(snapshot)
        self._sync_debug_command_preview()

    def _focused_debug_transaction(self, transactions):
        priority = (
            "attach",
            "halt",
            "run",
            "step",
            "run_to_cursor",
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
        descriptor = self._debug_backend_registry.descriptor(self._debug_backend_kind)
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            return (
                ("后端", descriptor.display_name),
                ("状态", "占位已注册，尚未接入执行器"),
                ("安全边界", "不会启动进程、连接探针或写目标"),
                ("下一步", descriptor.notes or "等待后续后端实现"),
            ) + debug_backend_local_profile_diagnostic_rows(self._debug_backend_kind)
        return (
            ("后端", descriptor.display_name),
            ("Keil 根目录", str(self._keil_root)),
            ("UVSOCK 端口", str(self._debug_uvsock_port)),
            ("状态", "等待发现 Keil"),
        )

    def _debug_workbench_error_diagnostics(self, message: str) -> tuple[tuple[str, str], ...]:
        if self._debug_backend_kind != DebugBackendKind.KEIL:
            return (
                ("后端", self._debug_backend_display_name()),
                ("错误", message),
            )
        return (
            ("后端", self._debug_backend_display_name()),
            ("Keil 根目录", str(self._keil_root)),
            ("UVSOCK 端口", str(self._debug_uvsock_port)),
            ("错误", message),
        )

    def _with_debug_session_contract_diagnostics(
        self,
        diagnostics: tuple[tuple[str, str], ...] | list[tuple[str, str]],
    ) -> tuple[tuple[str, str], ...]:
        controller = getattr(self, "_debug_session_controller", None)
        if controller is None:
            return tuple(diagnostics)
        snapshot = controller.snapshot
        commands = controller.commands
        enabled = [command.key for command in commands if command.enabled_by_state]
        executable = [command.key for command in commands if command.execution_enabled]
        policy = controller.safety_policy
        diagnostic_rows = self._dedupe_diagnostics(
            tuple(diagnostics)
            + self._keil_profile_diagnostics()
            + self._keil_profile_store_diagnostics()
            + self._keil_variable_preset_diagnostics()
            + self._keil_build_diagnostics()
            + self._keil_launch_diagnostics()
            + self._keil_auto_debug_diagnostics()
            + self._keil_breakpoint_sync_diagnostics()
            + self._keil_run_to_cursor_diagnostics()
            + self._remote_breakpoint_snapshot_diagnostics()
            + self._acquisition_source_diagnostics()
            + self._acquisition_batch_diagnostics()
            + self._debug_pc_evidence_diagnostics()
            + self._keil_live_write_diagnostics()
        )
        rows = diagnostic_rows + (
            ("会话合同", f"{snapshot.display_name} / {snapshot.state.value}"),
            ("目标状态", snapshot.target_state.value),
            ("安全策略", "dry-run" if policy.dry_run else policy.label),
            ("合同命令", ", ".join(enabled) if enabled else "--"),
            ("可执行命令", ", ".join(executable) if executable else "无"),
        )
        return rows

    def _remote_breakpoint_snapshot_diagnostics(self) -> tuple[tuple[str, str], ...]:
        snapshot = getattr(self, "_debug_remote_breakpoint_snapshot", None)
        if snapshot is None:
            return ()
        rows = [
            ("远端断点证据", str(getattr(snapshot, "snapshot_id", "") or "--")),
            ("远端断点完整", "是" if getattr(snapshot, "complete", False) else "否"),
            ("远端断点计数", str(len(getattr(snapshot, "breakpoints", ()) or ()))),
        ]
        error = str(getattr(snapshot, "error", "") or "")
        if error:
            rows.append(("远端断点错误", error))
        return tuple(rows)

    def _acquisition_source_diagnostics(self) -> tuple[tuple[str, str], ...]:
        descriptor = active_acquisition_source(
            getattr(self, "_scope_read_source", SCOPE_SOURCE_SWD),
            keil_watch_ready=True,
            serial_ready=hasattr(self, "_tab_serial"),
            include_planned=True,
        )
        return descriptor.diagnostic_rows()

    def _acquisition_batch_diagnostics(self) -> tuple[tuple[str, str], ...]:
        collector = getattr(self, "_collector", None)
        if collector is None or not hasattr(collector, "get_acquisition_batch"):
            return ()
        source = getattr(self, "_scope_read_source", SCOPE_SOURCE_SWD)
        try:
            batch = collector.get_acquisition_batch(source, tail_seconds=1.0 if collector.is_running else None)
        except Exception as exc:
            return (("采集批次", f"不可用：{exc}"),)
        rate = batch.actual_rate_hz or float(getattr(collector, "actual_rate", 0.0) or 0.0)
        duration = batch.duration_s
        rows = [
            ("采集批次来源", batch.source_key),
            ("采集批次样本", str(batch.sample_count)),
            ("采集批次变量", str(len(batch.variable_names))),
            ("采集批次频率", self._format_sample_rate(rate) if rate > 0 else "--"),
            ("采集批次时长", f"{duration:.3f}s" if duration > 0 else "--"),
        ]
        if batch.variable_names:
            preview = ", ".join(batch.variable_names[:4])
            if len(batch.variable_names) > 4:
                preview += f", +{len(batch.variable_names) - 4}"
            rows.append(("采集批次变量名", preview))
        return tuple(rows)

    def _acquisition_batch_status_detail(self) -> str:
        collector = getattr(self, "_collector", None)
        if collector is None or not hasattr(collector, "get_acquisition_batch"):
            return ""
        source = getattr(self, "_scope_read_source", SCOPE_SOURCE_SWD)
        try:
            batch = collector.get_acquisition_batch(source, tail_seconds=1.0 if collector.is_running else None)
        except Exception:
            return ""
        rate = batch.actual_rate_hz or float(getattr(collector, "actual_rate", 0.0) or 0.0)
        parts = [
            f"批次：{batch.source_key}",
            f"样本：{batch.sample_count}",
            f"变量：{len(batch.variable_names)}",
        ]
        if rate > 0:
            parts.append(f"批次频率：{self._format_sample_rate(rate)} Hz")
        if batch.variable_names:
            preview = ", ".join(batch.variable_names[:3])
            if len(batch.variable_names) > 3:
                preview += f", +{len(batch.variable_names) - 3}"
            parts.append(f"变量名：{preview}")
        return "  |  ".join(parts)

    def _debug_pc_evidence_diagnostics(self) -> tuple[tuple[str, str], ...]:
        record = getattr(self, "_debug_backend_snapshot_record", None)
        if not isinstance(record, dict):
            return ()
        pc = record.get("pc_location")
        if not isinstance(pc, dict):
            return ()
        complete = bool(pc.get("complete"))
        source = str(pc.get("source") or "--")
        line = pc.get("line")
        address = pc.get("address")
        location_parts: list[str] = []
        if line is not None:
            location_parts.append(f"line {line}")
        if address is not None:
            try:
                location_parts.append(f"0x{int(address):08X}")
            except (TypeError, ValueError):
                location_parts.append(str(address))
        function = str(pc.get("function") or "")
        if function:
            location_parts.append(function)
        message = str(pc.get("message") or "")
        return (
            ("PC 证据", "已回读" if complete else "未验证"),
            ("PC 来源", source),
            ("PC 位置", " / ".join(location_parts) if location_parts else "--"),
            ("PC 说明", message or "--"),
        )

    @staticmethod
    def _dedupe_diagnostics(rows: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for key, value in rows:
            key_text = str(key)
            if key_text in seen:
                continue
            seen.add(key_text)
            result.append((key_text, str(value)))
        return tuple(result)

    def _debug_backend_display_name(self) -> str:
        try:
            return self._debug_backend_registry.descriptor(self._debug_backend_kind).display_name
        except Exception:
            return str(getattr(self._debug_backend, "display_name", self._debug_backend_kind.value))

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
        if getattr(self, "_scope_read_source", "swd") != "swd":
            self._set_scope_read_source("swd")
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
        registry = self._scope_registry()
        for path in sorted(self._monitored):
            info = registry.get(path)
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
            addr_text = "Keil" if getattr(self, "_scope_read_source", "swd") == "keil_watch" else f"0x{addr:08X}"
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
        source = getattr(self, "_scope_read_source", "swd")
        connected = self._backend.is_connected if source == "swd" else self._scope_backend_connected()
        halted = bool(self._target_is_halted)
        selected = self._selected_value_path()
        has_variable = bool(selected and selected in self._scope_registry())

        for attr in ("_btn_halt_target", "_btn_resume_target"):
            button = getattr(self, attr, None)
            if button is not None:
                button.setEnabled(self._backend.is_connected)

        can_swd_write = source == "swd" and connected and halted and has_variable
        self._btn_write_value.setEnabled(can_swd_write)
        self._btn_restore_value.setEnabled(can_swd_write and selected in self._temporary_writes)

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

    def _current_selected_value_text(self) -> str:
        row = self._value_table.currentRow()
        if row < 0 or not self._value_table.item(row, 1):
            return ""
        current_text = self._value_table.item(row, 1).text().strip()
        return "" if current_text == "--" else current_text

    def _confirm_variable_write(
        self,
        path: str,
        addr: int,
        ti: TypeInfo,
        current_text: str,
        value_text: str,
    ) -> bool:
        current = current_text or "未知"
        sampling_note = "当前正在采样，写入会与采样读内存串行排队。" if self._collector.is_running else "目标已暂停，写入后会立即回读校验。"
        message = (
            f"变量：{path}\n"
            f"地址：0x{addr:08X}\n"
            f"类型：{format_type(ti)}\n"
            f"当前值：{current}\n"
            f"新值：{value_text.strip()}\n\n"
            f"{sampling_note}\n"
            "仅允许 RAM 中的基础数值/枚举变量，失败会拒绝或回滚。"
        )
        return ask_pcl_confirmation(
            self,
            "确认临时写入",
            message,
            confirm_text="写入",
            cancel_text="取消",
            kind="warning",
        )

    def _confirm_variable_restore(self, path: str, addr: int, ti: TypeInfo) -> bool:
        message = (
            f"变量：{path}\n"
            f"地址：0x{addr:08X}\n"
            f"类型：{format_type(ti)}\n\n"
            "将恢复到本次会话第一次临时写入前保存的原始字节，并在写回后校验。"
        )
        return ask_pcl_confirmation(
            self,
            "确认恢复变量",
            message,
            confirm_text="恢复",
            cancel_text="取消",
            kind="warning",
        )

    def _append_variable_write_audit(self, action: str, path: str, addr: int, ti: TypeInfo, result: dict):
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "action": action,
            "variable": path,
            "address": f"0x{addr:08X}",
            "type": format_type(ti),
            "target_state": getattr(self, "_target_state_value", "--"),
            "sampling": bool(self._collector.is_running),
            "result": {
                key: (value.hex() if isinstance(value, (bytes, bytearray)) else value)
                for key, value in (result or {}).items()
            },
        }
        try:
            with open(self._variable_write_audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                f.write("\n")
        except Exception as exc:
            logger.warning("变量写入审计日志保存失败：%s", exc)

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
        current_text = self._current_selected_value_text()

        value, ok = ask_pcl_text(
            self,
            "临时写入变量",
            f"{path}\n地址：0x{addr:08X}\n类型：{format_type(ti)}\n\n请输入新值，支持十进制或 0x 十六进制整数，浮点类型支持小数。",
            text=current_text,
            placeholder="例如 60、0x3C、0.125",
            confirm_text="下一步",
            cancel_text="取消",
            kind="info",
        )
        if not ok:
            return
        value = value.strip()
        if not value:
            self._show_warning("临时写入", "写入值不能为空。")
            return
        if not self._confirm_variable_write(path, addr, ti, current_text, value):
            return

        try:
            result = self._backend.write_variable_value(addr, ti, value)
        except Exception as e:
            self._show_warning("临时写入失败", str(e))
            return

        if path not in self._temporary_writes:
            self._temporary_writes[path] = (addr, ti, result["old_raw"])
        self._append_variable_write_audit("write", path, addr, ti, result)
        self._idle_read()
        self._refresh_debug_buttons()
        self._show_info(
            "临时写入完成",
            f"{self._short_value_name(path)}\n"
            f"{self._format_debug_value(result['old_value'])} -> "
            f"{self._format_debug_value(result['new_value'])}\n"
            "已回读校验，恢复按钮可回到首次写入前的原始值。",
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
        if not self._confirm_variable_restore(path, addr, ti):
            return
        try:
            result = self._backend.restore_variable_raw(addr, ti, raw)
        except Exception as e:
            self._show_warning("恢复失败", str(e))
            return

        self._temporary_writes.pop(path, None)
        self._append_variable_write_audit("restore", path, addr, ti, result)
        self._idle_read()
        self._refresh_debug_buttons()
        self._show_info(
            "恢复完成",
            f"{self._short_value_name(path)} 已恢复为 {self._format_debug_value(result['value'])}\n"
            "已回读校验。",
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

    def _on_view_variable_write_audit(self):
        audit_path = self._variable_write_audit_path.resolve()
        if audit_path.exists():
            try:
                os.startfile(audit_path)
            except Exception:
                subprocess.Popen(["notepad.exe", str(audit_path)])
        else:
            self._show_info("变量写入记录", "还没有变量写入或恢复记录。")

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
        source = getattr(self, "_scope_read_source", "swd")
        if source == "swd" and not self._elf_path:
            self._show_warning("错误", "请先导入 ELF 文件。")
            return
        active_backend = None
        if source == "swd" and not self._backend.is_connected:
            self._show_warning("错误", "请先连接调试器。")
            return
        if source == "keil_watch":
            try:
                active_backend = self._active_scope_backend(connect=True)
            except Exception as exc:
                self._show_warning("Keil Watch 连接失败", str(exc))
                return
        else:
            active_backend = self._backend

        self._collector.set_backend(active_backend)
        self._monitor_list = self._selected_monitor_items()

        if not self._monitor_list:
            self._show_warning("错误",
                "请先在变量页选择变量。")
            return

        rate = self._rate_combo.currentData()
        if rate == 0:
            rate = 1000  # 最大模式使用高频采样
        rate = int(rate)
        self._scope_rate_note = ""
        if source == "keil_watch":
            if hasattr(active_backend, "clamp_sample_rate"):
                rate, self._scope_rate_note = active_backend.clamp_sample_rate(rate)
            else:
                rate = min(rate, KEIL_WATCH_MAX_HZ)
                self._scope_rate_note = keil_watch_rate_warning(rate, len(self._monitor_list))
            extra_note = keil_watch_rate_warning(rate, len(self._monitor_list))
            if extra_note and not self._scope_rate_note:
                self._scope_rate_note = extra_note

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
            addr_item = QTableWidgetItem("Keil" if source == "keil_watch" else f"0x{addr:08X}")
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
        if self._scope_rate_note and hasattr(self, "_sb_label"):
            self._sb_label.setText(self._scope_rate_note)

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
        if getattr(self, "_scope_read_source", "swd") == "keil_watch" and self._keil_watch_backend is not None:
            self._keil_watch_backend.disconnect()
        self._collector._actual_rate = 0.0
        self._scope_rate_note = ""
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
        note = ""
        if getattr(self, "_scope_read_source", "swd") == "keil_watch":
            backend = self._keil_watch_backend
            if backend is not None and hasattr(backend, "clamp_sample_rate"):
                rate, note = backend.clamp_sample_rate(rate)
            else:
                rate = min(int(rate), KEIL_WATCH_MAX_HZ)
                note = keil_watch_rate_warning(rate, len(self._selected_monitor_items()))
        self._scope_rate_note = note
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
            if getattr(self, "_scope_read_source", "swd") == "keil_watch":
                return f"{self._collector._sample_rate} Hz（Keil 限速）"
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
            note = f"  |  {self._scope_rate_note}" if getattr(self, "_scope_rate_note", "") else ""
            detail = (
                f"实际：{self._format_sample_rate(actual)} Hz  |  "
                f"目标：{self._configured_sample_rate_text()}  |  "
                f"显示：{display_text}  |  窗口：{tw}s{scroll_mark}{note}"
            )
            text = f"实际：{self._format_sample_rate(actual)} Hz"
        else:
            note = f"  |  {self._scope_rate_note}" if getattr(self, "_scope_rate_note", "") else ""
            detail = (
                f"实际：-- Hz  |  目标：{self._configured_sample_rate_text()}  |  显示：{display_text}{note}"
            )
            text = "实际：-- Hz"
        batch_detail = self._acquisition_batch_status_detail()
        if batch_detail:
            detail = f"{detail}  |  {batch_detail}"
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
            info = self._scope_registry().get(name)
            addr_text = "Keil" if getattr(self, "_scope_read_source", "swd") == "keil_watch" and info else f"0x{info[0]:08X}" if info else ""
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
        source = getattr(self, "_scope_read_source", "swd")
        if self._collector.is_running:
            return
        if not self._monitored:
            if self._value_table.rowCount() > 0:
                self._value_table.setRowCount(0)
            return

        # Build monitor list from current selections
        monitor_list = self._selected_monitor_items()

        if not monitor_list:
            return

        try:
            if source == "keil_watch":
                backend = self._keil_watch_backend
                if backend is None or not backend.is_connected:
                    now = time.perf_counter()
                    if now < getattr(self, "_keil_watch_next_idle_connect", 0.0):
                        return
                    self._keil_watch_next_idle_connect = now + 2.0
                backend = self._active_scope_backend(connect=True)
            else:
                if not self._backend.is_connected:
                    return
                backend = self._backend
            raw = backend.read_batch(monitor_list)
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
        if not self._elf_path and getattr(self, "_scope_read_source", "swd") != "keil_watch":
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
                for p, (addr, ti) in self._scope_registry().items():
                    addr_text = "Keil" if getattr(self, "_scope_read_source", "swd") == "keil_watch" else f"0x{addr:08X}"
                    writer.writerow([p, addr_text, format_type(ti)])
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
        if getattr(self, "_scope_read_source", "swd") == "keil_watch":
            remembered_variables = sorted(self._monitored)
        else:
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
            "scope_read_source": getattr(self, "_scope_read_source", "swd"),
            "keil_watch_variables": {
                name: format_type(type_info)
                for name, (_addr, type_info) in sorted(self._keil_watch_registry.items())
            },
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
            "debug_keil": {
                "root": str(getattr(self, "_keil_root", Path("D:\\Keil"))),
                "uvsock_port": int(getattr(self, "_debug_uvsock_port", 4827)),
            },
            "debug_sources": self._debug_source_config_record(),
            "serial_port": self._tab_serial.port_combo.currentData() if hasattr(self, "_tab_serial") else "",
            "serial_baudrate": int(self._tab_serial.baud_combo.currentText()) if hasattr(self, "_tab_serial") else 115200,
            "serial_protocol": self._tab_serial.protocol_combo.currentText() if hasattr(self, "_tab_serial") else "FireWater CSV",
        }
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _restore_debug_source_config(self, cfg: dict):
        debug_sources = cfg.get("debug_sources", {}) if isinstance(cfg, dict) else {}
        if not isinstance(debug_sources, dict):
            return
        provider_key = str(debug_sources.get("provider_key", "") or "")
        if provider_key in {"auto", "keil", "compile_commands", "manual_roots", "elf_dwarf", "gdb_text"}:
            self._debug_source_provider_key = provider_key
        compile_path = str(debug_sources.get("compile_commands_path", "") or "")
        if compile_path:
            self._debug_compile_commands_path = Path(compile_path)
        roots = debug_sources.get("manual_source_roots", ())
        if isinstance(roots, (list, tuple)):
            self._debug_manual_source_roots = tuple(Path(str(root)) for root in roots if str(root or ""))
        self._debug_gdb_sources_text = str(debug_sources.get("gdb_sources_text", "") or "")
        self._debug_dwarf_line_table_text = str(debug_sources.get("dwarf_line_table_text", "") or "")
        dwarf_elf = str(debug_sources.get("dwarf_elf_path", "") or "")
        if dwarf_elf:
            self._debug_dwarf_text_elf_path = Path(dwarf_elf)
        remaps = debug_sources.get("remaps", ())
        if isinstance(remaps, (list, tuple)):
            self._debug_source_remaps = [
                {
                    "provider_key": str(item.get("provider_key", "")),
                    "missing_dir": str(item.get("missing_dir", "")),
                    "local_root": str(item.get("local_root", "")),
                }
                for item in remaps
                if isinstance(item, dict) and item.get("missing_dir") and item.get("local_root")
            ]

    def _debug_source_config_record(self) -> dict[str, object]:
        return {
            "provider_key": self._debug_source_provider_key,
            "compile_commands_path": str(self._debug_compile_commands_path or ""),
            "manual_source_roots": [str(root) for root in self._debug_manual_source_roots],
            "gdb_sources_text": self._debug_gdb_sources_text,
            "dwarf_line_table_text": self._debug_dwarf_line_table_text,
            "dwarf_elf_path": str(self._debug_dwarf_text_elf_path or ""),
            "remaps": list(self._debug_source_remaps),
        }

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
        sequence = ShutdownSequence()

        def stop_timers():
            stopped = []
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
                    stopped.append(name)
            return True, f"{len(stopped)} timers"

        def request_backend_shutdown():
            request = getattr(self._backend, "request_shutdown", None)
            if callable(request):
                request()
                return True, "requested"
            return True, "no request hook"

        def stop_sampling():
            self._unlimited_mode = False
            stopped = self._collector.stop(timeout=0.8)
            if not stopped:
                logger.warning("Sampling thread did not stop before shutdown timeout")
                return False, "sampling thread timeout"
            return True, "sampling stopped"

        def stop_serial():
            stopped = self._serial_controller.shutdown(timeout=0.6)
            if not stopped:
                logger.warning("Serial worker did not stop before shutdown timeout")
            if hasattr(self, "_tab_serial"):
                self._tab_serial.set_connected(False)
            return stopped, "serial stopped" if stopped else "serial worker timeout"

        def disconnect_backend():
            try:
                result = self._backend.disconnect(timeout=0.45)
            except TypeError:
                result = self._backend.disconnect()
            ok = True if result is None else bool(result)
            detail = "backend disconnected" if ok else (getattr(self._backend, "last_error", "") or "backend disconnect timeout")
            return ok, detail

        def disconnect_keil_watch():
            backend = getattr(self, "_keil_watch_backend", None)
            if backend is None:
                return True, "no watch session"
            try:
                result = backend.disconnect()
            except Exception as exc:
                return False, str(exc)
            ok = True if result is None else bool(result)
            return ok, "watch disconnected" if ok else (getattr(backend, "last_error", "") or "watch disconnect failed")

        sequence.run("stop timers", stop_timers)
        sequence.run("request backend shutdown", request_backend_shutdown)
        sequence.run("stop sampling", stop_sampling)
        sequence.run("disconnect keil watch", disconnect_keil_watch)
        sequence.run("stop serial", stop_serial)
        sequence.run("save config", self._save_config)
        sequence.run("disconnect backend", disconnect_backend)
        self._shutdown_report = sequence.report()
        for result in self._shutdown_report.steps:
            if not result.ok:
                logger.warning(
                    "关闭步骤未完全完成：%s detail=%s error=%s elapsed=%.1fms",
                    result.label,
                    result.detail,
                    result.error,
                    result.elapsed_ms,
                )
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
