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
    source_manifest_from_gdb_sources,
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

        manual = source_manifest_from_roots((root / "Core",), name="Manual Core", max_files=2)
        _assert(manual.provider == "manual_roots", "manual manifest provider mismatch")
        _assert(manual.source_count == 2, f"manual manifest max_files mismatch: {manual.source_count}")
        _assert(all(entry.path.suffix.lower() != ".txt" for entry in manual.entries), "manual manifest should ignore text files")
        manual_record = manual.to_record()
        json.dumps(manual_record, ensure_ascii=False, sort_keys=True)

        gdb_text = f"""
Source files for which symbols have been read in:

{src_dir / 'main.c'}, {src_dir / 'pid.cpp'}, {src_dir / 'ignore.txt'}
Source files for which symbols will be read in on demand:

{inc_dir / 'pid.h'}, {src_dir / 'main.c'}
"""
        gdb_manifest = source_manifest_from_gdb_sources(gdb_text, root=root, max_files=3)
        _assert(gdb_manifest.provider == "gdb_info_sources", "GDB manifest provider mismatch")
        _assert(gdb_manifest.source_count == 3, f"GDB manifest should filter and dedupe sources: {gdb_manifest.source_count}")
        _assert(len({entry.path for entry in gdb_manifest.entries}) == 3, "GDB manifest should dedupe paths")
        _assert(all(entry.path.suffix.lower() != ".txt" for entry in gdb_manifest.entries), "GDB manifest should ignore text files")
        json.dumps(gdb_manifest.to_record(), ensure_ascii=False, sort_keys=True)

        compile_commands_path = root / "compile_commands.json"
        compile_commands_path.write_text(
            json.dumps(
                [
                    {"directory": str(root), "command": "cc -c Core/Src/main.c", "file": "Core/Src/main.c"},
                    {"directory": str(root), "command": "c++ -c Core/Src/pid.cpp", "file": "Core/Src/pid.cpp"},
                    {"directory": str(root), "command": "cc -c Core/Src/main.c", "file": "Core/Src/main.c"},
                    {"directory": str(root), "command": "cc -c Core/Src/ignore.txt", "file": "Core/Src/ignore.txt"},
                    {"directory": str(root), "command": "cc -c Core/Inc/pid.h", "file": str(inc_dir / "pid.h")},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        compile_manifest = source_manifest_from_compile_commands(compile_commands_path)
        _assert(compile_manifest.provider == "compile_commands", "compile_commands provider mismatch")
        _assert(compile_manifest.source_count == 3, f"compile_commands should filter and dedupe: {compile_manifest.source_count}")
        _assert(len({entry.path for entry in compile_manifest.entries}) == 3, "compile_commands should dedupe paths")
        json.dumps(compile_manifest.to_record(), ensure_ascii=False, sort_keys=True)

    print("PASS debug source manifest probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
