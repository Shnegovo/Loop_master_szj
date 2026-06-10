"""Probe Keil project variable preset detection without touching hardware."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.presets import (  # noqa: E402
    keil_live_write_prompt_hint,
    keil_live_write_seed,
    keil_variable_preset_profile,
)


BALANCE_PROJECT = Path(
    r"D:\学习资料\平衡车\平衡车入门教程资料\程序源码\平衡车程序"
    r"\00-平衡车测试程序\平衡车测试程序-V1.0\Project.uvprojx"
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _expressions(presets) -> set[str]:
    return {preset.expression for preset in presets}


def main() -> int:
    f401_project = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
    f401 = keil_variable_preset_profile(f401_project, "STM32F401CCU6 Variable Probe")
    _assert(f401.key == "f401_variable_probe", f"F401 profile mismatch: {f401!r}")
    _assert(keil_live_write_seed(f401) == ("debug_setpoint", "6000"), "F401 default write seed mismatch")
    _assert({"debug_setpoint", "debug_gain"}.issubset(_expressions(f401.write_presets)), "F401 write presets missing")
    _assert({"debug_feedback", "debug_output"}.issubset(_expressions(f401.scope_presets)), "F401 scope presets missing")
    _assert("debug_setpoint" in keil_live_write_prompt_hint(f401), "F401 prompt should mention debug_setpoint")

    if BALANCE_PROJECT.exists():
        balance = keil_variable_preset_profile(BALANCE_PROJECT, "Target 1")
        _assert(balance.key == "balance_car_f103", f"balance profile mismatch: {balance!r}")
        _assert(keil_live_write_seed(balance) == ("SpeedLevel", "5"), "balance default write seed mismatch")
        _assert(
            {"SpeedLevel", "AngleAcc_Offset", "AnglePID.Kp", "AnglePID.Kd"}.issubset(
                _expressions(balance.write_presets)
            ),
            f"balance write presets missing: {_expressions(balance.write_presets)!r}",
        )
        _assert(
            {"Angle", "AveSpeed", "PWML", "PWMR"}.issubset(_expressions(balance.scope_presets)),
            f"balance scope presets missing: {_expressions(balance.scope_presets)!r}",
        )
        rows = dict(balance.diagnostic_rows())
        _assert(rows.get("变量预设") == "平衡车 F103 调参工程", f"balance diagnostics mismatch: {rows!r}")
        _assert("SpeedLevel" in rows.get("推荐写入", ""), f"balance write diagnostic mismatch: {rows!r}")
        _assert("AveSpeed" in rows.get("推荐示波", ""), f"balance scope diagnostic mismatch: {rows!r}")
        prompt = keil_live_write_prompt_hint(balance)
        _assert("SpeedLevel" in prompt and "推荐示波" in prompt, f"balance prompt mismatch: {prompt!r}")

    unknown = keil_variable_preset_profile(ROOT / "missing.uvprojx", "")
    _assert(unknown.key == "unknown", f"unknown profile mismatch: {unknown!r}")
    _assert(keil_live_write_seed(unknown) == ("debug_setpoint", "6000"), "unknown fallback seed mismatch")
    _assert(unknown.diagnostic_rows() == (), f"unknown diagnostics should be empty: {unknown.diagnostic_rows()!r}")

    print("PASS keil variable presets probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
