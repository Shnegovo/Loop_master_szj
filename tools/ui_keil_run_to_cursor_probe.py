"""Probe the Debug Workbench Keil run-to-cursor UI flow with a fake backend."""

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

from src.core.debug_snapshots import DebugPcLocation, RemoteBreakpointSnapshot  # noqa: E402
from src.core.debug_workbench import DebugCapabilities, DebugRuntimeState, make_debug_status  # noqa: E402
from src.core.keil.run_to_cursor import KeilRunToCursorRequest, KeilRunToCursorResult  # noqa: E402
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


class FakeRunToCursorBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run_to_cursor(self, *, source_path, line, project_path=None, target_name="", timeout_s=5.0, reset_before_run=False):
        source_path = Path(source_path).expanduser().resolve()
        line = int(line)
        self.calls.append(
            {
                "source_path": str(source_path),
                "line": line,
                "project_path": str(project_path or ""),
                "target_name": str(target_name or ""),
                "timeout_s": float(timeout_s),
                "reset_before_run": bool(reset_before_run),
            }
        )
        request = KeilRunToCursorRequest(
            project_path=Path(project_path).expanduser().resolve() if project_path else None,
            target_name=str(target_name or ""),
            source_path=source_path,
            line=line,
            axf_path=None,
        )
        snapshot = RemoteBreakpointSnapshot(
            schema_version=1,
            snapshot_id="fake-run-to-cursor-breakpoints",
            project_path=request.project_path,
            target_name=request.target_name,
            captured_at="2026-06-11T00:00:00+00:00",
            complete=True,
            breakpoints=(),
        )
        pc = DebugPcLocation(
            path=source_path,
            line=line,
            address=0x08000164,
            source="keil_eval_pc",
            complete=True,
            message="fake PC 已回读；源码行精确映射",
        )
        return KeilRunToCursorResult(
            request=request,
            attempted=True,
            succeeded=True,
            address=0x08000164,
            resolved_line=line,
            address_exact=True,
            before_snapshot=snapshot,
            after_set_snapshot=snapshot,
            after_cleanup_snapshot=snapshot,
            hit_pc=pc,
            temp_remote_id="0",
            set_command="BS 0x08000164",
            cleanup_command="BK 0",
            run_summary="UVSOCK 运行成功，目标运行中",
            cleanup_succeeded=True,
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

    with tempfile.TemporaryDirectory(prefix="loopmaster-ui-run-cursor-") as tmp:
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
        tab.show_source_location(source_path, 5)
        status = make_debug_status(
            state=DebugRuntimeState.PAUSED,
            backend="keil",
            detail="fake Keil paused",
            project_path=project_path,
            target_name="DebugDemo",
            capabilities=DebugCapabilities(
                can_discover=True,
                can_disconnect=True,
                can_read_variables=True,
                can_write_variables=True,
                can_halt=False,
                can_run=True,
                can_reset=True,
                can_step=True,
                can_sync_breakpoints=True,
            ),
        )
        tab.set_debug_status(status, controls_ready=True)
        fake_backend = FakeRunToCursorBackend()
        window._debug_backend = fake_backend
        window._sync_debug_command_preview()
        _pump(app, 0.1)

        button = tab._action_buttons.get("run_to_cursor")
        _assert(button is not None, "run-to-cursor action button missing")
        _assert(button.isEnabled(), "run-to-cursor action button should be enabled while paused")
        _assert("光标" in button.toolTip(), f"run-to-cursor tooltip mismatch: {button.toolTip()!r}")
        transaction = next(
            (item for item in getattr(tab, "_command_transactions", ()) if item.kind.value == "run_to_cursor"),
            None,
        )
        _assert(transaction is not None, "run-to-cursor transaction missing")
        _assert("resolve_source_line_address" in " ".join(transaction.command_preview), "run-to-cursor preview missing source resolution")

        confirm_calls, restore_confirmation = _patch_confirmation()
        try:
            window._on_debug_workbench_action("run_to_cursor")
            _pump(app, 0.2)
        finally:
            restore_confirmation()

        _assert(len(confirm_calls) == 1, f"confirmation not called once: {confirm_calls!r}")
        _assert("main.c" in confirm_calls[0]["message"] and "行号：5" in confirm_calls[0]["message"], confirm_calls[0]["message"])
        _assert(fake_backend.calls and fake_backend.calls[0]["line"] == 5, f"fake backend call mismatch: {fake_backend.calls!r}")
        rows = _diagnostics(tab)
        _assert(rows.get("运行到光标") == "成功", f"run-to-cursor diagnostics mismatch: {rows!r}")
        _assert(rows.get("目标地址") == "0x08000164", f"target address diagnostic mismatch: {rows!r}")
        _assert(rows.get("临时断点") == "0", f"temporary breakpoint diagnostic mismatch: {rows!r}")
        _assert(rows.get("运行前断点数") == "0", f"before breakpoint count mismatch: {rows!r}")
        _assert(rows.get("设置后断点数") == "0", f"after-set breakpoint count mismatch: {rows!r}")
        _assert(rows.get("清理后断点数") == "0", f"after-cleanup breakpoint count mismatch: {rows!r}")
        _assert(rows.get("临时断点残留") == "否", f"temporary breakpoint leak diagnostic mismatch: {rows!r}")
        _assert(rows.get("远端断点证据") == "fake-run-to-cursor-breakpoints", f"remote breakpoint evidence missing: {rows!r}")
        _assert(rows.get("远端断点完整") == "是", f"remote breakpoint completeness mismatch: {rows!r}")
        _assert(rows.get("PC 证据") == "已回读", f"PC evidence diagnostic mismatch: {rows!r}")
        _assert("PC 已回读" in tab.marker_label.text(), f"marker label missing PC evidence: {tab.marker_label.text()!r}")
        tooltip = tab.editor.gutter_tooltip_for_line(5)
        _assert("当前 PC" in tooltip and "已回读" in tooltip, f"gutter PC tooltip mismatch: {tooltip!r}")
        audit_lines = (output_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        _assert(audit_lines, "run-to-cursor audit log missing")
        record = json.loads(audit_lines[-1])
        _assert(record.get("action") == "keil_run_to_cursor", f"audit action mismatch: {record!r}")
        window.close()
        _pump(app, 0.1)

    print("PASS UI Keil run-to-cursor probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
