"""Probe backend-neutral source manifests."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_sources import (  # noqa: E402
    source_entries_from_paths,
    source_manifest_from_compile_commands,
    source_manifest_from_readelf_line_table_text,
    source_manifest_from_gdb_sources,
    source_manifest_missing_path_hints,
    preview_source_manifest_path_remap,
    source_manifest_from_roots,
    source_manifest_from_keil_project,
    source_tree_from_entries,
)
from src.core.debug_workbench import source_entries_from_keil_project  # noqa: E402
from src.core.keil.project import parse_keil_project  # noqa: E402


PROJECT = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project>
  <Targets>
    <Target>
      <TargetName>ManifestDemo</TargetName>
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopmaster-source-manifest-") as tmp:
        root = Path(tmp)
        project_dir = root / "MDK-ARM"
        src_dir = root / "Core" / "Src"
        inc_dir = root / "Core" / "Inc"
        project_dir.mkdir(parents=True)
        src_dir.mkdir(parents=True)
        inc_dir.mkdir(parents=True)
        (src_dir / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
        (inc_dir / "pid.h").write_text("#pragma once\n", encoding="utf-8")
        (src_dir / "ignore.txt").write_text("not source\n", encoding="utf-8")
        (src_dir / "pid.cpp").write_text("int pid() { return 1; }\n", encoding="utf-8")
        project_path = project_dir / "ManifestDemo.uvprojx"
        project_path.write_text(PROJECT, encoding="utf-8")

        project = parse_keil_project(project_path)
        entries = source_entries_from_keil_project(project)
        _assert(len(entries) == 2, f"Keil source entry count mismatch: {len(entries)}")
        manifest = source_manifest_from_keil_project(project)
        _assert(manifest.provider == "keil", "Keil manifest provider mismatch")
        _assert(manifest.source_count == 2, "Keil manifest source count mismatch")
        _assert(manifest.tree.children[0].name == "App", "Keil manifest tree group mismatch")
        _assert(manifest.entries[0].origin == "keil", "Keil entry origin mismatch")
        _assert(manifest.entries[0].raw_path, "Keil entry should retain raw path")
        json.dumps(manifest.to_record(), ensure_ascii=False, sort_keys=True)

        path_entries = source_entries_from_paths(
            (src_dir / "main.c", inc_dir / "pid.h"),
            root=root,
        )
        path_tree = source_tree_from_entries(path_entries)
        group_names = {group.name for group in path_tree.children}
        _assert({"Core\\Inc", "Core\\Src"} <= group_names or {"Core/Inc", "Core/Src"} <= group_names, f"path groups mismatch: {group_names!r}")
        _assert(path_entries[0].language == "c", "source path language mismatch")
        _assert(path_entries[1].language == "c-header", "header path language mismatch")

        manual = source_manifest_from_roots((root / "Core", root / "missing-root"), name="Manual Core", max_files=2)
        _assert(manual.provider == "manual_roots", "manual manifest provider mismatch")
        _assert(manual.source_count == 2, f"manual manifest max_files mismatch: {manual.source_count}")
        _assert(all(entry.path.suffix.lower() != ".txt" for entry in manual.entries), "manual manifest should ignore text files")
        _assert(all(entry.origin == "manual_roots" for entry in manual.entries), "manual entry origin mismatch")
        manual_diag = dict(manual.diagnostics)
        _assert(manual_diag.get("无效根") == "1", f"manual diagnostics should count invalid roots: {manual_diag!r}")
        _assert(manual_diag.get("截断") == "是", f"manual diagnostics should report truncation: {manual_diag!r}")
        manual_record = manual.to_record()
        json.dumps(manual_record, ensure_ascii=False, sort_keys=True)
        manual_file = source_manifest_from_roots((src_dir / "main.c",), name="Manual File", max_files=10)
        _assert(manual_file.source_count == 1 and manual_file.entries[0].name == "main.c", "manual roots should accept a single source file")

        gdb_text = f"""
Source files for which symbols have been read in:

Core/Src/main.c, {src_dir / 'pid.cpp'}, Core/Src/ignore.txt
Source files for which symbols will be read in on demand:

