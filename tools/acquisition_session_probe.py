"""Probe backend-neutral acquisition session contracts."""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.acquisition_session import (  # noqa: E402
    AcquisitionSessionState,
    acquisition_batch_from_rows,
    acquisition_session_contract,
    normalise_sample_values,
)
from src.core.acquisition_sources import (  # noqa: E402
    SCOPE_SOURCE_KEIL_WATCH,
    SCOPE_SOURCE_SERIAL_WAVEFORM,
    SCOPE_SOURCE_SWD,
    acquisition_source_descriptor,
    normalize_known_acquisition_source_key,
)
from src.core.models import BaseType  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    float_type = BaseType("float", 4, "float")
    int_type = BaseType("int32_t", 4, "signed")
    variables = (
        ("speed", 0x20000000, float_type),
        ("target", 0x20000004, int_type),
        ("output", 0x20000008, float_type),
    )

    swd = acquisition_session_contract(
        SCOPE_SOURCE_SWD,
        state=AcquisitionSessionState.RUNNING,
        sample_rate_hz=500,
        variable_count=len(variables),
        detail="probe",
    )
    swd_rows = dict(swd.diagnostic_rows())
    _assert(swd.source_key == SCOPE_SOURCE_SWD, f"SWD contract source mismatch: {swd!r}")
    _assert(swd_rows.get("采集会话") == "采集中", f"SWD state diagnostics mismatch: {swd_rows!r}")
    _assert(swd_rows.get("采集模式") == "非/轻侵入式", f"SWD mode diagnostics mismatch: {swd_rows!r}")
    _assert(swd_rows.get("调试接管") == "不接管调试链", f"SWD takeover diagnostics mismatch: {swd_rows!r}")
    _assert(swd.to_record()["sample_rate_hz"] == 500.0, f"SWD record rate mismatch: {swd.to_record()!r}")

    keil = acquisition_session_contract(SCOPE_SOURCE_KEIL_WATCH, state=AcquisitionSessionState.READY)
    keil_rows = dict(keil.diagnostic_rows())
    _assert(keil_rows.get("采集模式") == "调试器链路", f"Keil mode diagnostics mismatch: {keil_rows!r}")
    _assert(keil_rows.get("调试接管") == "会接管调试链", f"Keil takeover diagnostics mismatch: {keil_rows!r}")

    serial = acquisition_source_descriptor(SCOPE_SOURCE_SERIAL_WAVEFORM)
    _assert(serial.key == SCOPE_SOURCE_SERIAL_WAVEFORM, f"serial descriptor should stay serial: {serial!r}")
    _assert(normalize_known_acquisition_source_key(SCOPE_SOURCE_SERIAL_WAVEFORM) == SCOPE_SOURCE_SERIAL_WAVEFORM, "serial known-key normalize mismatch")
    serial_contract = acquisition_session_contract(SCOPE_SOURCE_SERIAL_WAVEFORM)
    serial_rows = dict(serial_contract.diagnostic_rows())
    _assert(serial_contract.source_key == SCOPE_SOURCE_SERIAL_WAVEFORM, f"serial contract source mismatch: {serial_contract!r}")
    _assert(serial_rows.get("采集模式") == "主动上报流", f"serial mode diagnostics mismatch: {serial_rows!r}")
    _assert(serial_rows.get("变量读取") == "不按变量名读取", f"serial read diagnostics mismatch: {serial_rows!r}")

    dict_values = normalise_sample_values(variables, {"speed": "12.5", "target": 60})
    _assert(dict_values["speed"] == 12.5 and dict_values["target"] == 60.0, f"dict normalise mismatch: {dict_values!r}")
    _assert(math.isnan(dict_values["output"]), f"missing dict value should be NaN: {dict_values!r}")

    tuple_values = normalise_sample_values(variables, (1, "2.5", "bad"))
    _assert(tuple_values["speed"] == 1.0 and tuple_values["target"] == 2.5, f"tuple normalise mismatch: {tuple_values!r}")
    _assert(math.isnan(tuple_values["output"]), f"bad tuple value should be NaN: {tuple_values!r}")

    batch = acquisition_batch_from_rows(
        SCOPE_SOURCE_SERIAL_WAVEFORM,
        variables,
        (
            {"speed": 58.0, "target": 60, "output": 0.2},
            (59.0, 60, 0.4),
            {"speed": 60.5, "target": 60},
        ),
        start_time_s=10.0,
        sample_interval_s=0.01,
        sequence_start=100,
    )
    _assert(batch.source_key == SCOPE_SOURCE_SERIAL_WAVEFORM, f"batch source mismatch: {batch!r}")
    _assert(batch.sample_count == 3, f"batch count mismatch: {batch.sample_count}")
    _assert(batch.samples[0].sequence == 100 and batch.samples[-1].sequence == 102, f"batch sequence mismatch: {batch.samples!r}")
    _assert(abs(batch.actual_rate_hz - 100.0) < 0.001, f"batch rate mismatch: {batch.actual_rate_hz}")
    series = batch.series()
    _assert(series["speed"][0] == (10.0, 10.01, 10.02), f"series timestamps mismatch: {series!r}")
    _assert(series["target"][1] == (60.0, 60.0, 60.0), f"series values mismatch: {series!r}")
    _assert(math.isnan(series["output"][1][-1]), f"missing batch value should remain NaN: {series!r}")
    _assert(batch.to_record()["sample_count"] == 3, f"batch record mismatch: {batch.to_record()!r}")

    _assert(normalize_known_acquisition_source_key("unknown") == SCOPE_SOURCE_SWD, "unknown known-key should fall back to SWD")

    print("PASS acquisition session probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
