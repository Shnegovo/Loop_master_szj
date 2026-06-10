# LoopMaster Development Log

This log records each development stage, what changed, how it was verified, and the next target. Keep it updated before each commit that closes a stage.

## Development Cadence Rules

- Stages should now represent major milestones or large version-level progress, not isolated micro tasks.
- Each completed stage must record the milestone result, verification, important notes, and the next target before moving on.
- Small UI polish, probe adjustments, and local cleanup should be bundled into the active milestone unless they are urgent blockers.
- Packaging and GitHub Release work should happen only for major versions or large milestone closures, not for every small stage.
- When the work can be split cleanly, use multiple agents in parallel for investigation, implementation, review, or probe runs, then merge the results into one milestone record.

## Stage 0 - v2.1.0 Baseline

- Release: `v2.1.0`
- GitHub: https://github.com/Shnegovo/Loop_master_szj
- Release asset: `LoopMaster_v2.1.exe`
- Baseline commit: `fcb2375`
- Follow-up hardening commit: `780d808`

### Completed

- Cleaned the workspace and pushed source to GitHub.
- Published `v2.1.0` release asset.
- Hardened shutdown paths:
  - GUI stops timers before cleanup.
  - Backend receives shutdown request before sampling/serial cleanup.
  - SWD session close can detach to a daemon cleanup thread instead of blocking forever.
  - Sampling stop now reports whether the thread actually stopped.
  - Serial connect/send/disconnect worker threads are tracked and briefly joined.

### Verified

- `python -m py_compile main.py src\core\collector.py src\core\mem_backend.py src\core\serial_backend.py src\ui\gui.py src\ui\serial_tab.py`
- `python tools\serial_parser_probe.py`
- `python tools\ui_close_process_probe.py --entry main.py --exit-timeout 10`
- Close probe result after hardening: `PASS`, close-to-exit about `187.5ms`.

### Next Target

- Extend close-process probing beyond idle close:
  - synthetic sampling window
  - slow sampling/read-in-progress window
  - serial worker active during close
- Keep the synthetic scenarios out of production app code.

## Stage 1 - Close Probe Scenario Coverage

### Goal

Make shutdown regressions visible before touching larger architecture changes.

### Completed

- Added `tools/ui_close_scenario_entry.py`, a synthetic MainWindow entry used only by probes.
- Added `--scenario` to `tools/ui_close_process_probe.py` for:
  - `idle`
  - `sampling`
  - `slow-sampling`
  - `serial-worker`

### Verified

- `python -m py_compile tools\ui_close_process_probe.py tools\ui_close_scenario_entry.py src\core\collector.py src\core\mem_backend.py src\ui\gui.py`
- `python tools\ui_close_process_probe.py --entry main.py --exit-timeout 10`
  - PASS, close-to-exit about `261.0ms`
- `python tools\ui_close_process_probe.py --scenario sampling --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `596.6ms`
- `python tools\ui_close_process_probe.py --scenario slow-sampling --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `610.2ms`
- `python tools\ui_close_process_probe.py --scenario serial-worker --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `328.7ms`

### Next Target

- UI edge polish and Chinese copy cleanup:
  - translate remaining obvious English tooltips/status text
  - reduce variable tree branch protrusion
  - hide splitter residue when the scope sidebar is collapsed
  - keep changes small and verify with existing UI probes

## Stage 2 - UI Edge Polish And Probe Cleanup

### Goal

Close the visible UI rough edges before starting larger architecture work.

### Completed

- Replaced obvious English UI strings with Chinese copy:
  - target tooltip
  - variable tree folder tooltip
  - curve color tooltips
  - scope sidebar tooltip
  - serial hero baud text
  - probe fallback names
  - sampling rate `MAX` display
- Switched the app and scope axis font preference to `Microsoft YaHei UI`.
- Reduced heavy button weight in the light theme while keeping primary buttons prominent.
- Increased variable tree indentation and explicitly styled tree branch areas to avoid branch/selection protrusions.
- Added `scopeMainSplitter` styling and hid its handle when the scope settings sidebar is collapsed.
- Made the serial assistant splitter non-collapsible, non-opaque during drag, and visually thinner.
- Hardened `PclComboBox` popup positioning:
  - repositions on owner move/resize
  - hides on window deactivate/close
  - clamps popup width/height inside the window
- Cleaned mojibake comments/log messages that could surface in logs or confuse follow-up work.
- Cleaned screenshot-only probe exits so Qt teardown noise does not mask UI PASS results.

### Verified

- `python -m py_compile main.py src\ui\gui.py src\ui\pcl_theme.py src\ui\serial_tab.py tools\ui_combo_popup_probe.py tools\ui_pid_waveform_probe.py`
- `python tools\serial_parser_probe.py`
  - PASS for CSV, FireWater, JustFloat, raw text, and hex lines.
- `python tools\ui_combo_popup_probe.py --output-dir %TEMP%\loopmaster-ui-stage2\combo-final-clean`
  - PASS, popup screenshot complete.
- `python tools\ui_serial_integration_probe.py --output-dir %TEMP%\loopmaster-ui-stage2\serial-rerun`
  - PASS, serial assistant screenshot complete.
- `python tools\ui_workspace_nav_probe.py --output-dir %TEMP%\loopmaster-ui-stage2\nav`
  - PASS, LoopMaster and serial workspace navigation screenshots complete.
- `python tools\ui_scope_splitter_perf_probe.py --output-dir %TEMP%\loopmaster-ui-stage2\splitter-perf-rerun --iterations 240`
  - PASS, avg `9.36ms`, p95 `14.85ms`, max `20.72ms`, slow>24ms `0`.
- `python tools\ui_human_flow_probe.py --output-dir %TEMP%\loopmaster-ui-stage2\human-flow`
  - PASS, covered live data, Halt, Run, splitter drag, sidebar hide/show, narrow window, axis toggles, stop, close.
- `python tools\ui_pid_waveform_probe.py --output-dir %TEMP%\loopmaster-ui-stage2\pid-waveform-clean2`
  - PASS, covered step overshoot, steady micro jitter, high-frequency small jitter, single-sample overshoot, absurd glitch clipping, and mixed three-pane PID.
- `python tools\ui_close_process_probe.py --entry main.py --exit-timeout 10`
  - PASS, close-to-exit about `346.8ms`.
- `python tools\ui_close_process_probe.py --scenario sampling --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `342.2ms`.
- `python tools\ui_close_process_probe.py --scenario serial-worker --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `352.2ms`.

### Notes

- A parallel run of multiple GUI probes made the splitter performance probe exceed budget once; a standalone rerun passed cleanly. Treat splitter performance probes as single-process measurements.
- Screenshot-only probes should avoid Qt teardown assertions; close/lifecycle behavior is covered by `ui_close_process_probe.py`.

### Next Target

- Start architecture foundation work before adding more large features:
  - add transport capability protocols for pyOCD/Keil/serial/replay paths
  - begin decoder extraction from serial backend
  - move pure scope display algorithms out of `MainWindow`
  - prepare a serial controller split so new tools do not continue to bloat `gui.py`
  - add a no-hardware fake transport probe for future Keil and replay work

## Stage 3 - Architecture Foundation Batch 1

### Goal

Start reducing backend coupling so Keil, pyOCD, serial, and future replay/protocol tools can share stable acquisition boundaries.

### Completed

- Added `src/core/transports/` capability protocols:
  - `VariableReadTransport`
  - `SampleSeriesTransport`
  - `SampleRowsTransport`
  - `TargetControl`
  - `VariableWriteTransport`
  - `DebugTransport`
- Changed `DataCollector` typing from concrete `SWDBackend` to `VariableReadTransport`.
- Added `tools/collector_fake_transport_probe.py`, a no-hardware probe that starts the real collector thread against a fake transport.
- Moved serial protocol parsing into `src/core/decoders/serial.py`.
- Added `src/core/decoders/__init__.py` as the public decoder entry.
- Kept `src/core/serial_backend.py` compatibility exports so existing serial tools still import `SerialProtocolParser` and `JUSTFLOAT_TAIL` from the old path.

### Verified

- `python -m py_compile main.py src\core\collector.py src\core\serial_backend.py src\core\decoders\serial.py src\core\transports\base.py src\ui\gui.py src\ui\serial_tab.py tools\collector_fake_transport_probe.py`
- `python -m py_compile src\core\collector.py src\core\transports\base.py src\core\transports\__init__.py src\core\decoders\__init__.py src\core\decoders\serial.py src\core\serial_backend.py tools\collector_fake_transport_probe.py`
- `python tools\collector_fake_transport_probe.py`
  - PASS, collected two fake variable series without hardware.
  - Latest run: `samples=56`, `actual_rate=248.6Hz`.
- `python tools\serial_parser_probe.py`
  - PASS for CSV, FireWater, JustFloat, raw text, and hex lines after decoder extraction.
- `python tools\ui_serial_integration_probe.py --output-dir %TEMP%\loopmaster-stage3\serial`
  - PASS, serial UI still renders after decoder extraction.
- `python tools\ui_close_process_probe.py --scenario sampling --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `784.9ms`.

### Notes

- This stage intentionally does not add Keil control yet. It creates the capability vocabulary needed for a Keil transport to fit cleanly beside pyOCD.
- `DataCollector` still uses duck-typed optional methods (`read_batch_rows`, `read_batch_samples`) for high-rate paths; the new protocols document those capabilities so later refactors can become stricter.
- Keil is installed under `D:\Keil`; a separate read-only discovery pass is in progress for UVSOCK/debug command integration.

### Next Target

- Continue architecture foundation:
  - inspect `D:\Keil` and document the most realistic Keil bridge path
  - extract pure scope display algorithms from `MainWindow`
  - start moving serial worker lifecycle into a controller object
  - keep UI and close-process probes green after each slice

## Stage 4 - Scope Display Algorithm Extraction

### Goal

Move scope display math out of `MainWindow` so later UI/controller refactors can keep waveform behavior stable and testable.

### Completed

