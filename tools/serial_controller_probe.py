"""No-hardware lifecycle probe for SerialController."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from src.ui.serial_controller import SerialController  # noqa: E402


@dataclass(frozen=True)
class FakePortInfo:
    device: str
    name: str = ""
    description: str = ""
    hwid: str = ""


class FakePortLister:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> list[FakePortInfo]:
        self.calls += 1
        return [FakePortInfo("COM_FAKE_CTRL", "COM_FAKE_CTRL", "Loopback Fixture", "FAKE")]


class FakeSerialCollector:
    def __init__(self) -> None:
        self.config = None
        self.starts = 0
        self.stops = 0
        self.clears = 0
        self.sends: list[tuple[object, str]] = []
        self.last_error = ""
        self._running = False
        self._log_sequence = 0
        self._logs: list[tuple[int, float, str]] = []
        self._data = {
            "speed.feedback": ([0.0, 0.05, 0.10], [60.0, 60.4, 60.2]),
            "pid.output": ([0.0, 0.05, 0.10], [41.0, 42.5, 41.8]),
        }

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, config=None) -> None:
        if config is not None:
            self.config = config
        if self.config is None:
            raise RuntimeError("fake collector requires config")
        self.starts += 1
        self._running = True
        self.last_error = ""
        self._append_log(
            f"fake opened {self.config.port} @ {self.config.baudrate}, {self.config.protocol}"
        )

    def stop(self) -> None:
        self.stops += 1
        if self._running:
            self._append_log("fake stopped")
        self._running = False
        return True

    def clear(self) -> None:
        self.clears += 1
        self._log_sequence = 0
        self._logs.clear()

    def send(self, payload: object, mode: str = "ascii") -> int:
        if not self._running:
            raise RuntimeError("fake serial port is not open")
        self.sends.append((payload, mode))
        self._append_log(f"fake sent {mode}: {payload!r}")
        return len(str(payload))

    def get_logs_since(self, sequence: int, max_lines: int | None = None):
        if sequence > self._log_sequence:
            sequence = 0
        entries = [(stamp, line) for seq, stamp, line in self._logs if seq > sequence]
        if max_lines is not None and max_lines > 0:
            entries = entries[-max_lines:]
        return self._log_sequence, entries

    def get_data(self, tail_seconds: float | None = None):
        return {
            name: (list(x_values), list(y_values))
            for name, (x_values, y_values) in self._data.items()
        }

    def _append_log(self, line: str) -> None:
        self._log_sequence += 1
        self._logs.append((self._log_sequence, time.perf_counter(), line))


class Recorder:
    def __init__(self) -> None:
        self.ports: list[object] = []
        self.logs: list[tuple[str, str]] = []
        self.connected: list[bool] = []
        self.busy: list[tuple[bool, str]] = []
        self.send_enabled: list[bool] = []
        self.send_accepted = 0
        self.scope_data: list[object] = []

    def bind(self, controller: SerialController) -> None:
        controller.portsChanged.connect(lambda ports: self.ports.extend(list(ports)))
        controller.logReceived.connect(lambda text, level="rx": self.logs.append((str(text), str(level))))
        controller.connectedChanged.connect(lambda value: self.connected.append(bool(value)))
        controller.busyChanged.connect(lambda busy, label="": self.busy.append((bool(busy), str(label))))
        controller.sendEnabledChanged.connect(lambda enabled: self.send_enabled.append(bool(enabled)))
        controller.sendAccepted.connect(lambda: setattr(self, "send_accepted", self.send_accepted + 1))
        controller.scopeDataChanged.connect(lambda data: self.scope_data.append(data))


def _app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication(sys.argv[:1])


def _pump(seconds: float = 0.05) -> None:
    app = _app()
    deadline = time.perf_counter() + max(0.0, seconds)
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.005)


def _wait_for(description: str, predicate, timeout: float = 2.0) -> None:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        _pump(0.02)
        if predicate():
            return
    raise AssertionError(f"timed out waiting for {description}")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    _app()
    collector = FakeSerialCollector()
    port_lister = FakePortLister()
    controller = SerialController(collector=collector, port_lister=port_lister)
    recorder = Recorder()
    recorder.bind(controller)

    controller.refresh_ports()
    _pump()
    _assert(port_lister.calls == 1, "port lister was not called")
    _assert(any(getattr(port, "device", "") == "COM_FAKE_CTRL" for port in recorder.ports), "port signal missing fake port")

    options = SimpleNamespace(port="COM_FAKE_CTRL", baudrate=115200, protocol="FireWater CSV")
    controller.connect_serial(options)
    _wait_for("collector start", lambda: collector.starts == 1 and collector.is_running)
    _wait_for("connected signal", lambda: True in recorder.connected)
    _assert(getattr(collector.config, "protocol", "") == "firewater", "protocol label was not normalized")
    _assert((True, "正在打开…") in recorder.busy, "busy-open signal missing")
    _assert(any(item[0] is False for item in recorder.busy), "busy false signal missing after connect")

    controller.refresh_runtime()
    _pump()
    _assert(any("fake opened" in text for text, _level in recorder.logs), "runtime log missing")
    _assert(recorder.scope_data and "speed.feedback" in recorder.scope_data[-1], "scope data signal missing")

    controller.send("ping\r\n", "ascii")
    _wait_for("collector send", lambda: len(collector.sends) == 1)
    _wait_for("send accepted", lambda: recorder.send_accepted == 1)
    _assert(recorder.send_enabled[:1] == [False], "send button disable signal missing")
    _assert(recorder.send_enabled[-1:] == [True], "send button re-enable signal missing")
    _assert(any(level == "tx" and "ping\\r\\n" in text for text, level in recorder.logs), "TX log was not escaped")

    controller.disconnect_serial()
    _wait_for("collector stop after disconnect", lambda: collector.stops == 1 and not collector.is_running)
    _wait_for("disconnected signal", lambda: False in recorder.connected)

    controller.clear()
    _assert(collector.clears == 1, "clear did not reach collector")

    controller.connect_serial(options)
    _wait_for("collector restart", lambda: collector.starts == 2 and collector.is_running)
    stops_before_shutdown = collector.stops
    stopped_cleanly = controller.shutdown(timeout=0.6)
    _pump()
    _assert(stopped_cleanly, "shutdown did not join workers")
    _assert(collector.stops > stops_before_shutdown and not collector.is_running, "shutdown did not stop collector")

    stuck_collector = FakeSerialCollector()
    stuck_collector.stop = lambda: False
    stuck_controller = SerialController(collector=stuck_collector, port_lister=port_lister)
    _assert(not stuck_controller.shutdown(timeout=0.05), "shutdown should report stuck collector")

    print(
        "PASS serial controller probe "
        f"ports={port_lister.calls} starts={collector.starts} "
        f"stops={collector.stops} sends={len(collector.sends)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
