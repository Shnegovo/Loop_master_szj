"""Safe UVSOCK preflight helpers.

This module deliberately stops before sending UVSOCK commands. It can load the
selected DLL and inspect whether uVision appears to be running, which is enough
for the next UI/backend wiring step without touching the target MCU.
"""

from __future__ import annotations

import ctypes
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.keil.discovery import KeilDiscovery, KeilFile, discover_keil


UVISION_PROCESS_NAMES = {"uv4.exe", "uv4", "uvision.exe", "uvision"}


@dataclass(frozen=True)
class KeilProcess:
    pid: int
    name: str
    path: str = ""


@dataclass(frozen=True)
class UvscLoadResult:
    dll: KeilFile | None
    loaded: bool
    error: str = ""
    handle: Any | None = None


@dataclass(frozen=True)
class UvscPreflight:
    discovery: KeilDiscovery
    processes: tuple[KeilProcess, ...]
    load_result: UvscLoadResult
    can_attempt_connection: bool
    reasons: tuple[str, ...]

    @property
    def uvision_running(self) -> bool:
        return bool(self.processes)

    def summary(self) -> str:
        dll_name = self.load_result.dll.path.name if self.load_result.dll else "--"
        reason_text = "OK" if not self.reasons else "; ".join(self.reasons)
        return (
            "UVSOCK preflight "
            f"dll={dll_name} loaded={self.load_result.loaded} "
            f"uvision_running={self.uvision_running} "
            f"can_attempt_connection={self.can_attempt_connection} "
            f"reasons={reason_text}"
        )


@dataclass(frozen=True)
class UvscConnectionResult:
    attempted: bool
    connected: bool
    port: int | None
    handle: int = 0
    status_code: int | None = None
    status_name: str = ""
    target_running: bool | None = None
    error: str = ""

    def summary(self) -> str:
        parts = [
            f"attempted={self.attempted}",
            f"connected={self.connected}",
            f"port={self.port if self.port is not None else '--'}",
        ]
        if self.status_code is not None:
            parts.append(f"status={self.status_name or self.status_code}")
        if self.target_running is not None:
            parts.append(f"target_running={self.target_running}")
        if self.error:
            parts.append(f"error={self.error}")
        return "UVSOCK connection " + " ".join(parts)


@dataclass(frozen=True)
class UvscVariableWriteResult:
    attempted: bool
    written: bool
    expression: str
    readback_expression: str
    value_text: str
    readback_text: str = ""
    assign_status: int | None = None
    readback_status: int | None = None
    error: str = ""

    def summary(self) -> str:
        status = "written" if self.written else "failed"
        detail = self.readback_text or self.error or "--"
        return f"UVSOCK variable write {status} {self.readback_expression}={detail}"


@dataclass(frozen=True)
class UvscLaunchPlan:
    command: tuple[str, ...]
    cwd: Path | None
    ready: bool
    reasons: tuple[str, ...]

    @property
    def display_command(self) -> str:
        return " ".join(shlex.quote(part) for part in self.command)


@dataclass(frozen=True)
class UvscLaunchResult:
    plan: UvscLaunchPlan
    launched: bool
    pid: int | None = None
    error: str = ""


@dataclass(frozen=True)
class UvscSmokeResult:
    launch: UvscLaunchResult | None
    preflight: UvscPreflight
    connection: UvscConnectionResult
    error: str = ""

    def summary(self) -> str:
        launched = self.launch.launched if self.launch else False
        return (
            "UVSOCK smoke "
            f"launched={launched} "
            f"attempted={self.connection.attempted} "
            f"connected={self.connection.connected} "
            f"error={self.error or self.connection.error or '--'}"
        )


UVSC_STATUS_NAMES = {
    0: "UVSC_STATUS_SUCCESS",
    1: "UVSC_STATUS_FAILED",
    2: "UVSC_STATUS_NOT_SUPPORTED",
    3: "UVSC_STATUS_NOT_INIT",
    4: "UVSC_STATUS_TIMEOUT",
    5: "UVSC_STATUS_INVALID_CONTEXT",
    6: "UVSC_STATUS_INVALID_PARAM",
    7: "UVSC_STATUS_BUFFER_TOO_SMALL",
    8: "UVSC_STATUS_CALLBACK_IN_USE",
    9: "UVSC_STATUS_COMMAND_ERROR",
}