- Added `src/ui/scope_algorithms.py` for pure scope display algorithms:
  - adaptive plot FPS caps
  - display point budget
  - interpolation and peak-preserving decimation
  - display thinning
  - PID-friendly Y range calculation and stabilization
- Updated `MainWindow` to delegate display processing and Y-range decisions to the new module.
- Removed the old private interpolation/decimation/thinning/Y-stabilization helpers from `MainWindow`.
- Added `tools/scope_algorithms_probe.py`, a fast no-Qt regression probe for:
  - step response and overshoot readability
  - micro jitter that should not be over-zoomed
  - high-frequency small jitter around a setpoint
  - single-sample overshoot preservation
  - absurd read-glitch clipping
  - high-rate decimation and low-rate interpolation
- Kept the existing full Qt PID waveform probe green after extraction.

### Verified

- `python -m py_compile main.py src\ui\gui.py src\ui\scope_algorithms.py tools\scope_algorithms_probe.py`
- `python tools\scope_algorithms_probe.py`
  - PASS.
- `python tools\ui_pid_waveform_probe.py --output-dir %TEMP%\loopmaster-stage4\pid`
  - PASS, generated six PID-oriented screenshots.
- `python tools\ui_scope_splitter_perf_probe.py --output-dir %TEMP%\loopmaster-stage4\splitter --iterations 240`
  - PASS, avg `6.02ms`, p95 `8.43ms`, p99 `12.66ms`, max `13.87ms`, slow>24ms `0`.
- `python tools\ui_human_flow_probe.py --output-dir %TEMP%\loopmaster-stage4\human-rerun`
  - PASS, covered live data, Halt, Run, splitter drag, sidebar hide/show, narrow window, axis toggles, stop, and close.
- `git diff --check -- src/ui/gui.py src/ui/scope_algorithms.py tools/scope_algorithms_probe.py`
  - PASS.
- `rg -n '_interpolate_data|_decimate_data|_thin_display_series|_peak_preserving_thin|_stabilize_y_range' src tools`
  - No stale old-helper references.
- `python tools\ui_close_process_probe.py --scenario sampling --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `510.9ms`.

### Notes

- Keil discovery found `D:\Keil\Keil_v5\UV4\UV4.exe`, `uVision.com`, and UVSOCK DLLs including `UVSC64.dll`. The realistic path is a Keil transport/bridge around UVSOCK/debug commands, not a direct UI monolith.
- A read-only architecture pass recommends the next extraction be a serial lifecycle controller so serial worker/timer/config logic stops living in `MainWindow`.

### Next Target

- Extract a `SerialController` slice:
  - own `SerialCollector`, serial timer, connect/send/disconnect workers, and shutdown
  - expose Qt signals for logs, connection state, ports, busy state, and scope data
  - leave `SerialTab` as the UI surface and `MainWindow` as the workspace/page coordinator
  - keep serial integration and close-process probes green

## Stage 5 - Serial Lifecycle Controller

### Goal

Stop `MainWindow` from owning serial worker, timer, and shutdown details so future serial/Keil/protocol features can be added through controllers instead of growing the main UI class.

### Completed

- Added `src/ui/serial_controller.py`.
- Moved serial assistant lifecycle state into `SerialController`:
  - `SerialCollector` ownership
  - connect/send/disconnect worker threads
  - runtime polling timer
  - log forwarding
  - scope data forwarding
  - busy/connected/send-enabled signals
  - shutdown and worker join handling
- Rewired `MainWindow` so `SerialTab` talks to `SerialController` through Qt signals.
- Kept compatibility wrappers in `MainWindow` for existing probes that call `_start_serial_worker()` and related serial helpers.
- Added `tools/serial_controller_probe.py`, a no-hardware lifecycle probe covering refresh, connect, runtime log/scope poll, send, disconnect, clear, reconnect, and shutdown.
- Cleaned `tools/ui_serial_integration_probe.py` so the visual screenshot probe exits normally; process-lifecycle behavior remains covered by close probes.

### Verified

- `python -m py_compile main.py src\ui\gui.py src\ui\serial_controller.py src\ui\serial_tab.py src\core\serial_backend.py tools\serial_controller_probe.py tools\ui_close_scenario_entry.py`
- `python tools\serial_controller_probe.py`
  - PASS, `ports=1`, `starts=2`, `stops=2`, `sends=1`.
- `python tools\serial_parser_probe.py`
  - PASS for CSV, FireWater, JustFloat, raw text, and hex lines.
- `python tools\ui_serial_integration_probe.py --output-dir %TEMP%\loopmaster-stage5\serial-final-clean`
  - PASS, serial UI screenshot rendered cleanly.
- `python tools\ui_workspace_nav_probe.py --output-dir %TEMP%\loopmaster-stage5\nav-final`
  - PASS, LoopMaster variables, LoopMaster scope, and serial assistant pages rendered.
- `python tools\ui_close_process_probe.py --scenario serial-worker --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `268.7ms`.
- `python tools\ui_close_process_probe.py --entry main.py --exit-timeout 10`
  - PASS, close-to-exit about `212.3ms`.
- `git diff --check -- src/ui/gui.py src/ui/serial_controller.py tools/serial_controller_probe.py`
  - PASS.

### Notes

- A read-only review found no clear controller extraction regressions. It confirmed the existing `ui_close_scenario_entry.py` serial-worker scenario still works because `MainWindow._start_serial_worker()` delegates to the controller.
- The no-hardware controller probe covers the controller unit lifecycle. `ui_serial_integration_probe.py` and `ui_close_process_probe.py` cover the `MainWindow + SerialTab` wiring and process-level shutdown.
- Real pyserial hardware loopback remains a later hardware-facing verification pass; this stage intentionally kept the architecture slice small and deterministic.

### Next Target

- Start the Keil bridge foundation:
  - add a small `src/core/keil/` bridge module that locates `D:\Keil\Keil_v5\UV4` and UVSOCK DLLs
  - expose safe capability/discovery functions before any variable writes
  - add a no-hardware Keil discovery probe
  - document the UVSOCK/debug-command path so later work can add read/write variable integration without turning the UI into a monolith

## Stage 6 - Keil Bridge Discovery Foundation

### Goal

Create the first safe Keil bridge layer: locate the installed uVision/UVSOCK components and describe available capabilities without launching Keil, connecting to ST-Link, halting the MCU, or writing variables.

### Completed

- Added `src/core/keil/`.
- Added `src/core/keil/discovery.py` for read-only Keil discovery:
  - finds `UV4.exe`, `uVision.com`, `UVSC.dll`, `UVSC64.dll`, and `UVSCWrapper.dll`
  - selects the UVSOCK DLL that matches Python bitness
  - reads PE machine type for DLL/EXE files
  - parses PE export names without `dumpbin` or extra dependencies
  - reports capability flags for open connection, debug enter, command execution, expression evaluation, memory read/write, variable enumeration, and target control
- Added `tools/keil_bridge_probe.py`, a no-launch/no-hardware probe.
- Added missing-root probe mode so explicit invalid Keil roots fail gracefully instead of silently falling back to `D:\Keil`.

### Verified

- `python -m py_compile src\core\keil\__init__.py src\core\keil\discovery.py tools\keil_bridge_probe.py src\core\transports\base.py src\core\transports\__init__.py tools\collector_fake_transport_probe.py`
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS, discovered `D:\Keil\Keil_v5\UV4`.
  - Selected `UVSC64.dll` for 64-bit Python.
  - Parsed `103` exports.
  - Confirmed all `13` important UVSOCK/debug exports.
- `python tools\keil_bridge_probe.py --keil-root D:\__missing_keil_root__ --expect-missing`
  - PASS, missing explicit root returns a clean missing discovery result.
- `python tools\collector_fake_transport_probe.py`
  - PASS, existing transport foundation still works after adding the Keil package.
- `git diff --check -- src/core/keil/__init__.py src/core/keil/discovery.py tools/keil_bridge_probe.py`
  - PASS.

### Notes

- This stage intentionally does not start `UV4.exe`, call UVSOCK, read Keil project files, touch `TOOLS.INI`, use ST-Link, or access the connected F401CCU6 board.
- The connected ST-Link/F401CCU6 can be used in a later hardware-facing Keil verification stage after the read-only connection layer exists.
- `discover_keil(root=...)` now treats an explicit root as strict. Calling `discover_keil()` without a root still searches environment/default locations.

### Next Target

- Add a read-only Keil UVSOCK connection skeleton:
  - load the selected `UVSC64.dll` with `ctypes`
  - expose typed bindings for open/close and harmless status/debug command calls
  - detect whether uVision is running before attempting connection
  - add a probe that can run in dry-run mode and, when Keil is open, try a non-mutating connection/status check
  - still avoid variable writes until RAM/type/readback safety is implemented

## Stage 7 - Keil UVSOCK Preflight

### Goal

Move from static Keil discovery to a safe runtime preflight: load the selected UVSOCK DLL and detect whether uVision is running, while still avoiding any UVSOCK command, target halt/run, memory access, or variable write.

### Completed

- Added `src/core/keil/uvsock.py`.
- Added `UvscPreflight`, `UvscLoadResult`, and `KeilProcess`.
- Added `load_uvsc_library()`:
  - uses the discovery-selected DLL
  - adds the UV4 directory to the DLL search path during load
  - rejects obvious Python/DLL bitness mismatches
  - loads `UVSC64.dll` with `ctypes.WinDLL`
- Added `list_running_uvision()` using `psutil` when available.
- Added `check_uvsock_preflight()`:
  - reports whether Keil is discovered
  - reports whether the UVSOCK DLL loads
  - reports whether uVision appears to be running
  - returns `can_attempt_connection=False` unless both the DLL is loaded and uVision is running
- Added `tools/keil_uvsock_preflight_probe.py`, a dry-run probe that does not connect to UVSOCK.

### Verified

