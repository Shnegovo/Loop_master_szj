"""Screenshot probe for the read-only Debug Workbench workspace."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_workbench import (  # noqa: E402
    DebugCapabilities,
    DebugRuntimeState,
    default_debug_capabilities,
    debug_command_plans_for_status,
    make_debug_status,
    search_document,
)
from src.core.debug_backend import DebugBackendDiagnostic, DebugBackendSessionSnapshot  # noqa: E402
from src.core.debug_snapshots import DebugPcLocation  # noqa: E402
from src.core.debug_variable_access import DebugVariableReadResult, DebugVariableWriteResult  # noqa: E402
from src.core.keil.commands import (  # noqa: E402
    KeilBreakpointRemoteSnapshot,
    KeilRemoteBreakpoint,
    KeilCommandHistory,
    build_keil_debug_transactions,
    transaction_by_key,
)
from src.core.keil.live_write import KeilLiveVariableReadResult, KeilLiveVariableWriteResult, KeilResolvedVariable  # noqa: E402
from src.core.keil.profile import KeilBuildResult, make_keil_debug_profile  # noqa: E402
from src.core.keil.uvsock import UvscLaunchResult  # noqa: E402
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
            <File><FileName>pid.c</FileName><FileType>1</FileType><FilePath>..\\Core\\Src\\pid.c</FilePath></File>
            <File><FileName>pid.h</FileName><FileType>5</FileType><FilePath>..\\Core\\Inc\\pid.h</FilePath></File>
          </Files>
        </Group>
        <Group>
          <GroupName>Startup</GroupName>
          <Files>
            <File><FileName>startup.s</FileName><FileType>2</FileType><FilePath>startup.s</FilePath></File>
          </Files>
        </Group>
      </Groups>
    </Target>
  </Targets>
</Project>
"""


MAIN_C = """#include "pid.h"
#include <stdint.h>

volatile int32_t debug_setpoint = 1000;

typedef struct {
    float target;
    float feedback;
    float output;
} MotorLoop;

static MotorLoop g_speed_loop = {0};

void speed_control_step(float speed_feedback)
{
    float speed_target = 60.0f;
    float speed_error = speed_target - speed_feedback;
    g_speed_loop.target = speed_target;
    g_speed_loop.feedback = speed_feedback;
    if (speed_error > 12.0f) {
        g_speed_loop.output += 2.0f;
    }
    if (speed_error < -12.0f) {
        g_speed_loop.output -= 2.0f;
    }
    pid_update_speed(speed_target, speed_feedback);
    if (g_speed_loop.output > 100.0f) {
        g_speed_loop.output = 100.0f;
    }
    if (g_speed_loop.output < 0.0f) {
        g_speed_loop.output = 0.0f;
    }
}

int main(void)
{
    float speed = 0.0f;
    while (1) {
        speed_control_step(speed);
        speed += 0.5f;
        if (speed > 65.0f) {
            speed = 58.0f;
        }
    }
}
"""


