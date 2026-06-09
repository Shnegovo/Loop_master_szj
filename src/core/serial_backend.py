"""Serial backend for text and binary stream protocols.

The module is import-safe when pyserial is missing. Functions that need real
serial I/O raise a clear RuntimeError in that case.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Optional

import numpy as np

from src.core.decoders.serial import (  # noqa: F401
    JUSTFLOAT_TAIL,
    SerialProtocolParser,
    _find_newline,
    _normalise_protocol,
    _parse_firewater_prefixed_values,
    _parse_named_values,
    _parse_number_list,
    _split_tokens,
    _strip_firewater_noise,
)

try:
    import serial
    from serial.tools import list_ports
except Exception as exc:  # pragma: no cover - depends on host environment
    serial = None
    list_ports = None
    _PYSERIAL_IMPORT_ERROR = exc
else:
    _PYSERIAL_IMPORT_ERROR = None


__all__ = [
    "JUSTFLOAT_TAIL",
    "SerialCollector",
    "SerialConfig",
    "SerialPortInfo",
    "SerialProtocolParser",
    "SerialSession",
    "list_serial_ports",
    "pyserial_error_message",
]


@dataclass
class SerialConfig:
    port: str
    baudrate: int = 115200
    protocol: str = "csv"
    channel_names: list[str] = field(default_factory=list)
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    timeout: float = 0.05
    write_timeout: Optional[float] = 1.0
    read_size: int = 4096
    buffer_seconds: float = 30.0
    max_samples: int = 200_000
    max_log_lines: int = 2000
    encoding: str = "utf-8"
    errors: str = "replace"


@dataclass(frozen=True)
class SerialPortInfo:
    device: str
    name: str = ""
    description: str = ""
    hwid: str = ""
    vid: Optional[int] = None
    pid: Optional[int] = None
    serial_number: str = ""
    manufacturer: str = ""
    product: str = ""
    location: str = ""


class SerialCollector:
    """Background serial reader with bounded logs and waveform sample buffers."""

    def __init__(self, config: Optional[SerialConfig] = None):
        self._config = config
        self._parser: Optional[SerialProtocolParser] = None
        self._serial: Any = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.RLock()
        self._logs: deque[tuple[int, float, str]] = deque(maxlen=2000)
        self._log_sequence = 0
        self._buffers: dict[str, _SeriesBuffer] = {}
        self._last_error = ""
        self._t0 = 0.0

    def configure(self, config: SerialConfig) -> None:
        was_running = self.is_running
        if was_running:
            self.stop()
        with self._lock:
            self._config = config
            self._parser = None
            self._logs = deque(maxlen=max(1, config.max_log_lines))
            self._log_sequence = 0
            self._buffers.clear()
            self._last_error = ""

    def start(self, config: Optional[SerialConfig] = None) -> None:
        if config is not None:
            self.configure(config)
        with self._lock:
            if self._running:
                return
            if self._config is None:
                raise RuntimeError("SerialCollector has no SerialConfig.")
            _require_pyserial()
            self._parser = SerialProtocolParser(
                self._config.protocol,
                self._config.channel_names,
                self._config.encoding,
                self._config.errors,
            )
            self._serial = serial.Serial(
                port=self._config.port,
                baudrate=self._config.baudrate,
                bytesize=self._config.bytesize,
                parity=self._config.parity,
                stopbits=self._config.stopbits,
                timeout=self._config.timeout,
                write_timeout=self._config.write_timeout,
            )
            self._running = True
            self._last_error = ""
            self._t0 = time.perf_counter()
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            port = self._serial
        if port is not None:
            try:
                port.cancel_read()
            except Exception:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        with self._lock:
            self._close_serial_locked()
            if self._thread is thread:
                self._thread = None

    def clear(self) -> None:
        with self._lock:
            self._logs.clear()
            self._log_sequence = 0
            self._buffers.clear()
            if self._parser is not None:
                self._parser.reset()

    def write(self, payload: bytes | bytearray | str) -> int:
        with self._lock:
            port = self._serial
            config = self._config
        if port is None or not bool(getattr(port, "is_open", False)):
            raise RuntimeError("Serial port is not open.")
        if isinstance(payload, str):
            encoding = config.encoding if config is not None else "utf-8"
            data = payload.encode(encoding, "replace")
        else:
            data = bytes(payload)
        return int(port.write(data))

    def send(self, payload: bytes | bytearray | str, mode: str = "ascii") -> int:
        if mode == "hex" and isinstance(payload, str):
            payload = bytes.fromhex("".join(payload.split()))
        return self.write(payload)

    def get_data(
        self,
        tail_seconds: Optional[float] = None,
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        with self._lock:
            result = {}
            for name, buffer in self._buffers.items():
                result[name] = buffer.get(tail_seconds)
            return result

    def get_logs(self, max_lines: Optional[int] = None) -> list[tuple[float, str]]:
        with self._lock:
            if max_lines is None or max_lines <= 0:
                return [(timestamp, line) for _, timestamp, line in self._logs]
            return [
                (timestamp, line)
                for _, timestamp, line in list(self._logs)[-max_lines:]
            ]

    def get_logs_since(
        self,
        sequence: int,
        max_lines: Optional[int] = None,
    ) -> tuple[int, list[tuple[float, str]]]:
        with self._lock:
            if sequence > self._log_sequence:
                sequence = 0
            entries = [
                (seq, timestamp, line)
                for seq, timestamp, line in self._logs
                if seq > sequence
            ]
            if max_lines is not None and max_lines > 0:
                entries = entries[-max_lines:]
            return self._log_sequence, [
                (timestamp, line)
                for _, timestamp, line in entries
            ]

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._serial is not None and bool(getattr(self._serial, "is_open", False))

    def _read_loop(self) -> None:
        while True:
            with self._lock:
                running = self._running
                port = self._serial
                parser = self._parser
                config = self._config
            if not running:
                break
            if port is None or parser is None or config is None:
                time.sleep(0.02)
                continue

            try:
                waiting = getattr(port, "in_waiting", 0) or 1
                data = port.read(min(max(waiting, 1), max(1, config.read_size)))
                if not data:
                    continue
                logs, samples = parser.feed(data)
                timestamp = time.perf_counter() - self._t0
                with self._lock:
                    for line in logs:
                        self._log_sequence += 1
                        self._logs.append((self._log_sequence, timestamp, line))
                    for sample in samples:
                        self._append_sample_locked(timestamp, sample, config)
            except Exception as exc:
                with self._lock:
                    self._last_error = f"Serial read stopped: {exc}"
                    self._running = False
                break

        with self._lock:
            self._close_serial_locked()

    def _append_sample_locked(
        self,
        timestamp: float,
        sample: dict[str, float],
        config: SerialConfig,
    ) -> None:
        for name, value in sample.items():
            buffer = self._buffers.get(name)
            if buffer is None:
                buffer = _SeriesBuffer(max(16, config.max_samples))
                self._buffers[name] = buffer
            buffer.append(timestamp, value)

    def _close_serial_locked(self) -> None:
        port = self._serial
        self._serial = None
        if port is None:
            return
        try:
            port.close()
        except Exception:
            pass


SerialSession = SerialCollector


def list_serial_ports() -> list[SerialPortInfo]:
    _require_pyserial()
    ports = []
    for port in list_ports.comports():
        ports.append(
            SerialPortInfo(
                device=str(port.device or ""),
                name=str(port.name or ""),
                description=str(port.description or ""),
                hwid=str(port.hwid or ""),
                vid=port.vid,
                pid=port.pid,
                serial_number=str(port.serial_number or ""),
                manufacturer=str(port.manufacturer or ""),
                product=str(port.product or ""),
                location=str(port.location or ""),
            )
        )
    return ports


def pyserial_error_message() -> str:
    if _PYSERIAL_IMPORT_ERROR is None:
        return ""
    return (
        "pyserial is required for serial support. Install it with "
        "`pip install pyserial` and restart LoopMaster. "
        f"Import error: {_PYSERIAL_IMPORT_ERROR}"
    )


def _require_pyserial() -> None:
    if serial is None or list_ports is None:
        raise RuntimeError(pyserial_error_message())


class _SeriesBuffer:
    def __init__(self, maxlen: int):
        self._timestamps = np.empty(maxlen, dtype=np.float64)
        self._values = np.empty(maxlen, dtype=np.float64)
        self._head = 0
        self._count = 0
        self._maxlen = maxlen

    def append(self, timestamp: float, value: float) -> None:
        self._timestamps[self._head] = timestamp
        self._values[self._head] = value
        self._head = (self._head + 1) % self._maxlen
        if self._count < self._maxlen:
            self._count += 1

    def get(self, tail_seconds: Optional[float]) -> tuple[np.ndarray, np.ndarray]:
        timestamps = self._array(self._timestamps)
        values = self._array(self._values)
        if tail_seconds is None or tail_seconds <= 0 or len(timestamps) == 0:
            return timestamps.copy(), values.copy()
        cutoff = timestamps[-1] - tail_seconds
        start = int(np.searchsorted(timestamps, cutoff, side="left"))
        return timestamps[start:].copy(), values[start:].copy()

    def _array(self, source: np.ndarray) -> np.ndarray:
        if self._count == 0:
            return np.array([], dtype=np.float64)
        start = (self._head - self._count) % self._maxlen
        end = self._head
        if start < end:
            return source[start:end]
        return np.concatenate((source[start:], source[:end]))
