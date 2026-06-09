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
