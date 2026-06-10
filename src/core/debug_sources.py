"""Backend-neutral source manifest models for the debug workbench."""

from __future__ import annotations

import json
import re
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


@dataclass(frozen=True)
class SourcePathMappingHint:
    missing_dir: str
    count: int
    raw_examples: tuple[str, ...] = ()
    resolved_from: tuple[str, ...] = ()

    def to_record(self) -> dict[str, object]:
        return {
            "missing_dir": self.missing_dir,
            "count": self.count,
            "raw_examples": list(self.raw_examples),
            "resolved_from": list(self.resolved_from),
        }


@dataclass(frozen=True)
class _ReadelfSourcePath:
    path: Path
    raw_path: str
    directory: str
    resolved_from: str


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
        group_name = group
        if root_path is not None and not path.is_absolute():
            try:
                resolved = (root_path / path).resolve()
            except OSError:
                resolved = root_path / path
            resolved_from = "root_relative"
        else:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            resolved_from = "absolute" if path.is_absolute() else "unresolved"
        if root_path is not None:
            try:
                relative = resolved.relative_to(root_path)
                group_name = str(relative.parent) if str(relative.parent) != "." else group
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
    invalid_roots = sum(1 for root in root_paths if not root.exists())
    truncated = False
    for root in root_paths:
        if not root.exists():
            continue
        if root.is_file():
            if _is_source_path(root):
                if len(entries) >= max_files:
                    truncated = True
                    break
                entries.extend(source_entries_from_paths((root,), root=root.parent, origin=provider))
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and _is_source_path(path):
                if len(entries) >= max_files:
                    truncated = True
                    break
                entries.extend(source_entries_from_paths((path,), root=root, origin=provider))
        if len(entries) >= max_files:
            truncated = True
            break
    entries = entries[:max_files]
    return SourceManifest(
        name=name,
        root=root_paths[0] if root_paths else None,
        provider=provider,
        entries=tuple(entries),
        diagnostics=(
            ("输入根", str(len(root_paths))),
            ("无效根", str(invalid_roots)),
            ("源码文件", str(len(entries))),
            ("截断", "是" if truncated else "否"),
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
    paths, filtered, duplicate = _parse_gdb_info_sources(text)
    entries = source_entries_from_paths(paths[:max_files], root=root, origin="gdb_info_sources")
    missing = sum(1 for entry in entries if not entry.exists)
    return SourceManifest(
        name=name,
        root=Path(root).expanduser().resolve() if root else None,
        provider="gdb_info_sources",
        entries=entries,
        diagnostics=(
            ("解析路径", str(len(paths))),
            ("源码文件", str(min(len(paths), max_files))),
            ("过滤", str(filtered)),
            ("重复", str(duplicate)),
            ("缺失", str(missing)),
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
    missing = sum(1 for entry in entries if not entry.exists)
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
            ("缺失", str(missing)),
            ("截断", "是" if len(paths) >= max_files and len(data) > max_files else "否"),
        ),
        metadata={"max_files": str(max_files)},
    )


def source_manifest_from_readelf_line_table_text(
    text: str,
    *,
    elf_path: str | Path | None = None,
    source_roots: Iterable[str | Path] = (),
    name: str = "ELF/DWARF Sources",
    max_files: int = 5000,
) -> SourceManifest:
    elf = Path(elf_path).expanduser().resolve() if elf_path else None
    roots = tuple(Path(root).expanduser().resolve() for root in source_roots)
    parsed_paths = _readelf_source_paths(text, elf, roots)
    entries: list[SourceEntry] = []
    seen: set[str] = set()
    filtered = 0
    duplicate = 0
    for parsed in parsed_paths:
        if not _is_source_path(parsed.path):
            filtered += 1
            continue
        key = str(parsed.path).lower()
        if key in seen:
            duplicate += 1
            continue
        seen.add(key)
        entries.append(_source_entry_from_readelf_path(parsed, roots, elf))
        if len(entries) >= max_files:
            break
    missing = sum(1 for entry in entries if not entry.exists)
    return SourceManifest(
        name=name,
        root=roots[0] if roots else (elf.parent if elf else None),
        provider="elf_dwarf",
        entries=tuple(entries),
        project_path=elf,
        diagnostics=(
            ("解析路径", str(len(parsed_paths))),
            ("源码文件", str(len(entries))),
            ("过滤", str(filtered)),
            ("重复", str(duplicate)),
            ("缺失", str(missing)),
            ("截断", "是" if len(entries) >= max_files and len(parsed_paths) > max_files else "否"),
        ),
        metadata={
            "max_files": str(max_files),
            "source_roots": str(len(roots)),
            "elf_path": str(elf) if elf else "",
        },
    )


def source_manifest_from_elf_dwarf(
    elf_path: str | Path,
    *,
    source_roots: Iterable[str | Path] = (),
    name: str | None = None,
    max_files: int = 5000,
) -> SourceManifest:
    elf = Path(elf_path).expanduser().resolve()
    from src.parser.readelf import run_readelf

    text = run_readelf(elf, "-wl")
    return source_manifest_from_readelf_line_table_text(
        text,
        elf_path=elf,
        source_roots=source_roots,
        name=name or f"{elf.name} DWARF Sources",
        max_files=max_files,
    )


def source_manifest_missing_path_hints(
    manifest: SourceManifest,
    *,
    max_hints: int = 4,
    max_examples: int = 3,
) -> tuple[SourcePathMappingHint, ...]:
    groups: dict[str, dict[str, object]] = {}
    for entry in manifest.entries:
        if entry.exists:
            continue
        missing_dir = str(entry.path.parent)
        group = groups.setdefault(
            missing_dir,
            {
                "count": 0,
                "raw_examples": [],
                "resolved_from": set(),
            },
        )
        group["count"] = int(group["count"]) + 1
        raw_examples = group["raw_examples"]
        if isinstance(raw_examples, list):
            raw_text = entry.raw_path or entry.name
            if raw_text and raw_text not in raw_examples and len(raw_examples) < max_examples:
                raw_examples.append(raw_text)
        resolved_from = group["resolved_from"]
        if isinstance(resolved_from, set) and entry.resolved_from:
            resolved_from.add(entry.resolved_from)
    hints: list[SourcePathMappingHint] = []
    for missing_dir, group in groups.items():
        raw_examples = tuple(str(item) for item in group.get("raw_examples", ()))
        resolved_from = tuple(sorted(str(item) for item in group.get("resolved_from", ())))
        hints.append(
            SourcePathMappingHint(
                missing_dir=missing_dir,
                count=int(group.get("count", 0)),
                raw_examples=raw_examples,
                resolved_from=resolved_from,
            )
        )
    hints.sort(key=lambda item: (-item.count, item.missing_dir.lower()))
    return tuple(hints[:max(0, int(max_hints))])


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
    paths, _filtered, _duplicate = _parse_gdb_info_sources(text)
    return paths


def _parse_gdb_info_sources(text: str) -> tuple[tuple[Path, ...], int, int]:
    paths: list[Path] = []
    seen: set[str] = set()
    filtered = 0
    duplicate = 0
    for raw_line in str(text).splitlines():
        for token in _candidate_path_tokens(raw_line):
            path = Path(token)
            if not _is_source_path(path):
                filtered += 1
                continue
            key = str(path).lower()
            if key in seen:
                duplicate += 1
                continue
            seen.add(key)
            paths.append(path)
    return tuple(paths), filtered, duplicate


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


def _readelf_source_paths(
    text: str,
    elf_path: Path | None,
    source_roots: tuple[Path, ...],
) -> tuple[_ReadelfSourcePath, ...]:
    directories: dict[int, str] = {}
    paths: list[_ReadelfSourcePath] = []
    in_dirs = False
    in_files = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        if "The Directory Table" in line:
            directories = {}
            in_dirs = True
            in_files = False
            continue
        if "The File Name Table" in line:
            in_dirs = False
            in_files = True
            continue
        if in_dirs:
            parsed_dir = _parse_readelf_directory_row(line)
            if parsed_dir is not None:
                index, directory = parsed_dir
                directories[index] = directory
            continue
        if in_files:
            if "Line Number Statements" in line:
                in_files = False
                continue
            parsed_file = _parse_readelf_file_row(line)
            if parsed_file is None:
                continue
            _index, directory_index, file_name = parsed_file
            directory = directories.get(directory_index, "")
            paths.append(_resolve_readelf_source_path(file_name, directory, elf_path, source_roots))
    return tuple(paths)


def _parse_readelf_directory_row(line: str) -> tuple[int, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(("Entry", "Name")):
        return None
    match = re.match(r"^(\d+)\s+(.+)$", stripped)
    if not match:
        return None
    return int(match.group(1)), _clean_readelf_table_value(match.group(2))


def _parse_readelf_file_row(line: str) -> tuple[int, int, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(("Entry", "Dir")):
        return None
    old_match = re.match(r"^(\d+)\s+(\d+)\s+\d+\s+\d+\s+(.+)$", stripped)
    if old_match:
        return (
            int(old_match.group(1)),
            int(old_match.group(2)),
            _clean_readelf_table_value(old_match.group(3)),
        )
    match = re.match(r"^(\d+)\s+(\d+)\s+(.+)$", stripped)
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        _clean_readelf_table_value(match.group(3)),
    )


def _clean_readelf_table_value(value: str) -> str:
    cleaned = str(value).strip()
    annotation = re.search(r"\):\s*(.+)$", cleaned)
    if annotation:
        cleaned = annotation.group(1).strip()
    if cleaned.startswith("(indexed string:") and ":" in cleaned:
        cleaned = cleaned.rsplit(":", 1)[-1].strip()
    return cleaned.strip().strip('"').strip("'")


def _resolve_readelf_source_path(
    file_name: str,
    directory: str,
    elf_path: Path | None,
    source_roots: tuple[Path, ...],
) -> _ReadelfSourcePath:
    raw_file = str(file_name).strip()
    raw_directory = str(directory).strip()
    file_path = Path(raw_file)
    if file_path.is_absolute():
        return _ReadelfSourcePath(
            path=_resolve_path(file_path),
            raw_path=raw_file,
            directory=raw_directory,
            resolved_from="file_absolute",
        )

    candidates: list[tuple[Path, str]] = []
    directory_path = Path(raw_directory) if raw_directory else None
    if directory_path is not None and directory_path.is_absolute():
        candidates.append((directory_path / file_path, "directory_absolute"))
    for root in source_roots:
        if directory_path is not None and raw_directory:
            candidates.append((root / directory_path / file_path, "source_root_directory"))
        candidates.append((root / file_path, "source_root"))
    if elf_path is not None:
        if directory_path is not None and raw_directory:
            candidates.append((elf_path.parent / directory_path / file_path, "elf_directory_relative"))
        candidates.append((elf_path.parent / file_path, "elf_relative"))
    if directory_path is not None and raw_directory:
        candidates.append((directory_path / file_path, "directory_relative"))
    candidates.append((file_path, "unresolved_relative"))

    for candidate, resolved_from in candidates:
        resolved = _resolve_path(candidate)
        if resolved.exists():
            return _ReadelfSourcePath(
                path=resolved,
                raw_path=raw_file,
                directory=raw_directory,
                resolved_from=resolved_from,
            )

    candidate, resolved_from = candidates[0]
    return _ReadelfSourcePath(
        path=_resolve_path(candidate),
        raw_path=raw_file,
        directory=raw_directory,
        resolved_from=resolved_from,
    )


def _source_entry_from_readelf_path(
    parsed: _ReadelfSourcePath,
    source_roots: tuple[Path, ...],
    elf_path: Path | None,
) -> SourceEntry:
    root = source_roots[0] if source_roots else (elf_path.parent if elf_path else None)
    group = _group_for_path(parsed.path, root, "ELF/DWARF")
    return SourceEntry(
        path=parsed.path,
        name=parsed.path.name,
        group=group,
        exists=parsed.path.exists(),
        language=SOURCE_LANGUAGES.get(parsed.path.suffix.lower(), "text"),
        origin="elf_dwarf",
        raw_path=parsed.raw_path,
        resolved_from=parsed.resolved_from,
        compile_directory=parsed.directory,
    )


def _group_for_path(path: Path, root: Path | None, fallback: str) -> str:
    if root is None:
        return fallback
    try:
        relative = path.relative_to(root)
    except ValueError:
        return fallback
    return str(relative.parent) if str(relative.parent) != "." else fallback


def _resolve_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser()
