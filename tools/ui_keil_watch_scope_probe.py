"""Probe Debug Workbench preset-to-Keil-Watch oscilloscope wiring."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_workbench import DebugRuntimeState, make_debug_status  # noqa: E402
from src.ui.gui import MainWindow  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


PROJECT = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project>
  <Targets>
    <Target>
      <TargetName>Target 1</TargetName>
      <Groups>
        <Group>
          <GroupName>User</GroupName>
          <Files>
            <File><FileName>main.c</FileName><FileType>1</FileType><FilePath>.\\User\\main.c</FilePath></File>
          </Files>
        </Group>
      </Groups>
    </Target>
  </Targets>
</Project>
"""


MAIN_C = """
float Angle;
float AngleAcc;
float AngleAcc_Filter;
float AveSpeed;
float DifSpeed;
int PWML;
int PWMR;
typedef struct {
    float Kp;
    float Ki;
    float Kd;
    float Out;
} PID_t;
PID_t AnglePID;
PID_t SpeedPID;
PID_t TurnPID;
unsigned short SpeedLevel = 5;
void Encoder_Get(void);
void Motor_SetPWM(int left, int right);
"""


class FakeKeilWatchBackend:
    recommended_hz = 20
    max_hz = 50

    def __init__(self) -> None:
        self.connected = False
        self.last_error = ""
        self.last_warning = ""
        self.reads: list[tuple[str, ...]] = []

    @property
    def is_connected(self) -> bool:
        return self.connected

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> bool:
        self.connected = False
        return True

    def clamp_sample_rate(self, requested_hz: int) -> tuple[int, str]:
        if int(requested_hz) > self.max_hz:
            return self.max_hz, f"Keil Watch 已从 {requested_hz} Hz 降到 {self.max_hz} Hz"
        return int(requested_hz), ""

    def read_batch(self, variables):
        names = tuple(name for name, _addr, _type_info in variables)
        self.reads.append(names)
        return {name: float(index + 1) for index, name in enumerate(names)}


class FakeDebugBackend:
    def __init__(self, watch: FakeKeilWatchBackend) -> None:
        self.watch = watch

    def create_watch_transport(self, *, connection_name: str = "LoopMasterWatch"):
        return self.watch


