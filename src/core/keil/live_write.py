"""Explicit Keil/UVSOCK live variable write service."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.core.models import (
    ArrayType,
    BaseType,
    EnumType,
    FuncType,
    PointerType,
    StructType,
    Symbol,
    TypeInfo,
    TypedefType,
    Variable,
)
from src.core.keil.uvsock import KeilUvscLiveSession
from src.parser.readelf import parse_debug_info, parse_symbol_table


class KeilMemorySession(Protocol):
    def execute_command(self, command: str, *, echo: bool = False) -> None:
        ...

    def read_memory(self, address: int, size: int) -> bytes:
        ...

    def write_memory(self, address: int, data: bytes) -> None:
        ...


@dataclass(frozen=True)
class KeilLiveVariableWriteRequest:
    expression: str
    value_text: str
    axf_path: Path | None = None
    address: int | None = None
    type_name: str = ""
    prefer_memory: bool = True
    allow_command_fallback: bool = True
    connection_name: str = "LoopMaster"


@dataclass(frozen=True)
class KeilLiveVariableReadRequest:
    expression: str
    axf_path: Path | None = None
    address: int | None = None
    type_name: str = ""
    connection_name: str = "LoopMaster"


@dataclass(frozen=True)
class KeilResolvedVariable:
    expression: str
    symbol: str
    address: int
    size: int
    type_info: TypeInfo | None = None
    type_name: str = ""
    source: str = ""
    ram_checked: bool = False


@dataclass(frozen=True)
class KeilLiveVariableWriteResult:
    attempted: bool
    written: bool
    expression: str
    value_text: str
    method: str
    resolved: KeilResolvedVariable | None = None
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
            if self.resolved is not None:
                target += f" @ 0x{self.resolved.address:08X}"
            if self.readback_raw:
                return f"Keil 写变量已回读：{target} = {self.readback_value or self.value_text} ({self.method})"
            return f"Keil 写变量已提交：{target} = {self.value_text} ({self.method}，未独立回读)"
        return f"Keil 写变量失败：{self.expression} ({self.error or self.method or '--'})"

    def to_record(self) -> dict[str, object]:
        resolved = None
        if self.resolved is not None:
            resolved = {
                "expression": self.resolved.expression,
                "symbol": self.resolved.symbol,
                "address": f"0x{self.resolved.address:08X}",
                "size": self.resolved.size,
                "type_name": self.resolved.type_name,
                "source": self.resolved.source,
                "ram_checked": self.resolved.ram_checked,
            }
        return {
            "attempted": self.attempted,
            "written": self.written,
            "expression": self.expression,
            "value_text": self.value_text,
            "method": self.method,
            "resolved": resolved,
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
class KeilLiveVariableReadResult:
    attempted: bool
    read: bool
    expression: str
    method: str
    resolved: KeilResolvedVariable | None = None
    raw: bytes = b""
    value: str = ""
    diagnostics: tuple[tuple[str, str], ...] = ()
    error: str = ""

    def summary(self) -> str:
        if self.read:
            target = self.expression
            if self.resolved is not None:
                target += f" @ 0x{self.resolved.address:08X}"
            return f"Keil 读变量成功：{target} = {self.value} ({self.method})"
        return f"Keil 读变量失败：{self.expression} ({self.error or self.method or '--'})"

    def to_record(self) -> dict[str, object]:
        resolved = None
        if self.resolved is not None:
            resolved = {
                "expression": self.resolved.expression,
                "symbol": self.resolved.symbol,
                "address": f"0x{self.resolved.address:08X}",
                "size": self.resolved.size,
                "type_name": self.resolved.type_name,
                "source": self.resolved.source,
                "ram_checked": self.resolved.ram_checked,
            }
        return {
            "attempted": self.attempted,
            "read": self.read,
            "expression": self.expression,
            "method": self.method,
            "resolved": resolved,
            "raw": self.raw.hex(),
            "value": self.value,
            "diagnostics": [{"key": key, "value": value} for key, value in self.diagnostics],
            "error": self.error,
        }


@dataclass(frozen=True)
class KeilLiveVariableSmokeResult:
    read: KeilLiveVariableReadResult | None
    write: KeilLiveVariableWriteResult

    @property
    def succeeded(self) -> bool:
        return _strict_memory_write_passed(self.write)

    def summary(self) -> str:
        if self.succeeded:
            before = f"；写前 {self.read.value}" if self.read is not None and self.read.value else ""
            return self.write.summary() + before
        return self.write.summary()


@dataclass(frozen=True)
class _ValueFormat:
    size: int
    signed: bool
    floating: bool
    type_name: str


@dataclass(frozen=True)
class _ResolvedParts:
    base: Variable | Symbol
    address: int
    size: int
    type_info: TypeInfo | None
    type_name: str
    source: str
    ram_checked: bool
    diagnostics: tuple[tuple[str, str], ...] = ()


@dataclass
class _SymbolCache:
    symbols: list[Symbol] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    debug_loaded: bool = False


_CACHE: dict[Path, _SymbolCache] = {}


def run_keil_live_variable_smoke(
    session: KeilMemorySession,
    request: KeilLiveVariableWriteRequest,
    *,
    read_before_write: bool = True,
) -> KeilLiveVariableSmokeResult:
    read_result = None
    if read_before_write:
        read_result = read_keil_live_variable(
            session,
            KeilLiveVariableReadRequest(
                expression=request.expression,
                axf_path=request.axf_path,
                address=request.address,
                type_name=request.type_name,
                connection_name=request.connection_name,
            ),
        )
        if not read_result.read:
            write_result = KeilLiveVariableWriteResult(
                attempted=False,
                written=False,
                expression=read_result.expression,
                value_text=request.value_text,
                method="memory",
                resolved=read_result.resolved,
                diagnostics=read_result.diagnostics,
                error=f"写前读取失败：{read_result.error}",
            )
            return KeilLiveVariableSmokeResult(read=read_result, write=write_result)
    strict_request = KeilLiveVariableWriteRequest(
        expression=request.expression,
        value_text=request.value_text,
        axf_path=request.axf_path,
        address=request.address,
        type_name=request.type_name,
        prefer_memory=True,
        allow_command_fallback=False,
        connection_name=request.connection_name,
    )
    write_result = write_keil_live_variable(session, strict_request)
    if not _strict_memory_write_passed(write_result) and not write_result.error:
        write_result = KeilLiveVariableWriteResult(
            attempted=write_result.attempted,
            written=False,
            expression=write_result.expression,
            value_text=write_result.value_text,
            method=write_result.method,
            resolved=write_result.resolved,
            old_raw=write_result.old_raw,
            new_raw=write_result.new_raw,
            readback_raw=write_result.readback_raw,
            old_value=write_result.old_value,
            readback_value=write_result.readback_value,
            command=write_result.command,
            attempts=write_result.attempts,
            diagnostics=write_result.diagnostics,
            error="严格 smoke 要求内存写入、RAM 符号和独立回读一致",
        )
    return KeilLiveVariableSmokeResult(read=read_result, write=write_result)


def read_keil_live_variable(
    session: KeilMemorySession,
    request: KeilLiveVariableReadRequest,
) -> KeilLiveVariableReadResult:
    expression = _clean_expression(request.expression)
    write_like_request = KeilLiveVariableWriteRequest(
        expression=expression,
        value_text="0",
        axf_path=request.axf_path,
        address=request.address,
        type_name=request.type_name,
        prefer_memory=True,
        allow_command_fallback=False,
        connection_name=request.connection_name,
    )
    try:
        resolved, value_format, diagnostics = resolve_keil_live_variable(write_like_request)
        raw = session.read_memory(resolved.address, resolved.size)
        return KeilLiveVariableReadResult(
            attempted=True,
            read=True,
            expression=expression,
            method="memory",
            resolved=resolved,
            raw=raw,
            value=format_keil_scalar_value(raw, value_format),
            diagnostics=diagnostics,
        )
    except Exception as exc:
        return KeilLiveVariableReadResult(
            attempted=True,
            read=False,
            expression=expression,
            method="memory",
            error=str(exc),
        )


def write_keil_live_variable(
    session: KeilMemorySession,
    request: KeilLiveVariableWriteRequest,
) -> KeilLiveVariableWriteResult:
    expression = _clean_expression(request.expression)
    value_text = request.value_text.strip()
    if not value_text:
        return KeilLiveVariableWriteResult(
            attempted=False,
            written=False,
            expression=expression,
            value_text=value_text,
            method="validate",
            error="写入值不能为空",
        )

    attempts: list[str] = []
    diagnostics: list[tuple[str, str]] = []
    resolved: KeilResolvedVariable | None = None
    memory_error = ""

    if request.prefer_memory:
        attempts.append("memory")
        try:
            resolved, value_format, extra_diagnostics = resolve_keil_live_variable(request)
            diagnostics.extend(extra_diagnostics)
            new_raw = encode_keil_scalar_value(value_text, value_format)
            old_raw = session.read_memory(resolved.address, resolved.size)
            session.write_memory(resolved.address, new_raw)
            readback_raw = session.read_memory(resolved.address, resolved.size)
            if readback_raw != new_raw:
                raise ValueError(f"写后回读不一致：wrote={new_raw.hex()} read={readback_raw.hex()}")
            return KeilLiveVariableWriteResult(
                attempted=True,
                written=True,
                expression=expression,
                value_text=value_text,
                method="memory",
                resolved=resolved,
                old_raw=old_raw,
                new_raw=new_raw,
                readback_raw=readback_raw,
                old_value=format_keil_scalar_value(old_raw, value_format),
                readback_value=format_keil_scalar_value(readback_raw, value_format),
                attempts=tuple(attempts),
                diagnostics=tuple(diagnostics),
            )
        except Exception as exc:
            memory_error = str(exc)
            diagnostics.append(("内存写入", memory_error))
            if not request.allow_command_fallback:
                return KeilLiveVariableWriteResult(
                    attempted=True,
                    written=False,
                    expression=expression,
                    value_text=value_text,
                    method="memory",
                    resolved=resolved,
                    attempts=tuple(attempts),
                    diagnostics=tuple(diagnostics),
                    error=memory_error,
                )

    attempts.append("command")
    command = f"{expression} = {value_text}"
    try:
        session.execute_command(command, echo=True)
    except Exception as exc:
        message = str(exc)
        if memory_error:
            message = f"{message}；内存路径：{memory_error}"
        return KeilLiveVariableWriteResult(
            attempted=True,
            written=False,
            expression=expression,
            value_text=value_text,
            method="command",
            resolved=resolved,
            command=command,
            attempts=tuple(attempts),
            diagnostics=tuple(diagnostics),
            error=message,
        )

    readback_raw = b""
    readback_value = ""
    old_raw = b""
    new_raw = b""
    if resolved is not None:
        try:
            value_format = value_format_for_type(resolved.type_info, resolved.size, resolved.type_name)
            readback_raw = session.read_memory(resolved.address, resolved.size)
            readback_value = format_keil_scalar_value(readback_raw, value_format)
            new_raw = encode_keil_scalar_value(value_text, value_format)
            if readback_raw != new_raw:
                return KeilLiveVariableWriteResult(
                    attempted=True,
                    written=False,
                    expression=expression,
                    value_text=value_text,
                    method="command",
                    resolved=resolved,
                    new_raw=new_raw,
                    readback_raw=readback_raw,
                    readback_value=readback_value,
                    command=command,
                    attempts=tuple(attempts),
                    diagnostics=tuple(diagnostics),
                    error=f"命令赋值后回读不一致：expected={new_raw.hex()} read={readback_raw.hex()}",
                )
        except Exception as exc:
            diagnostics.append(("命令回读", str(exc)))
    else:
        diagnostics.append(("命令回读", "未提供 AXF/地址，Keil 接受命令但无法独立回读"))
    return KeilLiveVariableWriteResult(
        attempted=True,
        written=True,
        expression=expression,
        value_text=value_text,
        method="command",
        resolved=resolved,
        old_raw=old_raw,
        new_raw=new_raw,
        readback_raw=readback_raw,
        readback_value=readback_value,
        command=command,
        attempts=tuple(attempts),
        diagnostics=tuple(diagnostics),
    )


def write_keil_live_variable_existing(
    request: KeilLiveVariableWriteRequest,
    *,
    keil_root: str | Path | None,
    port: int,
    require_debug: bool = True,
) -> KeilLiveVariableWriteResult:
    with KeilUvscLiveSession.connect_existing(
        root=keil_root,
        port=int(port),
        connection_name=request.connection_name,
        require_debug=require_debug,
        extended_stack=True,
    ) as session:
        return write_keil_live_variable(session, request)


def read_keil_live_variable_existing(
    request: KeilLiveVariableReadRequest,
    *,
    keil_root: str | Path | None,
    port: int,
    require_debug: bool = True,
) -> KeilLiveVariableReadResult:
    with KeilUvscLiveSession.connect_existing(
        root=keil_root,
        port=int(port),
        connection_name=request.connection_name,
        require_debug=require_debug,
        extended_stack=True,
    ) as session:
        return read_keil_live_variable(session, request)


def run_keil_live_variable_smoke_existing(
    request: KeilLiveVariableWriteRequest,
    *,
    keil_root: str | Path | None,
    port: int,
    require_debug: bool = True,
    read_before_write: bool = True,
) -> KeilLiveVariableSmokeResult:
    with KeilUvscLiveSession.connect_existing(
        root=keil_root,
        port=int(port),
        connection_name=request.connection_name,
        require_debug=require_debug,
        extended_stack=True,
    ) as session:
        return run_keil_live_variable_smoke(
            session,
            request,
            read_before_write=read_before_write,
        )


def resolve_keil_live_variable(
    request: KeilLiveVariableWriteRequest,
) -> tuple[KeilResolvedVariable, _ValueFormat, tuple[tuple[str, str], ...]]:
    expression = _clean_expression(request.expression)
    diagnostics: list[tuple[str, str]] = []
    if request.address is not None:
        size = _size_from_type_name(request.type_name) or 4
        value_format = value_format_for_type(None, size, request.type_name)
        resolved = KeilResolvedVariable(
            expression=expression,
            symbol=expression,
            address=int(request.address),
            size=size,
            type_name=value_format.type_name,
            source="address",
            ram_checked=_is_ram_address(int(request.address)),
        )
        if not resolved.ram_checked:
            raise ValueError(f"地址不在常见 Cortex-M RAM 范围：0x{resolved.address:08X}")
        return resolved, value_format, tuple(diagnostics)

    axf = Path(request.axf_path).expanduser().resolve() if request.axf_path else None
    if axf is None or not axf.exists():
        raise ValueError("内存写入需要可用 AXF/ELF 来解析符号")

    parts = _resolve_expression_from_axf(axf, expression)
    diagnostics.extend(parts.diagnostics)
    size = parts.size or _size_from_type_name(request.type_name) or _type_size(parts.type_info)
    if size <= 0:
        raise ValueError(f"无法确认变量长度：{expression}")
    value_format = value_format_for_type(parts.type_info, size, request.type_name or parts.type_name)
    resolved = KeilResolvedVariable(
        expression=expression,
        symbol=getattr(parts.base, "name", expression),
        address=parts.address,
        size=value_format.size,
        type_info=parts.type_info,
        type_name=value_format.type_name,
        source=parts.source,
        ram_checked=parts.ram_checked,
    )
    if not resolved.ram_checked:
        raise ValueError(f"符号不在常见 Cortex-M RAM 范围：{expression} @ 0x{resolved.address:08X}")
    return resolved, value_format, tuple(diagnostics)


def encode_keil_scalar_value(value_text: str, value_format: _ValueFormat) -> bytes:
    text = value_text.strip()
    if not text:
        raise ValueError("写入值不能为空")
    size = value_format.size
    if value_format.floating:
        try:
            value = float(text)
        except ValueError as exc:
            raise ValueError(f"无效浮点数：{value_text!r}") from exc
        if not math.isfinite(value):
            raise ValueError("不允许写入 NaN 或 Inf")
        if size == 4:
            return struct.pack("<f", value)
        if size == 8:
            return struct.pack("<d", value)
        raise ValueError("仅支持 32/64 位浮点写入")

    try:
        value = int(text, 0)
    except ValueError as exc:
        raise ValueError(f"无效整数：{value_text!r}") from exc
    bits = size * 8
    if value_format.signed:
        min_value = -(1 << (bits - 1))
        max_value = (1 << (bits - 1)) - 1
    else:
        min_value = 0
        max_value = (1 << bits) - 1
    if value < min_value or value > max_value:
        raise ValueError(f"数值超出 {size} 字节范围：{min_value}..{max_value}")
    return int(value).to_bytes(size, "little", signed=value_format.signed)


def format_keil_scalar_value(raw: bytes, value_format: _ValueFormat) -> str:
    data = bytes(raw[: value_format.size])
    if len(data) != value_format.size:
        return ""
    if value_format.floating:
        if value_format.size == 4:
            return f"{struct.unpack('<f', data)[0]:.7g}"
        if value_format.size == 8:
            return f"{struct.unpack('<d', data)[0]:.12g}"
        return ""
    return str(int.from_bytes(data, "little", signed=value_format.signed))


def value_format_for_type(type_info: TypeInfo | None, size: int, fallback_type_name: str = "") -> _ValueFormat:
    type_info = _resolve_typedef(type_info)
    if isinstance(type_info, BaseType):
        width = int(type_info.byte_size or size or 0)
        if width not in {1, 2, 4, 8}:
            raise ValueError(f"暂不支持 {width} 字节基础类型")
        return _ValueFormat(
            size=width,
            signed=_is_signed_base_type(type_info),
            floating=_is_float_base_type(type_info),
            type_name=type_info.name or fallback_type_name or f"{width}-byte",
        )
    if isinstance(type_info, EnumType):
        width = int(type_info.size or size or 4)
        if width not in {1, 2, 4}:
            raise ValueError("暂不支持该枚举类型")
        signed = any(value < 0 for _name, value in type_info.values)
        return _ValueFormat(size=width, signed=signed, floating=False, type_name=f"enum {type_info.name}".strip())
    if isinstance(type_info, PointerType):
        width = int(type_info.size or size or 4)
        if width not in {4, 8}:
            raise ValueError("暂不支持该指针类型")
        return _ValueFormat(size=width, signed=False, floating=False, type_name=fallback_type_name or "pointer")
    if isinstance(type_info, (StructType, ArrayType, FuncType)):
        raise ValueError("仅支持写入基础数值、枚举或指针变量")

    guessed = _format_from_type_name(fallback_type_name, size)
    if guessed is not None:
        return guessed
    width = int(size or 4)
    if width not in {1, 2, 4, 8}:
        raise ValueError(f"暂不支持 {width} 字节变量")
    return _ValueFormat(size=width, signed=True, floating=False, type_name=fallback_type_name or f"int{width * 8}")


def _resolve_expression_from_axf(axf: Path, expression: str) -> _ResolvedParts:
    cache = _symbol_cache(axf)
    parts = [part for part in expression.split(".") if part]
    if not parts:
        raise ValueError("变量表达式为空")
    root_name = parts[0]

    variable = _find_variable(cache.variables, root_name)
    symbol = _find_symbol(cache.symbols, root_name)
    if variable is None and symbol is None:
        raise ValueError(f"AXF 中找不到变量符号：{root_name}")

    base = variable if variable is not None else symbol
    address = int(getattr(base, "address", 0) or 0)
    type_info = getattr(base, "type_info", None)
    source = "dwarf" if variable is not None else "symtab"
    diagnostics: list[tuple[str, str]] = [("AXF", str(axf))]

    for member_name in parts[1:]:
        concrete = _resolve_typedef(type_info)
        if not isinstance(concrete, StructType):
            raise ValueError(f"{'.'.join(parts[:-1])} 不是结构体，无法解析成员 {member_name}")
        member = _find_struct_member(concrete, member_name)
        if member is None:
            raise ValueError(f"结构体 {concrete.name or '<anonymous>'} 中找不到成员：{member_name}")
        address += int(member.offset)
        type_info = member.type_info
        source = "dwarf_member"
        diagnostics.append(("成员偏移", f"{member_name}+{member.offset}"))

    size = _type_size(type_info)
    if size <= 0:
        size = int(getattr(base, "size", 0) or 0)
    type_name = _type_name(type_info)
    return _ResolvedParts(
        base=base,
        address=address,
        size=size,
        type_info=type_info,
        type_name=type_name,
        source=source,
        ram_checked=_is_ram_address(address),
        diagnostics=tuple(diagnostics),
    )


def _symbol_cache(axf: Path) -> _SymbolCache:
    key = axf.expanduser().resolve()
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    symbols = parse_symbol_table(key)
    variables: list[Variable] = []
    debug_loaded = False
    try:
        db = parse_debug_info(key)
        variables = list(db.variables)
        debug_loaded = db.has_debug_info()
    except Exception:
        variables = []
        debug_loaded = False
    cache = _SymbolCache(symbols=symbols, variables=variables, debug_loaded=debug_loaded)
    _CACHE[key] = cache
    return cache


def _clean_expression(expression: str) -> str:
    text = str(expression or "").strip()
    if not text:
        raise ValueError("变量名不能为空")
    if "\x00" in text or "\n" in text or "\r" in text:
        raise ValueError("变量名包含非法控制字符")
    if "=" in text or ";" in text:
        raise ValueError("变量名不能包含 = 或 ;")
    return text


def _strict_memory_write_passed(write: KeilLiveVariableWriteResult | None) -> bool:
    if write is None or not write.written:
        return False
    if write.method != "memory":
        return False
    if write.resolved is None or not write.resolved.ram_checked:
        return False
    if not write.old_raw or not write.new_raw or not write.readback_raw:
        return False
    return bool(write.readback_raw == write.new_raw)


def _find_variable(variables: list[Variable], name: str) -> Variable | None:
    for variable in variables:
        if variable.name == name and int(variable.address or 0) > 0:
            return variable
    return None


def _find_symbol(symbols: list[Symbol], name: str) -> Symbol | None:
    best: Symbol | None = None
    for symbol in symbols:
        if symbol.name != name:
            continue
        if int(symbol.address or 0) <= 0:
            continue
        if best is None:
            best = symbol
        if symbol.sym_type == "OBJECT" and symbol.binding in {"GLOBAL", "WEAK"}:
            return symbol
    return best


def _find_struct_member(struct_type: StructType, name: str):
    for member in struct_type.members:
        if member.name == name:
            return member
    return None


def _resolve_typedef(type_info: TypeInfo | None) -> TypeInfo | None:
    while isinstance(type_info, TypedefType):
        type_info = type_info.underlying_type
    return type_info


def _type_size(type_info: TypeInfo | None) -> int:
    type_info = _resolve_typedef(type_info)
    if isinstance(type_info, BaseType):
        return int(type_info.byte_size or 0)
    if isinstance(type_info, StructType):
        return int(type_info.size or 0)
    if isinstance(type_info, ArrayType):
        return int(type_info.total_size or 0)
    if isinstance(type_info, PointerType):
        return int(type_info.size or 0)
    if isinstance(type_info, EnumType):
        return int(type_info.size or 0)
    return 0


def _type_name(type_info: TypeInfo | None) -> str:
    original = type_info
    type_info = _resolve_typedef(type_info)
    if isinstance(original, TypedefType) and original.name:
        return original.name
    if isinstance(type_info, BaseType):
        return type_info.name
    if isinstance(type_info, StructType):
        return ("union " if type_info.is_union else "struct ") + (type_info.name or "<anonymous>")
    if isinstance(type_info, ArrayType):
        return f"{_type_name(type_info.element_type)}[{type_info.count}]"
    if isinstance(type_info, PointerType):
        return "pointer"
    if isinstance(type_info, EnumType):
        return f"enum {type_info.name}".strip()
    return ""


def _is_ram_address(address: int) -> bool:
    address = int(address)
    return 0x20000000 <= address <= 0x3FFFFFFF


def _normalise_encoding(encoding: str) -> str:
    value = (encoding or "").strip().lower()
    if value in {"4", "dw_ate_float"}:
        return "float"
    if value in {"5", "dw_ate_signed"}:
        return "signed"
    if value in {"6", "dw_ate_signed_char"}:
        return "signed char"
    if value in {"7", "dw_ate_unsigned"}:
        return "unsigned"
    if value in {"8", "dw_ate_unsigned_char"}:
        return "unsigned char"
    return value


def _is_float_base_type(base_type: BaseType) -> bool:
    encoding = _normalise_encoding(base_type.encoding)
    name = base_type.name.lower()
    return encoding == "float" or "float" in name or "double" in name


def _is_signed_base_type(base_type: BaseType) -> bool:
    encoding = _normalise_encoding(base_type.encoding)
    name = base_type.name.lower()
    if encoding.startswith("signed"):
        return True
    if encoding.startswith("unsigned") or "unsigned" in name or name.startswith("uint"):
        return False
    if name in {"char", "signed char"}:
        return True
    return not name.startswith("u")


def _format_from_type_name(type_name: str, size: int) -> _ValueFormat | None:
    text = str(type_name or "").strip().lower().replace(" ", "")
    if not text:
        return None
    aliases: dict[str, tuple[int, bool, bool]] = {
        "int8": (1, True, False),
        "int8_t": (1, True, False),
        "uint8": (1, False, False),
        "uint8_t": (1, False, False),
        "char": (1, True, False),
        "uchar": (1, False, False),
        "int16": (2, True, False),
        "int16_t": (2, True, False),
        "uint16": (2, False, False),
        "uint16_t": (2, False, False),
        "short": (2, True, False),
        "ushort": (2, False, False),
        "int32": (4, True, False),
        "int32_t": (4, True, False),
        "uint32": (4, False, False),
        "uint32_t": (4, False, False),
        "int": (4, True, False),
        "uint": (4, False, False),
        "float": (4, True, True),
        "float32": (4, True, True),
        "double": (8, True, True),
        "float64": (8, True, True),
        "int64": (8, True, False),
        "int64_t": (8, True, False),
        "uint64": (8, False, False),
        "uint64_t": (8, False, False),
    }
    item = aliases.get(text)
    if item is None:
        return None
    width, signed, floating = item
    if size and size != width:
        width = int(size)
    return _ValueFormat(size=width, signed=signed, floating=floating, type_name=type_name)


def _size_from_type_name(type_name: str) -> int:
    value = _format_from_type_name(type_name, 0)
    return value.size if value is not None else 0
