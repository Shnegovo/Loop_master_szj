"""Low-frequency Keil UVSOCK expression watch backend."""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from src.core.models import BaseType, TypeInfo, TypedefType
from src.core.transports import VariableSpec
from src.core.keil.uvsock import KeilUvscLiveSession, uvsc_status_name


KEIL_WATCH_RECOMMENDED_HZ = 20
KEIL_WATCH_MAX_HZ = 50


@dataclass(frozen=True)
class KeilWatchVariable:
    expression: str
    value_type: str = "float"
    label: str = ""


@dataclass(frozen=True)
class KeilWatchSample:
    expression: str
    value: float
    raw_text: str
    status_code: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status_code == 0 and not self.error and math.isfinite(self.value)


@dataclass(frozen=True)
class KeilWatchReadResult:
    samples: tuple[KeilWatchSample, ...]
    warning: str = ""

    @property
    def values(self) -> dict[str, float]:
        return {sample.expression: sample.value for sample in self.samples}

    @property
    def ok(self) -> bool:
        return all(sample.ok for sample in self.samples)


class KeilUvSockWatchBackend:
    """Collector-compatible backend for Keil debug expressions.

    UVSOCK expression evaluation is intentionally treated as a slow watch path:
    it is excellent for live PID tuning panels and low-rate oscilloscope traces,
    but it is not a replacement for SWD memory streaming or serial VOFA data.
    """

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        port: int = 4827,
        connection_name: str = "LoopMasterWatch",
        recommended_hz: int = KEIL_WATCH_RECOMMENDED_HZ,
        max_hz: int = KEIL_WATCH_MAX_HZ,
    ) -> None:
        self.root = Path(root) if root is not None else None
        self.port = int(port)
        self.connection_name = connection_name
        self.recommended_hz = max(1, int(recommended_hz))
        self.max_hz = max(self.recommended_hz, int(max_hz))
        self.last_error = ""
        self.last_warning = ""
        self._lock = threading.RLock()
        self._session: KeilUvscLiveSession | None = None

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._session is not None

    def connect(self) -> bool:
        with self._lock:
            if self._session is not None:
                return True
            self.last_error = ""
            try:
                self._session = KeilUvscLiveSession.connect_existing(
                    root=self.root,
                    port=self.port,
                    connection_name=self.connection_name,
                    require_debug=True,
                )
            except Exception as exc:
                self.last_error = str(exc)
                self._session = None
                return False
            return True

    def disconnect(self) -> bool:
        with self._lock:
            session = self._session
            self._session = None
        if session is not None:
            try:
                session.close()
            except Exception as exc:
                self.last_error = str(exc)
                return False
        return True

    def request_shutdown(self) -> None:
        self.disconnect()

    def clamp_sample_rate(self, requested_hz: int) -> tuple[int, str]:
        requested = max(1, int(requested_hz))
        if requested <= self.max_hz:
            return requested, self.rate_warning(requested)
        return self.max_hz, self.rate_warning(requested)

    def rate_warning(self, requested_hz: int) -> str:
        requested = max(1, int(requested_hz))
        if requested > self.max_hz:
            return (
                f"Keil Watch 通过 UVSOCK 表达式逐项读取，已从 {requested} Hz "
                f"降到 {self.max_hz} Hz；高速波形建议用 SWD/串口。"
            )
        if requested > self.recommended_hz:
            return (
                f"Keil Watch 当前 {requested} Hz，高于建议 {self.recommended_hz} Hz；"
                "变量较多时可能出现抖动或掉样。"
            )
        return ""

    def read_batch(self, variables: Sequence[VariableSpec]) -> dict[str, float]:
        result = self.read_expressions(_watch_variables_from_specs(variables))
        self.last_warning = result.warning
        self.last_error = _first_error(result.samples)
        return result.values

    def read_expressions(self, variables: Sequence[KeilWatchVariable | str]) -> KeilWatchReadResult:
        with self._lock:
            session = self._session
            if session is None:
                raise RuntimeError("Keil Watch backend is not connected")
            samples: list[KeilWatchSample] = []
            for item in variables:
                variable = item if isinstance(item, KeilWatchVariable) else KeilWatchVariable(str(item))
                expression = _sanitize_watch_expression(variable.expression)
                try:
                    raw_text, status = session.evaluate_expression(expression)
                except Exception as exc:
                    samples.append(
                        KeilWatchSample(
                            expression=expression,
                            value=float("nan"),
                            raw_text="",
                            status_code=-1,
                            error=str(exc),
                        )
                    )
                    continue
                if status != 0:
                    samples.append(
                        KeilWatchSample(
                            expression=expression,
                            value=float("nan"),
                            raw_text=raw_text,
                            status_code=status,
                            error=uvsc_status_name(status),
                        )
                    )
                    continue
                value, error = parse_keil_numeric_value(raw_text)
                samples.append(
                    KeilWatchSample(
                        expression=expression,
                        value=value,
                        raw_text=raw_text,
                        status_code=status,
                        error=error,
                    )
                )
        return KeilWatchReadResult(tuple(samples))


