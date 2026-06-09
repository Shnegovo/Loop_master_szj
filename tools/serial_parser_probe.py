"""Smoke probe for src.core.serial_backend parsers."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.serial_backend import JUSTFLOAT_TAIL, SerialProtocolParser  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        choices=("all", "csv", "firewater", "justfloat", "raw", "hex"),
        default="all",
    )
    args = parser.parse_args()

    protocols = ["csv", "firewater", "justfloat", "raw", "hex"]
    if args.protocol != "all":
        protocols = [args.protocol]

    ok = True
    for protocol in protocols:
        for name, data, expected_logs, expected_samples in _probe_cases(protocol):
            probe = SerialProtocolParser(protocol, ["a", "b", "c"])
            logs, samples = probe.feed(data)
            passed = logs == expected_logs and _samples_match(samples, expected_samples)
            status = "PASS" if passed else "FAIL"
            print(f"{status} {name}: logs={logs!r} samples={samples!r}")
            if not passed:
                print(
                    f"  expected logs={expected_logs!r} "
                    f"samples={expected_samples!r}"
                )
                ok = False
    return 0 if ok else 1


def _probe_cases(
    protocol: str,
) -> list[tuple[str, bytes, list[str], list[dict[str, float]]]]:
    if protocol == "csv":
        return [
            (
                "csv-number-list",
                b"1.0,-2.5,3.25\n",
                ["1.0,-2.5,3.25"],
                [{"a": 1.0, "b": -2.5, "c": 3.25}],
            )
        ]
    if protocol == "firewater":
        return [
            (
                "firewater-prefixed-named",
                b"FireWater:a=1.0,b=-2.5,c=3.25\n",
                ["FireWater:a=1.0,b=-2.5,c=3.25"],
                [{"a": 1.0, "b": -2.5, "c": 3.25}],
            ),
            (
                "firewater-equals",
                b"a=1,b=2\n",
                ["a=1,b=2"],
                [{"a": 1.0, "b": 2.0}],
            ),
            (
                "firewater-vofa-d",
                b"d:1,2\n",
                ["d:1,2"],
                [{"a": 1.0, "b": 2.0}],
            ),
            (
                "firewater-vofa-channels",
                b"channels: 1,2\n",
                ["channels: 1,2"],
                [{"a": 1.0, "b": 2.0}],
            ),
        ]
    if protocol == "justfloat":
        return [
            (
                "justfloat-frame",
                struct.pack("<fff", 1.0, -2.5, 3.25) + JUSTFLOAT_TAIL,
                [],
                [{"a": 1.0, "b": -2.5, "c": 3.25}],
            )
        ]
    if protocol == "raw":
        return [
            (
                "raw-short-no-newline",
                b"hello serial",
                ["hello serial"],
                [],
            )
        ]
    if protocol == "hex":
        return [
            (
                "hex-line",
                b"\x01\x02\x03\n",
                ["01 02 03"],
                [],
            )
        ]
    raise ValueError(f"Unsupported protocol: {protocol}")


def _samples_match(
    actual: list[dict[str, float]],
    expected: list[dict[str, float]],
) -> bool:
    if len(actual) != len(expected):
        return False
    return all(_sample_match(left, right) for left, right in zip(actual, expected))


def _sample_match(actual: dict[str, float], expected: dict[str, float]) -> bool:
    if set(actual) != set(expected):
        return False
    return all(abs(actual[name] - expected[name]) <= 1e-6 for name in expected)


if __name__ == "__main__":
    raise SystemExit(main())
