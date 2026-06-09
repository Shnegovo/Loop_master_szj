"""Probe pure debugger workbench models."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_workbench import (  # noqa: E402
    BreakpointStore,
    line_decorations,
    load_code_document,
    search_document,
    source_entries_from_keil_project,
    source_tree_from_entries,
)
from src.core.keil.project import parse_keil_project  # noqa: E402


PROJECT = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project>
  <Targets>
    <Target>
      <TargetName>DebugDemo</TargetName>
      <TargetOption>
        <TargetCommonOption>
          <OutputDirectory>Objects\\</OutputDirectory>
          <OutputName>debug_demo</OutputName>
          <CreateExecutable>1</CreateExecutable>
        </TargetCommonOption>
      </TargetOption>
      <Groups>
        <Group>
          <GroupName>App</GroupName>
          <Files>
            <File><FileName>main.c</FileName><FileType>1</FileType><FilePath>..\\Core\\Src\\main.c</FilePath></File>
            <File><FileName>pid.c</FileName><FileType>1</FileType><FilePath>..\\Core\\Src\\pid.c</FilePath></File>
            <File><FileName>pid.h</FileName><FileType>5</FileType><FilePath>..\\Core\\Inc\\pid.h</FilePath></File>
          </Files>
        </Group>
        <Group>
          <GroupName>Startup</GroupName>
          <Files>
            <File><FileName>startup.s</FileName><FileType>2</FileType><FilePath>startup.s</FilePath></File>
          </Files>
        </Group>
      </Groups>
    </Target>
  </Targets>
</Project>
"""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopmaster-debug-model-") as tmp:
        root = Path(tmp)
        project_dir = root / "MDK-ARM"
        (root / "Core" / "Src").mkdir(parents=True)
        (root / "Core" / "Inc").mkdir(parents=True)
        project_dir.mkdir(parents=True)
        (root / "Core" / "Src" / "main.c").write_text(
            "int main(void) {\n"
            "    int speed = 0;\n"
            "    speed += 60;\n"
            "    return speed;\n"
            "}\n",
            encoding="utf-8",
        )
        (root / "Core" / "Src" / "pid.c").write_text("// pid fixture\n", encoding="utf-8")
        (root / "Core" / "Inc" / "pid.h").write_text("// pid header\n", encoding="utf-8")
        (project_dir / "startup.s").write_text("; startup\n", encoding="utf-8")
        project_path = project_dir / "DebugDemo.uvprojx"
        project_path.write_text(PROJECT, encoding="utf-8")

        project = parse_keil_project(project_path)
        entries = source_entries_from_keil_project(project)
        tree = source_tree_from_entries(entries)
        _assert(len(entries) == 4, f"expected 4 source/header entries, got {len(entries)}")
        _assert({entry.language for entry in entries} == {"c", "c-header", "asm"}, "language classification changed")
        _assert(tree.name == "Sources" and len(tree.children) == 2, "source tree group shape changed")

        main_path = root / "Core" / "Src" / "main.c"
        pid_path = root / "Core" / "Src" / "pid.c"
        store = BreakpointStore()
        bp = store.add(main_path, 12)
        _assert(bp.enabled and bp.line == 12, "breakpoint add failed")
        _assert(store.get(main_path, 12) is not None, "breakpoint lookup failed")
        store.set_condition(main_path, 12, "speed > 60")
        store.set_enabled(main_path, 12, False)
        store.set_verified(main_path, 12, True)
        hit = store.record_hit(main_path, 12, 3)
        _assert(hit.hit_count == 3 and hit.verified and not hit.enabled, "breakpoint state update failed")

        store.add(pid_path, 20, condition="err != 0")
        _assert(len(store.all()) == 2, "breakpoint store count mismatch")
        _assert(len(store.for_file(main_path)) == 1, "breakpoints for file mismatch")
        _assert(store.toggle(pid_path, 20) is None, "toggle should remove existing breakpoint")
        _assert(store.toggle(pid_path, 21) is not None, "toggle should add new breakpoint")
        _assert(store.remove(main_path, 12), "remove should return true for existing breakpoint")
        _assert(len(store.all()) == 1, "final breakpoint count mismatch")

        store.add(main_path, 3, condition="speed > 50")
        document = load_code_document(main_path)
        _assert(document.line_count == 6, "document line count mismatch")
        _assert(document.language == "c", "document language mismatch")
        matches = search_document(document, "speed")
        _assert(len(matches) == 3, f"expected 3 speed matches, got {len(matches)}")
        decorations = line_decorations(
            document,
            store,
            current_pc_line=2,
            run_line=4,
            search_query="speed",
        )
        kinds = {(item.line, item.kind) for item in decorations}
        _assert((3, "breakpoint") in kinds, "breakpoint decoration missing")
        _assert((2, "pc") in kinds, "pc decoration missing")
        _assert((4, "run") in kinds, "run decoration missing")
        _assert(sum(1 for item in decorations if item.kind == "search") == 3, "search decoration count mismatch")

    print("PASS debug workbench model probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