def make_keil_watch_type(type_name: str) -> TypeInfo:
    normalized = str(type_name or "float").strip() or "float"
    lowered = normalized.lower()
    if "double" in lowered:
        return BaseType(normalized, 8, "float")
    if "float" in lowered:
        return BaseType(normalized, 4, "float")
    if "64" in lowered or lowered in {"long long", "uint64_t", "int64_t"}:
        size = 8
    elif "16" in lowered or lowered in {"short", "uint16_t", "int16_t"}:
        size = 2
    elif "8" in lowered or lowered in {"char", "uint8_t", "int8_t"}:
        size = 1
    else:
        size = 4
    encoding = "signed" if lowered.startswith("int") or lowered in {"short", "long", "long long"} else "unsigned"
    return BaseType(normalized, size, encoding)


def parse_keil_numeric_value(text: str) -> tuple[float, str]:
    raw = str(text or "").strip()
    if not raw:
        return float("nan"), "empty value"

    token = _extract_numeric_token(raw)
    if not token:
        return float("nan"), f"not numeric: {raw}"
    normalized = token.replace("_", "")
    try:
        if _looks_like_hex_float(normalized):
            return float.fromhex(normalized), ""
        if _looks_like_int(normalized):
            return float(int(normalized, 0)), ""
        return float(normalized), ""
    except ValueError:
        return float("nan"), f"parse failed: {raw}"


def keil_watch_rate_warning(sample_rate: int, variable_count: int = 1) -> str:
    rate = max(1, int(sample_rate))
    count = max(1, int(variable_count))
    effective_reads = rate * count
    if rate > KEIL_WATCH_MAX_HZ:
        return (
            f"Keil Watch 请求 {rate} Hz 超过上限 {KEIL_WATCH_MAX_HZ} Hz，"
            "会自动降档；高速抖动请用 SWD/串口数据源。"
        )
    if rate > KEIL_WATCH_RECOMMENDED_HZ or effective_reads > 120:
        return (
            f"Keil Watch 当前约 {effective_reads} 次表达式读取/秒，"
            "适合调参趋势，不适合捕获高频尖峰。"
        )
    return ""


def _watch_variables_from_specs(variables: Sequence[VariableSpec]) -> tuple[KeilWatchVariable, ...]:
    result = []
    for name, _address, type_info in variables:
        result.append(KeilWatchVariable(str(name), _type_name(type_info)))
    return tuple(result)


def _type_name(type_info: TypeInfo | None) -> str:
    while isinstance(type_info, TypedefType):
        if type_info.name:
            return type_info.name
        type_info = type_info.underlying_type
    if isinstance(type_info, BaseType):
        return type_info.name
    return "float"


def _sanitize_watch_expression(expression: str) -> str:
    value = str(expression or "").strip()
    if not value:
        raise ValueError("watch expression is empty")
    if "\n" in value or "\r" in value:
        raise ValueError("watch expression must be single-line")
    return value[:240]


def _extract_numeric_token(text: str) -> str:
    cleaned = text.strip()
    if "=" in cleaned:
        cleaned = cleaned.rsplit("=", 1)[-1].strip()
    match = re.search(
        r"[-+]?(?:0[xX][0-9A-Fa-f_]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][-+]?\d+)?|nan|inf|infinity)",
        cleaned,
    )
    return match.group(0) if match else ""


def _looks_like_int(token: str) -> bool:
    body = token[1:] if token.startswith(("+", "-")) else token
    return body.startswith(("0x", "0X")) or bool(re.fullmatch(r"\d+", body))


def _looks_like_hex_float(token: str) -> bool:
    lowered = token.lower()
    return lowered.startswith(("0x", "+0x", "-0x")) and ("." in lowered or "p" in lowered)


def _first_error(samples: Sequence[KeilWatchSample]) -> str:
    for sample in samples:
        if sample.error:
            return f"{sample.expression}: {sample.error}"
    return ""
