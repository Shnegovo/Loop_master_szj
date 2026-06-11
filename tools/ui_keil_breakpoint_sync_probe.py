"""Probe the Debug Workbench Keil breakpoint sync action with a fake backend."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_workbench import DebugCapabilities, DebugRuntimeState, make_debug_status  # noqa: E402
from src.core.keil.breakpoint_sync import (  # noqa: E402
    KeilBreakpointSyncResult,
    execute_keil_breakpoint_sync,
    remote_snapshot_from_operations,
)
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


class FakeSession:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute_command(self, command: str, *, echo: bool = False):
        self.commands.append(command)
        return "OK"


class FakeKeilBackend:
    def __init__(self) -> None:
        self.requests = []
        self.session = FakeSession()

    def sync_breakpoints(self, request):
        self.requests.append(request)
        snapshot = remote_snapshot_from_operations(request, complete=True)
        return execute_keil_breakpoint_sync(self.session, request, remote_snapshot=snapshot)


def _write_fixture(root: Path) -> Path:
    project_dir = root / "MDK-ARM"
    source_dir = root / "Core" / "Src"
    source_dir.mkdir(parents=True)
    project_dir.mkdir(parents=True)
    (source_dir / "main.c").write_text(
        "volatile int speed;\n"
        "int main(void)\n"
        "{\n"
        "    while (1) {\n"
        "        speed++;\n"
        "    }\n"
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

    with tempfile.TemporaryDirectory(prefix="loopmaster-ui-bp-sync-") as tmp:
        project_path = _write_fixture(Path(tmp))
        source_path = Path(tmp) / "Core" / "Src" / "main.c"
        output_dir = Path(tmp) / "probe-out"
        output_dir.mkdir()

        window = MainWindow()
        window._config_path = output_dir / "loopmaster.json"
        window._variable_write_audit_path = output_dir / "audit.jsonl"
        window._show_info = lambda _title, _message: None
        window._show_warning = lambda _title, _message: None
        window.resize(1360, 820)
        window.show()
        _pump(app, 0.2)

        tab = window._tab_debug_workbench
        tab.load_project(project_path)
        status = make_debug_status(
            state=DebugRuntimeState.RUNNING,
            backend="keil",
            detail="fake Keil running",
            project_path=project_path,
            target_name="DebugDemo",
            capabilities=DebugCapabilities(
                can_discover=True,
                can_attach=True,
                can_disconnect=True,
                can_read_variables=True,
                can_write_variables=True,
                can_halt=True,
                can_run=False,
                can_step=False,
                can_sync_breakpoints=True,
            ),
        )
        tab.set_debug_status(status, controls_ready=True)
        tab.add_breakpoint(5, path=source_path)
        fake_backend = FakeKeilBackend()
        window._debug_backend = fake_backend
        window._sync_debug_command_preview()
        _pump(app, 0.1)

        button = tab._action_buttons.get("sync_breakpoints")
        _assert(button is not None, "sync breakpoint action button missing")
        _assert(button.isEnabled(), "sync breakpoint action button should be enabled")

        confirm_calls, restore_confirmation = _patch_confirmation()
        try:
            window._on_debug_workbench_action("sync_breakpoints")
            _pump(app, 0.2)
        finally:
            restore_confirmation()

        _assert(len(confirm_calls) == 1, f"confirmation not called once: {confirm_calls!r}")
        _assert(fake_backend.requests, "fake backend did not receive sync request")
        request = fake_backend.requests[0]
        _assert(not request.remote_snapshot_complete, "expected push-local mode without a complete remote snapshot")
        _assert(any("BreakSet" in command for command in fake_backend.session.commands), fake_backend.session.commands)

        rows = _diagnostics(tab)
        _assert(rows.get("断点同步") == "成功", f"sync diagnostics mismatch: {rows!r}")
        _assert(rows.get("断点同步模式") == "推送本地", f"sync mode mismatch: {rows!r}")
        verification_texts = [
            tab.breakpoint_table.item(row, 4).text()
            for row in range(tab.breakpoint_table.rowCount())
            if tab.breakpoint_table.item(row, 4) is not None
        ]
        _assert(any("已验证" in text for text in verification_texts), f"breakpoint not verified: {verification_texts!r}")
        audit_lines = (output_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        _assert(audit_lines, "breakpoint sync audit log missing")
        record = json.loads(audit_lines[-1])
        _assert(record.get("action") == "keil_breakpoint_sync", f"audit action mismatch: {record!r}")
        window.close()
        _pump(app, 0.1)

    print("PASS UI Keil breakpoint sync probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
