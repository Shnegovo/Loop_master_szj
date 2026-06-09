"""Serial backend for text and binary stream protocols.

The module is import-safe when pyserial is missing. Functions that need real
serial I/O raise a clear RuntimeError in that case.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import re
import struct
import threading
import time
from typing import Any, Optional

import numpy as np

try:
    import serial
    from serial.tools import list_ports
except Exception as exc:  # pragma: no cover - depends on host environment
    serial = None
    list_ports = None
    _PYSERIAL_IMPORT_ERROR = exc
else:
    _PYSERIAL_IMPORT_ERROR = None


JUSTFLOAT_TAIL = b"\x00\x00\x80\x7f"
_NUMBER_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)

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


class SerialProtocolParser:
    """Incremental parser for raw/hex logs, CSV/FireWater lines, and JustFloat."""

    def __init__(
        self,
        protocol: str = "csv",
        channel_names: Optional[list[str]] = None,
        encoding: str = "utf-8",
        errors: str = "replace",
    ):
        self.protocol = _normalise_protocol(protocol)
        self.channel_names = list(channel_names or [])
        self.encoding = encoding
        self.errors = errors
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> tuple[list[str], list[dict[str, float]]]:
        if not data:
            return [], []
        if self.protocol == "justfloat":
            return self._feed_justfloat(data)
        return self._feed_lines(data)

    def parse_line(self, line: str) -> dict[str, float]:
        protocol = self.protocol
        if protocol in ("raw", "hex"):
            return {}
        text = _strip_firewater_noise(line) if protocol == "firewater" else line.strip()
        if not text:
            return {}
        prefixed_values = (
            _parse_firewater_prefixed_values(text)
            if protocol == "firewater"
            else []
        )
        named = _parse_named_values(text)
        if prefixed_values and (not named or len(prefixed_values) > len(named)):
            return self._name_values(prefixed_values)
        if named:
            return named
        values = _parse_number_list(text)
        if not values:
            return {}
        return self._name_values(values)

    def _feed_lines(self, data: bytes) -> tuple[list[str], list[dict[str, float]]]:
        self._buffer.extend(data)
        logs: list[str] = []
        samples: list[dict[str, float]] = []

        while True:
            newline_at = _find_newline(self._buffer)
            if newline_at < 0:
                break
            raw_line = bytes(self._buffer[:newline_at])
            del self._buffer[:newline_at + 1]
            if raw_line.endswith(b"\r"):
                raw_line = raw_line[:-1]
            if self.protocol == "hex":
                logs.append(raw_line.hex(" "))
                continue
            line = raw_line.decode(self.encoding, self.errors).strip()
            if not line:
                continue
            logs.append(line)
            parsed = self.parse_line(line)
            if parsed:
                samples.append(parsed)

        if self.protocol == "raw" and self._buffer:
            logs.append(bytes(self._buffer).decode(self.encoding, self.errors))
            self._buffer.clear()

        if len(self._buffer) > 8192:
            if self.protocol == "hex":
                logs.append(bytes(self._buffer).hex(" "))
            else:
                logs.append(bytes(self._buffer).decode(self.encoding, self.errors))
            self._buffer.clear()
        return logs, samples

    def _feed_justfloat(self, data: bytes) -> tuple[list[str], list[dict[str, float]]]:
        self._buffer.extend(data)
        samples: list[dict[str, float]] = []

        while True:
            tail_at = self._buffer.find(JUSTFLOAT_TAIL)
            if tail_at < 0:
                break
            frame = bytes(self._buffer[:tail_at])
            del self._buffer[:tail_at + len(JUSTFLOAT_TAIL)]
            values = []
            usable = len(frame) - (len(frame) % 4)
            for offset in range(0, usable, 4):
                values.append(struct.unpack_from("<f", frame, offset)[0])
            if values:
                samples.append(self._name_values(values))

        if len(self._buffer) > 8192:
            keep = len(JUSTFLOAT_TAIL) - 1
            del self._buffer[:-keep]
        return [], samples

    def _name_values(self, values: list[float]) -> dict[str, float]:
        result: dict[str, float] = {}
        for index, value in enumerate(values):
            if index < len(self.channel_names) and self.channel_names[index]:
                name = self.channel_names[index]
            else:
                name = f"ch{index + 1}"
            result[name] = float(value)
        return result


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


def _normalise_protocol(protocol: str) -> str:
    value = (protocol or "csv").strip().lower().replace("-", "_")
    aliases = {
        "text": "raw",
        "log": "raw",
        "fire_water": "firewater",
        "fw": "firewater",
        "just_float": "justfloat",
        "justfloat32": "justfloat",
        "float": "justfloat",
    }
    return aliases.get(value, value)


def _find_newline(buffer: bytearray) -> int:
    try:
        return buffer.index(0x0A)
    except ValueError:
        return -1


def _strip_firewater_noise(line: str) -> str:
    text = line.strip()
    if text.lower() == "firewater":
        return ""
    for prefix in ("$FireWater", "FireWater:", "FireWater,"):
        if text.startswith(prefix):
            return text[len(prefix):].strip(" ,:")
    return text


def _parse_firewater_prefixed_values(text: str) -> list[float]:
    name, separator, values = text.partition(":")
    if not separator or not name.strip():
        return []
    return _parse_number_list(values)


def _parse_named_values(text: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for token in _split_tokens(text):
        if ":" in token:
            name, value = token.split(":", 1)
        elif "=" in token:
            name, value = token.split("=", 1)
        else:
            continue
        name = name.strip()
        value = value.strip()
        if name and _NUMBER_RE.match(value):
            result[name] = float(value)
    return result


def _parse_number_list(text: str) -> list[float]:
    values = []
    for token in _split_tokens(text):
        if not _NUMBER_RE.match(token):
            return []
        values.append(float(token))
    return values


def _split_tokens(text: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[,;\t ]+", text.strip())
        if item.strip()
    ]


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
