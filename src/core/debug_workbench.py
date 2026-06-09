"""Core models for the future modern debugger workbench."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

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


@dataclass(frozen=True)
class SourceTreeNode:
    name: str
    path: Path | None = None
    children: tuple["SourceTreeNode", ...] = ()

    @property
    def is_file(self) -> bool:
        return self.path is not None


@dataclass(frozen=True)
class CodeLine:
    number: int
    text: str


@dataclass(frozen=True)
class CodeDocument:
    path: Path
    language: str
    lines: tuple[CodeLine, ...]

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def line_text(self, number: int) -> str:
        if number < 1 or number > len(self.lines):
            return ""
        return self.lines[number - 1].text


@dataclass(frozen=True)
class SearchMatch:
    line: int
    column: int
    text: str


@dataclass(frozen=True)
class LineDecoration:
    line: int
    kind: str
    label: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class Breakpoint:
    path: Path
    line: int
    enabled: bool = True
    condition: str = ""
    hit_count: int = 0
    verified: bool = False
    message: str = ""


class BreakpointStore:
    """Pure breakpoint state for a future gutter/list UI."""

    def __init__(self) -> None:
        self._breakpoints: dict[tuple[str, int], Breakpoint] = {}

    def all(self) -> tuple[Breakpoint, ...]:
        return tuple(sorted(self._breakpoints.values(), key=lambda bp: (str(bp.path).lower(), bp.line)))

    def for_file(self, path: str | Path) -> tuple[Breakpoint, ...]:
        key_path = _normalise_key_path(path)
        return tuple(bp for bp in self.all() if _normalise_key_path(bp.path) == key_path)

    def get(self, path: str | Path, line: int) -> Breakpoint | None:
        return self._breakpoints.get(_breakpoint_key(path, line))

    def add(
        self,
        path: str | Path,
        line: int,
        *,
        enabled: bool = True,
        condition: str = "",
        message: str = "",
    ) -> Breakpoint:
        line = _valid_line(line)
        breakpoint = Breakpoint(
            path=Path(path).expanduser().resolve(),
            line=line,
            enabled=bool(enabled),
            condition=str(condition),
            message=str(message),
        )
        self._breakpoints[_breakpoint_key(breakpoint.path, line)] = breakpoint
        return breakpoint

    def toggle(self, path: str | Path, line: int) -> Breakpoint | None:
        key = _breakpoint_key(path, line)
        if key in self._breakpoints:
            del self._breakpoints[key]
            return None
        return self.add(path, line)

    def remove(self, path: str | Path, line: int) -> bool:
        return self._breakpoints.pop(_breakpoint_key(path, line), None) is not None

    def set_enabled(self, path: str | Path, line: int, enabled: bool) -> Breakpoint:
        return self._update(path, line, enabled=bool(enabled))

    def set_condition(self, path: str | Path, line: int, condition: str) -> Breakpoint:
        return self._update(path, line, condition=str(condition))

    def set_verified(self, path: str | Path, line: int, verified: bool) -> Breakpoint:
        return self._update(path, line, verified=bool(verified))

    def record_hit(self, path: str | Path, line: int, count: int = 1) -> Breakpoint:
        breakpoint = self.get(path, line)
        if breakpoint is None:
            raise KeyError(f"breakpoint does not exist: {path}:{line}")
        next_count = max(0, breakpoint.hit_count + int(count))
        return self._update(path, line, hit_count=next_count)

    def clear(self) -> None:
        self._breakpoints.clear()

    def _update(self, path: str | Path, line: int, **changes) -> Breakpoint:
        key = _breakpoint_key(path, line)
        breakpoint = self._breakpoints.get(key)
        if breakpoint is None:
            raise KeyError(f"breakpoint does not exist: {path}:{line}")
        updated = replace(breakpoint, **changes)
        self._breakpoints[key] = updated
        return updated


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


def source_tree_from_entries(entries: tuple[SourceEntry, ...]) -> SourceTreeNode:
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


def load_code_document(path: str | Path, max_bytes: int = 2_000_000) -> CodeDocument:
    source_path = Path(path).expanduser().resolve()
    size = source_path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"source file is too large: {size} bytes")
    text = source_path.read_text(encoding="utf-8-sig", errors="replace")
    raw_lines = text.splitlines()
    if text.endswith(("\n", "\r")):
        raw_lines.append("")
    lines = tuple(CodeLine(index + 1, line) for index, line in enumerate(raw_lines))
    return CodeDocument(
        path=source_path,
        language=SOURCE_LANGUAGES.get(source_path.suffix.lower(), "text"),
        lines=lines,
    )


def search_document(
    document: CodeDocument,
    query: str,
    *,
    case_sensitive: bool = False,
    max_matches: int = 500,
) -> tuple[SearchMatch, ...]:
    query = str(query)
    if not query:
        return ()
    needle = query if case_sensitive else query.lower()
    matches: list[SearchMatch] = []
    for line in document.lines:
        haystack = line.text if case_sensitive else line.text.lower()
        start = 0
        while True:
            column = haystack.find(needle, start)
            if column < 0:
                break
            matches.append(SearchMatch(line.number, column + 1, line.text[column:column + len(query)]))
            if len(matches) >= max_matches:
                return tuple(matches)
            start = column + max(1, len(needle))
    return tuple(matches)


def line_decorations(
    document: CodeDocument,
    breakpoints: BreakpointStore | None = None,
    *,
    current_pc_line: int | None = None,
    run_line: int | None = None,
    search_query: str = "",
) -> tuple[LineDecoration, ...]:
    decorations: list[LineDecoration] = []
    if breakpoints is not None:
        for breakpoint in breakpoints.for_file(document.path):
            decorations.append(
                LineDecoration(
                    line=breakpoint.line,
                    kind="breakpoint",
                    label=breakpoint.condition,
                    enabled=breakpoint.enabled,
                )
            )
    if current_pc_line is not None and 1 <= int(current_pc_line) <= document.line_count:
        decorations.append(LineDecoration(line=int(current_pc_line), kind="pc", label="PC"))
    if run_line is not None and 1 <= int(run_line) <= document.line_count:
        decorations.append(LineDecoration(line=int(run_line), kind="run", label="Run"))
    for match in search_document(document, search_query):
        decorations.append(LineDecoration(line=match.line, kind="search", label=match.text))
    return tuple(sorted(decorations, key=lambda item: (item.line, item.kind, item.label)))


def _select_target(project: KeilProject, target_name: str | None) -> KeilTarget | None:
    if not target_name:
        return project.default_target
    for target in project.targets:
        if target.name == target_name:
            return target
    return None


def _breakpoint_key(path: str | Path, line: int) -> tuple[str, int]:
    return _normalise_key_path(path), _valid_line(line)


def _normalise_key_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve()).lower()


def _valid_line(line: int) -> int:
    line = int(line)
    if line <= 0:
        raise ValueError("breakpoint line must be >= 1")
    return line