- `python -m py_compile src\core\keil\__init__.py src\core\keil\discovery.py src\core\keil\uvsock.py tools\keil_bridge_probe.py tools\keil_uvsock_preflight_probe.py`
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS, discovery still selects `UVSC64.dll` and confirms `103` exports.
- `python tools\keil_bridge_probe.py --keil-root D:\__missing_keil_root__ --expect-missing`
  - PASS, strict missing-root behavior still works.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil`
  - PASS, `UVSC64.dll` loaded successfully.
  - No uVision process was running, so `can_attempt_connection=False`.
- `git diff --check -- src/core/keil/__init__.py src/core/keil/discovery.py src/core/keil/uvsock.py tools/keil_bridge_probe.py tools/keil_uvsock_preflight_probe.py`
  - PASS.

### Notes

- The connected ST-Link/F401CCU6 board was not accessed in this stage.
- This stage does not call `UVSC_OpenConnection`, `UVSC_DBG_STATUS`, Halt/Run, memory read/write, or variable APIs. It only proves that the local Python process can load the correct UVSOCK DLL.
- Current machine state during verification: uVision was not running.

### Next Target

- Add the first explicit UVSOCK connection attempt path behind an opt-in probe flag:
  - require uVision to already be running with UVSOCK enabled
  - use only open/close and status-style non-mutating calls after signatures are verified
  - never write memory or variables
  - log clear guidance when Keil is closed or UVSOCK is not enabled

## Stage 8 - Opt-In UVSOCK Connection Path

### Goal

Add the first UVSOCK connection attempt path, but keep it opt-in and read-only: it must refuse to call `UVSC_OpenConnection` unless Keil/uVision is already running, a port is explicitly provided, and the preflight is clean.

### Completed

- Added `UvscConnectionResult`.
- Added `attempt_existing_uvsock_connection()`:
  - requires an explicit port
  - validates the port range
  - requires successful discovery and DLL load
  - requires a running uVision process before any connection attempt
  - calls `UVSC_Init`, `UVSC_OpenConnection`, optional `UVSC_DBG_STATUS`, `UVSC_CloseConnection`, and `UVSC_UnInit` only after preflight passes
  - never starts Keil, never launches a project, never halts/runs the target, and never reads or writes memory/variables
- Updated `tools/keil_uvsock_preflight_probe.py`:
  - default remains dry-run preflight
  - `--attempt-existing --port <port>` enables the explicit open/close path
  - `--status` optionally asks for `UVSC_DBG_STATUS` only after a connection succeeds

### Verified

- `python -m py_compile src\core\keil\__init__.py src\core\keil\uvsock.py tools\keil_uvsock_preflight_probe.py`
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil`
  - PASS, `UVSC64.dll` loads and uVision is reported as not running.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil --attempt-existing --port 4827 --status`
  - PASS, did not attempt connection because uVision was not running.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS, discovery still finds `UVSC64.dll`, `103` exports, and all important UVSOCK exports.
- `python tools\keil_bridge_probe.py --keil-root D:\__missing_keil_root__ --expect-missing`
  - PASS, strict missing-root behavior still works.
- `git diff --check -- src/core/keil/__init__.py src/core/keil/uvsock.py tools/keil_uvsock_preflight_probe.py`
  - PASS.

### Notes

- The connected ST-Link/F401CCU6 board was not accessed in this stage.
- Current machine state during verification: uVision was not running, so the opt-in connection path correctly returned `attempted=False`.
- The next real connection test needs uVision opened with UVSOCK enabled on a known port.

### Next Target

- Add Keil/uVision launch guidance and optional user-controlled UVSOCK starter:
  - detect whether Keil is already running
  - provide the exact command needed to start uVision with a UVSOCK port
  - keep automatic launch disabled by default
  - when the user explicitly opts in, start Keil with UVSOCK enabled and run the open/status/close probe

## Stage 9 - Keil UVSOCK Launch Planning

### Goal

Make the next real UVSOCK connection test repeatable by generating the exact uVision launch command for a chosen port and optional project, while keeping automatic launch disabled by default.

### Completed

- Added `UvscLaunchPlan` and `UvscLaunchResult`.
- Added `build_uvision_uvsock_command()`:
  - selects `UV4.exe` from the discovered Keil installation
  - validates the UVSOCK port
  - accepts an optional Keil project and target
  - generates a `UV4.exe <project> -s <port>` command
  - returns `ready=False` when no project is supplied, so the command remains guidance-only
- Added `start_uvision_uvsock()` for explicit future use; it is not called unless a probe/user passes `--launch-uvsock`.
- Extended `tools/keil_uvsock_preflight_probe.py`:
  - `--plan-launch --port <port>` prints a guidance command without launching Keil
  - `--plan-launch --project <uvprojx>` verifies a project-backed command is ready
  - `--launch-uvsock` requires an explicit project before starting uVision
- Updated `docs/debug_workbench_plan.md` with the larger modern Keil debugger frontend goal:
  - VSCode/CubeIDE-like code view
  - visual breakpoints/gutter decorations
  - Watch/local/global variables
  - registers, call stack, RTOS/fault views
  - run-control integration
  - waveform timeline linkage
  - open-source reference and license tracking requirements

### Verified

- `python -m py_compile src\core\keil\__init__.py src\core\keil\uvsock.py tools\keil_uvsock_preflight_probe.py`
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS, Keil discovery still works.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil --plan-launch --port 4827`
  - PASS, generated guidance-only command and `ready=False`.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil --plan-launch --port 4827 --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, generated project-backed command and `ready=True`.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil --attempt-existing --port 4827 --status`
  - PASS, did not attempt connection because uVision was not running.
- `git diff --check -- docs/debug_workbench_plan.md src/core/keil/__init__.py src/core/keil/uvsock.py tools/keil_uvsock_preflight_probe.py`
  - PASS.

### Notes

- This stage still did not launch Keil, access ST-Link/F401CCU6, or call UVSOCK debug commands.
- Local Keil projects were only listed/used by path for command planning; source/project content was not read.
- The modern debugger frontend is now explicitly documented as a major future phase, not an accidental side feature.

### Next Target

- Add a user-controlled real UVSOCK smoke path:
  - launch a selected Keil project with `--launch-uvsock`
  - wait for uVision process detection
  - run the opt-in open/status/close probe
  - capture failures as actionable guidance
  - keep all memory/variable writes disabled

## Stage 10 - UVSOCK Smoke Orchestration

### Goal

Prepare the real UVSOCK smoke flow without making it automatic: the code can now launch/wait/connect/status/close as one orchestrated path, but probe defaults still avoid launching Keil unless `--launch-uvsock` is explicitly provided.

### Completed

- Added `UvscSmokeResult`.
- Added `run_uvsock_smoke()`:
  - optionally launches uVision with the selected project and UVSOCK port
  - waits for a uVision process after launch
  - reuses the existing opt-in open/status/close connection path
  - refuses to launch without a project
  - keeps all memory/variable writes disabled
- Extended `tools/keil_uvsock_preflight_probe.py` with `--smoke` and `--wait-seconds`.
- The smoke path can now be used in three tiers:
  - dry preflight only
  - smoke against an already running UVSOCK Keil
  - explicit launch + smoke with `--launch-uvsock --project <uvprojx>`

### Verified

- `python -m py_compile src\core\keil\__init__.py src\core\keil\uvsock.py tools\keil_uvsock_preflight_probe.py`
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil --smoke --port 4827 --status`
  - PASS, did not launch Keil and did not attempt connection because uVision was not running.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil --plan-launch --port 4827 --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, launch plan remains ready for an explicit future smoke run.

### Notes

- This stage still did not launch Keil, use ST-Link/F401CCU6, or call any memory/variable APIs.
- A real smoke run should be performed intentionally with a known project and port, then followed by UI integration only after the backend behavior is stable.

### Next Target

- Run a real UVSOCK smoke only when explicitly opted in:
  - choose a known project
  - launch uVision with UVSOCK
  - verify open/status/close behavior
  - document exact failure modes if UVSOCK or Keil project setup needs adjustment

## Stage 11 - Keil Project Metadata Parser

### Goal

Add a safe Keil project metadata layer so the future modern debugger frontend can understand targets, output AXF paths, groups, and source file paths without launching Keil or reading source contents.

### Completed

- Added `src/core/keil/project.py`.
- Added project model dataclasses:
  - `KeilProject`
  - `KeilTarget`
  - `KeilGroup`
  - `KeilProjectFile`
- Added `parse_keil_project()` for `.uvprojx`/`.uvproj` metadata:
  - target names
  - output directory/name
  - expected `.axf` output path
  - listing path
  - groups
  - file names, file types, and resolved paths
  - source/header classification by suffix
- Added `find_keil_projects()` for bounded project discovery under an explicit root.
- Added `tools/keil_project_probe.py`:
  - default synthetic `.uvprojx` parse
  - explicit project parse
  - bounded project listing
- Exported the project parser API from `src/core/keil/__init__.py`.

### Verified

- `python -m py_compile src\core\keil\__init__.py src\core\keil\project.py tools\keil_project_probe.py`
- `python tools\keil_project_probe.py`
  - PASS, synthetic project parsed with `1` target, `2` groups, `3` files, output `demo_f401.axf`.
- `python tools\keil_project_probe.py --list-root D:\Keil\code`
  - PASS, found `3` Keil projects by path.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, parsed `1` target, `5` groups, `19` files, output `D:\Keil\code\HELLO\MDK-ARM\HELLO\HELLO.axf`.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS, Keil discovery still works.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil --plan-launch --port 4827 --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, UVSOCK launch planning still works with the same project.
- `git diff --check -- src/core/keil/__init__.py src/core/keil/project.py tools/keil_project_probe.py`
  - PASS.

### Notes

- This stage only reads project XML metadata. It does not read source file contents, launch Keil, access ST-Link/F401CCU6, or call UVSOCK debug commands.
- The parser gives the future code view, breakpoint UI, DWARF/AXF loading, and UVSOCK launch flow a shared project model.

### Next Target

