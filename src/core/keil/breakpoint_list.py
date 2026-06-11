"""Parse Keil breakpoint list text into backend-neutral snapshots."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.core.debug_backend import backend_snapshot_id, now_iso
from src.core.debug_snapshots import RemoteBreakpoint, RemoteBreakpointSnapshot


@dataclass(frozen=True)
class KeilBreakpointListParseResult:
    snapshot: RemoteBreakpointSnapshot
    parsed_count: int
    ignored_lines: tuple[str, ...] = ()
    unresolved_lines: tuple[str, ...] = ()
    command: str = "BreakList"

    @property
    def complete(self) -> bool:
        return self.snapshot.complete

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        rows = [
            ("远端断点枚举", "完成" if self.snapshot.complete else "失败"),
            ("远端断点数", str(len(self.snapshot.breakpoints))),
            ("远端断点命令", self.command),
        ]
        if self.ignored_lines:
            rows.append(("远端断点忽略行", str(len(self.ignored_lines))))
        if self.unresolved_lines:
            rows.append(("远端断点未解析", str(len(self.unresolved_lines))))
        if self.snapshot.error:
            rows.append(("远端断点错误", self.snapshot.error))
        return tuple(rows)


def parse_keil_breakpoint_list(
    text: str,
    *,
    project_path: str | Path | None = None,
    target_name: str = "",
    captured_at: str = "",
    command: str = "BreakList",
) -> KeilBreakpointListParseResult:
    breakpoints: list[RemoteBreakpoint] = []
    ignored: list[str] = []
    unresolved: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or _is_header_or_noise(line):
            continue
        parsed = _parse_breakpoint_line(line)
        if parsed is None:
            ignored.append(line)
            continue
        if parsed.path is None or parsed.line <= 0:
            unresolved.append(line)
            continue
        breakpoints.append(parsed)
    error = ""
    if unresolved:
        error = f"Keil BreakList 有 {len(unresolved)} 行未解析出源码位置"
    snapshot = RemoteBreakpointSnapshot(
        schema_version=1,
        snapshot_id=_snapshot_id(project_path, target_name, breakpoints, text),
        project_path=Path(project_path).expanduser().resolve() if project_path else None,
        target_name=str(target_name or ""),
        captured_at=captured_at or now_iso(),
        complete=not unresolved,
        breakpoints=tuple(breakpoints),
        error=error,
    )
    return KeilBreakpointListParseResult(
        snapshot=snapshot,
        parsed_count=len(breakpoints),
        ignored_lines=tuple(ignored),
        unresolved_lines=tuple(unresolved),
        command=str(command or "BreakList"),
    )


def incomplete_keil_breakpoint_snapshot(
    *,
    project_path: str | Path | None,
    target_name: str = "",
    error: str,
    captured_at: str = "",
) -> RemoteBreakpointSnapshot:
    payload = {
        "project": str(project_path or ""),
        "target": str(target_name or ""),
        "captured_at": captured_at or now_iso(),
        "error": str(error or ""),
    }
    return RemoteBreakpointSnapshot(
        schema_version=1,
        snapshot_id=backend_snapshot_id(payload).replace("debug-backend-", "keil-remote-"),
        project_path=Path(project_path).expanduser().resolve() if project_path else None,
        target_name=str(target_name or ""),
        captured_at=str(payload["captured_at"]),
        complete=False,
        breakpoints=(),
        error=str(error or ""),
    )


def keil_breakpoint_list_command() -> str:
    return "BL"


_ID_PREFIX_RE = re.compile(r"^(?:#\s*)?(?P<id>\d+|0x[0-9a-fA-F]+)[\)\].:\s-]+(?P<rest>.+)$")
_LINE_TOKEN_RE = re.compile(r"(?:(?:line|ln|行)\s*[:=]?\s*)?(?P<line>\d+)\b", re.IGNORECASE)
_PATH_LINE_PATTERNS = (
    re.compile(r"(?P<path>[A-Za-z]:[\\/][^:|()]+?)[\s:(),]+(?P<line>\d+)(?=$|\s|[),])"),
    re.compile(r"(?P<path>\\\\[^:|()]+?)[\s:(),]+(?P<line>\d+)(?=$|\s|[),])"),
    re.compile(r"(?P<path>[/~][^:|()]+?)[\s:(),]+(?P<line>\d+)(?=$|\s|[),])"),
)
_QUOTED_PATH_RE = re.compile(r"['\"](?P<path>[^'\"]+\.[A-Za-z0-9_]+)['\"]")
_SOURCE_EXT_RE = re.compile(r"\.(?:c|cc|cpp|cxx|h|hpp|s|asm)$", re.IGNORECASE)


def _parse_breakpoint_line(line: str) -> RemoteBreakpoint | None:
    match = _ID_PREFIX_RE.search(line)
    if not match:
        return None
    remote_id = match.group("id")
    rest = match.group("rest").strip()
    enabled = _enabled_state(rest)
    condition = _condition(rest)
    path, line_number = _path_and_line(rest)
    raw_location = f"{path}:{line_number}" if path is not None and line_number else rest
    return RemoteBreakpoint(
        path=path,
        line=line_number,
        enabled=enabled,
        condition=condition,
        remote_id=remote_id,
        raw_location=raw_location,
        verified=bool(path is not None and line_number > 0),
        message="" if path is not None and line_number > 0 else "Keil 断点列表缺少源码路径或行号",
    )


def _path_and_line(text: str) -> tuple[Path | None, int]:
    for pattern in _PATH_LINE_PATTERNS:
        match = pattern.search(text)
        if match:
            return Path(_clean_path(match.group("path"))), int(match.group("line"))
    quoted = _QUOTED_PATH_RE.search(text)
    if quoted:
        path = Path(_clean_path(quoted.group("path")))
        after = text[quoted.end() :]
        line_match = _LINE_TOKEN_RE.search(after)
        if line_match:
            return path, int(line_match.group("line"))
        return path, 0
    return None, 0


def _enabled_state(text: str) -> bool | None:
    lowered = text.lower()
    if any(token in lowered for token in ("disabled", "disable", "inactive", "off", "已禁用", "禁用")):
        return False
    if any(token in lowered for token in ("enabled", "enable", "active", "on", "已启用", "启用")):
        return True
    return None


def _condition(text: str) -> str:
    patterns = (
        r"\bcond(?:ition)?\s*[:=]\s*(?P<value>.+)$",
        r"\bif\s*\((?P<value>.+?)\)\s*$",
        r"\bif\s+(?P<value>.+)$",
        r"条件\s*[:=]\s*(?P<value>.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _strip_condition(match.group("value"))
    return ""


def _strip_condition(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+(?:enabled|disabled|active|inactive|已启用|已禁用)\b.*$", "", text, flags=re.IGNORECASE)
    return text.strip(" ;")


def _clean_path(value: str) -> str:
    text = str(value or "").strip().strip("'\"")
    return text.replace("/", "\\") if re.match(r"^[A-Za-z]:/", text) else text


def _is_header_or_noise(line: str) -> bool:
    lowered = line.lower()
    if lowered in {"breaklist", "breakpoints", "no breakpoints", "no breakpoint"}:
        return True
    if lowered.startswith(("breakpoints", "break list", "id ", "num ", "---")):
        return True
    return False


def _snapshot_id(
    project_path: str | Path | None,
    target_name: str,
    breakpoints: Iterable[RemoteBreakpoint],
    raw_text: str,
) -> str:
    payload = "|".join(
        [
            str(project_path or ""),
            str(target_name or ""),
            hashlib.sha1(str(raw_text or "").encode("utf-8", errors="replace")).hexdigest(),
            ";".join(
                f"{item.remote_id}:{item.path}:{item.line}:{item.enabled}:{item.condition}"
                for item in breakpoints
            ),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()
    return f"keil-breakpoints-{digest[:16]}"


__all__ = [
    "KeilBreakpointListParseResult",
    "incomplete_keil_breakpoint_snapshot",
    "keil_breakpoint_list_command",
    "parse_keil_breakpoint_list",
]
