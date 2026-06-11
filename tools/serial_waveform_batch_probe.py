"""Probe serial waveform conversion into acquisition batches."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.acquisition_sources import SCOPE_SOURCE_SERIAL_WAVEFORM  # noqa: E402
from src.core.decoders.serial import SerialProtocolParser  # noqa: E402
from src.core.serial_waveform_batch import (  # noqa: E402
    serial_samples_to_acquisition_batch,
    serial_waveform_batch_from_series,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = SerialProtocolParser(
        "firewater",
        ["speed.target", "speed.feedback", "pid.output"],
    )
    _logs, rows = parser.feed(b"d:60,59.4,12.5\nspeed.target=60,speed.feedback=60.7,pid.output=8.25\n")
    row_batch = serial_samples_to_acquisition_batch(
        rows,
        start_time_s=2.0,
        sample_interval_s=0.01,
        sequence_start=40,
    )
    _assert(row_batch.source_key == SCOPE_SOURCE_SERIAL_WAVEFORM, f"row source mismatch: {row_batch!r}")
    _assert(row_batch.variable_names == ("speed.target", "speed.feedback", "pid.output"), f"row names mismatch: {row_batch.variable_names!r}")
    _assert(row_batch.sample_count == 2, f"row sample count mismatch: {row_batch.sample_count}")
    _assert(row_batch.samples[0].sequence == 40 and row_batch.samples[-1].sequence == 41, f"row sequence mismatch: {row_batch.samples!r}")
    _assert(abs(row_batch.actual_rate_hz - 100.0) < 0.001, f"row rate mismatch: {row_batch.actual_rate_hz}")
    _assert(row_batch.series()["speed.feedback"][1] == (59.4, 60.7), f"row series mismatch: {row_batch.series()!r}")

    series_batch = serial_waveform_batch_from_series(
        {
            "speed.feedback": (
                np.array([0.0, 0.01, 0.02], dtype=np.float64),
                np.array([58.0, 60.5, 61.0], dtype=np.float64),
            ),
            "pid.output": ([0.0, 0.01, 0.02], [10.0, "bad", 13.0]),
            "speed.target": ([0.0, 0.01, 0.02, 0.03], [60.0, 60.0, 60.0, 60.0]),
        }
    )
    _assert(series_batch.source_key == SCOPE_SOURCE_SERIAL_WAVEFORM, f"series source mismatch: {series_batch!r}")
    _assert(series_batch.variable_names == ("speed.feedback", "pid.output", "speed.target"), f"series names mismatch: {series_batch.variable_names!r}")
    _assert(series_batch.sample_count == 3, f"series sample count mismatch: {series_batch.sample_count}")
    _assert(abs(series_batch.sample_interval_s - 0.01) < 0.000001, f"series interval mismatch: {series_batch.sample_interval_s}")
    _assert(abs(series_batch.actual_rate_hz - 100.0) < 0.001, f"series rate mismatch: {series_batch.actual_rate_hz}")
    _assert(math.isnan(series_batch.samples[1].values["pid.output"]), f"bad values should become NaN: {series_batch.samples[1]!r}")

    empty_named = serial_waveform_batch_from_series({"empty": ([], [])})
    _assert(empty_named.variable_names == ("empty",), f"empty names should be preserved: {empty_named.variable_names!r}")
    _assert(empty_named.sample_count == 0, f"empty sample count mismatch: {empty_named.sample_count}")

    positional = serial_samples_to_acquisition_batch(
        ((1, 2), (3, "bad")),
        channel_names=("a",),
        start_time_s=1.0,
        sample_interval_s=0.5,
    )
    _assert(positional.variable_names == ("a", "ch2"), f"positional names mismatch: {positional.variable_names!r}")
    _assert(positional.samples[0].values["ch2"] == 2.0, f"positional value mismatch: {positional.samples[0]!r}")
    _assert(math.isnan(positional.samples[1].values["ch2"]), f"positional bad value mismatch: {positional.samples[1]!r}")

    print("PASS serial waveform batch probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
