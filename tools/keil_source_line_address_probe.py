"""Probe AXF/DWARF source-line to address resolution for Keil breakpoints."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.source_line_address import (  # noqa: E402
    resolve_address_source_line,
    resolve_source_line_address,
)


AXF = ROOT / "firmware" / "keil_f401_variable_probe" / "Objects" / "f401_variable_probe.axf"
MAIN_C = ROOT / "firmware" / "keil_f401_variable_probe" / "main.c"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    _assert(AXF.exists(), f"AXF missing: {AXF}")
    _assert(MAIN_C.exists(), f"source missing: {MAIN_C}")

    result = resolve_source_line_address(AXF, MAIN_C, 62, source_roots=(MAIN_C.parent,))
    _assert(result.resolved, f"line 62 did not resolve: {result}")
    _assert(result.address is not None and 0x08000000 <= result.address < 0x08100000, f"address out of flash: {result}")
    _assert(result.line == 62, f"line mismatch: {result}")
    _assert(result.exact, f"expected exact line match: {result}")

    nearest = resolve_source_line_address(AXF, MAIN_C, 66, source_roots=(MAIN_C.parent,))
    _assert(nearest.resolved, f"line 66 should resolve to a nearby executable line: {nearest}")
    _assert(nearest.line >= 66, f"nearest line should not move backwards: {nearest}")

    reverse = resolve_address_source_line(AXF, result.address, source_roots=(MAIN_C.parent,))
    _assert(reverse.resolved, f"address did not reverse-resolve: {reverse}")
    _assert(reverse.path is not None and reverse.path.name == "main.c", f"reverse path mismatch: {reverse}")
    _assert(reverse.line == 62, f"reverse line mismatch: {reverse}")
    _assert(reverse.exact, f"expected exact reverse match: {reverse}")

    print("PASS Keil source line address probe")
    print(f"line 62 -> 0x{result.address:08X}")
    print(f"0x{result.address:08X} -> {reverse.path}:{reverse.line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
