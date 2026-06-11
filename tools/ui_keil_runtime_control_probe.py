"""Probe Debug Workbench runtime control evidence refresh."""

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

from src.core.debug_backend import DebugBackendDiagnostic, DebugBackendSessionSnapshot  # noqa: E402
from src.core.debug_snapshots import DebugPcLocation, RemoteBreakpoint, RemoteBreakpointSnapshot  # noqa: E402
from src.core.debug_workbench import DebugBackendKind, DebugCapabilities, DebugRuntimeState, make_debug_status  # noqa: E402
from src.core.keil.backend import KeilRuntimeControlResult  # noqa: E402
from src.core.keil.uvsock import UvscRuntimeControlResult  # noqa: E402
from src.ui.gui import MainWindow  # noqa: E402
import src.ui.gui as gui_module  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


PROJECT = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project>
  <Targets>
    <Target>
      <TargetName>DebugDemo</TargetName>
      <TargetOption>
        <TargetCommonOption>
          <OutputDirectory>Objects\\</OutputDirectory>
          <OutputName>debug_demo</OutputName>
          <CreateExecutable>1</CreateExecutable>
        </TargetCommonOption>
      </TargetOption>
      <Groups>
        <Group>
          <GroupName>App</GroupName>
          <Files>
            <File><FileName>main.c</FileName><FileType>1</FileType><FilePath>..\\Core\\Src\\main.c</FilePath></File>
          </Files>
        </Group>
      </Groups>
    </Target>
  </Targets>
