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
from src.core.debug_snapshots import RemoteBreakpoint, RemoteBreakpointSnapshot  # noqa: E402
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
        snapshot = remote_snapshot_from_operations(
            request,
            complete=bool(request.remote_snapshot_complete),
            error="" if request.remote_snapshot_complete else "fake BL did not return breakpoint text",
        )
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
        warnings: list[tuple[str, str]] = []
        window._show_info = lambda _title, _message: None
        window._show_warning = lambda title, message: warnings.append((title, message))
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
        _assert(button.text() == "同步断点", f"sync breakpoint action label mismatch: {button.text()!r}")
        _assert("推送本地" in button.toolTip(), f"sync breakpoint tooltip should expose push-local mode: {button.toolTip()!r}")
        _assert(tab.breakpoint_editor_status.text() == "待同步", f"new local breakpoint should be marked pending: {tab.breakpoint_editor_status.text()!r}")
        _assert(tab.breakpoint_sync_command_label.text() == "命令 1", f"command plan chip mismatch: {tab.breakpoint_sync_command_label.text()!r}")
        _assert("UVSC_DBG_EXEC_CMD" in tab.breakpoint_sync_command_label.toolTip(), f"command plan tooltip mismatch: {tab.breakpoint_sync_command_label.toolTip()!r}")

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
        _assert(any(command.startswith("BS ") for command in fake_backend.session.commands), fake_backend.session.commands)

        rows = _diagnostics(tab)
        _assert(rows.get("断点同步") == "成功", f"sync diagnostics mismatch: {rows!r}")
        _assert(rows.get("断点同步模式") == "推送本地", f"sync mode mismatch: {rows!r}")
        _assert("BS " in rows.get("断点命令样例", ""), f"sync command sample missing: {rows!r}")
        _assert(rows.get("远端断点完整") == "否", f"remote breakpoint completeness mismatch: {rows!r}")
        _assert(rows.get("远端断点错误") == "fake BL did not return breakpoint text", f"remote breakpoint error mismatch: {rows!r}")
        breakpoint_rows = tab.local_breakpoints()
        _assert(any(not item.verified for item in breakpoint_rows), f"breakpoint should wait for remote readback: {breakpoint_rows!r}")
        _assert(
            any("等待断点列表回读" in item.message for item in breakpoint_rows),
            f"accepted-command evidence missing: {breakpoint_rows!r}",
        )
        _assert(tab.breakpoint_editor_status.text() == "待回读", f"accepted breakpoint should wait for readback: {tab.breakpoint_editor_status.text()!r}")
        audit_lines = (output_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        _assert(audit_lines, "breakpoint sync audit log missing")
        record = json.loads(audit_lines[-1])
        _assert(record.get("action") == "keil_breakpoint_sync", f"audit action mismatch: {record!r}")

        tab._breakpoints.set_condition(source_path, 5, "speed > 0")
        tab._refresh_breakpoint_views(select_path=source_path, select_line=5)
        window._debug_remote_breakpoint_snapshot = None
        window._sync_debug_command_preview()
        _assert("受限1" in tab.breakpoint_sync_command_label.text(), f"limited command chip mismatch: {tab.breakpoint_sync_command_label.text()!r}")
        limited_confirm_calls, restore_limited_confirmation = _patch_confirmation()
        try:
            window._on_debug_workbench_action("sync_breakpoints")
            _pump(app, 0.2)
        finally:
            restore_limited_confirmation()
        _assert(len(limited_confirm_calls) == 1, f"limited confirmation mismatch: {limited_confirm_calls!r}")
        limited_message = limited_confirm_calls[0]["message"]
        _assert("受限操作" in limited_message, limited_message)
        _assert("条件断点命令尚未验证" in limited_message, limited_message)
        limited_rows = _diagnostics(tab)
        _assert(limited_rows.get("断点同步") == "受限未执行", f"limited sync diagnostics mismatch: {limited_rows!r}")
        _assert(limited_rows.get("断点受限") == "1 未发送", f"limited sync count mismatch: {limited_rows!r}")
        _assert(warnings and warnings[-1][0] == "Keil 断点同步受限未执行", f"limited warning mismatch: {warnings!r}")
        tab._breakpoints.set_condition(source_path, 5, "")
        tab._refresh_breakpoint_views(select_path=source_path, select_line=5)
        warnings.clear()

        complete_snapshot = RemoteBreakpointSnapshot(
            schema_version=1,
            snapshot_id="ui-complete-breakpoints",
            project_path=project_path,
            target_name="DebugDemo",
            captured_at="2026-06-11T00:00:00+00:00",
            complete=True,
            breakpoints=(RemoteBreakpoint(path=source_path, line=5, enabled=True, remote_id="11"),),
            error="",
        )
        window._debug_remote_breakpoint_snapshot = complete_snapshot
        window._sync_debug_command_preview()
        second_confirm_calls, restore_second_confirmation = _patch_confirmation()
        try:
            window._on_debug_workbench_action("sync_breakpoints")
            _pump(app, 0.2)
        finally:
            restore_second_confirmation()
        _assert(len(second_confirm_calls) == 1, f"second confirmation mismatch: {second_confirm_calls!r}")
        _assert("推送本地断点" not in second_confirm_calls[0]["message"], second_confirm_calls[0]["message"])
        full_request = fake_backend.requests[-1]
        _assert(full_request.remote_snapshot_complete, "expected full-diff mode with complete remote snapshot")
        verified_rows = tab.local_breakpoints()
        _assert(
            any(item.line == 5 and item.verified and "id 11" in item.message for item in verified_rows),
            f"remote id evidence missing from verified breakpoint: {verified_rows!r}",
        )
        _assert(tab.breakpoint_editor_status.text() == "已验证", f"verified breakpoint chip mismatch: {tab.breakpoint_editor_status.text()!r}")
        _assert(tab.remote_breakpoint_count_label.text() == "远端 1", f"remote mirror count mismatch: {tab.remote_breakpoint_count_label.text()!r}")
        _assert(tab.remote_breakpoint_table.rowCount() == 1, f"remote mirror table row count mismatch: {tab.remote_breakpoint_table.rowCount()}")
        _assert(
            tab.remote_breakpoint_table.item(0, 0).text() == "11",
            f"remote mirror id mismatch: {tab.remote_breakpoint_table.item(0, 0).text()!r}",
        )

        window._debug_remote_breakpoint_snapshot = complete_snapshot
        tab._remove_breakpoint(source_path, 5)
        _pump(app, 0.1)
        clear_confirm_calls, restore_clear_confirmation = _patch_confirmation()
        try:
            window._on_debug_workbench_action("sync_breakpoints")
            _pump(app, 0.2)
        finally:
            restore_clear_confirmation()
        _assert(len(clear_confirm_calls) == 1, f"clear confirmation mismatch: {clear_confirm_calls!r}")
        _assert("删除：1" in clear_confirm_calls[0]["message"], clear_confirm_calls[0]["message"])
        clear_request = fake_backend.requests[-1]
        _assert(clear_request.remote_snapshot_complete, "clear-all should require a complete remote snapshot")
        _assert(any(command == "BK 11" for command in fake_backend.session.commands), fake_backend.session.commands)
        window.close()
        _pump(app, 0.1)

    print("PASS UI Keil breakpoint sync probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
