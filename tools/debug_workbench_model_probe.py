"""Probe pure debugger workbench models."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_workbench import (  # noqa: E402
    BreakpointStore,
    DebugCommandPlan,
    DebugRuntimeState,
    DebugWorkbenchSession,
    debug_command_plans_for_status,
    debug_actions_for_status,
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


def _action_map(session: DebugWorkbenchSession) -> dict[str, bool]:
    return {action.key: action.enabled for action in session.actions()}


def _plan_map(session: DebugWorkbenchSession) -> dict[str, DebugCommandPlan]:
    return {plan.key: plan for plan in session.command_plans()}


def _assert_plan_shape(plans: dict[str, DebugCommandPlan]) -> None:
    expected = {
        "discover",
        "attach",
        "disconnect",
        "halt",
        "run",
        "step",
        "sync_breakpoints",
        "write_variables",
    }
    _assert(set(plans) == expected, f"debug command plan keys changed: {sorted(plans)}")
    for key, plan in plans.items():
        _assert(plan.key == key, f"plan key mismatch for {key}")
        _assert(plan.title, f"plan title missing for {key}")
        _assert(plan.intent, f"plan intent missing for {key}")
        _assert(plan.status in {"可执行", "计划就绪", "等待条件"}, f"unexpected plan status for {key}: {plan.status}")
        _assert(not plan.execution_enabled or key == "discover", f"{key} should not become executable in plan-only mode")
        if key != "discover" and plan.preconditions_met:
            _assert("烟测" in plan.disabled_reason or "只显示计划" in plan.disabled_reason, f"{key} lacks preview guard")


def _assert_risky_plans_disabled(plans: dict[str, DebugCommandPlan]) -> None:
    for key in ("attach", "disconnect", "halt", "run", "step", "sync_breakpoints", "write_variables"):
        plan = plans[key]
        _assert(not plan.execution_enabled, f"{key} must remain execution-disabled")
        _assert(plan.requirements, f"{key} should explain requirements")
        _assert(plan.safety_notes, f"{key} should explain safety notes")
        _assert(plan.preview_steps, f"{key} should expose preview steps")


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

        session = DebugWorkbenchSession()
        session.set_project(project)
        _assert(session.status.state == DebugRuntimeState.DISCONNECTED, "initial debug state mismatch")
        actions = _action_map(session)
        _assert(actions["discover"], "discover should be enabled while disconnected")
        _assert(not actions["attach"], "attach should be disabled before discovery")
        _assert(not actions["halt"], "halt should be disabled before attach")
        plans = _plan_map(session)
        _assert_plan_shape(plans)
        _assert_risky_plans_disabled(plans)
        _assert(plans["discover"].execution_enabled, "safe discover preflight should remain executable")
        _assert(not plans["attach"].preconditions_met, "attach precondition should be blocked before discovery")

        session.mark_discovered(can_attach=True)
        _assert(session.status.state == DebugRuntimeState.KEIL_DISCOVERED, "discovered state mismatch")
        actions = _action_map(session)
        _assert(actions["discover"], "rediscover should remain enabled")
        _assert(actions["attach"], "attach should be enabled after discovery")
        _assert(not actions["run"], "run should be disabled before attach")
        plans = _plan_map(session)
        _assert_plan_shape(plans)
        _assert_risky_plans_disabled(plans)
        _assert(plans["attach"].preconditions_met, "attach plan should be ready after discovery")
        _assert(not plans["attach"].execution_enabled, "attach plan must stay disabled until smoke stage")

        session.mark_attached(running=True, runtime_control=True, breakpoint_sync=True)
        _assert(session.status.state == DebugRuntimeState.RUNNING, "running state mismatch")
        actions = _action_map(session)
        _assert(actions["disconnect"], "disconnect should be enabled after attach")
        _assert(actions["halt"], "halt should be enabled while running")
        _assert(not actions["run"], "run should be disabled while already running")
        _assert(actions["sync_breakpoints"], "breakpoint sync should be enabled when declared")
        _assert(not actions["write_variables"], "write variables should default disabled")
        plans = _plan_map(session)
        _assert_plan_shape(plans)
        _assert_risky_plans_disabled(plans)
        _assert(plans["halt"].preconditions_met, "halt plan should be ready while target is running")
        _assert(not plans["run"].preconditions_met, "run plan should be blocked while already running")
        _assert(plans["sync_breakpoints"].preconditions_met, "sync breakpoint plan should reflect capability")

        session.update_runtime(running=False, current_pc_line=3, run_line=4)
        _assert(session.status.state == DebugRuntimeState.PAUSED, "paused state mismatch")
        _assert(session.status.current_pc_line == 3, "pc line was not retained")
        actions = _action_map(session)
        _assert(actions["run"], "run should be enabled while paused")
        _assert(actions["step"], "step should be enabled while paused with runtime control")
        _assert(not actions["halt"], "halt should be disabled while paused")
        plans = _plan_map(session)
        _assert_plan_shape(plans)
        _assert_risky_plans_disabled(plans)
        _assert(plans["run"].preconditions_met, "run plan should be ready while paused")
        _assert(plans["step"].preconditions_met, "step plan should be ready while paused")
        _assert(not plans["halt"].preconditions_met, "halt plan should be blocked while paused")

        session.mark_attached(running=False, runtime_control=True, breakpoint_sync=True, variable_write=True)
        plans = _plan_map(session)
        _assert_plan_shape(plans)
        _assert_risky_plans_disabled(plans)
        write_plan = plans["write_variables"]
        _assert(write_plan.preconditions_met, "write variable precondition should reflect declared capability")
        _assert(not write_plan.execution_enabled, "write variable plan must remain disabled even when capability is declared")
        write_text = " ".join(write_plan.requirements + write_plan.safety_notes + write_plan.preview_steps)
        for phrase in ("RAM", "类型", "回读", "审计", "范围"):
            _assert(phrase in write_text, f"write variable plan missing safety phrase: {phrase}")
        for value in write_plan.__dict__.values():
            _assert(not callable(value), "write variable plan must not carry executable objects")

        status = session.mark_error("synthetic bridge timeout")
        _assert(status.state == DebugRuntimeState.ERROR, "error state mismatch")
        _assert(debug_actions_for_status(status)[0].enabled, "discover should be enabled after error")
        plans = {plan.key: plan for plan in debug_command_plans_for_status(status)}
        _assert_plan_shape(plans)
        _assert_risky_plans_disabled(plans)
        _assert("synthetic bridge timeout" in plans["attach"].disabled_reason, "error plan should preserve backend reason")
        session.disconnect()
        _assert(session.status.state == DebugRuntimeState.DISCONNECTED, "disconnect state mismatch")

        missing_preflight = SimpleNamespace(
            discovery=SimpleNamespace(installed=False),
            can_attempt_connection=False,
            reasons=("Keil/uVision was not discovered",),
            uvision_running=False,
        )
        status = session.apply_uvsock_preflight(missing_preflight)
        _assert(status.state == DebugRuntimeState.ERROR, "missing Keil preflight should map to error")
        _assert("未发现" in status.error, "preflight error should retain translated reason")

        idle_preflight = SimpleNamespace(
            discovery=SimpleNamespace(installed=True),
            can_attempt_connection=False,
            reasons=("uVision is not running",),
            uvision_running=False,
        )
        status = session.apply_uvsock_preflight(idle_preflight)
        _assert(status.state == DebugRuntimeState.KEIL_DISCOVERED, "idle preflight state mismatch")
        actions = _action_map(session)
        _assert(actions["discover"], "discover should remain enabled after idle preflight")
        _assert(not actions["attach"], "attach should be disabled when UVSOCK cannot attempt")

        ready_preflight = SimpleNamespace(
            discovery=SimpleNamespace(installed=True),
            can_attempt_connection=True,
            reasons=(),
            uvision_running=True,
        )
        status = session.apply_uvsock_preflight(ready_preflight)
        _assert(status.state == DebugRuntimeState.KEIL_DISCOVERED, "ready preflight state mismatch")
        _assert(_action_map(session)["attach"], "attach should be enabled when UVSOCK can attempt")

        failed_connection = SimpleNamespace(
            connected=False,
            error="UVSC_OpenConnection failed",
            target_running=None,
        )
        status = session.apply_uvsock_connection(failed_connection)
        _assert(status.state == DebugRuntimeState.ERROR, "failed connection should map to error")
        _assert("OpenConnection" in status.error, "connection error should be retained")

        running_connection = SimpleNamespace(
            connected=True,
            error="",
            target_running=True,
        )
        status = session.apply_uvsock_connection(running_connection)
        _assert(status.state == DebugRuntimeState.RUNNING, "running connection state mismatch")
        _assert(_action_map(session)["halt"], "halt should be enabled after connected running state")

        paused_connection = SimpleNamespace(
            connected=True,
            error="",
            target_running=False,
        )
        status = session.apply_uvsock_connection(paused_connection)
        _assert(status.state == DebugRuntimeState.PAUSED, "paused connection state mismatch")
        actions = _action_map(session)
        _assert(actions["run"], "run should be enabled after connected paused state")
        _assert(actions["step"], "step should be enabled after connected paused state")
        _assert(not actions["write_variables"], "write variables must stay disabled after UVSOCK projection")

    print("PASS debug workbench model probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