</Project>
"""


class FakeRuntimeBackend:
    def __init__(self, project_path: Path, source_path: Path) -> None:
        self.project_path = project_path
        self.source_path = source_path
        self.calls: list[str] = []

    def halt_target(self, *, project_path=None, target_name: str = ""):
        self.calls.append("halt")
        return self._result(
            "halt",
            DebugRuntimeState.PAUSED,
            target_running=False,
            pc_line=5,
            pc_complete=True,
            remote_id="halt-remote",
        )

    def run_target(self, *, project_path=None, target_name: str = ""):
        self.calls.append("run")
        return self._result(
            "run",
            DebugRuntimeState.RUNNING,
            target_running=True,
            pc_line=None,
            pc_complete=False,
            remote_id="run-remote",
            pc_message="目标运行中，PC 位置暂不稳定",
        )

    def reset_target(self, *, project_path=None, target_name: str = ""):
        self.calls.append("reset")
        return self._result(
            "reset",
            DebugRuntimeState.PAUSED,
            target_running=False,
            pc_line=2,
            pc_complete=True,
            remote_id="reset-remote",
        )

    def step_target(self, *, project_path=None, target_name: str = ""):
        self.calls.append("step")
        return self._result(
            "step",
            DebugRuntimeState.PAUSED,
            target_running=False,
            pc_line=6,
            pc_complete=True,
            remote_id="step-remote",
        )

    def step_over_target(self, *, project_path=None, target_name: str = ""):
        self.calls.append("step_over")
        return self._result(
            "step_over",
            DebugRuntimeState.PAUSED,
            target_running=False,
            pc_line=7,
            pc_complete=True,
            remote_id="step-over-remote",
        )

    def _result(
        self,
        action: str,
        state: DebugRuntimeState,
        *,
        target_running: bool,
        pc_line: int | None,
        pc_complete: bool,
        remote_id: str,
        pc_message: str = "fake PC 已回读",
    ) -> KeilRuntimeControlResult:
        pc = DebugPcLocation(
            path=self.source_path if pc_line is not None else None,
            line=pc_line,
            address=0x08000100 + int(pc_line or 0) * 4 if pc_line is not None else None,
            source="keil_eval_pc" if pc_complete else "keil_status",
            complete=pc_complete,
            message=pc_message,
        )
        remote = RemoteBreakpointSnapshot(
            schema_version=1,
            snapshot_id=remote_id,
            project_path=self.project_path,
            target_name="DebugDemo",
            captured_at="2026-06-11T00:00:00+00:00",
            complete=True,
            breakpoints=(
                RemoteBreakpoint(
                    path=self.source_path,
                    line=5,
                    address=0x08000114,
                    enabled=True,
                    remote_id=f"{remote_id}-bp",
                ),
            ),
        )
        status = make_debug_status(
            state=state,
            backend=DebugBackendKind.KEIL,
            detail=f"fake {action}",
            project_path=self.project_path,
            target_name="DebugDemo",
            current_pc_line=pc_line,
            capabilities=DebugCapabilities(
                can_discover=True,
                can_attach=True,
                can_disconnect=True,
                can_read_variables=True,
                can_write_variables=True,
                can_halt=state == DebugRuntimeState.RUNNING,
                can_run=state == DebugRuntimeState.PAUSED,
                can_reset=True,
                can_step=state == DebugRuntimeState.PAUSED,
                can_sync_breakpoints=True,
            ),
        )
        snapshot = DebugBackendSessionSnapshot(
            schema_version=1,
            backend=DebugBackendKind.KEIL,
            adapter_name="Fake Keil Runtime",
            snapshot_id=f"snapshot-{remote_id}",
            captured_at="2026-06-11T00:00:00+00:00",
            status=status,
            diagnostics=(
                DebugBackendDiagnostic("后端", "Fake Keil Runtime"),
                DebugBackendDiagnostic("运行控制快照", remote_id),
                DebugBackendDiagnostic("目标运行", "运行中" if target_running else "已暂停"),
            ),
            capabilities=(),
            read_only=False,
            connection_attempted=True,
            connection_established=True,
            target_running=target_running,
            port=4827,
            project_path=self.project_path,
            target_name="DebugDemo",
            pc_location=pc,
            remote_breakpoint_snapshot=remote,
            remote_breakpoint_snapshot_id=remote.snapshot_id,
        )
        return KeilRuntimeControlResult(
            action=action,
            uvsc=UvscRuntimeControlResult(
                attempted=True,
                action=action,
                succeeded=True,
                target_running=target_running,
            ),
            snapshot=snapshot,
        )


def _write_fixture(root: Path) -> Path:
    project_dir = root / "MDK-ARM"
    source_dir = root / "Core" / "Src"
    source_dir.mkdir(parents=True)
    project_dir.mkdir(parents=True)
    (source_dir / "main.c").write_text(
        "volatile int speed;\n"
        "int main(void)\n"
        "{\n"
        "    speed = 0;\n"
        "    speed++;\n"
        "    speed += 2;\n"
        "    return speed;\n"
        "}\n",
        encoding="utf-8",
    )
    project_path = project_dir / "DebugDemo.uvprojx"
    project_path.write_text(PROJECT, encoding="utf-8")
    return project_path


def _pump(app: QApplication, seconds: float = 0.15) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _diagnostics(tab) -> dict[str, str]:
    return {
        tab.diagnostics_table.item(row, 0).text(): tab.diagnostics_table.item(row, 1).text()
        for row in range(tab.diagnostics_table.rowCount())
        if tab.diagnostics_table.item(row, 0) is not None and tab.diagnostics_table.item(row, 1) is not None
    }


def _patch_confirmation():
    original = gui_module.ask_pcl_confirmation
    calls = []

    def fake_confirm(_parent, title, message, **kwargs):
        calls.append({"title": title, "message": message, "kwargs": kwargs})
        return True

    gui_module.ask_pcl_confirmation = fake_confirm

    def restore() -> None:
        gui_module.ask_pcl_confirmation = original

    return calls, restore


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_pcl_theme(app)
    pg.setConfigOptions(background=(255, 255, 255), foreground=(83, 101, 125), antialias=False)

    with tempfile.TemporaryDirectory(prefix="loopmaster-ui-runtime-") as tmp:
        project_path = _write_fixture(Path(tmp))
        source_path = Path(tmp) / "Core" / "Src" / "main.c"
        output_dir = Path(tmp) / "probe-out"
        output_dir.mkdir()

        window = MainWindow()
        window._config_path = output_dir / "loopmaster.json"
        window._show_info = lambda _title, _message: None
        window._show_warning = lambda _title, _message: None
        window.resize(1360, 820)
        window.show()
        _pump(app, 0.2)

        tab = window._tab_debug_workbench
        tab.load_project(project_path)
        tab.add_breakpoint(5, path=source_path)
        _assert(not tab.local_breakpoints()[0].verified, "fresh local breakpoint should start unverified")
        initial = make_debug_status(
            state=DebugRuntimeState.RUNNING,
            backend=DebugBackendKind.KEIL,
            detail="fake running before halt",
            project_path=project_path,
            target_name="DebugDemo",
            capabilities=DebugCapabilities(
                can_discover=True,
                can_disconnect=True,
                can_read_variables=True,
                can_write_variables=True,
                can_halt=True,
                can_reset=True,
                can_sync_breakpoints=True,
            ),
        )
        tab.set_debug_status(initial, controls_ready=True)
        fake_backend = FakeRuntimeBackend(project_path, source_path)
        window._debug_backend = fake_backend
        window._sync_debug_command_preview()

        confirmations, restore_confirmation = _patch_confirmation()
        try:
            window._on_debug_workbench_action("halt")
            _pump(app, 0.15)
            rows = _diagnostics(tab)
            _assert(tab.debug_status.state == DebugRuntimeState.PAUSED, f"halt should pause target: {tab.debug_status!r}")
            _assert(rows.get("运行控制") == "暂停", f"halt runtime diagnostic mismatch: {rows!r}")
            _assert(rows.get("PC 证据") == "已回读", f"halt PC evidence mismatch: {rows!r}")
            _assert(rows.get("远端断点证据") == "halt-remote", f"halt remote snapshot mismatch: {rows!r}")
            _assert("PC 已回读" in tab.marker_label.text(), f"halt marker missing PC evidence: {tab.marker_label.text()!r}")
            verified_breakpoints = tab.local_breakpoints()
            _assert(verified_breakpoints[0].verified, f"halt snapshot should verify local breakpoint: {verified_breakpoints!r}")
            _assert(
                "Keil 快照已回读" in verified_breakpoints[0].message,
                f"halt breakpoint evidence message mismatch: {verified_breakpoints!r}",
            )

            window._on_debug_workbench_action("run")
            _pump(app, 0.15)
            rows = _diagnostics(tab)
            _assert(tab.debug_status.state == DebugRuntimeState.RUNNING, f"run should mark target running: {tab.debug_status!r}")
            _assert(rows.get("运行控制") == "运行", f"run runtime diagnostic mismatch: {rows!r}")
            _assert(rows.get("PC 证据") == "未验证", f"run PC evidence should be unverified: {rows!r}")
            _assert(rows.get("PC 说明") == "目标运行中，PC 位置暂不稳定", f"run PC detail mismatch: {rows!r}")
            _assert(rows.get("远端断点证据") == "run-remote", f"run remote snapshot mismatch: {rows!r}")

            window._on_debug_workbench_action("reset")
            _pump(app, 0.15)
            rows = _diagnostics(tab)
            _assert(tab.debug_status.state == DebugRuntimeState.PAUSED, f"reset should pause target: {tab.debug_status!r}")
            _assert(rows.get("运行控制") == "复位", f"reset runtime diagnostic mismatch: {rows!r}")
            _assert(rows.get("远端断点证据") == "reset-remote", f"reset remote snapshot mismatch: {rows!r}")

            window._on_debug_workbench_action("step")
            _pump(app, 0.15)
            rows = _diagnostics(tab)
            _assert(tab.debug_status.state == DebugRuntimeState.PAUSED, f"step should leave target paused: {tab.debug_status!r}")
            _assert(rows.get("运行控制") == "单步", f"step runtime diagnostic mismatch: {rows!r}")
            _assert(rows.get("PC 证据") == "已回读", f"step PC evidence mismatch: {rows!r}")
            _assert(rows.get("远端断点证据") == "step-remote", f"step remote snapshot mismatch: {rows!r}")
        finally:
            restore_confirmation()

        _assert(fake_backend.calls == ["halt", "run", "reset", "step"], f"runtime calls mismatch: {fake_backend.calls!r}")
        _assert(len(confirmations) == 4, f"confirmation count mismatch: {confirmations!r}")
        window.close()
        _pump(app, 0.1)

    print("PASS UI Keil runtime control probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
