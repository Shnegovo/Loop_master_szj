"""Safe UVSOCK preflight helpers.

This module deliberately stops before sending UVSOCK commands. It can load the
selected DLL and inspect whether uVision appears to be running, which is enough
for the next UI/backend wiring step without touching the target MCU.
"""

from __future__ import annotations

import ctypes
import os
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
    init_code = int(library.UVSC_Init(1, 65535))
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


def _safe_uvsc_uninit(library) -> None:
    try:
        library.UVSC_UnInit()
    except Exception:
        pass
