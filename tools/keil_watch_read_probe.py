"""Probe Keil Watch expression parsing and collector-style reads."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.watch import (  # noqa: E402
    KeilUvSockWatchBackend,
    KeilWatchVariable,
    keil_watch_rate_warning,
    make_keil_watch_type,
    parse_keil_numeric_value,
)


class FakeSession:
    def __init__(self) -> None:
        self.values = {
            "Angle": ("Angle = 12.5", 0),
            "AveSpeed": ("-3.25", 0),
            "SpeedLevel": ("0x05", 0),
            "Bad": ("not ready", 0),
            "Missing": ("", 9),
        }
        self.closed = False

    def evaluate_expression(self, expression: str):
        return self.values.get(expression, ("0", 0))

    def close(self) -> None:
        self.closed = True


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    _assert(parse_keil_numeric_value("Angle = 12.5") == (12.5, ""), "decimal parse failed")
    _assert(parse_keil_numeric_value("0x10") == (16.0, ""), "hex parse failed")
    value, error = parse_keil_numeric_value("abc")
    _assert(value != value and "not numeric" in error, "non-numeric should be NaN with error")

    backend = KeilUvSockWatchBackend()
    backend._session = FakeSession()
    result = backend.read_expressions(
        [
            KeilWatchVariable("Angle", "float"),
            KeilWatchVariable("AveSpeed", "float"),
            KeilWatchVariable("SpeedLevel", "uint16_t"),
            KeilWatchVariable("Bad", "float"),
            KeilWatchVariable("Missing", "float"),
        ]
    )
    values = result.values
    _assert(values["Angle"] == 12.5, f"Angle read mismatch: {values!r}")
    _assert(values["AveSpeed"] == -3.25, f"AveSpeed read mismatch: {values!r}")
    _assert(values["SpeedLevel"] == 5.0, f"SpeedLevel read mismatch: {values!r}")
    _assert(values["Bad"] != values["Bad"], "Bad should be NaN")
    _assert(values["Missing"] != values["Missing"], "Missing should be NaN")

    batch = backend.read_batch(
        [
            ("Angle", 0, make_keil_watch_type("float")),
            ("SpeedLevel", 0, make_keil_watch_type("uint16_t")),
        ]
    )
    _assert(batch == {"Angle": 12.5, "SpeedLevel": 5.0}, f"batch mismatch: {batch!r}")

    clamped, warning = backend.clamp_sample_rate(1000)
    _assert(clamped == backend.max_hz and "降到" in warning, f"clamp mismatch: {clamped}, {warning}")
    _assert("表达式读取" in keil_watch_rate_warning(30, 6), "rate warning should mention expression reads")

    _assert(backend.disconnect(), "disconnect should close fake session")
    print("PASS keil watch read probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
