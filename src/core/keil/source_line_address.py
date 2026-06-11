"""Resolve source lines to code addresses from AXF/ELF DWARF line tables."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from src.parser.readelf import run_readelf


@dataclass(frozen=True)
class KeilSourceLineAddress:
    path: Path
    line: int
    address: int
    raw_file: str = ""
    resolved_from: str = ""
    exact: bool = True


@dataclass(frozen=True)
class KeilSourceLineAddressResult:
    requested_path: Path
    requested_line: int
    address: int | None = None
    line: int = 0
    raw_file: str = ""
    resolved_from: str = ""
    exact: bool = False
    error: str = ""

    @property
    def resolved(self) -> bool:
        return self.address is not None


_OFFSET_RE = re.compile(r"^\s*Offset:\s+")
_DIR_ROW_RE = re.compile(r"^\s*(?P<index>\d+)\t(?P<path>.+?)\s*$")
_FILE_ROW_RE = re.compile(r"^\s*(?P<index>\d+)\t(?P<dir>\d+)\t\d+\t\d+\t(?P<name>.+?)\s*$")
_SET_FILE_RE = re.compile(r"Set File Name to entry (?P<file>\d+)")
_SET_ADDRESS_RE = re.compile(r"set Address to 0x(?P<address>[0-9a-fA-F]+)", re.IGNORECASE)
_ADVANCE_LINE_RE = re.compile(r"Advance Line by -?\d+ to (?P<line>-?\d+)")
_SPECIAL_RE = re.compile(
    r"advance Address by .* to 0x(?P<address>[0-9a-fA-F]+) and Line by .* to (?P<line>-?\d+)",
    re.IGNORECASE,
)


def resolve_source_line_address(
    axf_path: str | Path,
    source_path: str | Path,
    line: int,
    *,
    source_roots: Iterable[str | Path] = (),
    allow_nearest: bool = True,
    max_line_delta: int = 3,
) -> KeilSourceLineAddressResult:
    requested_path = _resolve_path(Path(source_path))
    requested_line = int(line or 0)
    if requested_line <= 0:
        return KeilSourceLineAddressResult(
            requested_path=requested_path,
            requested_line=requested_line,
            error="invalid source line",
        )
    records = parse_source_line_addresses(axf_path, source_roots=tuple(source_roots))
    matches = [
        item for item in records
        if _path_match_score(item.path, requested_path) < 999
    ]
    if not matches:
        return KeilSourceLineAddressResult(
            requested_path=requested_path,
            requested_line=requested_line,
            error="source file not found in DWARF line table",
        )

    exact = [item for item in matches if int(item.line) == requested_line]
    if exact:
        best = min(exact, key=lambda item: item.address)
        return _result_from_record(best, requested_path, requested_line, exact=True)

    if allow_nearest:
        nearest = [
            item for item in matches
            if int(item.line) >= requested_line and int(item.line) - requested_line <= int(max_line_delta)
        ]
        if nearest:
            best = min(nearest, key=lambda item: (int(item.line) - requested_line, item.address))
            return _result_from_record(best, requested_path, requested_line, exact=False)

    return KeilSourceLineAddressResult(
        requested_path=requested_path,
        requested_line=requested_line,
        error="source line has no executable address in DWARF line table",
    )


def parse_source_line_addresses(
    axf_path: str | Path,
    *,
    source_roots: Iterable[str | Path] = (),
) -> tuple[KeilSourceLineAddress, ...]:
    axf = _resolve_path(Path(axf_path))
    roots = tuple(_resolve_path(Path(root)) for root in source_roots)
    return _parse_source_line_addresses_cached(str(axf), tuple(str(root) for root in roots))


@lru_cache(maxsize=32)
def _parse_source_line_addresses_cached(
    axf_path: str,
    source_roots: tuple[str, ...],
) -> tuple[KeilSourceLineAddress, ...]:
    text = run_readelf(axf_path, "-wl")
    if not text.strip():
        return ()
    roots = tuple(Path(root) for root in source_roots)
    return _parse_readelf_line_text(text, axf_path=Path(axf_path), source_roots=roots)


def _parse_readelf_line_text(
    text: str,
    *,
    axf_path: Path,
    source_roots: tuple[Path, ...],
) -> tuple[KeilSourceLineAddress, ...]:
    records: list[KeilSourceLineAddress] = []
    dirs: dict[int, str] = {}
    files: dict[int, Path] = {}
    raw_files: dict[int, str] = {}
    state = ""
    current_file = 1
    current_line = 1
    current_address = 0

    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        if _OFFSET_RE.match(line):
            dirs = {}
            files = {}
            raw_files = {}
            state = ""
            current_file = 1
            current_line = 1
            current_address = 0
            continue
        if "The Directory Table" in line:
            state = "dirs"
            continue
        if "The File Name Table" in line:
            state = "files"
            continue
        if "Line Number Statements" in line:
            state = "lines"
            current_file = 1
            current_line = 1
            current_address = 0
            continue

        if state == "dirs":
            match = _DIR_ROW_RE.match(line)
            if match:
                dirs[int(match.group("index"))] = match.group("path").strip()
            continue

        if state == "files":
            match = _FILE_ROW_RE.match(line)
            if match:
                index = int(match.group("index"))
                dir_index = int(match.group("dir"))
                raw_name = match.group("name").strip()
                files[index] = _resolve_file_entry(
                    raw_name,
                    dirs.get(dir_index, ""),
                    axf_path=axf_path,
                    source_roots=source_roots,
                )
                raw_files[index] = raw_name
            continue

        if state != "lines":
            continue

        file_match = _SET_FILE_RE.search(line)
        if file_match:
            current_file = int(file_match.group("file"))
            continue

        address_match = _SET_ADDRESS_RE.search(line)
        if address_match:
            current_address = int(address_match.group("address"), 16)
            continue

        line_match = _ADVANCE_LINE_RE.search(line)
        if line_match:
            current_line = int(line_match.group("line"))
            continue

        special_match = _SPECIAL_RE.search(line)
        if special_match:
            current_address = int(special_match.group("address"), 16)
            current_line = int(special_match.group("line"))
            _append_record(records, files, raw_files, current_file, current_line, current_address)
            continue

        if line.strip().endswith("Copy"):
            _append_record(records, files, raw_files, current_file, current_line, current_address)
            continue

        if "End of Sequence" in line:
            current_file = 1
            current_line = 1
            current_address = 0

    return tuple(records)


def _append_record(
    records: list[KeilSourceLineAddress],
    files: dict[int, Path],
    raw_files: dict[int, str],
    current_file: int,
    current_line: int,
    current_address: int,
) -> None:
    if current_address <= 0 or current_line <= 0:
        return
    path = files.get(int(current_file))
    if path is None:
        return
    records.append(
        KeilSourceLineAddress(
            path=path,
            line=int(current_line),
            address=int(current_address),
            raw_file=raw_files.get(int(current_file), path.name),
            resolved_from="readelf_debug_line",
            exact=True,
        )
    )


def _resolve_file_entry(
    raw_name: str,
    raw_directory: str,
    *,
    axf_path: Path,
    source_roots: tuple[Path, ...],
) -> Path:
    name_path = Path(raw_name)
    if name_path.is_absolute():
        return _resolve_path(name_path)
    candidates: list[Path] = []
    if raw_directory:
        directory = Path(raw_directory)
        if directory.is_absolute():
            candidates.append(directory / name_path)
        else:
            for root in source_roots:
                candidates.append(root / directory / name_path)
            candidates.append(axf_path.parent / directory / name_path)
    for root in source_roots:
        candidates.append(root / name_path)
    candidates.append(axf_path.parent / name_path)
    candidates.append(name_path)
    for candidate in candidates:
        resolved = _resolve_path(candidate)
        if resolved.exists():
            return resolved
    return _resolve_path(candidates[0])


def _result_from_record(
    record: KeilSourceLineAddress,
    requested_path: Path,
    requested_line: int,
    *,
    exact: bool,
) -> KeilSourceLineAddressResult:
    return KeilSourceLineAddressResult(
        requested_path=requested_path,
        requested_line=requested_line,
        address=record.address,
        line=record.line,
        raw_file=record.raw_file,
        resolved_from=record.resolved_from,
        exact=exact,
    )


def _path_match_score(candidate: Path, requested: Path) -> int:
    candidate_parts = _path_parts(candidate)
    requested_parts = _path_parts(requested)
    if candidate_parts == requested_parts:
        return 0
    if candidate_parts and len(candidate_parts) <= len(requested_parts):
        if requested_parts[-len(candidate_parts):] == candidate_parts:
            return 1
    if candidate.name.lower() == requested.name.lower():
        return 2
    return 999


def _path_parts(path: Path) -> tuple[str, ...]:
    return tuple(part.lower() for part in _resolve_path(path).parts)


def _resolve_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser()


__all__ = [
    "KeilSourceLineAddress",
    "KeilSourceLineAddressResult",
    "parse_source_line_addresses",
    "resolve_source_line_address",
]