VTT_UINT64 = 20
UVSOCK_SSTR_BYTES = 256
UVSC_MAX_API_STR_SIZE = 1024
UVSOCK_AMEM_HEADER_BYTES = 24


class UvscError(RuntimeError):
    def __init__(self, operation: str, status: int | None, detail: str = "") -> None:
        self.operation = operation
        self.status = status
        self.detail = detail
        name = uvsc_status_name(status) if status is not None else "--"
        message = f"{operation} failed: {name}"
        if detail:
            message += f" ({detail})"
        super().__init__(message)


class _TvalValue(ctypes.Union):
    _fields_ = [
        ("ul", ctypes.c_uint32),
        ("sc", ctypes.c_int8),
        ("uc", ctypes.c_uint8),
        ("i16", ctypes.c_int16),
        ("u16", ctypes.c_uint16),
        ("l", ctypes.c_int32),
        ("i", ctypes.c_int),
        ("i64", ctypes.c_int64),
        ("u64", ctypes.c_uint64),
        ("f", ctypes.c_float),
        ("d", ctypes.c_double),
    ]


class _Tval(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("vType", ctypes.c_int),
        ("v", _TvalValue),
    ]


class _Sstr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("nLen", ctypes.c_int),
        ("szStr", ctypes.c_char * UVSOCK_SSTR_BYTES),
    ]


class _Vset(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("val", _Tval),
        ("str", _Sstr),
    ]


class _UvsockOptions(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("flags", ctypes.c_uint32),
    ]


class _AmemHeader(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("nAddr", ctypes.c_uint64),
        ("nBytes", ctypes.c_uint32),
        ("ErrAddr", ctypes.c_uint64),
        ("nErr", ctypes.c_uint32),
    ]


class _ExecCmd(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("nRes", ctypes.c_uint32 * 7),
        ("sCmd", _Sstr),
    ]


