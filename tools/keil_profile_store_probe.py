"""Probe persistent Keil debug profile records without touching hardware."""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.profile import make_keil_debug_profile  # noqa: E402
from src.core.keil.profile_store import (  # noqa: E402
    KeilDebugProfileRecord,
    KeilDebugProfileStore,
    load_keil_profile_store,
    metadata_from_debug_profile,
    profile_record_from_debug_profile,
    save_keil_profile_store,
)


BALANCE_PROJECT = Path(
    r"D:\学习资料\平衡车\平衡车入门教程资料\程序源码\平衡车程序"
    r"\00-平衡车测试程序\平衡车测试程序-V1.0\Project.uvprojx"
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    f401_project = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
    profile = make_keil_debug_profile(
        root=Path("D:/Keil"),
        project_path=f401_project,
        target_name="STM32F401CCU6 Variable Probe",
        port=4827,
    )
    record = profile_record_from_debug_profile(profile)
    _assert(record.project_path == f401_project.resolve(), f"project mismatch: {record.project_path}")
    _assert(record.target_name == "STM32F401CCU6 Variable Probe", f"target mismatch: {record.target_name}")
    _assert(record.uvsock_port == 4827, f"port mismatch: {record.uvsock_port}")
    _assert("F401VariableProbe" in record.display_name, f"display mismatch: {record.display_name}")
    _assert(record.metadata.device.startswith("STM32F401"), f"F401 metadata device mismatch: {record.metadata}")
    _assert(record.metadata.runtime_suitability == "connected_f401_smoke", f"F401 suitability mismatch: {record.metadata}")
    _assert(record.metadata.default_write_expression == "debug_setpoint", f"F401 default write mismatch: {record.metadata}")

    store = KeilDebugProfileStore().upsert(record)
    _assert(store.default is not None and store.default.key == record.key, "default record mismatch")
    updated = KeilDebugProfileRecord(
        name="F401 smoke",
        project_path=f401_project,
        target_name="STM32F401CCU6 Variable Probe",
        keil_root=Path("D:/Keil"),
        uvsock_port=4828,
    )
    store = store.upsert(updated)
    _assert(len(store.records) == 1, f"upsert should replace same key: {len(store.records)}")
    _assert(store.default is not None and store.default.uvsock_port == 4828, "updated port not persisted")

    if BALANCE_PROJECT.exists():
        balance = KeilDebugProfileRecord(
            name="平衡车 F103",
            project_path=BALANCE_PROJECT,
            target_name="Target 1",
            keil_root=Path("D:/Keil"),
            uvsock_port=4827,
        )
        balance_metadata = metadata_from_debug_profile(balance.to_profile())
        balance = KeilDebugProfileRecord(
            name=balance.name,
            project_path=balance.project_path,
            target_name=balance.target_name,
            keil_root=balance.keil_root,
            uvsock_port=balance.uvsock_port,
            metadata=balance_metadata,
        )
        store = store.upsert(balance)
        _assert(store.default is not None and store.default.name == "平衡车 F103", "balance should become default")
        _assert(store.default.metadata.runtime_suitability == "reference_only_f103", f"balance suitability mismatch: {store.default.metadata}")
        _assert("AnglePID.Kp" in store.default.metadata.write_presets, f"balance write presets mismatch: {store.default.metadata}")
        _assert("AveSpeed" in store.default.metadata.scope_presets, f"balance scope presets mismatch: {store.default.metadata}")
        balance_profile = store.default.to_profile()
        rows = dict(balance_profile.diagnostic_rows())
        _assert(rows.get("Target") == "Target 1", f"balance target diagnostics mismatch: {rows!r}")
        _assert("STM32F103" in rows.get("芯片", ""), f"balance device diagnostics mismatch: {rows!r}")

    with tempfile.TemporaryDirectory(prefix="loopmaster-keil-profiles-") as tmp:
        path = Path(tmp) / "profiles.json"
        _assert(save_keil_profile_store(path, store), "save profile store failed")
        loaded = load_keil_profile_store(path)
        _assert(len(loaded.records) == len(store.records), "loaded record count mismatch")
        _assert(loaded.default is not None, "loaded default missing")
        _assert(loaded.default.key == store.default.key, "loaded default key mismatch")
        _assert(loaded.default.metadata.runtime_suitability == store.default.metadata.runtime_suitability, "loaded metadata mismatch")

    print("PASS Keil profile store probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
