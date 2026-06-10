"""Probe Keil debug profile planning without launching or building."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.profile import make_keil_debug_profile  # noqa: E402


BALANCE_PROJECT = Path(
    r"D:\学习资料\平衡车\平衡车入门教程资料\程序源码\平衡车程序"
    r"\00-平衡车测试程序\平衡车测试程序-V1.0\Project.uvprojx"
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _check_profile(project: Path, target_name: str, expected_axf_name: str) -> None:
    profile = make_keil_debug_profile(
        root=Path("D:/Keil"),
        project_path=project,
        target_name=target_name,
        port=4827,
    )
    _assert(profile.discovery.installed, "Keil discovery should find local installation")
    _assert(profile.project_path == project.resolve(), f"project path mismatch: {profile.project_path}")
    _assert(profile.target is not None, f"target not selected: {target_name}")
    _assert(profile.target_name == target_name or not target_name, f"target name mismatch: {profile.target_name}")
    _assert(profile.axf_path is not None and profile.axf_path.name == expected_axf_name, f"AXF mismatch: {profile.axf_path}")
    _assert(profile.build_plan.command, "build command missing")
    _assert(profile.build_plan.ready, f"build plan should be ready: {profile.build_plan.reasons}")
    _assert("-b" in profile.build_plan.command, f"build command should use -b: {profile.build_plan.command!r}")
    _assert("-j0" in profile.build_plan.command, f"build command should use -j0: {profile.build_plan.command!r}")
    _assert(profile.launch_plan.command, "launch command missing")
    _assert(profile.launch_plan.ready, f"launch plan should be ready: {profile.launch_plan.reasons}")
    _assert("-s" in profile.launch_plan.command, f"launch command should enable UVSOCK: {profile.launch_plan.command!r}")
    rows = dict(profile.diagnostic_rows())
    _assert(rows.get("Keil 档案") == "可用", f"profile diagnostic should be ready: {rows!r}")
    _assert(rows.get("工程") == str(project.resolve()), f"diagnostic project mismatch: {rows!r}")
    _assert(rows.get("AXF") == str(profile.axf_path), f"diagnostic AXF mismatch: {rows!r}")
    _assert(rows.get("构建状态") == "可构建", f"build diagnostic mismatch: {rows!r}")
    _assert(rows.get("启动状态") == "可启动", f"launch diagnostic mismatch: {rows!r}")


def main() -> int:
    f401_project = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
    _check_profile(f401_project, "STM32F401CCU6 Variable Probe", "f401_variable_probe.axf")

    if BALANCE_PROJECT.exists():
        profile = make_keil_debug_profile(
            root=Path("D:/Keil"),
            project_path=BALANCE_PROJECT,
            target_name="Target 1",
            port=4827,
        )
        _assert(profile.target is not None and profile.target_name == "Target 1", "balance target mismatch")
        _assert(profile.axf_path is not None and profile.axf_path.name == "Project.axf", f"balance AXF mismatch: {profile.axf_path}")
        _assert(not profile.axf_exists, "balance AXF should currently be missing in the reference folder")
        _assert(profile.build_plan.ready, f"balance build plan should be ready: {profile.build_plan.reasons}")
        rows = dict(profile.diagnostic_rows())
        _assert(rows.get("Keil 档案") == "可用", f"balance profile diagnostic mismatch: {rows!r}")
        _assert(rows.get("构建状态") == "可构建", f"balance build diagnostic mismatch: {rows!r}")
        _assert(rows.get("启动状态") == "可启动", f"balance launch diagnostic mismatch: {rows!r}")
        _assert(rows.get("AXF 状态") == "未生成", f"balance AXF diagnostic mismatch: {rows!r}")

    missing = make_keil_debug_profile(root=Path("D:/Keil"), project_path=None, port=4827)
    _assert(not missing.ready, "profile without project must not be ready")
    _assert(not missing.build_plan.ready, "build plan without project must not be ready")
    _assert(any("No Keil project" in reason for reason in missing.reasons), f"missing project reason mismatch: {missing.reasons}")
    rows = dict(missing.diagnostic_rows())
    _assert(rows.get("Keil 档案") == "需要配置", f"missing profile diagnostic mismatch: {rows!r}")
    _assert(rows.get("构建状态") != "可构建", f"missing build diagnostic mismatch: {rows!r}")
    _assert(rows.get("启动状态") != "可启动", f"missing launch diagnostic mismatch: {rows!r}")

    print("PASS keil debug profile probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
