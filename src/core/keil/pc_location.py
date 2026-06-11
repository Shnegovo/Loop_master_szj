"""Read Keil PC evidence through debug commands and map it to source."""

from __future__ import annotations

import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.core.debug_snapshots import DebugPcLocation
from src.core.keil.source_line_address import resolve_address_source_line


@dataclass(frozen=True)
class KeilPcReadResult:
    attempted: bool
    pc_location: DebugPcLocation
    command: str = "EVAL PC"
    raw_text: str = ""
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.pc_location.address is not None and not self.error


def read_keil_pc_location(
    session,
    *,
    axf_path: str | Path | None = None,
    source_roots: Iterable[str | Path] = (),
    command: str = "EVAL PC",
) -> KeilPcReadResult:
    text, error = capture_keil_command_log(session, command)
    if not text:
        return KeilPcReadResult(
            attempted=True,
            command=command,
            pc_location=DebugPcLocation(
                source="keil_eval_pc",
                complete=False,
                message=error or "Keil PC 命令未返回文本",
            ),
            raw_text=text,
            error=error or "empty PC command output",
        )
    address = parse_keil_eval_address(text)
    if address is None:
        return KeilPcReadResult(
            attempted=True,
            command=command,
            pc_location=DebugPcLocation(
                source="keil_eval_pc",
                complete=False,
                message="Keil PC 输出未解析出地址",
            ),
            raw_text=text,
            error="PC address not found",
        )

    path = None
    line = None
    mapping_message = "源码行未映射"
    if axf_path:
        resolved = resolve_address_source_line(
            axf_path,
            address,
            source_roots=source_roots,
            allow_nearest=True,
            max_address_delta=16,
        )
        if resolved.resolved:
            path = resolved.path
            line = resolved.line
            mapping_message = "源码行精确映射" if resolved.exact else "源码行邻近映射"
        else:
            mapping_message = resolved.error or mapping_message

    return KeilPcReadResult(
        attempted=True,
        command=command,
        pc_location=DebugPcLocation(
            path=path,
            line=line,
            address=address,
            source="keil_eval_pc",
            complete=True,
            message=f"Keil PC 已回读；{mapping_message}",
        ),
        raw_text=text,
    )


def capture_keil_command_log(session, command: str) -> tuple[str, str]:
    log_path = Path(tempfile.gettempdir()) / f"loopmaster_keil_cmd_{uuid.uuid4().hex}.log"
    try:
        try:
            log_path.unlink()
        except OSError:
            pass
        session.execute_command(f"LOG > {log_path}", echo=True)
        session.execute_command(command, echo=True)
        session.execute_command("LOG OFF", echo=True)
        return log_path.read_text(encoding="utf-8", errors="replace"), ""
    except Exception as exc:
        return "", str(exc)
    finally:
        try:
            log_path.unlink()
        except OSError:
            pass


_HEX_ADDRESS_RE = re.compile(r"\b0x(?P<address>[0-9a-fA-F]{6,16})\b")


def parse_keil_eval_address(text: str) -> int | None:
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.upper().startswith("LOG "):
            continue
        match = _HEX_ADDRESS_RE.search(line)
        if match:
            try:
                return int(match.group("address"), 16)
            except ValueError:
                return None
    return None


__all__ = [
    "KeilPcReadResult",
    "capture_keil_command_log",
    "parse_keil_eval_address",
    "read_keil_pc_location",
]
