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
    AcquisitionSourceState,
    active_acquisition_source,
    acquisition_source_options,
    build_acquisition_source_catalog,
    normalize_acquisition_source_key,
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
    _assert("非/轻侵入式" in swd.detail, f"SWD detail should preserve user mode wording: {swd.detail!r}")

    keil = by_key[SCOPE_SOURCE_KEIL_WATCH]
    _assert(keil.state == AcquisitionSourceState.READY, f"Keil Watch should be ready: {keil!r}")
    _assert(keil.max_hz is not None and keil.max_hz <= 30, f"Keil Watch max Hz too optimistic: {keil!r}")

    serial = by_key[SCOPE_SOURCE_SERIAL_WAVEFORM]
    _assert(serial.state == AcquisitionSourceState.ROUTE_ONLY, f"serial should be route-only: {serial!r}")
    _assert(serial.selectable and serial.implemented and not serial.write_capable, f"serial capability mismatch: {serial!r}")
    _assert("串口助手" in serial.detail, f"serial detail missing route note: {serial.detail!r}")

    for key in (SCOPE_SOURCE_OPENOCD_GDB, SCOPE_SOURCE_PYOCD, SCOPE_SOURCE_TI_MSPM0):
        source = by_key[key]
        _assert(source.state == AcquisitionSourceState.PLANNED, f"{key} should be planned: {source!r}")
        _assert(not source.selectable and not source.implemented, f"{key} should not be selectable yet: {source!r}")
        _assert("未接入" in source.safety_note or "未接入" in source.detail, f"{key} guard note missing: {source!r}")

    active_keil = active_acquisition_source(SCOPE_SOURCE_KEIL_WATCH)
    _assert(active_keil.key == SCOPE_SOURCE_KEIL_WATCH and active_keil.active, f"active Keil mismatch: {active_keil!r}")
    _assert(normalize_acquisition_source_key("unknown") == SCOPE_SOURCE_SWD, "unknown source should fall back to SWD")

    options = acquisition_source_options(SCOPE_SOURCE_SWD)
    option_map = {key: (label, note, enabled) for key, label, note, enabled in options}
    _assert(option_map[SCOPE_SOURCE_SWD][2], f"SWD option should be enabled: {option_map!r}")
    _assert(option_map[SCOPE_SOURCE_SERIAL_WAVEFORM][2], f"serial option should route to serial page: {option_map!r}")
    _assert(not option_map[SCOPE_SOURCE_TI_MSPM0][2], f"TI option should not be enabled without adapter: {option_map!r}")
    _assert("TI MSPM0G3507" in option_map[SCOPE_SOURCE_TI_MSPM0][0], "TI MSPM0G3507 label missing")

    print("PASS acquisition sources probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
