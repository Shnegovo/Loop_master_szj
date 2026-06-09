"""Qt controller for the serial assistant lifecycle."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from src.core.serial_backend import SerialCollector, SerialConfig, list_serial_ports


class SerialController(QObject):
    """Own serial I/O state so MainWindow only coordinates UI wiring."""

    portsChanged = Signal(object)
    logReceived = Signal(object, str)
    connectedChanged = Signal(bool)
    busyChanged = Signal(bool, str)
    sendEnabledChanged = Signal(bool)
    sendAccepted = Signal()
    scopeDataChanged = Signal(object)

    _connectFinished = Signal(bool, object, str)
    _disconnectFinished = Signal(str)
    _sendFinished = Signal(bool, str, str)

    def __init__(
        self,
        parent: QObject | None = None,
        collector: SerialCollector | None = None,
        port_lister: Callable[[], list[object]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._collector = collector or SerialCollector()
        self._port_lister = port_lister or list_serial_ports
        self._log_sequence = 0
        self._busy = False
        self._connected = False
        self._shutting_down = False
        self._workers: list[threading.Thread] = []

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self.refresh_runtime)
        self._connectFinished.connect(self._finish_connect)
        self._disconnectFinished.connect(self._finish_disconnect)
        self._sendFinished.connect(self._finish_send)

    @property
    def collector(self) -> SerialCollector:
        return self._collector

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def is_connected(self) -> bool:
        return self._connected

    def refresh_ports(self) -> None:
        if self._shutting_down:
            return
        try:
            self.portsChanged.emit(self._port_lister())
        except Exception as exc:
            self.logReceived.emit(str(exc), "rx")

    def connect_serial(self, options: object) -> None:
        if self._shutting_down:
            return
        if self._busy:
            self.logReceived.emit("串口操作尚未完成。", "rx")
            return

        port = str(getattr(options, "port", "") or "").strip()
        if not port or port.lower().startswith("no serial"):
            self.logReceived.emit("未选择串口。", "rx")
            return

        if self._collector.is_running:
            self._collector.stop()

        config = SerialConfig(
            port=port,
            baudrate=int(getattr(options, "baudrate", 115200) or 115200),
            protocol=serial_protocol_key(getattr(options, "protocol", "FireWater CSV")),
            buffer_seconds=60.0,
        )
        self._set_busy(True, "正在打开…")

        def worker() -> None:
            try:
                self._collector.start(config)
            except Exception as exc:
                self._connectFinished.emit(False, config, str(exc))
                return
            self._connectFinished.emit(True, config, "")

        self.start_worker(worker, "LoopMaster-serial-connect")

    def disconnect_serial(self, sync: bool = False) -> None:
        self._poll_timer.stop()
        if sync:
            self._collector.stop()
            self._set_connected(False)
            return
        if self._shutting_down:
            return
        if self._busy:
            self.logReceived.emit("串口操作尚未完成。", "rx")
            return
        self._set_busy(True, "正在关闭…")

        def worker() -> None:
            error = ""
            try:
                self._collector.stop()
            except Exception as exc:
                error = str(exc)
            self._disconnectFinished.emit(error)

        self.start_worker(worker, "LoopMaster-serial-disconnect")

    def clear(self) -> None:
        self._collector.clear()
        self._log_sequence = 0

    def send(self, payload: object, mode: str) -> None:
        if self._shutting_down:
            return
        if not self._collector.is_running:
            self.logReceived.emit("串口未连接。", "tx")
            return

        display = serial_payload_display(payload, mode)
        self.sendEnabledChanged.emit(False)

        def worker() -> None:
            try:
                self._collector.send(payload, mode)
            except Exception as exc:
                self._sendFinished.emit(False, display, str(exc))
                return
            self._sendFinished.emit(True, display, "")

        self.start_worker(worker, "LoopMaster-serial-send")

    def refresh_runtime(self) -> None:
        if self._shutting_down:
            return

        self._log_sequence, logs = self._collector.get_logs_since(self._log_sequence)
        for timestamp, line in logs:
            self.logReceived.emit(f"{timestamp:8.3f}s  {line}", "rx")

        data = self._collector.get_data(tail_seconds=10.0)
        if data:
            self.scopeDataChanged.emit(data)

        if self._connected and not self._collector.is_running:
            error = self._collector.last_error
            self._set_connected(False)
            if error:
                self.logReceived.emit(error, "rx")
            self._poll_timer.stop()

    def channel_count(self, tail_seconds: float = 10.0) -> int:
        try:
            return len(self._collector.get_data(tail_seconds=tail_seconds))
        except Exception:
            return 0

    def start_worker(self, target, name: str) -> None:
        if self._shutting_down:
            return
        self._workers = [worker for worker in self._workers if worker.is_alive()]
        worker = threading.Thread(target=target, name=name, daemon=True)
        self._workers.append(worker)
        worker.start()

    def join_workers(self, timeout: float = 0.6) -> bool:
        deadline = time.perf_counter() + max(0.0, float(timeout))
        workers = list(self._workers)
        all_stopped = True
        for worker in workers:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                all_stopped = False
                break
            worker.join(remaining)
            if worker.is_alive():
                all_stopped = False
        self._workers = [worker for worker in self._workers if worker.is_alive()]
        return all_stopped

    def shutdown(self, timeout: float = 0.6) -> bool:
        if self._shutting_down:
            return self.join_workers(timeout)
        self._shutting_down = True
        self._poll_timer.stop()
        try:
            self._collector.stop()
        finally:
            all_stopped = self.join_workers(timeout)
        self._busy = False
        self._connected = False
        return all_stopped

    def _finish_connect(self, ok: bool, config: object, error: str) -> None:
        if self._shutting_down:
            if ok:
                self._collector.stop()
            return

        self._set_busy(False)
        if not ok:
            self._set_connected(False)
            self.logReceived.emit(f"连接失败：{error}", "rx")
            return

        self._log_sequence = 0
        self._set_connected(True)
        self.logReceived.emit(
            f"已打开 {config.port} @ {config.baudrate}, {config.protocol}",
            "rx",
        )
        self._poll_timer.start(33)

    def _finish_disconnect(self, error: str) -> None:
        if self._shutting_down:
            return
        self._set_busy(False)
        self._set_connected(False)
        if error:
            self.logReceived.emit(f"断开失败：{error}", "rx")
        self.logReceived.emit("串口已断开。", "rx")

    def _finish_send(self, ok: bool, display: str, error: str) -> None:
        if self._shutting_down:
            return
        self.sendEnabledChanged.emit(True)
        if ok:
            self.logReceived.emit(display, "tx")
            self.sendAccepted.emit()
            return
        self.logReceived.emit(f"发送失败：{error}", "tx")

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = bool(busy)
        self.busyChanged.emit(self._busy, label)

    def _set_connected(self, connected: bool) -> None:
        connected = bool(connected)
        if self._connected == connected:
            self.connectedChanged.emit(connected)
            return
        self._connected = connected
        self.connectedChanged.emit(connected)


def serial_protocol_key(label: str) -> str:
    value = (label or "").strip().lower()
    if "just" in value:
        return "justfloat"
    if "raw" in value:
        return "raw"
    return "firewater"


def serial_payload_display(payload: object, mode: str) -> str:
    if mode == "hex" and isinstance(payload, (bytes, bytearray)):
        return bytes(payload).hex(" ")
    if isinstance(payload, bytes):
        return escape_serial_log_text(payload.decode("utf-8", "replace"))
    if isinstance(payload, bytearray):
        return escape_serial_log_text(bytes(payload).decode("utf-8", "replace"))
    return escape_serial_log_text(str(payload))


def escape_serial_log_text(text: str) -> str:
    return text.replace("\r", "\\r").replace("\n", "\\n")
