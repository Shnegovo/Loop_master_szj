"""Debugger variable read/write contracts shared by backend adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DebugResolvedVariable:
    expression: str
    symbol: str = ""
    address: int | None = None
    size: int = 0
    type_name: str = ""
    source: str = ""
    ram_checked: bool = False

    def to_record(self) -> dict[str, object]:
        return {
            "expression": self.expression,
            "symbol": self.symbol,
            "address": f"0x{self.address:08X}" if self.address is not None else "",
            "size": int(self.size),
            "type_name": self.type_name,
            "source": self.source,
            "ram_checked": self.ram_checked,
        }


@dataclass(frozen=True)
class DebugVariableReadRequest:
    expression: str
    binary_path: Path | None = None
    address: int | None = None
    type_name: str = ""
    connection_name: str = "LoopMasterVariableRead"


@dataclass(frozen=True)
class DebugVariableWriteRequest:
    expression: str
    value_text: str
    binary_path: Path | None = None
    address: int | None = None
    type_name: str = ""
    prefer_memory: bool = True
    allow_command_fallback: bool = True
    connection_name: str = "LoopMasterVariableWrite"


@dataclass(frozen=True)
class DebugVariableReadResult:
    attempted: bool
    read: bool
    expression: str
    method: str
    backend: str = ""
    resolved: DebugResolvedVariable | None = None
    raw: bytes = b""
    value: str = ""
    diagnostics: tuple[tuple[str, str], ...] = ()
    error: str = ""

    def summary(self) -> str:
        if self.read:
            target = self.expression
            if self.resolved is not None and self.resolved.address is not None:
                target += f" @ 0x{self.resolved.address:08X}"
            return f"变量读取成功：{target} = {self.value} ({self.method})"
        return f"变量读取失败：{self.expression} ({self.error or self.method or '--'})"

    def to_record(self) -> dict[str, object]:
        return {
            "attempted": self.attempted,
            "read": self.read,
            "expression": self.expression,
            "method": self.method,
            "backend": self.backend,
            "resolved": self.resolved.to_record() if self.resolved is not None else None,
            "raw": self.raw.hex(),
            "value": self.value,
            "diagnostics": [{"key": key, "value": value} for key, value in self.diagnostics],
            "error": self.error,
        }


@dataclass(frozen=True)
class DebugVariableWriteResult:
    attempted: bool
    written: bool
    expression: str
    value_text: str
    method: str
    backend: str = ""
    resolved: DebugResolvedVariable | None = None
    old_raw: bytes = b""
    new_raw: bytes = b""
    readback_raw: bytes = b""
    old_value: str = ""
    readback_value: str = ""
    command: str = ""
    attempts: tuple[str, ...] = ()
    diagnostics: tuple[tuple[str, str], ...] = ()
    error: str = ""

    def summary(self) -> str:
        if self.written:
            target = self.expression
            if self.resolved is not None and self.resolved.address is not None:
                target += f" @ 0x{self.resolved.address:08X}"
            if self.readback_value:
                return f"变量写入已回读：{target} = {self.readback_value} ({self.method})"
            return f"变量写入已提交：{target} = {self.value_text} ({self.method})"
        return f"变量写入失败：{self.expression} ({self.error or self.method or '--'})"

    def to_record(self) -> dict[str, object]:
        return {
            "attempted": self.attempted,
            "written": self.written,
            "expression": self.expression,
            "value_text": self.value_text,
            "method": self.method,
            "backend": self.backend,
            "resolved": self.resolved.to_record() if self.resolved is not None else None,
            "old_raw": self.old_raw.hex(),
            "new_raw": self.new_raw.hex(),
            "readback_raw": self.readback_raw.hex(),
            "old_value": self.old_value,
            "readback_value": self.readback_value,
            "command": self.command,
            "attempts": list(self.attempts),
            "diagnostics": [{"key": key, "value": value} for key, value in self.diagnostics],
            "error": self.error,
        }


@dataclass(frozen=True)
class DebugVariableSmokeResult:
    read: DebugVariableReadResult | None
    write: DebugVariableWriteResult

    @property
    def succeeded(self) -> bool:
        return bool(self.write.written and not self.write.error)

    def summary(self) -> str:
        if self.read is not None and self.read.read:
            return f"{self.write.summary()}；写前 {self.read.value}"
        return self.write.summary()


class DebugVariableAccessAdapter(Protocol):
    def read_variable(
        self,
        request: DebugVariableReadRequest,
        *,
        require_debug: bool = True,
    ) -> DebugVariableReadResult:
        ...

    def write_variable(
        self,
        request: DebugVariableWriteRequest,
        *,
        require_debug: bool = True,
    ) -> DebugVariableWriteResult:
        ...

    def smoke_variable_write(
        self,
        request: DebugVariableWriteRequest,
        *,
        require_debug: bool = True,
        read_before_write: bool = True,
    ) -> DebugVariableSmokeResult:
        ...
