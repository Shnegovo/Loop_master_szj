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

    print("PASS debug source manifest probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
