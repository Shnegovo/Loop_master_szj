"""Probe debugger backend registry wiring without connecting to hardware."""

from __future__ import annotations

import sys
import json
from dataclasses import fields, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_backend_registry import (  # noqa: E402
    DebugBackendDescriptor,
    DebugBackendRegistry,
    create_default_debug_backend_registry,
)
from src.core.debug_backend import (  # noqa: E402
    DebugBackendWorkerLifecycleRegistration,
    DebugBackendWorkerState,
)
from src.core.debug_workbench import DebugBackendKind  # noqa: E402
from src.core.keil.backend import KeilUvSockBackendAdapter  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_data_only(value: object, path: str = "lifecycle") -> None:
    _assert(not callable(value), f"{path} must not be callable")
    forbidden = {
        "callback",
        "dll_handle",
        "executor",
        "handle",
        "library",
        "pid",
        "popen",
        "process",
        "process_handle",
        "subprocess",
        "thread",
        "thread_handle",
        "transport_handle",
        "usb_handle",
    }
    if is_dataclass(value):
        for field in fields(value):
            lower = field.name.lower()
            _assert(lower not in forbidden, f"{path}.{field.name} is forbidden")
            _assert_data_only(getattr(value, field.name), f"{path}.{field.name}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            lower = str(key).lower()
            _assert(lower not in forbidden, f"{path}.{key} is forbidden")
            _assert_data_only(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _assert_data_only(item, f"{path}[{index}]")


class _FakeBackend:
    kind = DebugBackendKind.OFFLINE
    display_name = "Offline Fake"


def _assert_safe_lifecycle(
    lifecycle: DebugBackendWorkerLifecycleRegistration,
    *,
    worker_key: str,
) -> None:
    _assert(lifecycle.worker_key == worker_key, f"{worker_key} lifecycle key mismatch")
    _assert(lifecycle.state == DebugBackendWorkerState.REGISTERED, f"{worker_key} should only be registered")
    _assert(not lifecycle.autostart, f"{worker_key} must not autostart")
    _assert(lifecycle.read_only_first, f"{worker_key} must stay read-only-first")
    _assert(not lifecycle.may_start_process, f"{worker_key} must not start processes by default")
    _assert(not lifecycle.may_connect_probe, f"{worker_key} must not connect probes by default")
    _assert(not lifecycle.may_write_target, f"{worker_key} must not write target by default")
    json.dumps(lifecycle.to_record(), ensure_ascii=False, sort_keys=True)
    _assert_data_only(lifecycle)
    _assert_data_only(lifecycle.to_record())


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
    _assert_safe_lifecycle(registry.lifecycle("keil"), worker_key="keil_uvsock")
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
    expected_workers = {
        DebugBackendKind.KEIL: "keil_uvsock",
        DebugBackendKind.OPENOCD_GDB: "openocd_gdb",
        DebugBackendKind.PYOCD: "pyocd",
        DebugBackendKind.OFFLINE: "offline",
    }
    for kind, worker_key in expected_workers.items():
        _assert_safe_lifecycle(placeholders.lifecycle(kind), worker_key=worker_key)
    lifecycle_keys = tuple(item.worker_key for item in placeholders.lifecycles())
    _assert(lifecycle_keys == ("keil_uvsock", "openocd_gdb", "pyocd", "offline"), f"lifecycle order changed: {lifecycle_keys!r}")
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
    factory_calls = 0

    def counted_factory() -> _FakeBackend:
        nonlocal factory_calls
        factory_calls += 1
        return _FakeBackend()

    custom.register(
        DebugBackendDescriptor(
            kind=DebugBackendKind.OFFLINE,
            display_name="Offline Fake",
            factory=counted_factory,
            notes="probe-only fake backend",
        )
    )
    _assert(custom.default_kind() == DebugBackendKind.OFFLINE, "single fake backend should become default")
    _assert(tuple(item.kind for item in custom.descriptors()) == (DebugBackendKind.OFFLINE,), "descriptor listing changed")
    _assert(factory_calls == 0, "descriptor listing must not call backend factory")
    _assert(custom.descriptor("offline").display_name == "Offline Fake", "descriptor lookup changed")
    _assert(factory_calls == 0, "descriptor lookup must not call backend factory")
    _assert_safe_lifecycle(custom.lifecycle("offline"), worker_key="offline")
    _assert(factory_calls == 0, "lifecycle lookup must not call backend factory")
    fake = custom.create("offline")
    _assert(fake.kind == DebugBackendKind.OFFLINE, "fake backend kind mismatch")
    _assert(fake.display_name == "Offline Fake", "fake backend display name mismatch")
    _assert(factory_calls == 1, "create should call backend factory exactly once")

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
