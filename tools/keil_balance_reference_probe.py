"""Read-only probe for the user-provided balance-car Keil reference project."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.options import parse_keil_debug_options  # noqa: E402
from src.core.keil.presets import keil_live_write_seed, keil_variable_preset_profile  # noqa: E402
from src.core.keil.profile import make_keil_debug_profile  # noqa: E402
from src.core.keil.project import parse_keil_project  # noqa: E402


DEFAULT_PROJECT = Path(
    r"D:\学习资料\平衡车\平衡车入门教程资料\程序源码\平衡车程序"
    r"\00-平衡车测试程序\平衡车测试程序-V1.0\Project.uvprojx"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the balance-car Keil reference project without hardware access.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--target", default="Target 1")
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project_path = Path(args.project).expanduser().resolve()
    if not project_path.exists():
        raise SystemExit(f"Project does not exist: {project_path}")

    project = parse_keil_project(project_path)
    profile = make_keil_debug_profile(
        root=Path(args.keil_root),
        project_path=project_path,
        target_name=args.target,
        port=int(args.port),
    )
    options = parse_keil_debug_options(project_path, profile.target_name or args.target)
    presets = keil_variable_preset_profile(project_path, profile.target_name or args.target)
    expression, value = keil_live_write_seed(presets)

    record = {
        "project": str(project.path),
        "targets": [
            {
                "name": target.name,
                "files": len(target.files),
                "sources": len(target.source_files),
                "headers": len(target.header_files),
                "output": str(target.output_path or ""),
            }
            for target in project.targets
        ],
        "selected_target": profile.target_name,
        "profile_ready": profile.ready,
        "axf": str(profile.axf_path or ""),
        "axf_exists": profile.axf_exists,
        "build_command": profile.build_plan.display_command,
        "launch_command": profile.launch_plan.display_command,
        "device": options.device,
        "adapter": options.adapter_label,
        "protocol": options.protocol_label,
        "debug_clock": options.debug_clock_label,
        "flash_algorithm": options.flash_algorithm,
        "flash_range": options.flash_range_label,
        "ram_range": options.ram_range_label,
        "warnings": list(options.warnings),
        "preset_key": presets.key,
        "preset_name": presets.display_name,
        "default_write": {"expression": expression, "value": value},
        "write_presets": [
            {
                "expression": item.expression,
                "label": item.label,
                "type": item.value_type,
                "default": item.default_value,
                "range": item.range_hint,
                "write_allowed": item.write_allowed,
            }
            for item in presets.write_presets
        ],
        "scope_presets": [
            {
                "expression": item.expression,
                "label": item.label,
                "type": item.value_type,
                "purpose": item.purpose,
            }
            for item in presets.scope_presets
        ],
        "notes": list(presets.notes),
    }

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Project: {record['project']}")
        print(f"Target: {record['selected_target']}")
        print(f"Device: {record['device']} via {record['adapter']} {record['protocol']}")
        print(f"AXF: {record['axf']} exists={record['axf_exists']}")
        print(f"Default write: {expression}={value}")
        print("Scope presets: " + ", ".join(item["expression"] for item in record["scope_presets"][:12]))
        if record["warnings"]:
            print("Warnings: " + "；".join(record["warnings"]))

    if record["selected_target"] != "Target 1":
        raise AssertionError(f"Unexpected target: {record['selected_target']}")
    if record["device"] != "STM32F103C8":
        raise AssertionError(f"Unexpected device: {record['device']}")
    if record["preset_key"] != "balance_car_f103":
        raise AssertionError(f"Unexpected preset profile: {record['preset_key']}")
    if expression != "SpeedLevel" or value != "5":
        raise AssertionError(f"Unexpected default write: {expression}={value}")
    if "Angle" not in {item["expression"] for item in record["scope_presets"]}:
        raise AssertionError("Angle scope preset missing")
    if "AveSpeed" not in {item["expression"] for item in record["scope_presets"]}:
        raise AssertionError("AveSpeed scope preset missing")

    print("PASS keil balance reference probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
