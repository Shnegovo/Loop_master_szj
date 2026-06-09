"""Probe LoopMaster sampling throughput without hardware.

The goal is to make sure UI rendering changes do not throttle the collector.
It uses the same DataCollector class with a deterministic fake backend and
reports achieved sample rates for common target rates.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.collector import DataCollector  # noqa: E402
from src.core.models import BaseType  # noqa: E402


class FastFakeBackend:
    is_connected = True

    def read_batch(self, variables):
        now = time.perf_counter()
        return {
            name: math.sin(now * (index + 1)) * 100.0
            for index, (name, _addr, _ti) in enumerate(variables)
        }

    def read_batch_rows(self, variables, batch_size: int):
        rows = []
        base = time.perf_counter()
        for sample in range(batch_size):
            now = base + sample * 0.001
            rows.append([
                math.sin(now * (index + 1)) * 100.0
                for index, (_name, _addr, _ti) in enumerate(variables)
            ])
        return rows


def measure(rate: int, variables_count: int, seconds: float) -> float:
    ti = BaseType("float", 4, "float")
    variables = [
        (f"signal_{index}", 0x20000000 + index * 4, ti)
        for index in range(variables_count)
    ]
    collector = DataCollector()
    collector.set_backend(FastFakeBackend())
    collector.configure(rate, buffer_seconds=max(4.0, seconds + 1.0))
    collector.set_variables(variables)
    collector.start()
    start_count = collector._sample_count
    start = time.perf_counter()
    time.sleep(seconds)
    elapsed = time.perf_counter() - start
    count = collector._sample_count - start_count
    collector.stop()
    return count / elapsed if elapsed > 0 else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rates", default="10,50,100,200,500,1000")
    parser.add_argument("--variables", type=int, default=8)
    parser.add_argument("--seconds", type=float, default=2.5)
    parser.add_argument("--min-ratio", type=float, default=0.92)
    args = parser.parse_args()

    failures: list[str] = []
    for item in args.rates.split(","):
        rate = int(item.strip())
        actual = measure(rate, args.variables, args.seconds)
        ratio = actual / rate if rate else 0
        print(f"target={rate:4d} Hz  actual={actual:7.1f} Hz  ratio={ratio:5.1%}")
        if ratio < args.min_ratio:
            failures.append(f"{rate} Hz only reached {actual:.1f} Hz ({ratio:.1%})")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