{inc_dir / 'pid.h'}, Core/Src/main.c, Core/Src/missing.c
"""
        gdb_manifest = source_manifest_from_gdb_sources(gdb_text, root=root, max_files=10)
        _assert(gdb_manifest.provider == "gdb_info_sources", "GDB manifest provider mismatch")
        _assert(gdb_manifest.source_count == 4, f"GDB manifest should filter and dedupe sources: {gdb_manifest.source_count}")
        _assert(len({entry.path for entry in gdb_manifest.entries}) == 4, "GDB manifest should dedupe paths")
        _assert(all(entry.path.suffix.lower() != ".txt" for entry in gdb_manifest.entries), "GDB manifest should ignore text files")
        _assert(all(entry.origin == "gdb_info_sources" for entry in gdb_manifest.entries), "GDB entry origin mismatch")
        _assert(any(entry.resolved_from == "root_relative" for entry in gdb_manifest.entries), "GDB relative paths should resolve against root")
        gdb_diag = dict(gdb_manifest.diagnostics)
        _assert(gdb_diag.get("过滤") == "1", f"GDB diagnostics should count filtered paths: {gdb_diag!r}")
        _assert(gdb_diag.get("重复") == "1", f"GDB diagnostics should count duplicates: {gdb_diag!r}")
        _assert(gdb_diag.get("缺失") == "1", f"GDB diagnostics should count missing sources: {gdb_diag!r}")
        gdb_hints = source_manifest_missing_path_hints(gdb_manifest)
        _assert(len(gdb_hints) == 1, f"GDB missing path hint mismatch: {gdb_hints!r}")
        _assert(gdb_hints[0].count == 1 and "missing.c" in " ".join(gdb_hints[0].raw_examples), f"GDB hint content mismatch: {gdb_hints!r}")
        json.dumps([hint.to_record() for hint in gdb_hints], ensure_ascii=False, sort_keys=True)
        json.dumps(gdb_manifest.to_record(), ensure_ascii=False, sort_keys=True)

        compile_commands_path = root / "compile_commands.json"
        compile_commands_path.write_text(
            json.dumps(
                [
                    {"directory": str(root), "command": "cc -c Core/Src/main.c", "file": "Core/Src/main.c"},
                    {"directory": str(root), "command": "c++ -c Core/Src/pid.cpp", "file": "Core/Src/pid.cpp"},
                    {"directory": str(root), "command": "cc -c Core/Src/main.c", "file": "Core/Src/main.c"},
                    {"directory": str(root), "command": "cc -c Core/Src/ignore.txt", "file": "Core/Src/ignore.txt"},
                    {"directory": str(root), "command": "cc -c Core/Src/missing.c", "file": "Core/Src/missing.c"},
                    {"directory": str(root), "command": "cc -c Core/Inc/pid.h", "file": str(inc_dir / "pid.h")},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        compile_manifest = source_manifest_from_compile_commands(compile_commands_path)
        _assert(compile_manifest.provider == "compile_commands", "compile_commands provider mismatch")
        _assert(compile_manifest.source_count == 4, f"compile_commands should filter and dedupe: {compile_manifest.source_count}")
        _assert(len({entry.path for entry in compile_manifest.entries}) == 4, "compile_commands should dedupe paths")
        _assert(all(entry.origin == "compile_commands" for entry in compile_manifest.entries), "compile_commands entry origin mismatch")
        _assert(any(entry.resolved_from == "directory_relative" for entry in compile_manifest.entries), "compile_commands should mark directory-relative paths")
        compile_diag = dict(compile_manifest.diagnostics)
        _assert(compile_diag.get("重复") == "1", f"compile_commands diagnostics should count duplicates: {compile_diag!r}")
        _assert(compile_diag.get("过滤") == "1", f"compile_commands diagnostics should count filtered paths: {compile_diag!r}")
        _assert(compile_diag.get("缺失") == "1", f"compile_commands diagnostics should count missing sources: {compile_diag!r}")
        compile_hints = source_manifest_missing_path_hints(compile_manifest)
        _assert(len(compile_hints) == 1, f"compile_commands missing hint mismatch: {compile_hints!r}")
        _assert(compile_hints[0].count == 1 and compile_hints[0].resolved_from == ("directory_relative",), f"compile hint content mismatch: {compile_hints!r}")
        remap_root = root / "Remapped" / "Src"
        remap_root.mkdir(parents=True)
        (remap_root / "missing.c").write_text("int recovered(void) { return 2; }\n", encoding="utf-8")
        preview = preview_source_manifest_path_remap(
            compile_manifest,
            missing_dir=compile_hints[0].missing_dir,
            local_root=remap_root,
        )
        _assert(preview.before_missing == 1, f"remap before count mismatch: {preview!r}")
        _assert(preview.after_missing == 0, f"remap after count mismatch: {preview!r}")
        _assert(preview.remapped_count == 1 and preview.resolved_count == 1, f"remap hit count mismatch: {preview!r}")
        _assert(dict(preview.manifest.diagnostics).get("重映射命中") == "1", "remap diagnostics should report hits")
        _assert(any(entry.resolved_from.startswith("remap:") for entry in preview.manifest.entries), "remapped entries should keep provenance")
        _assert(dict(compile_manifest.diagnostics).get("缺失") == "1", "remap preview must not mutate original manifest")
        json.dumps(preview.to_record(), ensure_ascii=False, sort_keys=True)
        json.dumps(compile_manifest.to_record(), ensure_ascii=False, sort_keys=True)

        dwarf_text = f"""
