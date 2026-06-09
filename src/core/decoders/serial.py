"""Serial protocol parsing helpers."""

from __future__ import annotations

import re
import struct
from typing import Optional


JUSTFLOAT_TAIL = b"\x00\x00\x80\x7f"
_NUMBER_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)

__all__ = [
    "JUSTFLOAT_TAIL",
    "SerialProtocolParser",
]


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
