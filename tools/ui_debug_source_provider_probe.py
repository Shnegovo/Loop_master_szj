"""Probe explicit Debug Workbench source-provider configuration."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui.gui import MainWindow  # noqa: E402
from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402
from src.core.debug_sources import source_manifest_missing_path_hints  # noqa: E402


PROJECT = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project>
  <Targets>
    <Target>
      <TargetName>SourceProviderDemo</TargetName>
      <Groups>
        <Group>
          <GroupName>App</GroupName>
          <Files>
            <File><FileName>main.c</FileName><FileType>1</FileType><FilePath>..\\Core\\Src\\main.c</FilePath></File>
            <File><FileName>pid.h</FileName><FileType>5</FileType><FilePath>..\\Core\\Inc\\pid.h</FilePath></File>
          </Files>
        </Group>
      </Groups>
    </Target>
  </Targets>
</Project>
"""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _pump(app: QApplication, seconds: float = 0.15) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _write_fixture(root: Path) -> Path:
    project_dir = root / "MDK-ARM"
    src_dir = root / "Core" / "Src"
    inc_dir = root / "Core" / "Inc"
    build_dir = root / "build"
    project_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)
    inc_dir.mkdir(parents=True)
    build_dir.mkdir(parents=True)
    (src_dir / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    (src_dir / "pid.c").write_text("int pid(void) { return 1; }\n", encoding="utf-8")
    (inc_dir / "pid.h").write_text("#pragma once\n", encoding="utf-8")
    (src_dir / "ignore.txt").write_text("ignore\n", encoding="utf-8")
    project_path = project_dir / "SourceProviderDemo.uvprojx"
    project_path.write_text(PROJECT, encoding="utf-8")
    (build_dir / "demo.elf").write_bytes(b"\x7fELF")
    compile_commands = root / "compile_commands.json"
    compile_commands.write_text(
        json.dumps(
            [
                {"directory": str(root), "command": "cc -c Core/Src/main.c", "file": "Core/Src/main.c"},
                {"directory": str(root), "command": "cc -c Core/Src/missing.c", "file": "Core/Src/missing.c"},
                {"directory": str(root), "command": "cc -c Core/Src/ignore.txt", "file": "Core/Src/ignore.txt"},
                {"directory": str(root), "command": "cc -c Core/Src/main.c", "file": "Core/Src/main.c"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return project_path


def _diagnostics(tab) -> dict[str, str]:
    return {
        tab.diagnostics_table.item(row, 0).text(): tab.diagnostics_table.item(row, 1).text()
        for row in range(tab.diagnostics_table.rowCount())
        if tab.diagnostics_table.item(row, 0) is not None and tab.diagnostics_table.item(row, 1) is not None
    }


def _tree_texts(tab) -> tuple[str, ...]:
    texts: list[str] = []
    for index in range(tab.source_tree.topLevelItemCount()):
        item = tab.source_tree.topLevelItem(index)
        texts.append(item.text(0))
        for child_index in range(item.childCount()):
            texts.append(item.child(child_index).text(0))
    return tuple(texts)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_pcl_theme(app)
    with tempfile.TemporaryDirectory(prefix="loopmaster-source-provider-ui-") as tmp:
        root = Path(tmp)
        project_path = _write_fixture(root)
        window = MainWindow()
        window._config_path = root / "probe-loopmaster.json"
        window.resize(1280, 820)
        window.show()
        _pump(app, 0.25)

        window._show_workspace_page("debug_sources")
        tab = window._tab_debug_workbench
        tab.load_project(project_path)
        window._elf_path = root / "build" / "demo.elf"
        window._recent_elf_path = window._elf_path
        _pump(app, 0.15)

        options = {
            tab.source_provider_combo.itemData(index)
            for index in range(tab.source_provider_combo.count())
        }
        for key in {"compile_commands", "manual_roots", "gdb_text", "elf_dwarf"}:
            _assert(key in options, f"source provider option missing: {key}")
        _assert(hasattr(tab, "source_provider_configure_button"), "configure button missing")

        manifest = window.configure_debug_compile_commands(root / "compile_commands.json")
        _pump(app, 0.15)
        _assert(manifest.provider == "compile_commands", "compile_commands manifest provider mismatch")
        _assert(manifest.source_count == 2, f"compile_commands source count mismatch: {manifest.source_count}")
        _assert(
            tab.source_manifest is not None and tab.source_manifest.provider == "compile_commands",
            f"tab did not keep compile_commands manifest: key={window._debug_source_provider_key!r} "
            f"combo={tab.source_provider_combo.currentData()!r} "
            f"tab={getattr(tab.source_manifest, 'provider', None)!r} "
            f"returned={manifest.provider!r}",
        )
        diag = _diagnostics(tab)
        _assert(diag.get("源码缺失") == "1", f"compile_commands missing diagnostics mismatch: {diag!r}")
        _assert(diag.get("源码过滤") == "1", f"compile_commands filtered diagnostics mismatch: {diag!r}")
        _assert(diag.get("源码重复") == "1", f"compile_commands duplicate diagnostics mismatch: {diag!r}")
        _assert("missing.c" in diag.get("映射示例", ""), f"compile_commands mapping example mismatch: {diag!r}")
        _assert("缺失 1" in tab.source_provider_missing_label.text(), "compile_commands missing chip mismatch")
        _assert("映射提示" in tab.source_provider_missing_label.toolTip(), "compile_commands missing tooltip should include mapping hints")
        _assert(tab.source_provider_remap_button.isEnabled(), "remap button should be enabled when source files are missing")
        _assert(any("(缺失)" in text for text in _tree_texts(tab)), f"missing tree node absent: {_tree_texts(tab)!r}")
        remap_root = root / "Remapped" / "Src"
        remap_root.mkdir(parents=True)
        (remap_root / "missing.c").write_text("int recovered(void) { return 2; }\n", encoding="utf-8")
        hints = source_manifest_missing_path_hints(manifest)
        preview = window.preview_debug_source_remap(
            missing_dir=hints[0].missing_dir,
            local_root=remap_root,
            persist=True,
        )
        _pump(app, 0.15)
        _assert(preview.before_missing == 1 and preview.after_missing == 0, f"remap preview count mismatch: {preview!r}")
        diag = _diagnostics(tab)
        _assert(diag.get("源码缺失") == "0", f"remap missing diagnostics mismatch: {diag!r}")
        _assert(diag.get("重映射命中") == "1", f"remap hit diagnostics mismatch: {diag!r}")
        _assert("路径正常" in tab.source_provider_missing_label.text(), f"remap chip mismatch: {tab.source_provider_missing_label.text()!r}")
        _assert(not tab.source_provider_remap_button.isEnabled(), "remap button should disable after missing files are resolved")
        remap_records = window._debug_source_config_record().get("remaps", [])
        _assert(remap_records and remap_records[-1]["local_root"] == str(remap_root.resolve()), f"remap config mismatch: {remap_records!r}")

        gdb_text = (
            "Source files for which symbols have been read in:\n\n"
            "Core/Src/main.c, Core/Src/missing_gdb.c, Core/Src/ignore.txt, Core/Src/main.c\n"
        )
        gdb_manifest = window.configure_debug_gdb_sources_text(gdb_text, root=root)
        _pump(app, 0.15)
        _assert(gdb_manifest.provider == "gdb_info_sources", "GDB manifest provider mismatch")
        _assert(gdb_manifest.source_count == 2, f"GDB source count mismatch: {gdb_manifest.source_count}")
        diag = _diagnostics(tab)
        _assert(diag.get("源码来源") == "GDB 源码表", f"GDB source label mismatch: {diag!r}")
        _assert(diag.get("源码缺失") == "1", f"GDB missing diagnostics mismatch: {diag!r}")
        _assert(diag.get("源码过滤") == "1", f"GDB filtered diagnostics mismatch: {diag!r}")
        _assert(diag.get("源码重复") == "1", f"GDB duplicate diagnostics mismatch: {diag!r}")
        _assert("missing_gdb.c" in diag.get("映射示例", ""), f"GDB mapping example mismatch: {diag!r}")

        dwarf_text = f"""
Raw dump of debug contents of section .debug_line:

  The Directory Table:
  0     {root}
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
            elf_path=root / "build" / "demo.elf",
            source_roots=(root,),
        )
        _pump(app, 0.15)
        _assert(dwarf_manifest.provider == "elf_dwarf", "DWARF manifest provider mismatch")
        _assert(dwarf_manifest.source_count == 2, f"DWARF source count mismatch: {dwarf_manifest.source_count}")
        diag = _diagnostics(tab)
        _assert(diag.get("源码来源") == "ELF/DWARF", f"DWARF source label mismatch: {diag!r}")
        _assert(diag.get("源码缺失") == "1", f"DWARF missing diagnostics mismatch: {diag!r}")
        _assert(diag.get("源码过滤") == "1", f"DWARF filtered diagnostics mismatch: {diag!r}")
        _assert("missing_dwarf.c" in diag.get("映射示例", ""), f"DWARF mapping example mismatch: {diag!r}")
        record = window._debug_source_config_record()
        _assert(record["provider_key"] == "elf_dwarf", f"config provider mismatch: {record!r}")
        _assert("readelf" not in " ".join(_tree_texts(tab)).lower(), "tree should not show launched readelf output")

        window.close()
        _pump(app, 0.2)

    print("PASS debug source provider UI probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
