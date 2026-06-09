"""Shared transport capability protocols.

These protocols describe what the acquisition layer needs, without tying it
to pyOCD, Keil, serial, or future replay backends.
"""

from __future__ import annotations

from typing import Protocol, Sequence, TypeAlias, runtime_checkable

from src.core.models import TypeInfo


VariableSpec: TypeAlias = tuple[str, int, TypeInfo]


@runtime_checkable
class VariableReadTransport(Protocol):
    """Backend that can read the current value of watched variables."""

    @property
    def is_connected(self) -> bool:
        ...

    def read_batch(self, variables: Sequence[VariableSpec]) -> dict[str, float] | Sequence[float]:
        ...


class SampleSeriesTransport(VariableReadTransport, Protocol):
    """Optional backend capability for several samples per collector tick."""

    def read_batch_samples(
        self,
        variables: Sequence[VariableSpec],
        count: int,
    ) -> Sequence[dict[str, float] | Sequence[float]]:
        ...


class SampleRowsTransport(VariableReadTransport, Protocol):
    """Optional backend capability for already-rowed sample batches."""

    def read_batch_rows(
        self,
        variables: Sequence[VariableSpec],
        count: int,
    ) -> Sequence[dict[str, float] | Sequence[float]]:
        ...


class TargetControl(Protocol):
    """Optional debug target run-state control."""

    def target_state(self) -> str:
        ...

    def halt_target(self) -> bool:
        ...

    def resume_target(self) -> bool:
        ...


class VariableWriteTransport(Protocol):
    """Optional capability for writing a typed variable value."""

    def write_variable_value(self, address: int, type_info: TypeInfo, value_text: str) -> dict:
        ...


class DebugTransport(VariableReadTransport, TargetControl, VariableWriteTransport, Protocol):
    """Full debug backend capability set used by the current LoopMaster panel."""