class KeilUvscLiveSession:
    """Explicit UVSOCK live session for real Keil debug commands.

    The session does not launch uVision or change target run state. Callers must
    opt in with a project/port flow and should keep the UI confirmation layer
    outside this low-level wrapper.
    """

    def __init__(self, library, handle: int, *, owns_uvsc: bool = True) -> None:
        self.library = library
        self.handle = int(handle)
        self._owns_uvsc = bool(owns_uvsc)
        self._closed = False

    @classmethod
    def connect_existing(
        cls,
        root: str | os.PathLike[str] | None,
        port: int,
        *,
        connection_name: str = "LoopMaster",
        require_debug: bool = True,
        extended_stack: bool = True,
    ) -> "KeilUvscLiveSession":
        preflight = check_uvsock_preflight(root=root, require_running=True)
        if not preflight.can_attempt_connection:
            raise UvscError("UVSOCK preflight", None, "; ".join(preflight.reasons) or "preflight failed")
        library = preflight.load_result.handle
        if library is None:
            raise UvscError("UVSOCK load", None, "DLL handle is missing")
        if not (1 <= int(port) <= 65535):
            raise ValueError("UVSOCK port must be in range 1..65535")

        _configure_uvsc_signatures(library)
        init_code = int(library.UVSC_Init(int(port), int(port)))
        if init_code != 0:
            raise UvscError("UVSC_Init", init_code)

        handle = ctypes.c_int(0)
        uv_port = ctypes.c_int(int(port))
        name = connection_name.encode("utf-8")[:256] or b"LoopMaster"
        try:
            open_code = int(
                library.UVSC_OpenConnection(
                    ctypes.c_char_p(name),
                    ctypes.byref(handle),
                    ctypes.byref(uv_port),
                    None,
                    0,
                    None,
                    None,
                    None,
                    0,
                    None,
                )
            )
            if open_code != 0:
                raise UvscError("UVSC_OpenConnection", open_code)
            session = cls(library, handle.value, owns_uvsc=True)
            if extended_stack:
                session.set_extended_stack(True)
            if require_debug:
                session.enter_debug()
            return session
        except Exception:
            if handle.value:
                try:
                    library.UVSC_CloseConnection(handle.value, 0)
                except Exception:
                    pass
            _safe_uvsc_uninit(library)
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.handle:
                self.library.UVSC_CloseConnection(self.handle, 0)
        finally:
            if self._owns_uvsc:
                _safe_uvsc_uninit(self.library)

    def __enter__(self) -> "KeilUvscLiveSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def enter_debug(self) -> None:
        status = int(self.library.UVSC_DBG_ENTER(self.handle))
        if status != 0:
            detail = self.last_error_text()
            if "Target is in debug mode" in detail:
                return
            raise UvscError("UVSC_DBG_ENTER", status, detail)

    def exit_debug(self) -> None:
        status = int(self.library.UVSC_DBG_EXIT(self.handle))
        if status != 0:
            raise UvscError("UVSC_DBG_EXIT", status, self.last_error_text())

    def set_extended_stack(self, enabled: bool = True) -> None:
        options = _UvsockOptions(flags=1 if enabled else 0)
        status = int(self.library.UVSC_GEN_SET_OPTIONS(self.handle, ctypes.byref(options)))
        if status != 0:
            raise UvscError("UVSC_GEN_SET_OPTIONS", status, self.last_error_text())

    def target_running(self) -> bool | None:
        running = ctypes.c_int(0)
        status = int(self.library.UVSC_DBG_STATUS(self.handle, ctypes.byref(running)))
        if status != 0:
            raise UvscError("UVSC_DBG_STATUS", status, self.last_error_text())
        return bool(running.value)

    def last_error_text(self) -> str:
        msg_type = ctypes.c_int(0)
        status = ctypes.c_int(0)
        buffer = ctypes.create_string_buffer(UVSC_MAX_API_STR_SIZE)
        try:
            result = int(
                self.library.UVSC_GetLastError(
                    self.handle,
                    ctypes.byref(msg_type),
                    ctypes.byref(status),
                    buffer,
                    ctypes.sizeof(buffer),
                )
            )
        except Exception:
            return ""
        text = buffer.value.decode("utf-8", errors="replace").strip()
        parts = []
        if result != 0:
            parts.append(f"last_error_status={uvsc_status_name(result)}")
        if msg_type.value:
            parts.append(f"msg=0x{msg_type.value:X}")
        if status.value:
            parts.append(f"uv_status={status.value}")
        if text:
            parts.append(text)
        return "; ".join(parts)

    def evaluate_expression(self, expression: str) -> tuple[str, int]:
        vset = _make_vset(expression)
        status = int(
            self.library.UVSC_DBG_EVAL_EXPRESSION_TO_STR(
                self.handle,
                ctypes.byref(vset),
                ctypes.sizeof(vset),
            )
        )
        if status != 0:
            return "", status
        return _sstr_to_text(vset.str), status

    def assign_expression(self, expression: str) -> tuple[int, str]:
        vset = _make_vset(expression)
        status = int(
            self.library.UVSC_DBG_CALC_EXPRESSION(
                self.handle,
                ctypes.byref(vset),
                ctypes.sizeof(vset),
            )
        )
        return status, "" if status == 0 else self.last_error_text()

    def write_expression_value(self, expression: str, value_text: str) -> UvscVariableWriteResult:
        expression = _sanitize_expression(expression)
        value_text = value_text.strip()
        if not value_text:
            return UvscVariableWriteResult(
                attempted=False,
                written=False,
                expression=expression,
                readback_expression=expression,
                value_text=value_text,
                error="value is empty",
            )
        assignment = f"{expression} = {value_text}"
        assign_status, assign_error = self.assign_expression(assignment)
        if assign_status != 0:
            return UvscVariableWriteResult(
                attempted=True,
                written=False,
                expression=assignment,
                readback_expression=expression,
                value_text=value_text,
                assign_status=assign_status,
                error=assign_error or uvsc_status_name(assign_status),
            )
        readback_text, readback_status = self.evaluate_expression(expression)
        return UvscVariableWriteResult(
            attempted=True,
            written=readback_status == 0,
            expression=assignment,
            readback_expression=expression,
            value_text=value_text,
            readback_text=readback_text,
            assign_status=assign_status,
            readback_status=readback_status,
            error="" if readback_status == 0 else uvsc_status_name(readback_status),
        )

    def read_memory(self, address: int, size: int) -> bytes:
        if int(address) < 0:
            raise ValueError("memory address must be non-negative")
        if not (1 <= int(size) <= 4096):
            raise ValueError("memory read size must be in range 1..4096")
        buffer = _make_amem_buffer(int(address), bytes(int(size)))
        status = int(self.library.UVSC_DBG_MEM_READ(self.handle, buffer, ctypes.sizeof(buffer)))
        if status != 0:
            raise UvscError("UVSC_DBG_MEM_READ", status, self.last_error_text())
        return bytes(buffer[UVSOCK_AMEM_HEADER_BYTES : UVSOCK_AMEM_HEADER_BYTES + int(size)])

    def write_memory(self, address: int, data: bytes) -> None:
        if int(address) < 0:
            raise ValueError("memory address must be non-negative")
        payload = bytes(data)
        if not (1 <= len(payload) <= 4096):
            raise ValueError("memory write size must be in range 1..4096")
        buffer = _make_amem_buffer(int(address), payload)
        status = int(self.library.UVSC_DBG_MEM_WRITE(self.handle, buffer, ctypes.sizeof(buffer)))
        if status != 0:
            raise UvscError("UVSC_DBG_MEM_WRITE", status, self.last_error_text())

    def execute_command(self, command: str, *, echo: bool = False) -> None:
        cmd = _ExecCmd()
        cmd.flags = 1 if echo else 0
        _set_sstr(cmd.sCmd, _sanitize_expression(command))
        status = int(self.library.UVSC_DBG_EXEC_CMD(self.handle, ctypes.byref(cmd), ctypes.sizeof(cmd)))
        if status != 0:
            raise UvscError("UVSC_DBG_EXEC_CMD", status, self.last_error_text())