- Add a debugger workbench data model for source files and breakpoints:
  - represent source tree entries independently of Qt widgets
  - model visual breakpoints, enabled state, file/line, condition, and hit count
  - add no-UI probes so the future code editor/gutter can be built on a stable core model

## Stage 12 - Debug Workbench Source And Breakpoint Models

### Goal

Create pure core models for the future modern Keil debugger frontend so the code editor, gutter, and breakpoint list can be built on tested state instead of ad-hoc Qt widget logic.

### Completed

- Added `src/core/debug_workbench.py`.
- Added source view models:
  - `SourceEntry`
  - `SourceTreeNode`
  - language classification for C/C++/ASM/header files
- Added breakpoint models:
  - `Breakpoint`
  - `BreakpointStore`
- `BreakpointStore` now supports:
  - add/upsert
  - lookup
  - list all
  - list by file
  - toggle add/remove
  - remove
  - enable/disable
  - condition update
  - verified-state update
  - hit-count recording
- Added `source_entries_from_keil_project()` and `source_tree_from_entries()` to bridge the Keil project parser into a future code tree/editor UI.
- Added `tools/debug_workbench_model_probe.py`, a no-UI probe for source entries, tree shape, and breakpoint behavior.

### Verified

- `python -m py_compile src\core\debug_workbench.py src\core\keil\project.py tools\debug_workbench_model_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS, source entries/tree and breakpoint store behavior are stable.
- `python tools\keil_project_probe.py`
  - PASS, synthetic Keil project parsing still works.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, real project metadata still parses.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil --plan-launch --port 4827 --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, Keil launch planning still works.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS, Keil discovery still works.
- `git diff --check -- src/core/debug_workbench.py tools/debug_workbench_model_probe.py`
  - PASS.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, read source contents, or call UVSOCK debug commands.
- The new model is intentionally UI-agnostic so the future code view can be implemented with Qt widgets, QScintilla, Monaco-in-webview, or another editor surface without rewriting breakpoint semantics.

### Next Target

- Add a minimal Qt-free code document layer:
  - load source text only from an explicit selected file
  - map line numbers to breakpoint decorations
  - provide search/current-PC/run-line decoration state
  - add probes with synthetic files before building the visual editor

## Stage 13 - Code Document And Line Decoration Model

### Goal

Add a Qt-free code document layer that can feed a future modern source editor with line text, search matches, breakpoint decorations, current-PC highlights, and run-line highlights.

### Completed

- Extended `src/core/debug_workbench.py`.
- Added code document models:
  - `CodeLine`
  - `CodeDocument`
  - `SearchMatch`
  - `LineDecoration`
- Added `load_code_document()`:
  - only loads an explicitly selected file
  - enforces a max file size
  - decodes UTF-8 with BOM support and replacement for invalid bytes
  - classifies language by suffix
- Added `search_document()` for case-insensitive/case-sensitive search with a match limit.
- Added `line_decorations()`:
  - breakpoint decorations from `BreakpointStore`
  - current PC line
  - run line
  - search result decorations
- Expanded `tools/debug_workbench_model_probe.py` to cover document loading, search, and line decoration behavior.

### Verified

- `python -m py_compile src\core\debug_workbench.py tools\debug_workbench_model_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS, source tree, breakpoint store, document load, search, and line decorations all passed.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, real project metadata still parses.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS, Keil discovery still works.
- `git diff --check -- src/core/debug_workbench.py tools/debug_workbench_model_probe.py`
  - PASS.

### Notes

- This stage reads only synthetic source files in the probe. It still does not launch Keil, access ST-Link/F401CCU6, or call UVSOCK debug commands.
- The future visual editor can now render from stable core state instead of inventing breakpoint/search/PC semantics inside Qt widgets.

### Next Target

- Add a first read-only debug workbench UI surface:
  - show a source tree from a Keil project
  - show a source preview with line numbers
  - render breakpoint/current-PC/search decorations from the core model
  - keep it disconnected from Keil runtime until UVSOCK smoke is verified

## Stage 14 - Read-Only Debug Workbench UI Surface

### Goal

Add the first visible modern Keil debug workbench surface: a source tree, source preview, line-number gutter, local breakpoints, search highlights, and PC/run-line decorations, while keeping the page disconnected from Keil runtime control.

### Completed

- Added `src/ui/debug_workbench_tab.py`.
- Added `DebugWorkbenchTab`, a self-contained Qt page for the new debugger workspace.
- Added `SourceCodeEditor`, a read-only `QPlainTextEdit` preview with:
  - line-number gutter
  - gutter click breakpoint toggling
  - breakpoint markers
  - current PC marker
  - run-line marker
  - search-line highlights
- Added Keil project loading through the existing read-only project parser.
- Added source tree rendering from the core `SourceTreeNode` model.
- Added a local breakpoint table for the selected source file/project.
- Integrated a third workspace domain in `MainWindow`:
  - `LoopMaster`
  - `调试工作台`
  - `串口助手`
- Added debug-workbench hero summaries for project name, source count, and local breakpoint count.
- Added `tools/ui_debug_workbench_probe.py`, a screenshot probe that builds a synthetic Keil project/source tree and verifies the UI surface.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py src\ui\gui.py tools\ui_debug_workbench_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS, pure source/breakpoint/document/decorator models still work.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS, existing LoopMaster and serial workspace navigation still works with the new domain present.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS, serial assistant integration screenshot still renders.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, real Keil project metadata still parses.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS, Keil discovery still works.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, or write variables.
- The new debug page is intentionally a UI/model bridge only. Runtime state is represented as decorations so future Keil/UVSOCK, pyOCD, or DAP-style backends can drive the same visual layer.
- The screenshot probe initially exposed a Windows offscreen font-rendering issue. The probe now uses the native desktop platform by default and leaves `QT_QPA_PLATFORM=offscreen` as an explicit opt-in for CI-style runs.
- Open-source IDE/debugger references for future stages must be tracked in the plan or handoff notes with source and license before any code is copied or adapted.

### Next Target

- Add a debug-workbench state/controller layer before touching hardware:
  - represent disconnected/Keil-discovered/Keil-attached/runtime-paused/runtime-running states
  - keep UI controls disabled or read-only until a verified backend capability exists
  - prepare source tree selection, search result navigation, and breakpoint state for later Keil synchronization
  - add a no-hardware probe for state transitions

## Stage 15 - Debug Workbench State Foundation

### Goal

Add a pure, no-hardware debug workbench state layer so the new Keil-facing UI can show reliable connection/runtime status and future run-control actions without pretending that backend control already exists.

### Completed

- Extended `src/core/debug_workbench.py` with a pure debug session model:
  - `DebugRuntimeState`
  - `DebugBackendKind`
  - `DebugCapabilities`
  - `DebugWorkbenchStatus`
  - `DebugAction`
  - `DebugWorkbenchSession`
- Added status/action reducers for disconnected, Keil discovered, Keil attached, running, paused, and error states.
- Added pure projection helpers for synthetic UVSOCK preflight and connection results.
- Kept variable writes disabled by default even after a connected runtime projection.
- Updated `DebugWorkbenchTab` with:
  - Chinese status text and status dot
  - disabled run-control action row for discover/connect/disconnect/halt/run
  - summary text that includes the current debug state
  - runtime marker updates driven by `DebugWorkbenchStatus`
- Updated debug workbench probes so UI screenshots now verify status text and confirm run-control buttons stay disabled until a backend controller is explicitly wired.

### Verified

- `python -m py_compile src\core\debug_workbench.py src\ui\debug_workbench_tab.py tools\debug_workbench_model_probe.py tools\ui_debug_workbench_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS, state/action/capability transitions work in the pure model.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS, workspace navigation still renders LoopMaster, debug workbench, and serial assistant domains.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS, serial assistant integration screenshot still renders.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, real Keil project metadata still parses.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS, Keil discovery still works and prefers `UVSC64.dll`.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, or write variables.
- The UI now has visible run-control affordances, but they remain disabled until a backend controller is connected and deliberately marks itself ready.
- The next hardware-facing steps must remain opt-in because Keil, ST-Link, target reset state, and variable writes can disturb a real MCU session.

### Next Target

- Add an explicit no-hardware Keil preflight controller/UI wiring stage:
  - trigger Keil discovery from the `发现 Keil` action
  - feed the existing Keil bridge/preflight result into `DebugWorkbenchSession`
  - show Chinese success/error details in the debug workbench
  - keep attach, halt, run, breakpoint sync, and variable writes disabled until a later hardware/smoke stage

## Stage 16 - No-Hardware Keil Discovery Wiring

### Goal

Wire the debug workbench `发现 Keil` action to the existing safe Keil/UVSOCK preflight path, so the UI can report real local Keil installation/preflight state without launching Keil, connecting UVSOCK, or touching the attached ST-Link/F401CCU6 target.

### Completed

- Added `debugActionRequested` signaling to `DebugWorkbenchTab`.
- Added a controller-ready gate so debug action buttons can be enabled only when a UI controller is intentionally wired.
- Connected `MainWindow` to the debug workbench action signal.
- Implemented `_discover_keil_for_debug_workbench()` using the existing `check_uvsock_preflight()` helper.
- Preserved the current project/target context when preflight status is applied.
- Added `LOOPMASTER_KEIL_ROOT`/config-backed Keil root storage, defaulting to `D:\Keil`.
- Translated common Keil/UVSOCK preflight reasons into Chinese before they reach the debug workbench status line.
- Extended the debug UI probe so it verifies:
  - the discover action is enabled when the controller is wired
  - the no-hardware preflight leaves the UI in a non-busy state
  - the synthetic runtime-control state still keeps real control buttons disabled when no backend controller is attached

### Verified

- `python -m py_compile src\core\debug_workbench.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_workbench_model_probe.py tools\ui_debug_workbench_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS, translated preflight reasons and state reducers remain valid.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS, workspace navigation still renders.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS, serial assistant integration still renders.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS, Keil discovery still finds `UVSC64.dll`.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, real Keil project metadata still parses.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- The `发现 Keil` button now performs a real local preflight, but `连接`/`暂停`/`运行` remain guarded for a later explicit hardware or UVSOCK smoke stage.
- The current PowerShell permission issue was environmental: the previous restricted sandbox path failed around `PwshShim`, while the full-access environment runs `pwsh 7.6.2` normally.

