"""Probe Debug Workbench Keil profile save/load UI wiring."""

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
from src.core.keil.profile_store import load_keil_profile_store  # noqa: E402
from src.ui.gui import MainWindow  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402


PROJECT = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project>
  <Targets>
    <Target>
      <TargetName>Target 1</TargetName>
      <TargetOption>
        <TargetCommonOption>
          <OutputDirectory>Objects\\</OutputDirectory>
          <OutputName>Project</OutputName>
          <CreateExecutable>1</CreateExecutable>
        </TargetCommonOption>
      </TargetOption>
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


def _pump(app: QApplication, seconds: float = 0.2) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _write_fixture(root: Path) -> Path:
    project_dir = root / "KeilProject"
    user_dir = project_dir / "User"
    user_dir.mkdir(parents=True)
    (user_dir / "main.c").write_text("int debug_setpoint = 1;\n", encoding="utf-8")
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

    with tempfile.TemporaryDirectory(prefix="loopmaster-keil-profile-ui-") as tmp:
        tmp_path = Path(tmp)
        project_path = _write_fixture(tmp_path)
        window = MainWindow()
        window._config_path = tmp_path / "loopmaster.json"
        window._keil_profile_store_path = tmp_path / "profiles.json"
        window._keil_profile_store = load_keil_profile_store(window._keil_profile_store_path)
        window._show_info = lambda *_args, **_kwargs: None
        window._show_warning = lambda *_args, **_kwargs: None
        tab = window._tab_debug_workbench
        tab.load_project(project_path)
        tab.set_debug_status(
            make_debug_status(
                state=DebugRuntimeState.KEIL_DISCOVERED,
                backend="keil",
                detail="fake discovered",
                project_path=project_path,
                target_name="Target 1",
            ),
            controls_ready=True,
        )
        window._refresh_debug_workbench_diagnostics()
        _pump(app)

        tab.profile_save_button.click()
        _pump(app, 0.2)
        store = load_keil_profile_store(window._keil_profile_store_path)
        _assert(store.default is not None, "default profile was not saved")
        _assert(store.default.project_path == project_path.resolve(), f"saved project mismatch: {store.default}")
        _assert(store.default.target_name == "Target 1", f"saved target mismatch: {store.default.target_name}")

        window.close()
        app.processEvents()

        window = MainWindow()
        window._config_path = tmp_path / "loopmaster.json"
        window._keil_profile_store_path = tmp_path / "profiles.json"
        window._keil_profile_store = load_keil_profile_store(window._keil_profile_store_path)
        window._show_info = lambda *_args, **_kwargs: None
        window._show_warning = lambda *_args, **_kwargs: None
        tab = window._tab_debug_workbench
        tab.profile_load_button.click()
        _pump(app, 0.2)
        _assert(tab.debug_status.project_path == project_path.resolve(), "load should restore project path")
        _assert(tab.debug_status.target_name == "Target 1", f"load should restore target: {tab.debug_status.target_name}")
        rows = {
            tab.diagnostics_table.item(row, 0).text(): tab.diagnostics_table.item(row, 1).text()
            for row in range(tab.diagnostics_table.rowCount())
            if tab.diagnostics_table.item(row, 0) is not None and tab.diagnostics_table.item(row, 1) is not None
        }
        _assert(rows.get("默认调试档案", ""), f"profile diagnostics missing: {rows!r}")
        _assert(rows.get("档案 Target") == "Target 1", f"profile target diagnostics mismatch: {rows!r}")

        window._apply_debug_keil_config(tmp_path, 4933)
        _pump(app, 0.1)
        _assert(window._debug_uvsock_port == 4933, "Keil port should update")
        _assert(str(window._keil_root) == str(tmp_path), "Keil root should update")
        rows = {
            tab.diagnostics_table.item(row, 0).text(): tab.diagnostics_table.item(row, 1).text()
            for row in range(tab.diagnostics_table.rowCount())
            if tab.diagnostics_table.item(row, 0) is not None and tab.diagnostics_table.item(row, 1) is not None
        }
        _assert(rows.get("UVSOCK 端口") == "4933", f"port diagnostics mismatch: {rows!r}")
        _assert("配置已更新" in tab.debug_status.detail, f"status should mention config update: {tab.debug_status.detail!r}")

        window.close()
        app.processEvents()

    print("PASS Keil profile store UI probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
