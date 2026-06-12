"""OpenOCD/GDB live read-only smoke executor.

The executor is intentionally narrow: it may start OpenOCD and GDB only when
`execute=True`, and it never writes memory or flashes the target. Runtime halt
is also opt-in because even a read-only debugger attach can disturb a running
control loop.
"""

from __future__ import annotations

import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.core.debug_backend import backend_snapshot_id
from src.core.debug_snapshots import RemoteBreakpoint, RemoteBreakpointSnapshot
from src.core.openocd_gdb.profile import OpenOcdGdbProfile, default_openocd_gdb_profile


DEFAULT_AXF = Path("D:/LoopMaster_v2.1/firmware/keil_f401_variable_probe/Objects/f401_variable_probe.axf")


@dataclass(frozen=True)
class OpenOcdGdbReadOnlyRequest:
    openocd_root: Path | None = None
    gdb_path: Path | None = None
    axf_path: Path | None = DEFAULT_AXF
    gdb_port: int = 3333
    telnet_port: int = 4444
    tcl_port: int = 6666
    execute: bool = False
    allow_halt: bool = False
    resume_after_halt: bool = False
    breakpoint_location: str = ""
    connect_timeout_s: float = 10.0
    gdb_timeout_s: float = 8.0


@dataclass(frozen=True)
class OpenOcdGdbReadOnlyResult:
    attempted: bool
    succeeded: bool
    stage: str
    detail: str
    profile: OpenOcdGdbProfile
    openocd_command: tuple[str, ...]
    gdb_command: tuple[str, ...]
    openocd_lines: tuple[str, ...] = ()
    gdb_lines: tuple[str, ...] = ()
    target_state: str = "unknown"
    pc_value: str = ""
    halted_by_probe: bool = False
    resumed_after_halt: bool = False
    breakpoint_location: str = ""
    breakpoint_id: str = ""
    breakpoint_inserted: bool = False
    breakpoint_deleted: bool = False
    breakpoint_leaked: bool = False
    breakpoint_detail: str = ""

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        rows = [
            ("OpenOCD/GDB 执行", "已执行" if self.attempted else "dry-run"),
            ("OpenOCD/GDB 结果", "通过" if self.succeeded else "未通过"),
            ("OpenOCD/GDB 阶段", self.stage or "--"),
            ("OpenOCD/GDB 说明", self.detail or "--"),
            ("OpenOCD/GDB 目标状态", self.target_state or "--"),
            ("OpenOCD/GDB PC", self.pc_value or "--"),
            ("OpenOCD/GDB 暂停", "是" if self.halted_by_probe else "否"),
            ("OpenOCD/GDB 恢复", "是" if self.resumed_after_halt else "否"),
        ]
        if self.breakpoint_location:
            rows.extend(
                (
                    ("OpenOCD/GDB 断点", self.breakpoint_location),
                    ("OpenOCD/GDB 断点编号", self.breakpoint_id or "--"),
                    ("OpenOCD/GDB 断点插入", "是" if self.breakpoint_inserted else "否"),
                    ("OpenOCD/GDB 断点删除", "是" if self.breakpoint_deleted else "否"),
                    ("OpenOCD/GDB 断点泄漏", "是" if self.breakpoint_leaked else "否"),
                    ("OpenOCD/GDB 断点说明", self.breakpoint_detail or "--"),
                )
            )
        return tuple(rows) + self.profile.diagnostic_rows()

    def to_record(self) -> dict[str, object]:
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "stage": self.stage,
            "detail": self.detail,
            "target_state": self.target_state,
            "pc_value": self.pc_value,
            "halted_by_probe": self.halted_by_probe,
            "resumed_after_halt": self.resumed_after_halt,
            "breakpoint_location": self.breakpoint_location,
            "breakpoint_id": self.breakpoint_id,
            "breakpoint_inserted": self.breakpoint_inserted,
            "breakpoint_deleted": self.breakpoint_deleted,
            "breakpoint_leaked": self.breakpoint_leaked,
            "breakpoint_detail": self.breakpoint_detail,
            "openocd_command": list(self.openocd_command),
            "gdb_command": list(self.gdb_command),
            "openocd_lines": list(self.openocd_lines),
            "gdb_lines": list(self.gdb_lines),
            "profile": self.profile.to_summary_record(),
            "diagnostics": [{"key": key, "value": value} for key, value in self.diagnostic_rows()],
        }