def check_uvsock_preflight(
    root: str | os.PathLike[str] | None = None,
    require_running: bool = False,
) -> UvscPreflight:
    discovery = discover_keil(root)
    processes = list_running_uvision()
    load_result = load_uvsc_library(discovery)
    reasons = []

    if not discovery.installed:
        reasons.append("Keil/uVision was not discovered")
    if not load_result.loaded:
        reasons.append(load_result.error or "UVSOCK DLL could not be loaded")
    if not processes:
        reasons.append("uVision is not running")

    can_attempt = discovery.installed and load_result.loaded and bool(processes)
    if require_running:
        can_attempt = can_attempt and bool(processes)

    return UvscPreflight(
        discovery=discovery,
        processes=processes,
        load_result=load_result,
        can_attempt_connection=can_attempt,
        reasons=tuple(reasons),
    )


def attempt_existing_uvsock_connection(
    root: str | os.PathLike[str] | None,
    port: int | None,
    query_status: bool = False,
    connection_name: str = "LoopMaster",
) -> tuple[UvscPreflight, UvscConnectionResult]:
    """Opt-in connection to an already running UVSOCK port.

    This function never starts uVision. It only connects to an explicit port,
    optionally asks for debug run status, then closes the connection.
    """

    preflight = check_uvsock_preflight(root=root, require_running=True)
    if port is None:
        return preflight, UvscConnectionResult(
            attempted=False,
            connected=False,
            port=None,
            error="No UVSOCK port was provided",
        )
    if not (1 <= int(port) <= 65535):
        return preflight, UvscConnectionResult(
            attempted=False,
            connected=False,
            port=int(port),
            error="UVSOCK port must be in range 1..65535",
        )
    if not preflight.can_attempt_connection:
        return preflight, UvscConnectionResult(
            attempted=False,
            connected=False,
            port=int(port),
            error="; ".join(preflight.reasons) or "preflight failed",
        )

    library = preflight.load_result.handle
    if library is None:
        return preflight, UvscConnectionResult(
            attempted=False,
            connected=False,
            port=int(port),
            error="UVSOCK DLL handle is missing",
        )

    _configure_uvsc_signatures(library)
    init_code = int(library.UVSC_Init(int(port), int(port)))
    if init_code != 0:
        return preflight, UvscConnectionResult(
            attempted=True,
            connected=False,
            port=int(port),
            status_code=init_code,
            status_name=uvsc_status_name(init_code),
            error="UVSC_Init failed",
        )

    handle = ctypes.c_int(0)
    uv_port = ctypes.c_int(int(port))
    name = connection_name.encode("utf-8")[:256] or b"LoopMaster"
    open_code = int(
        library.UVSC_OpenConnection(
            ctypes.c_char_p(name),
            ctypes.byref(handle),
            ctypes.byref(uv_port),
            None,
            0,
            None,
            None,
            None,
            0,
            None,
        )
    )
    if open_code != 0:
        _safe_uvsc_uninit(library)
        return preflight, UvscConnectionResult(
            attempted=True,
            connected=False,
            port=int(uv_port.value),
            status_code=open_code,
            status_name=uvsc_status_name(open_code),
            error="UVSC_OpenConnection failed",
        )

    status_code: int | None = None
    target_running: bool | None = None
    try:
        if query_status:
            running = ctypes.c_int(0)
            status_code = int(library.UVSC_DBG_STATUS(handle.value, ctypes.byref(running)))
            if status_code == 0:
                target_running = bool(running.value)
    finally:
        try:
            library.UVSC_CloseConnection(handle.value, 0)
        finally:
            _safe_uvsc_uninit(library)

    return preflight, UvscConnectionResult(
        attempted=True,
        connected=True,
        port=int(uv_port.value),
        handle=int(handle.value),
        status_code=status_code,
        status_name=uvsc_status_name(status_code) if status_code is not None else "",
        target_running=target_running,
    )