### Next Target

- Add a read-only debug session diagnostics panel:
  - show Keil root, selected UVSOCK DLL, uVision running state, process count, and preflight reasons
  - expose a safe launch-plan preview for UVSOCK without starting Keil
  - keep actual attach/halt/run behind a later opt-in smoke stage

## Stage 17 - Debug Workbench Diagnostics Panel

### Goal

Give the new debug workbench a compact, readable diagnostics panel that explains what the Keil bridge found and what a future UVSOCK launch would look like, while staying strictly no-hardware and no-launch.

### Completed

- Added a `后端诊断` table to `DebugWorkbenchTab`.
- Added `set_backend_diagnostics()` so the UI can receive backend diagnostics without knowing Keil internals.
- Kept the useful rows visible first:
  - uVision process state/count
  - whether a connection could be attempted
  - preflight reasons
  - launch preview status
  - Keil root and selected UVSOCK DLL
- Added tooltips for long diagnostic values such as DLL paths and launch commands.
- Added MainWindow diagnostics assembly from:
  - `check_uvsock_preflight()`
  - `build_uvision_uvsock_command()`
- Added Chinese translation for common launch/preflight reasons in the UI diagnostics path.
- Extended the UI probe to assert that the diagnostics table includes Keil root, UVSOCK DLL, and launch command rows.
- Iterated screenshot layout so the first visible diagnostic rows are operationally useful, not just path metadata.

### Verified

- `python -m py_compile src\core\debug_workbench.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_workbench_model_probe.py tools\ui_debug_workbench_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- The launch command is preview-only. It is generated to make future behavior visible and reviewable before any opt-in smoke stage.
- The diagnostics panel intentionally lives beside source/breakpoint state so future Keil sessions can show both code context and backend health without opening a separate tool window.

### Next Target

- Add source navigation polish for the debug workbench:
  - search result next/previous controls
  - breakpoint table click-to-source navigation
  - clearer selected-source state in the source tree
  - keep everything local/read-only until Keil breakpoint synchronization is deliberately introduced

## Stage 18 - Debug Workbench Source Navigation Polish

### Goal

Make the debug workbench source view feel closer to a modern IDE by improving local search and breakpoint navigation, while keeping all behavior local/read-only and away from Keil runtime control.

### Completed

- Added `上一处` / `下一处` search navigation buttons beside the source search box.
- Added current search-hit state with:
  - highlighted active search line
  - gutter marker for the active result
  - marker text such as `搜索 1/28`
- Added breakpoint-table click navigation back to the matching source line.
- Added source-tree selection sync when a file is opened programmatically.
- Added target-combo handling so switching a Keil target refreshes the source tree, loads the first source, updates the summary, and keeps the debug session target name current.
- Fixed the no-hardware launch preview target risk noted during review: future `-t` preview now follows the selected target instead of stale project state.
- Added `UVSOCK 端口` as a first-class diagnostics row.
- Extended `tools/ui_debug_workbench_probe.py` to verify:
  - search navigation activates a match
  - marker text shows the active search index
  - source tree selects the current source file
  - breakpoint table navigation jumps to the expected line
  - diagnostics include the UVSOCK port row

### Verified

- `python -m py_compile src\core\debug_workbench.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_workbench_model_probe.py tools\ui_debug_workbench_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- The improvements are intentionally local UI behavior so later Keil synchronization can reuse the same interactions rather than replacing them.

### Next Target

- Add a safe debug command planning layer:
  - model future Keil actions as explicit plans before execution
  - render attach/halt/run/step/sync-breakpoint/write-variable plans as disabled previews
  - include safety notes and required opt-in conditions per action
  - keep execution disabled until a separate UVSOCK smoke stage is deliberately started

## Stage 19 - Safe Debug Command Planning Layer

### Goal

Introduce an explicit plan layer between "the backend says this action is possible" and "LoopMaster is allowed to execute it", so future Keil/UVSOCK runtime controls can be reviewed, displayed, and tested before any hardware-facing command path is enabled.

### Completed

- Added pure command-plan models in `src/core/debug_workbench.py`:
  - `DebugPlanRisk`
  - `DebugCommandPlan`
  - `DebugWorkbenchSession.command_plans()`
  - `debug_command_plans_for_status()`
- Added stable plans for:
  - Keil discovery/preflight
  - attach/disconnect
  - halt/run/step
  - breakpoint synchronization
  - variable writes
- Kept real execution separated from capability state:
  - safe Keil discovery can remain executable as a no-hardware preflight
  - attach/halt/run/step/sync/write remain preview-only even when their preconditions are met
- Added stronger safety text for variable writes:
  - RAM-only or explicit address whitelist
  - type/length/alignment/range/endian checks
  - old/new value review
  - audit log
  - write-after-readback verification
- Added a compact top toolbar `动作计划` preview strip in the debug workbench.
- Moved plan presentation out of the left navigation column after screenshot review showed that a table there made narrow windows too cramped.
- Kept the left debug workbench column focused on source tree, backend diagnostics, and local breakpoints.
- Extended model and UI probes to verify:
  - every debug action has a plan
  - risky plans remain execution-disabled
  - running state makes halt ready but still preview-only
  - paused state makes run/step/write ready but still preview-only
  - write-variable safety text includes RAM/type/range/readback requirements
  - real toolbar buttons stay disabled without a backend controller

### Verified

- `python -m py_compile src\core\debug_workbench.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_workbench_model_probe.py tools\ui_debug_workbench_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS, Keil discovery still finds the 64-bit UVSOCK DLL and important exports.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, the real HELLO project still parses.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- The new command-plan layer intentionally treats runtime capability as necessary but not sufficient. A future smoke/controller stage must opt into execution separately.
- Screenshot review caught and fixed a layout issue: the first implementation put a full plan table in the left column, which squeezed diagnostics and breakpoints in narrow windows.

### Next Target

- Add a dry-run Keil command transaction layer:
  - represent UVSOCK attach/halt/run/step/breakpoint/write-variable operations as typed transaction objects
  - render the exact future command intent without executing it
  - add a controller-level execution gate distinct from UI button readiness
  - persist a lightweight debug audit log for planned commands and future execution results
  - keep all hardware-facing execution disabled until an explicit UVSOCK smoke stage is started

## Stage 20 - Dry-Run Keil Command Transactions

### Goal

Create a typed dry-run transaction layer between the human-readable debug plans and any future UVSOCK executor, so LoopMaster can preview exact Keil command intent, guard state, and audit records without launching Keil, opening UVSOCK, or touching the target MCU.

### Completed

- Added `src/core/keil/commands.py` as a data-only transaction layer.
- Added typed objects for:
  - command kind
  - guard state
  - command guard
  - breakpoint intent
  - variable write intent
  - command transaction
- Added `build_keil_debug_transactions()` to turn the current debug status plus Stage 19 plans into dry-run transactions.
- Forced Stage 20 transactions to stay dry-run:
  - `dry_run=True`
  - `execution_enabled=False`
  - `ready=False`
  - even if a future `execution_gate` value is passed
- Added explicit guards for:
  - action preconditions
  - execution gate
  - data-only payload
  - no Keil launch
  - UVSOCK port validity
  - project and target context
  - attached-session requirement
  - breakpoint batch and line/path validation
  - variable write batch, type check, RAM whitelist, range check, and write-after-readback
- Added command previews for future UVSOCK-facing operations:
  - `UVSC_OpenConnection`
  - `UVSC_DBG_STOP_EXECUTION`
  - `UVSC_DBG_START_EXECUTION`
  - `UVSC_DBG_EXEC_CMD`
  - `UVSC_DBG_VARIABLE_SET`
  - `UVSC_DBG_EVAL_EXPRESSION_TO_STR`
- Added JSONL audit-record output with `append_keil_audit_log()`.
- Exported the new transaction objects from `src/core/keil/__init__.py`.
- Reused the existing top `动作计划` strip for UI display:
  - short visible text shows `干跑` and audit state
  - full command intent, transaction id, project, target, port, guards, and audit summary live in the tooltip
  - no new left-column table was added
- Wired `MainWindow` to refresh command transaction previews after debug workbench setup and Keil discovery.
- Extended the debug workbench screenshot probe to assert:
  - running state previews a dry-run halt transaction
  - paused state previews dry-run run/step intent
  - tooltip includes UVSOCK command text, project, target, port, transaction id, guard text, and audit text
  - real action buttons remain disabled without a backend controller
- Added `tools/keil_command_transaction_probe.py` to verify the transaction layer without Qt, Keil launch, UVSOCK attach, or hardware access.

### Verified

- `python -m py_compile src\core\debug_workbench.py src\core\keil\commands.py src\core\keil\__init__.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_workbench_model_probe.py tools\keil_command_transaction_probe.py tools\ui_debug_workbench_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS, all transactions remain dry-run, JSON-auditable, and data-only.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- Transaction previews intentionally use strings and primitives only. They do not carry DLL handles, subprocess objects, callbacks, or UVSOCK executor references.
- Screenshot review kept the UI light: the dry-run transaction preview lives in the existing top plan strip, while the left column remains source tree, diagnostics, and local breakpoints.

### Next Target

- Add a debug audit/command history surface:
  - keep a bounded in-memory history of dry-run transactions
  - expose a small command-history drawer or compact bottom strip without crowding the source editor
  - include filtering by action/risk/blocked state
  - persist only explicit audit records, not raw handles or sensitive Keil config
  - continue staying no-hardware until a deliberately scoped UVSOCK smoke stage is selected

## Stage 21 - Dry-Run Command History Preview

### Goal

Make the Keil debug workbench remember recent dry-run command intent without adding visual clutter or writing anything to disk automatically, so later UVSOCK smoke/execution stages have a clear audit trail shape already in place.