def run_openocd_gdb_readonly_probe(request: OpenOcdGdbReadOnlyRequest) -> OpenOcdGdbReadOnlyResult:
    profile = default_openocd_gdb_profile(
        request.openocd_root,
        gdb_path=request.gdb_path,
        gdb_port=request.gdb_port,
        telnet_port=request.telnet_port,
        tcl_port=request.tcl_port,
    )
    openocd_command = _openocd_command(profile)
    gdb_command = _gdb_command(profile, request.axf_path)
    if not request.execute:
        return OpenOcdGdbReadOnlyResult(
            attempted=False,
            succeeded=profile.ready_for_preview,
            stage="dry_run",
            detail="dry-run only; pass --execute to start OpenOCD and GDB/MI.",
            profile=profile,
            openocd_command=openocd_command,
            gdb_command=gdb_command,
        )
    if not profile.ready_for_preview:
        return OpenOcdGdbReadOnlyResult(
            attempted=True,
            succeeded=False,
            stage="profile",
            detail="OpenOCD/GDB profile is incomplete.",
            profile=profile,
            openocd_command=openocd_command,
            gdb_command=gdb_command,
        )
    return _execute_readonly_probe(request, profile, openocd_command, gdb_command)


class OpenOcdGdbLiveSession:
    """Persistent OpenOCD/GDB session used by the app backend."""

    def __init__(self, request: OpenOcdGdbReadOnlyRequest) -> None:
        self.request = request
        self.profile = default_openocd_gdb_profile(
            request.openocd_root,
            gdb_path=request.gdb_path,
            gdb_port=request.gdb_port,
            telnet_port=request.telnet_port,
            tcl_port=request.tcl_port,
        )
        self.openocd_command = _openocd_command(self.profile)
        self.gdb_command = _gdb_command(self.profile, request.axf_path)
        self.openocd: subprocess.Popen[str] | None = None
        self.gdb: subprocess.Popen[str] | None = None
        self.openocd_reader: _ProcessReader | None = None
        self.gdb_reader: _ProcessReader | None = None
        self.target_state = "unknown"
        self.pc_value = ""
        self.halted_by_probe = False
        self.resumed_after_halt = False
        self._token = 10

    @property
    def alive(self) -> bool:
        return bool(
            self.openocd is not None
            and self.openocd.poll() is None
            and self.gdb is not None
            and self.gdb.poll() is None
            and self.gdb_reader is not None
        )

    @property
    def gdb_lines(self) -> tuple[str, ...]:
        return tuple(self.gdb_reader.lines if self.gdb_reader is not None else ())

    @property
    def openocd_lines(self) -> tuple[str, ...]:
        return tuple(self.openocd_reader.lines if self.openocd_reader is not None else ())

    def start(self) -> None:
        if self.alive:
            return
        if not self.profile.ready_for_preview:
            raise RuntimeError("OpenOCD/GDB profile is incomplete.")
        self.openocd = subprocess.Popen(
            list(self.openocd_command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )
        self.openocd_reader = _ProcessReader(self.openocd, "openocd-live")
        if not self.openocd_reader.wait_for(
            lambda line: _openocd_ready(line, self.profile.gdb_port) or _openocd_failed(line),
            self.request.connect_timeout_s,
        ):
            raise RuntimeError("OpenOCD did not report a GDB listener before timeout.")
        if any(_openocd_failed(line) for line in self.openocd_reader.lines):
            raise RuntimeError(_last_matching(self.openocd_reader.lines, _openocd_failed) or "OpenOCD failed during startup.")
        self.gdb = subprocess.Popen(
            list(self.gdb_command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )
        self.gdb_reader = _ProcessReader(self.gdb, "gdb-live")
        self.gdb_reader.wait_for(lambda line: "(gdb)" in line, min(3.0, self.request.gdb_timeout_s))
        self._send("-gdb-set pagination off")
        self._send("-gdb-set target-async on")
        connect = self._send(f"-target-select extended-remote localhost:{self.profile.gdb_port}")
        if not _mi_ok(connect):
            raise RuntimeError(_mi_detail(connect) or "GDB failed to connect to OpenOCD.")
        self.target_state = _target_state_from_lines(self.gdb_reader.lines)
        self.read_pc()
        self.halted_by_probe = _connection_halt_observed(self.openocd_reader.lines, self.gdb_reader.lines)
        if not self.pc_value and self.request.allow_halt:
            self._halt_for_pc_readback()
        if self.halted_by_probe and self.request.resume_after_halt and self.target_state == "stopped":
            self.resume()

    def read_pc(self) -> str:
        self._require_alive()
        result = self._send("-data-evaluate-expression $pc")
        self.pc_value = _parse_pc_value(result)
        self.target_state = _target_state_from_lines(self.gdb_reader.lines if self.gdb_reader is not None else [])
        return self.pc_value

    def resume(self) -> bool:
        self._require_alive()
        result = self._send("-exec-continue")
        self.resumed_after_halt = _mi_ok(result) or "*running" in "\n".join(result)
        if self.resumed_after_halt and self.gdb_reader is not None:
            self.gdb_reader.wait_for(lambda line: "*running" in line, 1.0)
        self.target_state = _target_state_from_lines(self.gdb_reader.lines if self.gdb_reader is not None else [])
        return self.resumed_after_halt

    def _halt_for_pc_readback(self) -> None:
        interrupt = self._send("-exec-interrupt")
        self.halted_by_probe = self.halted_by_probe or _mi_ok(interrupt) or "*stopped" in "\n".join(interrupt)
        if self.gdb_reader is not None:
            self.gdb_reader.wait_for(lambda line: "*stopped" in line, 1.0)
            self.target_state = _target_state_from_lines(self.gdb_reader.lines)
        self.read_pc()

    def insert_breakpoint(self, location: str) -> tuple[str, tuple[str, ...]]:
        result = self._send_breakpoint_mutation(f"-break-insert {_quote_mi_arg(location)}")
        return _parse_breakpoint_number(result), result

    def delete_breakpoint(self, remote_id: str) -> tuple[str, ...]:
        return self._send_breakpoint_mutation(f"-break-delete {remote_id}")

    def enable_breakpoint(self, remote_id: str) -> tuple[str, ...]:
        return self._send_breakpoint_mutation(f"-break-enable {remote_id}")

    def disable_breakpoint(self, remote_id: str) -> tuple[str, ...]:
        return self._send_breakpoint_mutation(f"-break-disable {remote_id}")

    def _send_breakpoint_mutation(self, command: str) -> tuple[str, ...]:
        should_resume = self._ensure_stopped_for_mutation() and self.request.resume_after_halt
        result = self._send(command)
        if should_resume:
            self.resume()
        return result

    def _ensure_stopped_for_mutation(self) -> bool:
        self.target_state = _target_state_from_lines(self.gdb_reader.lines if self.gdb_reader is not None else [])
        if self.target_state != "running":
            return False
        interrupt = self._send("-exec-interrupt")
        halted = _mi_ok(interrupt) or "*stopped" in "\n".join(interrupt)
        if self.gdb_reader is not None:
            self.gdb_reader.wait_for(lambda line: "*stopped" in line, 1.0)
            self.target_state = _target_state_from_lines(self.gdb_reader.lines)
        self.halted_by_probe = self.halted_by_probe or halted or self.target_state == "stopped"
        return self.target_state == "stopped"

    def breakpoint_snapshot(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
    ) -> RemoteBreakpointSnapshot:
        result = self._send("-break-list")
        breakpoints = tuple(_parse_gdb_breakpoints(result))
        payload = {
            "backend": "openocd_gdb",
            "project": str(project_path or ""),
            "target": str(target_name or ""),
            "count": len(breakpoints),
            "breakpoints": [item.to_record() for item in breakpoints],
        }
        return RemoteBreakpointSnapshot(
            schema_version=1,
            snapshot_id=backend_snapshot_id(payload),
            project_path=Path(project_path).expanduser().resolve() if project_path else None,
            target_name=str(target_name or ""),
            captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            complete=True,
            breakpoints=breakpoints,
        )

    def close(self) -> None:
        _gdb_exit(self.gdb, self.gdb_reader)
        _terminate(self.gdb)
        _terminate(self.openocd)
        if self.gdb_reader is not None:
            self.gdb_reader.join(1.0)
        if self.openocd_reader is not None:
            self.openocd_reader.join(1.0)
        self.gdb = None
        self.openocd = None
        self.gdb_reader = None
        self.openocd_reader = None

    def _send(self, command: str) -> tuple[str, ...]:
        self._require_alive()
        self._token += 1
        return _send_mi(self.gdb, self.gdb_reader, self._token, command, self.request.gdb_timeout_s)  # type: ignore[arg-type]

    def _require_alive(self) -> None:
        if not self.alive:
            raise RuntimeError("OpenOCD/GDB live session is not connected.")


def _execute_readonly_probe(
    request: OpenOcdGdbReadOnlyRequest,
    profile: OpenOcdGdbProfile,
    openocd_command: tuple[str, ...],
    gdb_command: tuple[str, ...],
) -> OpenOcdGdbReadOnlyResult:
    openocd: subprocess.Popen[str] | None = None
    gdb: subprocess.Popen[str] | None = None
    openocd_reader: _ProcessReader | None = None
    gdb_reader: _ProcessReader | None = None
    target_state = "unknown"
    pc_value = ""
    halted_by_probe = False
    resumed_after_halt = False
    breakpoint_id = ""
    breakpoint_inserted = False
    breakpoint_deleted = False
    breakpoint_leaked = False
    breakpoint_detail = ""
    stage = "start_openocd"
    detail = ""
    succeeded = False
    try:
        openocd = subprocess.Popen(
            list(openocd_command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )
        openocd_reader = _ProcessReader(openocd, "openocd")
        if not openocd_reader.wait_for(
            lambda line: _openocd_ready(line, profile.gdb_port) or _openocd_failed(line),
            request.connect_timeout_s,
        ):
            detail = "OpenOCD did not report a GDB listener before timeout."
            return _result(request, profile, openocd_command, gdb_command, openocd_reader, gdb_reader, stage, detail, False)
        if any(_openocd_failed(line) for line in openocd_reader.lines):
            detail = _last_matching(openocd_reader.lines, _openocd_failed) or "OpenOCD failed during startup."
            return _result(request, profile, openocd_command, gdb_command, openocd_reader, gdb_reader, stage, detail, False)

        stage = "start_gdb"
        gdb = subprocess.Popen(
            list(gdb_command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )
        gdb_reader = _ProcessReader(gdb, "gdb")
        gdb_reader.wait_for(lambda line: "(gdb)" in line, min(3.0, request.gdb_timeout_s))

        stage = "gdb_connect"
        _send_mi(gdb, gdb_reader, 1, "-gdb-set pagination off", request.gdb_timeout_s)
        _send_mi(gdb, gdb_reader, 2, "-gdb-set target-async on", request.gdb_timeout_s)
        connect = _send_mi(
            gdb,
            gdb_reader,
            3,
            f"-target-select extended-remote localhost:{profile.gdb_port}",
            request.gdb_timeout_s,
        )
        if not _mi_ok(connect):
            detail = _mi_detail(connect) or "GDB failed to connect to OpenOCD."
            return _result(request, profile, openocd_command, gdb_command, openocd_reader, gdb_reader, stage, detail, False)
        target_state = _target_state_from_lines(gdb_reader.lines)

        stage = "read_pc"
        pc_result = _send_mi(gdb, gdb_reader, 4, "-data-evaluate-expression $pc", request.gdb_timeout_s)
        pc_value = _parse_pc_value(pc_result)
        halted_by_probe = halted_by_probe or _connection_halt_observed(openocd_reader.lines, gdb_reader.lines)
        if not pc_value and request.allow_halt:
            stage = "halt_for_pc"
            interrupt = _send_mi(gdb, gdb_reader, 5, "-exec-interrupt", request.gdb_timeout_s)
            halted_by_probe = _mi_ok(interrupt) or "*stopped" in "\n".join(interrupt)
            target_state = _target_state_from_lines(gdb_reader.lines)
            pc_result = _send_mi(gdb, gdb_reader, 6, "-data-evaluate-expression $pc", request.gdb_timeout_s)
            pc_value = _parse_pc_value(pc_result)
        if pc_value and request.breakpoint_location:
            stage = "breakpoint_smoke"
            breakpoint = _smoke_breakpoint(gdb, gdb_reader, request.breakpoint_location, request.gdb_timeout_s)
            breakpoint_id = breakpoint["id"]
            breakpoint_inserted = bool(breakpoint["inserted"])
            breakpoint_deleted = bool(breakpoint["deleted"])
            breakpoint_leaked = bool(breakpoint["leaked"])
            breakpoint_detail = str(breakpoint["detail"])
        if halted_by_probe and request.resume_after_halt and target_state == "stopped":
            stage = "resume_after_halt"
            resume = _send_mi(gdb, gdb_reader, 7, "-exec-continue", request.gdb_timeout_s)
            resumed_after_halt = _mi_ok(resume) or "*running" in "\n".join(resume)
            target_state = _target_state_from_lines(gdb_reader.lines)
        if pc_value:
            succeeded = not request.breakpoint_location or (
                breakpoint_inserted and breakpoint_deleted and not breakpoint_leaked
            )
            detail = "Connected through OpenOCD/GDB and read $pc."
            if request.breakpoint_location:
                detail = f"{detail} Breakpoint smoke: {breakpoint_detail}"
        else:
            succeeded = _mi_ok(connect)
            detail = _mi_detail(pc_result) or "Connected through OpenOCD/GDB, but PC was not readable without additional target control."
        return _result(
            request,
            profile,
            openocd_command,
            gdb_command,
            openocd_reader,
            gdb_reader,
            stage,
            detail,
            succeeded,
            target_state=target_state,
            pc_value=pc_value,
            halted_by_probe=halted_by_probe,
            resumed_after_halt=resumed_after_halt,
            breakpoint_location=request.breakpoint_location,
            breakpoint_id=breakpoint_id,
            breakpoint_inserted=breakpoint_inserted,
            breakpoint_deleted=breakpoint_deleted,
            breakpoint_leaked=breakpoint_leaked,
            breakpoint_detail=breakpoint_detail,
        )
    finally:
        _gdb_exit(gdb, gdb_reader)
        _terminate(gdb)
        _terminate(openocd)
        if gdb_reader is not None:
            gdb_reader.join(1.0)
        if openocd_reader is not None:
            openocd_reader.join(1.0)


def _result(
    request: OpenOcdGdbReadOnlyRequest,
    profile: OpenOcdGdbProfile,
    openocd_command: tuple[str, ...],
    gdb_command: tuple[str, ...],
    openocd_reader: "_ProcessReader | None",
    gdb_reader: "_ProcessReader | None",
    stage: str,
    detail: str,
    succeeded: bool,
    *,
    target_state: str = "unknown",
    pc_value: str = "",
    halted_by_probe: bool = False,
    resumed_after_halt: bool = False,
    breakpoint_location: str = "",
    breakpoint_id: str = "",
    breakpoint_inserted: bool = False,
    breakpoint_deleted: bool = False,
    breakpoint_leaked: bool = False,
    breakpoint_detail: str = "",
) -> OpenOcdGdbReadOnlyResult:
    return OpenOcdGdbReadOnlyResult(
        attempted=bool(request.execute),
        succeeded=bool(succeeded),
        stage=stage,
        detail=detail,
        profile=profile,
        openocd_command=openocd_command,
        gdb_command=gdb_command,
        openocd_lines=tuple(openocd_reader.lines if openocd_reader is not None else ()),
        gdb_lines=tuple(gdb_reader.lines if gdb_reader is not None else ()),
        target_state=target_state,
        pc_value=pc_value,
        halted_by_probe=halted_by_probe,
        resumed_after_halt=resumed_after_halt,
        breakpoint_location=breakpoint_location,
        breakpoint_id=breakpoint_id,
        breakpoint_inserted=breakpoint_inserted,
        breakpoint_deleted=breakpoint_deleted,
        breakpoint_leaked=breakpoint_leaked,
        breakpoint_detail=breakpoint_detail,
    )


class _ProcessReader:
    def __init__(self, process: subprocess.Popen[str], label: str) -> None:
        self.process = process
        self.label = label
        self.lines: list[str] = []
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=f"{label}-reader", daemon=True)
        self._thread.start()

    def wait_for(self, predicate: Callable[[str], bool], timeout_s: float) -> bool:
        deadline = time.monotonic() + max(0.1, float(timeout_s))
        while time.monotonic() < deadline:
            while True:
                try:
                    line = self._queue.get_nowait()
                except queue.Empty:
                    break
                if predicate(line):
                    return True
            if self.process.poll() is not None:
                return any(predicate(line) for line in self.lines)
            time.sleep(0.02)
        return any(predicate(line) for line in self.lines)

    def collect_until(self, predicate: Callable[[str], bool], timeout_s: float) -> tuple[str, ...]:
        start = len(self.lines)
        self.wait_for(predicate, timeout_s)
        return tuple(self.lines[start:])

    def join(self, timeout_s: float) -> None:
        self._thread.join(timeout=max(0.0, float(timeout_s)))

    def _run(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        for line in stream:
            text = line.rstrip()
            self.lines.append(text)
            self._queue.put(text)


def _send_mi(
    gdb: subprocess.Popen[str],
    reader: _ProcessReader,
    token: int,
    command: str,
    timeout_s: float,
) -> tuple[str, ...]:
    if gdb.stdin is None:
        return ()
    gdb.stdin.write(f"{token}{command}\n")
    gdb.stdin.flush()
    prefix = str(token)
    return reader.collect_until(lambda line: line.startswith(prefix + "^"), timeout_s)


def _openocd_command(profile: OpenOcdGdbProfile) -> tuple[str, ...]:
    command = [str(profile.openocd_path or "openocd")]
    if profile.scripts_dir is not None:
        command.extend(("-s", str(profile.scripts_dir)))
    command.extend(
        (
            "-f",
            profile.interface_name,
            "-f",
            profile.target_name,
            "-c",
            f"gdb_port {profile.gdb_port}",
            "-c",
            f"telnet_port {profile.telnet_port}",
            "-c",
            f"tcl_port {profile.tcl_port}",
        )
    )
    return tuple(command)


def _gdb_command(profile: OpenOcdGdbProfile, axf_path: Path | None) -> tuple[str, ...]:
    command = [str(profile.gdb_path or "arm-none-eabi-gdb"), "--interpreter=mi2", "-q"]
    if axf_path is not None and Path(axf_path).exists():
        command.append(str(Path(axf_path).expanduser().resolve()))
    return tuple(command)


def _openocd_ready(line: str, gdb_port: int) -> bool:
    text = line.lower()
    return "listening on port" in text and str(int(gdb_port)) in text and "gdb" in text


def _openocd_failed(line: str) -> bool:
    text = line.lower()
    return "error:" in text or "failed" in text or "unable to open" in text


def _last_matching(lines: list[str], predicate: Callable[[str], bool]) -> str:
    for line in reversed(lines):
        if predicate(line):
            return line
    return ""


def _mi_ok(lines: tuple[str, ...]) -> bool:
    return any("^done" in line or "^connected" in line or "^running" in line for line in lines)


def _mi_detail(lines: tuple[str, ...]) -> str:
    joined = "\n".join(lines)
    match = re.search(r'msg="([^"]+)"', joined)
    if match:
        return match.group(1).replace("\\n", " ")
    for line in reversed(lines):
        if line:
            return line
    return ""


def _parse_pc_value(lines: tuple[str, ...]) -> str:
    joined = "\n".join(lines)
    match = re.search(r'value="([^"]+)"', joined)
    return match.group(1) if match else ""


def _target_state_from_lines(lines: list[str]) -> str:
    for line in reversed(lines):
        if "*stopped" in line:
            return "stopped"
        if "*running" in line:
            return "running"
    return "unknown"


def _smoke_breakpoint(
    gdb: subprocess.Popen[str],
    reader: _ProcessReader,
    location: str,
    timeout_s: float,
) -> dict[str, object]:
    created_id = ""
    inserted = False
    deleted = False
    leaked = False
    detail = ""
    try:
        before = _send_mi(gdb, reader, 20, "-break-list", timeout_s)
        before_ids = _parse_breakpoint_numbers(before)
        insert = _send_mi(gdb, reader, 21, f"-break-insert {_quote_mi_arg(location)}", timeout_s)
        created_id = _parse_breakpoint_number(insert)
        inserted = bool(created_id) and _mi_ok(insert)
        if not inserted:
            detail = _mi_detail(insert) or "breakpoint insert failed"
            return {
                "id": created_id,
                "inserted": inserted,
                "deleted": deleted,
                "leaked": True,
                "detail": detail,
            }
        after_insert = _send_mi(gdb, reader, 22, "-break-list", timeout_s)
        after_insert_ids = _parse_breakpoint_numbers(after_insert)
        if created_id not in after_insert_ids or created_id in before_ids:
            detail = f"breakpoint {created_id} was not uniquely visible after insert"
            return {
                "id": created_id,
                "inserted": inserted,
                "deleted": deleted,
                "leaked": True,
                "detail": detail,
            }
        delete = _send_mi(gdb, reader, 23, f"-break-delete {created_id}", timeout_s)
        deleted = _mi_ok(delete)
        after_delete = _send_mi(gdb, reader, 24, "-break-list", timeout_s)
        leaked = created_id in _parse_breakpoint_numbers(after_delete)
        detail = f"breakpoint {created_id} inserted, listed, deleted, leak={leaked}"
        return {
            "id": created_id,
            "inserted": inserted,
            "deleted": deleted,
            "leaked": leaked,
            "detail": detail,
        }
    finally:
        if created_id and not deleted:
            try:
                _send_mi(gdb, reader, 25, f"-break-delete {created_id}", timeout_s)
            except Exception:
                pass


def _parse_breakpoint_number(lines: tuple[str, ...]) -> str:
    joined = "\n".join(lines)
    match = re.search(r'bkpt=\{number="([^"]+)"', joined)
    return match.group(1) if match else ""


def _parse_breakpoint_numbers(lines: tuple[str, ...]) -> set[str]:
    joined = "\n".join(lines)
    return set(re.findall(r'number="([^"]+)"', joined))


def _parse_gdb_breakpoints(lines: tuple[str, ...]) -> tuple[RemoteBreakpoint, ...]:
    joined = "\n".join(lines)
    items: list[RemoteBreakpoint] = []
    for body in re.findall(r"bkpt=\{([^{}]+)\}", joined):
        attrs = dict(re.findall(r'([A-Za-z0-9_-]+)="([^"]*)"', body))
        number = attrs.get("number", "")
        address = _hex_or_none(attrs.get("addr", ""))
        line = _int_or_zero(attrs.get("line", ""))
        path_text = attrs.get("fullname") or attrs.get("file") or ""
        path = Path(path_text.replace("\\\\", "\\")).expanduser() if path_text else None
        enabled_text = attrs.get("enabled", "")
        enabled = True if enabled_text == "y" else False if enabled_text == "n" else None
        raw = attrs.get("original-location") or attrs.get("what") or ""
        items.append(
            RemoteBreakpoint(
                path=path,
                line=line,
                address=address,
                enabled=enabled,
                remote_id=number,
                raw_location=raw,
                verified=True,
                message="OpenOCD/GDB 已回读该断点",
            )
        )
    return tuple(items)


def _hex_or_none(text: str | None) -> int | None:
    value = str(text or "").strip()
    if not value.startswith("0x"):
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def _int_or_zero(text: str | None) -> int:
    try:
        return int(str(text or "0"), 10)
    except ValueError:
        return 0


def _quote_mi_arg(text: str) -> str:
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _connection_halt_observed(openocd_lines: list[str], gdb_lines: list[str]) -> bool:
    openocd_text = "\n".join(openocd_lines).lower()
    gdb_text = "\n".join(gdb_lines)
    return "halted due to debug-request" in openocd_text or "*stopped" in gdb_text


def _gdb_exit(gdb: subprocess.Popen[str] | None, reader: _ProcessReader | None) -> None:
    if gdb is None or gdb.poll() is not None or gdb.stdin is None or reader is None:
        return
    try:
        _send_mi(gdb, reader, 90, "-gdb-exit", 1.5)
    except Exception:
        pass


def _terminate(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2.0)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)