def build_uvision_uvsock_command(
    root: str | os.PathLike[str] | None,
    port: int,
    project: str | os.PathLike[str] | None = None,
    target: str | None = None,
    use_console: bool = False,
) -> UvscLaunchPlan:
    discovery = discover_keil(root)
    reasons = []

    if not discovery.installed or discovery.uv4_dir is None:
        reasons.append("Keil/uVision was not discovered")
        return UvscLaunchPlan(command=(), cwd=None, ready=False, reasons=tuple(reasons))

    if not (1 <= int(port) <= 65535):
        reasons.append("UVSOCK port must be in range 1..65535")

    executable = discovery.uvision_com if use_console else discovery.uv4_exe
    if executable is None or not executable.exists:
        reasons.append("uVision executable is missing")
        exe_path = ""
    else:
        exe_path = str(executable.path)

    command: list[str] = [exe_path] if exe_path else []
    if project:
        project_path = Path(project).expanduser().resolve()
        if not project_path.exists():
            reasons.append(f"Keil project does not exist: {project_path}")
        command.append(str(project_path))
    else:
        reasons.append("No Keil project was provided; launch is guidance-only")

    command.extend(["-s", str(int(port))])
    if target:
        command.extend(["-t", str(target)])

    return UvscLaunchPlan(
        command=tuple(command),
        cwd=discovery.uv4_dir,
        ready=bool(command and not reasons),
        reasons=tuple(reasons),
    )


def start_uvision_uvsock(
    root: str | os.PathLike[str] | None,
    port: int,
    project: str | os.PathLike[str],
    target: str | None = None,
    use_console: bool = False,
) -> UvscLaunchResult:
    plan = build_uvision_uvsock_command(
        root=root,
        port=port,
        project=project,
        target=target,
        use_console=use_console,
    )
    if not plan.ready:
        return UvscLaunchResult(plan=plan, launched=False, error="; ".join(plan.reasons))

    try:
        process = subprocess.Popen(
            list(plan.command),
            cwd=str(plan.cwd) if plan.cwd else None,
            close_fds=True,
        )
    except Exception as exc:
        return UvscLaunchResult(plan=plan, launched=False, error=str(exc))
    return UvscLaunchResult(plan=plan, launched=True, pid=int(process.pid))