### Completed

- Added `KeilCommandHistoryEntry` and `KeilCommandHistory` in `src/core/keil/commands.py`.
- Added bounded in-memory command history with a default capacity of 64 entries.
- Added adjacent duplicate coalescing:
  - repeated A/A updates merge into one entry and increment `seen_count`
  - A/B/A remains three separate segments so state transitions are not hidden
- Added history metadata:
  - entry id
  - sequence
  - first/last seen timestamps
  - event/source
  - transaction id
  - action/risk/dry-run state
  - project/target/port
  - command preview
  - blocked reasons
  - guard pass/wait/blocked summary
  - audit summary
- Added filtered recent-history lookup by action kind, risk, and blocked state.
- Kept history in memory only. JSONL persistence still requires explicit `append_keil_audit_log()`.
- Exported history objects through `src/core/keil/__init__.py`.
- Added a compact `历史 N` chip to the existing top `动作计划` strip.
- The history chip tooltip shows the recent dry-run history without adding a left-column or editor-bottom table.
- Wired `MainWindow` to record only the current focused dry-run transaction during UI sync, avoiding one history row per hidden action.
- Passed local breakpoints into transaction preview/history generation so future breakpoint sync previews can reflect local breakpoint count.
- Extended probes to verify:
  - bounded history length
  - adjacent duplicate merge
  - A/B/A segment preservation
  - history filtering
  - history records are JSON-serializable and data-only
  - recording history does not auto-create an audit file
  - UI chip displays non-empty history
  - UI tooltip shows merged and multi-state dry-run history

### Verified

- `python -m py_compile src\core\debug_workbench.py src\core\keil\commands.py src\core\keil\__init__.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_workbench_model_probe.py tools\keil_command_transaction_probe.py tools\ui_debug_workbench_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS, including bounded history, duplicate coalescing, A/B/A preservation, filtering, data-only records, and explicit-only audit logging.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- History is deliberately not a persistent log. It is an in-memory UI aid until a future explicit audit/export action is introduced.
- The UI remains compact: no new permanent left-panel table, no drawer, and no source-editor height penalty.

### Next Target

- Add a breakpoint-sync dry-run diff:
  - compare local breakpoints against a future remote snapshot model
  - classify add/remove/enable/disable/update-condition operations
  - show counts in the dry-run transaction preview/history tooltip
  - validate source file membership and line numbers before any future Keil sync command exists
  - keep all sync execution disabled until the UVSOCK smoke/controller stage is deliberately started

## Stage 22 - Breakpoint Sync Dry-Run Diff

### Goal

Make breakpoint synchronization explainable before any Keil execution path exists: compare local breakpoints against a modeled remote snapshot, classify the changes, surface the result in the existing dry-run UI, and keep incomplete remote state safely gated.

### Completed

- Added remote-breakpoint snapshot models in `src/core/keil/commands.py`:
  - `KeilRemoteBreakpoint`
  - `KeilBreakpointRemoteSnapshot`
  - `KeilBreakpointDiffSummary`
- Extended `build_keil_debug_transactions()` with `remote_breakpoint_snapshot`.
- Added `build_keil_breakpoint_diff_summary()` so sync dry-runs now classify:
  - add
  - remove
  - enable
  - disable
  - update-condition
  - noop
- Kept the sync transaction data-only and execution-disabled. Non-sync transactions no longer carry breakpoint diff summaries, so attach/halt/run transaction identities do not change when only breakpoint state changes.
- Added source membership and line-number validation into the diff guard path.
- Missing or incomplete remote snapshots now keep sync in a WAIT guard state.
- Complete empty local + complete empty remote snapshots now pass as a valid no-op diff.
- Added diff counts to the debug workbench top plan tooltip without adding another permanent panel.
- Added optional `MainWindow.set_debug_remote_breakpoint_snapshot()` wiring for future controller integration.
- Added `breakpoint_diff` into dry-run audit/history records when a sync transaction is explicitly recorded.
- Exported the new breakpoint snapshot/diff models through `src/core/keil/__init__.py`.
- Extended probes with synthetic remote snapshots so the UI and model tests cover visible diff counts.

### Verified

- `python -m py_compile src\core\debug_workbench.py src\core\keil\commands.py src\core\keil\__init__.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_workbench_model_probe.py tools\keil_command_transaction_probe.py tools\ui_debug_workbench_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS, including add/remove/enable/disable/update-condition/noop counts, incomplete snapshot waiting, complete empty snapshot no-op pass, invalid line blocking, and data-only transaction records.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- The remote snapshot is still a model/input seam, not a live UVSOCK reader.
- The UI remains compact: the source tree, diagnostics, breakpoint list, and code editor layout are unchanged; diff counts live in the existing action-plan tooltip.

### Next Target

- Improve local breakpoint editing UX:
  - make the breakpoint table support enable/disable changes and condition edits
  - add a clear remove action that keeps source, table, and gutter decorations synchronized
  - refresh dry-run diff previews immediately after each local breakpoint edit
  - preserve the compact VSCode-like workbench feel without adding a heavy panel
  - keep all Keil sync execution dry-run only until the controller/smoke stage is deliberately started

## Stage 23 - Local Breakpoint Editing UX

### Goal

Make the debug workbench breakpoint table behave like a real debugger control surface: toggle enable state, edit conditions, remove entries, and keep source decorations plus dry-run previews in sync immediately.

### Completed

- Expanded the breakpoint table in `src/ui/debug_workbench_tab.py` from 3 columns to 5:
  - 启用
  - 文件
  - 行
  - 条件
  - 操作
- Added table-driven breakpoint editing:
  - check the 启用 column to flip `BreakpointStore.set_enabled()`
  - edit the 条件 column to call `BreakpointStore.set_condition()`
  - click 删除 to remove the breakpoint via `BreakpointStore.remove()`
- Kept the existing gutter breakpoint toggle working.
- Added a compact breakpoint-refresh helper so edits update:
  - source decorations
  - summary text
  - `summaryChanged`
  - dry-run command previews
- Prevented breakpoint-table click navigation from fighting the edit/action columns.
- Added a small danger-style delete button so the table still fits the modern UI language without adding a new panel.
- Extended the debug workbench screenshot probe to exercise:
  - enable/disable toggles
  - condition editing
  - row deletion
  - immediate dry-run diff refresh after each edit
- Kept the core breakpoint model unchanged; the new behavior reuses the existing store APIs.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py tools\ui_debug_workbench_probe.py src\ui\gui.py`
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- The edit flow is intentionally still table-based rather than a large right-side editor, to keep the workbench compact.
- The gutter remains the quickest add/remove path; the table is now the structured edit path.

### Next Target

- Add a breakpoint quick-add / inline edit path from the source gutter and table selection:
  - make the condition field feel less like a spreadsheet cell and more like a debugger control
  - keep row edits reflected in the code gutter instantly
  - continue preserving the compact, modern, VSCode-like layout
  - stay dry-run only for any future Keil controller execution

## Stage 24 - Breakpoint Quick Editor Strip

### Goal

Make breakpoint editing feel less like spreadsheet editing and more like a modern debugger: selecting a breakpoint should expose a compact, focused control strip for enable state, condition editing, clearing, and removal.

### Completed

- Added a compact breakpoint quick editor below the local breakpoint table in `src/ui/debug_workbench_tab.py`.
- The quick editor follows the currently selected breakpoint row and shows:
  - file and line
  - enable/disable toggle
  - condition expression input
  - clear-condition action
  - delete action
- Wired the quick editor to the existing `BreakpointStore` APIs:
  - `set_enabled()`
  - `set_condition()`
  - `remove()`
- Added signal guards so table rebuilds and quick-editor refreshes do not recurse.
- Kept the existing table edit path and gutter toggle path working.
- Kept the UI compact by adding a tool-strip style row instead of a separate properties panel.
- Styled the quick editor to match the current light VSCode-like workbench theme.
- Extended the debug workbench UI probe so it now exercises:
  - selected-breakpoint quick editor state
  - quick condition clear
  - quick enable/disable toggle
  - quick delete
  - dry-run diff refresh after each quick-editor mutation

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py tools\ui_debug_workbench_probe.py`
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- The quick editor is intentionally local-only and dry-run-aware; it updates the same local breakpoint model that the future Keil sync controller will consume.
- The source gutter remains add/remove focused; condition editing is now easier through the selected-row strip.

### Next Target

- Add source-side breakpoint affordances:
  - make gutter-created breakpoints auto-select themselves in the table and quick editor
  - add a lightweight condition-edit shortcut for the current source line
  - keep source decorations, table selection, and dry-run diff preview synchronized
  - continue no-hardware until a deliberately scoped UVSOCK controller/smoke stage

## Stage 25 - Source-Side Breakpoint Affordances

### Goal

Make source-first breakpoint work feel natural: when a breakpoint is created from the gutter or current source line, the breakpoint table and quick editor should immediately follow it so the user can edit conditions without hunting through the table.

### Completed

- Gutter-created breakpoints now auto-select their matching row in the local breakpoint table.
- The breakpoint quick editor now follows newly created gutter breakpoints immediately.
- Added a compact `当前行条件` action in the source editor header.
- The current-line condition action:
  - creates a breakpoint on the current source line when none exists
  - selects the matching breakpoint row
  - focuses/selects the quick editor condition input
  - reuses the existing quick editor mutation path
