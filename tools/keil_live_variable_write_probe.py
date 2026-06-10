"""Probe Keil UVSOCK live variable-write wrapper without launching Keil."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.uvsock import (  # noqa: E402
    KeilUvscLiveSession,
    _Vset,
    _ExecCmd,
    _configure_uvsc_signatures,
    _sstr_to_text,
    _set_sstr,
)


class FakeFunction:
    def __init__(self, func):
        self._func = func
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._func(*args)


class FakeUvscLibrary:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.UVSC_Init = FakeFunction(self._init)
        self.UVSC_UnInit = FakeFunction(self._uninit)
        self.UVSC_OpenConnection = FakeFunction(self._open)
        self.UVSC_CloseConnection = FakeFunction(self._close)
        self.UVSC_DBG_STATUS = FakeFunction(self._status)
        self.UVSC_DBG_ENTER = FakeFunction(self._enter)
        self.UVSC_DBG_EXIT = FakeFunction(self._exit)
        self.UVSC_DBG_STOP_EXECUTION = FakeFunction(self._stop)
        self.UVSC_DBG_START_EXECUTION = FakeFunction(self._start)
        self.UVSC_GEN_SET_OPTIONS = FakeFunction(self._set_options)
        self.UVSC_DBG_CALC_EXPRESSION = FakeFunction(self._calc)
        self.UVSC_DBG_EVAL_EXPRESSION_TO_STR = FakeFunction(self._eval)
        self.UVSC_DBG_MEM_READ = FakeFunction(self._mem_read)
        self.UVSC_DBG_MEM_WRITE = FakeFunction(self._mem_write)
        self.UVSC_DBG_EXEC_CMD = FakeFunction(self._exec_cmd)
        self.UVSC_GetLastError = FakeFunction(self._last_error)
        self.memory = bytearray(b"\xE8\x03\x00\x00")
        self.running = False

    def _init(self, *_args) -> int:
        self.calls.append(("init", None))
        return 0

    def _uninit(self) -> int:
        self.calls.append(("uninit", None))
        return 0

    def _open(self, name, handle, port, *_args) -> int:
        self.calls.append(("open", (ctypes.string_at(name).decode("utf-8"), port._obj.value)))
        handle._obj.value = 42
        return 0

    def _close(self, handle: int, mode: int) -> int:
        self.calls.append(("close", (handle, mode)))
        return 0

    def _status(self, handle: int, running) -> int:
        self.calls.append(("status", handle))
        running._obj.value = 1 if self.running else 0
        return 0

    def _enter(self, handle: int) -> int:
        self.calls.append(("enter", handle))
        return 0

    def _exit(self, handle: int) -> int:
        self.calls.append(("exit", handle))
        return 0

    def _stop(self, handle: int) -> int:
        self.calls.append(("stop", handle))
        self.running = False
        return 0

    def _start(self, handle: int) -> int:
        self.calls.append(("start", handle))
        self.running = True
        return 0

    def _set_options(self, handle: int, options) -> int:
        self.calls.append(("options", (handle, options._obj.flags)))
        return 0

    def _calc(self, handle: int, vset_ptr, length: int) -> int:
        vset = ctypes.cast(vset_ptr, ctypes.POINTER(_Vset)).contents
        self.calls.append(("calc", (handle, _sstr_to_text(vset.str), length)))
        return 0

    def _eval(self, handle: int, vset_ptr, length: int) -> int:
        vset = ctypes.cast(vset_ptr, ctypes.POINTER(_Vset)).contents
        expression = _sstr_to_text(vset.str)
        self.calls.append(("eval", (handle, expression, length)))
        _set_sstr(vset.str, "5000")
        return 0

    def _mem_read(self, handle: int, buffer, length: int) -> int:
        self.calls.append(("mem_read", (handle, length)))
        ctypes.memmove(ctypes.addressof(buffer) + 24, bytes(self.memory), len(self.memory))
        return 0

    def _mem_write(self, handle: int, buffer, length: int) -> int:
        self.calls.append(("mem_write", (handle, length)))
        raw = ctypes.string_at(ctypes.addressof(buffer) + 24, 4)
        self.memory[:] = raw
        return 0

    def _exec_cmd(self, handle: int, cmd_ptr, length: int) -> int:
        cmd = ctypes.cast(cmd_ptr, ctypes.POINTER(_ExecCmd)).contents
        self.calls.append(("exec", (handle, _sstr_to_text(cmd.sCmd), length)))
        return 0

    def _last_error(self, handle: int, msg_type, status, buffer, max_len: int) -> int:
        self.calls.append(("last_error", handle))
        msg_type._obj.value = 0
        status._obj.value = 0
        ctypes.memset(buffer, 0, max_len)
        return 0


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> int:
    fake = FakeUvscLibrary()
    _configure_uvsc_signatures(fake)

    session = KeilUvscLiveSession(fake, 42, owns_uvsc=True)
    try:
        session.set_extended_stack(True)
        session.enter_debug()
        running = session.target_running()
        _assert(running is False, "target_running should decode fake halted state")
        run_result = session.run_target()
        _assert(run_result.succeeded and run_result.target_running is True, f"run_target mismatch: {run_result}")
        halt_result = session.halt_target()
        _assert(halt_result.succeeded and halt_result.target_running is False, f"halt_target mismatch: {halt_result}")

        result = session.write_expression_value("debug_setpoint", "5000")
        _assert(result.written, f"write should pass: {result}")
        _assert(result.expression == "debug_setpoint = 5000", "assignment expression mismatch")
        _assert(result.readback_expression == "debug_setpoint", "readback expression mismatch")
        _assert(result.readback_text == "5000", "readback value mismatch")
        session.write_memory(0x20000008, (6000).to_bytes(4, "little", signed=True))
        memory = session.read_memory(0x20000008, 4)
        _assert(int.from_bytes(memory, "little", signed=True) == 6000, "memory readback mismatch")
        session.execute_command("debug_setpoint = 7000")
    finally:
        session.close()

    names = [name for name, _payload in fake.calls]
    expected = [
        "options",
        "enter",
        "status",
        "start",
        "status",
        "stop",
        "status",
        "calc",
        "eval",
        "mem_write",
        "mem_read",
        "exec",
        "close",
        "uninit",
    ]
    _assert(names == expected, f"call order mismatch: {names!r}")
    calc_payload = fake.calls[7][1]
    _assert(calc_payload[1] == "debug_setpoint = 5000", f"calc payload mismatch: {calc_payload!r}")

    failed = session.write_expression_value("debug_gain", "")
    _assert(not failed.attempted and not failed.written, "empty write should be rejected before UVSC call")

    print("PASS keil live variable write probe")
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
