"""Backend-neutral debugger snapshot data models."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class RemoteBreakpoint:
    path: Path | None = None
    line: int = 0
    enabled: bool | None = None
    condition: str | None = ""
    remote_id: str = ""
    raw_location: str = ""
    verified: bool = True
    message: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path is not None else "",
            "line": self.line,
            "enabled": self.enabled,
            "condition": self.condition,
            "remote_id": self.remote_id,
            "raw_location": self.raw_location,
            "verified": self.verified,
            "message": self.message,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | object) -> "RemoteBreakpoint":
        if isinstance(record, cls):
            return record
        condition = _get(record, "condition", "")
        return cls(
            path=_path_or_none(_get(record, "path", None)),
            line=_int_or_default(_get(record, "line", 0), 0),
            enabled=_bool_or_none(_get(record, "enabled", None)),
            condition=None if condition is None else str(condition),
            remote_id=str(_get(record, "remote_id", "") or ""),
            raw_location=str(_get(record, "raw_location", "") or ""),
            verified=_bool_or_default(_get(record, "verified", True), True),
            message=str(_get(record, "message", "") or ""),
        )


@dataclass(frozen=True)
class RemoteBreakpointSnapshot:
    schema_version: int
    snapshot_id: str
    project_path: Path | None
    target_name: str
    captured_at: str
    complete: bool
    breakpoints: tuple[RemoteBreakpoint, ...]
    error: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "project_path": str(self.project_path) if self.project_path else "",
            "target_name": self.target_name,
            "captured_at": self.captured_at,
            "complete": self.complete,
            "error": self.error,
            "breakpoints": [item.to_record() for item in self.breakpoints],
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | object) -> "RemoteBreakpointSnapshot":
        if isinstance(record, cls):
            return record
        source = _record_source(record)
        breakpoints = _get(source, "breakpoints", ()) or ()
        return cls(
            schema_version=_int_or_default(_get(source, "schema_version", 1), 1),
            snapshot_id=str(_get(source, "snapshot_id", "") or ""),
            project_path=_path_or_none(_get(source, "project_path", None)),
            target_name=str(_get(source, "target_name", "") or ""),
            captured_at=str(_get(source, "captured_at", "") or ""),
            complete=_bool_or_default(_get(source, "complete", False), False),
            breakpoints=tuple(RemoteBreakpoint.from_record(item) for item in breakpoints),
            error=str(_get(source, "error", "") or ""),
        )


@dataclass(frozen=True)
class DebugPcLocation:
    path: Path | None = None
    line: int | None = None
    address: int | None = None
    function: str = ""
    source: str = ""
    complete: bool = False
    message: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else "",
            "line": self.line,
            "address": self.address,
            "function": self.function,
            "source": self.source,
            "complete": self.complete,
            "message": self.message,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | object) -> "DebugPcLocation":
        if isinstance(record, cls):
            return record
        source = _record_source(record)
        return cls(
            path=_path_or_none(_get(source, "path", None)),
            line=_int_or_none(_get(source, "line", None)),
            address=_int_or_none(_get(source, "address", None)),
            function=str(_get(source, "function", "") or ""),
            source=str(_get(source, "source", "") or ""),
            complete=_bool_or_default(_get(source, "complete", False), False),
            message=str(_get(source, "message", "") or ""),
        )


@dataclass(frozen=True)
class TargetSnapshot:
    schema_version: int = 1
    backend: str = ""
    adapter_name: str = ""
    snapshot_id: str = ""
    captured_at: str = ""
    state: str = ""
    label: str = ""
    detail: str = ""
    read_only: bool = True
    connection_attempted: bool = False
    connection_established: bool = False
    target_running: bool | None = None
    port: int | None = None
    project_path: Path | None = None
    target_name: str = ""
    pc_location: DebugPcLocation | None = None
    remote_breakpoint_snapshot: RemoteBreakpointSnapshot | None = None
    remote_breakpoint_snapshot_id: str = ""
    diagnostics: tuple[tuple[str, str], ...] = ()
    capabilities: tuple[tuple[str, bool], ...] = ()
    error: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "backend": self.backend,
            "adapter_name": self.adapter_name,
            "snapshot_id": self.snapshot_id,
            "captured_at": self.captured_at,
            "state": self.state,
            "label": self.label,
            "detail": self.detail,
            "read_only": self.read_only,
            "connection_attempted": self.connection_attempted,
            "connection_established": self.connection_established,
            "target_running": self.target_running,
            "port": self.port,
            "project_path": str(self.project_path) if self.project_path else "",
            "target_name": self.target_name,
            "pc_location": self.pc_location.to_record() if self.pc_location else None,
            "remote_breakpoint_snapshot": (
                self.remote_breakpoint_snapshot.to_record()
                if self.remote_breakpoint_snapshot is not None
                else None
            ),
            "remote_breakpoint_snapshot_id": self.remote_breakpoint_snapshot_id,
            "diagnostics": [
                {"key": key, "value": value}
                for key, value in self.diagnostics
            ],
            "capabilities": dict(self.capabilities),
            "error": self.error,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | object) -> "TargetSnapshot":
        if isinstance(record, cls):
            return record
        source = _record_source(record)
        status = _get(source, "status", {}) or {}
        pc_record = _get(source, "pc_location", None)
        remote_record = _get(source, "remote_breakpoint_snapshot", None)
        return cls(
            schema_version=_int_or_default(_get(source, "schema_version", 1), 1),
            backend=_enum_text(_get(source, "backend", _get(status, "backend", ""))),
            adapter_name=str(_get(source, "adapter_name", "") or ""),
            snapshot_id=str(_get(source, "snapshot_id", "") or ""),
            captured_at=str(_get(source, "captured_at", "") or ""),
            state=_enum_text(_get(source, "state", _get(status, "state", ""))),
            label=str(_get(source, "label", _get(status, "label", "")) or ""),
            detail=str(_get(source, "detail", _get(status, "detail", "")) or ""),
            read_only=_bool_or_default(_get(source, "read_only", True), True),
            connection_attempted=_bool_or_default(_get(source, "connection_attempted", False), False),
            connection_established=_bool_or_default(_get(source, "connection_established", False), False),
            target_running=_bool_or_none(_get(source, "target_running", None)),
            port=_int_or_none(_get(source, "port", None)),
            project_path=_path_or_none(_get(source, "project_path", _get(status, "project_path", None))),
            target_name=str(_get(source, "target_name", _get(status, "target_name", "")) or ""),
            pc_location=DebugPcLocation.from_record(pc_record) if pc_record is not None else None,
            remote_breakpoint_snapshot=(
                RemoteBreakpointSnapshot.from_record(remote_record)
                if remote_record is not None
                else None
            ),
            remote_breakpoint_snapshot_id=str(_get(source, "remote_breakpoint_snapshot_id", "") or ""),
            diagnostics=_diagnostic_items(_get(source, "diagnostics", ())),
            capabilities=_capability_items(_get(source, "capabilities", _get(status, "capabilities", ()))),
            error=str(_get(source, "error", _get(status, "error", "")) or ""),
        )


def to_record(value: object) -> Any:
    if hasattr(value, "to_record") and callable(getattr(value, "to_record")):
        return value.to_record()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: to_record(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_record(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_record(item) for item in value]
    return value


def from_record(model: type[T], record: Mapping[str, Any] | object) -> T:
    factory = getattr(model, "from_record", None)
    if callable(factory):
        return factory(record)
    raise TypeError(f"{model!r} does not provide from_record()")


def _record_source(record: Mapping[str, Any] | object) -> Mapping[str, Any] | object:
    if isinstance(record, Mapping):
        return record
    to_record_method = getattr(record, "to_record", None)
    if callable(to_record_method):
        return to_record_method()
    return record


def _get(source: Mapping[str, Any] | object, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _path_or_none(value: object) -> Path | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return Path(text)


def _int_or_default(value: object, default: int) -> int:
    result = _int_or_none(value)
    return default if result is None else result


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_default(value: object, default: bool) -> bool:
    result = _bool_or_none(value)
    return default if result is None else result


def _bool_or_none(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if lowered in {"0", "false", "no", "n", "off", "disabled"}:
            return False
        return None
    return bool(value)


def _enum_text(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value or "")


def _diagnostic_items(value: object) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for item in _iter_items(value):
        if isinstance(item, Mapping):
            rows.append((str(item.get("key", "") or ""), str(item.get("value", "") or "")))
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            rows.append((str(item[0]), str(item[1])))
        else:
            rows.append((str(_get(item, "key", "") or ""), str(_get(item, "value", "") or "")))
    return tuple(rows)


def _capability_items(value: object) -> tuple[tuple[str, bool], ...]:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _bool_or_default(item, False)) for key, item in value.items()))
    rows: list[tuple[str, bool]] = []
    for item in _iter_items(value):
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            rows.append((str(item[0]), _bool_or_default(item[1], False)))
    return tuple(sorted(rows))


def _iter_items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return ()


__all__ = [
    "DebugPcLocation",
    "RemoteBreakpoint",
    "RemoteBreakpointSnapshot",
    "TargetSnapshot",
    "from_record",
    "to_record",
]
