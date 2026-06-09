"""Standalone serial assistant tab for LoopMaster."""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.ui.pcl_theme import PclComboBox, polish_combo_popup


@dataclass(frozen=True)
class SerialConnectOptions:
    port: str
    baudrate: int
    protocol: str


class SerialTab(QWidget):
    """Modern, host-window agnostic serial assistant page."""

    connectRequested = Signal(object)
    disconnectRequested = Signal()
    refreshPortsRequested = Signal()
    clearRequested = Signal()
    pauseChanged = Signal(bool)
    sendRequested = Signal(object, str)
    protocolChanged = Signal(str)

    BAUD_RATES = (
        "9600",
        "19200",
        "38400",
        "57600",
        "115200",
        "230400",
        "460800",
        "921600",
        "1000000",
        "2000000",
    )
    PROTOCOLS = ("Raw", "FireWater CSV", "JustFloat")
    MAX_LOG_BLOCKS = 3000
    MAX_POINTS = 4000
    CURVE_COLORS = ("#2563eb", "#0ea5a5", "#f97316", "#7c3aed", "#ef4444", "#0284c7")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = None
        self._connected = False
        self._paused = False
        self._ports_managed = False
        self._t0 = time.monotonic()
        self._series_data: dict[str, tuple[deque[float], deque[float]]] = {}
        self._curves: dict[str, object] = {}

        self.setObjectName("serialTab")
        self._build_ui()
        self._polish_combos()
        self._apply_style()
        self.refresh_ports()

    def attach_session(self, session: object | None) -> None:
        """Attach a future serial backend without depending on MainWindow."""
        self._session = session
        if session is None:
            return

        self._connect_optional_signal(session, "portsChanged", self.set_ports)
        self._connect_optional_signal(session, "logReceived", self.append_log)
        self._connect_optional_signal(session, "connectedChanged", self.set_connected)
        self._connect_optional_signal(session, "samplesReceived", self.add_samples)

        if hasattr(session, "list_ports"):
            ports = session.list_ports()
            if ports is not None:
                self.set_ports(ports)

    def refresh_ports(self) -> None:
        if not self._ports_managed:
            ports = list(_discover_serial_ports())
            self.set_ports(ports)
        self.refreshPortsRequested.emit()

    def set_ports_managed(self, managed: bool) -> None:
        self._ports_managed = bool(managed)

    def set_ports(self, ports: Iterable[object]) -> None:
        current = self.port_combo.currentText()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for port in ports:
            text = _port_label(port)
            value = _port_value(port)
            self.port_combo.addItem(text, value)
        if self.port_combo.count() == 0:
            self.port_combo.addItem("未发现串口", "")
        elif current:
            index = self.port_combo.findText(current)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)
        self.port_combo.blockSignals(False)
        self.port_combo.setMaxVisibleItems(max(1, self.port_combo.count()))
        polish_combo_popup(self.port_combo)

    def append_log(self, text: object, level: str = "rx") -> None:
        if self._paused:
            return
        prefix = "RX" if level.lower() != "tx" else "TX"
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {prefix}  {text}")
        document = self.log_view.document()
        if document.blockCount() > self.MAX_LOG_BLOCKS:
            cursor = self.log_view.textCursor()
            cursor.movePosition(cursor.Start)
            for _ in range(document.blockCount() - self.MAX_LOG_BLOCKS):
                cursor.select(cursor.BlockUnderCursor)
                cursor.removeSelectedText()
                cursor.deleteChar()

    def set_connected(self, connected: bool) -> None:
        self._connected = bool(connected)
        self.connect_button.setText("断开" if self._connected else "连接")
        self.status_dot.setProperty("connected", self._connected)
        self.status_text.setText("已连接" if self._connected else "空闲")
        for widget in (self.port_combo, self.baud_combo, self.protocol_combo, self.refresh_button):
            widget.setEnabled(not self._connected)
        self._repolish(self.connect_button, self.status_dot)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def add_sample(self, value: float, x: float | None = None, name: str = "ch1") -> None:
        x_value = float(x) if x is not None else time.monotonic() - self._t0
        x_data, y_data = self._series_data.setdefault(
            name,
            (deque(maxlen=self.MAX_POINTS), deque(maxlen=self.MAX_POINTS)),
        )
        x_data.append(x_value)
        y_data.append(float(value))
        self._ensure_curve(name).setData(list(x_data), list(y_data))

    def add_samples(self, samples: Sequence[object]) -> None:
        for item in samples:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                self.add_sample(float(item[1]), float(item[0]))
            else:
                self.add_sample(float(item))

    def set_scope_data(self, data: Mapping[str, tuple[Sequence[float], Sequence[float]]]) -> None:
        visible_names = set()
        for name, (x_values, y_values) in data.items():
            if len(x_values) == 0 or len(y_values) == 0:
                continue
            visible_names.add(str(name))
            curve = self._ensure_curve(str(name))
            curve.setData(
                _tail_values(x_values, self.MAX_POINTS),
                _tail_values(y_values, self.MAX_POINTS),
            )
        for name, curve in list(self._curves.items()):
            if name not in visible_names:
                curve.clear()

    def clear(self) -> None:
        self.log_view.clear()
        self._series_data.clear()
        for curve in self._curves.values():
            curve.clear()
        self.clearRequested.emit()

    def selected_options(self) -> SerialConnectOptions:
        return SerialConnectOptions(
            port=str(self.port_combo.currentData() or self.port_combo.currentText()),
            baudrate=int(self.baud_combo.currentText()),
            protocol=self.protocol_combo.currentText(),
        )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        root.addWidget(self._build_connection_bar())

        splitter = QSplitter(Qt.Vertical)
        splitter.setObjectName("serialSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(4)
        splitter.setOpaqueResize(False)
        splitter.addWidget(self._build_scope_panel())
        splitter.addWidget(self._build_console_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([420, 300])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_send_bar())

    def _build_connection_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("serialCard")
        layout = QGridLayout(bar)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("serialStatusDot")
        self.status_text = QLabel("空闲")
        self.status_text.setObjectName("serialStatusText")
        status = QHBoxLayout()
        status.setSpacing(6)
        status.addWidget(self.status_dot)
        status.addWidget(self.status_text)
        status.addStretch(1)
        layout.addLayout(status, 0, 0)

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("serialToolButton")
        self.refresh_button.clicked.connect(self.refresh_ports)
        layout.addWidget(self.refresh_button, 0, 1)

        self.port_combo = PclComboBox()
        self.port_combo.setObjectName("serialCombo")
        self.port_combo.setMinimumWidth(190)
        layout.addWidget(self.port_combo, 0, 2)

        self.baud_combo = PclComboBox()
        self.baud_combo.setObjectName("serialCombo")
        self.baud_combo.addItems(self.BAUD_RATES)
        self.baud_combo.setCurrentText("115200")
        layout.addWidget(self.baud_combo, 0, 3)

        self.protocol_combo = PclComboBox()
        self.protocol_combo.setObjectName("serialCombo")
        self.protocol_combo.addItems(self.PROTOCOLS)
        self.protocol_combo.setCurrentText("FireWater CSV")
        self.protocol_combo.currentTextChanged.connect(self.protocolChanged.emit)
        layout.addWidget(self.protocol_combo, 0, 4)

        self.connect_button = QPushButton("连接")
        self.connect_button.setObjectName("serialPrimaryButton")
        self.connect_button.clicked.connect(self._on_connect_clicked)
        layout.addWidget(self.connect_button, 0, 5)
        layout.setColumnStretch(2, 1)
        return bar

    def _build_scope_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("serialCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("示波")
        title.setObjectName("serialSectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        hint = QLabel("实时曲线")
        hint.setObjectName("serialHint")
        header.addWidget(hint)
        layout.addLayout(header)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setObjectName("serialPlot")
        self.plot_widget.setBackground("#ffffff")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.22)
        self.plot_widget.setLabel("bottom", "时间", units="s")
        self.plot_widget.setLabel("left", "值")
        self.plot_widget.getPlotItem().setMenuEnabled(False)
        self.plot_widget.getPlotItem().hideButtons()
        for axis_name in ("left", "bottom"):
            try:
                self.plot_widget.getPlotItem().getAxis(axis_name).enableAutoSIPrefix(False)
            except Exception:
                pass
        self.plot_widget.addLegend(offset=(8, 8))
        layout.addWidget(self.plot_widget, 1)
        return panel

    def _ensure_curve(self, name: str):
        curve = self._curves.get(name)
        if curve is not None:
            return curve
        color = self.CURVE_COLORS[len(self._curves) % len(self.CURVE_COLORS)]
        curve = self.plot_widget.plot(name=name, pen=pg.mkPen(color, width=2))
        self._curves[name] = curve
        return curve

    def _build_console_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("serialCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("串口日志")
        title.setObjectName("serialSectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.pause_button = QPushButton("暂停日志")
        self.pause_button.setObjectName("serialToolButton")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._on_pause_toggled)
        self.clear_button = QPushButton("清空")
        self.clear_button.setObjectName("serialToolButton")
        self.clear_button.clicked.connect(self.clear)
        header.addWidget(self.pause_button)
        header.addWidget(self.clear_button)
        layout.addLayout(header)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("serialLog")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(self.MAX_LOG_BLOCKS + 64)
        self.log_view.setPlaceholderText("收发串口数据会显示在这里。")
        layout.addWidget(self.log_view, 1)
        return panel

    def _build_send_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("serialCard")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self.send_mode_group = QButtonGroup(self)
        self.send_mode_group.setExclusive(True)
        self.ascii_button = QPushButton("ASCII")
        self.hex_button = QPushButton("HEX")
        for index, button in enumerate((self.ascii_button, self.hex_button)):
            button.setObjectName("serialSegmentButton")
            button.setCheckable(True)
            button.setMinimumWidth(62)
            self.send_mode_group.addButton(button, index)
            layout.addWidget(button)
        self.ascii_button.setChecked(True)

        self.line_ending_combo = PclComboBox()
        self.line_ending_combo.setObjectName("serialCombo")
        self.line_ending_combo.setMinimumWidth(82)
        self.line_ending_combo.addItem("无结尾", "")
        self.line_ending_combo.addItem("LF", "\n")
        self.line_ending_combo.addItem("CRLF", "\r\n")
        self.line_ending_combo.setCurrentText("LF")
        layout.addWidget(self.line_ending_combo)

        self.send_edit = QLineEdit()
        self.send_edit.setObjectName("serialSendEdit")
        self.send_edit.setPlaceholderText("输入要发送的数据")
        self.send_edit.returnPressed.connect(self._send_current_input)
        layout.addWidget(self.send_edit, 1)

        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("serialPrimaryButton")
        self.send_button.clicked.connect(self._send_current_input)
        layout.addWidget(self.send_button)
        return bar

    def _polish_combos(self) -> None:
        for combo in (
            self.port_combo,
            self.baud_combo,
            self.protocol_combo,
            self.line_ending_combo,
        ):
            combo.setMaxVisibleItems(max(1, combo.count()))
            polish_combo_popup(combo)

    def _on_connect_clicked(self) -> None:
        if self._connected:
            self.disconnectRequested.emit()
            return

        options = self.selected_options()
        self.connectRequested.emit(options)

    def _on_pause_toggled(self, paused: bool) -> None:
        self._paused = bool(paused)
        self.pause_button.setText("继续日志" if self._paused else "暂停日志")
        self.pauseChanged.emit(self._paused)

    def _send_current_input(self) -> None:
        text = self.send_edit.text()
        if not text:
            return
        mode = "hex" if self.hex_button.isChecked() else "ascii"
        payload: object = text
        if mode == "hex":
            try:
                payload = _parse_hex_payload(text)
            except ValueError:
                self.append_log("HEX 格式错误，可输入 38、AA 55 或 0x38。", "tx")
                return
        else:
            payload = text + str(self.line_ending_combo.currentData() or "")
        self.sendRequested.emit(payload, mode)

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QWidget#serialTab {
                background: #f6f8fb;
                color: #0f172a;
                font-family: "Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI", sans-serif;
                font-size: 10pt;
            }
            QFrame#serialCard {
                background: #ffffff;
                border: 1px solid #dce6f0;
                border-radius: 8px;
            }
            QLabel#serialSectionTitle {
                color: #111827;
                font-size: 11pt;
                font-weight: 600;
            }
            QLabel#serialHint, QLabel#serialStatusText {
                color: #64748b;
                font-size: 9pt;
            }
            QLabel#serialStatusDot {
                color: #ef4444;
                font-size: 14px;
            }
            QLabel#serialStatusDot[connected="true"] {
                color: #16a34a;
            }
            QComboBox#serialCombo, QLineEdit#serialSendEdit, QPlainTextEdit#serialLog {
                background: #f8fbff;
                border: 1px solid #d2deea;
                border-radius: 7px;
                padding: 7px 9px;
                selection-background-color: #2563eb;
                selection-color: white;
            }
            QComboBox#serialCombo:hover, QLineEdit#serialSendEdit:hover, QPlainTextEdit#serialLog:hover {
                border-color: #9fb8d4;
            }
            QComboBox#serialCombo:focus, QLineEdit#serialSendEdit:focus, QPlainTextEdit#serialLog:focus {
                border-color: #2563eb;
                background: #ffffff;
            }
            QFrame#comboPopupWindow {
                background: #ffffff;
                border: 1px solid #cfddec;
                border-radius: 8px;
            }
            QFrame#comboPopupWindow QListView#comboPopupView, QAbstractItemView#comboPopupView {
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
                margin: 0px;
                color: #0f172a;
                outline: 0;
                selection-background-color: #2563eb;
                selection-color: #ffffff;
            }
            QFrame#comboPopupWindow QListView#comboPopupView::item, QAbstractItemView#comboPopupView::item {
                min-height: 24px;
                padding: 4px 8px;
                margin: 0px;
                border-radius: 4px;
            }
            QFrame#comboPopupWindow QListView#comboPopupView::item:hover, QAbstractItemView#comboPopupView::item:hover {
                background: #eef6ff;
                color: #1d4ed8;
            }
            QFrame#comboPopupWindow QListView#comboPopupView::item:selected, QAbstractItemView#comboPopupView::item:selected {
                background: #2563eb;
                color: #ffffff;
            }
            QPlainTextEdit#serialLog {
                font-family: "Cascadia Code", "Consolas", monospace;
                font-size: 9.5pt;
            }
            QPushButton#serialPrimaryButton, QPushButton#serialToolButton, QPushButton#serialSegmentButton {
                min-height: 34px;
                border-radius: 7px;
                padding: 6px 12px;
                font-weight: 560;
            }
            QPushButton#serialPrimaryButton {
                background: #2563eb;
                border: 1px solid #2563eb;
                color: #ffffff;
            }
            QPushButton#serialPrimaryButton:hover {
                background: #1d4ed8;
                border-color: #1d4ed8;
            }
            QPushButton#serialToolButton, QPushButton#serialSegmentButton {
                background: #f8fbff;
                border: 1px solid #d2deea;
                color: #0f172a;
            }
            QPushButton#serialToolButton:hover, QPushButton#serialSegmentButton:hover {
                background: #eef6ff;
                border-color: #9fb8d4;
            }
            QPushButton#serialToolButton:checked, QPushButton#serialSegmentButton:checked {
                background: #e0edff;
                border-color: #2563eb;
                color: #1d4ed8;
            }
            QSplitter#serialSplitter::handle {
                background: transparent;
                border-top: 1px solid #c7d5e4;
                height: 4px;
            }
        """)

    def _repolish(self, *widgets: QWidget) -> None:
        for widget in widgets:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def _connect_optional_signal(self, owner: object, name: str, slot: object) -> None:
        signal = getattr(owner, name, None)
        if hasattr(signal, "connect"):
            signal.connect(slot)


def _discover_serial_ports() -> list[object]:
    try:
        from serial.tools import list_ports

        return list(list_ports.comports())
    except Exception:
        pass

    try:
        from PySide6.QtSerialPort import QSerialPortInfo

        return list(QSerialPortInfo.availablePorts())
    except Exception:
        return []


def _tail_values(values: Sequence[float], max_points: int):
    if len(values) <= max_points:
        return values if not isinstance(values, deque) else list(values)
    try:
        return values[-max_points:]
    except (TypeError, KeyError):
        return list(values)[-max_points:]


def _parse_hex_payload(text: str) -> bytes:
    raw = (text or "").strip()
    if not raw:
        return b""
    if re.search(r"[\s,;]", raw):
        tokens = [token for token in re.split(r"[\s,;]+", raw) if token]
        hex_text = "".join(_normalise_hex_token(token) for token in tokens)
    else:
        hex_text = raw[2:] if raw.lower().startswith("0x") else raw
        if len(hex_text) % 2:
            hex_text = "0" + hex_text
    if not re.fullmatch(r"[0-9a-fA-F]+", hex_text):
        raise ValueError("invalid hex")
    return bytes.fromhex(hex_text)


def _normalise_hex_token(token: str) -> str:
    value = token.strip()
    if value.lower().startswith("0x"):
        value = value[2:]
    if not value or not re.fullmatch(r"[0-9a-fA-F]+", value):
        raise ValueError("invalid hex")
    if len(value) % 2:
        value = "0" + value
    return value


def _port_label(port: object) -> str:
    device = getattr(port, "device", None) or getattr(port, "portName", lambda: "")()
    description = getattr(port, "description", None)
    if callable(description):
        description = description()
    if description and description != device:
        return f"{device} - {description}"
    return str(device or port)


def _port_value(port: object) -> str:
    device = getattr(port, "device", None)
    if device:
        return str(device)
    port_name = getattr(port, "portName", None)
    if callable(port_name):
        return str(port_name())
    return str(port)
