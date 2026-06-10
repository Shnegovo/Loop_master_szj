"""Small lifecycle helpers for deterministic shutdown sequencing."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable


@dataclass(frozen=True)
class ShutdownStepResult:
    label: str
    ok: bool
    elapsed_ms: float
    detail: str = ""
    error: str = ""

    def to_record(self) -> dict[str, object]:
        return {
            "label": self.label,
            "ok": self.ok,
            "elapsed_ms": round(float(self.elapsed_ms), 3),
            "detail": self.detail,
            "error": self.error,
        }


@dataclass(frozen=True)
class ShutdownReport:
    started_at: float
    elapsed_ms: float
    steps: tuple[ShutdownStepResult, ...]

    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.steps)

    def step(self, label: str) -> ShutdownStepResult | None:
        for item in self.steps:
            if item.label == label:
                return item
        return None

    def to_record(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "started_at": self.started_at,
            "elapsed_ms": round(float(self.elapsed_ms), 3),
            "steps": [step.to_record() for step in self.steps],
        }


class ShutdownSequence:
    def __init__(self) -> None:
        self._started_at = time.perf_counter()
        self._steps: list[ShutdownStepResult] = []

    def run(self, label: str, fn: Callable[[], object]) -> ShutdownStepResult:
        started = time.perf_counter()
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 - shutdown must keep going
            result = ShutdownStepResult(
                label=str(label),
                ok=False,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                error=str(exc),
            )
        else:
            ok, detail = _coerce_step_value(value)
            result = ShutdownStepResult(
                label=str(label),
                ok=ok,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                detail=detail,
            )
        self._steps.append(result)
        return result

    def report(self) -> ShutdownReport:
        return ShutdownReport(
            started_at=self._started_at,
            elapsed_ms=(time.perf_counter() - self._started_at) * 1000.0,
            steps=tuple(self._steps),
        )


def _coerce_step_value(value: object) -> tuple[bool, str]:
    if value is None:
        return True, ""
    if isinstance(value, bool):
        return value, ""
    if isinstance(value, tuple) and value:
        ok = bool(value[0])
        detail = str(value[1]) if len(value) > 1 else ""
        return ok, detail
    return True, str(value)
