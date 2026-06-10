"""Probe Keil .uvoptx debug option parsing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.options import parse_keil_debug_options  # noqa: E402
from src.core.keil.profile import make_keil_debug_profile  # noqa: E402


BALANCE_PROJECT = Path(
    r"D:\学习资料\平衡车\平衡车入门教程资料\程序源码\平衡车程序"
    r"\00-平衡车测试程序\平衡车测试程序-V1.0\Project.uvprojx"
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    f401_project = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
    f401 = parse_keil_debug_options(f401_project, "STM32F401CCU6 Variable Probe")
    _assert(f401.exists, "F401 uvoptx should exist")
    _assert(f401.device == "STM32F401CCUx", f"F401 device mismatch: {f401.device!r}")
    _assert(f401.adapter_label == "ST-Link", f"F401 adapter mismatch: {f401.adapter_label!r}")
    _assert(f401.protocol_label == "SWD", f"F401 protocol mismatch: {f401.protocol_label!r}")
    _assert(f401.debug_clock_hz == 10_000_000, f"F401 debug clock mismatch: {f401.debug_clock_hz!r}")
    _assert(f401.flash_algorithm == "STM32F4xx_256.FLM", f"F401 flash algorithm mismatch: {f401.flash_algorithm!r}")
    _assert(f401.flash_start == 0x08000000 and f401.flash_length == 0x040000, f"F401 flash range mismatch: {f401.flash_range_label}")
    rows = dict(f401.diagnostic_rows())
    _assert(rows.get("调试器") == "ST-Link", f"F401 diagnostics mismatch: {rows!r}")
    _assert(rows.get("调试接口") == "SWD", f"F401 diagnostics mismatch: {rows!r}")

    profile = make_keil_debug_profile(
        root=Path("D:/Keil"),
        project_path=f401_project,
        target_name="STM32F401CCU6 Variable Probe",
        port=4827,
    )
    profile_rows = dict(profile.diagnostic_rows())
    _assert(profile_rows.get("调试器") == "ST-Link", f"profile debug diagnostics missing: {profile_rows!r}")
    _assert(profile_rows.get("Flash 算法") == "STM32F4xx_256.FLM", f"profile flash diagnostics missing: {profile_rows!r}")

    if BALANCE_PROJECT.exists():
        balance = parse_keil_debug_options(BALANCE_PROJECT, "Target 1")
        _assert(balance.exists, "balance uvoptx should exist")
        _assert(balance.device == "STM32F103C8", f"balance device mismatch: {balance.device!r}")
        _assert(balance.adapter_label == "ST-Link", f"balance adapter mismatch: {balance.adapter_label!r}")
        _assert(balance.protocol_label == "SWD", f"balance protocol mismatch: {balance.protocol_label!r}")
        _assert(balance.debug_clock_hz == 10_000_000, f"balance debug clock mismatch: {balance.debug_clock_hz!r}")
        _assert(balance.flash_algorithm == "STM32F10x_128.FLM", f"balance flash algorithm mismatch: {balance.flash_algorithm!r}")
        _assert(balance.flash_start == 0x08000000 and balance.flash_length == 0x020000, f"balance flash range mismatch: {balance.flash_range_label}")
        warning_text = "；".join(balance.warnings)
        _assert("STM32F103C8" in warning_text and "128KB" in warning_text, f"balance warning mismatch: {warning_text!r}")
        balance_rows = dict(balance.diagnostic_rows())
        _assert("InvalidFlash" in balance_rows.get("Flash 配置", ""), f"balance invalid flash diagnostic missing: {balance_rows!r}")
        _assert("128KB" in balance_rows.get("调试配置警告", ""), f"balance warning diagnostic missing: {balance_rows!r}")

    missing = parse_keil_debug_options(ROOT / "missing.uvprojx", "")
    _assert(not missing.exists, "missing uvoptx should not exist")
    _assert(dict(missing.diagnostic_rows()).get("调试配置") == "未找到 .uvoptx", "missing diagnostics mismatch")

    print("PASS keil debug options probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
