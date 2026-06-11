"""No-hardware probe for DataCollector transport compatibility."""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.collector import DataCollector
from src.core.acquisition_sources import SCOPE_SOURCE_KEIL_WATCH
from src.core.models import BaseType
from src.core.transports import VariableSpec


class FakeTransport:
    def __init__(self) -> None:
        self._connected = True
        self._sample = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    def read_batch(self, variables: Sequence[VariableSpec]) -> dict[str, float]:
        self._sample += 1
        return {
            name: float(self._sample + index * 10)
            for index, (name, _address, _type_info) in enumerate(variables)
        }

    def read_batch_rows(
        self,
        variables: Sequence[VariableSpec],
        count: int,
    ) -> list[dict[str, float]]:
        rows: list[dict[str, float]] = []
        for _ in range(max(1, int(count))):
            rows.append(self.read_batch(variables))
        return rows


def main() -> None:
    float_type = BaseType("float", 4, "float")
    variables = [
        ("speed.feedback", 0x20000000, float_type),
        ("speed.target", 0x20000004, float_type),
    ]
    collector = DataCollector()
    collector.set_backend(FakeTransport())
    collector.configure(200, buffer_seconds=1.0)
    collector.set_variables(variables)
    collector.start()
    time.sleep(0.28)
    stopped = collector.stop(timeout=1.0)
    data = collector.get_data()

    if not stopped:
        raise SystemExit("collector thread did not stop")
    if set(data) != {"speed.feedback", "speed.target"}:
        raise SystemExit(f"unexpected series names: {sorted(data)}")

    lengths = {name: len(values) for name, (_timestamps, values) in data.items()}
    if any(length <= 0 for length in lengths.values()):
        raise SystemExit(f"empty collected series: {lengths}")
    if collector.actual_rate <= 0.0:
        raise SystemExit("collector actual_rate did not update")

    feedback = data["speed.feedback"][1]
    if len(feedback) >= 2 and not bool((feedback[1:] >= feedback[:-1]).all()):
        raise SystemExit("fake transport samples are not monotonic")

    batch = collector.get_acquisition_batch(SCOPE_SOURCE_KEIL_WATCH)
    if batch.source_key != SCOPE_SOURCE_KEIL_WATCH:
        raise SystemExit(f"collector acquisition batch source mismatch: {batch.source_key}")
    if batch.sample_count != min(lengths.values()):
        raise SystemExit(f"collector acquisition batch count mismatch: {batch.sample_count} vs {lengths}")
    if set(batch.variable_names) != {"speed.feedback", "speed.target"}:
        raise SystemExit(f"collector acquisition batch variables mismatch: {batch.variable_names!r}")
    series = batch.series()
    if set(series) != {"speed.feedback", "speed.target"}:
        raise SystemExit(f"collector acquisition batch series mismatch: {series.keys()!r}")
    if batch.to_record()["sample_count"] != batch.sample_count:
        raise SystemExit("collector acquisition batch record count mismatch")

    print(
        "PASS collector fake transport "
        f"samples={collector._sample_count} lengths={lengths} "
        f"actual_rate={collector.actual_rate:.1f}Hz",
        flush=True,
    )


if __name__ == "__main__":
    main()