Raw dump of debug contents of section .debug_line:

  The Directory Table (offset 0x22, lines 3, columns 1):
  Entry Name
  0     {root}
  1     Core/Src
  2     {inc_dir}

  The File Name Table (offset 0x44, lines 5, columns 2):
  Entry Dir Name
  0     1   main.c
  1     1   pid.cpp
  2     2   pid.h
  3     1   ignore.txt
  4     1   main.c

  Line Number Statements:
"""
        elf_path = root / "build" / "demo.elf"
        elf_path.parent.mkdir()
        elf_path.write_bytes(b"\x7fELF")
        dwarf_manifest = source_manifest_from_readelf_line_table_text(
            dwarf_text,
            elf_path=elf_path,
            source_roots=(root,),
            max_files=10,
        )
        _assert(dwarf_manifest.provider == "elf_dwarf", "DWARF manifest provider mismatch")
        _assert(dwarf_manifest.source_count == 3, f"DWARF manifest should filter and dedupe: {dwarf_manifest.source_count}")
        _assert(len({entry.path for entry in dwarf_manifest.entries}) == 3, "DWARF manifest should dedupe paths")
        _assert(all(entry.origin == "elf_dwarf" for entry in dwarf_manifest.entries), "DWARF entry origin mismatch")
        _assert(any(entry.resolved_from in {"source_root_directory", "directory_absolute"} for entry in dwarf_manifest.entries), "DWARF entries should keep resolution provenance")
        _assert(dict(dwarf_manifest.diagnostics).get("过滤") == "1", "DWARF diagnostics should count filtered paths")
        _assert(dict(dwarf_manifest.diagnostics).get("重复") == "1", "DWARF diagnostics should count duplicates")
        json.dumps(dwarf_manifest.to_record(), ensure_ascii=False, sort_keys=True)

        legacy_dwarf_text = """
The Directory Table:
  1\tCore\\Src
  2\tCore\\Inc

The File Name Table:
  1\t1\t0\t0\tmain.c
  2\t2\t0\t0\tpid.h
  3\t1\t0\t0\tstartup.s
Line Number Statements:
"""
        (src_dir / "startup.s").write_text(".syntax unified\n", encoding="utf-8")
        legacy_manifest = source_manifest_from_readelf_line_table_text(
            legacy_dwarf_text,
            elf_path=elf_path,
            source_roots=(root,),
            max_files=10,
        )
        legacy_names = {entry.name for entry in legacy_manifest.entries}
        _assert({"main.c", "pid.h", "startup.s"} <= legacy_names, f"legacy DWARF names mismatch: {legacy_names!r}")
        _assert(any(entry.language == "asm" for entry in legacy_manifest.entries), "legacy DWARF should classify ASM sources")
        json.dumps(legacy_manifest.to_record(), ensure_ascii=False, sort_keys=True)

    print("PASS debug source manifest probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
