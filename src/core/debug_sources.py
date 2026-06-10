"""Backend-neutral source manifest models for the debug workbench."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.core.keil.project import KeilProject, KeilTarget


SOURCE_LANGUAGES = {
    ".c": "c",
    ".h": "c-header",
    ".cpp": "cpp",
    ".hpp": "cpp-header",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".s": "asm",
    ".asm": "asm",
}


@dataclass(frozen=True)
class SourceEntry:
    path: Path
    name: str
    group: str
    exists: bool
    language: str
    origin: str = ""
    raw_path: str = ""
    resolved_from: str = ""
    compile_directory: str = ""

    def to_record(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "name": self.name,
            "group": self.group,
            "exists": self.exists,
            "language": self.language,
            "origin": self.origin,
            "raw_path": self.raw_path,
            "resolved_from": self.resolved_from,
            "compile_directory": self.compile_directory,
        }


@dataclass(frozen=True)
class SourceTreeNode:
    name: str
    path: Path | None = None
    children: tuple["SourceTreeNode", ...] = ()

    @property
    def is_file(self) -> bool:
        return self.path is not None


@dataclass(frozen=True)
class SourceManifest:
    name: str
    root: Path | None
    provider: str
    entries: tuple[SourceEntry, ...]
    target_name: str = ""
    project_path: Path | None = None
    diagnostics: tuple[tuple[str, str], ...] = ()
    metadata: dict[str, str] | None = None

    @property
    def source_count(self) -> int:
        return len(self.entries)

    @property
    def tree(self) -> SourceTreeNode:
        return source_tree_from_entries(self.entries)

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(entry.path for entry in self.entries)

    def to_record(self) -> dict[str, object]:
        return {
            "name": self.name,
            "root": str(self.root) if self.root else "",
            "provider": self.provider,
            "target_name": self.target_name,
            "project_path": str(self.project_path) if self.project_path else "",
            "diagnostics": [
                {"key": key, "value": value}
                for key, value in self.diagnostics
            ],
            "metadata": dict(self.metadata or {}),
            "entries": [entry.to_record() for entry in self.entries],
        }


def source_entries_from_paths(
    paths: Iterable[str | Path],
    *,
    root: str | Path | None = None,
    group: str = "Sources",
    origin: str = "",
    compile_directory: str = "",
) -> tuple[SourceEntry, ...]:
    root_path = Path(root).expanduser().resolve() if root else None
    entries: list[SourceEntry] = []
    for item in paths:
        path = Path(item).expanduser()
        raw_path = str(item)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        group_name = group
        resolved_from = "absolute" if path.is_absolute() else "unresolved"
        if root_path is not None:
            try:
                relative = resolved.relative_to(root_path)
                group_name = str(relative.parent) if str(relative.parent) != "." else group
                resolved_from = "root_relative" if not path.is_absolute() else "absolute"
            except ValueError:
                group_name = group
        entries.append(
            SourceEntry(
                path=resolved,
                name=resolved.name,
                group=group_name,
                exists=resolved.exists(),
                language=SOURCE_LANGUAGES.get(resolved.suffix.lower(), "text"),
                origin=origin,
                raw_path=raw_path,
                resolved_from=resolved_from,
                compile_directory=compile_directory,
            )
        )
    return tuple(entries)


def source_manifest_from_roots(
    roots: Iterable[str | Path],
    *,
    name: str = "Manual Sources",
    provider: str = "manual_roots",
    max_files: int = 5000,
) -> SourceManifest:
    root_paths = tuple(Path(root).expanduser().resolve() for root in roots)
    entries: list[SourceEntry] = []
    for root in root_paths:
        if not root.exists():
            continue
        if root.is_file():
            if _is_source_path(root):
                entries.extend(source_entries_from_paths((root,), root=root.parent, origin=provider))
            continue
        for path in sorted(root.rglob("*")):
            if len(entries) >= max_files:
                break
            if path.is_file() and _is_source_path(path):
                entries.extend(source_entries_from_paths((path,), root=root, origin=provider))
        if len(entries) >= max_files:
            break
    return SourceManifest(
        name=name,
        root=root_paths[0] if root_paths else None,
        provider=provider,
        entries=tuple(entries[:max_files]),
        diagnostics=(
            ("输入根", str(len(root_paths))),
            ("源码文件", str(min(len(entries), max_files))),
            ("截断", "是" if len(entries) > max_files else "否"),
        ),
        metadata={"max_files": str(max_files)},
    )


def source_manifest_from_gdb_sources(
    text: str,
    *,
    root: str | Path | None = None,
    name: str = "GDB Sources",
    max_files: int = 5000,
) -> SourceManifest:
    paths = _paths_from_gdb_info_sources(text)
    return SourceManifest(
        name=name,
        root=Path(root).expanduser().resolve() if root else None,
        provider="gdb_info_sources",
        entries=source_entries_from_paths(paths[:max_files], root=root, origin="gdb_info_sources"),
        diagnostics=(
            ("解析路径", str(len(paths))),
            ("源码文件", str(min(len(paths), max_files))),
            ("截断", "是" if len(paths) > max_files else "否"),
        ),
        metadata={"max_files": str(max_files)},
    )


def source_manifest_from_compile_commands(
    path: str | Path,
    *,
    name: str = "Compile Commands",
    max_files: int = 5000,
) -> SourceManifest:
    compile_path = Path(path).expanduser().resolve()
    data = json.loads(compile_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("compile_commands.json must contain a list")
    paths: list[tuple[Path, str, str]] = []
    seen: set[str] = set()
    filtered = 0
    duplicate = 0
    for item in data:
        source_path = _compile_command_source_path(item, compile_path.parent)
        if source_path is None:
            filtered += 1
            continue
        path, raw_path, directory = source_path
        if not _is_source_path(path):
            filtered += 1
            continue
        key = str(path).lower()
        if key in seen:
            duplicate += 1
            continue
        seen.add(key)
        paths.append((path, raw_path, directory))
        if len(paths) >= max_files:
            break
    entries = tuple(
        source_entries_from_paths(
            (path,),
            root=compile_path.parent,
            origin="compile_commands",
            compile_directory=directory,
        )[0]
        for path, raw_path, directory in paths
    )
    entries = tuple(
        SourceEntry(
            path=entry.path,
            name=entry.name,
            group=entry.group,
            exists=entry.exists,
            language=entry.language,
            origin=entry.origin,
            raw_path=raw_path,
            resolved_from="absolute" if Path(raw_path).is_absolute() else "directory_relative",
            compile_directory=entry.compile_directory,
        )
        for entry, (_path, raw_path, _directory) in zip(entries, paths)
    )
    return SourceManifest(
        name=name,
        root=compile_path.parent,
        provider="compile_commands",
        entries=entries,
        project_path=compile_path,
        diagnostics=(
            ("编译项", str(len(data))),
            ("源码文件", str(len(entries))),
            ("过滤", str(filtered)),
            ("重复", str(duplicate)),
            ("截断", "是" if len(paths) >= max_files and len(data) > max_files else "否"),
        ),
        metadata={"max_files": str(max_files)},
    )


def source_entries_from_keil_project(
    project: KeilProject,
    target_name: str | None = None,
) -> tuple[SourceEntry, ...]:
    target = _select_target(project, target_name)
    if target is None:
        return ()
    entries = []
    for group in target.groups:
        for file in group.files:
            if not (file.is_source or file.is_header):
                continue
            entries.append(
                SourceEntry(
                    path=file.path,
                    name=file.name,
                    group=group.name,
                    exists=file.exists,
                    language=SOURCE_LANGUAGES.get(file.suffix, "text"),
                    origin="keil",
                    raw_path=str(getattr(file, "path_text", "") or file.path),
                    resolved_from="keil_project",
                )
            )
    return tuple(entries)


def source_manifest_from_keil_project(
    project: KeilProject,
    target_name: str | None = None,
) -> SourceManifest:
    target = _select_target(project, target_name)
    target_label = target.name if target is not None else str(target_name or "")
    return SourceManifest(
        name=project.path.name,
        root=project.path.parent,
        provider="keil",
        entries=source_entries_from_keil_project(project, target_label or None),
        target_name=target_label,
        project_path=project.path,
        diagnostics=(("provider", "keil"), ("target", target_label)),
    )


def source_tree_from_entries(entries: Iterable[SourceEntry]) -> SourceTreeNode:
    group_map: dict[str, list[SourceTreeNode]] = {}
    for entry in entries:
        group_map.setdefault(entry.group or "Ungrouped", []).append(
            SourceTreeNode(name=entry.name, path=entry.path)
        )
    groups = tuple(
        SourceTreeNode(
            name=name,
            children=tuple(sorted(nodes, key=lambda node: node.name.lower())),
        )
        for name, nodes in sorted(group_map.items(), key=lambda item: item[0].lower())
    )
    return SourceTreeNode(name="Sources", children=groups)


def _select_target(project: KeilProject, target_name: str | None) -> KeilTarget | None:
    if not target_name:
        return project.default_target
    for target in project.targets:
        if target.name == target_name:
            return target
    return None


def _is_source_path(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_LANGUAGES


def _paths_from_gdb_info_sources(text: str) -> tuple[Path, ...]:
    paths: list[Path] = []
    seen: set[str] = set()
    for raw_line in str(text).splitlines():
        for token in _candidate_path_tokens(raw_line):
            path = Path(token)
            if not _is_source_path(path):
                continue
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
    return tuple(paths)


def _candidate_path_tokens(line: str) -> tuple[str, ...]:
    cleaned = line.strip()
    if not cleaned or cleaned.endswith(":"):
        return ()
    for prefix in ("Source files for which symbols have been read in:", "Source files for which symbols will be read in on demand:"):
        cleaned = cleaned.replace(prefix, " ")
    tokens: list[str] = []
    for chunk in cleaned.replace(";", ",").split(","):
        token = chunk.strip().strip('"').strip("'")
        if not token:
            continue
        if token.startswith("`") and token.endswith("'"):
            token = token[1:-1]
        if token:
            tokens.append(token)
    return tuple(tokens)


def _compile_command_source_path(item: object, default_root: Path) -> tuple[Path, str, str] | None:
    if not isinstance(item, dict):
        return None
    file_value = item.get("file")
    if not file_value:
        return None
    raw_path = str(file_value)
    path = Path(raw_path)
    directory = item.get("directory")
    root = Path(str(directory)).expanduser() if directory else default_root
    if path.is_absolute():
        return path, raw_path, str(root)
    return (root / path).resolve(), raw_path, str(root)
