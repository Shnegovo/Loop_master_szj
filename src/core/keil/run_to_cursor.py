"""Keil run-to-cursor transaction through a temporary breakpoint."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from src.core.debug_snapshots import DebugPcLocation, RemoteBreakpoint, RemoteBreakpointSnapshot
from src.core.keil.breakpoint_list import parse_keil_breakpoint_list
from src.core.keil.pc_location import capture_keil_command_log, read_keil_pc_location
from src.core.keil.source_line_address import KeilSourceLineAddressResult, resolve_source_line_address


class KeilRunToCursorSession(Protocol):
    def execute_command(self, command: str, *, echo: bool = False) -> Any:
        ...

    def target_running(self) -> bool | None:
        ...

    def run_target(self) -> object:
        ...

    def halt_target(self) -> object:
        ...

    def reset_target(self) -> object:
        ...


@dataclass(frozen=True)
class KeilRunToCursorRequest:
    project_path: Path | None
    target_name: str
    source_path: Path
    line: int
    axf_path: Path | None = None
    source_roots: tuple[Path, ...] = ()
    timeout_s: float = 5.0
    reset_before_run: bool = False
    allow_existing_breakpoint: bool = True


@dataclass(frozen=True)
class KeilRunToCursorResult:
    request: KeilRunToCursorRequest
    attempted: bool
    succeeded: bool
    address: int | None = None
    resolved_line: int = 0
    address_exact: bool = False
    before_snapshot: RemoteBreakpointSnapshot | None = None
    after_set_snapshot: RemoteBreakpointSnapshot | None = None
    after_cleanup_snapshot: RemoteBreakpointSnapshot | None = None
    hit_pc: DebugPcLocation | None = None
    temp_remote_id: str = ""
    used_existing_breakpoint: bool = False
    set_command: str = ""
    cleanup_command: str = ""
    run_summary: str = ""
    reset_summary: str = ""
    halt_after_timeout_summary: str = ""
    status_samples: tuple[bool | None, ...] = ()
    cleanup_succeeded: bool = False
    error: str = ""

    def summary(self) -> str:
        if self.succeeded:
            target = f"{self.request.source_path.name}:{self.request.line}"
            return f"Keil 运行到光标完成：{target}"
        return f"Keil 运行到光标失败：{self.error or '未知错误'}"

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        rows = [
            ("运行到光标", "成功" if self.succeeded else "失败"),
            ("目标源码", f"{self.request.source_path}:{self.request.line}"),
            ("目标地址", f"0x{self.address:08X}" if self.address is not None else "--"),
            ("临时断点", self.temp_remote_id or ("复用已有断点" if self.used_existing_breakpoint else "--")),
            ("清理结果", "成功" if self.cleanup_succeeded else ("无需清理" if self.used_existing_breakpoint else "未完成")),
        ]
        if self.hit_pc is not None:
            rows.append(("命中 PC", _pc_text(self.hit_pc)))
        if self.error:
            rows.append(("运行到光标错误", self.error))
        return tuple(rows)

    def to_record(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "project_path": str(self.request.project_path or ""),
            "target_name": self.request.target_name,
            "source_path": str(self.request.source_path),
            "line": self.request.line,
            "axf_path": str(self.request.axf_path or ""),
            "address": f"0x{self.address:08X}" if self.address is not None else "",
            "resolved_line": self.resolved_line,
            "address_exact": self.address_exact,
            "temp_remote_id": self.temp_remote_id,
            "used_existing_breakpoint": self.used_existing_breakpoint,
            "set_command": self.set_command,
            "cleanup_command": self.cleanup_command,
            "run_summary": self.run_summary,
            "reset_summary": self.reset_summary,
            "halt_after_timeout_summary": self.halt_after_timeout_summary,
            "status_samples": list(self.status_samples),
            "cleanup_succeeded": self.cleanup_succeeded,
            "hit_pc": self.hit_pc.to_record() if self.hit_pc is not None else None,
            "before_snapshot": self.before_snapshot.to_record() if self.before_snapshot is not None else None,
            "after_set_snapshot": self.after_set_snapshot.to_record() if self.after_set_snapshot is not None else None,
            "after_cleanup_snapshot": self.after_cleanup_snapshot.to_record() if self.after_cleanup_snapshot is not None else None,
            "error": self.error,
        }


def run_keil_to_cursor_transaction(
    session: KeilRunToCursorSession,
    request: KeilRunToCursorRequest,
) -> KeilRunToCursorResult:
    address_result = _resolve_target_address(request)
    if not address_result.resolved:
        return _failed(request, address_result=address_result, error=address_result.error or "源码行无法解析到地址")
    address = int(address_result.address)
    reset_summary = ""
    run_summary = ""
    halt_summary = ""
    status_samples: list[bool | None] = []
    before_snapshot: RemoteBreakpointSnapshot | None = None
    after_set_snapshot: RemoteBreakpointSnapshot | None = None
    after_cleanup_snapshot: RemoteBreakpointSnapshot | None = None
    hit_pc: DebugPcLocation | None = None
    temp_remote_id = ""
    used_existing = False
    cleanup_command = ""
    cleanup_succeeded = False

    try:
        if request.reset_before_run:
            reset_result = session.reset_target()
            reset_summary = _summary(reset_result)
            if not bool(getattr(reset_result, "succeeded", False)):
                return _failed(request, address_result=address_result, error=reset_summary or "复位失败")
        elif session.target_running() is True:
            return _failed(request, address_result=address_result, error="目标正在运行，运行到光标要求先暂停")

        before_snapshot = _breakpoint_snapshot(session, request, "before")
        before_ids = {item.remote_id for item in before_snapshot.breakpoints}
        existing = _enabled_breakpoints_at_address(before_snapshot.breakpoints, address)
        set_command = ""
        if existing and request.allow_existing_breakpoint:
            used_existing = True
            after_set_snapshot = before_snapshot
        else:
            set_command = f"BS 0x{address:08X}"
            session.execute_command(set_command, echo=True)
            after_set_snapshot = _breakpoint_snapshot(session, request, "after_set")
            created = _created_breakpoints_at_address(after_set_snapshot.breakpoints, before_ids, address)
            if not created:
                return _failed(
                    request,
                    address_result=address_result,
                    before_snapshot=before_snapshot,
                    after_set_snapshot=after_set_snapshot,
                    set_command=set_command,
                    reset_summary=reset_summary,
                    error="临时断点设置后未能从 Keil BL 回读确认",
                )
            temp_remote_id = created[-1].remote_id

        run_result = session.run_target()
        run_summary = _summary(run_result)
        if not bool(getattr(run_result, "succeeded", False)):
            return _finish_with_cleanup(
                session,
                request,
                address_result,
                before_snapshot=before_snapshot,
                after_set_snapshot=after_set_snapshot,
                temp_remote_id=temp_remote_id,
                used_existing=used_existing,
                set_command=set_command,
                run_summary=run_summary,
                reset_summary=reset_summary,
                error=run_summary or "运行命令失败",
            )

        deadline = time.perf_counter() + max(0.2, float(request.timeout_s))
        hit = False
        while time.perf_counter() < deadline:
            running = session.target_running()
            status_samples.append(running)
            if running is False:
                hit = True
                break
            time.sleep(0.05)
        if not hit:
            halt_result = session.halt_target()
            halt_summary = _summary(halt_result)
            return _finish_with_cleanup(
                session,
                request,
                address_result,
                before_snapshot=before_snapshot,
                after_set_snapshot=after_set_snapshot,
                temp_remote_id=temp_remote_id,
                used_existing=used_existing,
                set_command=set_command,
                run_summary=run_summary,
                reset_summary=reset_summary,
                halt_after_timeout_summary=halt_summary,
                status_samples=tuple(status_samples),
                error="等待临时断点命中超时",
            )

        pc_result = read_keil_pc_location(
            session,
            axf_path=request.axf_path,
            source_roots=request.source_roots,
        )
        hit_pc = pc_result.pc_location
        pc_error = _pc_hit_error(hit_pc, request)
        if pc_error:
            return _finish_with_cleanup(
                session,
                request,
                address_result,
                before_snapshot=before_snapshot,
                after_set_snapshot=after_set_snapshot,
                hit_pc=hit_pc,
                temp_remote_id=temp_remote_id,
                used_existing=used_existing,
                set_command=set_command,
                run_summary=run_summary,
                reset_summary=reset_summary,
                status_samples=tuple(status_samples),
                error=pc_error,
            )

        cleanup = _cleanup_temp_breakpoint(
            session,
            request,
            address,
            temp_remote_id,
            before_ids,
            used_existing,
        )
        cleanup_command = cleanup["command"]
        cleanup_succeeded = bool(cleanup["succeeded"])
        after_cleanup_snapshot = cleanup["snapshot"]
        if not cleanup_succeeded:
            return KeilRunToCursorResult(
                request=request,
                attempted=True,
                succeeded=False,
                address=address,
                resolved_line=address_result.line,
                address_exact=address_result.exact,
                before_snapshot=before_snapshot,
                after_set_snapshot=after_set_snapshot,
                after_cleanup_snapshot=after_cleanup_snapshot,
                hit_pc=hit_pc,
                temp_remote_id=temp_remote_id,
                used_existing_breakpoint=used_existing,
                set_command=set_command,
                cleanup_command=cleanup_command,
                run_summary=run_summary,
                reset_summary=reset_summary,
                status_samples=tuple(status_samples),
                cleanup_succeeded=False,
                error=str(cleanup["error"] or "临时断点清理失败"),
            )

        return KeilRunToCursorResult(
            request=request,
            attempted=True,
            succeeded=True,
            address=address,
            resolved_line=address_result.line,
            address_exact=address_result.exact,
            before_snapshot=before_snapshot,
            after_set_snapshot=after_set_snapshot,
            after_cleanup_snapshot=after_cleanup_snapshot,
            hit_pc=hit_pc,
            temp_remote_id=temp_remote_id,
            used_existing_breakpoint=used_existing,
            set_command=set_command,
            cleanup_command=cleanup_command,
            run_summary=run_summary,
            reset_summary=reset_summary,
            status_samples=tuple(status_samples),
            cleanup_succeeded=cleanup_succeeded,
        )
    except Exception as exc:
        return _finish_with_cleanup(
            session,
            request,
            address_result,
            before_snapshot=before_snapshot,
            after_set_snapshot=after_set_snapshot,
            hit_pc=hit_pc,
            temp_remote_id=temp_remote_id,
            used_existing=used_existing,
            set_command=f"BS 0x{address:08X}" if temp_remote_id else "",
            run_summary=run_summary,
            reset_summary=reset_summary,
            halt_after_timeout_summary=halt_summary,
            status_samples=tuple(status_samples),
            error=str(exc),
        )


def _finish_with_cleanup(
    session: KeilRunToCursorSession,
    request: KeilRunToCursorRequest,
    address_result: KeilSourceLineAddressResult,
    *,
    before_snapshot: RemoteBreakpointSnapshot | None,
    after_set_snapshot: RemoteBreakpointSnapshot | None,
    hit_pc: DebugPcLocation | None = None,
    temp_remote_id: str,
    used_existing: bool,
    set_command: str,
    run_summary: str,
    reset_summary: str,
    halt_after_timeout_summary: str = "",
    status_samples: tuple[bool | None, ...] = (),
    error: str,
) -> KeilRunToCursorResult:
    before_ids = {item.remote_id for item in before_snapshot.breakpoints} if before_snapshot is not None else set()
    cleanup = _cleanup_temp_breakpoint(
        session,
        request,
        int(address_result.address) if address_result.address is not None else 0,
        temp_remote_id,
        before_ids,
        used_existing,
    )
    cleanup_error = str(cleanup["error"] or "")
    full_error = error
    if cleanup_error:
        full_error = f"{error}; cleanup: {cleanup_error}"
    return KeilRunToCursorResult(
        request=request,
        attempted=True,
        succeeded=False,
        address=address_result.address,
        resolved_line=address_result.line,
        address_exact=address_result.exact,
        before_snapshot=before_snapshot,
        after_set_snapshot=after_set_snapshot,
        after_cleanup_snapshot=cleanup["snapshot"],
        hit_pc=hit_pc,
        temp_remote_id=temp_remote_id,
        used_existing_breakpoint=used_existing,
        set_command=set_command,
        cleanup_command=str(cleanup["command"] or ""),
        run_summary=run_summary,
        reset_summary=reset_summary,
        halt_after_timeout_summary=halt_after_timeout_summary,
        status_samples=status_samples,
        cleanup_succeeded=bool(cleanup["succeeded"]),
        error=full_error,
    )


def _cleanup_temp_breakpoint(
    session: KeilRunToCursorSession,
    request: KeilRunToCursorRequest,
    address: int,
    temp_remote_id: str,
    before_ids: set[str],
    used_existing: bool,
) -> dict[str, object]:
    if used_existing or not temp_remote_id:
        return {
            "command": "",
            "succeeded": True,
            "snapshot": _breakpoint_snapshot(session, request, "after_cleanup"),
            "error": "",
        }
    command = f"BK {temp_remote_id}"
    try:
        session.execute_command(command, echo=True)
        snapshot = _breakpoint_snapshot(session, request, "after_cleanup")
        remaining = [
            item for item in snapshot.breakpoints
            if item.remote_id == temp_remote_id or (item.address == address and item.remote_id not in before_ids)
        ]
        return {
            "command": command,
            "succeeded": not remaining,
            "snapshot": snapshot,
            "error": "临时断点仍存在" if remaining else "",
        }
    except Exception as exc:
        return {
            "command": command,
            "succeeded": False,
            "snapshot": None,
            "error": str(exc),
        }


def _resolve_target_address(request: KeilRunToCursorRequest) -> KeilSourceLineAddressResult:
    if request.axf_path is None:
        return KeilSourceLineAddressResult(
            requested_path=request.source_path,
            requested_line=request.line,
            error="缺少 AXF，无法把源码行解析到地址",
        )
    return resolve_source_line_address(
        request.axf_path,
        request.source_path,
        request.line,
        source_roots=request.source_roots,
        allow_nearest=False,
    )


def _breakpoint_snapshot(
    session: KeilRunToCursorSession,
    request: KeilRunToCursorRequest,
    label: str,
) -> RemoteBreakpointSnapshot:
    text, error = capture_keil_command_log(session, "BL")
    result = parse_keil_breakpoint_list(
        text,
        project_path=request.project_path,
        target_name=request.target_name,
        command=f"LOG+BL:{label}",
    )
    if error and not result.snapshot.error:
        return RemoteBreakpointSnapshot(
            schema_version=result.snapshot.schema_version,
            snapshot_id=result.snapshot.snapshot_id,
            project_path=result.snapshot.project_path,
            target_name=result.snapshot.target_name,
            captured_at=result.snapshot.captured_at,
            complete=False,
            breakpoints=result.snapshot.breakpoints,
            error=error,
        )
    return result.snapshot


def _enabled_breakpoints_at_address(
    breakpoints: Iterable[RemoteBreakpoint],
    address: int,
) -> tuple[RemoteBreakpoint, ...]:
    return tuple(item for item in breakpoints if item.address == address and item.enabled is not False)


def _created_breakpoints_at_address(
    breakpoints: Iterable[RemoteBreakpoint],
    before_ids: set[str],
    address: int,
) -> tuple[RemoteBreakpoint, ...]:
    return tuple(item for item in breakpoints if item.address == address and item.remote_id not in before_ids)


def _pc_hit_error(pc: DebugPcLocation, request: KeilRunToCursorRequest) -> str:
    if pc.address is None:
        return pc.message or "命中后 PC 未回读"
    if pc.path is None or pc.line is None:
        return pc.message or "命中后 PC 未映射源码"
    if Path(pc.path).resolve() != request.source_path.resolve() or int(pc.line) != int(request.line):
        return f"PC 停在 {pc.path}:{pc.line}，不是目标 {request.source_path}:{request.line}"
    return ""


def _summary(result: object) -> str:
    summary = getattr(result, "summary", None)
    if callable(summary):
        return str(summary())
    return str(result or "")


def _pc_text(pc: DebugPcLocation) -> str:
    address = f"0x{pc.address:08X}" if pc.address is not None else "--"
    location = f"{pc.path}:{pc.line}" if pc.path is not None and pc.line is not None else "--"
    return f"{address} / {location}"


def _failed(
    request: KeilRunToCursorRequest,
    *,
    address_result: KeilSourceLineAddressResult | None = None,
    before_snapshot: RemoteBreakpointSnapshot | None = None,
    after_set_snapshot: RemoteBreakpointSnapshot | None = None,
    set_command: str = "",
    reset_summary: str = "",
    error: str,
) -> KeilRunToCursorResult:
    return KeilRunToCursorResult(
        request=request,
        attempted=True,
        succeeded=False,
        address=address_result.address if address_result is not None else None,
        resolved_line=address_result.line if address_result is not None else 0,
        address_exact=address_result.exact if address_result is not None else False,
        before_snapshot=before_snapshot,
        after_set_snapshot=after_set_snapshot,
        set_command=set_command,
        reset_summary=reset_summary,
        error=error,
    )


__all__ = [
    "KeilRunToCursorRequest",
    "KeilRunToCursorResult",
    "KeilRunToCursorSession",
    "run_keil_to_cursor_transaction",
]
