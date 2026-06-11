"""Probe acquisition source descriptors and guard rails."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.acquisition_sources import (  # noqa: E402
    SCOPE_SOURCE_KEIL_WATCH,
    SCOPE_SOURCE_OPENOCD_GDB,
    SCOPE_SOURCE_PYOCD,
    SCOPE_SOURCE_SERIAL_WAVEFORM,
    SCOPE_SOURCE_SWD,
    SCOPE_SOURCE_TI_MSPM0,
    AcquisitionSourceMode,
    AcquisitionSourceState,
    acquisition_source_descriptor,
    acquisition_source_key_for_debug_backend,
    active_acquisition_source,
    acquisition_source_options,
    build_acquisition_source_catalog,
    debugger_backed_source_keys,
    normalize_acquisition_source_key,
    normalize_known_acquisition_source_key,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    catalog = build_acquisition_source_catalog(SCOPE_SOURCE_SWD)
    by_key = {source.key: source for source in catalog}
    expected = {
        SCOPE_SOURCE_SWD,
        SCOPE_SOURCE_KEIL_WATCH,
        SCOPE_SOURCE_SERIAL_WAVEFORM,
        SCOPE_SOURCE_OPENOCD_GDB,
        SCOPE_SOURCE_PYOCD,
        SCOPE_SOURCE_TI_MSPM0,
    }
    _assert(expected <= set(by_key), f"catalog missing keys: {set(by_key)!r}")

    swd = by_key[SCOPE_SOURCE_SWD]
    _assert(swd.active, f"SWD should be active by default: {swd!r}")
    _assert(swd.selectable and swd.implemented and swd.write_capable, f"SWD capability mismatch: {swd!r}")
    _assert(swd.mode == AcquisitionSourceMode.LIGHT_INTRUSIVE, f"SWD mode mismatch: {swd!r}")
    _assert(not swd.capabilities.owns_debug_session, f"SWD should not own debug session: {swd!r}")
    _assert("非/轻侵入式" in swd.detail, f"SWD detail should preserve user mode wording: {swd.detail!r}")

    keil = by_key[SCOPE_SOURCE_KEIL_WATCH]
    _assert(keil.state == AcquisitionSourceState.READY, f"Keil Watch should be ready: {keil!r}")
    _assert(keil.mode == AcquisitionSourceMode.DEBUGGER_BACKED, f"Keil mode mismatch: {keil!r}")
    _assert(keil.capabilities.breakpoint_control and keil.capabilities.runtime_control, f"Keil debug capabilities missing: {keil!r}")
    _assert(keil.capabilities.owns_debug_session and keil.capabilities.source_visual, f"Keil source/debug ownership mismatch: {keil!r}")
    _assert(keil.max_hz is not None and keil.max_hz <= 30, f"Keil Watch max Hz too optimistic: {keil!r}")

    serial = by_key[SCOPE_SOURCE_SERIAL_WAVEFORM]
    _assert(serial.state == AcquisitionSourceState.ROUTE_ONLY, f"serial should be route-only: {serial!r}")
    _assert(serial.selectable and serial.implemented and not serial.write_capable, f"serial capability mismatch: {serial!r}")
    _assert(serial.mode == AcquisitionSourceMode.SERIAL_STREAM, f"serial mode mismatch: {serial!r}")
    _assert(serial.capabilities.waveform_read and serial.capabilities.serial_txrx, f"serial waveform/txrx missing: {serial!r}")
    _assert(not serial.capabilities.variable_write and not serial.capabilities.owns_debug_session, f"serial should not own debugger: {serial!r}")
    _assert("串口助手" in serial.detail, f"serial detail missing route note: {serial.detail!r}")

    for key in (SCOPE_SOURCE_OPENOCD_GDB, SCOPE_SOURCE_PYOCD, SCOPE_SOURCE_TI_MSPM0):
        source = by_key[key]
        _assert(source.state == AcquisitionSourceState.PLANNED, f"{key} should be planned: {source!r}")
        _assert(not source.selectable and not source.implemented, f"{key} should not be selectable yet: {source!r}")
        _assert(source.mode == AcquisitionSourceMode.DEBUGGER_BACKED, f"{key} should be debugger-backed: {source!r}")
        _assert(source.capabilities.breakpoint_control and source.capabilities.runtime_control, f"{key} debug capabilities missing")
        _assert(source.capabilities.waveform_read and source.capabilities.variable_read, f"{key} waveform/read capabilities missing")
        _assert("未接入" in source.safety_note or "未接入" in source.detail, f"{key} guard note missing: {source!r}")

    active_keil = active_acquisition_source(SCOPE_SOURCE_KEIL_WATCH)
    _assert(active_keil.key == SCOPE_SOURCE_KEIL_WATCH and active_keil.active, f"active Keil mismatch: {active_keil!r}")
    _assert(normalize_acquisition_source_key("unknown") == SCOPE_SOURCE_SWD, "unknown source should fall back to SWD")
    _assert(normalize_acquisition_source_key(SCOPE_SOURCE_SERIAL_WAVEFORM) == SCOPE_SOURCE_SWD, "main scope normalizer should keep serial route-only")
    _assert(normalize_known_acquisition_source_key(SCOPE_SOURCE_SERIAL_WAVEFORM) == SCOPE_SOURCE_SERIAL_WAVEFORM, "known source normalizer should keep serial")
    _assert(acquisition_source_descriptor(SCOPE_SOURCE_SERIAL_WAVEFORM).key == SCOPE_SOURCE_SERIAL_WAVEFORM, "serial descriptor lookup mismatch")

    options = acquisition_source_options(SCOPE_SOURCE_SWD)
    option_map = {key: (label, note, enabled) for key, label, note, enabled in options}
    _assert(option_map[SCOPE_SOURCE_SWD][2], f"SWD option should be enabled: {option_map!r}")
    _assert(option_map[SCOPE_SOURCE_SERIAL_WAVEFORM][2], f"serial option should route to serial page: {option_map!r}")
    _assert(not option_map[SCOPE_SOURCE_TI_MSPM0][2], f"TI option should not be enabled without adapter: {option_map!r}")
    _assert("TI MSPM0G3507" in option_map[SCOPE_SOURCE_TI_MSPM0][0], "TI MSPM0G3507 label missing")

    diagnostics = dict(swd.diagnostic_rows())
    _assert(diagnostics.get("采集模式") == "非/轻侵入式", f"SWD diagnostics missing mode: {diagnostics!r}")
    _assert(diagnostics.get("调试接管") == "不接管调试链", f"SWD diagnostics should show no debug takeover: {diagnostics!r}")
    keil_diagnostics = dict(keil.diagnostic_rows())
    _assert(keil_diagnostics.get("调试接管") == "会接管调试链", f"Keil takeover diagnostics missing: {keil_diagnostics!r}")
    _assert("断点" in keil_diagnostics.get("调试能力", ""), f"Keil diagnostics missing breakpoint capability: {keil_diagnostics!r}")

    _assert(acquisition_source_key_for_debug_backend("keil") == SCOPE_SOURCE_KEIL_WATCH, "Keil backend source mapping mismatch")
    _assert(acquisition_source_key_for_debug_backend("openocd_gdb") == SCOPE_SOURCE_OPENOCD_GDB, "OpenOCD source mapping mismatch")
    _assert(acquisition_source_key_for_debug_backend("pyocd") == SCOPE_SOURCE_PYOCD, "pyOCD source mapping mismatch")
    _assert(acquisition_source_key_for_debug_backend("ti_mspm0") == SCOPE_SOURCE_TI_MSPM0, "TI source mapping mismatch")
    _assert(acquisition_source_key_for_debug_backend("offline") is None, "offline should not map to live acquisition source")
    _assert(
        debugger_backed_source_keys() == (
            SCOPE_SOURCE_KEIL_WATCH,
            SCOPE_SOURCE_OPENOCD_GDB,
            SCOPE_SOURCE_PYOCD,
            SCOPE_SOURCE_TI_MSPM0,
        ),
        "debugger-backed source ordering changed",
    )

    print("PASS acquisition sources probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