def _pump(app: QApplication, seconds: float = 0.2) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _write_fixture(root: Path) -> Path:
    project_dir = root / "Balance"
    user_dir = project_dir / "User"
    user_dir.mkdir(parents=True)
    (user_dir / "main.c").write_text(MAIN_C, encoding="utf-8")
    project_path = project_dir / "Project.uvprojx"
    project_path.write_text(PROJECT, encoding="utf-8")
    return project_path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    pg.setConfigOptions(antialias=False)
    app = QApplication.instance() or QApplication([])
    apply_pcl_theme(app)
    watch = FakeKeilWatchBackend()

    with tempfile.TemporaryDirectory(prefix="loopmaster-keil-watch-ui-") as tmp:
        project_path = _write_fixture(Path(tmp))
        window = MainWindow()
        window._config_path = Path(tmp) / "loopmaster.json"
        window._scope_read_source = "swd"
        window._keil_watch_registry.clear()
        window._keil_watch_backend = None
        window._keil_watch_next_idle_connect = 0.0
        window._monitored = set()
        window._monitor_list = []
        window._collector.stop(timeout=0.2)
        window._collector.set_backend(window._backend)
        window._sync_value_table_placeholders()
        window._debug_backend = FakeDebugBackend(watch)
        tab = window._tab_debug_workbench
        tab.set_debug_status(
            make_debug_status(
                state=DebugRuntimeState.KEIL_ATTACHED,
                backend="keil",
                detail="fake attached",
                project_path=project_path,
                target_name="Target 1",
            ),
            controls_ready=True,
        )
        window._refresh_debug_variable_presets()
        _pump(app)

        expressions = [
            tab.variable_preset_table.item(row, 0).text()
            for row in range(tab.variable_preset_table.rowCount())
            if tab.variable_preset_table.item(row, 0) is not None
        ]
        _assert("Angle" in expressions, f"Angle preset missing: {expressions!r}")
        angle_row = expressions.index("Angle")
        tab.variable_preset_table.selectRow(angle_row)
        tab.variable_preset_watch_button.click()
        _pump(app, 0.1)

        _assert(window._scope_read_source == "keil_watch", "scope source should switch to Keil Watch")
        boundary_scope = getattr(getattr(tab, "boundary_scope_label", None), "text", lambda: "")()
        _assert("Keil Watch" in boundary_scope, f"debug boundary scope chip should switch to Keil Watch: {boundary_scope!r}")
        scope_combo_key = tab.scope_source_combo.currentData() if hasattr(tab, "scope_source_combo") else ""
        _assert(scope_combo_key == "keil_watch", f"scope source combo should switch to Keil Watch: {scope_combo_key!r}")
        diagnostics = {
            tab.diagnostics_table.item(row, 0).text(): tab.diagnostics_table.item(row, 1).text()
            for row in range(tab.diagnostics_table.rowCount())
            if tab.diagnostics_table.item(row, 0) is not None and tab.diagnostics_table.item(row, 1) is not None
        }
        _assert(diagnostics.get("示波采集源") == "Keil Watch", f"diagnostics should show Keil Watch source: {diagnostics!r}")
        _assert(diagnostics.get("采集模式") == "调试器链路", f"Keil Watch mode diagnostics mismatch: {diagnostics!r}")
        _assert(diagnostics.get("调试接管") == "会接管调试链", f"Keil Watch takeover diagnostics mismatch: {diagnostics!r}")
        _assert("断点" in diagnostics.get("调试能力", ""), f"Keil Watch debug capability diagnostics mismatch: {diagnostics!r}")
        _assert("Angle" in window._monitored, f"Angle not monitored: {window._monitored!r}")
        _assert("Angle" in window._keil_watch_registry, "Angle missing from Keil watch registry")
        _assert(window._value_table.rowCount() >= 1, "value table should show watch variable")
        _assert(window._value_table.item(0, 2).text() == "Keil", "address column should show Keil")

        window._idle_read()
        _pump(app, 0.1)
        _assert(watch.connected, "idle read should connect fake watch backend")
        _assert(watch.reads and watch.reads[-1] == ("Angle",), f"watch read mismatch: {watch.reads!r}")
        _assert(window._value_table.item(0, 1).text() != "--", "value table should update from watch read")

        window._rate_combo.setCurrentIndex(window._rate_combo.findData(0))
        window._on_start()
        _pump(app, 0.25)
        _assert(window._collector.is_running, "collector should start with fake Keil Watch backend")
        _assert(window._collector._sample_rate == watch.max_hz, f"sample rate should clamp to {watch.max_hz}")
        _assert("降到" in window._scope_rate_note, f"rate note missing clamp warning: {window._scope_rate_note!r}")
        window._refresh_debug_workbench_diagnostics()
        diagnostics = {
            tab.diagnostics_table.item(row, 0).text(): tab.diagnostics_table.item(row, 1).text()
            for row in range(tab.diagnostics_table.rowCount())
            if tab.diagnostics_table.item(row, 0) is not None and tab.diagnostics_table.item(row, 1) is not None
        }
        _assert(diagnostics.get("采集批次来源") == "keil_watch", f"Keil Watch batch source mismatch: {diagnostics!r}")
        _assert(int(diagnostics.get("采集批次样本", "0")) > 0, f"Keil Watch batch sample count mismatch: {diagnostics!r}")
        _assert("Angle" in diagnostics.get("采集批次变量名", ""), f"Keil Watch batch variable names mismatch: {diagnostics!r}")
        window._on_stop()
        watch.connect()
        _assert(watch.connected, "fake watch should reconnect before close check")
        window.close()
        app.processEvents()
        _assert(not watch.connected, "window close should disconnect Keil Watch backend")

    print("PASS Keil Watch scope UI probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
