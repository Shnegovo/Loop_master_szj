"""Probe Keil run-to-cursor through a temporary breakpoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.run_to_cursor import (  # noqa: E402
    KeilRunToCursorRequest,
    run_keil_to_cursor_transaction,
)
from src.core.keil.uvsock import KeilUvscLiveSession, UvscRuntimeControlResult  # noqa: E402


DEFAULT_PROJECT = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
DEFAULT_SOURCE = ROOT / "firmware" / "keil_f401_variable_probe" / "main.c"
DEFAULT_AXF = ROOT / "firmware" / "keil_f401_variable_probe" / "Objects" / "f401_variable_probe.axf"
DEFAULT_SOURCE_ROOT = ROOT / "firmware" / "keil_f401_variable_probe"
DEFAULT_TARGET = "STM32F401CCU6 Variable Probe"


class FakeRunToCursorSession:
    def __init__(self, address: int) -> None:
        self.address = int(address)
        self.breakpoints: dict[str, int] = {}
        self.next_id = 0
        self.running = False
        self.log_path: Path | None = None
        self.commands: list[str] = []

    def execute_command(self, command: str, *, echo: bool = False):
        self.commands.append(command)
        text = str(command)
        if text.startswith("LOG >"):
            self.log_path = Path(text.split(">", 1)[1].strip().strip('"'))
            return command
        if text == "LOG OFF":
            self.log_path = None
            return command
        if text == "BL":
            if self.log_path is not None:
                lines = ["Breakpoints"]
                for remote_id, address in sorted(self.breakpoints.items(), key=lambda item: int(item[0])):
                    lines.append(f"{remote_id}: enabled 0x{address:08X}")
                self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return command
        if text == "EVAL PC":
            if self.log_path is not None:
                self.log_path.write_text(f"EVAL PC\n0x{self.address:08X} {self.address}\nLOG OFF\n", encoding="utf-8")
            return command
        if text.startswith("BS "):
            address = int(text.split(None, 1)[1], 0)
            remote_id = str(self.next_id)
            self.next_id += 1
            self.breakpoints[remote_id] = address
            return command
        if text.startswith("BK "):
            self.breakpoints.pop(text.split(None, 1)[1], None)
            return command
        return command

    def target_running(self) -> bool | None:
        if self.running:
            self.running = False
            return False
        return False

    def run_target(self) -> UvscRuntimeControlResult:
        self.running = True
        return UvscRuntimeControlResult(
            attempted=True,
            action="run",
            succeeded=True,
            target_running=True,
        )

    def halt_target(self) -> UvscRuntimeControlResult:
        self.running = False
        return UvscRuntimeControlResult(
            attempted=True,
            action="halt",
            succeeded=True,
            target_running=False,
        )

    def reset_target(self) -> UvscRuntimeControlResult:
        self.running = False
        return UvscRuntimeControlResult(
            attempted=True,
            action="reset",
            succeeded=True,
            target_running=False,
        )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Keil run-to-cursor transaction.")
    parser.add_argument("--live", action="store_true", help="Run against an existing Keil UVSOCK debug session.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--axf", default=str(DEFAULT_AXF))
    parser.add_argument("--line", type=int, default=62)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    source = Path(args.source).expanduser().resolve()
    axf = Path(args.axf).expanduser().resolve()
    request = KeilRunToCursorRequest(
        project_path=project,
        target_name=args.target,
        source_path=source,
        line=int(args.line),
        axf_path=axf,
        source_roots=(DEFAULT_SOURCE_ROOT,),
        timeout_s=float(args.timeout),
        reset_before_run=True,
    )

    fake = FakeRunToCursorSession(0x08000164)
    fake_result = run_keil_to_cursor_transaction(fake, request)
    _assert(fake_result.succeeded, fake_result.summary())
    _assert(fake_result.temp_remote_id == "0", f"fake temp breakpoint id mismatch: {fake_result.to_record()!r}")
    _assert(fake_result.cleanup_succeeded, "fake temporary breakpoint cleanup did not succeed")
    _assert(not fake.breakpoints, f"fake temporary breakpoint leaked: {fake.breakpoints!r}")

    record = {
        "fake": fake_result.to_record(),
        "fake_commands": fake.commands,
    }

    if args.live:
        with KeilUvscLiveSession.connect_existing(
            root=Path(args.keil_root),
            port=int(args.port),
            connection_name="LoopMasterRunToCursorProbe",
            require_debug=True,
        ) as session:
            live_result = run_keil_to_cursor_transaction(session, request)
        record["live"] = live_result.to_record()
        _assert(live_result.succeeded, live_result.summary())
        _assert(live_result.temp_remote_id != "", "live temporary breakpoint id missing")
        _assert(live_result.cleanup_succeeded, "live temporary breakpoint cleanup did not succeed")
        _assert(live_result.hit_pc is not None and live_result.hit_pc.line == int(args.line), f"live PC mismatch: {live_result.to_record()!r}")
        after_cleanup = live_result.after_cleanup_snapshot
        _assert(after_cleanup is not None, "live cleanup snapshot missing")
        leaked = [
            item for item in after_cleanup.breakpoints
            if item.remote_id == live_result.temp_remote_id
        ]
        _assert(not leaked, f"live temporary breakpoint leaked: {live_result.to_record()!r}")

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    print("PASS Keil run-to-cursor probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