def run_uvsock_smoke(
    root: str | os.PathLike[str] | None,
    port: int,
    project: str | os.PathLike[str] | None = None,
    target: str | None = None,
    launch: bool = False,
    wait_seconds: float = 8.0,
    query_status: bool = True,
) -> UvscSmokeResult:
    launch_result: UvscLaunchResult | None = None
    if launch:
        if not project:
            preflight = check_uvsock_preflight(root=root, require_running=True)
            return UvscSmokeResult(
                launch=None,
                preflight=preflight,
                connection=UvscConnectionResult(
                    attempted=False,
                    connected=False,
                    port=int(port),
                    error="A Keil project is required to launch uVision",
                ),
                error="A Keil project is required to launch uVision",
            )
        launch_result = start_uvision_uvsock(
            root=root,
            port=port,
            project=project,
            target=target,
        )
        if not launch_result.launched:
            preflight = check_uvsock_preflight(root=root, require_running=True)
            return UvscSmokeResult(
                launch=launch_result,
                preflight=preflight,
                connection=UvscConnectionResult(
                    attempted=False,
                    connected=False,
                    port=int(port),
                    error=launch_result.error,
                ),
                error=launch_result.error,
            )
        _wait_for_uvision(wait_seconds)

    preflight, connection = attempt_existing_uvsock_connection(
        root=root,
        port=port,
        query_status=query_status,
    )
    return UvscSmokeResult(
        launch=launch_result,
        preflight=preflight,
        connection=connection,
        error=connection.error,
    )


def load_uvsc_library(discovery: KeilDiscovery) -> UvscLoadResult:
    dll = discovery.preferred_uvsc
    if dll is None or not dll.exists:
        return UvscLoadResult(dll=dll, loaded=False, error="No UVSOCK DLL was selected")
    if os.name != "nt":
        return UvscLoadResult(dll=dll, loaded=False, error="UVSOCK DLL loading requires Windows")
    if discovery.python_bits >= 64 and dll.machine and dll.machine != "x64":
        return UvscLoadResult(
            dll=dll,
            loaded=False,
            error=f"64-bit Python cannot load {dll.machine} UVSOCK DLL",
        )

    try:
        with _dll_directory(dll.path.parent):
            handle = ctypes.WinDLL(str(dll.path))
    except Exception as exc:
        return UvscLoadResult(dll=dll, loaded=False, error=str(exc))
    return UvscLoadResult(dll=dll, loaded=True, handle=handle)


def uvsc_status_name(status: int | None) -> str:
    if status is None:
        return ""
    return UVSC_STATUS_NAMES.get(int(status), f"UVSC_STATUS_{int(status)}")


def list_running_uvision() -> tuple[KeilProcess, ...]:
    try:
        import psutil
    except Exception:
        return ()

    processes = []
    for process in psutil.process_iter(("pid", "name", "exe")):
        try:
            info = process.info
        except Exception:
            continue
        name = str(info.get("name") or "")
        if name.lower() not in UVISION_PROCESS_NAMES:
            continue
        processes.append(
            KeilProcess(
                pid=int(info.get("pid") or 0),
                name=name,
                path=str(info.get("exe") or ""),
            )
        )
    return tuple(sorted(processes, key=lambda item: item.pid))


class _dll_directory:
    def __init__(self, path: Path) -> None:
        self._path = str(path)
        self._token = None

    def __enter__(self):
        add_directory = getattr(os, "add_dll_directory", None)
        if callable(add_directory):
            self._token = add_directory(self._path)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            self._token.close()
            self._token = None