def _pump(app: QApplication, seconds: float = 0.25) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _write_fixture(root: Path) -> Path:
    project_dir = root / "MDK-ARM"
    src_dir = root / "Core" / "Src"
    inc_dir = root / "Core" / "Inc"
    src_dir.mkdir(parents=True)
    inc_dir.mkdir(parents=True)
    project_dir.mkdir(parents=True)
    (src_dir / "main.c").write_text(MAIN_C, encoding="utf-8")
    (src_dir / "pid.c").write_text(
        "float pid_update_speed(float target, float feedback) {\n"
        "    return target - feedback;\n"
        "}\n",
        encoding="utf-8",
    )
    (inc_dir / "pid.h").write_text(
        "#pragma once\nfloat pid_update_speed(float target, float feedback);\n",
        encoding="utf-8",
    )
    (project_dir / "startup.s").write_text("; synthetic startup\n", encoding="utf-8")
    project_path = project_dir / "DebugDemo.uvprojx"
    project_path.write_text(PROJECT, encoding="utf-8")
    (root / "compile_commands.json").write_text(
        json.dumps(
            [
                {"directory": str(root), "command": "cc -c Core/Src/main.c", "file": "Core/Src/main.c"},
                {"directory": str(root), "command": "cc -c Core/Src/pid.c", "file": "Core/Src/pid.c"},
                {"directory": str(root), "command": "cc -c Core/Src/missing.c", "file": "Core/Src/missing.c"},
                {"directory": str(root), "command": "cc -c Core/Inc/pid.h", "file": "Core/Inc/pid.h"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "build").mkdir(exist_ok=True)
    (root / "build" / "DebugDemo.elf").write_bytes(b"\x7fELF")
    return project_path


def _image_stats(widget: QWidget) -> dict[str, float]:
    image = widget.grab().toImage().convertToFormat(QImage.Format_RGB32)
    width = image.width()
    height = image.height()
    step_x = max(1, width // 96)
    step_y = max(1, height // 72)
    colors: set[tuple[int, int, int]] = set()
    luminance_values: list[int] = []
    non_white = 0
    samples = 0
    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            color = image.pixelColor(x, y)
            r, g, b = color.red(), color.green(), color.blue()
            colors.add((r // 16, g // 16, b // 16))
            luminance = (299 * r + 587 * g + 114 * b) // 1000
            luminance_values.append(luminance)
            if not (r > 246 and g > 246 and b > 246):
                non_white += 1
            samples += 1
    return {
        "width": float(width),
        "height": float(height),
        "unique": float(len(colors)),
        "contrast": float(max(luminance_values) - min(luminance_values)) if luminance_values else 0.0,
        "non_white_ratio": float(non_white / max(1, samples)),
    }


def _check_non_blank(widget: QWidget, label: str) -> list[str]:
    stats = _image_stats(widget)
    issues: list[str] = []
    if stats["width"] < 300 or stats["height"] < 300:
        issues.append(f"{label}: widget too small {stats['width']:.0f}x{stats['height']:.0f}")
    if stats["unique"] < 14 or stats["contrast"] < 18 or stats["non_white_ratio"] < 0.02:
        issues.append(
            f"{label}: looks blank unique={stats['unique']:.0f} "
            f"contrast={stats['contrast']:.0f} non_white={stats['non_white_ratio']:.3f}"
        )
    return issues


def _plan_rows(tab) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for plan in getattr(tab, "_plan_rows", ()):
        rows[plan.title] = {
            "status": plan.status,
            "risk": tab._risk_label(plan),
            "tooltip": tab._plan_tooltip(plan),
        }
    return rows


def _row_for_line(tab, line: int) -> int:
    for row in range(tab.breakpoint_table.rowCount()):
        item = tab.breakpoint_table.item(row, 2)
        if item is not None and item.text() == str(line):
            return row
    return -1


def _assert_phrases(issues: list[str], text: str, phrases: tuple[str, ...], context: str) -> None:
    for phrase in phrases:
        if phrase not in text:
            issues.append(f"{context} missing {phrase}: {text!r}")


def _sync_command_transactions(
    tab,
    history: KeilCommandHistory | None = None,
    port: int = 4827,
    history_key: str | None = None,
) -> None:
    status = tab.debug_status
    transactions = build_keil_debug_transactions(
        status,
        debug_command_plans_for_status(status),
        port=port,
        project_path=status.project_path,
        target_name=status.target_name,
        breakpoints=tab.local_breakpoints(),
        source_paths=tab.local_source_paths(),
        remote_breakpoint_snapshot=getattr(tab, "_remote_breakpoint_snapshot", None),
        backend_snapshot=getattr(tab, "_debug_backend_snapshot_record", None),
        execution_gate=False,
    )
    tab.set_command_transactions(transactions)
    if history is not None:
        focused = transaction_by_key(transactions, history_key) if history_key else _focused_transaction(transactions)
        if focused is not None:
            history.record(focused, event="previewed", source="ui_probe")
        tab.set_command_history_entries(history.recent(limit=5))


def _patch_confirmation(ok: bool):
    original = gui_module.ask_pcl_confirmation

    def fake_confirm(*_args, **_kwargs):
        return ok

    gui_module.ask_pcl_confirmation = fake_confirm

    def restore() -> None:
        gui_module.ask_pcl_confirmation = original

    return restore


def _patch_input(value: str, ok: bool):
    original = gui_module.ask_pcl_text

    def fake_ask_text(*_args, **_kwargs):
        return value, ok

    gui_module.ask_pcl_text = fake_ask_text

    def restore() -> None:
        gui_module.ask_pcl_text = original

    return restore


def _focused_transaction(transactions):
    priority = (
        "attach",
        "halt",
        "run",
        "reset",
        "step",
        "step_over",
        "run_to_cursor",
        "sync_breakpoints",
        "write_variables",
        "disconnect",
        "discover",
    )
    ready = {transaction.kind.value for transaction in transactions if transaction.preconditions_met}
    for key in priority:
        if key in ready:
            return transaction_by_key(transactions, key)
    return transactions[0] if transactions else None


def _fake_keil_resolved(expression: str) -> KeilResolvedVariable:
    return KeilResolvedVariable(
        expression=expression,
        symbol=expression,
        address=0x20000008,
        size=4,
        type_name="int",
        source="ui-probe",
        ram_checked=True,
    )


class _FakeReadOnlyBackend:
    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.calls = 0
        self.profile_calls = 0
        self.build_calls = 0
        self.launch_calls = 0
        self.read_calls = 0
        self.write_calls = 0
        self.generic_read_calls = 0
        self.generic_write_calls = 0

    def debug_profile(
        self,
        *,
        project_path=None,
        target_name: str = "",
    ):
        self.profile_calls += 1
        return make_keil_debug_profile(
            root=Path("D:/Keil"),
            project_path=project_path or self.project_path,
            target_name=target_name,
            port=4827,
        )

    def build_project(
        self,
        *,
        project_path=None,
        target_name: str = "",
        timeout: float = 180.0,
    ):
        self.build_calls += 1
        profile = self.debug_profile(project_path=project_path or self.project_path, target_name=target_name)
        return KeilBuildResult(
            plan=profile.build_plan,
            attempted=True,
            succeeded=True,
            returncode=0,
            log_path=profile.build_plan.log_path,
            output_tail="fake build path: UI probe does not invoke uVision.com; 0 Error(s), 0 Warning(s).",
            axf_path=profile.axf_path,
            axf_exists=True,
        )

    def launch_uvsock(
        self,
        *,
        project_path=None,
        target_name: str = "",
    ):
        self.launch_calls += 1
        profile = self.debug_profile(project_path=project_path or self.project_path, target_name=target_name)
        return UvscLaunchResult(
            plan=profile.launch_plan,
            launched=True,
            pid=4242,
        )

    def write_live_variable(self, request, *, require_debug: bool = True):
        self.write_calls += 1
        new_raw = int(request.value_text, 0).to_bytes(4, "little", signed=True)
        return KeilLiveVariableWriteResult(
            attempted=True,
            written=True,
            expression=request.expression,
            value_text=request.value_text,
            method="memory",
            resolved=_fake_keil_resolved(request.expression),
            old_raw=(5000).to_bytes(4, "little", signed=True),
            new_raw=new_raw,
            readback_raw=new_raw,
            old_value="5000",
            readback_value=request.value_text,
        )

    def read_live_variable(self, request, *, require_debug: bool = True):
        self.read_calls += 1
        return KeilLiveVariableReadResult(
            attempted=True,
            read=True,
            expression=request.expression,
            method="memory",
            resolved=_fake_keil_resolved(request.expression),
            raw=(5000).to_bytes(4, "little", signed=True),
            value="5000",
        )

    def read_variable(self, request, *, require_debug: bool = True):
        self.generic_read_calls += 1
        self.read_calls += 1
        return DebugVariableReadResult(
            attempted=True,
            read=True,
            expression=request.expression,
            method="memory",
            backend="keil",
            resolved=None,
            raw=(5000).to_bytes(4, "little", signed=True),
            value="5000",
        )

    def write_variable(self, request, *, require_debug: bool = True):
        self.generic_write_calls += 1
        self.write_calls += 1
        new_raw = int(request.value_text, 0).to_bytes(4, "little", signed=True)
        return DebugVariableWriteResult(
            attempted=True,
            written=True,
            expression=request.expression,
            value_text=request.value_text,
            method="memory",
            backend="keil",
            resolved=None,
            old_raw=(5000).to_bytes(4, "little", signed=True),
            new_raw=new_raw,
            readback_raw=new_raw,
            old_value="5000",
            readback_value=request.value_text,
        )

    def read_only_session_snapshot(
        self,
        *,
        project_path=None,
        target_name: str = "",
        previous_status=None,
        attempt_connection: bool = True,
        query_status: bool = True,
        include_breakpoints: bool = True,
    ):
        self.calls += 1
        status = make_debug_status(
            state=DebugRuntimeState.RUNNING,
            backend="keil",
            detail="UVSOCK 一次性快照已读取，目标运行中",
            project_path=project_path or self.project_path,
            target_name=target_name,
            capabilities=DebugCapabilities(
                can_discover=True,
                can_attach=True,
                can_disconnect=False,
                can_read_variables=True,
                can_write_variables=False,
                can_halt=False,
                can_run=False,
                can_step=False,
                can_sync_breakpoints=False,
            ),
        )
        return DebugBackendSessionSnapshot(
            schema_version=1,
            backend=status.backend,
            adapter_name="Fake Keil / UVSOCK",
            snapshot_id="debug-backend-ui-fake",
            captured_at="2026-06-10T00:00:00+00:00",
            status=status,
            diagnostics=(
                DebugBackendDiagnostic("后端", "Keil / UVSOCK"),
                DebugBackendDiagnostic("模式", "只读快照"),
                DebugBackendDiagnostic("连接尝试", "是"),
                DebugBackendDiagnostic("连接结果", "已连接"),
                DebugBackendDiagnostic("目标运行", "运行中"),
                DebugBackendDiagnostic("连接错误", "--"),
            ),
            capabilities=(),
            read_only=True,
            connection_attempted=attempt_connection,
            connection_established=True,
            target_running=True,
            port=4827,
            project_path=Path(project_path or self.project_path),
            target_name=target_name,
            pc_location=DebugPcLocation(
                source="keil_uvsock",
                complete=False,
                message="Keil PC 位置读取尚未实现",
            ),
            remote_breakpoint_snapshot=KeilBreakpointRemoteSnapshot(
                schema_version=1,
                snapshot_id="keil-ui-fake-read-only-incomplete",
                project_path=Path(project_path or self.project_path),
                target_name=target_name,
                captured_at="2026-06-10T00:00:00+00:00",
                complete=False,
                breakpoints=(),
                error="Keil 只读快照尚未实现断点枚举解析",
            ),
            remote_breakpoint_snapshot_id="keil-ui-fake-read-only-incomplete",
        )


def _remote_snapshot(project_path: Path, source_dir: Path) -> KeilBreakpointRemoteSnapshot:
    return KeilBreakpointRemoteSnapshot(
        schema_version=1,
        snapshot_id="keil-ui-remote-breakpoint-snapshot-demo",
        project_path=project_path,
        target_name="DebugDemo",
        captured_at="2026-06-10T00:00:00+00:00",
        complete=True,
        breakpoints=(
            KeilRemoteBreakpoint(path=source_dir / "main.c", line=3, enabled=False, condition="speed > 80", remote_id="bp-1", raw_location=f"{source_dir / 'main.c'}:3"),
            KeilRemoteBreakpoint(path=source_dir / "main.c", line=24, enabled=True, condition="speed_error < -24", remote_id="bp-2", raw_location=f"{source_dir / 'main.c'}:24"),
            KeilRemoteBreakpoint(path=source_dir / "main.c", line=12, enabled=True, condition="", remote_id="bp-3", raw_location=f"{source_dir / 'main.c'}:12"),
            KeilRemoteBreakpoint(path=source_dir / "main.c", line=48, enabled=True, condition="", remote_id="bp-4", raw_location=f"{source_dir / 'main.c'}:48"),
            KeilRemoteBreakpoint(path=source_dir / "main.c", line=96, enabled=True, condition="", remote_id="bp-5", raw_location=f"{source_dir / 'main.c'}:96"),
        ),
        error="",
    )


def _save(window: MainWindow, output_dir: Path, name: str) -> Path:
    QApplication.processEvents()
    path = output_dir / f"{name}.png"
    if not window.grab().save(str(path)):
        raise RuntimeError(f"failed to save screenshot: {path}")
    return path


def _save_widget(widget: QWidget, output_dir: Path, name: str) -> Path:
    QApplication.processEvents()
    path = output_dir / f"{name}.png"
    if not widget.grab().save(str(path)):
        raise RuntimeError(f"failed to save widget screenshot: {path}")
    return path


def _diagnostics(tab) -> dict[str, str]:
    return {
        tab.diagnostics_table.item(row, 0).text(): tab.diagnostics_table.item(row, 1).text()
        for row in range(tab.diagnostics_table.rowCount())
        if tab.diagnostics_table.item(row, 0) is not None and tab.diagnostics_table.item(row, 1) is not None
    }


def _source_tree_texts(tab) -> list[str]:
    texts: list[str] = []
    for index in range(tab.source_tree.topLevelItemCount()):
        group = tab.source_tree.topLevelItem(index)
        texts.append(group.text(0))
        for child_index in range(group.childCount()):
            texts.append(group.child(child_index).text(0))
    return texts


def run(output_dir: Path, width: int, height: int) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_pcl_theme(app)
    pg.setConfigOptions(
        background=(255, 255, 255),
        foreground=(83, 101, 125),
        antialias=False,
    )

    screenshots: list[Path] = []
    issues: list[str] = []
    history = KeilCommandHistory(max_entries=5)
    with tempfile.TemporaryDirectory(prefix="loopmaster-debug-ui-") as tmp:
        project_path = _write_fixture(Path(tmp))
        window = MainWindow()
        window._config_path = output_dir / "probe-loopmaster.json"
        window._probe_infos = []
        window._probe_warnings = []
        window._show_info = lambda title, message: window._probe_infos.append((title, message))
        window._show_warning = lambda title, message: window._probe_warnings.append((title, message))
        window._scope_read_source = "swd"
        window._collector.stop(timeout=0.2)
        window._collector.set_backend(window._backend)
        window._refresh_debug_scope_acquisition_status()
        window._refresh_debug_workbench_diagnostics()
        window.resize(width, height)
        window.show()
        _pump(app, 0.35)

        window._show_workspace_page("debug_sources")
        tab = window._tab_debug_workbench
        tab.load_project(project_path)
        window._elf_path = project_path.parent.parent / "build" / "DebugDemo.elf"
        window._recent_elf_path = window._elf_path
        provider_labels = [
            tab.source_provider_combo.itemText(index)
            for index in range(tab.source_provider_combo.count())
        ] if hasattr(tab, "source_provider_combo") else []
        for label in ("自动", "Keil 工程", "编译数据库", "源码根", "ELF/DWARF", "GDB 文本"):
            if label not in provider_labels:
                issues.append(f"source provider selector missing {label}: {provider_labels!r}")
        if "Keil 工程" not in tab.source_provider_state_label.text() or "4 文件" not in tab.source_provider_count_label.text():
            issues.append(
                f"Keil source provider chips mismatch: "
                f"{tab.source_provider_state_label.text()!r} / {tab.source_provider_count_label.text()!r}"
            )
        boundary_labels = [
            getattr(getattr(tab, attr, None), "text", lambda: "")()
            for attr in ("boundary_backend_label", "boundary_source_label", "boundary_scope_label")
        ]
        boundary_text = " / ".join(boundary_labels)
        if "调试 Keil" not in boundary_text or "源码 Keil 工程" not in boundary_text or "示波" not in boundary_text:
            issues.append(f"mode boundary strip mismatch: {boundary_text!r}")
        live_loop_text = " / ".join(
            chip.text()
            for chip in getattr(tab, "live_loop_chips", [])
        )
        for phrase in ("会话", "PC", "断点", "写入", "示波"):
            if phrase not in live_loop_text:
                issues.append(f"live loop strip missing {phrase}: {live_loop_text!r}")
        scope_source_labels = [
            tab.scope_source_combo.itemText(index)
            for index in range(tab.scope_source_combo.count())
        ] if hasattr(tab, "scope_source_combo") else []
        for label in ("SWD 内存", "Keil Watch", "串口波形", "OpenOCD/GDB", "pyOCD", "TI MSPM0G3507"):
            if not any(label in item for item in scope_source_labels):
                issues.append(f"scope source selector missing {label}: {scope_source_labels!r}")
        scope_source_key = tab.scope_source_combo.currentData() if hasattr(tab, "scope_source_combo") else ""
        if scope_source_key != "swd":
            issues.append(f"scope source selector should default to SWD: {scope_source_key!r}")
        preset_rows = [
            tab.variable_preset_table.item(row, 0).text()
            for row in range(tab.variable_preset_table.rowCount())
            if tab.variable_preset_table.item(row, 0) is not None
        ] if hasattr(tab, "variable_preset_table") else []
        if "debug_setpoint" not in preset_rows:
            issues.append(f"variable preset table missing debug_setpoint: {preset_rows!r}")
        if getattr(tab, "variable_preset_write_button", None) is None or not tab.variable_preset_write_button.isEnabled():
            issues.append("variable preset write button should be enabled for F401-style fixture")
        backend_labels = [
            tab.backend_combo.itemText(index)
            for index in range(tab.backend_combo.count())
        ] if hasattr(tab, "backend_combo") else []
        for label in ("Keil / UVSOCK", "OpenOCD / GDB", "pyOCD", "TI MSPM0G3507", "离线回放"):
            if label not in backend_labels:
                issues.append(f"backend selector missing {label}: {backend_labels!r}")
        if hasattr(tab, "backend_combo"):
            openocd_index = tab.backend_combo.findData("openocd_gdb")
            if openocd_index < 0:
                issues.append("backend selector missing openocd_gdb data")
            else:
                tab.backend_combo.setCurrentIndex(openocd_index)
                _pump(app, 0.15)
                if getattr(window, "_debug_backend_kind", None).value != "openocd_gdb":
                    issues.append(f"backend selector did not switch main window: {getattr(window, '_debug_backend_kind', None)!r}")
                if "OpenOCD / GDB" not in tab.status_text.text():
                    issues.append(f"OpenOCD placeholder status mismatch: {tab.status_text.text()!r}")
                source_manifest = getattr(tab, "source_manifest", None)
                if source_manifest is None or not source_manifest.provider.endswith("_preview"):
                    issues.append(f"OpenOCD source manifest preview missing: {source_manifest!r}")
                elif source_manifest.source_count < 4:
                    issues.append(f"OpenOCD source preview should reuse Keil sources: {source_manifest.source_count}")
                if tab.source_tree.topLevelItemCount() < 2:
                    issues.append(f"OpenOCD source preview tree missing groups: {tab.source_tree.topLevelItemCount()}")
                if "OpenOCD / GDB 复用源码预览" not in tab.summary_label.text():
                    issues.append(f"OpenOCD summary did not show source preview: {tab.summary_label.text()!r}")
                if "preview" not in tab.source_provider_state_label.text().lower() and "预览" not in tab.summary_label.text():
                    issues.append(f"OpenOCD source provider chip did not reflect preview: {tab.source_provider_state_label.text()!r}")
                diag = _diagnostics(tab)
                if diag.get("源码文件") != str(source_manifest.source_count):
                    issues.append(f"OpenOCD source diagnostics missing source rows: {diag!r}")
                if diag.get("后端") != "OpenOCD / GDB" or "尚未接入" not in diag.get("状态", ""):
                    issues.append(f"OpenOCD placeholder diagnostics mismatch: {diag!r}")
                if diag.get("示波采集源") != "SWD 内存" or "非/轻侵入式" not in diag.get("采集侵入性", ""):
                    issues.append(f"scope acquisition diagnostics mismatch: {diag!r}")
                if diag.get("采集模式") != "非/轻侵入式" or diag.get("调试接管") != "不接管调试链":
                    issues.append(f"scope acquisition mode boundary mismatch: {diag!r}")
                if diag.get("采集批次来源") != "swd" or diag.get("采集批次样本") != "0":
                    issues.append(f"empty acquisition batch diagnostics mismatch: {diag!r}")
                compile_index = tab.source_provider_combo.findData("compile_commands")
                if compile_index < 0:
                    issues.append("source provider selector missing compile_commands data")
                else:
                    tab.source_provider_combo.setCurrentIndex(compile_index)
                    _pump(app, 0.15)
                    compile_manifest = getattr(tab, "source_manifest", None)
                    if compile_manifest is None or compile_manifest.provider != "compile_commands":
                        issues.append(f"compile_commands source preview mismatch: {compile_manifest!r}")
                    elif compile_manifest.source_count != 4:
                        issues.append(f"compile_commands source count mismatch: {compile_manifest.source_count}")
                    if "编译数据库" not in tab.source_provider_state_label.text():
                        issues.append(f"compile_commands source chip mismatch: {tab.source_provider_state_label.text()!r}")
                    diag = _diagnostics(tab)
                    if diag.get("源码来源") != "编译数据库" or diag.get("源码文件") != "4" or diag.get("源码缺失") != "1":
                        issues.append(f"compile_commands source diagnostics mismatch: {diag!r}")
                    if "缺失 1" not in tab.source_provider_missing_label.text():
                        issues.append(f"compile_commands missing chip mismatch: {tab.source_provider_missing_label.text()!r}")
                    if not any("(缺失)" in text for text in _source_tree_texts(tab)):
                        issues.append(f"compile_commands source tree should show missing node: {_source_tree_texts(tab)!r}")
                roots_index = tab.source_provider_combo.findData("manual_roots")
                if roots_index < 0:
                    issues.append("source provider selector missing manual_roots data")
                else:
                    tab.source_provider_combo.setCurrentIndex(roots_index)
                    _pump(app, 0.15)
                    roots_manifest = getattr(tab, "source_manifest", None)
                    if roots_manifest is None or not roots_manifest.provider.endswith("_roots_preview"):
                        issues.append(f"manual roots source preview mismatch: {roots_manifest!r}")
                    elif roots_manifest.source_count < 4:
                        issues.append(f"manual roots source count mismatch: {roots_manifest.source_count}")
                    if "源码根" not in tab.source_provider_state_label.text() and "roots preview" not in tab.source_provider_state_label.text():
                        issues.append(f"manual roots source chip mismatch: {tab.source_provider_state_label.text()!r}")
                gdb_text = (
                    "Source files for which symbols have been read in:\n\n"
                    "Core/Src/main.c, Core/Src/missing_gdb.c, Core/Src/ignore.txt, Core/Src/main.c\n"
                )
                gdb_manifest = window.configure_debug_gdb_sources_text(gdb_text, root=project_path.parent.parent)
                _pump(app, 0.15)
                if gdb_manifest.provider != "gdb_info_sources" or gdb_manifest.source_count != 2:
                    issues.append(f"explicit GDB text manifest mismatch: {gdb_manifest!r}")
                diag = _diagnostics(tab)
                if diag.get("源码来源") != "GDB 源码表" or diag.get("源码缺失") != "1" or diag.get("源码重复") != "1" or diag.get("源码过滤") != "1":
                    issues.append(f"explicit GDB diagnostics mismatch: {diag!r}")
                if "缺失 1" not in tab.source_provider_missing_label.text():
                    issues.append(f"explicit GDB missing chip mismatch: {tab.source_provider_missing_label.text()!r}")
                dwarf_text = f"""
Raw dump of debug contents of section .debug_line:

  The Directory Table:
  0     {project_path.parent.parent}
  1     Core/Src

  The File Name Table:
  Entry Dir Name
  0     1   main.c
  1     1   missing_dwarf.c
  2     1   ignore.txt

  Line Number Statements:
"""
                dwarf_manifest = window.configure_debug_dwarf_line_table_text(
                    dwarf_text,
                    elf_path=project_path.parent.parent / "build" / "DebugDemo.elf",
                    source_roots=(project_path.parent.parent,),
                )
                _pump(app, 0.15)
                if dwarf_manifest.provider != "elf_dwarf" or dwarf_manifest.source_count != 2:
                    issues.append(f"explicit DWARF text manifest mismatch: {dwarf_manifest!r}")
                diag = _diagnostics(tab)
                if diag.get("源码来源") != "ELF/DWARF" or diag.get("源码缺失") != "1" or diag.get("源码过滤") != "1":
                    issues.append(f"explicit DWARF diagnostics mismatch: {diag!r}")
                auto_index = tab.source_provider_combo.findData("auto")
                if auto_index >= 0:
                    tab.source_provider_combo.setCurrentIndex(auto_index)
                    _pump(app, 0.15)
                window._discover_debug_backend_for_workbench()
                _pump(app, 0.15)
                placeholder_transactions = getattr(tab, "_command_transactions", ())
                if not placeholder_transactions or getattr(placeholder_transactions[0], "backend", "") != "openocd_gdb":
                    issues.append(f"OpenOCD placeholder transactions missing: {placeholder_transactions!r}")
                placeholder_tip = tab.plan_guard_label.toolTip()
                if "OpenOCD / GDB 后端尚未接入执行器" not in placeholder_tip:
                    issues.append(f"OpenOCD placeholder tooltip missing blocked reason: {placeholder_tip!r}")
                placeholder_history_tip = tab.plan_history_label.toolTip()
                if "openocd_gdb" not in placeholder_history_tip or "后端尚未接入执行器" not in placeholder_history_tip:
                    issues.append(f"OpenOCD placeholder history missing generic entry: {placeholder_history_tip!r}")
                unsafe_actions = [
                    key
                    for key in ("halt", "run", "reset", "step", "step_over", "run_to_cursor", "sync_breakpoints", "write_variables")
                    if getattr(tab, "_action_buttons", {}).get(key) is not None
                    and getattr(tab, "_action_buttons", {})[key].isEnabled()
                ]
                if unsafe_actions:
                    issues.append(f"OpenOCD placeholder enabled dangerous actions: {unsafe_actions!r}")
                ti_index = tab.backend_combo.findData("ti_mspm0")
                if ti_index < 0:
                    issues.append("backend selector missing ti_mspm0 data")
                else:
                    tab.backend_combo.setCurrentIndex(ti_index)
                    _pump(app, 0.15)
                    if getattr(window, "_debug_backend_kind", None).value != "ti_mspm0":
                        issues.append(f"TI backend selector did not switch main window: {getattr(window, '_debug_backend_kind', None)!r}")
                    if "TI MSPM0G3507" not in tab.status_text.text():
                        issues.append(f"TI placeholder status mismatch: {tab.status_text.text()!r}")
                    window._discover_debug_backend_for_workbench()
                    _pump(app, 0.15)
                    diag = _diagnostics(tab)
                    if diag.get("后端") != "TI MSPM0G3507" or diag.get("工具链阶段") != "计划中":
                        issues.append(f"TI placeholder diagnostics mismatch: {diag!r}")
                    if "MSPM0G3507" not in diag.get("适配目标", ""):
                        issues.append(f"TI placeholder target mismatch: {diag!r}")
                    safety = diag.get("安全边界", "")
                    if "不连接探针" not in safety or "不写目标" not in safety:
                        issues.append(f"TI placeholder safety boundary mismatch: {diag!r}")
                    ti_transactions = getattr(tab, "_command_transactions", ())
                    if not ti_transactions or getattr(ti_transactions[0], "backend", "") != "ti_mspm0":
                        issues.append(f"TI placeholder transactions missing: {ti_transactions!r}")
                    unsafe_actions = [
                        key
                        for key in ("halt", "run", "reset", "step", "step_over", "run_to_cursor", "sync_breakpoints", "write_variables")
                        if getattr(tab, "_action_buttons", {}).get(key) is not None
                        and getattr(tab, "_action_buttons", {})[key].isEnabled()
                    ]
                    if unsafe_actions:
                        issues.append(f"TI placeholder enabled dangerous actions: {unsafe_actions!r}")
                keil_index = tab.backend_combo.findData("keil")
                if keil_index >= 0:
                    tab.backend_combo.setCurrentIndex(keil_index)
                    _pump(app, 0.15)
                    restored_manifest = getattr(tab, "source_manifest", None)
                    if restored_manifest is None or restored_manifest.provider != "keil":
                        issues.append(f"Keil source manifest was not restored: {restored_manifest!r}")
                    if "DebugDemo.uvprojx" not in tab.summary_label.text():
                        issues.append(f"Keil summary was not restored: {tab.summary_label.text()!r}")
        source_dir = project_path.parent.parent / "Core" / "Src"
        remote_snapshot = _remote_snapshot(project_path, source_dir)
        tab.set_remote_breakpoint_snapshot(remote_snapshot)
        window._debug_remote_breakpoint_snapshot = remote_snapshot
        discover_button = getattr(tab, "_action_buttons", {}).get("discover")
        if discover_button is None or not discover_button.isEnabled():
            issues.append("discover action should be enabled when debug workbench controller is wired")
        window._discover_keil_for_debug_workbench()
        _pump(app, 0.15)
        if "正在发现" in tab.status_text.text():
            issues.append(f"discover preflight left the UI busy: {tab.status_text.text()!r}")
        if discover_button is None or not discover_button.isEnabled():
            issues.append("discover action should remain available after no-hardware preflight")
        diagnostic_keys = {
            tab.diagnostics_table.item(row, 0).text()
            for row in range(tab.diagnostics_table.rowCount())
            if tab.diagnostics_table.item(row, 0) is not None
        }
        for key in ("Keil 根目录", "UVSOCK DLL", "UVSOCK 端口", "启动命令"):
            if key not in diagnostic_keys:
                issues.append(f"diagnostics table missing {key}: {sorted(diagnostic_keys)!r}")
        diag = _diagnostics(tab)
        for key in ("Keil 档案", "AXF", "AXF 状态", "构建命令", "构建状态", "启动命令", "启动状态"):
            if key not in diag:
                issues.append(f"profile diagnostics table missing {key}: {diag!r}")
        for key, expected in (("Keil 档案", "可用"), ("AXF 状态", "未生成"), ("构建状态", "可构建"), ("启动状态", "可启动")):
            if diag.get(key) != expected:
                issues.append(f"profile diagnostic mismatch {key}: {diag!r}")
        for key in ("build_project", "launch_uvsock", "auto_debug"):
            button = getattr(tab, "_action_buttons", {}).get(key)
            if button is None or not button.isEnabled():
                issues.append(f"{key} action should be enabled after Keil profile discovery")
            elif "调试档案" not in button.toolTip() or "显式" not in button.toolTip():
                issues.append(f"{key} tooltip should describe explicit profile action: {button.toolTip()!r}")
        fake_backend = _FakeReadOnlyBackend(project_path)
        window._debug_backend = fake_backend
        attach_button = getattr(tab, "_action_buttons", {}).get("attach")
        if attach_button is None:
            issues.append("attach action button missing")
        else:
            attach_button.setEnabled(True)
            attach_button.click()
            _pump(app, 0.15)
            if fake_backend.calls != 1:
                issues.append(f"attach action did not request one read-only snapshot: {fake_backend.calls}")
            backend_record = getattr(window, "_debug_backend_snapshot_record", None)
            tab._debug_backend_snapshot_record = backend_record
            if not backend_record or backend_record.get("snapshot_id") != "debug-backend-ui-fake":
                issues.append(f"read-only attach backend snapshot evidence missing: {backend_record!r}")
            if "一次性快照" not in tab.status_text.text() or "目标运行中" not in tab.status_text.text():
                issues.append(f"read-only attach status mismatch: {tab.status_text.text()!r}")
            diag = {
                tab.diagnostics_table.item(row, 0).text(): tab.diagnostics_table.item(row, 1).text()
                for row in range(tab.diagnostics_table.rowCount())
                if tab.diagnostics_table.item(row, 0) is not None and tab.diagnostics_table.item(row, 1) is not None
            }
            for key, expected in (("模式", "只读快照"), ("连接尝试", "是"), ("连接结果", "已连接"), ("目标运行", "运行中")):
                if diag.get(key) != expected:
                    issues.append(f"read-only attach diagnostic mismatch {key}: {diag!r}")
            for key, expected in (
                ("PC 证据", "未验证"),
                ("PC 来源", "keil_uvsock"),
                ("PC 说明", "Keil PC 位置读取尚未实现"),
                ("远端断点证据", "keil-ui-fake-read-only-incomplete"),
                ("远端断点完整", "否"),
                ("远端断点计数", "0"),
                ("远端断点错误", "Keil 只读快照尚未实现断点枚举解析"),
            ):
                if diag.get(key) != expected:
                    issues.append(f"read-only attach evidence diagnostic mismatch {key}: {diag!r}")
            for key in ("Keil 档案", "AXF", "AXF 状态", "构建命令", "构建状态", "启动命令", "启动状态"):
                if key not in diag:
                    issues.append(f"read-only attach profile diagnostic missing {key}: {diag!r}")
            for key in ("build_project", "launch_uvsock", "auto_debug"):
                button = getattr(tab, "_action_buttons", {}).get(key)
                if button is None or not button.isEnabled():
                    issues.append(f"read-only attach should keep {key} action enabled")
            blocked_actions = [
                key
                for key in ("run", "step", "step_over", "run_to_cursor")
                if getattr(tab, "_action_buttons", {}).get(key) is not None
                and getattr(tab, "_action_buttons", {})[key].isEnabled()
            ]
            if blocked_actions:
                issues.append(f"read-only attach enabled dangerous actions: {blocked_actions!r}")
            sync_button = getattr(tab, "_action_buttons", {}).get("sync_breakpoints")
            if sync_button is None or not sync_button.isEnabled():
                issues.append("read-only attach should expose explicit Keil breakpoint sync action")
            elif sync_button.text() != "同步断点":
                issues.append(f"Keil breakpoint sync button label mismatch: {sync_button.text()!r}")
            elif "确认" not in sync_button.toolTip() and "显式" not in sync_button.toolTip():
                issues.append(f"Keil breakpoint sync tooltip should mention explicit confirmation: {sync_button.toolTip()!r}")
            remote_refresh = getattr(tab, "remote_breakpoint_refresh_button", None)
            if remote_refresh is None:
                issues.append("remote breakpoint refresh button missing")
            else:
                remote_refresh.click()
                _pump(app, 0.15)
                if fake_backend.calls != 2:
                    issues.append(f"remote breakpoint refresh should request a second read-only snapshot: {fake_backend.calls}")
                if tab.remote_breakpoint_state_label.text() != "快照 未完整":
                    issues.append(f"remote breakpoint refresh state mismatch: {tab.remote_breakpoint_state_label.text()!r}")
                if tab.remote_breakpoint_count_label.text() != "远端 0":
                    issues.append(f"remote breakpoint refresh count mismatch: {tab.remote_breakpoint_count_label.text()!r}")
            halt_button = getattr(tab, "_action_buttons", {}).get("halt")
            if halt_button is None or not halt_button.isEnabled():
                issues.append("read-only attach should expose explicit Keil halt action while target is running")
            elif "UVSOCK" not in halt_button.toolTip() or ("确认" not in halt_button.toolTip() and "显式" not in halt_button.toolTip()):
                issues.append(f"Keil halt action tooltip should mention explicit confirmation: {halt_button.toolTip()!r}")
            reset_button = getattr(tab, "_action_buttons", {}).get("reset")
            if reset_button is None or not reset_button.isEnabled():
                issues.append("read-only attach should expose explicit Keil reset action while target is attached")
            elif "UVSOCK" not in reset_button.toolTip() or ("确认" not in reset_button.toolTip() and "显式" not in reset_button.toolTip()):
                issues.append(f"Keil reset action tooltip should mention explicit confirmation: {reset_button.toolTip()!r}")
            write_button = getattr(tab, "_action_buttons", {}).get("write_variables")
            if write_button is None or not write_button.isEnabled():
                issues.append("read-only attach should expose explicit Keil live write action")
            elif "确认" not in write_button.toolTip() and "显式" not in write_button.toolTip():
                issues.append(f"Keil live write action tooltip should mention explicit confirmation: {write_button.toolTip()!r}")
            preset_write = getattr(tab, "variable_preset_write_button", None)
            if preset_write is None or not preset_write.isEnabled():
                issues.append("variable preset write button should remain enabled after attach")
            else:
                tab.variable_preset_table.selectRow(0)
                restore_text = _patch_input("debug_setpoint\n6000", True)
                restore_confirm = _patch_confirmation(True)
                try:
                    preset_write.click()
                    _pump(app, 0.15)
                finally:
                    restore_confirm()
                    restore_text()
                if fake_backend.write_calls != 1:
                    issues.append(f"preset write should request one fake write: {fake_backend.write_calls}")
                if fake_backend.read_calls != 1:
                    issues.append(f"preset write should request one fake baseline read: {fake_backend.read_calls}")
                if fake_backend.generic_read_calls != 1 or fake_backend.generic_write_calls != 1:
                    issues.append(
                        "preset write should use generic variable access: "
                        f"read={fake_backend.generic_read_calls} write={fake_backend.generic_write_calls}"
                    )
                last_read = getattr(window, "_debug_last_live_read_result", None)
                if last_read is None or not last_read.read or last_read.value != "5000":
                    issues.append(f"preset write baseline read mismatch: {last_read!r}")
                last_write = getattr(window, "_debug_last_live_write_result", None)
                if last_write is None or not last_write.written or last_write.expression != "debug_setpoint":
                    issues.append(f"preset write result mismatch: {last_write!r}")
                write_diag = _diagnostics(tab)
                if write_diag.get("写前读取结果") != "成功" or write_diag.get("写前基线值") != "5000":
                    issues.append(f"preset write baseline diagnostics mismatch: {write_diag!r}")
            sync_transaction = transaction_by_key(getattr(tab, "_command_transactions", ()), "sync_breakpoints")
            sync_text = " ".join(sync_transaction.command_preview + sync_transaction.blocked_reasons) if sync_transaction is not None else ""
            if "mode=push_local_only" not in sync_text and "只推送本地断点" not in sync_text:
                issues.append(f"read-only attach should use push-local breakpoint sync mode: {sync_text!r}")
            if sync_transaction is None or sync_transaction.backend_snapshot_id != "debug-backend-ui-fake":
                issues.append(f"read-only attach sync transaction should retain backend snapshot id: {sync_transaction!r}")
            elif "debug-backend-ui-fake" not in tab.plan_guard_label.toolTip() or "后端快照" not in tab.plan_guard_label.toolTip():
                issues.append(f"read-only attach transaction tooltip missing backend snapshot evidence: {tab.plan_guard_label.toolTip()!r}")
            auto_button = getattr(tab, "_action_buttons", {}).get("auto_debug")
            if auto_button is None:
                issues.append("auto debug action button missing")
            else:
                restore_confirm = _patch_confirmation(True)
                try:
                    auto_button.click()
                    _pump(app, 0.15)
                finally:
                    restore_confirm()
                if fake_backend.build_calls != 1:
                    issues.append(f"auto debug should request one fake build: {fake_backend.build_calls}")
                if fake_backend.launch_calls != 0:
                    issues.append(f"auto debug should reuse fake connection instead of launching: {fake_backend.launch_calls}")
                if fake_backend.write_calls != 2:
                    issues.append(f"auto debug should request a second fake write: {fake_backend.write_calls}")
                if fake_backend.read_calls != 2:
                    issues.append(f"auto debug should request a second fake read: {fake_backend.read_calls}")
                auto_result = getattr(window, "_debug_keil_auto_debug_result", None)
                if auto_result is None or not auto_result.succeeded:
                    issues.append(f"auto debug result missing or failed: {auto_result!r}")
                elif auto_result.read is None or not auto_result.read.read or auto_result.read.value != "5000":
                    issues.append(f"auto debug read-before-write result mismatch: {auto_result.read!r}")
                elif getattr(window, "_debug_last_live_read_result", None) is not auto_result.read:
                    issues.append("auto debug should publish its strict smoke read as the latest baseline")
                auto_diag = _diagnostics(tab)
                if (
                    auto_diag.get("自动调试") != "成功"
                    or auto_diag.get("自动写入前结果") != "成功"
                    or auto_diag.get("自动写入前回读") != "5000"
                    or auto_diag.get("自动写入结果") != "成功"
                ):
                    issues.append(f"auto debug diagnostics mismatch: {auto_diag!r}")
        tab.search_edit.setText("speed")
        if not tab.search_next_button.isEnabled():
            issues.append("search next button should be enabled after a query with matches")
        tab.search_next_button.click()
        _pump(app, 0.1)
        if getattr(tab, "_active_search_line", None) is None:
            issues.append("search navigation did not activate a match")
        if "搜索 1/" not in tab.marker_label.text():
            issues.append(f"search navigation did not show active index: {tab.marker_label.text()!r}")
        if tab.source_tree.currentItem() is None or "main.c" not in tab.source_tree.currentItem().text(0):
            issues.append("source tree did not select the current source file")
        tab._toggle_breakpoint(96)
        _pump(app, 0.1)
        row96 = _row_for_line(tab, 96)
        if row96 < 0:
            issues.append("gutter-created breakpoint line 96 was not added")
        elif tab.breakpoint_table.currentRow() != row96 or "main.c:96" not in tab.breakpoint_editor_label.text():
            issues.append(
                f"gutter-created breakpoint was not auto-selected: row={tab.breakpoint_table.currentRow()} "
                f"label={tab.breakpoint_editor_label.text()!r}"
            )
        tab._toggle_breakpoint(96)
        _pump(app, 0.1)
        if _row_for_line(tab, 96) >= 0:
            issues.append("gutter second toggle did not remove line 96 breakpoint")
        tab.add_breakpoint(3, condition="speed > 60")
        tab.add_breakpoint(12, condition="speed_error > 40")
        tab.add_breakpoint(24, enabled=False, condition="speed_error < -12")
        tab.add_breakpoint(48)
        tab.set_breakpoint_verification(3, verified=True)
        tab.set_breakpoint_verification(24, verified=False, message="Keil 未回读到该断点")
        tab._scroll_editor_to_line(31)
        if getattr(tab, "current_line_breakpoint_button", None) is None:
            issues.append("current-line breakpoint button missing")
        elif not tab.current_line_breakpoint_button.isEnabled() or tab.current_line_breakpoint_button.text() != "当前行断点":
            issues.append(
                f"current-line breakpoint button initial state mismatch: "
                f"{tab.current_line_breakpoint_button.isEnabled()} / {tab.current_line_breakpoint_button.text()!r}"
            )
        else:
            tab.current_line_breakpoint_button.click()
        _pump(app, 0.1)
        if getattr(tab, "current_line_breakpoint_button", None) is not None and tab.current_line_breakpoint_button.text() != "移除断点":
            issues.append(f"current-line breakpoint button did not switch to remove: {tab.current_line_breakpoint_button.text()!r}")
        tab.current_line_condition_button.click()
        _pump(app, 0.1)
        row31 = _row_for_line(tab, 31)
        if row31 < 0:
            issues.append("current-line breakpoint button did not create line 31 breakpoint")
        elif tab.breakpoint_table.currentRow() != row31 or "main.c:31" not in tab.breakpoint_editor_label.text():
            issues.append(
                f"current-line breakpoint was not auto-selected: row={tab.breakpoint_table.currentRow()} "
                f"label={tab.breakpoint_editor_label.text()!r}"
            )
        tab.breakpoint_editor_condition.setText("speed > 70")
        tab.breakpoint_editor_condition.editingFinished.emit()
        _pump(app, 0.1)
        row31 = _row_for_line(tab, 31)
        if row31 >= 0 and tab.breakpoint_table.item(row31, 3).text() != "speed > 70":
            issues.append(f"current-line quick condition did not persist: {tab.breakpoint_table.item(row31, 3).text()!r}")
        tab.current_line_breakpoint_button.click()
        _pump(app, 0.1)
        if _row_for_line(tab, 31) >= 0:
            issues.append("current-line breakpoint button did not remove line 31")
        if getattr(tab, "current_line_breakpoint_button", None) is not None and tab.current_line_breakpoint_button.text() != "当前行断点":
            issues.append(f"current-line breakpoint button did not switch back to add: {tab.current_line_breakpoint_button.text()!r}")
        tab.add_breakpoint(72)
        tab.breakpoint_table.setCurrentCell(2, 2)
        tab.breakpoint_table.cellClicked.emit(2, 2)
        _pump(app, 0.1)
        if tab.editor.textCursor().blockNumber() + 1 != 24:
            issues.append(f"breakpoint table did not navigate to line 24: {tab.editor.textCursor().blockNumber() + 1}")
        tab.search_next_button.click()
        _pump(app, 0.1)
        tab.set_debug_status(
            make_debug_status(
                state=DebugRuntimeState.RUNNING,
                backend="keil",
                detail="合成运行状态，后端控制器尚未接入",
                project_path=project_path,
                current_pc_line=18,
                run_line=29,
                capabilities=default_debug_capabilities(
                    DebugRuntimeState.RUNNING,
                    runtime_control=True,
                    breakpoint_sync=True,
                ),
            ),
            controls_ready=False,
        )
        tab.set_remote_breakpoint_snapshot(remote_snapshot)
        window._debug_remote_breakpoint_snapshot = remote_snapshot
        _sync_command_transactions(tab, history)
        _sync_command_transactions(tab, history)
        _sync_command_transactions(tab, history, history_key="sync_breakpoints")
        running_plans = _plan_rows(tab)
        required_plan_titles = {
            "连接调试会话",
            "断开调试会话",
            "暂停目标",
            "继续运行",
            "单步",
            "同步断点",
            "写变量",
        }
        missing_plans = required_plan_titles - set(running_plans)
        if missing_plans:
            issues.append(f"command plan preview missing plans: {sorted(missing_plans)!r}")
        if running_plans.get("暂停目标", {}).get("status") != "计划就绪":
            issues.append(f"halt plan should be ready but disabled while running: {running_plans.get('暂停目标')!r}")
        if running_plans.get("继续运行", {}).get("status") != "等待条件":
            issues.append(f"run plan should wait while already running: {running_plans.get('继续运行')!r}")
        if "干跑" not in tab.plan_guard_label.text() or "未执行" not in tab.plan_guard_label.text():
            issues.append(f"top plan strip should show dry-run audit preview while running: {tab.plan_guard_label.text()!r}")
        _assert_phrases(
            issues,
            tab.plan_guard_label.toolTip(),
            (
                "diff_breakpoints(add=1",
                "remove=1",
                "enable=1",
                "disable=1",
                "update_condition=1",
                "noop=1",
                "verified=1",
                "unverified=1",
                "pending_verify=3",
            ),
            "sync breakpoint plan tooltip",
        )
        sync_mode_text = getattr(tab, "breakpoint_sync_mode_label", None).text() if hasattr(tab, "breakpoint_sync_mode_label") else ""
        sync_ops_text = getattr(tab, "breakpoint_sync_ops_label", None).text() if hasattr(tab, "breakpoint_sync_ops_label") else ""
        sync_verify_text = getattr(tab, "breakpoint_sync_verify_label", None).text() if hasattr(tab, "breakpoint_sync_verify_label") else ""
        sync_command_text = getattr(tab, "breakpoint_sync_command_label", None).text() if hasattr(tab, "breakpoint_sync_command_label") else ""
        if "完整差分" not in sync_mode_text:
            issues.append(f"breakpoint sync mode strip mismatch: {sync_mode_text!r}")
        for phrase in ("+1", "-1", "启1", "停1", "条1"):
            if phrase not in sync_ops_text:
                issues.append(f"breakpoint sync ops strip missing {phrase}: {sync_ops_text!r}")
        for phrase in ("已1", "未1", "待3"):
            if phrase not in sync_verify_text:
                issues.append(f"breakpoint sync verify strip missing {phrase}: {sync_verify_text!r}")
        if "命令 1" not in sync_command_text or "受限4" not in sync_command_text:
            issues.append(f"breakpoint sync command strip mismatch: {sync_command_text!r}")
        sync_strip_tooltip = getattr(tab, "breakpoint_sync_mode_label", None).toolTip() if hasattr(tab, "breakpoint_sync_mode_label") else ""
        if (
            "keil-ui-remote-breakpoint-snapshot-demo" not in sync_strip_tooltip
            or "完整差分同步" not in sync_strip_tooltip
            or "干跑命令计划" not in sync_strip_tooltip
            or "UVSC_DBG_EXEC_CMD" not in sync_strip_tooltip
        ):
            issues.append(f"breakpoint sync strip tooltip mismatch: {sync_strip_tooltip!r}")
        sync_button = getattr(tab, "_action_buttons", {}).get("sync_breakpoints")
        if sync_button is None or sync_button.text() != "同步断点":
            issues.append(f"breakpoint sync action label mismatch: {getattr(sync_button, 'text', lambda: None)()!r}")
        elif "完整差分同步" not in sync_button.toolTip() or "点击后会再次确认" not in sync_button.toolTip():
            issues.append(f"breakpoint sync action tooltip mismatch: {sync_button.toolTip()!r}")
        remote_state_text = getattr(tab, "remote_breakpoint_state_label", None).text() if hasattr(tab, "remote_breakpoint_state_label") else ""
        remote_count_text = getattr(tab, "remote_breakpoint_count_label", None).text() if hasattr(tab, "remote_breakpoint_count_label") else ""
        if "完整" not in remote_state_text:
            issues.append(f"remote breakpoint state chip mismatch: {remote_state_text!r}")
        if remote_count_text != "远端 5":
            issues.append(f"remote breakpoint count chip mismatch: {remote_count_text!r}")
        if not hasattr(tab, "remote_breakpoint_table") or tab.remote_breakpoint_table.rowCount() != 5:
            row_count = tab.remote_breakpoint_table.rowCount() if hasattr(tab, "remote_breakpoint_table") else -1
            issues.append(f"remote breakpoint table row count mismatch: {row_count}")
        else:
            first_remote_id = tab.remote_breakpoint_table.item(0, 0).text()
            first_remote_file = tab.remote_breakpoint_table.item(0, 1).text()
            first_remote_line = tab.remote_breakpoint_table.item(0, 2).text()
            if first_remote_id != "bp-1" or first_remote_file != "main.c" or first_remote_line != "3":
                issues.append(
                    "remote breakpoint table first row mismatch: "
                    f"{first_remote_id!r}, {first_remote_file!r}, {first_remote_line!r}"
                )
        if hasattr(tab, "breakpoint_sync_mode_label"):
            screenshots.append(
                _save_widget(
                    tab.breakpoint_sync_mode_label.parentWidget(),
                    output_dir,
                    "04_debug_breakpoint_sync_strip",
                )
            )
        if hasattr(tab, "remote_breakpoint_table"):
            screenshots.append(
                _save_widget(
                    tab.remote_breakpoint_table.parentWidget(),
                    output_dir,
                    "05_debug_remote_breakpoint_mirror",
                )
            )
        if "启用 4" not in tab.marker_label.text() or "停用 1" not in tab.marker_label.text() or "条件 3" not in tab.marker_label.text():
            issues.append(f"marker label should summarize breakpoint states: {tab.marker_label.text()!r}")
        if "PC 未验证" not in tab.marker_label.text():
            issues.append(f"synthetic PC marker should be shown as unverified: {tab.marker_label.text()!r}")
        for phrase in ("已验证 1", "未验证 1", "待验证 3"):
            if phrase not in tab.marker_label.text():
                issues.append(f"marker label missing verification phrase {phrase}: {tab.marker_label.text()!r}")
        tooltip18 = tab.editor.gutter_tooltip_for_line(18)
        tooltip3 = tab.editor.gutter_tooltip_for_line(3)
        tooltip24 = tab.editor.gutter_tooltip_for_line(24)
        tooltip48 = tab.editor.gutter_tooltip_for_line(48)
        if "当前 PC" not in tooltip18 or "未验证" not in tooltip18 or "本地状态" not in tooltip18:
            issues.append(f"synthetic PC tooltip should show local unverified evidence: {tooltip18!r}")
        if "启用断点" not in tooltip3 or "已验证" not in tooltip3 or "条件: speed > 60" not in tooltip3:
            issues.append(f"enabled conditional breakpoint tooltip mismatch: {tooltip3!r}")
        if "停用断点" not in tooltip24 or "未验证" not in tooltip24 or "Keil 未回读到该断点" not in tooltip24 or "条件: speed_error < -12" not in tooltip24:
            issues.append(f"disabled conditional breakpoint tooltip mismatch: {tooltip24!r}")
        if tooltip48 != "启用断点 · 待验证":
            issues.append(f"plain breakpoint tooltip mismatch: {tooltip48!r}")
        if tab.breakpoint_table.columnCount() != 6:
            issues.append(f"breakpoint table should expose edit columns: {tab.breakpoint_table.columnCount()}")
        row3_verify = _row_for_line(tab, 3)
        row24_verify = _row_for_line(tab, 24)
        row48_verify = _row_for_line(tab, 48)
        if row3_verify >= 0 and tab.breakpoint_table.item(row3_verify, 4).text() != "已验证":
            issues.append(f"verified breakpoint table state mismatch: {tab.breakpoint_table.item(row3_verify, 4).text()!r}")
        if row24_verify >= 0 and tab.breakpoint_table.item(row24_verify, 4).text() != "未验证":
            issues.append(f"unverified breakpoint table state mismatch: {tab.breakpoint_table.item(row24_verify, 4).text()!r}")
        if row48_verify >= 0 and tab.breakpoint_table.item(row48_verify, 4).text() != "待验证":
            issues.append(f"pending breakpoint table state mismatch: {tab.breakpoint_table.item(row48_verify, 4).text()!r}")
        if not hasattr(tab, "breakpoint_editor_condition"):
            issues.append("breakpoint quick editor was not created")
        if "历史 " not in tab.plan_history_label.text() or tab.plan_history_label.text() == "历史 0":
            issues.append(f"history chip did not record running preview: {tab.plan_history_label.text()!r}")
        history_tip = tab.plan_history_label.toolTip()
        if "最近干跑命令历史" not in history_tip or "x2" not in history_tip:
            issues.append(f"history tooltip should show recent merged dry-run records: {history_tip!r}")
        _assert_phrases(
            issues,
            history_tip,
            ("verified=1", "unverified=1", "pending=3", "snapshot=debug-backend-ui-fake"),
            "running history tooltip",
        )
        running_tooltip = tab.plan_guard_label.toolTip()
        for phrase in ("UVSC_DBG_STOP_EXECUTION", "DebugDemo.uvprojx", "DebugDemo", "4827", "后端快照", "debug-backend-ui-fake"):
            if phrase not in running_tooltip:
                issues.append(f"running transaction tooltip missing {phrase}: {running_tooltip!r}")
        write_tip = running_plans.get("写变量", {}).get("tooltip", "")
        for phrase in ("RAM", "类型", "回读", "范围"):
            if phrase not in write_tip:
                issues.append(f"write variable plan tooltip missing {phrase}: {write_tip!r}")
        row12 = _row_for_line(tab, 12)
        if row12 < 0:
            issues.append("condition edit row for line 12 was not found")
        else:
            tab.breakpoint_table.setCurrentCell(row12, 3)
            _pump(app, 0.1)
            if "main.c:12" not in tab.breakpoint_editor_label.text():
                issues.append(f"quick editor did not follow selected breakpoint: {tab.breakpoint_editor_label.text()!r}")
            if getattr(tab, "breakpoint_editor_status", None) is None or tab.breakpoint_editor_status.text() not in {"待同步", "未验证", "已验证"}:
                issues.append(f"quick editor status chip mismatch: {getattr(getattr(tab, 'breakpoint_editor_status', None), 'text', lambda: '')()!r}")
            if tab.breakpoint_editor_condition.text() != "speed_error > 40":
                issues.append(f"quick editor condition did not load selected breakpoint: {tab.breakpoint_editor_condition.text()!r}")
            tab.breakpoint_editor_clear.click()
            _pump(app, 0.1)
            _sync_command_transactions(tab, history, history_key="sync_breakpoints")
            _assert_phrases(
                issues,
                tab.plan_guard_label.toolTip(),
                ("diff_breakpoints(add=1", "remove=1", "enable=1", "disable=1", "update_condition=0", "noop=2", "verified=1", "unverified=1", "pending_verify=3"),
                "quick condition clear tooltip",
            )
        row3 = _row_for_line(tab, 3)
        if row3 < 0:
            issues.append("enable-toggle row for line 3 was not found")
        else:
            tab.breakpoint_table.setCurrentCell(row3, 0)
            _pump(app, 0.1)
            tab.breakpoint_editor_enabled.setChecked(False)
            _pump(app, 0.1)
            _sync_command_transactions(tab, history, history_key="sync_breakpoints")
            _assert_phrases(
                issues,
                tab.plan_guard_label.toolTip(),
                ("diff_breakpoints(add=1", "remove=1", "enable=0", "disable=1", "update_condition=1", "noop=2", "verified=1", "unverified=1", "pending_verify=3"),
                "quick enable toggle tooltip",
            )
        row24 = _row_for_line(tab, 24)
        if row24 < 0:
            issues.append("enable-toggle row for line 24 was not found")
        else:
            checked_item = tab.breakpoint_table.item(row24, 0)
            if checked_item is None:
                issues.append("enable-toggle item for line 24 missing")
            else:
                checked_item.setCheckState(Qt.Checked)
                _pump(app, 0.1)
                _sync_command_transactions(tab, history, history_key="sync_breakpoints")
                _assert_phrases(
                    issues,
                    tab.plan_guard_label.toolTip(),
                    ("diff_breakpoints(add=1", "remove=1", "enable=0", "disable=0", "update_condition=2", "noop=2", "verified=1", "unverified=1", "pending_verify=3"),
                    "line 24 enable toggle tooltip",
                )
        row72 = _row_for_line(tab, 72)
        if row72 < 0:
            issues.append("delete row for line 72 was not found")
        else:
            if tab.breakpoint_table.cellWidget(row72, 5) is None:
                issues.append("delete button for line 72 missing")
            tab.breakpoint_table.setCurrentCell(row72, 5)
            _pump(app, 0.1)
            tab.breakpoint_editor_delete.click()
            _pump(app, 0.1)
            _sync_command_transactions(tab, history, history_key="sync_breakpoints")
            if tab.breakpoint_table.rowCount() != 4:
                issues.append(f"quick delete did not remove row: {tab.breakpoint_table.rowCount()}")
            if "4 个本地断点" not in tab.summary_label.text():
                issues.append(f"summary did not update after deletion: {tab.summary_label.text()!r}")
            _assert_phrases(
                issues,
                tab.plan_guard_label.toolTip(),
                ("diff_breakpoints(add=0", "remove=1", "enable=0", "disable=0", "update_condition=2", "noop=2", "verified=1", "unverified=1", "pending_verify=2"),
                "quick delete tooltip",
            )
        _pump(app, 0.35)
        screenshots.append(_save(window, output_dir, "01_debug_workbench_project"))

        tab.set_debug_status(
            make_debug_status(
                state=DebugRuntimeState.PAUSED,
                backend="keil",
                detail="合成暂停状态，动作仍只允许计划预览",
                project_path=project_path,
                current_pc_line=18,
                run_line=29,
                capabilities=default_debug_capabilities(
                    DebugRuntimeState.PAUSED,
                    runtime_control=True,
                    breakpoint_sync=True,
                    variable_write=True,
                ),
            ),
            controls_ready=False,
        )
        _sync_command_transactions(tab, history)
        _sync_command_transactions(tab, history, history_key="sync_breakpoints")
        paused_plans = _plan_rows(tab)
        if paused_plans.get("继续运行", {}).get("status") != "计划就绪":
            issues.append(f"run plan should be ready but disabled while paused: {paused_plans.get('继续运行')!r}")
        if paused_plans.get("复位目标", {}).get("status") != "计划就绪":
            issues.append(f"reset plan should be ready but disabled while paused: {paused_plans.get('复位目标')!r}")
        if paused_plans.get("单步", {}).get("status") != "计划就绪":
            issues.append(f"step plan should be ready but disabled while paused: {paused_plans.get('单步')!r}")
        if paused_plans.get("跨过", {}).get("status") != "计划就绪":
            issues.append(f"step-over plan should be ready but disabled while paused: {paused_plans.get('跨过')!r}")
        if paused_plans.get("写变量", {}).get("status") != "计划就绪":
            issues.append(f"write variable plan should surface readiness without execution: {paused_plans.get('写变量')!r}")
        write_plan_tip = paused_plans.get("写变量", {}).get("tooltip", "")
        if "显式执行" not in write_plan_tip and "后端控制器" not in write_plan_tip:
            issues.append("write variable plan tooltip should mention explicit execution guard")
        if "继续运行" not in tab.plan_focus_label.text():
            issues.append(f"top plan strip should focus run while paused: {tab.plan_focus_label.text()!r}")
        if "干跑" not in tab.plan_guard_label.text() or "未执行" not in tab.plan_guard_label.text():
            issues.append(f"top plan strip should show dry-run guard: {tab.plan_guard_label.text()!r}")
        paused_tooltip = tab.plan_guard_label.toolTip()
        for phrase in ("UVSC_DBG_START_EXECUTION", "交易 ID", "Guard", "审计"):
            if phrase not in paused_tooltip:
                issues.append(f"paused transaction tooltip missing {phrase}: {paused_tooltip!r}")
        paused_history_tip = tab.plan_history_label.toolTip()
        if "继续运行" not in paused_history_tip or "同步断点" not in paused_history_tip:
            issues.append(f"history tooltip should retain paused and breakpoint-sync segments: {paused_history_tip!r}")
        _assert_phrases(
            issues,
            paused_history_tip,
            ("verified=1", "unverified=1", "pending=2"),
            "paused history tooltip",
        )
        enabled_actions = [
            key
            for key, button in getattr(tab, "_action_buttons", {}).items()
            if button.isEnabled()
        ]
        if enabled_actions:
            issues.append(f"debug buttons should remain disabled in paused synthetic state: {enabled_actions!r}")

        tab.editor.verticalScrollBar().setValue(8)
        _pump(app, 0.2)
        screenshots.append(_save(window, output_dir, "02_debug_workbench_decorations"))

        window.resize(max(1100, width - 260), max(700, height - 120))
        _pump(app, 0.35)
        screenshots.append(_save(window, output_dir, "03_debug_workbench_narrow"))

        if getattr(window, "_current_workspace_domain", "") != "debug":
            issues.append("workspace domain did not switch to debug")
        if tab.source_tree.topLevelItemCount() < 2:
            issues.append(f"source tree groups missing: {tab.source_tree.topLevelItemCount()}")
        if tab.current_document is None or tab.current_document.line_count < 35:
            issues.append("current document did not load enough source lines")
        elif len(search_document(tab.current_document, "speed")) < 10:
            issues.append("search hits for speed are unexpectedly low")
        if tab.breakpoint_table.rowCount() != 4:
            issues.append(f"breakpoint table row count={tab.breakpoint_table.rowCount()} expected=4")
        if "PC" not in tab.marker_label.text() or "运行行" not in tab.marker_label.text():
            issues.append(f"marker label missing runtime decorations: {tab.marker_label.text()!r}")
        if "停用 0" in tab.marker_label.text():
            issues.append(f"marker label should omit zero-state breakpoint groups: {tab.marker_label.text()!r}")
        tab.set_pc_evidence(
            DebugPcLocation(
                line=18,
                address=0x08001234,
                function="main",
                source="keil_uvsock",
                complete=True,
                message="Keil 已回读当前 PC",
            )
        )
        _pump(app, 0.1)
        verified_pc_tip = tab.editor.gutter_tooltip_for_line(18)
        if "PC 已回读" not in tab.marker_label.text():
            issues.append(f"verified PC marker should show readback evidence: {tab.marker_label.text()!r}")
        for phrase in ("已回读", "0x08001234", "main", "Keil 已回读当前 PC"):
            if phrase not in verified_pc_tip:
                issues.append(f"verified PC tooltip missing {phrase}: {verified_pc_tip!r}")
        if "目标已暂停" not in tab.status_text.text():
            issues.append(f"status text did not reflect paused synthetic state: {tab.status_text.text()!r}")
        enabled_actions = [
            key
            for key, button in getattr(tab, "_action_buttons", {}).items()
            if button.isEnabled()
        ]
        if enabled_actions:
            issues.append(f"debug actions should remain disabled without backend controller: {enabled_actions!r}")
        if "DebugDemo.uvprojx" not in window._hero_file.text():
            issues.append(f"hero did not show project name: {window._hero_file.text()!r}")
        issues.extend(_check_non_blank(window._tabs.currentWidget(), "debug workbench content"))
        issues.extend(_check_non_blank(tab.editor.viewport(), "code editor viewport"))

        window.close()
        app.processEvents()
        app.quit()

    if issues:
        print("FAIL debug workbench UI probe", flush=True)
        for issue in dict.fromkeys(issues):
            print(f"- {issue}", flush=True)
        print("screenshots:", flush=True)
        for path in screenshots:
            print(path, flush=True)
        return 1

    print("PASS debug workbench UI probe", flush=True)
    for path in screenshots:
        print(path, flush=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "tools" / "ui-debug-workbench")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    args = parser.parse_args()
    raise SystemExit(run(args.output_dir, max(1100, args.width), max(700, args.height)))


if __name__ == "__main__":
    main()
