"""Dry-run controller for backend-neutral debug sessions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from src.core.debug_backend import DebugBackendSessionSnapshot
from src.core.debug_backend_registry import DebugBackendRegistry
from src.core.debug_session_contract import (
    DebugSessionBackend,
    DebugSessionCommand,
    DebugSessionSafetyPolicy,
    DebugSessionSnapshot,
    DebugSessionSpec,
    DebugSessionState,
    command_matrix_for_session,
    event_from_session,
    make_debug_session_snapshot,
)
from src.core.debug_workbench import DebugBackendKind


@dataclass(frozen=True)
class DebugSessionControllerState:
    spec: DebugSessionSpec
    snapshot: DebugSessionSnapshot
    commands: tuple[DebugSessionCommand, ...]

    def command(self, key: str) -> DebugSessionCommand | None:
        wanted = str(key)
        for command in self.commands:
            if command.key == wanted:
                return command
        return None

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        return self.snapshot.diagnostic_rows()

    def to_record(self) -> dict[str, object]:
        return {
            "spec": self.spec.to_record(),
            "snapshot": self.snapshot.to_record(),
            "commands": [command.to_record() for command in self.commands],
        }


class DebugSessionController:
    """Own selected backend, dry-run policy and contract snapshot.

    This controller intentionally does not execute commands. It can ask adapters
    for no-connect discovery snapshots, convert them to the neutral contract and
    expose a command matrix guarded by the current safety policy.
    """

    def __init__(
        self,
        registry: DebugBackendRegistry,
        *,
        safety_policy: DebugSessionSafetyPolicy | None = None,
        backend: DebugBackendKind | str | None = None,
    ) -> None:
        self._registry = registry
        self._safety_policy = safety_policy or DebugSessionSafetyPolicy()
        self._backend_kind = _coerce_backend_kind(backend) if backend is not None else registry.default_kind()
        self._state = self._make_initial_state()

    @property
    def backend_kind(self) -> DebugBackendKind:
        return self._backend_kind

    @property
    def safety_policy(self) -> DebugSessionSafetyPolicy:
        return self._safety_policy

    @property
    def state(self) -> DebugSessionControllerState:
        return self._state

    @property
    def snapshot(self) -> DebugSessionSnapshot:
        return self._state.snapshot

    @property
    def commands(self) -> tuple[DebugSessionCommand, ...]:
        return self._state.commands

    def set_backend(self, backend: DebugBackendKind | str) -> DebugSessionControllerState:
        self._backend_kind = _coerce_backend_kind(backend)
        self._state = self._make_initial_state()
        return self._state

    def set_safety_policy(self, policy: DebugSessionSafetyPolicy) -> DebugSessionControllerState:
        self._safety_policy = policy
        self._state = self._with_policy(self._state.snapshot)
        return self._state

    def update_spec(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
        source_provider: str = "",
        metadata: dict[str, str] | None = None,
    ) -> DebugSessionControllerState:
        spec = replace(
            self._state.spec,
            project_path=Path(project_path).expanduser().resolve() if project_path else None,
            target_name=str(target_name or ""),
            source_provider=str(source_provider or ""),
            metadata=dict(metadata or {}),
        )
        snapshot = make_debug_session_snapshot(
            backend=spec.backend,
            display_name=spec.display_name,
            state=DebugSessionState.DISCONNECTED,
            project_path=spec.project_path,
            target_name=spec.target_name,
            detail="等待刷新调试后端",
            safety_policy=self._safety_policy,
            diagnostics=(("后端", spec.display_name), ("状态", "等待刷新")),
            metadata=spec.metadata,
        )
        self._state = self._build_state(spec, snapshot)
        return self._state

    def discover(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
    ) -> DebugSessionControllerState:
        spec = self._spec(
            project_path=project_path,
            target_name=target_name,
        )
        adapter = self._registry.create(self._backend_kind)
        backend_snapshot = adapter.discover(
            project_path=spec.project_path,
            target_name=spec.target_name,
        )
        self._state = self._from_backend_snapshot(spec, backend_snapshot)
        return self._state

    def preview_read_only_snapshot(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
        attempt_connection: bool = False,
        query_status: bool = False,
    ) -> DebugSessionControllerState:
        spec = self._spec(
            project_path=project_path,
            target_name=target_name,
        )
        adapter = self._registry.create(self._backend_kind)
        backend_snapshot = adapter.read_only_session_snapshot(
            project_path=spec.project_path,
            target_name=spec.target_name,
            attempt_connection=bool(attempt_connection),
            query_status=bool(query_status),
        )
        self._state = self._from_backend_snapshot(spec, backend_snapshot)
        return self._state

    def record_event(self, kind: str, detail: str) -> dict[str, object]:
        return event_from_session(self.snapshot, kind=kind, detail=detail).to_record()

    def _make_initial_state(self) -> DebugSessionControllerState:
        spec = self._spec()
        snapshot = make_debug_session_snapshot(
            backend=spec.backend,
            display_name=spec.display_name,
            state=DebugSessionState.DISCONNECTED,
            project_path=spec.project_path,
            target_name=spec.target_name,
            safety_policy=self._safety_policy,
            diagnostics=(("后端", spec.display_name), ("状态", "未连接")),
            metadata=spec.metadata,
        )
        return self._build_state(spec, snapshot)

    def _with_policy(self, snapshot: DebugSessionSnapshot) -> DebugSessionControllerState:
        adjusted = replace(snapshot, safety_policy=self._safety_policy)
        return self._build_state(self._state.spec, adjusted)

    def _from_backend_snapshot(
        self,
        spec: DebugSessionSpec,
        backend_snapshot: DebugBackendSessionSnapshot,
    ) -> DebugSessionControllerState:
        snapshot = backend_snapshot.to_session_contract()
        snapshot = replace(
            snapshot,
            safety_policy=self._safety_policy,
            metadata=dict(snapshot.metadata or {}) | dict(spec.metadata or {}),
        )
        return self._build_state(spec, snapshot)

    def _build_state(
        self,
        spec: DebugSessionSpec,
        snapshot: DebugSessionSnapshot,
    ) -> DebugSessionControllerState:
        return DebugSessionControllerState(
            spec=spec,
            snapshot=snapshot,
            commands=command_matrix_for_session(snapshot),
        )

    def _spec(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
    ) -> DebugSessionSpec:
        descriptor = self._registry.descriptor(self._backend_kind)
        current = self._state.spec if hasattr(self, "_state") else None
        if project_path:
            project = Path(project_path).expanduser().resolve()
        else:
            project = current.project_path if current is not None else None
        target = str(target_name or (current.target_name if current is not None else ""))
        return DebugSessionSpec(
            backend=_session_backend(self._backend_kind),
            display_name=descriptor.display_name,
            project_path=project,
            target_name=target,
            source_provider=current.source_provider if current is not None else "",
            safety_policy=self._safety_policy,
            metadata=dict(current.metadata) if current is not None else {},
        )


def _coerce_backend_kind(value: DebugBackendKind | str) -> DebugBackendKind:
    if isinstance(value, DebugBackendKind):
        return value
    return DebugBackendKind(str(value))


def _session_backend(kind: DebugBackendKind) -> DebugSessionBackend:
    try:
        return DebugSessionBackend(kind.value)
    except ValueError:
        return DebugSessionBackend.NONE
