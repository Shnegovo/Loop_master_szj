"""Keil uVision project parsing helpers.

The parser reads only project metadata: targets, output paths, groups, and file
paths. It does not read source files or Keil configuration/license files.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".s", ".asm"}
HEADER_SUFFIXES = {".h", ".hpp", ".hh", ".inc"}


@dataclass(frozen=True)
class KeilProjectFile:
    name: str
    type_id: str
    path_text: str
    path: Path
    exists: bool

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()

    @property
    def is_source(self) -> bool:
        return self.suffix in SOURCE_SUFFIXES

    @property
    def is_header(self) -> bool:
        return self.suffix in HEADER_SUFFIXES


@dataclass(frozen=True)
class KeilGroup:
    name: str
    files: tuple[KeilProjectFile, ...]


@dataclass(frozen=True)
class KeilTarget:
    name: str
    output_directory: str
    output_name: str
    create_executable: bool
    output_path: Path | None
    listing_path: Path | None
    groups: tuple[KeilGroup, ...]

    @property
    def files(self) -> tuple[KeilProjectFile, ...]:
        return tuple(file for group in self.groups for file in group.files)

    @property
    def source_files(self) -> tuple[KeilProjectFile, ...]:
        return tuple(file for file in self.files if file.is_source)

    @property
    def header_files(self) -> tuple[KeilProjectFile, ...]:
        return tuple(file for file in self.files if file.is_header)


@dataclass(frozen=True)
class KeilProject:
    path: Path
    name: str
    targets: tuple[KeilTarget, ...]

    @property
    def default_target(self) -> KeilTarget | None:
        return self.targets[0] if self.targets else None


def parse_keil_project(path: str | Path) -> KeilProject:
    project_path = Path(path).expanduser().resolve()
    root = ET.parse(project_path).getroot()
    targets = tuple(_parse_target(project_path, target) for target in root.findall(".//Target"))
    return KeilProject(
        path=project_path,
        name=project_path.stem,
        targets=targets,
    )


def find_keil_projects(root: str | Path, max_count: int = 50) -> tuple[Path, ...]:
    root_path = Path(root).expanduser()
    if not root_path.exists():
        return ()
    projects = []
    for pattern in ("*.uvprojx", "*.uvproj"):
        for path in root_path.rglob(pattern):
            projects.append(path.resolve())
            if len(projects) >= max_count:
                return tuple(sorted(projects))
    return tuple(sorted(projects))


def _parse_target(project_path: Path, target_node: ET.Element) -> KeilTarget:
    project_dir = project_path.parent
    name = _text(target_node, "TargetName") or "Unnamed Target"
    output_directory = _text(target_node, ".//TargetCommonOption/OutputDirectory")
    output_name = _text(target_node, ".//TargetCommonOption/OutputName") or project_path.stem
    create_executable = _text(target_node, ".//TargetCommonOption/CreateExecutable") != "0"
    listing_text = _text(target_node, ".//TargetCommonOption/ListingPath")
    output_path = _output_path(project_dir, output_directory, output_name) if create_executable else None
    listing_path = _resolve_project_path(project_dir, listing_text) if listing_text else None
    groups = tuple(_parse_group(project_dir, group) for group in target_node.findall(".//Group"))
    return KeilTarget(
        name=name,
        output_directory=output_directory,
        output_name=output_name,
        create_executable=create_executable,
        output_path=output_path,
        listing_path=listing_path,
        groups=groups,
    )


def _parse_group(project_dir: Path, group_node: ET.Element) -> KeilGroup:
    name = _text(group_node, "GroupName") or "Ungrouped"
    files = tuple(_parse_file(project_dir, file_node) for file_node in group_node.findall(".//File"))
    return KeilGroup(name=name, files=files)


def _parse_file(project_dir: Path, file_node: ET.Element) -> KeilProjectFile:
    name = _text(file_node, "FileName")
    type_id = _text(file_node, "FileType")
    path_text = _text(file_node, "FilePath") or name
    path = _resolve_project_path(project_dir, path_text)
    return KeilProjectFile(
        name=name or path.name,
        type_id=type_id,
        path_text=path_text,
        path=path,
        exists=path.exists(),
    )


def _output_path(project_dir: Path, output_directory: str, output_name: str) -> Path:
    output_dir = _resolve_project_path(project_dir, output_directory) if output_directory else project_dir
    output = Path(output_name)
    if output.suffix.lower() not in {".axf", ".elf", ".out"}:
        output = output.with_suffix(".axf")
    return (output_dir / output).resolve()


def _resolve_project_path(project_dir: Path, value: str) -> Path:
    value = value.strip().replace("\\", "/")
    if not value:
        return project_dir.resolve()
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (project_dir / path).resolve()


def _text(node: ET.Element, path: str) -> str:
    found = node.find(path)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def project_summary_lines(project: KeilProject) -> list[str]:
    lines = [f"Project: {project.path}", f"Targets: {len(project.targets)}"]
    for target in project.targets:
        lines.append(
            f"Target {target.name}: groups={len(target.groups)} "
            f"files={len(target.files)} sources={len(target.source_files)} "
            f"headers={len(target.header_files)} output={target.output_path or '--'}"
        )
    return lines


def source_paths(target: KeilTarget) -> tuple[Path, ...]:
    return tuple(file.path for file in target.source_files)


def header_paths(target: KeilTarget) -> tuple[Path, ...]:
    return tuple(file.path for file in target.header_files)


def existing_files(files: Iterable[KeilProjectFile]) -> tuple[KeilProjectFile, ...]:
    return tuple(file for file in files if file.exists)
