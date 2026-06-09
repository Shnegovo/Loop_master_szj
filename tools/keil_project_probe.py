"""Probe Keil project parsing without launching Keil."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.project import find_keil_projects, parse_keil_project, project_summary_lines  # noqa: E402


SYNTHETIC_PROJECT = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project>
  <Targets>
    <Target>
      <TargetName>DemoF401</TargetName>
      <TargetOption>
        <TargetCommonOption>
          <OutputDirectory>Objects\\</OutputDirectory>
          <OutputName>demo_f401</OutputName>
          <CreateExecutable>1</CreateExecutable>
          <ListingPath>Listings\\</ListingPath>
        </TargetCommonOption>
      </TargetOption>
      <Groups>
        <Group>
          <GroupName>Application/User</GroupName>
          <Files>
            <File>
              <FileName>main.c</FileName>
              <FileType>1</FileType>
              <FilePath>..\\Core\\Src\\main.c</FilePath>
            </File>
            <File>
              <FileName>app.h</FileName>
              <FileType>5</FileType>
              <FilePath>..\\Core\\Inc\\app.h</FilePath>
            </File>
          </Files>
        </Group>
        <Group>
          <GroupName>Startup</GroupName>
          <Files>
            <File>
              <FileName>startup.s</FileName>
              <FileType>2</FileType>
              <FilePath>startup.s</FilePath>
            </File>
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
    parser = argparse.ArgumentParser(description="Probe Keil uvprojx parsing.")
    parser.add_argument("--project", default="")
    parser.add_argument("--list-root", default="")
    args = parser.parse_args()

    if args.list_root:
        projects = find_keil_projects(args.list_root, max_count=20)
        print(f"Keil projects under {args.list_root}: {len(projects)}")
        for path in projects[:10]:
            print(f"  {path}")
        _assert(projects, "no Keil projects found under list-root")
        print("PASS keil project listing")
        return 0

    if args.project:
        project_path = Path(args.project)
        project = parse_keil_project(project_path)
        for line in project_summary_lines(project):
            print(line)
        _assert(project.targets, "real project has no targets")
        target = project.default_target
        _assert(target is not None, "real project default target missing")
        _assert(target.files, "real project has no files")
        _assert(target.output_path is not None, "real project output path missing")
        print(
            "PASS keil project parse "
            f"targets={len(project.targets)} files={len(target.files)} sources={len(target.source_files)}"
        )
        return 0

    with tempfile.TemporaryDirectory(prefix="loopmaster-keil-project-") as tmp:
        root = Path(tmp)
        project_dir = root / "MDK-ARM"
        (root / "Core" / "Src").mkdir(parents=True)
        (root / "Core" / "Inc").mkdir(parents=True)
        project_dir.mkdir(parents=True)
        (root / "Core" / "Src" / "main.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
        (root / "Core" / "Inc" / "app.h").write_text("#pragma once\n", encoding="utf-8")
        (project_dir / "startup.s").write_text("; startup\n", encoding="utf-8")
        project_path = project_dir / "Demo.uvprojx"
        project_path.write_text(SYNTHETIC_PROJECT, encoding="utf-8")

        project = parse_keil_project(project_path)
        for line in project_summary_lines(project):
            print(line)
        _assert(project.name == "Demo", "project name mismatch")
        _assert(len(project.targets) == 1, "synthetic target count mismatch")
        target = project.targets[0]
        _assert(target.name == "DemoF401", "target name mismatch")
        _assert(target.output_path == (project_dir / "Objects" / "demo_f401.axf").resolve(), "output path mismatch")
        _assert(len(target.groups) == 2, "group count mismatch")
        _assert(len(target.files) == 3, "file count mismatch")
        _assert(len(target.source_files) == 2, "source count mismatch")
        _assert(len(target.header_files) == 1, "header count mismatch")
        _assert(all(file.exists for file in target.files), "synthetic files should exist")
        print("PASS keil synthetic project parse")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
