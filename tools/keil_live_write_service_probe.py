"""Probe reusable Keil live variable-write service without hardware."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.core.keil.live_write as live_write  # noqa: E402
from src.core.keil.live_write import (  # noqa: E402
    KeilLiveVariableWriteRequest,
    write_keil_live_variable,
)
from src.core.models import BaseType, MemberInfo, StructType, Symbol, Variable  # noqa: E402


class FakeSession:
    def __init__(self) -> None:
        self.memory = bytearray(128)
        self.commands: list[str] = []
        self.write_count = 0
        self.fail_memory = False

    def _offset(self, address: int) -> int:
        return int(address) - 0x20000000

    def execute_command(self, command: str, *, echo: bool = False) -> None:
        self.commands.append(command)

    def read_memory(self, address: int, size: int) -> bytes:
        offset = self._offset(address)
        return bytes(self.memory[offset : offset + int(size)])

    def write_memory(self, address: int, data: bytes) -> None:
        self.write_count += 1
        if self.fail_memory:
            raise RuntimeError("synthetic memory write failure")
        offset = self._offset(address)
        self.memory[offset : offset + len(data)] = bytes(data)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _patch_cache(axf: Path):
    float_t = BaseType("float", 4, "float")
    int_t = BaseType("int", 4, "signed")
    speed_level_t = BaseType("uint16_t", 2, "unsigned")
    pid_t = StructType(
        "PID_t",
        28,
        members=[
            MemberInfo("Target", 0, float_t),
            MemberInfo("Actual", 4, float_t),
            MemberInfo("Out", 8, float_t),
            MemberInfo("Kp", 12, float_t),
            MemberInfo("Ki", 16, float_t),
            MemberInfo("Kd", 20, float_t),
            MemberInfo("Error0", 24, float_t),
        ],
    )
    cache = live_write._SymbolCache(
        symbols=[
            Symbol("debug_setpoint", 0x20000008, 4, "GLOBAL", "OBJECT", "3"),
            Symbol("SpeedLevel", 0x20000040, 2, "GLOBAL", "OBJECT", "3"),
        ],
        variables=[
            Variable("debug_setpoint", 0x20000008, 4, type_info=int_t),
            Variable("SpeedLevel", 0x20000040, 2, type_info=speed_level_t),
            Variable("AnglePID", 0x20000020, 28, type_info=pid_t),
        ],
        debug_loaded=True,
    )
    live_write._CACHE[axf] = cache


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopmaster-keil-live-write-") as tmp:
        axf = Path(tmp) / "demo.axf"
        axf.write_bytes(b"\x7fELF")
        axf = axf.resolve()
        _patch_cache(axf)

        session = FakeSession()
        session.memory[8:12] = (5000).to_bytes(4, "little", signed=True)
        result = write_keil_live_variable(
            session,
            KeilLiveVariableWriteRequest("debug_setpoint", "6000", axf_path=axf),
        )
        _assert(result.written, result.summary())
        _assert(result.method == "memory", f"expected memory method, got {result.method}")
        _assert(result.old_value == "5000", f"old value mismatch: {result.old_value}")
        _assert(result.readback_value == "6000", f"readback mismatch: {result.readback_value}")
        _assert(session.read_memory(0x20000008, 4) == (6000).to_bytes(4, "little", signed=True), "memory payload mismatch")

        result = write_keil_live_variable(
            session,
            KeilLiveVariableWriteRequest("SpeedLevel", "3", axf_path=axf),
        )
        _assert(result.written, result.summary())
        _assert(result.resolved is not None and result.resolved.size == 2, "SpeedLevel should resolve as uint16_t")
        _assert(session.read_memory(0x20000040, 2) == b"\x03\x00", "uint16 payload mismatch")

        result = write_keil_live_variable(
            session,
            KeilLiveVariableWriteRequest("AnglePID.Kp", "40.5", axf_path=axf),
        )
        _assert(result.written, result.summary())
        _assert(result.resolved is not None and result.resolved.address == 0x2000002C, "member offset mismatch")
        _assert(result.resolved.type_name == "float", f"member type mismatch: {result.resolved.type_name}")
        _assert(result.readback_value.startswith("40.5"), f"float readback mismatch: {result.readback_value}")

        command_session = FakeSession()
        command_session.fail_memory = True
        result = write_keil_live_variable(
            command_session,
            KeilLiveVariableWriteRequest("debug_setpoint", "7000", axf_path=axf, allow_command_fallback=True),
        )
        _assert(result.written, result.summary())
        _assert(result.method == "command", f"fallback should use command: {result.method}")
        _assert(command_session.commands == ["debug_setpoint = 7000"], f"command mismatch: {command_session.commands!r}")
        _assert(any(key == "内存写入" for key, _value in result.diagnostics), "fallback should retain memory diagnostic")

    print("PASS keil live write service probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

