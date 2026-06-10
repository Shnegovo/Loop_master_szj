"""Backend-neutral source manifest models for the debug workbench."""

from __future__ import annotations

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

    def to_record(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "name": self.name,
            "group": self.group,
            "exists": self.exists,
            "language": self.language,
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
            "entries": [entry.to_record() for entry in self.entries],
        }


def source_entries_from_paths(
    paths: Iterable[str | Path],
    *,
    root: str | Path | None = None,
    group: str = "Sources",
) -> tuple[SourceEntry, ...]:
    root_path = Path(root).expanduser().resolve() if root else None
    entries: list[SourceEntry] = []
    for item in paths:
        path = Path(item).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        group_name = group
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
                entries.extend(source_entries_from_paths((root,), root=root.parent))
            continue
        for path in sorted(root.rglob("*")):
            if len(entries) >= max_files:
                break
            if path.is_file() and _is_source_path(path):
                entries.extend(source_entries_from_paths((path,), root=root))
        if len(entries) >= max_files:
            break
    return SourceManifest(
        name=name,
        root=root_paths[0] if root_paths else None,
        provider=provider,
        entries=tuple(entries[:max_files]),
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
