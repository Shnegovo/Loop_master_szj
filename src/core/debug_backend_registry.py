"""Registry for debugger backend adapters.

The registry owns adapter factories, not live sessions. Creating a backend may
perform light object construction, but registration itself must not launch Keil,
start OpenOCD, connect pyOCD, or touch a probe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.core.debug_backend import DebugBackendAdapter
from src.core.debug_workbench import (
    DebugBackendKind,
    DebugRuntimeState,
    make_debug_status,
)
from src.core.keil.backend import KeilBackendConfig, KeilUvSockBackendAdapter
from src.core.debug_backend import (
    DebugBackendDiagnostic,
    DebugBackendSessionSnapshot,
    backend_snapshot_id,
    now_iso,
)


DebugBackendFactory = Callable[[], DebugBackendAdapter]


@dataclass(frozen=True)
class DebugBackendDescriptor:
    kind: DebugBackendKind
    display_name: str
    factory: DebugBackendFactory
    read_only_first: bool = True
    notes: str = ""

    def create(self) -> DebugBackendAdapter:
        return self.factory()


class DebugBackendRegistry:
    def __init__(self) -> None:
        self._descriptors: dict[DebugBackendKind, DebugBackendDescriptor] = {}

    def register(self, descriptor: DebugBackendDescriptor) -> None:
        self._descriptors[descriptor.kind] = descriptor

    def kinds(self) -> tuple[DebugBackendKind, ...]:
        return tuple(self._descriptors)

    def descriptors(self) -> tuple[DebugBackendDescriptor, ...]:
        return tuple(self._descriptors.values())

    def descriptor(self, kind: DebugBackendKind | str) -> DebugBackendDescriptor:
        backend_kind = _coerce_backend_kind(kind)
        try:
            return self._descriptors[backend_kind]
        except KeyError as exc:
            raise KeyError(f"debug backend is not registered: {backend_kind.value}") from exc

    def create(self, kind: DebugBackendKind | str) -> DebugBackendAdapter:
        return self.descriptor(kind).create()

    def default_kind(self) -> DebugBackendKind:
        if DebugBackendKind.KEIL in self._descriptors:
            return DebugBackendKind.KEIL
        if self._descriptors:
            return next(iter(self._descriptors))
        raise RuntimeError("no debug backends are registered")


@dataclass(frozen=True)
class UnavailableDebugBackend:
    kind: DebugBackendKind
    display_name: str
    detail: str

    def discover(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
        previous_status: object | None = None,
    ) -> DebugBackendSessionSnapshot:
        return self._snapshot(
            project_path=project_path,
            target_name=target_name,
            attempted=False,
        )

    def read_only_session_snapshot(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
        previous_status: object | None = None,
        attempt_connection: bool = True,
        query_status: bool = True,
    ) -> DebugBackendSessionSnapshot:
        return self._snapshot(
            project_path=project_path,
            target_name=target_name,
            attempted=bool(attempt_connection),
        )

    def _snapshot(
        self,
        *,
        project_path: str | Path | None,
        target_name: str,
        attempted: bool,
    ) -> DebugBackendSessionSnapshot:
        project = Path(project_path).expanduser().resolve() if project_path else None
        captured_at = now_iso()
        status = make_debug_status(
            state=DebugRuntimeState.ERROR,
            backend=self.kind,
            detail=self.detail,
            project_path=project,
            target_name=target_name,
            error=self.detail,
        )
        diagnostics = (
            DebugBackendDiagnostic("后端", self.display_name),
            DebugBackendDiagnostic("状态", "尚未接入"),
            DebugBackendDiagnostic("说明", self.detail),
        )
        payload = {
            "backend": self.kind.value,
            "adapter": self.display_name,
            "project": str(project or ""),
            "target": str(target_name or ""),
            "attempted": attempted,
            "detail": self.detail,
        }
        return DebugBackendSessionSnapshot(
            schema_version=1,
            backend=self.kind,
            adapter_name=self.display_name,
            snapshot_id=backend_snapshot_id(payload),
            captured_at=captured_at,
            status=status,
            diagnostics=diagnostics,
            capabilities=(),
            read_only=True,
            connection_attempted=attempted,
            connection_established=False,
            target_running=None,
            project_path=project,
            target_name=str(target_name or ""),
        )


def create_default_debug_backend_registry(
    *,
    keil_root: str | Path | None = None,
    uvsock_port: int = 4827,
    include_placeholders: bool = False,
) -> DebugBackendRegistry:
    registry = DebugBackendRegistry()
    root = Path(keil_root).expanduser() if keil_root else None
    port = int(uvsock_port)
    registry.register(
        DebugBackendDescriptor(
            kind=DebugBackendKind.KEIL,
            display_name="Keil / UVSOCK",
            factory=lambda: KeilUvSockBackendAdapter(
                KeilBackendConfig(root=root, port=port)
            ),
            read_only_first=True,
            notes="Keil is the first real backend; all live control remains opt-in.",
        )
    )
    if include_placeholders:
        _register_unavailable_placeholders(registry)
    return registry


def _register_unavailable_placeholders(registry: DebugBackendRegistry) -> None:
    placeholders = (
        (
            DebugBackendKind.OPENOCD_GDB,
            "OpenOCD / GDB",
            "OpenOCD/GDB 后端占位已注册，真实进程启动与端口连接将在后续 opt-in 阶段接入。",
        ),
        (
            DebugBackendKind.PYOCD,
            "pyOCD",
            "pyOCD 后端占位已注册，真实连接将在后续 opt-in 阶段接入。",
        ),
        (
            DebugBackendKind.OFFLINE,
            "离线回放",
            "离线回放后端占位已注册，后续用于记录回放和无硬件调试。",
        ),
    )
    for kind, display_name, detail in placeholders:
        registry.register(
            DebugBackendDescriptor(
                kind=kind,
                display_name=display_name,
                factory=lambda k=kind, name=display_name, text=detail: UnavailableDebugBackend(k, name, text),
                read_only_first=True,
                notes=detail,
            )
        )


def _coerce_backend_kind(kind: DebugBackendKind | str) -> DebugBackendKind:
    if isinstance(kind, DebugBackendKind):
        return kind
    return DebugBackendKind(str(kind))
