"""Probe Keil PC readback and AXF source mapping."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.pc_location import parse_keil_eval_address, read_keil_pc_location  # noqa: E402
from src.core.keil.uvsock import KeilUvscLiveSession  # noqa: E402


AXF = ROOT / "firmware" / "keil_f401_variable_probe" / "Objects" / "f401_variable_probe.axf"
SOURCE_ROOT = ROOT / "firmware" / "keil_f401_variable_probe"


class FakeSession:
    def __init__(self, text: str) -> None:
        self.text = text
        self.commands: list[str] = []
        self.log_path: Path | None = None

    def execute_command(self, command: str, *, echo: bool = False):
        self.commands.append(command)
        if command.startswith("LOG >"):
            self.log_path = Path(command.split(">", 1)[1].strip().strip('"'))
            return command
        if command == "EVAL PC" and self.log_path is not None:
            self.log_path.write_text(self.text, encoding="utf-8")
        return command


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Keil PC readback.")
    parser.add_argument("--live", action="store_true", help="Read PC from an existing Keil UVSOCK debug session.")
    parser.add_argument("--step", action="store_true", help="With --live, execute one Keil trace step and read PC again.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sample = "EVAL PC\n0x0800015A 134218074\nLOG OFF\n"
    _assert(parse_keil_eval_address(sample) == 0x0800015A, "PC address parse failed")

    fake = FakeSession(sample)
    result = read_keil_pc_location(fake, axf_path=AXF, source_roots=(SOURCE_ROOT,))
    _assert(result.succeeded, f"fake PC read failed: {result!r}")
    _assert(result.pc_location.address == 0x0800015A, f"fake PC address mismatch: {result!r}")
    _assert(result.pc_location.path is not None and result.pc_location.path.name == "main.c", f"fake PC path mismatch: {result!r}")
    _assert(result.pc_location.line == 26, f"fake PC line mismatch: {result!r}")

    record = {
        "fake": result.pc_location.to_record(),
        "fake_commands": fake.commands,
    }

    if args.live:
        with KeilUvscLiveSession.connect_existing(
            root=Path(args.keil_root),
            port=4827,
            connection_name="LoopMasterPcProbe",
            require_debug=True,
        ) as session:
            live = read_keil_pc_location(session, axf_path=AXF, source_roots=(SOURCE_ROOT,))
            step_result = None
            after_step = None
            halted_before_step = None
            if args.step:
                try:
                    if session.target_running() is True:
                        halted_before_step = session.halt_target()
                except Exception:
                    halted_before_step = None
                step_result = session.step_target()
                after_step = read_keil_pc_location(session, axf_path=AXF, source_roots=(SOURCE_ROOT,))
        record["live"] = {
            "succeeded": live.succeeded,
            "pc": live.pc_location.to_record(),
            "error": live.error,
            "raw": live.raw_text,
        }
        _assert(live.succeeded, f"live PC read failed: {live!r}")
        _assert(live.pc_location.address is not None, f"live PC address missing: {live!r}")
        _assert(live.pc_location.path is not None, f"live PC source mapping missing: {live!r}")
        if args.step:
            record["step"] = {
                "succeeded": step_result.succeeded if step_result is not None else False,
                "summary": step_result.summary() if step_result is not None else "",
                "error": step_result.error if step_result is not None else "",
                "target_running": step_result.target_running if step_result is not None else None,
                "after_pc": after_step.pc_location.to_record() if after_step is not None else None,
                "halted_before_step": halted_before_step.summary() if halted_before_step is not None else "",
            }
            _assert(step_result is not None and step_result.succeeded, f"live step failed: {step_result!r}")
            _assert(step_result.target_running is False, f"live step should leave target paused: {step_result!r}")
            _assert(after_step is not None and after_step.succeeded, f"after-step PC read failed: {after_step!r}")

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    print("PASS Keil PC location probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
