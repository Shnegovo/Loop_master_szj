"""Screenshot probe for the read-only Debug Workbench workspace."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_workbench import (  # noqa: E402
    DebugRuntimeState,
    default_debug_capabilities,
    make_debug_status,
    search_document,
)
from src.ui.gui import MainWindow  # noqa: E402
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


def _save(window: MainWindow, output_dir: Path, name: str) -> Path:
    QApplication.processEvents()
    path = output_dir / f"{name}.png"
    if not window.grab().save(str(path)):
        raise RuntimeError(f"failed to save screenshot: {path}")
    return path


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
    with tempfile.TemporaryDirectory(prefix="loopmaster-debug-ui-") as tmp:
        project_path = _write_fixture(Path(tmp))
        window = MainWindow()
        window._config_path = output_dir / "probe-loopmaster.json"
        window.resize(width, height)
        window.show()
        _pump(app, 0.35)

        window._show_workspace_page("debug_sources")
        tab = window._tab_debug_workbench
        tab.load_project(project_path)
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
        tab.add_breakpoint(12)
        tab.add_breakpoint(24, enabled=False, condition="speed_error < -12")
        tab.breakpoint_table.setCurrentCell(1, 0)
        tab.breakpoint_table.cellClicked.emit(1, 0)
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
        write_tip = running_plans.get("写变量", {}).get("tooltip", "")
        for phrase in ("RAM", "类型", "回读", "范围"):
            if phrase not in write_tip:
                issues.append(f"write variable plan tooltip missing {phrase}: {write_tip!r}")
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
        paused_plans = _plan_rows(tab)
        if paused_plans.get("继续运行", {}).get("status") != "计划就绪":
            issues.append(f"run plan should be ready but disabled while paused: {paused_plans.get('继续运行')!r}")
        if paused_plans.get("单步", {}).get("status") != "计划就绪":
            issues.append(f"step plan should be ready but disabled while paused: {paused_plans.get('单步')!r}")
        if paused_plans.get("写变量", {}).get("status") != "计划就绪":
            issues.append(f"write variable plan should surface readiness without execution: {paused_plans.get('写变量')!r}")
        if "烟测" not in paused_plans.get("写变量", {}).get("tooltip", ""):
            issues.append("write variable plan tooltip should mention smoke-stage execution guard")
        if "继续运行" not in tab.plan_focus_label.text():
            issues.append(f"top plan strip should focus run while paused: {tab.plan_focus_label.text()!r}")
        if "烟测" not in tab.plan_guard_label.text():
            issues.append(f"top plan strip should show execution guard: {tab.plan_guard_label.text()!r}")
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
        if tab.breakpoint_table.rowCount() != 2:
            issues.append(f"breakpoint table row count={tab.breakpoint_table.rowCount()} expected=2")
        if "PC" not in tab.marker_label.text() or "运行行" not in tab.marker_label.text():
            issues.append(f"marker label missing runtime decorations: {tab.marker_label.text()!r}")
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
