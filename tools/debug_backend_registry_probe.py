"""Probe debugger backend registry wiring without connecting to hardware."""

from __future__ import annotations

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_backend_registry import (  # noqa: E402
    DebugBackendDescriptor,
    DebugBackendRegistry,
    create_default_debug_backend_registry,
)
from src.core.debug_workbench import DebugBackendKind  # noqa: E402
from src.core.keil.backend import KeilUvSockBackendAdapter  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class _FakeBackend:
    kind = DebugBackendKind.OFFLINE
    display_name = "Offline Fake"


def main() -> int:
    registry = create_default_debug_backend_registry(
        keil_root=Path("D:/Keil"),
        uvsock_port=4827,
    )
    _assert(registry.default_kind() == DebugBackendKind.KEIL, "default backend should remain Keil")
    _assert(registry.kinds() == (DebugBackendKind.KEIL,), f"default registry changed: {registry.kinds()!r}")
    descriptor = registry.descriptor("keil")
    _assert(descriptor.display_name == "Keil / UVSOCK", "Keil descriptor name mismatch")
    _assert(descriptor.read_only_first, "Keil must stay read-only-first")
    adapter = registry.create(DebugBackendKind.KEIL)
    _assert(isinstance(adapter, KeilUvSockBackendAdapter), f"adapter type mismatch: {type(adapter)!r}")
    _assert(adapter.config.root == Path("D:/Keil"), "Keil root should flow into adapter config")
    _assert(adapter.config.port == 4827, "UVSOCK port should flow into adapter config")

    placeholders = create_default_debug_backend_registry(
        keil_root=Path("D:/Keil"),
        uvsock_port=4827,
        include_placeholders=True,
    )
    _assert(
        placeholders.kinds()
        == (
            DebugBackendKind.KEIL,
            DebugBackendKind.OPENOCD_GDB,
            DebugBackendKind.PYOCD,
            DebugBackendKind.OFFLINE,
        ),
        f"placeholder registry changed: {placeholders.kinds()!r}",
    )
    for kind in (DebugBackendKind.OPENOCD_GDB, DebugBackendKind.PYOCD, DebugBackendKind.OFFLINE):
        backend = placeholders.create(kind)
        snapshot = backend.read_only_session_snapshot(
            project_path="D:/demo/demo.elf",
            target_name="demo",
            attempt_connection=True,
        )
        record = snapshot.to_record()
        json.dumps(record, ensure_ascii=False, sort_keys=True)
        _assert(snapshot.backend == kind, f"{kind.value} snapshot backend mismatch")
        _assert(snapshot.read_only, f"{kind.value} placeholder should stay read-only")
        _assert(snapshot.connection_attempted, f"{kind.value} should preserve attempted flag")
        _assert(not snapshot.connection_established, f"{kind.value} placeholder must not connect")
        _assert(not snapshot.status.capabilities.can_halt, f"{kind.value} placeholder must not allow halt")
        _assert(not snapshot.status.capabilities.can_run, f"{kind.value} placeholder must not allow run")
        _assert(not snapshot.status.capabilities.can_write_variables, f"{kind.value} placeholder must not allow writes")
        _assert("尚未接入" in " ".join(value for _, value in snapshot.diagnostic_rows()), f"{kind.value} diagnostics mismatch")

    custom = DebugBackendRegistry()
    custom.register(
        DebugBackendDescriptor(
            kind=DebugBackendKind.OFFLINE,
            display_name="Offline Fake",
            factory=_FakeBackend,
            notes="probe-only fake backend",
        )
    )
    _assert(custom.default_kind() == DebugBackendKind.OFFLINE, "single fake backend should become default")
    fake = custom.create("offline")
    _assert(fake.kind == DebugBackendKind.OFFLINE, "fake backend kind mismatch")
    _assert(fake.display_name == "Offline Fake", "fake backend display name mismatch")

    try:
        custom.create("keil")
    except KeyError:
        pass
    else:
        raise AssertionError("unregistered backend lookup should fail")

    print("PASS debug backend registry probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
