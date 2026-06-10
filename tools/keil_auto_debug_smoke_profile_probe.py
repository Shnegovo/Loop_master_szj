"""Probe profile-driven Keil auto-debug smoke dry-run."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.profile_store import (  # noqa: E402
    KeilDebugProfileRecord,
    KeilDebugProfileStore,
    save_keil_profile_store,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    project = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
    with tempfile.TemporaryDirectory(prefix="loopmaster-smoke-profile-") as tmp:
        store_path = Path(tmp) / "profiles.json"
        store = KeilDebugProfileStore().upsert(
            KeilDebugProfileRecord(
                name="probe profile",
                project_path=project,
                target_name="STM32F401CCU6 Variable Probe",
                keil_root=Path("D:/Keil"),
                uvsock_port=4933,
            )
        )
        _assert(save_keil_profile_store(store_path, store), "failed to save profile store")
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "keil_auto_debug_smoke.py"),
                "--use-profile",
                "--profile-store",
                str(store_path),
                "--json",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=45,
        )
    _assert(result.returncode == 0, f"smoke dry-run failed: {result.stdout}\n{result.stderr}")
    marker = "\nPASS keil auto debug smoke dry-run"
    payload_text = result.stdout.split(marker, 1)[0].strip()
    record = json.loads(payload_text)
    _assert(record["profile_source"] == "saved", f"profile source mismatch: {record!r}")
    _assert(record["profile_name"] == "probe profile", f"profile name mismatch: {record!r}")
    _assert(record["port"] == 4933, f"profile port mismatch: {record!r}")
    _assert(record["target"] == "STM32F401CCU6 Variable Probe", f"profile target mismatch: {record!r}")
    _assert("F401VariableProbe.uvprojx" in record["project"], f"profile project mismatch: {record!r}")

    print("PASS Keil auto-debug smoke profile probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
