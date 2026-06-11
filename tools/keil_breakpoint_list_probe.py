"""Probe Keil breakpoint list parsing and backend snapshot plumbing."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.breakpoint_list import (  # noqa: E402
    incomplete_keil_breakpoint_snapshot,
    parse_keil_breakpoint_list,
)
from src.core.keil.backend import KeilBackendConfig, KeilUvSockBackendAdapter  # noqa: E402


COMPLETE_SAMPLE = r"""
Breakpoints
1     enabled   D:\demo\Core\Src\main.c:42
2     disabled  D:\demo\Core\Src\pid.c line 88 if speed > 60
#3    "D:\demo\Core Inc\pid params.h" line 12 condition: AnglePID.Kp > 3.0
0x4   已禁用   D:\demo\Core\Src\motor.c(33)
"""

INCOMPLETE_SAMPLE = COMPLETE_SAMPLE + "5     enabled   main_without_path\n"

ADDRESS_ONLY_SAMPLE = """BL
 0: (E 0x08000164) '0x08000164', CNT=1, enabled
LOG OFF
"""


class FakeSession:
    def __init__(self, text: str) -> None:
        self.text = text
        self.commands: list[str] = []

    def try_execute_command_text(self, command: str, *, echo: bool = False):
        self.commands.append(command)
        return self.text, ""


class FakeLogSession:
    def __init__(self, log_text: str) -> None:
        self.log_text = log_text
        self.commands: list[str] = []
        self.log_path: Path | None = None

    def try_execute_command_text(self, command: str, *, echo: bool = False):
        self.commands.append(f"try:{command}")
        return "BL", ""

    def execute_command(self, command: str, *, echo: bool = False):
        self.commands.append(command)
        if command.startswith("LOG >"):
            self.log_path = Path(command.split(">", 1)[1].strip().strip('"'))
            return command
        if command == "BL" and self.log_path is not None:
            self.log_path.write_text(self.log_text, encoding="utf-8")
        return command


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    result = parse_keil_breakpoint_list(
        COMPLETE_SAMPLE,
        project_path=Path("D:/demo/demo.uvprojx"),
        target_name="DebugDemo",
        captured_at="2026-06-11T00:00:00+00:00",
    )
    snapshot = result.snapshot
    _assert(snapshot.complete, "parsed snapshot should be complete")
    _assert(len(snapshot.breakpoints) == 4, f"breakpoint count mismatch: {snapshot.breakpoints!r}")
    by_id = {bp.remote_id: bp for bp in snapshot.breakpoints}
    _assert(by_id["1"].line == 42 and by_id["1"].enabled is True, f"bp1 mismatch: {by_id['1']!r}")
    _assert(by_id["2"].line == 88 and by_id["2"].enabled is False, f"bp2 mismatch: {by_id['2']!r}")
    _assert(by_id["2"].condition == "speed > 60", f"bp2 condition mismatch: {by_id['2']!r}")
    _assert(by_id["3"].line == 12 and "AnglePID.Kp" in by_id["3"].condition, f"bp3 mismatch: {by_id['3']!r}")
    _assert(by_id["0x4"].line == 33 and by_id["0x4"].enabled is False, f"bp4 mismatch: {by_id['0x4']!r}")
    rows = dict(result.diagnostic_rows())
    _assert(rows.get("远端断点枚举") == "完成", f"diagnostics mismatch: {rows!r}")

    incomplete_result = parse_keil_breakpoint_list(
        INCOMPLETE_SAMPLE,
        project_path=Path("D:/demo/demo.uvprojx"),
        target_name="DebugDemo",
        captured_at="2026-06-11T00:00:00+00:00",
    )
    _assert(not incomplete_result.snapshot.complete, "unresolved breakpoint lines should make snapshot incomplete")
    _assert("未映射" in incomplete_result.snapshot.error, f"incomplete error mismatch: {incomplete_result.snapshot.error!r}")

    address_result = parse_keil_breakpoint_list(
        ADDRESS_ONLY_SAMPLE,
        project_path=Path("D:/demo/demo.uvprojx"),
        target_name="DebugDemo",
        captured_at="2026-06-11T00:00:00+00:00",
        command="LOG+BL",
    )
    _assert(not address_result.snapshot.complete, "address-only breakpoint should keep snapshot source-incomplete")
    _assert(len(address_result.snapshot.breakpoints) == 1, f"address breakpoint missing: {address_result.snapshot!r}")
    address_bp = address_result.snapshot.breakpoints[0]
    _assert(address_bp.address == 0x08000164, f"address breakpoint mismatch: {address_bp!r}")
    _assert(address_bp.path is None and address_bp.line == 0, f"address-only source mismatch: {address_bp!r}")

    incomplete = incomplete_keil_breakpoint_snapshot(
        project_path=Path("D:/demo/demo.uvprojx"),
        target_name="DebugDemo",
        error="no command text",
    )
    _assert(not incomplete.complete and incomplete.error == "no command text", f"incomplete mismatch: {incomplete!r}")

    adapter = KeilUvSockBackendAdapter(KeilBackendConfig(root=Path("D:/Keil"), port=4827))
    fake_session = FakeSession(COMPLETE_SAMPLE)
    backend_snapshot = adapter._breakpoint_snapshot_from_session(
        fake_session,
        project_path=Path("D:/demo/demo.uvprojx"),
        target_name="DebugDemo",
        captured_at="2026-06-11T00:00:00+00:00",
    )
    _assert(fake_session.commands == ["BL"], f"backend command mismatch: {fake_session.commands!r}")
    _assert(backend_snapshot.complete and len(backend_snapshot.breakpoints) == 4, f"backend snapshot mismatch: {backend_snapshot!r}")

    log_session = FakeLogSession(ADDRESS_ONLY_SAMPLE)
    log_snapshot = adapter._breakpoint_snapshot_from_session(
        log_session,
        project_path=Path("D:/demo/demo.uvprojx"),
        target_name="DebugDemo",
        captured_at="2026-06-11T00:00:00+00:00",
    )
    _assert(log_session.commands[:2] == ["try:BL", "LOG > " + str(log_session.log_path)], f"log fallback command mismatch: {log_session.commands!r}")
    _assert(not log_snapshot.complete and len(log_snapshot.breakpoints) == 1, f"log snapshot mismatch: {log_snapshot!r}")
    _assert(log_snapshot.breakpoints[0].address == 0x08000164, f"log address mismatch: {log_snapshot.breakpoints!r}")

    empty_session = SimpleNamespace(try_execute_command_text=lambda command, echo=False: ("", "no text"))
    failed_snapshot = adapter._breakpoint_snapshot_from_session(
        empty_session,
        project_path=Path("D:/demo/demo.uvprojx"),
        target_name="DebugDemo",
        captured_at="2026-06-11T00:00:00+00:00",
    )
    _assert(not failed_snapshot.complete and "no text" in failed_snapshot.error, f"failed snapshot mismatch: {failed_snapshot!r}")

    print("PASS Keil breakpoint list probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