- The action disables itself when no source document is loaded.
- Selection uses full path + line matching, so same-name files in different groups do not collide.
- Extended the debug workbench UI probe to cover:
  - gutter add auto-selection
  - gutter second-toggle removal
  - current-line condition breakpoint creation
  - quick condition persistence from the source-side action
  - cleanup without disturbing the Stage 22/23 diff baseline

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py tools\ui_debug_workbench_probe.py`
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- The new source-side action is local-only and feeds the same dry-run breakpoint model used by the planned Keil sync controller.
- Current-line condition editing is intentionally a small header action instead of a modal dialog, keeping the workbench compact.

### Next Target

- Improve source gutter/status readability:
  - show clearer visual distinction for enabled, disabled, and conditional breakpoints
  - expose concise hover/tooltips for breakpoint line state
  - keep table, gutter, and quick editor state synchronized
  - continue dry-run/no-hardware until a scoped UVSOCK smoke stage is selected

## Stage 26 - Source Gutter Breakpoint Readability

### Goal

Make source-side breakpoint state easier to read at a glance: enabled, disabled, and conditional breakpoints should not all look like the same red dot, and the current source status should summarize breakpoint state clearly.

### Completed

- Improved `SourceCodeEditor` gutter rendering in `src/ui/debug_workbench_tab.py`.
- Enabled breakpoints remain filled red markers.
- Disabled breakpoints now render as a hollow marker with muted dashed outline.
- Conditional breakpoints now render an additional compact diamond marker inside the breakpoint dot.
- Added gutter tooltip text through `gutter_tooltip_for_line()`:
  - enabled breakpoint
  - disabled breakpoint
  - condition expression
  - PC/run/search line state
- Updated source marker text so the current file summarizes breakpoint state:
  - total breakpoint count
  - enabled count
  - disabled count
  - conditional count
- Kept the existing breakpoint model and dry-run transaction semantics unchanged.
- Extended the debug workbench UI probe to assert:
  - marker label contains enabled/disabled/conditional state counts
  - enabled conditional breakpoint tooltip is correct
  - disabled conditional breakpoint tooltip is correct
  - plain breakpoint tooltip is concise
  - zero-count state groups are not shown

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py tools\ui_debug_workbench_probe.py`
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- The changes are presentation-only and consume the existing `LineDecoration` model.
- Conditional breakpoint rendering is intentionally compact so the gutter does not become visually noisy.

### Next Target

- Add local breakpoint verification-state placeholders:
  - show pending/verified/unverified status in the breakpoint table and gutter tooltip
  - keep the state local and dry-run only for now
  - prepare a clean UI path for future Keil breakpoint readback verification

## Stage 27 - Local Breakpoint Verification Placeholders

### Goal

Prepare the debugger workbench for future Keil breakpoint readback by adding local-only verification state display now, without adding any hardware or UVSOCK execution path.

### Completed

- Reused the existing `Breakpoint.verified` and `Breakpoint.message` fields to model:
  - `待验证`: no backend readback yet
  - `已验证`: backend confirmed the breakpoint
  - `未验证`: backend rejected or failed to find the breakpoint, with a message
- Extended `BreakpointStore.set_verified()` to accept an optional message.
- Added `BreakpointStore.set_message()` for future backend/UI integration.
- Extended `LineDecoration` with `verified` and `message`.
- Passed breakpoint verification state into source decorations.
- Added a `验证` column to the local breakpoint table.
- Added verification labels and tooltips:
  - `待验证`
  - `已验证`
  - `未验证`
- Updated gutter tooltips to include verification state and failure/rejection messages.
- Updated source marker text to summarize:
  - enabled/disabled count
  - conditional count
  - verified/unverified/pending count
- Added `DebugWorkbenchTab.set_breakpoint_verification()` as a local UI integration hook for later Keil readback results.
- Kept all verification state local and dry-run-only.
- Extended the debug workbench UI probe to cover pending, verified, and unverified breakpoint states in:
  - marker text
  - gutter tooltip text
  - breakpoint table verification column

### Verified

- `python -m py_compile src\core\debug_workbench.py src\ui\debug_workbench_tab.py tools\ui_debug_workbench_probe.py`
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated debug workbench screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS.

### Notes

- This stage still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- `未验证` currently means “local placeholder for future backend rejection/failure”; there is no live Keil readback yet.
- The data path is intentionally small so a future UVSOCK controller can set verification results without refactoring the UI.

### Next Target

- Milestone draft: `Keil Dry-Run Debugger Cohesion`
  - combine the next dry-run debugger work into one larger milestone instead of splitting it into small UI/probe stages
  - include breakpoint verification summaries, dry-run transaction preview/history consistency, and compact UI/probe polish as one milestone scope
  - keep the scope no-hardware and no live UVSOCK execution until a deliberate controller/smoke milestone is selected
  - close the milestone only after recording completed results, verification, notes, and the following target

## Milestone 28 - Keil Dry-Run Debugger Cohesion

### Goal

Close the current dry-run debugger contract as one larger milestone: breakpoint verification inventory, sync-breakpoint preview/history/audit consistency, UI probe coverage, and a real feasibility check for the Keil debug chain without launching Keil or touching hardware.

### Completed

- Extended dry-run Keil breakpoint intents with local verification metadata:
  - `verified`
  - `message`
- Extended breakpoint diff summaries and audit records with:
  - `verified_count`
  - `unverified_count`
  - `pending_verify_count`
- Added verification counts to `diff_breakpoints(...)` command preview.
- Added `本地验证` guard/detail to sync-breakpoint transactions.
- Kept local verification informational in dry-run:
  - pending/unverified verification state does not block the transaction by itself
  - invalid breakpoint locations still block sync readiness
- Counted verification readiness only for valid local breakpoint rows, so invalid line `0` does not inflate pending verification.
- Added breakpoint verification counts to Debug Workbench transaction tooltip and dry-run history tooltip.
- Hardened Keil transaction probe coverage:
  - normal sync with verified/unverified/pending local breakpoints
  - empty local/remote sync
  - all-verified sync
  - JSONL audit record for `sync_breakpoints`
  - bounded history behavior with sync records
- Hardened Debug Workbench UI probe coverage:
  - sync preview tooltip verification fields
  - history tooltip verification fields
  - quick condition clear, enable toggles, and delete flows
- Confirmed the local Keil debug chain is technically viable:
  - `D:\Keil\Keil_v5\UV4\UV4.exe`
  - `D:\Keil\Keil_v5\UV4\uVision.com`
  - `D:\Keil\Keil_v5\UV4\UVSC.dll`
  - `D:\Keil\Keil_v5\UV4\UVSC64.dll`
  - `D:\Keil\Keil_v5\UV4\UVSCWrapper.dll`
- Confirmed `UVSC64.dll` exports the important UVSOCK/debug entry points needed for a real controller path, including open/close connection, status, expression eval, variable set, memory read/write, run and stop execution.
- Updated the debug workbench plan so Keil is the first backend, not the only backend; future OpenOCD, pyOCD, GDB server and offline replay adapters should plug into a common debug backend boundary.

### Verified

- `python -m py_compile src\core\debug_workbench.py src\core\keil\commands.py src\core\keil\__init__.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_workbench_model_probe.py tools\keil_command_transaction_probe.py tools\ui_debug_workbench_probe.py`
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\keil_bridge_probe.py --keil-root D:\Keil --show-exports`
  - PASS, selected `UVSC64.dll`, found 103 exports and all important exports.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil`
  - PASS, DLL loaded; no running uVision, so no live connection attempted.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS, parsed one target and 19 source files.
- `git diff --check`
  - PASS.

### Notes

- This milestone still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK, halt/run the target, sync breakpoints, or write variables.
- The Keil chain is feasible, but not yet proven end-to-end against a live uVision debug session.
- The next hardware-facing work should be opt-in and split by risk: read-only connection/status first, then read variables/breakpoints, then explicit write/control actions.
- Future architecture must keep Keil, OpenOCD, pyOCD, GDB server and offline replay as backend adapters behind the same workbench model.

### Next Target

- Milestone: `Debug Backend Adapter + Keil Read-Only Session Snapshot`
  - extract a shared debug backend adapter boundary before adding more live controls
  - keep the existing UI/source/breakpoint/transaction model backend-neutral
  - add an opt-in Keil read-only smoke path for `OpenConnection -> DBG_STATUS -> CloseConnection`
  - capture target status, project/target, PC location and remote breakpoint/variable snapshot where possible
  - continue blocking write variables, breakpoint apply, Halt/Run/Step, reset and flash operations until a later opt-in execution milestone

## Milestone 29 - Debug Backend Adapter + Keil Read-Only Snapshot Foundation

### Goal

Start the larger debug-backend milestone by adding a backend adapter boundary and a Keil read-only snapshot model, so future Keil, OpenOCD, pyOCD, GDB server and offline replay integrations can feed the same workbench instead of wiring backend-specific code directly into the UI.

### Completed

- Added `src\core\debug_backend.py` with data-only backend adapter contracts:
  - `DebugBackendDiagnostic`
  - `DebugBackendSessionSnapshot`
  - `DebugBackendAdapter`
- Added `src\core\keil\backend.py` with `KeilUvSockBackendAdapter`.
- Keil adapter now exposes:
  - `discover()` for no-connect Keil/UVSOCK preflight
  - `read_only_session_snapshot()` for explicit read-only UVSOCK open/status/close attempts
- Kept Keil snapshots data-only and JSON-serializable.
- Added read-only connection status projection so a successful UVSOCK status query can show target running/paused state without enabling Halt, Run, Step, variable writes, or breakpoint sync.
- Moved Debug Workbench `发现 Keil` UI wiring from direct `check_uvsock_preflight()` calls to the Keil adapter.
- Removed the old UI-local Keil diagnostics formatter; diagnostics now come from the backend snapshot rows.
- Added `tools\debug_backend_adapter_probe.py`:
  - monkeypatches the Keil adapter dependencies
  - proves `discover()` does not connect
  - proves read-only snapshots keep dangerous capabilities disabled
  - proves snapshots are JSON/data-only
- Added `tools\keil_backend_adapter_probe.py`:
  - runs the real local Keil adapter in no-connect mode by default
  - can later be reused with `--attempt-existing` for explicit read-only UVSOCK smoke

### Verified

- `python -m py_compile src\core\debug_backend.py src\core\debug_workbench.py src\core\keil\backend.py src\core\keil\commands.py src\core\keil\uvsock.py src\core\keil\__init__.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_backend_adapter_probe.py tools\keil_backend_adapter_probe.py tools\ui_debug_workbench_probe.py tools\keil_command_transaction_probe.py`
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_backend_adapter_probe.py --keil-root D:\Keil --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx --target HELLO`
  - PASS; generated a no-connect Keil adapter snapshot with `attempted=False` and `connected=False`.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil --show-exports`
  - PASS.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS.
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.

