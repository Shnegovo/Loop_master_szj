"""Explicit Keil auto-debug smoke runner for the F401 variable probe.

Default mode is dry-run planning only. Add --execute to launch Keil/UVSOCK and
write a RAM variable in a real debug session.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.auto_debug import KeilAutoDebugRequest, run_keil_auto_debug_transaction  # noqa: E402
from src.core.keil.backend import KeilBackendConfig, KeilUvSockBackendAdapter  # noqa: E402
from src.core.keil.profile import make_keil_debug_profile  # noqa: E402
from src.core.keil.profile_store import load_keil_profile_store  # noqa: E402
from src.core.keil.presets import keil_live_write_seed, keil_variable_preset_profile  # noqa: E402


DEFAULT_PROJECT = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
DEFAULT_TARGET = "STM32F401CCU6 Variable Probe"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an explicit Keil auto-debug smoke transaction.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--use-profile", action="store_true", help="Use the default saved Keil debug profile.")
    parser.add_argument("--profile-store", default=str(ROOT / "loopmaster_keil_profiles.json"))
    parser.add_argument("--profile", default="", help="Saved profile name/key/project substring. Defaults to the store default.")
    parser.add_argument("--expression", default="")
    parser.add_argument("--write-value", default="")
    parser.add_argument("--wait-seconds", type=float, default=25.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--build-timeout", type=float, default=180.0)
    parser.add_argument("--no-build", action="store_true", help="Fail if AXF is missing instead of building.")
    parser.add_argument("--no-launch", action="store_true", help="Connect to an already running uVision instance.")
    parser.add_argument("--no-write", action="store_true", help="Stop after UVSOCK connection/status readback.")
    parser.add_argument("--execute", action="store_true", help="Actually start Keil/connect/write the target.")
    parser.add_argument("--json", action="store_true", help="Print JSON records.")
    args = parser.parse_args()

    profile_record = _select_saved_profile(args.profile_store, args.profile) if args.use_profile else None
    keil_root = Path(args.keil_root)
    port = int(args.port)
    project = Path(args.project).expanduser().resolve()
    target = args.target
    if profile_record is not None:
        keil_root = profile_record.keil_root or keil_root
        port = int(profile_record.uvsock_port)
        project = profile_record.project_path.expanduser().resolve()
        target = profile_record.target_name

    adapter = KeilUvSockBackendAdapter(
        KeilBackendConfig(root=keil_root, port=port, connection_name="LoopMasterAutoSmoke")
    )
    profile = make_keil_debug_profile(
        root=keil_root,
        project_path=project,
        target_name=target,
        port=port,
    )
    target = profile.target_name or target
    preset_profile = keil_variable_preset_profile(project, target)
    expression, write_value = keil_live_write_seed(preset_profile)
    if args.expression:
        expression = args.expression
    if args.write_value:
        write_value = args.write_value
    request = KeilAutoDebugRequest(
        project_path=project,
        target_name=target,
        build_if_missing=not args.no_build,
        launch_if_needed=not args.no_launch,
        wait_seconds=float(args.wait_seconds),
        poll_interval=float(args.poll_interval),
        write_smoke=not args.no_write,
        expression=expression,
        value_text=write_value,
        build_timeout=float(args.build_timeout),
        connection_name="LoopMasterAutoSmoke",
    )

    if not args.execute:
        record = {
            "mode": "dry_run",
            "profile_source": "saved" if profile_record is not None else "defaults_or_args",
            "profile_name": profile_record.display_name if profile_record is not None else "",
            "keil_root": str(keil_root),
            "port": port,
            "project": str(project),
            "target": target,
            "profile_ready": profile.ready,
            "profile_reasons": list(profile.reasons),
            "axf": str(profile.axf_path or ""),
            "axf_exists": profile.axf_exists,
            "build_command": profile.build_plan.display_command,
            "launch_command": profile.launch_plan.display_command,
            "write": None if args.no_write else {"expression": expression, "value": write_value},
            "diagnostics": [{"key": key, "value": value} for key, value in profile.diagnostic_rows()],
            "next": "Add --execute to start Keil/UVSOCK and modify the target RAM variable.",
        }
        _print_record(record, json_mode=args.json)
        print("PASS keil auto debug smoke dry-run")
        return 0

    result = run_keil_auto_debug_transaction(adapter, request)
    record = {
        "mode": "execute",
        "succeeded": result.succeeded,
        "summary": result.summary(),
        "steps": [
            {
                "key": step.key,
                "title": step.title,
                "attempted": step.attempted,
                "succeeded": step.succeeded,
                "detail": step.detail,
                "elapsed_ms": step.elapsed_ms,
            }
            for step in result.steps
        ],
        "diagnostics": [{"key": key, "value": value} for key, value in result.diagnostic_rows()],
    }
    if result.write is not None:
        record["write"] = result.write.to_record()
    _print_record(record, json_mode=args.json)
    if not result.succeeded:
        print(result.summary())
        return 1
    print(result.summary())
    print("PASS keil auto debug smoke execute")
    return 0


def _print_record(record: dict[str, object], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for key, value in record.items():
        if key == "diagnostics":
            print("diagnostics:")
            for item in value if isinstance(value, list) else []:
                print(f"  {item['key']}: {item['value']}")
        elif key == "steps":
            print("steps:")
            for item in value if isinstance(value, list) else []:
                print(
                    f"  {item['key']}: attempted={item['attempted']} "
                    f"succeeded={item['succeeded']} detail={item['detail']}"
                )
        elif isinstance(value, dict):
            print(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        else:
            print(f"{key}: {value}")


def _select_saved_profile(store_path: str, profile_selector: str):
    store = load_keil_profile_store(store_path)
    if not store.records:
        raise SystemExit(f"No saved Keil profiles found: {store_path}")
    selector = str(profile_selector or "").strip().lower()
    if not selector:
        record = store.default
        if record is None:
            raise SystemExit(f"No default Keil profile in: {store_path}")
        return record
    for record in store.records:
        haystack = "\n".join(
            (
                record.key,
                record.display_name,
                str(record.project_path),
                record.target_name,
            )
        ).lower()
        if selector in haystack:
            return record
    raise SystemExit(f"Saved Keil profile not found: {profile_selector}")


if __name__ == "__main__":
    raise SystemExit(main())
