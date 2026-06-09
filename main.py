#!/usr/bin/env python3
"""LoopMaster — ELF/AXF symbol & struct memory layout analyzer with live scope."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main():
    gui_commands = {"scope"}
    cli_commands = {"info", "symbols", "variables", "struct"}

    if len(sys.argv) > 1 and sys.argv[1] == "scan-probes":
        import json
        from src.core.mem_backend import SWDBackend

        out_path = None
        if "--output" in sys.argv:
            i = sys.argv.index("--output")
            if i + 1 < len(sys.argv):
                out_path = sys.argv[i + 1]
        payload = SWDBackend.scan_probes()
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if out_path:
            Path(out_path).write_text(text, encoding="utf-8")
        else:
            print(text)
        return

    # Double-click/no-arg launch opens the GUI. CLI subcommands still work.
    if len(sys.argv) == 1 or sys.argv[1] in gui_commands:
        import argparse
        parser = argparse.ArgumentParser(prog="loopmaster scope", description="MCU Variable Oscilloscope")
        if len(sys.argv) > 1 and sys.argv[1] == "scope":
            parser.add_argument("scope", help="(reserved)")
        parser.add_argument("elf", type=str, nargs="?", default=None, help="Path to ELF/AXF file (optional)")
        parser.add_argument("--pack", type=str, default=None, help="Path to CMSIS-Pack file")
        parser.add_argument("--target", type=str, default=None, help="pyOCD target name")
        args = parser.parse_args()

        from src.ui.gui import run_scope
        run_scope(args.elf, pack_path=args.pack, target=args.target)
    elif sys.argv[1] in cli_commands:
        from src.ui.cli import main as cli_main
        cli_main()
    else:
        from src.ui.gui import run_scope
        run_scope(sys.argv[1])


if __name__ == "__main__":
    main()