### Notes

- This milestone slice still does not launch Keil, access ST-Link/F401CCU6, attach to UVSOCK by default, halt/run the target, sync breakpoints, or write variables.
- The adapter has an explicit `--attempt-existing` probe path for a later read-only smoke test, but this commit only verifies the no-connect path against the current machine.
- `status_from_uvsock_connection()` remains available for future execution-capable stages, but the new adapter uses a stricter read-only status projection for this milestone.

### Next Target

- Continue the same large milestone with an opt-in live Keil read-only smoke workflow:
  - require uVision already running with UVSOCK enabled
  - run `keil_backend_adapter_probe.py --attempt-existing --status`
  - surface the read-only snapshot in the UI without enabling control buttons
  - start modeling remote breakpoint/PC snapshots as incomplete until Keil readback parsing is proven

## Milestone 29 Update - Keil Read-Only Attach UI Path

### Goal

Continue the same large debug-backend milestone by wiring the Debug Workbench `连接` action to the Keil adapter's explicit read-only session snapshot path, while keeping target control and writes locked down.

### Completed

- Wired the Debug Workbench `attach` action to `_connect_keil_read_only_for_debug_workbench()`.
- The attach path now calls `KeilUvSockBackendAdapter.read_only_session_snapshot()` only after the user explicitly requests `连接`.
- The UI now surfaces read-only snapshot status and diagnostics:
  - mode
  - connection attempted
  - connection result
  - target running state
  - connection error
- Kept `Halt`、`Run`、`Step`、`同步断点` and `写变量` disabled after a read-only attach snapshot.
- Extended the UI probe with a fake read-only Keil backend so the attach path is tested without launching Keil, opening real UVSOCK, or touching ST-Link/F401CCU6.
- Verified the real CLI adapter `--attempt-existing --status` path in the current no-uVision environment; it fails safely with `attempted=False` and leaves dangerous capabilities disabled.

### Verified

- `python -m py_compile src\ui\gui.py tools\ui_debug_workbench_probe.py src\core\debug_backend.py src\core\keil\backend.py`
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_backend_adapter_probe.py --keil-root D:\Keil --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx --target HELLO`
  - PASS.
- `python tools\keil_backend_adapter_probe.py --keil-root D:\Keil --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx --target HELLO --attempt-existing --status`
  - PASS; current machine has no running uVision, so no connection was attempted.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil --show-exports`
  - PASS.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS.
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.

### Notes

- This update still does not launch Keil, access ST-Link/F401CCU6, halt/run the target, sync breakpoints, or write variables.
- The only real UVSOCK-capable path remains explicit: user clicks `连接`, or a developer runs `keil_backend_adapter_probe.py --attempt-existing --status`.
- On the current machine there is no running uVision process, so the real CLI attempt path proves safe failure but not a successful live session.

### Next Target

- Continue the same milestone by modeling read-only PC and remote breakpoint snapshot placeholders:
  - mark remote snapshots incomplete until real Keil parsing is proven
  - feed incomplete snapshots into existing dry-run diff UI without claiming sync readiness
  - prepare a future successful live UVSOCK smoke to populate status and snapshot evidence

## Milestone 29 Update - Incomplete PC and Remote Breakpoint Snapshot Placeholders

### Goal

Continue the read-only backend milestone by making PC and remote breakpoint evidence explicit in the backend snapshot model, while clearly marking the data incomplete until real Keil readback parsing is proven.

### Completed

- Added `DebugPcLocation` to `src\core\debug_backend.py`.
- Extended `DebugBackendSessionSnapshot` with:
  - `pc_location`
  - `remote_breakpoint_snapshot`
  - serialized PC and remote breakpoint records
- Keil adapter now creates an incomplete PC placeholder:
  - message: `Keil PC 位置读取尚未实现`
  - complete: `False`
- Keil adapter now creates an incomplete `KeilBreakpointRemoteSnapshot` placeholder:
  - complete: `False`
  - no remote breakpoints
  - error: `Keil 只读快照尚未实现断点枚举解析`
- Keil diagnostics now show:
  - `PC 位置: 待 Keil 回读`
  - `远端断点: 待 Keil 枚举`
- `MainWindow` now stores the adapter's remote breakpoint snapshot after discover/read-only attach.
- Existing dry-run breakpoint diff now receives the incomplete remote snapshot and keeps sync-breakpoint readiness waiting instead of treating an empty remote list as complete.
- Extended adapter and UI probes to assert that PC and remote breakpoint placeholders remain incomplete and data-only.

### Verified

- `python -m py_compile src\core\debug_backend.py src\core\keil\backend.py src\ui\gui.py tools\ui_debug_workbench_probe.py tools\debug_backend_adapter_probe.py tools\keil_backend_adapter_probe.py`
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_backend_adapter_probe.py --keil-root D:\Keil --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx --target HELLO`
  - PASS; PC and remote breakpoint placeholders reported incomplete.
- `python tools\keil_backend_adapter_probe.py --keil-root D:\Keil --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx --target HELLO --attempt-existing --status`
  - PASS; current machine has no running uVision, so no connection was attempted and placeholders remained incomplete.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil --show-exports`
  - PASS.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS.
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.

### Notes

- This update still does not launch Keil, access ST-Link/F401CCU6, halt/run the target, sync breakpoints, or write variables.
- The placeholders are deliberately incomplete; they are not evidence of a successful Keil PC read or breakpoint enumeration.
- Feeding incomplete remote breakpoints into dry-run diff prevents a dangerous false-positive where “no remote breakpoints” could be mistaken for a complete remote snapshot.

### Next Target

- Continue the same milestone with read-only snapshot evidence/audit:
  - preserve snapshot IDs in history/audit records where useful
  - model mismatch/stale reasons for project/target/port
  - prepare the future live UVSOCK smoke to replace placeholders with real PC and breakpoint readback

## Milestone 29 Update - Backend Snapshot Evidence in Dry-Run Audit

### Goal

Continue the read-only backend milestone by making backend session evidence visible in every relevant dry-run transaction, history entry and JSONL audit record, so future live Keil smoke tests can be traced back to the exact read-only snapshot that produced the UI state.

### Completed

- Extended Keil dry-run transactions with backend snapshot evidence:
  - `backend_snapshot_id`
  - trimmed `backend_snapshot` record
- Preserved backend snapshot evidence in:
  - transaction IDs
  - transaction audit records
  - dry-run command history entries
  - JSONL audit output
- Trimmed backend snapshot records to data-only fields:
  - backend, adapter, capture time
  - read-only and connection state
  - project, target, status detail
  - PC placeholder record
  - remote breakpoint snapshot id/completeness/error
- Wired `MainWindow` to store adapter snapshot records after both Keil discover and explicit read-only attach.
- Fed the stored backend snapshot record into the dry-run transaction builder.
- Updated Debug Workbench tooltips:
  - focused transaction tooltip now shows `后端快照`
  - history tooltip now includes `snapshot=<id>`
- Hardened probes so snapshot evidence must survive through transaction, UI tooltip, history and JSONL audit paths.
- Ran a parallel read-only architecture review for future OpenOCD/pyOCD/GDB support. The review confirmed the current adapter foundation is useful, but `DebugRuntimeState`, remote breakpoint snapshots, source providers and command transactions still need a backend-neutral split before direct OpenOCD/pyOCD/GDB adapters are clean.
- Re-checked Keil debug-chain feasibility against official Keil UVSOCK/Debug Commands documentation and the local `UVSC64.dll` exports; the approach remains viable, but live target behavior still requires a separate opt-in smoke stage.

### Verified

- `python -m py_compile src\core\debug_backend.py src\core\debug_workbench.py src\core\keil\backend.py src\core\keil\commands.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\keil_command_transaction_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_backend_adapter_probe.py --keil-root D:\Keil --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx --target HELLO`
  - PASS; no-connect adapter snapshot reported `attempted=False`, `connected=False`, with PC/remote-breakpoint placeholders incomplete.
- `python tools\keil_backend_adapter_probe.py --keil-root D:\Keil --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx --target HELLO --attempt-existing --status`
  - PASS; current machine has no running uVision, so it failed safely with `attempted=False`, `connected=False`.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_bridge_probe.py --keil-root D:\Keil --show-exports`
  - PASS; selected `UVSC64.dll`, found 103 exports and all important exports.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil`
  - PASS; DLL loaded, no running uVision, no live connection attempted.
- `python tools\keil_project_probe.py --project D:\Keil\code\HELLO\MDK-ARM\HELLO.uvprojx`
  - PASS; parsed one target and 19 source files.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `git diff --check`
  - PASS.

### Notes

- This update still does not launch Keil, access ST-Link/F401CCU6, halt/run the target, sync breakpoints, or write variables.
- The snapshot evidence proves traceability of dry-run decisions, not successful Keil PC readback or real breakpoint enumeration.
- Backend snapshot data remains deliberately JSON/data-only; no DLL handles, subprocesses, callbacks, thread objects or executors are stored in transaction/history/audit records.
- Keil remains the first proven local backend path, but the next architecture work must avoid baking Keil names and UVSOCK assumptions into the common workbench model.

### Next Target

- Continue the large debug-backend milestone with a backend-neutral architecture slice:
  - extract common `RemoteBreakpointSnapshot` / `RemoteBreakpoint` types out of Keil-specific commands
  - introduce a generic `DebugCommandTransaction` shell while keeping Keil UVSOCK preview formatting backend-specific
  - add backend registry/controller plumbing so UI selection can later switch between Keil, OpenOCD/GDB, pyOCD and offline replay
  - add no-hardware fake OpenOCD/GDB and pyOCD adapter probes before any live probe collision risk
  - then run a separate opt-in Keil live read-only smoke only after uVision is already open in Debug mode