def _configure_uvsc_signatures(library) -> None:
    library.UVSC_Init.argtypes = [ctypes.c_int, ctypes.c_int]
    library.UVSC_Init.restype = ctypes.c_int
    library.UVSC_UnInit.argtypes = []
    library.UVSC_UnInit.restype = ctypes.c_int
    library.UVSC_OpenConnection.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_void_p,
    ]
    library.UVSC_OpenConnection.restype = ctypes.c_int
    library.UVSC_CloseConnection.argtypes = [ctypes.c_int, ctypes.c_int]
    library.UVSC_CloseConnection.restype = ctypes.c_int
    library.UVSC_DBG_STATUS.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    library.UVSC_DBG_STATUS.restype = ctypes.c_int
    library.UVSC_DBG_ENTER.argtypes = [ctypes.c_int]
    library.UVSC_DBG_ENTER.restype = ctypes.c_int
    library.UVSC_DBG_EXIT.argtypes = [ctypes.c_int]
    library.UVSC_DBG_EXIT.restype = ctypes.c_int
    library.UVSC_GEN_SET_OPTIONS.argtypes = [ctypes.c_int, ctypes.POINTER(_UvsockOptions)]
    library.UVSC_GEN_SET_OPTIONS.restype = ctypes.c_int
    library.UVSC_DBG_CALC_EXPRESSION.argtypes = [ctypes.c_int, ctypes.POINTER(_Vset), ctypes.c_int]
    library.UVSC_DBG_CALC_EXPRESSION.restype = ctypes.c_int
    library.UVSC_DBG_EVAL_EXPRESSION_TO_STR.argtypes = [ctypes.c_int, ctypes.POINTER(_Vset), ctypes.c_int]
    library.UVSC_DBG_EVAL_EXPRESSION_TO_STR.restype = ctypes.c_int
    library.UVSC_DBG_EXEC_CMD.argtypes = [ctypes.c_int, ctypes.POINTER(_ExecCmd), ctypes.c_int]
    library.UVSC_DBG_EXEC_CMD.restype = ctypes.c_int
    library.UVSC_DBG_MEM_READ.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    library.UVSC_DBG_MEM_READ.restype = ctypes.c_int
    library.UVSC_DBG_MEM_WRITE.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    library.UVSC_DBG_MEM_WRITE.restype = ctypes.c_int
    library.UVSC_GetLastError.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    library.UVSC_GetLastError.restype = ctypes.c_int


def _safe_uvsc_uninit(library) -> None:
    try:
        library.UVSC_UnInit()
    except Exception:
        pass


def _wait_for_uvision(timeout: float) -> bool:
    deadline = time.perf_counter() + max(0.0, float(timeout))
    while time.perf_counter() < deadline:
        if list_running_uvision():
            return True
        time.sleep(0.1)
    return bool(list_running_uvision())


def _sanitize_expression(expression: str) -> str:
    text = expression.strip()
    if not text:
        raise ValueError("expression is empty")
    if "\x00" in text or "\n" in text or "\r" in text:
        raise ValueError("expression contains unsupported control characters")
    return text


def _set_sstr(target: _Sstr, text: str) -> None:
    data = text.encode("utf-8")
    if len(data) >= UVSOCK_SSTR_BYTES:
        raise ValueError(f"UVSOCK string is too long ({len(data)} bytes)")
    target.nLen = len(data) + 1
    target.szStr = data + b"\x00" * (UVSOCK_SSTR_BYTES - len(data))


def _sstr_to_text(source: _Sstr) -> str:
    raw = bytes(source.szStr)
    if b"\x00" in raw:
        raw = raw.split(b"\x00", 1)[0]
    return raw.decode("utf-8", errors="replace").strip()


def _make_vset(expression: str) -> _Vset:
    vset = _Vset()
    vset.val.vType = VTT_UINT64
    vset.val.v.u64 = 0
    _set_sstr(vset.str, _sanitize_expression(expression))
    return vset


def _make_amem_buffer(address: int, data: bytes) -> ctypes.Array:
    payload = bytes(data)
    buffer_type = ctypes.c_ubyte * (UVSOCK_AMEM_HEADER_BYTES + len(payload))
    buffer = buffer_type()
    header = _AmemHeader.from_buffer(buffer)
    header.nAddr = int(address)
    header.nBytes = len(payload)
    header.ErrAddr = 0
    header.nErr = 0
    if payload:
        ctypes.memmove(ctypes.addressof(buffer) + UVSOCK_AMEM_HEADER_BYTES, payload, len(payload))
    return buffer
