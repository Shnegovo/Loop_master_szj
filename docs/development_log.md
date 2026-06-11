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

## Milestone 30 - Backend-Neutral Debug Foundation

### Goal

Start the larger multi-toolchain architecture slice so Keil remains the first real backend, but OpenOCD/GDB, pyOCD and offline replay can plug into the same workbench model without copying Keil-specific snapshot and transaction types.

### Completed

- Added backend-neutral snapshot models in `src\core\debug_snapshots.py`:
  - `RemoteBreakpoint`
  - `RemoteBreakpointSnapshot`
  - `DebugPcLocation`
  - `TargetSnapshot`
  - generic `to_record()` / `from_record()` helpers
- Updated `DebugBackendSessionSnapshot` to use common PC and remote-breakpoint snapshot types instead of importing Keil breakpoint snapshots.
- Kept Keil compatibility by preserving:
  - `KeilRemoteBreakpoint`
  - `KeilBreakpointRemoteSnapshot`
  as aliases to the common snapshot models.
- Added `src\core\debug_backend_registry.py`:
  - registry stores backend factories rather than live sessions
  - default registry still creates Keil / UVSOCK first
  - optional placeholders register `OpenOCD / GDB`, `pyOCD` and `离线回放`
  - placeholders return data-only, read-only, unavailable snapshots and do not start processes or connect probes
- Added `src\core\debug_transactions.py`:
  - backend-neutral `DebugCommandTransaction`
  - backend-neutral guard states
  - unavailable-backend dry-run transaction builder
  - snapshot evidence preservation in generic audit records
- Updated `MainWindow` to create its debug backend through the registry instead of directly instantiating `KeilUvSockBackendAdapter`.
- Added no-hardware probes:
  - `tools\debug_snapshot_model_probe.py`
  - `tools\debug_backend_registry_probe.py`
  - `tools\debug_transaction_shell_probe.py`
- Verified fake OpenOCD/GDB, pyOCD and offline backend placeholders can be registered and audited without launching external tools.

### Verified

- `python -m py_compile src\core\debug_snapshots.py src\core\debug_backend.py src\core\debug_backend_registry.py src\core\debug_transactions.py src\core\debug_workbench.py src\core\keil\backend.py src\core\keil\commands.py src\core\keil\__init__.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_snapshot_model_probe.py tools\debug_backend_registry_probe.py tools\debug_transaction_shell_probe.py tools\debug_backend_adapter_probe.py tools\keil_command_transaction_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\debug_snapshot_model_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS, generated screenshots:
    - `tools\ui-debug-workbench\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench\03_debug_workbench_narrow.png`

### Notes

- This milestone does not launch Keil, OpenOCD or pyOCD.
- This milestone does not access ST-Link/F401CCU6, halt/run/step a target, sync breakpoints, write variables, flash firmware or open live GDB/UVSOCK sessions.
- The new OpenOCD/GDB and pyOCD entries are intentionally unavailable placeholders; their value is proving the registry, snapshot and dry-run audit surfaces are no longer Keil-only.
- The next live Keil smoke remains constrained to `OpenConnection -> DBG_STATUS -> CloseConnection` after uVision is manually opened in Debug mode.

### Next Target

- Continue Milestone 30 with controller/UI selection wiring:
  - surface backend selection in the Debug Workbench without changing the visual style
  - keep unavailable backends visibly blocked and dry-run only
  - move Keil transaction UI typing toward generic `DebugCommandTransaction` while preserving current Keil preview behavior
  - start extracting source provider / `SourceManifest` so Keil `.uvprojx`, ELF/DWARF and GDB source lists can feed the same editor tree

## Milestone 30 Update - Debug Backend Selector UI

### Goal

Wire the new backend registry into the Debug Workbench UI so Keil, OpenOCD/GDB, pyOCD and offline replay appear as selectable workbench backends, while unavailable non-Keil backends stay visibly blocked and dry-run only.

### Completed

- Added a compact backend selector to the Debug Workbench toolbar.
- Populated the selector from `DebugBackendRegistry` descriptors:
  - `Keil / UVSOCK`
  - `OpenOCD / GDB`
  - `pyOCD`
  - `离线回放`
- Keil remains the default backend and keeps the existing discover/read-only attach behavior.
- Switching backend now:
  - rebuilds the selected adapter from the registry
  - clears stale backend snapshots and dry-run history
  - refreshes status, diagnostics and command preview
  - preserves current project/target context where possible
- OpenOCD/GDB, pyOCD and offline replay placeholders now produce data-only blocked snapshots and generic dry-run command transactions.
- Generic unavailable transactions are shown in the same action-plan strip as Keil transactions, but they are not recorded into the Keil-specific history yet.
- Generalized command-plan wording for non-Keil backends so the UI no longer claims OpenOCD/GDB or pyOCD are using UVSOCK/Keil-specific steps.
- Extended the Debug Workbench UI probe to select OpenOCD/GDB, run the placeholder discover path and assert dangerous actions remain disabled.

### Verified

- `python -m py_compile src\core\debug_snapshots.py src\core\debug_backend.py src\core\debug_backend_registry.py src\core\debug_transactions.py src\core\debug_workbench.py src\core\keil\backend.py src\core\keil\commands.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_snapshot_model_probe.py tools\debug_backend_registry_probe.py tools\debug_transaction_shell_probe.py tools\debug_backend_adapter_probe.py tools\keil_command_transaction_probe.py tools\debug_workbench_model_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\debug_snapshot_model_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS; includes backend selector and OpenOCD/GDB placeholder flow.
- `python tools\ui_workspace_nav_probe.py --output-dir tools\ui-workspace-nav --width 1400 --height 820`
  - PASS.
- `python tools\ui_serial_integration_probe.py --output-dir tools\ui-serial-integration`
  - PASS.
- `git diff --check`
  - PASS except the existing CRLF normalization warning for `src\ui\gui.py`.

### Notes

- This update still does not launch Keil, OpenOCD, pyOCD or GDB.
- This update still does not connect ST-Link/F401CCU6, halt/run/step a target, sync breakpoints, write variables or flash firmware.
- Non-Keil backend history is intentionally not persisted through `KeilCommandHistory`; a generic history model should replace that before non-Keil backends become active.

### Next Target

- Continue Milestone 30 by extracting generic command history and source-provider surfaces:
  - move UI typing from `KeilCommandTransaction` to generic transaction-compatible protocols
  - add a generic history model for Keil and non-Keil transactions
  - introduce `SourceManifest` so Keil projects, ELF/DWARF and future GDB source lists can share the editor tree

## Milestone 30 Update - Generic Debug Command History

### Goal

Remove the next Keil-only bottleneck by giving Keil and non-Keil dry-run transactions a shared history model, so OpenOCD/GDB, pyOCD and offline replay placeholder transactions can be audited in the UI without borrowing `KeilCommandHistory`.

### Completed

- Added generic history primitives to `src\core\debug_transactions.py`:
  - `DebugCommandHistoryEntry`
  - `DebugCommandHistory`
- Generic history records:
  - backend id
  - transaction id
  - command preview
  - blocked reasons
  - guard summary
  - backend snapshot id/evidence
  - optional port and breakpoint diff data when present
- `DebugCommandHistory` now supports:
  - adjacent duplicate merge
  - bounded history
  - recent filters by kind, risk, blocked state and backend
  - JSON/data-only records
- `MainWindow` now uses `DebugCommandHistory` for all debug backends.
- Debug Workbench history tooltip now displays the backend id for each entry.
- Non-Keil unavailable backend transactions now appear in the same history strip as Keil dry-run transactions.
- Keil's existing `KeilCommandHistory` remains in place for compatibility and its probe coverage still passes.

### Verified

- `python -m py_compile src\core\debug_snapshots.py src\core\debug_backend.py src\core\debug_backend_registry.py src\core\debug_transactions.py src\core\debug_workbench.py src\core\keil\backend.py src\core\keil\commands.py src\core\keil\__init__.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_snapshot_model_probe.py tools\debug_backend_registry_probe.py tools\debug_transaction_shell_probe.py tools\debug_backend_adapter_probe.py tools\keil_command_transaction_probe.py tools\debug_workbench_model_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\debug_snapshot_model_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS; includes generic history duplicate merge and backend filter coverage.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS; OpenOCD/GDB placeholder now records a generic history entry.
- `git diff --check`
  - PASS except the existing CRLF normalization warning for `src\ui\gui.py`.

### Notes

- This update still does not launch or connect Keil/OpenOCD/pyOCD/GDB.
- Keil's older history class is intentionally left available until all Keil-specific probes and imports are migrated.
- Generic history is still dry-run/audit only; there is no execution path attached to history entries.

### Next Target

- Continue Milestone 30 with `SourceManifest` extraction:
  - keep Keil `.uvprojx` as the first source provider
  - add generic manifest objects for future ELF/DWARF, compile_commands and GDB source lists
  - adapt Debug Workbench source tree consumption toward the generic manifest without changing visual layout

## Milestone 30 Update - Source Manifest Foundation

### Goal

Extract a backend-neutral source manifest layer so Keil `.uvprojx` remains the first source provider, while future ELF/DWARF, `compile_commands.json`, OpenOCD/GDB source lists and manual source roots can feed the same Debug Workbench source tree.

### Completed

- Added `src\core\debug_sources.py` with:
  - `SourceEntry`
  - `SourceTreeNode`
  - `SourceManifest`
  - `source_entries_from_paths()`
  - `source_entries_from_keil_project()`
  - `source_manifest_from_keil_project()`
  - `source_tree_from_entries()`
- Keil project source parsing now produces a generic `SourceManifest`.
- `DebugWorkbenchTab` now stores `_source_manifest` and uses it for:
  - source count
  - local source paths
  - source tree construction
- Kept legacy imports through `src\core\debug_workbench.py` so existing probes and callers continue to work.
- Added `tools\debug_source_manifest_probe.py` covering:
  - Keil project -> manifest
  - path list -> grouped source entries
  - source tree generation
  - JSON/data-only manifest records

### Verified

- `python -m py_compile src\core\debug_sources.py src\core\debug_snapshots.py src\core\debug_backend.py src\core\debug_backend_registry.py src\core\debug_transactions.py src\core\debug_workbench.py src\core\keil\backend.py src\core\keil\commands.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_source_manifest_probe.py tools\debug_snapshot_model_probe.py tools\debug_backend_registry_probe.py tools\debug_transaction_shell_probe.py tools\debug_backend_adapter_probe.py tools\keil_command_transaction_probe.py tools\debug_workbench_model_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\debug_snapshot_model_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS.
- `git diff --check`
  - PASS.

### Notes

- This is still a data/model layer update; it does not add live OpenOCD/GDB, pyOCD or Keil execution.
- The Debug Workbench visual layout is unchanged; this only changes the source data path behind the tree.
- Keil `.uvprojx` remains the only real source provider today, but the UI no longer needs to grow around a Keil-only source list.

### Next Target

- Continue Milestone 30 with one of two larger next slices:
  - add an ELF/DWARF/manual-root `SourceManifest` provider and fake GDB source-list probe
  - or start the strictly opt-in Keil live read-only smoke (`OpenConnection -> DBG_STATUS -> CloseConnection`) if uVision is manually opened in Debug mode

## Milestone 30 Update - Manual Source Root Provider

### Goal

Add the first non-Keil `SourceManifest` provider so future OpenOCD/GDB, pyOCD and offline workflows can build the editor tree from source roots without requiring a Keil project file.

### Completed

- Added `source_manifest_from_roots()` to `src\core\debug_sources.py`.
- The provider scans source roots for known C/C++/ASM extensions and ignores unrelated files.
- The provider supports a `max_files` cap to avoid accidentally walking huge trees.
- Extended `tools\debug_source_manifest_probe.py` to cover:
  - nested manual source roots
  - non-source file filtering
  - source count limiting
  - JSON/data-only manifest output

### Verified

- `python -m py_compile src\core\debug_sources.py tools\debug_source_manifest_probe.py src\core\debug_workbench.py src\ui\debug_workbench_tab.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS.

### Notes

- The manual source-root provider is not yet exposed in the UI; it is a backend/provider foundation for future OpenOCD/GDB and offline flows.
- No debugger, probe, Keil, OpenOCD or pyOCD process is launched.

### Next Target

- Continue with a fake GDB source-list provider or, if uVision is manually opened in Debug mode, run the strictly opt-in Keil live read-only smoke.

## Milestone 30 Update - GDB Source List Provider

### Goal

Add a no-process `SourceManifest` provider for GDB-style source lists, preparing OpenOCD/GDB integrations to feed source files into the same Debug Workbench tree without launching GDB yet.

### Completed

- Added `source_manifest_from_gdb_sources()` to `src\core\debug_sources.py`.
- Added parser helpers for typical GDB `info sources` text:
  - strips section headers
  - splits comma/semicolon separated source lists
  - filters to known C/C++/ASM source extensions
  - deduplicates paths
  - respects `max_files`
- Extended `tools\debug_source_manifest_probe.py` with synthetic GDB `info sources` output.

### Verified

- `python -m py_compile src\core\debug_sources.py tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `git diff --check`
  - PASS.

### Notes

- This provider does not run GDB, OpenOCD or pyOCD; it only parses already captured source-list text.
- Future OpenOCD/GDB adapters can call this after a read-only `info sources` stage and hand the manifest to the same UI tree.

### Next Target

- Continue with compile_commands/ELF-adjacent source providers, or fold the new providers into the backend placeholder snapshots so the UI can preview non-Keil source trees.

## Milestone 30 Update - Compile Commands Source Provider

### Goal

Add a CMake/VSCode/CubeIDE-friendly `compile_commands.json` source provider so non-Keil projects can produce the same `SourceManifest` without requiring a Keil project file.

### Completed

- Added `source_manifest_from_compile_commands()` to `src\core\debug_sources.py`.
- The provider:
  - reads JSON only, without invoking compilers
  - resolves relative `file` entries against their `directory`
  - accepts absolute `file` entries
  - filters to known source/header/ASM extensions
  - deduplicates paths
  - respects `max_files`
- Extended `tools\debug_source_manifest_probe.py` with relative/absolute/duplicate/non-source compile command entries.

### Verified

- `python -m py_compile src\core\debug_sources.py tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `git diff --check`
  - PASS.

### Notes

- This provider does not run compilers, build systems, GDB, OpenOCD or pyOCD.
- This is another source-tree foundation piece for future multi-toolchain debug sessions.

### Next Target

- Add ELF-adjacent source discovery or wire non-Keil source manifests into backend placeholder snapshots for UI preview.

## Milestone 30 Update - Source Manifest Provenance Fields

### Goal

Add source provenance and diagnostics fields before introducing DWARF/ELF path recovery, so future missing-source and path-mapping problems can be audited without changing the UI first.

### Completed

- Extended `SourceEntry` with:
  - `origin`
  - `raw_path`
  - `resolved_from`
  - `compile_directory`
- Extended `SourceManifest` with:
  - `diagnostics`
  - `metadata`
- Providers now fill provenance details:
  - Keil entries use `origin=keil` and keep original project `path_text`
  - manual root entries use `origin=manual_roots`
  - GDB source-list entries use `origin=gdb_info_sources`
  - compile commands entries use `origin=compile_commands`, raw `file`, compile `directory`, and resolution mode
- Provider diagnostics now report basic counts such as source files, filtered entries, duplicates and truncation.
- Extended `tools\debug_source_manifest_probe.py` to assert provenance and diagnostics fields.

### Verified

- `python -m py_compile src\core\debug_sources.py tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `git diff --check`
  - PASS.

### Notes

- This remains a data-only source manifest update; no UI path mapping dialog is added yet.
- No debugger, compiler, OpenOCD, pyOCD, Keil live session or ST-Link access is used.

### Next Target

- Add ELF/DWARF-adjacent source discovery using the existing readelf path, then decide whether to expose a non-Keil source manifest preview in the Debug Workbench.

## Milestone 30 Update - ELF/DWARF Source Discovery

### Goal

Add an ELF/DWARF-adjacent source provider so Keil AXF/ELF, OpenOCD/GDB, pyOCD and offline replay flows can recover source files from DWARF line tables without depending on a Keil project file.

### Completed

- Added `source_manifest_from_readelf_line_table_text()` to `src\core\debug_sources.py`.
- Added `source_manifest_from_elf_dwarf()` as the future live wrapper around the existing `readelf -wl` route.
- The ELF/DWARF provider:
  - parses captured `readelf -wl` directory and file tables
  - supports old `Entry Dir Time Size Name` and newer `Entry Dir Name` table shapes
  - resolves absolute paths, source-root-relative paths and ELF-directory-relative paths
  - filters to known C/C++/ASM source extensions
  - deduplicates paths and records missing-source counts
  - keeps raw DWARF path, directory and resolution provenance on every `SourceEntry`
- Extended `tools\debug_source_manifest_probe.py` with synthetic DWARF line-table fixtures, including duplicate entries, non-source filtering and ASM classification.

### Verified

- `python -m py_compile src\core\debug_sources.py tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS.

### Notes

- The probe uses synthetic text only; it does not launch Keil, OpenOCD, pyOCD, GDB or connect ST-Link.
- This keeps the architecture Keil-first but backend-neutral: Keil `.uvprojx`, ELF/DWARF, `compile_commands.json`, GDB source lists and manual roots now all feed the same `SourceManifest` model.
- Path mapping UI is still pending; missing source files are surfaced through diagnostics and provenance fields for the next slice.

### Next Target

- Expose non-Keil `SourceManifest` previews through backend placeholder snapshots or a safe workbench selector, then add path-mapping diagnostics for missing DWARF/GDB sources.

## Milestone 30 Update - Non-Keil Source Preview UI

### Goal

Let the Debug Workbench consume non-Keil source manifests in the UI so OpenOCD/GDB, pyOCD and offline replay paths can show a real source tree before their live executors are implemented.

### Completed

- `DebugWorkbenchTab` now has a backend-neutral `set_source_manifest()` path.
- Keil target switching still regenerates the Keil project manifest, while external manifests are no longer overwritten by Keil-only rebuild logic.
- MainWindow now synchronizes a safe source preview when the user switches debug backends:
  - Keil restores the project manifest.
  - Non-Keil placeholders reuse the current source tree when available.
  - If no current tree exists, the preview can fall back to nearby `compile_commands.json` or lightweight source roots.
  - Empty previews still show diagnostics instead of leaving the tree in a stale state.
- Command-history synchronization is paused while backend/source/diagnostics are being switched so dry-run history does not capture intermediate UI state.
- Extended `tools\ui_debug_workbench_probe.py` to assert:
  - OpenOCD/GDB backend selection keeps a populated source preview.
  - the summary reflects the non-Keil preview.
  - switching back to Keil restores the Keil source manifest.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py src\ui\gui.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `git diff --check`
  - PASS with an existing `src/ui/gui.py` CRLF normalization warning only.

### Notes

- Backend switching still does not launch OpenOCD, pyOCD, GDB, Keil or readelf, and does not connect probes.
- The ELF/DWARF live wrapper remains available as a provider, but the UI preview does not run it automatically on backend switch to avoid freezes and hidden process launches.
- This is a UI consumption layer, not a path-mapping dialog yet.

### Next Target

- Add a small source-provider selector/path diagnostics surface for explicit ELF/DWARF, `compile_commands.json`, GDB text and manual-root previews, then use it to show missing-source path mapping hints.

## Milestone 30 Update - Source Provider Selector

### Goal

Make source provenance visible and controllable in the Debug Workbench without crowding the top toolbar, so multi-toolchain sessions can explain where the source tree came from before live OpenOCD/GDB, pyOCD or Keil control is enabled.

### Completed

- Added a compact source-provider selector to the left `源码树` header.
- Added source status chips for:
  - active source provider
  - source file count
  - missing-path state
- Source diagnostics are now folded into the existing diagnostics table ahead of backend diagnostics:
  - `源码来源`
  - `源码文件`
  - `源码缺失`
  - `源码重复`
  - `源码过滤`
  - `源码截断`
  - `源码根`
- MainWindow now tracks an explicit source provider key:
  - `自动`
  - `Keil 工程`
  - `编译数据库`
  - `源码根`
  - `ELF/DWARF`
  - `GDB 文本`
- Explicit `compile_commands` and manual-root preview paths are wired.
- `ELF/DWARF` and `GDB 文本` are visible as pending explicit providers, but selecting them does not launch tools or processes yet.
- Extended `tools\ui_debug_workbench_probe.py` to verify:
  - provider selector labels
  - Keil source chips
  - OpenOCD/GDB preview source diagnostics
  - explicit `compile_commands` preview
  - explicit manual-root preview

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py src\ui\gui.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.

### Notes

- Default backend/source switching still does not run `readelf`, OpenOCD, pyOCD, GDB or Keil, and does not touch ST-Link.
- The provider selector is intentionally placed in the source-tree panel instead of the top toolbar to avoid narrow-layout clipping.
- This adds the UI path for provenance and explicit providers; actual file pickers/path mapping dialogs are still pending.

### Next Target

- Move to lifecycle/exit hardening: centralize worker shutdown registration and add close probes for sampling, serial and debug-worker stuck scenarios.

## Milestone 30 Update - Lifecycle Shutdown Report

### Goal

Make shutdown behavior auditable and keep process-exit probes covering real active-worker scenarios, so close-window regressions are caught before adding more debug backends.

### Completed

- Added `src\core\lifecycle.py`:
  - `ShutdownSequence`
  - `ShutdownStepResult`
  - `ShutdownReport`
- `MainWindow._shutdown()` now runs the existing shutdown steps through `ShutdownSequence` and stores `self._shutdown_report`.
- Shutdown steps now record per-step timing, detail, soft failures and exceptions while still continuing later cleanup.
- Existing shutdown order is preserved:
  - stop timers
  - request backend shutdown
  - stop sampling
  - stop serial
  - save config
  - disconnect backend
- Added `tools\lifecycle_shutdown_probe.py` for fast no-Qt lifecycle sequencing checks.
- Extended close-process scenarios with `stuck-serial-worker`, a worker that intentionally ignores the shutdown flag.
- Updated `tools\ui_close_process_probe.py` to accept the new stuck-worker scenario.

### Verified

- `python -m py_compile src\core\lifecycle.py src\ui\gui.py tools\lifecycle_shutdown_probe.py tools\ui_close_process_probe.py tools\ui_close_scenario_entry.py`
  - PASS.
- `python tools\lifecycle_shutdown_probe.py`
  - PASS.
- `python tools\collector_fake_transport_probe.py`
  - PASS.
- `python tools\serial_controller_probe.py`
  - PASS.
- `python tools\ui_close_process_probe.py --entry main.py --exit-timeout 10`
  - PASS, close-to-exit about `1573.3ms`.
- `python tools\ui_close_process_probe.py --scenario sampling --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `817.0ms`.
- `python tools\ui_close_process_probe.py --scenario slow-sampling --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `726.2ms`.
- `python tools\ui_close_process_probe.py --scenario serial-worker --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `779.4ms`.
- `python tools\ui_close_process_probe.py --scenario stuck-serial-worker --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `1716.6ms`.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `git diff --check`
  - PASS with an existing `src/ui/gui.py` CRLF normalization warning only.

### Notes

- The stuck-worker scenario verifies that a daemon worker ignoring shutdown does not keep the process alive.
- `self._shutdown_report` is now available for future probes or diagnostic UI if a close step becomes slow or incomplete.
- This does not add new live debug actions and does not touch ST-Link, Keil, OpenOCD, pyOCD or GDB.

### Next Target

- Continue lifecycle hardening by registering future debug/backend workers through the same shutdown-report vocabulary, then return to explicit source file/path mapping actions.

## Milestone 30 Update - Shutdown Result Propagation

### Goal

Make lower-level serial and SWD shutdown failures visible to controllers and the shutdown report instead of reporting success after only requesting cleanup.

### Completed

- `SerialCollector.stop()` now returns whether the serial read thread actually stopped.
- `SerialController.shutdown()` now combines collector stop result and worker join result.
- Serial disconnect worker now reports a read-thread timeout through the existing disconnect-finished error path.
- `SWDBackend.disconnect(timeout)` now returns `False` when:
  - the I/O lock cannot be acquired within the timeout
  - session close times out
  - session close raises an exception
- `MainWindow._shutdown()` now records backend disconnect success/failure in the shutdown report.
- Added `tools\swd_disconnect_probe.py` to verify SWD disconnect success, close failure, close timeout and lock timeout without hardware.
- Extended `tools\serial_controller_probe.py` to verify a stuck collector makes controller shutdown return `False`.

### Verified

- `python -m py_compile src\core\serial_backend.py src\ui\serial_controller.py src\core\mem_backend.py src\ui\gui.py tools\serial_controller_probe.py tools\swd_disconnect_probe.py`
  - PASS.
- `python tools\serial_controller_probe.py`
  - PASS.
- `python tools\swd_disconnect_probe.py`
  - PASS.
- `python tools\collector_fake_transport_probe.py`
  - PASS.
- `python tools\lifecycle_shutdown_probe.py`
  - PASS.
- `python tools\ui_close_process_probe.py --scenario sampling --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `551.1ms`.
- `python tools\ui_close_process_probe.py --scenario serial-worker --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `407.3ms`.
- `python tools\ui_close_process_probe.py --scenario stuck-serial-worker --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `950.4ms`.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench --width 1440 --height 900`
  - PASS.
- `git diff --check`
  - PASS with existing CRLF normalization warnings only.

### Notes

- Process-exit success is no longer the only signal; low-level cleanup can now report incomplete shutdown to lifecycle diagnostics.
- No live debugger, serial hardware, Keil, OpenOCD, pyOCD, GDB or ST-Link access is used.

### Next Target

- Add data-only backend lifecycle metadata to the debug backend registry, so future Keil/OpenOCD/pyOCD workers can be listed and audited before any real worker is introduced.

## Milestone 30 Update - Debug Backend Lifecycle Metadata

### Goal

Add data-only backend lifecycle metadata so Keil, OpenOCD/GDB, pyOCD and offline replay backends can declare future worker/shutdown behavior before any live debug worker is introduced.

### Completed

- Added `DebugBackendWorkerState` and `DebugBackendWorkerLifecycleRegistration`.
- `DebugBackendDescriptor` can now carry lifecycle registration data without changing the adapter factory contract.
- `DebugBackendRegistry.lifecycle()` and `DebugBackendRegistry.lifecycles()` expose lifecycle metadata without creating adapters.
- Default Keil, OpenOCD/GDB, pyOCD and offline replay registrations now declare:
  - no autostart
  - read-only-first
  - no default process launch
  - no default probe connection
  - no default target write
- Kept `DebugBackendDescriptor` constructor compatibility by appending the new lifecycle field after existing fields.
- Extended `tools\debug_backend_registry_probe.py` to verify lifecycle data is serializable, data-only, and does not call backend factories.

### Verified

- `python -m py_compile src\core\debug_backend.py src\core\debug_backend_registry.py src\core\debug_transactions.py src\core\lifecycle.py tools\debug_backend_registry_probe.py tools\debug_transaction_shell_probe.py tools\lifecycle_shutdown_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\lifecycle_shutdown_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir %TEMP%\loopmaster-stage-lifecycle-ui --width 1440 --height 900`
  - PASS.
- `python tools\ui_close_process_probe.py --scenario stuck-serial-worker --exit-timeout 10 --settle 1.0`
  - PASS, close-to-exit about `1250.5ms`.
- `python tools\serial_controller_probe.py`
  - PASS.
- `python tools\collector_fake_transport_probe.py`
  - PASS, actual rate about `246.8Hz`.

### Notes

- This stage is registry/model-only.
- No live debugger process is launched.
- No ST-Link, Keil session, OpenOCD, pyOCD, GDB, serial hardware or target MCU access is used.
- UI backend option tuples remain `(key, label, note)`; lifecycle metadata is not pushed into the selector contract.
- Backend factory constructors must remain side-effect-free. Future OpenOCD/pyOCD/Keil worker startup belongs behind explicit opt-in actions and shutdown-report registration.

### Next Target

- Return to explicit source/path mapping actions:
  - add file/directory selection for `compile_commands.json`, manual roots, ELF/DWARF and GDB text previews
  - show missing-source diagnostics and path-mapping hints
  - keep all providers backend-neutral so Keil, OpenOCD/GDB and pyOCD share the same `SourceManifest` route
  - later surface lifecycle metadata in diagnostics without changing backend selector tuple shape

## Milestone 30 Update - Explicit Source Provider Configuration

### Goal

Let the Debug Workbench accept explicit source-provider inputs without launching external tools, so Keil, OpenOCD/GDB, pyOCD and offline replay can share the same source tree and missing-path diagnostics.

### Completed

- Added a compact `配置` button next to the source-provider selector.
- Added explicit configuration methods for:
  - `compile_commands.json`
  - manual source roots
  - pasted GDB `info sources` text
  - pasted `readelf -wl` DWARF line-table text
- Kept ELF/DWARF safe: the UI parses already captured text and still does not auto-run `readelf`.
- GDB text and compile commands now report missing source counts so the existing source chip can show `缺失 N`.
- Manual source roots now report invalid-root and truncation diagnostics.
- GDB relative paths can now resolve against an explicit source root instead of the current working directory.
- Fixed a UI state bug where an external source manifest on the Keil backend could be silently restored to the Keil project manifest by target-combo refresh events.
- Persisted debug source-provider configuration in `loopmaster.json`.
- Added `tools\ui_debug_source_provider_probe.py` for a focused no-dialog/no-process UI regression test.

### Verified

- `python -m py_compile src\core\debug_sources.py src\ui\gui.py src\ui\debug_workbench_tab.py tools\debug_source_manifest_probe.py tools\ui_debug_source_provider_probe.py tools\ui_debug_workbench_probe.py tools\debug_backend_registry_probe.py tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\ui_debug_source_provider_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir %TEMP%\loopmaster-ui-debug-workbench-source-config-rerun --width 1440 --height 900`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\ui_close_process_probe.py --entry main.py --exit-timeout 10`
  - PASS, close-to-exit about `376.6ms`.

### Notes

- This stage does not launch Keil, OpenOCD, pyOCD, GDB or `readelf`.
- This stage does not access ST-Link, serial hardware or the target MCU.
- It is a source-provider configuration slice, not a full path-mapping rule editor yet.
- The source tree already disables missing files and appends `(缺失)`, so users can see which files need mapping before live debug is enabled.

### Next Target

- Add a lightweight path-mapping hint/action layer:
  - show the most common missing directories from DWARF/GDB/imported manifests
  - let the user remap one missing prefix to a local source root
  - keep remapping data-only and previewable before any live backend work
  - keep the new focused source-provider probe separate from the larger Debug Workbench visual probe

## Milestone 30 Update - Missing Source Mapping Hints

### Goal

Make missing-source diagnostics more actionable by summarizing where imported GDB/DWARF/compile database paths failed to resolve, without introducing live debugger actions or a full mapping-rule editor yet.

### Completed

- Added `SourcePathMappingHint` and `source_manifest_missing_path_hints()`.
- Missing source entries are grouped by resolved missing directory.
- Each hint keeps:
  - missing directory
  - missing file count
  - raw path examples
  - resolution provenance values such as `root_relative` or `directory_relative`
- Debug Workbench diagnostics now show:
  - `映射提示`
  - `映射示例`
- Source provider chips tooltip now includes mapping hint details when missing paths exist.
- The focused source-provider UI probe now verifies mapping examples for compile commands and GDB text imports.

### Verified

- `python -m py_compile src\core\debug_sources.py src\ui\debug_workbench_tab.py tools\debug_source_manifest_probe.py tools\ui_debug_source_provider_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\ui_debug_source_provider_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir %TEMP%\loopmaster-ui-debug-workbench-path-hints --width 1440 --height 900`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.

### Notes

- This is still data-only and UI-only.
- No Keil, OpenOCD, pyOCD, GDB, `readelf`, ST-Link, serial hardware or target MCU access is used.
- The next step is the actual preview remapping action: apply a user-selected local root to missing source prefixes and rebuild the manifest.

### Next Target

- Add data-only source remapping preview:
  - choose a missing prefix/directory from the hint list
  - map it to a local source root
  - rebuild affected entries and show before/after missing counts
  - keep remap state provider-neutral for Keil, GDB, DWARF and offline replay

## Milestone 30 Update - Source Remap Preview

### Goal

Add a safe preview path for mapping missing source directories to a local source root before live debug backends depend on the source tree.

### Completed

- Added `SourcePathRemapPreview`.
- Added `preview_source_manifest_path_remap()`:
  - takes a `SourceManifest`, missing directory and local root
  - rebuilds only matching missing entries
  - preserves origin/raw path/provenance
  - reports before/after missing counts
  - returns a new manifest without mutating the original
- Added `MainWindow.preview_debug_source_remap()` so the Debug Workbench can apply a preview manifest and update diagnostics.
- Debug Workbench diagnostics now surface `重映射` and `重映射命中`.
- Extended the focused source-provider UI probe to verify missing count goes from `1` to `0` after a preview remap.

### Verified

- `python -m py_compile src\core\debug_sources.py src\ui\gui.py tools\debug_source_manifest_probe.py tools\ui_debug_source_provider_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\ui_debug_source_provider_probe.py`
  - PASS.

### Notes

- This is data-only preview plumbing, not a full user-facing remap wizard yet.
- No Keil, OpenOCD, pyOCD, GDB, `readelf`, ST-Link, serial hardware or target MCU access is used.
- Remap provenance is marked as `remap:<previous resolved_from>` so later UI can explain why a path changed.

### Next Target

- Add the user-facing remap action:
  - select the top missing-path hint from the UI
  - choose a local source root
  - apply the existing preview remap
  - persist remap choices per provider/project

## Milestone 30 Update - User Source Remap Action

### Goal

Make the source remap preview usable from the Debug Workbench UI while keeping it data-only and safe for future Keil/OpenOCD/pyOCD flows.

### Completed

- Added an `映射` button next to the source-provider `配置` button.
- The button enables only when the current `SourceManifest` has missing-path hints.
- Clicking the action selects the highest-priority missing directory, asks for a local source root, and applies the existing remap preview.
- `MainWindow.preview_debug_source_remap()` now supports `persist=True`.
- Remap choices are saved in `loopmaster.json` under `debug_sources.remaps`.
- The focused source-provider UI probe now verifies:
  - remap button enables when paths are missing
  - missing count changes from `1` to `0`
  - remap diagnostics show `重映射命中`
  - remap button disables after paths are resolved
  - remap config is recorded

### Verified

- `python -m py_compile src\ui\gui.py src\ui\debug_workbench_tab.py tools\ui_debug_source_provider_probe.py`
  - PASS.
- `python tools\ui_debug_source_provider_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.

### Notes

- The UI action still only rebuilds the local source manifest.
- It does not launch Keil, OpenOCD, pyOCD, GDB, `readelf`, ST-Link, serial hardware or target MCU access.
- Saved remap choices are recorded but not yet automatically replayed on app startup.

### Next Target

- Replay saved remap choices after source-provider config is restored:
  - apply matching saved remaps after a manifest is rebuilt
  - keep a clear diagnostic row showing which remap was replayed
  - avoid replaying stale remaps that no longer match any missing directory

## Milestone 31 Update - Saved Source Remap Replay

### Goal

Make saved source-path remaps survive provider rebuilds and config restore, so imported `compile_commands.json`, GDB source lists, DWARF text and future Keil/OpenOCD/pyOCD source manifests do not fall back to missing paths after the user already mapped them once.

### Completed

- Added saved remap replay inside `MainWindow._sync_debug_source_manifest_preview()`.
- Replay now:
  - matches remap records by source provider
  - treats Keil `auto` and explicit `keil` as compatible while Keil is the active backend
  - applies matching missing-directory remaps after each manifest rebuild
  - skips stale mappings whose missing directory no longer exists or whose local root disappeared
  - keeps remap replay data-only and avoids external processes
- Debug Workbench diagnostics now show:
  - `重映射重放`
  - `重映射`
  - `重映射命中`
  - `重映射跳过`
- The focused source-provider UI probe now verifies:
  - a persisted remap is recorded once
  - provider rebuild replays the saved remap
  - explicit config restore replays the saved remap
  - remapped local source paths flow into `tab.local_source_paths()`
  - replay does not duplicate saved remap records
- Cleaned ignored local development leftovers:
  - Python `__pycache__` directories
  - old regenerable UI screenshot/runtime output directories under `tools`

### Verified

- `python -m py_compile src\ui\gui.py src\ui\debug_workbench_tab.py tools\ui_debug_source_provider_probe.py`
  - PASS.
- `python tools\ui_debug_source_provider_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir %TEMP%\loopmaster-ui-debug-remap-replay --width 1440 --height 900`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.

### Notes

- This stage does not launch Keil, OpenOCD, pyOCD, GDB, `readelf`, ST-Link, serial hardware or target MCU access.
- The GitHub repository was switched to `PUBLIC` after the release-link 404 check. An unauthenticated HEAD request now returns `200 OK` for the repo and the `LoopMaster_v2.1.exe` release asset resolves through GitHub's release-asset redirect to a `200 OK` download.
- The repository does not yet contain a root `LICENSE` file; add one before encouraging reuse or redistribution beyond casual downloads.
- The local `dist\LoopMaster_v2.1.exe` and `cockpit-tools-main` reference directory were intentionally kept.

### Next Target

- Start the larger debug adapter foundation:
  - define a backend-neutral connection/session contract for Keil, OpenOCD/GDB, pyOCD and offline replay
  - keep every action dry-run/read-only until an explicit hardware smoke stage
  - move new debug panels out of `gui.py` into registered workbench modules
  - add an explicit open-source license and update the README/release notes for public users

## Milestone 32 Update - Public Repository Baseline

### Goal

Make the now-public repository understandable and legally usable for outside users without changing runtime behavior.

### Completed

- Switched `Shnegovo/Loop_master_szj` to `PUBLIC` on GitHub.
- Verified unauthenticated access:
  - repository page returns `200 OK`
  - `v2.1.0` release asset redirects and returns `200 OK`
  - release asset size is `100197322` bytes
- Added a root `LICENSE` file using MIT License.
- Rewrote `README.md` for the current v2.1 direction:
  - release download link
  - current capabilities
  - dry-run/live-debug safety boundary
  - source-run instructions
  - hardware requirements
  - development probes
  - short-term and long-term roadmap

### Verified

- `python -m py_compile main.py src\ui\gui.py src\ui\debug_workbench_tab.py tools\ui_debug_source_provider_probe.py`
  - PASS.
- `python tools\debug_source_manifest_probe.py`
  - PASS.
- `gh repo view Shnegovo/Loop_master_szj --json visibility,isPrivate,url`
  - `visibility=PUBLIC`, `isPrivate=false`.
- `gh release view v2.1.0 --repo Shnegovo/Loop_master_szj --json assets,url`
  - release asset is present and uploaded.

### Notes

- This stage only changes public-facing docs/license and repository visibility.
- No Keil, OpenOCD, pyOCD, GDB, `readelf`, ST-Link, serial hardware or target MCU access is used.
- The installer is still unsigned, so Windows/browser reputation prompts are expected.

### Next Target

- Start the backend-neutral debug session contract:
  - model session lifecycle, capabilities and command safety levels
  - support Keil/OpenOCD-GDB/pyOCD/offline replay without live execution
  - add no-hardware probes for contract behavior
  - keep UI integration modular and avoid growing `gui.py`

## Milestone 33 Update - Backend-Neutral Session Contract

### Goal

Introduce a backend-neutral debug session contract so Keil, OpenOCD/GDB, pyOCD and offline replay can share the same lifecycle, capability and safety-policy vocabulary before any live debugger control is enabled.

### Completed

- Added `src\core\debug_session_contract.py`.
- New data-only contract types include:
  - `DebugSessionBackend`
  - `DebugSessionState`
  - `DebugTargetState`
  - `DebugCommandSafety`
  - `DebugSessionSpec`
  - `DebugSessionSnapshot`
  - `DebugSessionCapabilities`
  - `DebugSessionSafetyPolicy`
  - `DebugSessionCommand`
  - `DebugSessionEvent`
- Added `command_matrix_for_session()` so a snapshot can produce a generic command availability matrix for:
  - discover
  - attach
  - disconnect
  - halt
  - run
  - step
  - sync breakpoints
  - write variables
- Default policy remains dry-run/read-only:
  - `dry_run=True`
  - no process launch
  - no probe connection
  - no run control
  - no target writes
- `DebugBackendSessionSnapshot` now exports `to_session_contract()` so existing Keil and placeholder backend snapshots can feed the new contract without a disruptive UI rewrite.
- Extended probes to assert:
  - contract JSON serialization
  - no handles/callables/process/thread objects in contract data
  - default safety gates block live attach/run-control/write commands
  - Keil adapter snapshots convert to the new contract
  - OpenOCD/GDB, pyOCD and offline placeholder snapshots convert to the new contract

### Verified

- `python -m py_compile src\core\debug_session_contract.py src\core\debug_backend.py tools\debug_session_contract_probe.py tools\debug_backend_adapter_probe.py tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_session_contract_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\debug_snapshot_model_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.

### Notes

- This is an architecture/data-contract slice only.
- It does not launch Keil, OpenOCD, pyOCD, GDB, `readelf`, ST-Link, serial hardware or target MCU access.
- Existing UI still consumes the old workbench status/transaction objects; the new session contract is now available as the next migration target.
- Public repo audit follow-up still needed:
  - add release checksum and verification note
  - improve public release notes/user guide/screenshots/troubleshooting
  - add package/build metadata such as `pyproject.toml`
  - consider pinned/locked dependency set for reproducible public installs

### Next Target

- Build the dry-run debug session controller:
  - own selected backend, current contract snapshot, safety policy and command matrix
  - adapt Keil/OpenOCD-GDB/pyOCD/offline snapshots into the controller
  - expose a compact API for UI refresh without adding more orchestration to `gui.py`
  - keep all actions no-hardware and blocked by the default safety policy

## Milestone 34 Update - Dry-Run Session Controller

### Goal

Add a small controller layer that owns backend selection, session contract snapshots, safety policy and command matrix state before wiring this into the UI.

### Completed

- Added `src\core\debug_session_controller.py`.
- The controller now:
  - owns the selected `DebugBackendKind`
  - keeps a `DebugSessionSpec`
  - keeps the current neutral `DebugSessionSnapshot`
  - exposes the current `DebugSessionCommand` matrix
  - can switch backend without creating backend adapters
  - can update project/target/source-provider metadata without live side effects
  - can convert adapter discovery/read-only snapshots into neutral session state
  - can generate data-only session events for audit/history use
- Added `tools\debug_session_controller_probe.py`.
- The probe verifies:
  - controller construction does not create backend adapters
  - placeholder OpenOCD/GDB preview remains no-connect/no-live
  - fake adapter discovery and read-only snapshot flow through the controller
  - default dry-run policy blocks attach/run/write execution
  - explicit read-only policy enables only read-only attach/disconnect
  - explicit run-control policy enables run/step/breakpoint sync but still blocks writes
  - controller records are JSON-serializable and data-only

### Verified

- `python -m py_compile src\core\debug_session_controller.py tools\debug_session_controller_probe.py`
  - PASS.
- `python tools\debug_session_contract_probe.py`
  - PASS.
- `python tools\debug_session_controller_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.

### Notes

- This is still no-hardware architecture work.
- No Keil, OpenOCD, pyOCD, GDB, `readelf`, ST-Link, serial hardware or target MCU access is used.
- The controller is not yet wired into `src\ui\gui.py`; that is deliberate to keep the first slice low risk.

### Next Target

- Wire the controller into the Debug Workbench refresh path in a read-only way:
  - create it beside the existing backend registry
  - mirror backend selection into the controller
  - surface contract diagnostics/command matrix without changing live behavior
  - keep existing UI probes passing

## Milestone 35 Update - Session Contract UI Mirror

### Goal

Expose the new dry-run session controller in the Debug Workbench diagnostics without changing existing backend behavior.

### Completed

- `MainWindow` now creates a `DebugSessionController` beside the existing backend registry.
- Backend selection is mirrored into the controller.
- Backend discovery and read-only snapshot results are applied to the controller via the neutral session contract.
- Discovery/read-only errors are mirrored as controller error snapshots.
- Backend diagnostics now append neutral contract rows:
  - `会话合同`
  - `目标状态`
  - `安全策略`
  - `合同命令`
  - `可执行命令`
- Added controller APIs for:
  - applying an existing backend snapshot
  - marking a controller error without touching hardware
- Extended `tools\debug_session_controller_probe.py` for those APIs.

### Verified

- `python -m py_compile src\ui\gui.py src\core\debug_session_controller.py tools\debug_session_controller_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\debug_session_controller_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir %TEMP%\loopmaster-ui-session-controller-rerun --width 1440 --height 900`
  - PASS.
- `python tools\debug_session_contract_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.

### Notes

- This still does not perform live Halt/Run/Step/write-variable operations.
- The next stage should stop expanding dry-run scaffolding and introduce a real debugger path with a strictly read-only first smoke.

### Next Target

- Add a real read-only debugger smoke path:
  - first try Keil UVSOCK/Debug Commands against an already-open Keil debug session
  - read session/target status only
  - do not Halt/Run/Step/sync breakpoints/write variables
  - if Keil is not reachable, add an OpenOCD/GDB or pyOCD read-only discovery path next

## Milestone 36 Update - Real Variable Write Polish and F401 Probe Target

### Goal

Stop expanding dry-run-only scaffolding and push a real user-facing variable modification flow forward. The working rule for the next stages is:

- Real function first.
- Architecture is added only where it directly supports the real function being delivered.

### Completed

- Polished the existing pyOCD/ST-Link variable write path in the LoopMaster scope panel:
  - replaced the native single-line input with the PCL-styled input dialog
  - added an explicit PCL confirmation step before target writes
  - confirmation shows variable name, address, type, current value and new value
  - restore now has its own confirmation step
  - success messages now state that write/restore was read-back verified
  - restore keeps the first pre-write raw bytes so repeated writes can still recover the original value
- Added local write audit logging:
  - `loopmaster_variable_writes.jsonl`
  - records write/restore action, variable, address, type, target state, sampling state and verified result
  - added `文件 -> 查看变量写入记录`
  - kept the audit file ignored as local runtime state
- Added `tools\ui_variable_write_flow_probe.py`:
  - fake backend, no hardware, offscreen UI
  - verifies write button enablement gates
  - verifies write calls carry address/type/value
  - verifies temporary old raw bytes are captured
  - verifies restore calls and clears state
  - verifies input cancel and confirmation cancel do not touch the backend
- Added a minimal Keil STM32F401CCU6 validation firmware:
  - `firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx`
  - `main.c`
  - `startup_stm32f401ccux.s`
  - `f401_variable_probe.sct`
  - `README.md`
  - exposes `debug_setpoint`, `debug_feedback`, `debug_counter`, `debug_gain`, `debug_error`, `debug_flags`
  - intended as the fixed target for LoopMaster/Keil/ST-Link variable read-write validation

### Verified

- `python -m py_compile src\ui\pcl_theme.py src\ui\gui.py tools\ui_variable_write_flow_probe.py`
  - PASS.
- `python tools\ui_variable_write_flow_probe.py`
  - PASS.
- `python tools\keil_project_probe.py --project firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx`
  - PASS.
- `python tools\debug_session_contract_probe.py`
  - PASS.
- `python tools\debug_session_controller_probe.py`
  - PASS.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil`
  - PASS; local Keil/UVSOCK DLL is available, uVision is not currently running.

### Notes

- This stage improves the already-real pyOCD/ST-Link memory write path. It does not yet execute Keil UVSOCK variable writes.
- Keil launch/connect feasibility exists locally, but the production Keil backend still reports `can_write_variables=False`.
- The F401 project is deliberately small so the next live write stage has known symbols and a safe RAM-only target.

### Next Target

- Implement real Keil live variable modification against the F401 probe project:
  - add a persistent UVSOCK session wrapper instead of one-shot preflight only
  - wire `UVSC_DBG_VARIABLE_SET` and expression readback/eval wrappers
  - keep RAM/type/readback safety gates consistent with the pyOCD path
  - add a Keil project profile so LoopMaster can launch uVision with `.uvprojx`, target and port
  - run the first smoke on the F401 variables (`debug_setpoint`, `debug_gain`, `debug_feedback`) before adding broader debugger features

## Milestone 37 Update - Keil UVSOCK Live Write Entry Point

### Goal

Create the first real Keil-side path that can modify a variable through uVision/UVSOCK once a debug session is running.

### Completed

- Added a persistent `KeilUvscLiveSession` wrapper in `src\core\keil\uvsock.py`.
- Added ctypes bindings for the UVSOCK calls needed by live expression writes:
  - `UVSC_DBG_ENTER`
  - `UVSC_GEN_SET_OPTIONS`
  - `UVSC_DBG_STATUS`
  - `UVSC_DBG_CALC_EXPRESSION`
  - `UVSC_DBG_EVAL_EXPRESSION_TO_STR`
  - `UVSC_DBG_EXIT`
- Added packed ctypes structures matching the needed UVSOCK header shapes:
  - `TVAL`
  - `SSTR`
  - `VSET`
  - `UVSOCK_OPTIONS`
- Implemented expression-based variable writes:
  - assignment expression, for example `debug_setpoint = 5000`
  - readback expression, for example `debug_setpoint`
  - result object with assignment status, readback status and readback text
- Added `tools\keil_live_variable_write_probe.py`:
  - fake UVSC DLL, no Keil process, no hardware
  - verifies call order, VSET string payload, status handling and readback parsing
- Added `tools\keil_live_write_probe.py`:
  - default preflight only
  - launch-plan mode for the F401 Keil probe project
  - explicit `--write` mode for real live writes when uVision/UVSOCK is running
- Exported the live session/result types from `src\core\keil\__init__.py`.

### Verified

- `python -m py_compile src\core\keil\uvsock.py src\core\keil\__init__.py tools\keil_live_variable_write_probe.py tools\keil_live_write_probe.py`
  - PASS.
- `python tools\keil_live_variable_write_probe.py`
  - PASS.
- `python tools\keil_live_write_probe.py --keil-root D:\Keil`
  - PASS; preflight only, uVision is not running.
- `python tools\keil_live_write_probe.py --keil-root D:\Keil --plan-launch --project firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx --target "STM32F401CCU6 Variable Probe" --port 4827`
  - PASS.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil`
  - PASS.
- `python tools\keil_project_probe.py --project firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx`
  - PASS.
- `python tools\debug_session_contract_probe.py`
  - PASS.
- `python tools\debug_session_controller_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.

### Notes

- This stage creates a real Keil write execution entry point, but it still has not written to the physical F401 board because uVision is not currently running.
- The first implementation uses expression assignment through `UVSC_DBG_CALC_EXPRESSION` plus `UVSC_DBG_EVAL_EXPRESSION_TO_STR` readback. This avoids blocking the real write path on `UVSC_DBG_VARIABLE_SET` variable-ID enumeration.
- The explicit write command is intentionally opt-in:
  - no `--write` means no target state modification
  - `--write --expression debug_setpoint --value 5000` is required before a real UVSOCK write is sent

### Next Target

- Drive a real end-to-end Keil/F401 smoke:
  - launch uVision with the F401 probe `.uvprojx`
  - ensure the project builds or clearly reports the missing Keil pack/build reason
  - enter/debug-load the F401 target if possible
  - run `tools\keil_live_write_probe.py --write --expression debug_setpoint --value 5000`
  - then wire the same live write call into the Debug Workbench UI behind the existing confirmation/audit pattern

## Milestone 38 Update - Real Keil/ST-Link F401 Memory Write Smoke

### Goal

Move from UVSOCK readiness to a verified physical F401 write path: launch or
attach to uVision, enter the ST-Link debug session, write a known RAM variable,
and read it back.

### Completed

- Added a committed `F401VariableProbe.uvoptx` for the F401 validation project.
  - selects `STLink\ST-LINKIII-KEIL_SWO.dll`
  - uses SWD protocol at 10 MHz
  - uses the STM32F4 256 KB flash algorithm
  - prevents uVision from falling back to ULINK on this validation target
- Adjusted the F401 project metadata:
  - added the F401 SVD path
  - kept the STM32F401CCUx device, RAM and flash layout
  - kept command-line build clean with Keil ARMCLANG V6.22
- Hardened `KeilUvscLiveSession`:
  - `UVSC_Init` now scopes to the requested UVSOCK port
  - `UVSC_GetLastError` is surfaced for failed live commands
  - `UVSC_DBG_ENTER` treats "Target is in debug mode" as an idempotent success
  - failed connect/enter attempts close the UVSOCK handle before uninit
  - added packed `AMEM` memory read/write helpers
- Extended `tools\keil_live_write_probe.py`:
  - waits for UVSOCK readiness after launch
  - supports Keil Command Window commands through `UVSC_DBG_EXEC_CMD`
  - supports direct RAM writes through `UVSC_DBG_MEM_WRITE`
  - resolves `--symbol` from an AXF via `--axf`
  - verifies writes with `UVSC_DBG_MEM_READ`
  - keeps `UVSC_DBG_CALC_EXPRESSION` available as an experimental direct expression route
- Added `tools\keil_f401_debug_config_probe.py` to keep the F401 debug config from regressing back to ULINK/F103 settings.

### Verified

- `python -m py_compile src\core\keil\uvsock.py tools\keil_live_variable_write_probe.py tools\keil_live_write_probe.py tools\keil_f401_debug_config_probe.py`
  - PASS.
- `python tools\keil_live_variable_write_probe.py`
  - PASS.
- `python tools\keil_f401_debug_config_probe.py`
  - PASS.
- `python tools\keil_project_probe.py --project firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx`
  - PASS.
- `D:\Keil\Keil_v5\UV4\uVision.com -b D:\LoopMaster_v2.1\firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx -t "STM32F401CCU6 Variable Probe" -j0 -o D:\LoopMaster_v2.1\firmware\keil_f401_variable_probe\build.log`
  - PASS, `0 Error(s), 0 Warning(s)`.
- `python tools\keil_live_write_probe.py --keil-root D:\Keil --launch-uvsock --wait-seconds 25 --project firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx --target "STM32F401CCU6 Variable Probe" --port 4827 --write --prefer-memory --address 0x20000008 --value-type int32 --value 5000`
  - PASS, wrote and read back `debug_setpoint` by RAM address.
- `python tools\keil_live_write_probe.py --keil-root D:\Keil --port 4827 --write --prefer-memory --axf firmware\keil_f401_variable_probe\Objects\f401_variable_probe.axf --symbol debug_setpoint --value-type int32 --value 7777`
  - PASS, resolved `debug_setpoint` to `0x20000008`, wrote and read back.
- `python tools\keil_live_write_probe.py --keil-root D:\Keil --port 4827 --write --prefer-memory --axf firmware\keil_f401_variable_probe\Objects\f401_variable_probe.axf --symbol debug_setpoint --value-type int32 --value 5000`
  - PASS, restored the validation value.
- `python tools\keil_live_write_probe.py --keil-root D:\Keil --port 4827 --write --exec-command "debug_setpoint = 6000" --axf firmware\keil_f401_variable_probe\Objects\f401_variable_probe.axf --symbol debug_setpoint --value-type int32 --value 6000`
  - PASS, assigned by C variable name through Keil Command Window semantics and read back the RAM bytes.
- `python tools\keil_live_write_probe.py --keil-root D:\Keil --port 4827 --write --exec-command "debug_setpoint = 5000" --axf firmware\keil_f401_variable_probe\Objects\f401_variable_probe.axf --symbol debug_setpoint --value-type int32 --value 5000`
  - PASS, restored the validation value through the command path.
- `python tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil --attempt-existing --port 4827 --status`
  - PASS, UVSOCK connected and target status was readable.

### Notes

- This is the first verified Keil-controlled physical-target write in this
  branch. It uses Keil/uVision as debug master, ST-Link for the probe, and
  LoopMaster/UVSOCK as the outside control panel.
- Two real write paths are now verified:
  - variable-name command: `UVSC_DBG_EXEC_CMD("debug_setpoint = 6000")`
  - symbol-address memory write: AXF symbol -> `UVSC_DBG_MEM_WRITE` -> `UVSC_DBG_MEM_READ`
- `UVSC_DBG_CALC_EXPRESSION` still returns `UVSC_STATUS_COMMAND_ERROR` for
  `debug_setpoint = 5000` on the live target, so it stays secondary until the
  exact uVision expression context requirement is nailed down.
- The old failure `No ULINK2/ME Device found` was caused by missing reusable
  uVision debug options. The committed `.uvoptx` fixes the F401 smoke target.

### Next Target

- Wire the proven Keil write paths into Debug Workbench:
  - profile fields: Keil root, project, target, port, AXF
  - launch/connect status with Chinese UI text
  - prefer `UVSC_DBG_EXEC_CMD` for C variable-name writes when a symbol name is available
  - keep `UVSC_DBG_MEM_WRITE` as the explicit address/type fallback
  - variable write confirmation and JSONL audit reuse
  - type-aware packing for int/uint/float sizes
  - stop/start controls using the already exported UVSOCK halt/run functions
- Keep investigating expression and `UVSC_DBG_VARIABLE_SET` for richer watch
  semantics, but do not block the UI integration on them.

## Milestone 39 Update - Debug Workbench Keil Live Variable Write

### Goal

Move the verified Keil/F401 write path from probe scripts into the actual Debug
Workbench UI while keeping the default Keil attach path read-only.

### Completed

- Added reusable Keil live write service:
  - `src/core/keil/live_write.py`
  - request/result models for explicit live writes
  - direct RAM write path using AXF/ELF symbol resolution
  - Keil Command Window assignment fallback using `UVSC_DBG_EXEC_CMD`
  - old/new/readback bytes and scalar formatting for audit/UI diagnostics
  - DWARF struct-member resolution for expressions such as `AnglePID.Kp`
- Added `KeilUvSockBackendAdapter.write_live_variable(...)` as the explicit
  target-write entry point.
- Kept `read_only_session_snapshot()` behavior unchanged:
  - `can_write_variables=False`
  - no halt/run/write capability is silently enabled by attach
  - existing read-only adapter probes still pass
- Added a Debug Workbench `写变量` action button.
  - It is only enabled when Keil is attached/paused/running and the backend
    controls are ready.
  - It remains a separate explicit action instead of changing the meaning of
    read-only attach.
- Wired `MainWindow._write_keil_live_variable_from_workbench()`:
  - prompts for variable/expression and value
  - auto-discovers AXF from the loaded ELF or current Keil project target output
  - shows a second confirmation before writing
  - calls the Keil adapter live write method
  - appends JSONL audit records to `loopmaster_variable_writes.jsonl`
  - shows the last live write result in Debug Workbench diagnostics
- Added `tools/keil_live_write_service_probe.py`:
  - no-hardware fake-session coverage for scalar RAM writes
  - `SpeedLevel` uint16 write coverage
  - `AnglePID.Kp` struct-member float write coverage
  - memory failure -> command assignment fallback coverage
- Updated `tools/ui_debug_workbench_probe.py` so it treats `写变量` as a
  deliberate explicit Keil action while still blocking halt/run/step/sync in
  read-only contexts.

### Balance-Car Reference Notes

- Reference project:
  `D:\学习资料\平衡车\平衡车入门教程资料\程序源码\平衡车程序\00-平衡车测试程序\平衡车测试程序-V1.0\Project.uvprojx`
- Target: `Target 1`, device `STM32F103C8`.
- Expected AXF output:
  `D:\学习资料\平衡车\平衡车入门教程资料\程序源码\平衡车程序\00-平衡车测试程序\平衡车测试程序-V1.0\Objects\Project.axf`
- Current reference folder has no built AXF yet, so LoopMaster will currently
  fall back to Keil command assignment if the project is open in uVision Debug.
- Best first write/read demo variables:
  - write: `SpeedLevel`, `AngleAcc_Offset`, `AnglePID.Kp`, `AnglePID.Kd`
  - read/scope: `Angle`, `AveSpeed`, `PWML`, `PWMR`
- Avoid unsafe first demos:
  - `RunFlag=1`
  - direct PWM writes on a powered motor setup
  - persistent store array writes such as `Store_Data`

### Verified

- `python -m py_compile src/core/keil/live_write.py src/core/keil/backend.py src/ui/debug_workbench_tab.py src/ui/gui.py tools/keil_live_write_service_probe.py tools/ui_debug_workbench_probe.py`
  - PASS.
- `python tools/keil_live_write_service_probe.py`
  - PASS.
- `python tools/keil_backend_live_write_probe.py`
  - PASS.
- `python tools/debug_backend_adapter_probe.py`
  - PASS.
- `python tools/keil_backend_adapter_probe.py --keil-root D:\Keil`
  - PASS; uVision was not running, read-only discovery stayed safe.
- `python tools/debug_workbench_model_probe.py`
  - PASS.
- `python tools/debug_session_contract_probe.py`
  - PASS.
- `python tools/keil_live_variable_write_probe.py`
  - PASS.
- `python tools/keil_command_transaction_probe.py`
  - PASS.
- `python tools/debug_backend_registry_probe.py`
  - PASS.
- `python tools/ui_debug_source_provider_probe.py --output-dir tools/ui-debug-source-provider-live-write`
  - PASS.
- `python tools/ui_debug_workbench_probe.py --output-dir tools/ui-debug-workbench-live-write --width 1440 --height 900`
  - PASS.
- `python tools/keil_live_write_probe.py --keil-root D:\Keil --port 4827`
  - PASS preflight-only; UVSOCK DLL loads, uVision is not currently running.

### Notes

- This stage adds a real UI execution path. A successful actual write still
  depends on Keil/uVision being in Debug mode on a project/target, or on the
  next stage's automatic launch/config profile.
- For expressions with a built AXF, memory write is preferred because it gives
  deterministic bytes and readback. For expressions without AXF, the Keil
  command assignment fallback can still write variables visible to uVision.
- Struct-member writes are now possible when DWARF debug info exists. This is
  important for real PID work such as `AnglePID.Kp` / `SpeedPID.Target`.

### Next Target

- Build the Keil debug profile layer:
  - project path, target, port, AXF, Keil root
  - "launch uVision with UVSOCK" button/path
  - "build selected project" helper and readable failure output
  - reusable profile for the F401 probe and the F103 balance-car reference
- Add first variable preset panel for Keil:
  - safe write candidates from the balance-car project
  - read/scope-only candidates for angle/speed/PWM
  - range hints for PID-friendly tuning
- Add Keil halt/run implementation behind explicit controls and probes.

## Milestone 40 Update - Keil Debug Profile Build Launch Flow

### Goal

Turn the Keil workbench from a manual "Keil must already be open" panel into a
profile-driven flow that knows the selected project, target, AXF, build command,
and UVSOCK launch command.

### Completed

- Added `src/core/keil/profile.py`:
  - `KeilDebugProfile` captures Keil root, `.uvprojx`, target, UVSOCK port,
    AXF path/status, build command, and launch command.
  - `KeilBuildPlan` and `KeilBuildResult` wrap `uVision.com -b ... -t ... -j0`
    with readable diagnostics and build log paths.
  - `launch_keil_uvsock_from_profile()` uses the profile's `UV4.exe ... -s`
    launch plan and refuses to launch when the profile is incomplete.
- Extended `KeilUvSockBackendAdapter`:
  - `debug_profile(...)`
  - `build_project(...)`
  - `launch_uvsock(...)`
  - backend diagnostics now include profile/build/launch readiness rows.
- Added Debug Workbench actions:
  - `构建`
  - `启动Keil`
  - both are explicit profile actions enabled only for a Keil backend with a
    loaded project and ready controls.
- Wired `MainWindow` profile flow:
  - build confirmation dialog
  - launch confirmation dialog
  - build/launch result diagnostics
  - AXF discovery now prefers the current profile's target output.
- Tightened Keil status wording:
  - launching uVision now says it only proves the process was started; the user
    must still connect to verify UVSOCK/debug state.
  - read-only attach status now says "一次性快照" instead of implying a kept
    connection.
  - command-window variable writes now distinguish "已回读" from "已提交但未独立回读".
- Hardened command fallback writes:
  - if a resolved address exists, command assignment must read back matching
    bytes.
  - if no AXF/address is available, the result is still auditable but marked as
    not independently read back.
- Extended probes:
  - `tools/keil_debug_profile_probe.py`
  - `tools/ui_debug_workbench_probe.py`
  - `tools/debug_workbench_model_probe.py`
  - `tools/keil_live_write_service_probe.py`

### Verified

- `python -m py_compile src/core/keil/profile.py src/core/keil/live_write.py src/core/keil/backend.py src/core/debug_workbench.py src/ui/debug_workbench_tab.py src/ui/gui.py tools/keil_debug_profile_probe.py tools/keil_live_write_service_probe.py tools/debug_workbench_model_probe.py tools/ui_debug_workbench_probe.py`
  - PASS.
- `python tools/keil_debug_profile_probe.py`
  - PASS.
- `python tools/keil_live_write_service_probe.py`
  - PASS.
- `python tools/debug_workbench_model_probe.py`
  - PASS.
- `python tools/debug_backend_adapter_probe.py`
  - PASS.
- `python tools/keil_backend_adapter_probe.py --keil-root D:\Keil --project firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx --target "STM32F401CCU6 Variable Probe"`
  - PASS; uVision was not running, discovery stayed in safe preflight mode.
- `python tools/keil_command_transaction_probe.py`
  - PASS.
- `python tools/keil_backend_live_write_probe.py`
  - PASS.
- `python tools/ui_debug_workbench_probe.py --output-dir tools/ui-debug-workbench-profile --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-profile\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-profile\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-profile\03_debug_workbench_narrow.png`
- `python tools/ui_debug_source_provider_probe.py --output-dir tools/ui-debug-source-provider-profile`
  - PASS.
- `python tools/debug_session_contract_probe.py`
  - PASS.

### Notes

- This stage does not create a new public app version or Release package. It is
  a debugger-integration stage on `main`.
- Current UI path is now:
  - open Keil project
  - `构建`
  - `启动Keil`
  - `连接`
  - `写变量`
- `启动Keil` still starts uVision and UVSOCK but does not yet wait for the port
  to become connectable or enter Debug automatically. That is the next stage.
- Keil live writes remain real and verified from prior F401 smoke testing, but
  the UI still depends on Keil/uVision being in a valid debug session.

### Next Target

- Add an automatic Keil debug transaction:
  - build selected project
  - launch uVision with UVSOCK
  - wait for UVSOCK readiness
  - connect/read status
  - run a guarded `debug_setpoint` write/readback smoke flow on the F401 probe
    project when the user selects that profile.
- Implement real Keil Halt/Run actions:
  - wrap `UVSC_DBG_STOP_EXECUTION`
  - wrap `UVSC_DBG_START_EXECUTION`
  - read back target state after each action
  - keep explicit confirmations and audit rows.
- Add a first Keil variable preset panel:
  - F401 probe preset: `debug_setpoint`
  - balance-car safe writes: `SpeedLevel`, `AngleAcc_Offset`, `AnglePID.Kp`,
    `AnglePID.Kd`
  - read/scope-only presets: `Angle`, `AveSpeed`, `PWML`, `PWMR`.

## Milestone 41 Update - Keil Runtime Control And Variable Presets

### Goal

Move the Keil path past build/launch scaffolding by adding real opt-in
Halt/Run runtime controls and making the first live-write defaults understand a
real user MCU project instead of always defaulting to the F401 probe.

### Completed

- Added Keil UVSOCK runtime control wrappers:
  - `KeilUvscLiveSession.halt_target()`
  - `KeilUvscLiveSession.run_target()`
  - both call the UVSOCK start/stop execution exports and then read back
    `UVSC_DBG_STATUS` to confirm target state.
- Added adapter-level runtime control:
  - `KeilUvSockBackendAdapter.halt_target(...)`
  - `KeilUvSockBackendAdapter.run_target(...)`
  - each opens an explicit live UVSOCK session, performs the requested control,
    then refreshes a read-only snapshot and checks the returned running/paused
    state.
- Wired Debug Workbench actions:
  - `暂停` is enabled for Keil when controls are ready and the target snapshot
    says running.
  - `运行` is enabled for Keil when controls are ready and the target snapshot
    says paused.
  - both actions still show explicit confirmation before touching the target.
- Added runtime diagnostics rows for the UI:
  - action
  - success/failure
  - returned target state
  - UVSOCK status/error when present.
- Added `src/core/keil/presets.py`:
  - `KeilVariablePreset`
  - `KeilVariablePresetProfile`
  - `keil_variable_preset_profile(...)`
  - `keil_live_write_seed(...)`
  - `keil_live_write_prompt_hint(...)`
- Added project-aware live-write defaults:
  - F401 probe defaults to `debug_setpoint` / `6000`.
  - the balance-car F103 project defaults to `SpeedLevel` / `5`.
  - unknown projects keep the old safe fallback `debug_setpoint` / `6000`.
- Added Keil variable preset diagnostics in the Debug Workbench:
  - `变量预设`
  - `推荐写入`
  - `推荐示波`
- Added `tools/keil_variable_presets_probe.py`.
- Updated `tools/ui_debug_workbench_probe.py` so the probe now protects the new
  intended rule:
  - read-only attach remains safe for step/sync/run when the target is already
    running.
  - Keil `暂停` is allowed as a separate explicit runtime-control action and
    must mention UVSOCK/confirmation in the tooltip.

### Balance-Car Reference Findings

Reference project:

`D:\学习资料\平衡车\平衡车入门教程资料\程序源码\平衡车程序\00-平衡车测试程序\平衡车测试程序-V1.0\Project.uvprojx`

- Target: `Target 1`.
- Device: `STM32F103C8`.
- Toolchain: ARMCC 5 / Keil MDK.
- Output AXF: `Objects\Project.axf`.
- Current output state: AXF is not generated yet; sources and headers referenced
  by the project are present.
- Debug configuration summary from `.uvoptx`:
  - ST-Link target debug selected.
  - SWD / ARM CoreSight path.
  - debug clock around 10 MHz.
  - flash-before-debug enabled.
  - flash algorithm appears to be `STM32F10x_128.FLM`; this should be shown as
    "needs real Keil confirmation" because the Device is F103C8.
- First write candidates:
  - `SpeedLevel` (`uint16_t`) for scalar write/readback.
  - `AngleAcc_Offset` (`float`) for calibration-style write.
  - `AnglePID.Kp`, `AnglePID.Kd`, `SpeedPID.Kp` for later PID tuning.
- First read/scope candidates:
  - `Angle`
  - `AngleAcc`
  - `AngleAcc_Filter`
  - `AveSpeed`
  - `DifSpeed`
  - `PWML`
  - `PWMR`
  - `AnglePID.Out`
  - `SpeedPID.Out`

### Verified

- `python -m py_compile src/core/keil/presets.py src/core/keil/__init__.py src/core/keil/uvsock.py src/core/keil/backend.py src/ui/gui.py src/ui/debug_workbench_tab.py tools/keil_variable_presets_probe.py tools/ui_debug_workbench_probe.py tools/keil_live_variable_write_probe.py tools/debug_backend_adapter_probe.py`
  - PASS.
- `python tools/keil_variable_presets_probe.py`
  - PASS.
- `python tools/keil_debug_profile_probe.py`
  - PASS.
- `python tools/ui_debug_workbench_probe.py --output-dir tools/ui-debug-workbench-runtime-control --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-runtime-control\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-runtime-control\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-runtime-control\03_debug_workbench_narrow.png`
- `python tools/debug_backend_adapter_probe.py`
  - PASS.
- `python tools/keil_live_variable_write_probe.py`
  - PASS.
- `python tools/keil_command_transaction_probe.py`
  - PASS.
- `python tools/keil_backend_adapter_probe.py --keil-root D:\Keil --project firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx --target "STM32F401CCU6 Variable Probe"`
  - PASS; uVision was not running, discovery stayed in safe preflight mode.
- `python tools/keil_backend_live_write_probe.py`
  - PASS.
- `python tools/debug_workbench_model_probe.py`
  - PASS.
- `python tools/ui_debug_source_provider_probe.py --output-dir tools/ui-debug-source-provider-runtime-control`
  - PASS.

### Notes

- This milestone still does not force-start or control the user's connected
  hardware automatically. Halt/Run are now implemented, but only behind explicit
  UI actions and confirmation.
- The balance-car project is now recognized by source features rather than a
  hard-coded absolute path, so moving the folder should still keep the same
  variable presets.
- Keil live write remains the real function priority. This stage improves the
  first write target selection so the next automatic transaction can use
  `SpeedLevel` on the F103 balance-car project and `debug_setpoint` on the F401
  probe.

### Next Target

- Add an automatic Keil debug transaction:
  - parse `.uvprojx/.uvoptx` profile and show device/debug-adapter warnings.
  - if AXF is missing, run `uVision.com -b ... -t ... -j0`.
  - launch `UV4.exe ... -s 4827 -t ...`.
  - wait for UVSOCK readiness instead of asking the user to manually time the
    connect click.
  - connect and read target state.
  - perform a guarded write/readback smoke:
    - F401 probe: `debug_setpoint`.
    - balance-car: `SpeedLevel`.
  - log old value, new value, readback, target state, AXF, project and target.
- Add a real variable preset panel in the Debug Workbench:
  - write presets
  - scope presets
  - risk hints
  - one-click fill into the existing write dialog.

## Milestone 42 Update - Keil Auto Debug Transaction Skeleton

### Goal

Start turning the manual Keil sequence into one explicit audited transaction:
profile, build when needed, launch UVSOCK, wait for connection, then run a
guarded live-write/readback smoke variable.

### Completed

- Added `src/core/keil/auto_debug.py`:
  - `KeilAutoDebugRequest`
  - `KeilAutoDebugStep`
  - `KeilAutoDebugResult`
  - `run_keil_auto_debug_transaction(...)`
- The transaction service uses the existing backend adapter methods instead of
  creating a parallel Keil implementation:
  - `debug_profile(...)`
  - `build_project(...)`
  - `launch_uvsock(...)`
  - `read_only_session_snapshot(...)`
  - `write_live_variable(...)`
- Transaction behavior:
  - creates a Keil debug profile
  - builds only when AXF is missing and `build_if_missing=True`
  - launches uVision/UVSOCK when requested
  - polls the read-only session snapshot until UVSOCK connection is established
  - chooses the smoke variable from the project-aware preset layer
    - F401 probe: `debug_setpoint` / `6000`
    - balance-car F103: `SpeedLevel` / `5`
  - writes through the existing Keil live-write service, preserving AXF/RAM
    readback and command fallback behavior.
- Added `自动调试` to the Debug Workbench action bar.
- Wired `MainWindow._run_keil_auto_debug_from_workbench()`:
  - explicit confirmation
  - runs the auto transaction
  - applies returned read-only backend snapshot
  - records live write audit when a smoke write is attempted
  - refreshes diagnostics, command preview and hero state.
- Added auto-debug diagnostics rows to the Debug Workbench:
  - overall transaction result
  - project/target/AXF
  - per-step success/failure
  - smoke write target/readback/error.
- Added `tools/keil_auto_debug_transaction_probe.py`:
  - fake backend with no real Keil launch and no hardware access
  - verifies AXF-present path skips build
  - verifies AXF-missing path builds first
  - verifies connection polling
  - verifies connection timeout failure.
- Updated UI screenshot probe so `自动调试` follows the same explicit
  Keil-profile action rules as build and launch.
- Extended the UI screenshot probe to click `自动调试` with a fake backend and
  patched confirmation dialogs, verifying fake build/launch/connect/write
  without starting uVision.

### Verified

- `python -m py_compile src/core/keil/auto_debug.py src/core/keil/__init__.py src/ui/gui.py src/ui/debug_workbench_tab.py tools/keil_auto_debug_transaction_probe.py tools/ui_debug_workbench_probe.py`
  - PASS.
- `python tools/keil_auto_debug_transaction_probe.py`
  - PASS.
- `python tools/ui_debug_workbench_probe.py --output-dir tools/ui-debug-workbench-auto-debug-click --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-auto-debug-click\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-auto-debug-click\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-auto-debug-click\03_debug_workbench_narrow.png`
- `python tools/keil_variable_presets_probe.py`
  - PASS.
- `python tools/keil_debug_profile_probe.py`
  - PASS.
- `python tools/debug_backend_adapter_probe.py`
  - PASS.
- `python tools/keil_live_variable_write_probe.py`
  - PASS.
- `python tools/keil_backend_live_write_probe.py`
  - PASS.
- `python tools/debug_workbench_model_probe.py`
  - PASS.
- `python tools/debug_session_contract_probe.py`
  - PASS.

### Notes

- This stage adds the real orchestration layer and UI entry, but the verification
  uses a fake backend to avoid starting Keil or touching the connected ST-Link
  without a dedicated hardware smoke run.
- The auto transaction is intentionally explicit and confirmed. It can start
  Keil and write RAM variables, so it is not run automatically on page open.
- The next hardware-facing run should use the F401 probe first because its AXF
  already exists and the write target is a purpose-built RAM variable.

### Next Target

- Run a controlled hardware smoke on the F401 probe:
  - launch Keil/UVSOCK
  - wait for connection
  - write `debug_setpoint`
  - read back and log audit.
- Add `.uvoptx` debug adapter summary parsing to the profile diagnostics:
  - ST-Link/SWD
  - debug clock
  - flash algorithm
  - SVD/device hints
  - warnings such as F103C8 plus 128K flash algorithm.
- Add the real variable preset panel:
  - visible write/scope preset lists
  - one-click fill into the write dialog
  - risk hints for `RunFlag`, PID gains and motor-output-adjacent variables.

## Milestone 43 Update - Keil Debug Option Diagnostics

### Goal

Expose real Keil `.uvoptx` debug-adapter configuration in LoopMaster so the
profile can show whether the selected project is using ST-Link/SWD, which debug
clock and flash algorithm are configured, and whether Keil already marked flash
configuration as suspicious.

### Completed

- Added `src/core/keil/options.py`:
  - `KeilDebugOptionsSummary`
  - `parse_keil_debug_options(...)`
- The parser reads `.uvoptx` next to the `.uvprojx` and combines it with
  `.uvprojx` target metadata.
- Extracted diagnostics:
  - device/vendor/pack/SVD
  - target debug vs simulator selection
  - monitor DLL
  - ST-Link registry key/options
  - protocol (`2` -> SWD, `1` -> JTAG)
  - debug clock
  - flash algorithm
  - flash range
  - RAM range
  - Keil `InvalidFlash=1` marker.
- Added warnings:
  - generic `InvalidFlash=1`
  - F103C8 projects that appear to use a 128KB flash algorithm/range.
- Integrated debug options into `KeilDebugProfile.diagnostic_rows()`, so the
  Debug Workbench automatically shows these rows during discovery/profile/attach.
- Exported the parser through `src/core/keil/__init__.py`.
- Added `tools/keil_debug_options_probe.py`.

### Verified

- `python -m py_compile src/core/keil/options.py src/core/keil/profile.py src/core/keil/__init__.py tools/keil_debug_options_probe.py tools/keil_debug_profile_probe.py tools/ui_debug_workbench_probe.py`
  - PASS.
- `python tools/keil_debug_options_probe.py`
  - PASS.
- `python tools/keil_debug_profile_probe.py`
  - PASS.
- `python tools/ui_debug_workbench_probe.py --output-dir tools/ui-debug-workbench-debug-options --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-debug-options\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-debug-options\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-debug-options\03_debug_workbench_narrow.png`
- `python tools/keil_auto_debug_transaction_probe.py`
  - PASS.
- `python tools/debug_backend_adapter_probe.py`
  - PASS.
- `python tools/keil_backend_adapter_probe.py --keil-root D:\Keil --project firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx --target "STM32F401CCU6 Variable Probe"`
  - PASS; output now includes debug option diagnostics such as ST-Link, SWD,
    10 MHz, flash algorithm and SVD.
- `python tools/debug_workbench_model_probe.py`
  - PASS.

### Notes

- This is still no-hardware parsing. It does not confirm the ST-Link probe is
  physically connected; it tells the user what the Keil project is configured to
  use.
- Both the F401 probe project and the F103 balance-car reference show
  `InvalidFlash=1` in Keil metadata, so LoopMaster now surfaces that instead of
  hiding it.
- The balance-car reference warning is intentionally conservative: F103C8 plus
  128KB algorithm may be common in hobby projects, but it still deserves a
  visible "confirm in Keil" note before automated flashing/debug launch.

### Next Target

- Add a visible variable preset panel in Debug Workbench:
  - write presets and scope presets
  - fill selected preset into the write dialog
  - show risk hints from the preset metadata.
- Add a controlled hardware-smoke command for F401 auto-debug:
  - explicit CLI/tool invocation
  - no UI modal dependency
  - log result and leave uVision/process state clear.

## Milestone 44 Update - Debug Workbench Variable Preset Panel

### Goal

Move project-aware variable presets out of diagnostics-only text and into a
usable Debug Workbench panel so the user can directly pick recommended write or
scope variables.

### Completed

- Added a `变量预设` panel to the Debug Workbench navigation/sidebar.
- Added `DebugWorkbenchTab.set_variable_presets(...)`.
- Added `DebugWorkbenchTab.variablePresetWriteRequested(expression, value)`.
- The preset table shows:
  - expression
  - default value
  - type
  - label/purpose
  - tooltip with write/read-only status.
- `写入预设` opens the existing Keil live-write flow with the selected preset's
  expression/default value already filled in.
- Double-clicking a writable preset triggers the same write-flow signal.
- Read/scope-only presets are visible but greyed and do not emit write requests.
- MainWindow now refreshes the preset panel when:
  - the Debug Workbench is initialized
  - project/target summary changes
  - backend changes
  - diagnostics refresh.
- The existing Keil write confirmation, AXF/RAM checks, command fallback and
  JSONL audit path are reused; the preset panel does not bypass safety.
- Updated `tools/ui_debug_workbench_probe.py`:
  - synthetic fixture now includes `debug_setpoint`
  - asserts the preset table contains `debug_setpoint`
  - clicks `写入预设` with patched text/confirmation dialogs
  - verifies the fake backend receives the write
  - keeps the existing fake `自动调试` click coverage.

### Verified

- `python -m py_compile src/ui/debug_workbench_tab.py src/ui/gui.py tools/ui_debug_workbench_probe.py`
  - PASS.
- `python tools/ui_debug_workbench_probe.py --output-dir tools/ui-debug-workbench-variable-presets --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-variable-presets\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-variable-presets\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-variable-presets\03_debug_workbench_narrow.png`
- `python tools/keil_variable_presets_probe.py`
  - PASS.
- `python tools/keil_debug_options_probe.py`
  - PASS.
- `python tools/keil_auto_debug_transaction_probe.py`
  - PASS.
- `python tools/debug_backend_adapter_probe.py`
  - PASS.
- `python tools/debug_workbench_model_probe.py`
  - PASS.
- `python tools/keil_debug_profile_probe.py`
  - PASS.

### Notes

- This is the first visible preset panel. It does not yet add selected scope
  presets to the oscilloscope collector; that remains a separate data-source
  binding task.
- The panel is intentionally compact to avoid crowding the source tree and
  breakpoint list. If it becomes cramped on smaller screens, the next UI polish
  should move presets into a right-side tab or collapsible section.

### Next Target

- Add hardware-facing F401 auto-debug smoke tooling:
  - a CLI/probe that can run the auto transaction with explicit flags
  - clear process cleanup/reporting
  - no hidden hardware side effects.
- Then bind scope presets to the oscilloscope/watch path:
  - add selected read-only variables to a Keil/UVSOCK watch list
  - show realistic sampling-rate warnings for Keil bridge mode.

## Milestone 45 Update - Explicit F401 Auto-Debug Smoke CLI

### Goal

Create a reproducible hardware-smoke entry point for the F401 Keil probe while
keeping the default behavior dry-run only.

### Completed

- Added `tools/keil_auto_debug_smoke.py`.
- Default mode prints a dry-run plan and does not start Keil, connect UVSOCK, or
  write target memory.
- `--execute` runs the real auto-debug transaction:
  - profile
  - build if AXF is missing
  - launch Keil/UVSOCK unless `--no-launch`
  - wait for connection
  - write `debug_setpoint` unless `--no-write`
- Added options:
  - `--keil-root`
  - `--port`
  - `--project`
  - `--target`
  - `--expression`
  - `--write-value`
  - `--wait-seconds`
  - `--poll-interval`
  - `--no-build`
  - `--no-launch`
  - `--no-write`
  - `--json`
- Updated `firmware/keil_f401_variable_probe/README.md` with dry-run and
  execute examples.

### Verified

- `python -m py_compile tools/keil_auto_debug_smoke.py src/core/keil/auto_debug.py`
  - PASS.
- `python tools/keil_auto_debug_smoke.py --json`
  - PASS; dry-run output includes:
    - F401 project path
    - AXF path/status
    - build command
    - launch command
    - ST-Link/SWD/10 MHz debug option diagnostics
    - planned `debug_setpoint = 5000` write.
- `python tools/keil_auto_debug_transaction_probe.py`
  - PASS.

### Notes

- `--execute` was not run in this milestone. The tool exists so the next
  hardware-facing smoke can be explicit, reproducible and logged.
- The existing `tools/keil_live_write_probe.py` remains useful for low-level
  command/memory write paths. The new smoke runner tests the higher-level
  LoopMaster transaction flow.

### Next Target

- Run the F401 smoke with `--execute` when the board/ST-Link/uVision state is
  intentionally ready.
- Add Keil watch/read sampling path for preset scope variables, with clear
  sampling-rate warnings.

## Milestone 46 Update - Keil Watch Read Path And Scope Binding

### Goal

Move Keil integration beyond project/profile display and live-write prompts by
adding a real low-frequency UVSOCK expression read path that can feed the
existing oscilloscope UI.

### Completed

- Added `src/core/keil/watch.py`.
  - `KeilUvSockWatchBackend` owns a persistent UVSOCK live session.
  - Exposes the existing collector protocol: `is_connected` and `read_batch`.
  - Reads expressions through `KeilUvscLiveSession.evaluate_expression()`.
  - Parses common Keil numeric output including `name = 12.3`, decimal and hex.
  - Returns `NaN` for non-numeric/error reads so plotting can continue.
  - Adds Keil-specific sample-rate guidance:
    - recommended 20 Hz
    - hard UI clamp 50 Hz
    - explicit warning when users request high-rate Watch sampling.
- Extended `KeilUvSockBackendAdapter`.
  - `create_watch_transport(...)`
  - `read_watch_once(...)`
- Extended Debug Workbench variable presets.
  - Added `加入示波` button.
  - Writable presets still open the existing guarded write flow.
  - Scope/read-only presets can now be added directly to the oscilloscope.
  - Double-click behavior:
    - writable row: write flow
    - read-only/scope row: add to Watch scope.
- Bound Keil Watch to the main oscilloscope.
  - Scope source can switch between normal SWD memory reads and Keil Watch.
  - Keil Watch variables do not require ELF import or pyOCD/SWD connection.
  - Address column shows `Keil`.
  - Idle value refresh and background sampling both use the active scope source.
  - Idle UVSOCK connection attempts are throttled so a closed uVision session
    does not cause UI churn.
  - Keil Watch mode is saved/restored in `loopmaster.json`.
  - CSV export works for Keil Watch variable lists and captured Watch data.
- Expanded balance-car scope presets using the user-provided F103 reference:
  - `Angle`, `AngleAcc`, `AngleAcc_Filter`, `AngleDelta`
  - `AveSpeed`, `DifSpeed`, `PWML`, `PWMR`
  - `AnglePID.Target`, `AnglePID.Actual`, `AnglePID.Out`
  - `AnglePID.POut`, `AnglePID.IOut`, `AnglePID.DOut`
  - `SpeedPID.Target`, `SpeedPID.Actual`, `SpeedPID.Out`
  - `TurnPID.Out`

### Verified

- `python -m py_compile src/core/keil/watch.py src/core/keil/backend.py src/core/keil/__init__.py src/ui/debug_workbench_tab.py src/ui/gui.py tools/keil_watch_read_probe.py`
  - PASS.
- `python tools/keil_watch_read_probe.py`
  - PASS.
- `python tools/keil_variable_presets_probe.py`
  - PASS.
- `python tools/ui_keil_watch_scope_probe.py`
  - PASS.
- `python tools/ui_debug_workbench_probe.py --output-dir tools/ui-debug-workbench-keil-watch --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-keil-watch\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-keil-watch\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-keil-watch\03_debug_workbench_narrow.png`
- `python tools/keil_debug_options_probe.py`
  - PASS.
- `python tools/keil_auto_debug_transaction_probe.py`
  - PASS.
- `python tools/debug_workbench_model_probe.py`
  - PASS.
- `python tools/keil_debug_profile_probe.py`
  - PASS.
- `python tools/debug_session_contract_probe.py`
  - PASS.
- `python tools/debug_backend_registry_probe.py`
  - PASS.
- `python tools/debug_backend_adapter_probe.py`
  - PASS.

### Notes

- This is intentionally a low-frequency Keil Watch path. It is suitable for
  PID tuning trends and live variable panels, not for high-bandwidth waveform
  capture. High-frequency data should continue through SWD memory reads or
  serial/VOFA-style streams.
- The user-provided balance-car reference is now useful in the UI without
  importing its AXF first: selecting a preset can add expressions directly to
  Keil Watch. Real reads still require a live uVision Debug/UVSOCK session.
- UVSOCK lifecycle may be global inside Keil's DLL. Watch sessions are
  disconnectable, and future write/runtime operations should pause or serialize
  against Watch polling before deeper hardware testing.

### Next Target

- Run a deliberate real F401 hardware smoke through the existing
  `tools/keil_auto_debug_smoke.py --execute` path when ready.
- Add a Keil debug profile picker:
  - project path
  - target
  - AXF status
  - UVSOCK port
  - ST-Link option diagnostics
  - saved per-project defaults.
- Start the modern debugger UI layer:
  - real breakpoint set/clear/sync against Keil
  - source gutter actions modeled after VS Code/CubeIDE
  - Watch expressions grouped into reusable tuning panels.
- Keep OpenOCD/GDB and pyOCD compatibility in the same transport shape:
  status snapshot, runtime control, variable write, low-frequency watch, and
  high-frequency sampling as separate capabilities.

## Milestone 47 Update - Keil Debug Profile Store And Runtime Config

### Goal

Make Keil projects reusable as named debug profiles instead of relying on the
current scattered UI state. Also expose Keil root/UVSOCK port configuration
without auto-starting or auto-connecting hardware.

### Completed

- Added `src/core/keil/profile_store.py`.
  - `KeilDebugProfileRecord`
  - `KeilDebugProfileStore`
  - JSON load/save helpers
  - conversion from/to existing `KeilDebugProfile`
  - default profile tracking and upsert replacement by project/target key.
- Added profile persistence file:
  - `loopmaster_keil_profiles.json`
  - keeps up to 16 recent Keil debug profiles.
- Added Debug Workbench profile controls in the `后端诊断` header area:
  - `Keil配置`
  - `保存档案`
  - `载入`
- Added Keil runtime config flow.
  - Select Keil root directory.
  - Edit UVSOCK port.
  - Rebuild debug backend registry and session controller after changes.
  - Disconnect existing Keil Watch session before replacing root/port.
  - Preserve current project/target in a safe disconnected state.
  - No automatic discover/connect/build/download is triggered.
- Added config persistence:
  - new `debug_keil.root`
  - new `debug_keil.uvsock_port`
  - old top-level `keil_root` remains written for compatibility.
- Added profile diagnostics:
  - profile count
  - default profile
  - profile project
  - profile target
  - profile port.

### Verified

- `python -m py_compile src/core/keil/profile_store.py src/core/keil/__init__.py src/ui/debug_workbench_tab.py src/ui/gui.py tools/keil_profile_store_probe.py tools/ui_keil_profile_store_probe.py`
  - PASS.
- `python tools/keil_profile_store_probe.py`
  - PASS.
- `python tools/ui_keil_profile_store_probe.py`
  - PASS.
- `python tools/keil_debug_profile_probe.py`
  - PASS.
- `python tools/keil_debug_options_probe.py`
  - PASS.
- `python tools/ui_keil_watch_scope_probe.py`
  - PASS.
- `python tools/keil_watch_read_probe.py`
  - PASS.
- `python tools/debug_backend_adapter_probe.py`
  - PASS.
- `python tools/ui_debug_workbench_probe.py --output-dir tools/ui-debug-workbench-profile-store-final --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-profile-store-final\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-profile-store-final\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-profile-store-final\03_debug_workbench_narrow.png`

### Notes

- The first UI version deliberately keeps profile controls compact in the
  diagnostics header, following the existing left-panel information hierarchy.
- Applying Keil config only refreshes local backend state and diagnostics. It
  does not launch Keil, connect ST-Link, download firmware, halt/run, or write
  variables.
- AXF path still uses the existing automatic project/target derivation. Manual
  AXF override is supported in core profile creation but not yet exposed in the
  UI.

### Next Target

- Run or dry-run the saved profile against the F401 probe flow from UI/CLI:
  - profile selected
  - AXF exists/build status clear
  - launch command preview clear
  - explicit execute path remains guarded.
- Start real Keil breakpoint sync:
  - local gutter breakpoint intent
  - UVSOCK command transaction execution
  - remote verification snapshot.
- Add reusable Watch groups/tuning panels for PID workflows.

## Milestone 48 Update - Profile-Driven Keil Auto-Debug Smoke

### Goal

Connect saved Keil debug profiles to the existing auto-debug smoke flow, so a
project/target/root/port saved once by the UI can drive repeatable smoke runs.

### Completed

- Extended `tools/keil_auto_debug_smoke.py`.
  - Added `--use-profile`.
  - Added `--profile-store`.
  - Added `--profile` selector by name/key/project substring.
  - Dry-run output now reports:
    - profile source
    - profile name
    - Keil root
    - UVSOCK port.
  - Existing default F401 dry-run behavior remains unchanged unless
    `--use-profile` is explicitly provided.
- Added `tools/keil_auto_debug_smoke_profile_probe.py`.
  - Creates a temporary profile store.
  - Runs the smoke CLI in dry-run JSON mode.
  - Verifies project/target/port come from the saved profile.
- Updated UI auto-debug behavior.
  - If no Keil project is currently open, `自动调试` attempts to load the
    default saved Keil debug profile.
  - If no saved profile exists, it keeps the explicit prompt to open a project.
  - Existing behavior is unchanged when a project is already open.

### Verified

- `python -m py_compile src/ui/gui.py tools/ui_keil_profile_store_probe.py tools/keil_auto_debug_smoke.py tools/keil_auto_debug_smoke_profile_probe.py`
  - PASS.
- `python tools/keil_auto_debug_smoke.py --json`
  - PASS; default F401 dry-run still works.
- `python tools/keil_auto_debug_smoke_profile_probe.py`
  - PASS.
- `python tools/ui_keil_profile_store_probe.py`
  - PASS.
- `python tools/keil_auto_debug_transaction_probe.py`
  - PASS.
- `python tools/ui_debug_workbench_probe.py --output-dir tools/ui-debug-workbench-profile-smoke --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-profile-smoke\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-profile-smoke\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-profile-smoke\03_debug_workbench_narrow.png`

### Notes

- This still does not run hardware unless `--execute` is explicitly passed.
- Saved profiles now close the loop for repeatable smoke planning:
  save profile in UI -> run CLI dry-run/execute from that profile -> inspect
  same project/target/port diagnostics.

### Next Target

- Add a guarded UI dry-run/smoke preview around the saved profile.
- Start real Keil breakpoint sync execution and remote verification.
- Keep all hardware-changing actions behind explicit confirmation.

## Milestone 49 Update - Guarded Keil Breakpoint Sync UI Execution

### Goal

Move the Debug Workbench breakpoint path from dry-run preview toward a real
Keil action: users can add local source breakpoints, press `同步断点`, confirm
the diff, and send guarded Keil Debug Commands through UVSOCK.

### Completed

- Added `src/core/keil/breakpoint_sync.py`.
  - Builds breakpoint sync requests from current UI state.
  - Executes commands through a UVSOCK command session.
  - Records command results, diagnostics, remote snapshot hints, and audit data.
- Wired Keil breakpoint sync into the real backend adapter.
  - `KeilUvSockBackendAdapter.sync_breakpoints(...)` connects to an existing
    debug session and sends command-window breakpoint commands.
  - Connected Keil snapshots now expose explicit breakpoint sync capability.
- Wired the Debug Workbench UI.
  - Added the `同步断点` action button.
  - Main window now handles `sync_breakpoints`.
  - The confirmation dialog shows add/remove/enable/disable/condition/noop
    counts and whether the mode is full diff or push-local.
  - The breakpoint table is marked verified/failed after execution.
  - Diagnostics now include `断点同步`, `断点命令`, `断点同步模式`, snapshot id, and
    error rows.
  - Audit lines are appended to the existing JSONL audit file.
- Made the first real command mode safer.
  - `BreakSet \path\file.c\line` is emitted for ordinary local breakpoint adds.
  - Removing, enabling, disabling, and condition updates require a Keil remote
    breakpoint id; without that id the operation is rejected instead of guessed.
  - When remote breakpoint enumeration is incomplete, the UI uses push-local
    mode and does not delete remote breakpoints.
- Fixed `tools/keil_auto_debug_smoke.py` dry-run output so passing a project
  without `--target` reports the selected Keil project default target instead
  of the F401 fixture target.
- Smoke dry-run now uses the current Keil variable preset as the default write
  seed when `--expression`/`--write-value` are omitted.
  - F401 fixture stays on `debug_setpoint=6000`.
  - Balance-car project defaults to `SpeedLevel=5`.

### Verified

- `python -m py_compile src/core/debug_workbench.py src/core/keil/__init__.py src/core/keil/backend.py src/core/keil/breakpoint_sync.py src/core/keil/commands.py src/ui/debug_workbench_tab.py src/ui/gui.py tools/debug_backend_adapter_probe.py tools/keil_auto_debug_smoke.py tools/keil_breakpoint_sync_probe.py tools/ui_debug_workbench_probe.py tools/ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools/keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools/ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools/ui_debug_workbench_probe.py --output-dir tools/ui-debug-workbench-breakpoint-sync --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-breakpoint-sync\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-breakpoint-sync\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-breakpoint-sync\03_debug_workbench_narrow.png`
- `python tools/debug_backend_adapter_probe.py`
  - PASS.
- `python tools/keil_command_transaction_probe.py`
  - PASS.
- `python tools/debug_transaction_shell_probe.py`
  - PASS.
- `python tools/keil_auto_debug_transaction_probe.py`
  - PASS.
- `python tools/keil_auto_debug_smoke.py --json`
  - PASS.
- `python tools/keil_auto_debug_smoke_profile_probe.py`
  - PASS.
- `python tools/keil_watch_read_probe.py`
  - PASS.
- `python tools/keil_variable_presets_probe.py`
  - PASS.
- Balance-car reference project dry-run:
  - project:
    `D:\学习资料\平衡车\平衡车入门教程资料\程序源码\平衡车程序\00-平衡车测试程序\平衡车测试程序-V1.0\Project.uvprojx`
  - PASS; Keil profile resolves `Target 1`, STM32F103C8, ST-Link/SWD, build and
    launch commands.
  - PASS; default dry-run write seed resolves `SpeedLevel=5`.
  - Preset profile resolves `balance_car_f103` with PID/scope presets such as
    `Angle`, `AveSpeed`, `PWML`, `PWMR`, `AnglePID.*`, `SpeedPID.*`.

### Notes

- This milestone intentionally supports real local breakpoint add commands
  first. Full remote breakpoint diff still needs Keil breakpoint enumeration or
  a reliable `BreakList` parser so deletes/enable/disable/condition updates can
  use actual Keil breakpoint ids.
- `BreakSet` command generation is based on Keil Debug Command source-line
  expressions. It still needs live uVision verification before enabling more
  complex condition/update workflows.
- Hardware-changing operations remain behind explicit confirmation.

### Next Target

- Verify `BreakSet` live against the user's Keil/ST-Link/F401 setup and record
  the exact Keil command-window response.
- Add Keil breakpoint listing/parsing so remote ids become available and full
  delete/enable/disable sync can be safely unlocked.
- Begin the debugger source experience pass: PC line readback, gutter current
  line, source breakpoint hit feedback, and a more VSCode-like debug side panel.

## Milestone 50 Update - Keil BL Remote Breakpoint Snapshot Foundation

### Goal

Turn breakpoint sync from local-only push into a safer remote-aware flow by
parsing Keil breakpoint numbers and correcting command generation against the
local Keil uVision help.

### Completed

- Verified Keil command syntax from local `D:\Keil\Keil_v5\UV4\uv4.chm`.
  - uVision Debug Commands require the uppercase command abbreviation.
  - Breakpoint commands are `BS`, `BL`, `BK`, `BE`, and `BD`.
  - `BK`/`BE`/`BD` operate on breakpoint numbers from `BL`.
  - `BK` renumbers breakpoints, so deletes must be ordered carefully.
- Added `src/core/keil/breakpoint_list.py`.
  - Parses Keil `BL` text into backend-neutral `RemoteBreakpointSnapshot`.
  - Keeps `remote_id` as the Keil breakpoint number.
  - Supports decimal/hex ids, English/Chinese enable state, quoted paths,
    paths with spaces, `file:line`, `file line N`, and `file(N)`.
  - Downgrades the snapshot to incomplete when a breakpoint-like line cannot
    be resolved to source path and line, preventing unsafe full-diff deletes.
- Corrected breakpoint command generation.
  - Add: `BS <source-line-expression>`.
  - Remove: `BK <remote_id>`.
  - Enable: `BE <remote_id>`.
  - Disable: `BD <remote_id>`.
  - No location fallback is emitted for id-based commands.
- Made sync execution safer.
  - Enable/disable run before deletes.
  - Deletes run in descending remote id order to reduce Keil renumbering risk.
  - Condition add/update remains blocked until live command behavior is proven.
- Wired backend snapshots.
  - Connected Keil snapshots now attempt `BL` through the live UVSOCK session.
  - Sync results attempt a post-sync `BL` and use it when command output text is
    available.
  - If UVSOCK only echoes or returns no command-window text, the snapshot stays
    incomplete with a clear reason.
- Extended probes.
  - Parser coverage for id/path/state/condition formats.
  - Backend BreakList plumbing with fake session output.
  - UI full-diff confirmation path when a complete remote snapshot is present.
  - Regression for descending `BK` order.

### Verified

- `python -m py_compile src/core/keil/breakpoint_list.py src/core/keil/backend.py src/core/keil/uvsock.py src/core/keil/breakpoint_sync.py src/core/keil/commands.py src/core/keil/__init__.py tools/keil_breakpoint_list_probe.py tools/keil_backend_breakpoint_list_probe.py tools/keil_breakpoint_sync_probe.py tools/ui_keil_breakpoint_sync_probe.py tools/debug_backend_adapter_probe.py tools/keil_backend_adapter_probe.py`
  - PASS.
- `python tools/keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools/keil_breakpoint_list_probe.py`
  - PASS.
- `python tools/keil_backend_breakpoint_list_probe.py`
  - PASS.
- `python tools/ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools/debug_backend_adapter_probe.py`
  - PASS.
- `python tools/keil_backend_adapter_probe.py`
  - PASS in no-live-uVision discover mode.
- `python tools/ui_debug_workbench_probe.py --output-dir tools/ui-debug-workbench-breaklist-final --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-breaklist-final\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-breaklist-final\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-breaklist-final\03_debug_workbench_narrow.png`
- `python tools/keil_command_transaction_probe.py`
  - PASS.
- `python tools/debug_transaction_shell_probe.py`
  - PASS.
- `python tools/debug_snapshot_model_probe.py`
  - PASS.
- `python tools/keil_auto_debug_transaction_probe.py`
  - PASS.
- `python tools/keil_auto_debug_smoke.py --json`
  - PASS.
- `python tools/keil_auto_debug_smoke_profile_probe.py`
  - PASS.
- `python tools/keil_watch_read_probe.py`
  - PASS.
- `python tools/keil_variable_presets_probe.py`
  - PASS.

### Notes

- `UVSC_DBG_EXEC_CMD` may not return command-window output in `_ExecCmd.sCmd`
  on real uVision. The code detects echo/no-output and keeps snapshots
  incomplete instead of trusting a guessed list.
- A future live run must capture the exact `BL` behavior on the user's Keil
  setup. If UVSOCK cannot return `BL` text directly, the next option is a
  command-window/output capture path or another UVSOCK callback mechanism.
- Absolute Windows path source-line expressions still need live verification.
  Keil documentation prefers `\ModuleName\LineNumber`, while current commands
  keep the existing absolute path expression until hardware evidence says to
  remap to module-only expressions.

### Next Target

- Run a guarded live Keil smoke against the connected F401/ST-Link setup:
  - launch or attach uVision/UVSOCK
  - execute `BS` on a harmless source line
  - run `BL`
  - record whether command output text is returned through UVSOCK.
- If `BL` text is available, unlock full remote breakpoint delete/enable/disable
  from the UI for verified complete snapshots.
- Start the source debugger view pass: PC readback, current-line marker, break
  hit feedback, and a compact VSCode-like debug side panel.

## Milestone 51 Update - Real Keil F401 Live Write Proof

### Goal

Move the Keil work from "the bridge exists" to a repeatable real hardware proof:
LoopMaster must start or reuse Keil/UVSOCK, connect to the attached ST-Link/F401
probe project, write a RAM variable, and independently read it back.

### Completed

- Added an auto-debug device guard.
  - `KeilAutoDebugRequest.expected_device` defaults to the F401 CLI smoke path.
  - `--execute` is blocked when the project device does not match unless
    `--allow-device-mismatch` is explicitly passed.
  - The user-provided balance-car reference project is STM32F103C8, so it is
    kept as a reference/preset sample, not the current F401 hardware target.
- Fixed a real UVSOCK debug-mode edge case.
  - `UVSC_GEN_SET_OPTIONS` can return "Target is in debug mode" after uVision
    is already attached.
  - `set_extended_stack()` now treats that state as idempotent, matching
    `UVSC_DBG_ENTER`.
- Made auto-debug reuse existing UVSOCK sessions.
  - `prefer_existing_session=True` now checks the current port before launching
    a new UV4 process.
  - If the connection is reusable, auto-debug records `reuse` and skips launch.
  - Connection polling no longer asks for breakpoint enumeration; this avoids
    an extra UVSOCK debug session and fixed the native DLL exit-code crash seen
    after a successful write.
- Added read-only/diagnostic tools.
  - `tools/keil_balance_reference_probe.py` summarizes the F103 balance-car
    project, presets, debug adapter, and warnings without touching hardware.
  - `tools/keil_breakpoint_live_smoke.py` records live `BL`/`BS` command
    behavior against the F401 probe.

### Verified

- `python tools/keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json`
  - PASS on the connected ST-Link/F401 setup.
  - Reused an existing UVSOCK session and skipped launching a new UV4 process.
  - Wrote `debug_setpoint` at `0x20000008`.
  - Read back `6000` through the memory path.
  - Process exit code was `0`.
- Earlier first live run also proved launch/connect/write when no reusable
  session was used:
  - uVision launched.
  - UVSOCK attached.
  - `debug_setpoint` changed from `1000` to `6000`.
- F103 mismatch guard:
  - `python tools/keil_auto_debug_smoke.py --keil-root D:\Keil --project "<balance Project.uvprojx>" --target "Target 1" --expected-device STM32F401 --execute --no-build --no-launch --no-write --json`
  - PASS as a deliberate failure: blocked before build, launch, connect, or
    write because project device is `STM32F103C8`.
- `python tools/keil_balance_reference_probe.py`
  - PASS; confirms Target `Target 1`, device `STM32F103C8`, ST-Link/SWD,
    default write `SpeedLevel=5`, scope presets including `Angle`,
    `AveSpeed`, `PWML/PWMR`, and PID fields.
- `python tools/keil_breakpoint_live_smoke.py --json`
  - PASS; `BL` executes but only echoes `BL`.
- `python tools/keil_breakpoint_live_smoke.py --set-breakpoint --json`
  - PASS; `BS \D:\LoopMaster_v2.1\firmware\keil_f401_variable_probe\main.c\62`
    is accepted and echoed by Keil.
- `python -m py_compile src/core/keil/backend.py src/core/keil/auto_debug.py src/core/keil/uvsock.py tools/keil_auto_debug_transaction_probe.py tools/keil_auto_debug_smoke.py tools/keil_balance_reference_probe.py tools/keil_breakpoint_live_smoke.py tools/keil_live_variable_write_probe.py`
  - PASS.
- `python tools/keil_auto_debug_transaction_probe.py`
  - PASS.
- `python tools/keil_live_variable_write_probe.py`
  - PASS.
- `python tools/keil_live_write_service_probe.py`
  - PASS.
- `python tools/keil_backend_adapter_probe.py --keil-root D:\Keil --project firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx --target "STM32F401CCU6 Variable Probe"`
  - PASS.

### Notes

- This milestone proves real Keil live variable modification through the
  attached F401/ST-Link setup.
- The balance-car reference is valuable for the product workflow and PID/scope
  presets, but it is an STM32F103C8 project. It must not be auto-executed
  against the currently attached F401 board.
- `UVSC_DBG_EXEC_CMD("BL")` only returns the echoed command text on this setup.
  Full remote breakpoint enumeration still needs a UVSOCK callback, output
  capture, or Keil command-window logging path.
- `BS` was accepted with the current absolute Windows path source expression,
  but because `BL` output is unavailable, automatic remote cleanup/id mapping is
  still not unlocked.

### Next Target

- Add a harder auto-debug ready check before writes:
  - verify project/debug options,
  - read the seed variable before write,
  - require AXF/RAM/readback for smoke success.
- Investigate UVSOCK callbacks or another output-capture path for command
  window text so `BL` can produce real remote breakpoint ids.
- Continue the modern debugger UI pass: source current line, breakpoint gutter
  feedback, compact debug side panel, and later OpenOCD/pyOCD/GDB adapters.

## Milestone 52 Update - Strict Keil Live Variable Smoke

### Goal

Make the real Keil live-write proof harder to fake: a successful auto-debug
write must prove the variable can be resolved through the AXF, read from RAM
before the write, written through the memory path, and independently read back.

### Completed

- Added a first-class Keil live variable read path.
  - `KeilLiveVariableReadRequest` / `KeilLiveVariableReadResult`.
  - `read_keil_live_variable()` for existing `KeilMemorySession` objects.
  - `read_keil_live_variable_existing()` for UVSOCK connections.
- Added a single-session strict smoke path.
  - `run_keil_live_variable_smoke()` performs read-before-write and
    write/readback through one `KeilUvscLiveSession`.
  - `run_keil_live_variable_smoke_existing()` avoids the two-connection pattern
    that previously triggered `UVSC_GEN_SET_OPTIONS` internal errors on this
    Keil setup.
  - Strict success requires method `memory`, a RAM-checked resolved symbol,
    non-empty old/new/readback raw bytes, and `readback_raw == new_raw`.
- Updated auto-debug orchestration.
  - `KeilAutoDebugRequest.read_before_write=True` by default.
  - `KeilAutoDebugRequest.strict_write_smoke=True` by default.
  - Strict mode disables command fallback as a success path.
  - Auto-debug stores and reports the baseline read result in diagnostics.
- Updated UI copy for the Keil auto-debug confirmation so the user sees the
  real sequence: build missing AXF, start or reuse Keil/UVSOCK, connect,
  read before write, then write and read back.
- Added explicit CLI downgrade switches for investigation only:
  - `--no-read-before-write`
  - `--no-strict-write-smoke`
- Tightened probe coverage.
  - The transaction probe now covers the real `run_live_variable_smoke()`
    branch instead of only the legacy read/write fallback.
  - The backend adapter probe now verifies `read_live_variable()` and
    `run_live_variable_smoke()` pass through root/port/debug/read-before-write
    arguments.
  - UI probe fakes now return memory-like results so strict mode is exercised.
- Re-checked the user-provided balance-car project as a reference only.
  - Target `Target 1`, device `STM32F103C8`, ARMCC/StdPeriph F103 project.
  - Current AXF is missing and the project is not suitable for the connected
    F401 board.
  - Useful future profile fields: project/target/device/core, AXF, adapter,
    flash/RAM ranges, source groups, key source files, PID instances, watch
    variables, and runtime suitability.

### Verified

- `python -m py_compile src\core\keil\live_write.py src\core\keil\backend.py src\core\keil\auto_debug.py src\core\keil\__init__.py src\ui\gui.py tools\keil_auto_debug_transaction_probe.py tools\keil_auto_debug_smoke.py tools\keil_live_write_service_probe.py tools\keil_backend_live_write_probe.py tools\ui_debug_workbench_probe.py tools\keil_balance_reference_probe.py`
  - PASS.
- `python tools\keil_live_write_service_probe.py`
  - PASS.
- `python tools\keil_backend_live_write_probe.py`
  - PASS.
- `python tools\keil_auto_debug_transaction_probe.py`
  - PASS.
- `python tools\keil_auto_debug_smoke.py --json`
  - PASS; dry-run records `read_before_write=true` and
    `strict_write_smoke=true`.
- `python tools\keil_auto_debug_smoke.py --json --no-read-before-write --no-strict-write-smoke`
  - PASS; dry-run downgrade switches are visible in JSON.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-strict-smoke-final2 --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-strict-smoke-final2\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-strict-smoke-final2\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-strict-smoke-final2\03_debug_workbench_narrow.png`
- `python tools\keil_balance_reference_probe.py --json`
  - PASS; confirms F103 balance-car reference metadata and PID/scope presets.
- `python tools\keil_variable_presets_probe.py`
  - PASS.
- `python tools\keil_debug_profile_probe.py`
  - PASS.
- `python tools\keil_debug_options_probe.py`
  - PASS.
- `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json`
  - PASS on the connected ST-Link/F401 setup.
  - Reused existing UVSOCK session in paused state.
  - Resolved `debug_setpoint` from DWARF to `0x20000008`.
  - Read before write returned `6000` because the prior proof had already set
    the variable.
  - Wrote through the memory method and read back `6000`.
  - Process exit code was `0`.

### Notes

- This milestone closes the "command-only success" hole: Keil accepting a
  debug command is not enough for strict auto-debug success.
- The current F401 proof is now real variable access rather than only a
  workflow scaffold.
- The balance-car project should enter the next Keil profile/workspace layer as
  an F103 reference and PID preset source, not as something to run on the
  attached F401 board.
- Architecture review confirms the next multi-toolchain step should extract
  three small execution-facing protocols beside the existing read-only backend:
  run control, breakpoint sync, and variable access. Keil build/launch remains
  a Keil workflow, not a cross-backend core API.

### Next Target

- Add a debugger profile/workspace schema that can store both:
  - the F401 live probe project used for real smoke tests,
  - the F103 balance-car reference project with PID/watch metadata.
- Start extracting a generic variable-access execution interface so Keil,
  OpenOCD, pyOCD and GDB can share UI actions without hard-coded Keil method
  names.
- Keep pushing real features before more scaffolding: next visible feature
  should let the UI read a selected variable baseline and present the strict
  write result as a human-readable transaction.

## Milestone 53 Update - Variable Baseline Read and Shared Access Contract

### Goal

Turn the strict Keil write proof into a more human PID-tuning workflow: before
the user writes a variable from the UI, LoopMaster should read the current
value, show it in the confirmation/diagnostics, and keep it in the audit log.
At the same time, start the smallest useful shared variable-access contract so
later OpenOCD, pyOCD and GDB adapters are not forced to copy Keil method names.

### Completed

- Added `src/core/debug_variable_access.py`.
  - Shared request/result dataclasses for variable read, write and write smoke.
  - `DebugVariableAccessAdapter` protocol with `read_variable()`,
    `write_variable()` and `smoke_variable_write()`.
  - Stable `to_record()` output including backend, resolved address, raw bytes
    and diagnostics.
- Added generic variable-access methods to `KeilUvSockBackendAdapter`.
  - `read_variable()` maps to `read_live_variable()`.
  - `write_variable()` maps to `write_live_variable()`.
  - `smoke_variable_write()` maps to the strict single-session Keil smoke.
  - Existing Keil-specific methods remain intact for compatibility.
- Improved the visible UI write-variable flow.
  - Normal "Keil 写变量" now requires `read_live_variable` and
    `write_live_variable`.
  - After the user selects/enters a variable and new value, LoopMaster performs
    a baseline read first.
  - If the baseline read fails, the write is stopped to avoid blind PID writes.
  - The final confirmation now shows current value and new value.
  - Diagnostics now include:
    - `写前读取变量`
    - `写前读取结果`
    - `写前基线值`
    - `写前读取地址`
  - Audit records now include `baseline_read`.
- Auto-debug strict smoke now also publishes its read result as the latest UI
  baseline, so manual write and auto-debug diagnostics share the same surface.
- Re-checked the F103 balance-car reference path in this stage.
  - It remains an F103 reference/preset source, not something to execute on the
    connected F401 board.
  - Existing profile/preset probes continue to cover its target, device, AXF,
    PID write presets and scope presets.

### Verified

- `python -m py_compile src\core\debug_variable_access.py src\core\keil\backend.py src\ui\gui.py tools\debug_variable_access_probe.py tools\keil_backend_live_write_probe.py tools\ui_debug_workbench_probe.py tools\keil_auto_debug_transaction_probe.py tools\keil_live_write_service_probe.py tools\keil_auto_debug_smoke.py`
  - PASS.
- `python tools\debug_variable_access_probe.py`
  - PASS.
- `python tools\keil_backend_live_write_probe.py`
  - PASS; covers both Keil-specific and generic variable-access method names.
- `python tools\keil_auto_debug_transaction_probe.py`
  - PASS.
- `python tools\keil_live_write_service_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-baseline-read --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-baseline-read\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-baseline-read\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-baseline-read\03_debug_workbench_narrow.png`
- `python tools\keil_auto_debug_smoke.py --json`
  - PASS.
- `python tools\keil_balance_reference_probe.py --json`
  - PASS.
- `python tools\keil_profile_store_probe.py`
  - PASS.
- `python tools\keil_variable_presets_probe.py`
  - PASS.
- `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json`
  - PASS on the connected ST-Link/F401 setup.
  - Reused the existing paused UVSOCK session.
  - Resolved `debug_setpoint` to `0x20000008`.
  - Baseline read returned `6000`.
  - Memory write/readback returned `6000`.

### Notes

- The generic variable-access contract is intentionally thin. It is not a large
  backend rewrite; it creates stable names for the UI and future OpenOCD/pyOCD
  adapters while Keil keeps working through the already proven path.
- The ordinary manual write path is now safer for PID tuning: the user sees the
  current value immediately before committing the write, and a failed read stops
  the write.
- `DebugRuntimeState` and several UI action methods are still Keil-named. That
  is the next architectural layer, not this slice.

### Next Target

- Move the UI write-variable action from Keil-specific method names to the new
  generic `DebugVariableAccessAdapter` methods while keeping Keil as the first
  implementation.
- Add a richer debugger profile/workspace record that stores F401 live-probe
  and F103 balance-car metadata together with runtime suitability.
- Continue the source-debugger work: PC/current-line readback and breakpoint
  evidence should be the next visible debugging features after variable access.

## Milestone 54 Update - Generic Variable UI and Richer Keil Profiles

### Goal

Use the shared variable-access contract in the UI, not just in backend tests,
and make saved Keil debug profiles carry enough metadata to distinguish the
real F401 smoke project from the F103 balance-car reference project.

### Completed

- Migrated the manual UI write-variable path to the generic variable-access
  method names.
  - `_write_keil_live_variable_from_workbench()` now prefers
    `read_variable()` / `write_variable()`.
  - Existing Keil `read_live_variable()` / `write_live_variable()` remains as a
    fallback for compatibility.
  - UI diagnostics and audit still use the existing Keil-shaped result surface,
    with conversion helpers from generic results.
- Extended the UI probe fake backend with generic methods.
  - The probe now verifies manual writes use `read_variable()` and
    `write_variable()` rather than only Keil-specific names.
- Extended persistent Keil debug profile records.
  - Store version is now `2`.
  - Added `KeilDebugProfileMetadata` with:
    - device and inferred CPU core
    - adapter/protocol
    - flash/RAM ranges
    - flash algorithm
    - runtime suitability
    - preset key
    - default write variable/value
    - write and scope preset summaries
    - warning summaries
  - Old profile JSON remains readable because metadata defaults to an empty
    record when absent.
- Added metadata generation from `KeilDebugProfile`.
  - F401 probe profiles are marked `connected_f401_smoke`.
  - The user-provided balance-car project is marked `reference_only_f103`.
  - Unknown projects fall back to `candidate_with_axf` or `profile_only`.
- Surfaced saved profile metadata in the debug workbench diagnostics table.
  - This makes the selected profile's device, adapter, suitability and default
    write visible without opening the JSON file.

### Verified

- `python -m py_compile src\core\debug_variable_access.py src\core\keil\profile_store.py src\core\keil\backend.py src\ui\gui.py tools\keil_profile_store_probe.py tools\ui_debug_workbench_probe.py tools\keil_backend_live_write_probe.py tools\debug_variable_access_probe.py`
  - PASS.
- `python tools\keil_profile_store_probe.py`
  - PASS; verifies F401 metadata, F103 reference-only metadata and persisted
    metadata reload.
- `python tools\debug_variable_access_probe.py`
  - PASS.
- `python tools\keil_backend_live_write_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-generic-variable-final --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-generic-variable-final\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-generic-variable-final\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-generic-variable-final\03_debug_workbench_narrow.png`
- `python tools\keil_balance_reference_probe.py --json`
  - PASS.
- `python tools\keil_variable_presets_probe.py`
  - PASS.
- `python tools\keil_auto_debug_smoke.py --json`
  - PASS.
- `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json`
  - PASS on the connected ST-Link/F401 setup.
  - Reused the existing paused UVSOCK session.
  - Baseline read returned `6000`.
  - Memory write/readback returned `6000`.

### Notes

- This does not make OpenOCD/pyOCD fully functional yet, but it removes one UI
  hard dependency on Keil-specific method names.
- The metadata is deliberately compact and serializable. It is meant to support
  the next UI profile selector and future multi-toolchain profile import, not
  replace the full Keil project parser.
- The F103 balance-car project is now explicitly modeled as reference-only in
  saved profile metadata, matching the hardware mismatch guard.

### Next Target

- Add a user-visible profile selector/inspector row for runtime suitability and
  default write/scope presets.
- Start the first source-debugger visible slice:
  - current PC/current source line evidence,
  - clearer breakpoint evidence when Keil only echoes command text,
  - shared interface shape for OpenOCD/GDB run control.

## Milestone 55 Update - PC Evidence UI and Debug Workbench Narrow-Window Fix

### Goal

Make the source-debugger UI distinguish real backend PC evidence from local or
placeholder state, then fix the narrow-window overlap found during screenshot
inspection.

### Completed

- Added PC evidence state to `DebugWorkbenchTab`.
  - `set_pc_evidence()` accepts the existing `DebugPcLocation` model.
  - PC gutter tooltip now reports whether the PC is read back, unverified or
    pending.
  - The source marker summary now shows `PC 已回读` or `PC 未验证` instead of a
    generic `PC` when evidence is available.
  - Unverified PC markers are rendered with a lighter dashed arrow so they do
    not look like confirmed backend state.
- Passed backend snapshot PC evidence through the main window.
  - Discover, read-only attach, generic snapshot application and Keil Halt/Run
    paths now update the debug workbench PC evidence.
  - Backend switching and Keil runtime reconfiguration clear stale PC evidence.
- Added PC evidence diagnostics.
  - The diagnostics table now surfaces `PC 证据`, `PC 来源`, `PC 位置` and
    `PC 说明` from the current backend snapshot record.
  - Keil's current placeholder remains explicitly marked as unverified:
    `Keil PC 位置读取尚未实现`.
- Fixed a real UI issue found in screenshot review.
  - The debug workbench navigation/diagnostics column now uses an internal
    borderless scroll area.
  - In narrow windows, diagnostics, variable presets and breakpoints scroll
    vertically instead of overlapping each other.
- Updated the UI screenshot probe.
  - Fake read-only backend snapshots now carry an incomplete `DebugPcLocation`.
  - The probe asserts unverified PC diagnostics, local unverified PC tooltips
    and a synthetic verified PC tooltip with address/function evidence.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py src\ui\gui.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\debug_snapshot_model_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-pc-evidence-scroll --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-pc-evidence-scroll\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-pc-evidence-scroll\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-pc-evidence-scroll\03_debug_workbench_narrow.png`
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_backend_adapter_probe.py`
  - PASS; Keil PC snapshot remains incomplete until real PC readback exists.
- `python tools\debug_variable_access_probe.py`
  - PASS.
- `python tools\keil_balance_reference_probe.py --json`
  - PASS; the F103 balance-car project remains a reference profile.
- `python tools\keil_profile_store_probe.py`
  - PASS.
- `python tools\keil_variable_presets_probe.py`
  - PASS.
- `python tools\keil_backend_live_write_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json`
  - PASS on the connected ST-Link/F401 setup.
  - LoopMaster launched Keil/UVSOCK, connected in 5 attempts, read
    `debug_setpoint = 1000`, wrote `6000`, and read back `6000` from
    `0x20000008`.

### Notes

- This milestone does not claim real Keil PC/source-line readback yet. It makes
  the UI truthful by carrying incomplete PC evidence through the same surface
  that future Keil/OpenOCD/pyOCD readback will use.
- The F401 Keil smoke caused uVision to rewrite `.uvprojx/.uvoptx` local state;
  those generated changes were restored and not included in this milestone.
- No `UV4.exe`/`uVision` process remained after cleanup.

### Next Target

- Implement the first real breakpoint execution slice for Keil:
  - convert local breakpoints into concrete Keil debug commands,
  - execute an explicit sync action with confirmation,
  - record backend evidence for accepted/failed breakpoints,
  - keep the same evidence model compatible with OpenOCD/GDB and pyOCD.
- Investigate reliable PC/source-line readback routes:
  - Keil Debug Commands/UVSOCK status,
  - AXF symbol and DWARF line mapping,
  - GDB/MI-compatible PC readback for OpenOCD and pyOCD.

## Milestone 56 Update - Keil Breakpoints Use AXF Line Addresses

### Goal

Move Keil breakpoint sync from source-path command strings toward a real,
verifiable debug chain: resolve local source lines through AXF/DWARF, send
address breakpoints to Keil, and do not claim remote breakpoint readback when
UVSOCK only echoes `BL`.

### Completed

- Added AXF/DWARF source-line address resolution.
  - New module: `src/core/keil/source_line_address.py`.
  - Parses `arm-none-eabi-readelf -wl` text.
  - Resolves `source.c:line` to flash code addresses.
  - F401 probe verified `main.c:62 -> 0x08000164`.
- Extended Keil breakpoint operations with address evidence.
  - `KeilBreakpointSyncOperation` now carries:
    - `address`
    - `address_source`
    - `address_exact`
  - `keil_breakpoint_command()` now emits `BS 0x...` when an address is
    available, falling back to the source-line expression only when unresolved.
- Passed AXF profile data into UI breakpoint sync.
  - The workbench builds breakpoint sync requests with the current Keil
    profile's AXF when available.
  - Sync diagnostics now show the AXF path and address-resolution counts.
- Fixed breakpoint evidence honesty.
  - Keil backend no longer treats a fallback snapshot as a complete remote
    breakpoint enumeration.
  - If `BL` only echoes and does not return a parseable list, the snapshot is
    incomplete with error:
    `Keil 已执行断点命令，但 BL 未返回可解析断点列表`.
  - The UI now distinguishes:
    - remote breakpoint list confirms the breakpoint: `Keil 已回读该断点`
    - command was accepted but no list readback exists:
      `Keil 已接受命令，等待断点列表回读`
- Updated live smoke tooling.
  - `tools/keil_breakpoint_live_smoke.py` now resolves AXF line addresses.
  - Added `--verify-hit` to run the target after setting a breakpoint and poll
    for a stop.
- Recorded TI follow-up constraints without interrupting Keil work.
  - TI path: `D:\ti`.
  - TI target scope: MSPM0G3507 only.
  - No TI debug hardware is currently available, so later TI work should start
    with offline CCS/SysConfig/SDK project parsing and ELF/source/variable
    mapping, not hardware claims.

### Verified

- `python -m py_compile src\core\keil\source_line_address.py src\core\keil\breakpoint_sync.py src\core\keil\backend.py src\core\keil\commands.py src\ui\gui.py tools\keil_breakpoint_live_smoke.py tools\ui_keil_breakpoint_sync_probe.py tools\keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\keil_source_line_address_probe.py`
  - PASS; `main.c:62 -> 0x08000164`.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS; verifies address-backed `BS 0x...` command generation.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS; verifies command-accepted-but-not-read-back evidence remains
    unverified.
- `python tools\keil_breakpoint_live_smoke.py --keil-root D:\Keil --line 62 --set-breakpoint --verify-hit --json`
  - PASS on connected ST-Link/F401.
  - Sent `BS 0x08000164`.
  - `hit_detected=true` after running the target.
  - `BL` still only echoed `BL`, so remote breakpoint list enumeration remains
    incomplete.
- `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json`
  - PASS; variable read/write still works after the breakpoint changes.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-keil-breakpoint-address --width 1440 --height 900`
  - PASS; screenshots:
    - `tools\ui-debug-workbench-keil-breakpoint-address\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-keil-breakpoint-address\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-keil-breakpoint-address\03_debug_workbench_narrow.png`
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_backend_breakpoint_list_probe.py`
  - PASS.
- `python tools\keil_breakpoint_list_probe.py`
  - PASS.
- `python tools\debug_variable_access_probe.py`
  - PASS.
- `python tools\keil_profile_store_probe.py`
  - PASS.
- `python tools\keil_variable_presets_probe.py`
  - PASS.
- `python tools\keil_balance_reference_probe.py --json`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\keil_backend_live_write_probe.py`
  - PASS.

### Notes

- The Keil breakpoint base chain is now real enough to set an address
  breakpoint and observe the target stop on F401 hardware.
- Full remote breakpoint enumeration is not solved because UVSOCK
  `UVSC_DBG_EXEC_CMD("BL")` returns only the echoed command in current testing.
  The UI and diagnostics now reflect that limitation instead of overstating it.
- No big-version packaging was done for this milestone.
- No `UV4.exe`/`uVision` process remained after cleanup.

### Next Target

- Finish Keil breakpoint usability:
  - investigate an alternate way to read Keil breakpoint IDs/list state,
  - add a safer clear/remove workflow once IDs are available,
  - surface address evidence in the UI confirmation/details,
  - add a dedicated hardware breakpoint smoke that leaves the target in a known
    paused/running state.
- After Keil breakpoints are stable, start TI MSPM0G3507 offline adapter work
  from `D:\ti` without claiming live debug support until hardware is available.

## Milestone 57 Update - Keil Breakpoint Address Readback

### Goal

Close the next Keil breakpoint gap: after LoopMaster sends a real address
breakpoint and the F401 target hits it, the backend should bring back truthful
remote evidence instead of reporting only "command accepted".

### Completed

- Added backend-neutral address evidence to `RemoteBreakpoint`.
  - Remote breakpoint snapshots now serialize/deserialize `address`.
  - This is intentionally generic for Keil, OpenOCD/GDB, pyOCD, and later TI
    adapters.
- Extended Keil `BL` parsing for address-only breakpoints.
  - Keil can return lines like:
    `0: (E 0x08000164) '0x08000164', CNT=1, enabled`.
  - The parser now preserves that as a remote breakpoint with
    `address=0x08000164`.
  - The snapshot remains source-incomplete because Keil did not provide a file
    and line number.
- Added a backend `LOG+BL` fallback.
  - Direct UVSOCK `BL` still usually echoes only `BL`.
  - The backend now opens a temporary Keil command log, executes `BL`, reads the
    log file, parses it, and deletes the temporary file.
  - Direct `BL` errors still take priority over later log-capture errors.
- Improved UI breakpoint evidence.
  - The Keil breakpoint confirmation now shows AXF path, resolved/unresolved
    address counts, and address samples.
  - If the remote snapshot contains an address-only breakpoint that matches the
    address LoopMaster just sent, the local breakpoint is marked:
    `Keil 已按地址回读该断点`.
  - If only command success exists and no remote evidence exists, it remains
    unverified.
- Updated smoke tooling.
  - `tools/keil_breakpoint_live_smoke.py` gained `--capture-bl-log`.
  - The smoke conclusion now distinguishes direct `BL` echo from `LOG+BL`
    address readback.

### Verified

- `python -m py_compile src\core\debug_snapshots.py src\core\keil\breakpoint_list.py src\core\keil\backend.py src\core\keil\commands.py src\core\keil\breakpoint_sync.py src\ui\gui.py tools\keil_breakpoint_list_probe.py tools\keil_breakpoint_sync_probe.py tools\ui_keil_breakpoint_sync_probe.py tools\keil_breakpoint_live_smoke.py`
  - PASS.
- `python tools\keil_breakpoint_list_probe.py`
  - PASS; covers address-only `LOG+BL` parsing and backend log fallback.
- `python tools\debug_snapshot_model_probe.py`
  - PASS.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS; verifies address diagnostic samples.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\keil_breakpoint_live_smoke.py --keil-root D:\Keil --line 62 --set-breakpoint --verify-hit --capture-bl-log --json`
  - PASS on connected ST-Link/F401.
  - Sent `BS 0x08000164`.
  - `hit_detected=true`.
  - `LOG+BL` returned one address-only remote breakpoint at `0x08000164`.
- Backend read-only snapshot against the live Keil session:
  - PASS.
  - `connection_established=true`.
  - `target_running=false`.
  - `remote_count=1`.
  - remote address list included `0x08000164`.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-breakpoint-address --width 1440 --height 900`
  - PASS; screenshot review showed no obvious overlap in the workbench source
    and breakpoint evidence area.
- `python tools\debug_variable_access_probe.py`
  - PASS.
- `python tools\keil_backend_live_write_probe.py`
  - PASS.
- `python tools\keil_profile_store_probe.py`
  - PASS.
- `git diff --check`
  - PASS.

### Notes

- Keil breakpoint basics are now materially further than command preview:
  LoopMaster can resolve a source line to AXF address, send `BS 0x...`, run the
  target, observe the breakpoint hit, and read back the remote address through
  `LOG+BL`.
- The remaining gap is source/ID fidelity:
  - Keil's log output currently gives the breakpoint index and address, not a
    source path and line.
  - Full safe remove/enable/disable needs stable ID handling and address/source
    matching rules before LoopMaster should perform aggressive remote cleanup.
- No big-version packaging was done for this milestone.
- Test-created `UV4.exe` and temporary `tools\keil_breakpoint_live_bl.log` were
  cleaned up after verification.

### Next Target

- Finish Keil breakpoint management:
  - parse and preserve Keil breakpoint IDs from `LOG+BL`,
  - match address-only remote breakpoints back to local AXF source lines,
  - add safe remove/enable/disable workflows using verified IDs,
  - add a UI flow for "clear LoopMaster-managed breakpoints" with confirmation.
- Then move to Keil PC/source-location readback and stepping.
- TI MSPM0G3507 remains queued after Keil basics, starting from offline
  project/ELF/source mapping under `D:\ti` until live TI hardware exists.

## Milestone 58 Update - Keil Breakpoint Full Diff Basics

### Goal

Turn Keil breakpoint readback into a usable management loop: map remote
address-only breakpoints back to source lines, preserve Keil IDs, and allow
LoopMaster to add, disable, enable, and remove breakpoints through full diff
sync.

### Completed

- Added reverse AXF/DWARF mapping.
  - `resolve_address_source_line()` maps code addresses back to source file and
    line.
  - Verified `0x08000164 -> main.c:62` on the F401 probe AXF.
- Mapped Keil address-only `LOG+BL` snapshots back to source lines.
  - Backend now uses current project/Target source roots and AXF profile data.
  - When all remote address breakpoints map to source, the snapshot becomes
    complete and safe for diff.
  - Keil remote ID is preserved, for example ID `0` at `main.c:62`.
- Enabled safe clear-all behavior.
  - The UI no longer blocks breakpoint sync just because there are no local
    breakpoints.
  - If there is a complete remote snapshot, syncing an empty local set produces
    remove operations such as `BK 0`.
  - If no local breakpoints exist and the remote snapshot is incomplete, the UI
    still blocks cleanup.
- Updated UI action availability.
  - The `同步断点` action is available after Keil attach so users can open the
    explicit confirmation flow for add/remove/clear.
  - The tooltip now clearly states that it performs an explicit UVSOCK action
    with confirmation.
- Extended probes.
  - Address reverse mapping.
  - Backend source mapping from real Keil-style `LOG+BL` text.
  - UI clear-all sync path producing `BK <id>`.
  - Workbench probe expectations updated so `sync_breakpoints` is allowed while
    `run`/`step` remain blocked in the read-only attach scenario.

### Verified

- `python -m py_compile src\core\keil\source_line_address.py src\core\keil\breakpoint_sync.py src\core\keil\backend.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\keil_source_line_address_probe.py tools\keil_breakpoint_list_probe.py tools\keil_breakpoint_sync_probe.py tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\keil_source_line_address_probe.py`
  - PASS; `line 62 -> 0x08000164` and
    `0x08000164 -> ...\main.c:62`.
- `python tools\keil_breakpoint_list_probe.py`
  - PASS.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS; includes clear-all remote sync.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-breakpoint-clear --width 1440 --height 900`
  - PASS; screenshots generated and reviewed.
- `python tools\debug_variable_access_probe.py`
  - PASS.
- `python tools\keil_profile_store_probe.py`
  - PASS.
- Real F401/ST-Link Keil checks:
  - Keil auto debug variable smoke passed.
  - `BS 0x08000164` set and hit at `main.c:62`.
  - Backend read-only snapshot mapped remote ID `0` to `main.c:62`.
  - `BK 0` clear-all removed the remote breakpoint; final read-only snapshot
    reported `complete=true`, `count=0`.
  - Manual enable/disable script output showed `BD 0` and `BE 0` succeed with
    remote enabled state changing false/true; the script's shell return code was
    unreliable, so the JSON evidence was used and final cleanup was separately
    confirmed.
- `git diff --check`
  - PASS.

### Notes

- Keil breakpoint basics now cover:
  - source line to address,
  - address breakpoint set,
  - target hit detection,
  - remote address/ID readback,
  - address to source remap,
  - full-diff clear via `BK`,
  - disable/enable via `BD`/`BE` verified by JSON output.
- The remaining Keil basics before moving on:
  - make PC/source-location readback real,
  - add single-step/step-over behavior,
  - improve UI layout for narrow debug-workbench widths where the top action
    row is still crowded.
- Test-created `UV4.exe` and `tools\keil_breakpoint_live_bl.log` were cleaned
  up after verification.

### Next Target

- Implement truthful Keil PC/source-location readback.
- Add stepping controls backed by Keil commands and refresh PC/source markers.
- Keep TI MSPM0G3507 queued after Keil debug basics; start with offline adapter
  parsing from `D:\ti`.

## Milestone 59 Update - Keil PC Readback And Step

### Goal

Make Keil source-level debugging visibly real in LoopMaster: read the actual PC
from Keil/uVision, map it back to source, and execute a real single-step while
refreshing the PC/source marker.

### Completed

- Added Keil PC readback.
  - New module: `src/core/keil/pc_location.py`.
  - Uses `LOG+EVAL PC` because direct UVSOCK expression evaluation rejects
    Keil system variables such as `$`/`PC`/`R15`.
  - Parses output such as `0x0800015A 134218074`.
  - Maps the PC address back to source using existing AXF/DWARF line mapping.
- Connected PC evidence into backend snapshots.
  - When the target is paused, `read_only_session_snapshot()` now attempts PC
    readback.
  - Snapshot diagnostics now show concrete PC text such as
    `0x08000164 / main.c:62`.
  - Running targets do not get implicitly halted just to read PC.
- Added Keil step execution.
  - `KeilUvscLiveSession.step_target()` executes Keil `T`.
  - Step now polls target status until it returns to paused, because Keil can
    briefly report running after the step command returns.
  - `KeilUvSockBackendAdapter.step_target()` reuses the runtime-control path and
    refreshes snapshot/PC after execution.
- Exposed step through the Debug Workbench action path.
  - `step` now uses the same explicit confirmation and backend execution path
    as Halt/Run.
  - The action is enabled only from the paused Keil state in the UI layer.
- Recorded architecture requirement from user.
  - Debug and scope must remain selectable by mode:
    - original non-invasive/light-invasive variable and serial scope,
    - Keil-driven debug mode,
    - future OpenOCD/pyOCD/GDB/TI OCD modes,
    - all able to feed the same scope surface through acquisition sources.
  - This is now documented in `docs/debug_workbench_plan.md`.

### Verified

- `python -m py_compile src\core\keil\pc_location.py src\core\keil\uvsock.py src\core\keil\backend.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\keil_pc_location_probe.py`
  - PASS.
- `python tools\keil_pc_location_probe.py --json`
  - PASS; fake `LOG+EVAL PC` maps `0x0800015A -> main.c:26`.
- `python tools\keil_pc_location_probe.py --live --step --keil-root D:\Keil --json`
  - PASS on connected ST-Link/F401.
  - Live PC read example: `0x08000124 -> main.c:32`.
  - Step result: `UVSOCK 单步成功，目标已暂停`.
  - After-step PC example: `0x08000134 -> main.c:35`.
- Backend snapshot live check:
  - PASS by evidence.
  - `connection_established=true`.
  - `target_running=false`.
  - `pc_location.complete=true`.
  - Example diagnostic: `PC 位置 = 0x0800015A / main.c:26`.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-pc-step-final --width 1440 --height 900`
  - PASS; screenshot reviewed.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\debug_session_contract_probe.py`
  - PASS.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\debug_variable_access_probe.py`
  - PASS.
- `python tools\keil_profile_store_probe.py`
  - PASS.

### Notes

- Official Keil command behavior observed locally:
  - `UVSC_DBG_EVAL_EXPRESSION_TO_STR("$"/"PC"/"R15")` returns command error.
  - `UVSC_DBG_EXEC_CMD("EVAL PC")` only echoes directly.
  - `LOG > file` + `EVAL PC` + `LOG OFF` captures the real PC output.
- Step command `T` is asynchronous enough that immediate status can be
  misleading. The backend now polls for paused before declaring step success.
- The current step implementation is trace/single-step only. Step-over and
  step-out remain separate work.
- Test-created `UV4.exe` was cleaned up after verification.

### Next Target

- Add step-over/step-out candidates and verify which Keil commands are reliable
  through UVSOCK.
- Improve the debug-workbench top action layout for narrower widths.
- Start extracting acquisition/source-mode selection so non-invasive scope,
  Keil scope, and future OCD scope can coexist without UI crowding.

## Milestone 60 - Keil Step-Over and Scope/Debug Mode Boundary

### Goal

Make the next Keil runtime-control action real instead of only planned: expose a
verified Step Over path from UI -> backend -> UVSOCK -> target -> PC/source
readback. Also record the user requirement that debug and scope must remain
mode-selectable instead of forcing every waveform through a debugger.

### Completed

- Added Keil Step Over runtime control.
  - `KeilUvscLiveSession.step_over_target()` sends Keil debug command `P`.
  - `KeilUvSockBackendAdapter.step_over_target()` uses the same explicit
    runtime-control path as Halt/Run/Step and refreshes snapshot evidence after
    execution.
  - `UvscRuntimeControlResult.summary()` and Keil runtime labels now render
    `step_over` as `跨过`.
- Exposed Step Over in backend-neutral planning.
  - `DebugAction("step_over", "跨过", ...)` is enabled only when the target is
    paused and runtime-control capability is present.
  - `DebugCommandKind` and `KeilCommandKind` now both include `step_over`.
  - Generic debug session command matrix includes `step_over`, so future
    OpenOCD/GDB, pyOCD, GDB server and TI OCD adapters can expose the same
    action without inventing another UI path.
- Exposed Step/Step Over in the Debug Workbench UI.
  - Added `单步` and `跨过` action buttons.
  - Shortened several action labels (`启动`, `调试`, `断点`, `写入`) and reduced
    button minimum width to keep the top toolbar usable at 1440px and narrower
    probe sizes.
- Extended Keil PC probe.
  - `tools/keil_pc_location_probe.py` now supports `--step-over`.
  - The probe asserts Step Over succeeds, leaves the target paused, and has a
    valid after-step PC/source readback.
- Recorded debug/scope mode boundary.
  - LoopMaster must support the original non-invasive/light-invasive scope and
    serial scope modes independently from debugger-driven modes.
  - Keil/OpenOCD/pyOCD/GDB/TI OCD modes may feed the same scope surface, but the
    scope/acquisition layer must stay separate from debug backend state.
  - Debug backend owns target state, PC/source, breakpoints and variable/memory
    access. Acquisition sessions own sample timing, buffering, decimation and
    waveform health.

### Verified

- `python -m py_compile src\core\keil\uvsock.py src\core\keil\backend.py src\core\debug_workbench.py src\core\debug_transactions.py src\core\debug_session_contract.py src\core\keil\commands.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\keil_pc_location_probe.py tools\debug_workbench_model_probe.py tools\debug_session_contract_probe.py tools\debug_session_controller_probe.py tools\keil_command_transaction_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\debug_session_contract_probe.py`
  - PASS.
- `python tools\debug_session_controller_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\debug_variable_access_probe.py`
  - PASS.
- `python tools\keil_profile_store_probe.py`
  - PASS.
- `python tools\keil_pc_location_probe.py --json`
  - PASS; fake `LOG+EVAL PC` still maps `0x0800015A -> main.c:26`.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-step-over --width 1440 --height 900`
  - PASS.
  - Screenshot reviewed: new `单步` / `跨过` buttons fit the action row without
    overlapping the source toolbar or plan strip.
- `python tools\keil_pc_location_probe.py --live --step --step-over --keil-root D:\Keil --json`
  - PASS on connected ST-Link/F401.
  - Live PC example before stepping: `0x08000144 -> main.c:38`.
  - Step result: `UVSOCK 单步成功，目标已暂停`, after PC `main.c:39`.
  - Step Over result: `UVSOCK 跨过成功，目标已暂停`, after PC `main.c:64`.
- `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json`
  - PASS on connected ST-Link/F401.
  - Reused the paused UVSOCK session and verified live variable access.
  - `debug_setpoint @ 0x20000008 = 6000`; baseline read and readback both
    succeeded through memory write path.
- Keil/uVision cleanup check:
  - Closed the test-created UV4 session.
  - `Get-Process UV4,uVision` returned no remaining process.

### Notes

- Keil command `O` was tested as a Step Out candidate in the current F401 probe
  context. It did not produce a safe paused-after-action result and left the
  target running, so Step Out is intentionally not exposed in the UI yet.
- `P` Step Over is reliable in the tested F401 probe context and now has the
  same confirmation/readback pattern as other runtime-control actions.
- No package was built for this milestone because it is not a large version
  release boundary.

### Next Target

- Keep hardening Keil debugger basics before returning to broad architecture:
  reset/run-to-cursor candidates, richer breakpoint status, and a clearer
  source/debug action layout.
- Start the acquisition-source boundary work: define a small neutral model for
  original lightweight scope, serial scope, Keil watch polling, future
  OpenOCD/pyOCD/TI watch polling and file replay.
- Continue avoiding Step Out until a live probe can prove a consistent
  paused-after-action result across source locations.

## Milestone 61 - Keil Reset Runtime Control

### Goal

Add one more real Keil debugger control only after live verification: Reset must
be exposed as its own capability, execute through UVSOCK, leave the target in a
known state, and refresh PC/source evidence.

### Completed

- Tested Keil reset command candidates against the connected ST-Link/F401
  probe.
  - `RESET` succeeds through `UVSC_DBG_EXEC_CMD`.
  - `RST` also succeeds as an alias, but LoopMaster uses only `RESET` to keep
    UI, preview and audit wording consistent.
  - Reset leaves the target paused and PC maps to
    `startup_stm32f401ccux.s:41`.
- Added explicit reset capability.
  - `DebugCapabilities` and `DebugSessionCapabilities` now have `can_reset`.
  - Generic debug session command matrix includes `reset`.
  - `DebugCommandKind` and `KeilCommandKind` include `reset`.
  - Read-only snapshots and placeholder backends assert that reset is not
    enabled by accident.
- Added Keil reset execution.
  - `KeilUvscLiveSession.reset_target()` sends `RESET` and polls until target
    state is paused.
  - `KeilUvSockBackendAdapter.reset_target()` refreshes the backend snapshot
    and PC/source evidence after execution.
  - UI runtime-control path now handles `reset` with the same explicit
    confirmation flow as Halt/Run/Step/Step Over.
- Added Debug Workbench UI action.
  - New `复位` button in the action row.
  - Top action buttons now use tighter minimum width to preserve 1440px and
    narrow-probe layout.
- Hardened UVSOCK connection setup.
  - `UVSC_GEN_SET_OPTIONS` is treated as optional in
    `KeilUvscLiveSession.connect_existing()`.
  - If Keil returns an internal error while setting this option, the session
    records `options_warning` and continues to `enter_debug`, letting real
    status/command/PC readback decide success.
  - Direct `set_extended_stack()` remains strict for low-level tests and
    explicit callers.
- Extended live PC probe.
  - `tools/keil_pc_location_probe.py` now supports `--reset`.
  - The probe asserts reset succeeds, target remains paused, and PC/source
    mapping exists after reset.

### Verified

- Reset candidate inline probe:
  - `RESET`: success, target paused, after PC `0x08000054 ->
    startup_stm32f401ccux.s:41`.
  - `RST`: success, target paused, same PC/source; kept as observed alias only.
- `python -m py_compile src\core\debug_workbench.py src\core\debug_transactions.py src\core\debug_session_contract.py src\core\debug_backend.py src\core\keil\uvsock.py src\core\keil\backend.py src\core\keil\commands.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\debug_workbench_model_probe.py tools\debug_session_contract_probe.py tools\debug_session_controller_probe.py tools\debug_transaction_shell_probe.py tools\keil_command_transaction_probe.py tools\debug_backend_adapter_probe.py tools\ui_debug_workbench_probe.py tools\keil_pc_location_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\debug_session_contract_probe.py`
  - PASS.
- `python tools\debug_session_controller_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS; adapter reset execution refreshes paused snapshot.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\keil_backend_adapter_probe.py`
  - PASS; read-only adapter still does not enable reset.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-reset --width 1440 --height 900`
  - PASS; screenshot reviewed and action row still fits after adding `复位`.
- `python tools\keil_pc_location_probe.py --live --step --step-over --reset --keil-root D:\Keil --json`
  - PASS on connected ST-Link/F401.
  - Reset result: `UVSOCK 复位成功，目标已暂停`.
  - After reset PC: `startup_stm32f401ccux.s:41`.
- `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json`
  - PASS on connected ST-Link/F401.
  - Confirmed live variable read/write still works after reset support.
  - `debug_setpoint @ 0x20000008 = 6000`; command exit code `0`.
- Keil/uVision cleanup check:
  - Closed the test-created UV4 session.
  - `Get-Process UV4,uVision` returned no remaining process.

### Notes

- Reset is high-risk runtime control even though it leaves the target paused.
  It remains behind explicit user confirmation.
- `UVSC_GEN_SET_OPTIONS` can return `UVSC Internal Error` while an otherwise
  valid Keil session is running. The new connection path no longer treats that
  optional setup step as fatal.
- Step Out remains intentionally unavailable.

### Next Target

- Investigate run-to-cursor as a temporary-breakpoint transaction, with strict
  cleanup of the temporary breakpoint before exposing any UI button.
- Start defining the acquisition-source model so lightweight scope, serial
  scope, Keil polling and later OpenOCD/pyOCD/TI polling can share waveform UI
  without sharing debugger state.

## Milestone 62 - Keil Run-to-Cursor Temporary Breakpoint Transaction

### Goal

Prove `run-to-cursor` as a real Keil debugger operation without adding a UI
button yet. The core requirement is strict temporary-breakpoint hygiene: resolve
the cursor line to an address, set a temporary breakpoint, run, verify the PC at
the target source line, remove only the temporary breakpoint, and verify it is
gone.

### Completed

- Live-tested the transaction manually on the connected ST-Link/F401 probe.
  - Target line: `firmware/keil_f401_variable_probe/main.c:62`.
  - DWARF/AXF resolved the line to `0x08000164`.
  - `BS 0x08000164` created remote breakpoint id `0`.
  - Run stopped with PC at `main.c:62`.
  - `BK 0` removed the temporary breakpoint.
  - Final `BL` showed no remaining breakpoints.
- Added reusable Keil run-to-cursor transaction.
  - New module: `src/core/keil/run_to_cursor.py`.
  - `KeilRunToCursorRequest` describes project, target, source line, AXF,
    source roots, timeout and optional deterministic reset.
  - `run_keil_to_cursor_transaction()` performs:
    1. exact source-line address resolution,
    2. preflight `BL`,
    3. temporary `BS`,
    4. `Run`,
    5. target paused polling,
    6. `LOG+EVAL PC` source verification,
    7. `BK` cleanup,
    8. final `BL` cleanup verification.
  - Failure paths also attempt cleanup when a temporary breakpoint was created.
  - Existing user breakpoints at the target address can be reused without being
    deleted by the transaction.
- Exposed the transaction through the Keil backend adapter.
  - `KeilUvSockBackendAdapter.run_to_cursor()` exists for future UI/DAP use.
  - The Debug Workbench UI does not expose a run-to-cursor button yet.
- Added probe coverage.
  - New `tools/keil_run_to_cursor_probe.py`.
  - Default mode uses a fake session and validates command order and cleanup.
  - `--live` mode executes the real transaction against Keil/UVSOCK.

### Verified

- Manual inline live probe:
  - PASS.
  - `BS 0x08000164`, `Run`, PC `0x08000164 -> main.c:62`, `BK 0`,
    final `BL` empty.
- `python -m py_compile src\core\keil\run_to_cursor.py src\core\keil\backend.py src\core\keil\__init__.py tools\keil_run_to_cursor_probe.py`
  - PASS.
- `python tools\keil_run_to_cursor_probe.py --json`
  - PASS.
  - Fake command order: `BL -> BS -> BL -> EVAL PC -> BK -> BL`.
- `python tools\keil_run_to_cursor_probe.py --live --keil-root D:\Keil --json`
  - PASS on connected ST-Link/F401.
  - Live temporary breakpoint id: `0`.
  - Hit PC: `0x08000164 / main.c:62`.
  - Cleanup command: `BK 0`.
  - Final cleanup snapshot contains no temporary breakpoint.
- Regression checks:
  - `python tools\keil_command_transaction_probe.py` PASS.
  - `python tools\keil_breakpoint_sync_probe.py` PASS.
  - `python tools\debug_backend_adapter_probe.py` PASS.
  - `python tools\debug_backend_registry_probe.py` PASS.
  - `python tools\debug_variable_access_probe.py` PASS.
  - `python tools\keil_backend_live_write_probe.py` PASS.
  - `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json`
    PASS with exit code `0`; live `debug_setpoint` write/readback still works.
- Keil/uVision cleanup check:
  - Closed the test-created UV4 session.
  - `Get-Process UV4,uVision` returned no remaining process.

### Notes

- This milestone intentionally does not add a visible UI button. The transaction
  is real but still needs more edge-case coverage before user-facing exposure.
- `run-to-cursor` does not reset the target by default. The live probe uses
  `reset_before_run=True` only to make the test deterministic.
- Address-only Keil `BL` output is still acceptable for transaction hygiene
  because the AXF/DWARF layer verifies the hit PC/source line.

### Next Target

- Add edge probes for run-to-cursor when a user breakpoint already exists at the
  target address, when timeout happens, and when cleanup must run after a failed
  PC verification.
- After those pass, expose run-to-cursor in UI as an explicit high-risk action
  tied to the current editor line.
- Then return to the acquisition-source model boundary so debugger polling and
  lightweight/serial scope can share the waveform UI safely.

## Milestone 63 - Run-to-Cursor Edge Guards

### Goal

Before exposing run-to-cursor in the UI, prove the transaction behaves safely
outside the happy path: it must not delete a user breakpoint, and it must clean
up temporary breakpoints after timeout or PC/source verification failure.

### Completed

- Extended `tools/keil_run_to_cursor_probe.py` with fake edge scenarios.
  - Normal temporary breakpoint path still verifies `BS -> Run -> PC -> BK`.
  - Existing user breakpoint at the target address is reused.
  - Timeout path fails the transaction, halts the target, then removes the temp
    breakpoint.
  - PC mismatch path fails the transaction and removes the temp breakpoint.
- Added fake session controls for:
  - pre-existing remote breakpoints,
  - no-hit timeout simulation,
  - mismatched PC address simulation.

### Verified

- `python -m py_compile tools\keil_run_to_cursor_probe.py src\core\keil\run_to_cursor.py`
  - PASS.
- `python tools\keil_run_to_cursor_probe.py --json`
  - PASS.
  - Existing-breakpoint scenario:
    - `used_existing_breakpoint=true`.
    - No `BK` command is sent.
    - Remote breakpoint id `7` remains.
  - Timeout scenario:
    - Transaction fails with `等待临时断点命中超时`.
    - `halt_after_timeout_summary=UVSOCK 暂停成功，目标已暂停`.
    - `cleanup_command=BK 0`.
    - Final breakpoint snapshot is empty.
  - PC mismatch scenario:
    - Transaction fails because PC maps to `main.c:63` instead of `main.c:62`.
    - `cleanup_command=BK 0`.
    - Final breakpoint snapshot is empty.

### Notes

- This stage still does not add a visible UI button. The backend transaction now
  has the minimum cleanup guard coverage needed before UI exposure.
- Live hardware coverage remains from Milestone 62; this stage is fake-edge
  coverage to make failure behavior deterministic.

### Next Target

- Expose `运行到光标` in the Debug Workbench as a high-risk explicit Keil action
  tied to the current editor line.
- The UI path must surface target source, resolved address, temporary
  breakpoint id, hit PC and cleanup result in diagnostics/history.

## Milestone 64 - Keil Run-to-Cursor UI Flow

### Goal

Move the already verified Keil run-to-cursor transaction from backend-only into
the Debug Workbench UI, while keeping it explicit, auditable, and honest about
PC evidence.

### Completed

- Added `run_to_cursor` to the debugger action model, backend-neutral dry-run
  transaction model, Keil dry-run transaction model, and debug session contract.
- Added a visible `到光标` button in the Debug Workbench action bar.
- Added `DebugWorkbenchTab.current_cursor_location()` and
  `show_source_location()` so execution code can use the active editor file and
  line without reaching through private UI details.
- Added `MainWindow._run_keil_to_cursor_from_workbench()`:
  - requires Keil backend,
  - requires target paused,
  - requires a valid source cursor line,
  - shows an explicit high-risk confirmation,
  - calls `KeilUvSockBackendAdapter.run_to_cursor()`,
  - surfaces target source, resolved address, temporary breakpoint id, hit PC,
    cleanup result and errors in diagnostics,
  - writes `keil_run_to_cursor` audit records,
  - updates the editor PC marker with verified backend PC evidence.
- Fixed a latent PC evidence UI bug so real `DebugPcLocation` values no longer
  crash source decoration refresh.
- Added `tools/ui_keil_run_to_cursor_probe.py` to simulate a user selecting a
  source line and triggering `到光标` through a fake Keil backend.

### Verified

- `python -m py_compile src\core\debug_workbench.py src\core\debug_transactions.py src\core\debug_session_contract.py src\core\keil\commands.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\ui_keil_run_to_cursor_probe.py`
  - PASS.
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.
  - Button enabled in paused Keil state.
  - Confirmation includes `main.c` and target line.
  - Fake backend receives the current editor line.
  - Diagnostics show `运行到光标=成功`, `目标地址=0x08000164`, `临时断点=0`, `PC 证据=已回读`.
  - Gutter tooltip shows verified current PC.
  - Audit log records `keil_run_to_cursor`.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-run-to-cursor --width 1440 --height 900`
  - PASS.
  - Screenshots:
    - `tools\ui-debug-workbench-run-to-cursor\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-run-to-cursor\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-run-to-cursor\03_debug_workbench_narrow.png`
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_session_contract_probe.py`
  - PASS.
- `python tools\debug_session_controller_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\keil_run_to_cursor_probe.py --json`
  - PASS.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.

### Mode Boundary Requirement

- LoopMaster must keep multiple acquisition/debug modes available:
  - original non/light-intrusive SWD variable oscilloscope,
  - serial assistant and VOFA-like serial waveform source,
  - Keil/UVSOCK debugger mode,
  - future OpenOCD/GDB and pyOCD modes,
  - future TI MSPM0G3507 adapter work under `D:\ti`.
- These modes should be selectable as source/debug backends, not forced into one
  crowded panel and not allowed to replace each other.
- The scope UI should accept data from SWD memory polling, serial frames, Keil
  Watch/variables, and later GDB/TI adapters through a shared acquisition-source
  boundary.

### Notes

- This stage adds UI exposure but still uses a fake UI probe for the button
  path. Live run-to-cursor hardware coverage remains from Milestone 62.
- The action is intentionally allowed only while the target is paused.
- The current command plan is still dry-run/audit-oriented. The actual action is
  guarded by a user confirmation before sending UVSOCK commands.

### Next Target

- Run a live F401 UI/manual smoke for `到光标` when a Keil Debug session is
  available, then tighten any status or diagnostic rough edges found there.
- Continue Keil basics before TI: visual breakpoint interaction, breakpoint
  validation feedback, current PC/run line polish, and a clearer debugger/scope
  source selector.
- After the Keil basics are usable, start the TI MSPM0G3507 research/adaptation
  notes from `D:\ti`, even without live TI hardware.

## Milestone 65 - Live F401 Run-to-Cursor Verification

### Goal

Prove that the newly exposed run-to-cursor path is backed by a real Keil/ST-Link
debug chain, not only by fake UI and transaction probes.

### Completed

- Confirmed no `UV4`/`uVision` process was running before the live smoke.
- Launched the F401 variable-probe Keil project through the existing auto-debug
  smoke path.
- Connected to the live Keil/UVSOCK debug session without writing variables.
- Ran the live run-to-cursor transaction against:
  - Project: `firmware\keil_f401_variable_probe\F401VariableProbe.uvprojx`
  - Target: `STM32F401CCU6 Variable Probe`
  - Source: `firmware\keil_f401_variable_probe\main.c`
  - Line: `62`
- Closed the test-created `UV4.exe` process and confirmed there was no
  `UV4`/`uVision` process left.

### Verified

- `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json --no-write`
  - PASS.
  - Started `UV4.exe` with PID `15788`.
  - Connected after 3 attempts.
  - No variable write was performed.
- `python tools\keil_run_to_cursor_probe.py --live --keil-root D:\Keil --json`
  - PASS.
  - Live target address: `0x08000164`.
  - Temporary breakpoint id: `0`.
  - Hit PC: `0x08000164 / main.c:62`.
  - Cleanup command: `BK 0`.
  - Cleanup snapshot contained no leaked temporary breakpoint.
- `Get-Process UV4,uVision`
  - Confirmed no remaining Keil/uVision process after cleanup.

### Notes

- This validates the real backend transaction. The UI path has already been
  covered by `tools\ui_keil_run_to_cursor_probe.py`; the remaining gap is a
  true end-to-end manual/UI smoke against the real window, which is riskier and
  should be done after the next UI feedback polish.
- The live probe uses `reset_before_run=True` for deterministic hardware state;
  the UI action keeps `reset_before_run=False` and requires a paused target.

### Next Target

- Tighten the visible breakpoint/PC feedback now that run-to-cursor is real:
  make the current line, verified PC, pending remote breakpoints, and failed
  breakpoint validation easier to scan.
- Then split debugger source/mode selection from scope acquisition source so
  original SWD scope, serial waveform, Keil Watch, OpenOCD/pyOCD, and future TI
  MSPM0G3507 can coexist without crowding the right-side panels.

## Milestone 66 - Debug Workbench Toolbar Responsiveness

### Goal

Clean up the Debug Workbench toolbar after adding `到光标`, especially in narrow
window widths where the action buttons and search box could feel crowded.

### Completed

- Grouped debug action buttons into logical clusters:
  - discovery/build/launch/debug,
  - attach/disconnect,
  - run-control,
  - breakpoints/write.
- Added subtle separators between action clusters.
- Shortened the first action label from `发现后端` to `发现`.
- Moved the search box and previous/next buttons to the second toolbar row so
  narrow windows no longer squeeze the query text.
- Moved action buttons to a full-width toolbar row so they do not overlap the
  project summary or each other.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-toolbar-responsive --width 1440 --height 900`
  - PASS.
  - Screenshots:
    - `tools\ui-debug-workbench-toolbar-responsive\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-toolbar-responsive\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-toolbar-responsive\03_debug_workbench_narrow.png`
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.

### Notes

- Visual review of the narrow screenshot confirmed the action row no longer
  overlaps and the search box keeps readable text.
- This is UI polish only; no backend behavior changed.

### Next Target

- Continue visual feedback polish in the editor itself: clearer current PC vs
  run line distinction, breakpoint verification states, and failed/pending
  validation readability.
- Then return to the source/acquisition selector boundary before starting the TI
  MSPM0G3507 adapter notes.

## Milestone 67 - Breakpoint Verification Badges

### Goal

Make local breakpoint verification state visible directly in the source editor
gutter instead of hiding it only in the breakpoint table or tooltip.

### Completed

- Added small gutter badges on breakpoint markers:
  - green dot for verified backend readback,
  - orange dot for failed/unverified backend feedback,
  - hollow gray dot for pending verification.
- Kept the existing breakpoint circle and conditional breakpoint diamond, so the
  new badge adds state without replacing the familiar marker.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-breakpoint-badges --width 1440 --height 900`
  - PASS.
  - Screenshots:
    - `tools\ui-debug-workbench-breakpoint-badges\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-breakpoint-badges\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-breakpoint-badges\03_debug_workbench_narrow.png`
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.

### Notes

- Visual review confirmed the failed/unverified state is now visible in the
  gutter, while the line number area remains readable.
- This is still deliberately subtle; the table remains the detailed source of
  exact verification messages.

### Next Target

- Build the source/acquisition selector boundary: keep debugger backend choice,
  source manifest provider, and waveform acquisition source separate enough that
  SWD, serial, Keil Watch, OpenOCD/pyOCD, and TI MSPM0G3507 can be added without
  crowding the same right-side options.

## Milestone 68 - Debug/Source/Scope Boundary Strip

### Goal

Make the Debug Workbench visibly distinguish three concepts that will keep
growing as more tools are added:

- Debug backend.
- Source manifest provider.
- Scope acquisition source.

### Completed

- Added a `链路边界` strip to the Debug Workbench navigation panel.
- The strip now shows:
  - `调试 ...` for backend/debugger selection,
  - `源码 ...` for source provider,
  - `示波 ...` for the active waveform acquisition source.
- Synced the strip from `MainWindow` so it reflects current SWD vs Keil Watch
  scope acquisition.
- Kept the strip vertical so it remains readable in the fixed-width sidebar.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py src\ui\gui.py tools\ui_debug_workbench_probe.py tools\ui_keil_watch_scope_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-boundary-final --width 1440 --height 900`
  - PASS.
  - The probe now asserts the boundary strip includes `调试 Keil`, `源码 Keil 工程`, and a `示波` source.
  - Screenshots:
    - `tools\ui-debug-workbench-boundary-final\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-boundary-final\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-boundary-final\03_debug_workbench_narrow.png`
- `python tools\ui_keil_watch_scope_probe.py`
  - PASS.
  - The probe now asserts the boundary strip switches to `示波 Keil Watch` after adding a Keil Watch preset to scope.
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.

### Notes

- This is a first visible boundary, not the final architecture. It prevents the
  current UI from implying that debug backend, source provider, and waveform
  data source are the same thing.
- Serial waveform and future OpenOCD/pyOCD/TI adapters still need to plug into
  this boundary through real source/acquisition models.

### Next Target

- Turn this visible boundary into a small model layer for acquisition sources:
  SWD memory, Keil Watch, serial waveform, and future OpenOCD/pyOCD/TI should
  advertise label, rate limits, write safety, and expected use cases.
- Then start TI MSPM0G3507 notes from `D:\ti` after Keil basics remain stable.

## Milestone 69 - Acquisition Source Capability Model

### Goal

Make debug/scope acquisition modes explicit and selectable without mixing them
into one crowded panel:

- Keep the original non/light-intrusive LoopMaster SWD variable scope path.
- Keep Keil/UVSOCK Watch as a debugger-backed low-frequency scope path.
- Keep serial waveform as an independent serial assistant route.
- Reserve honest planned entries for OpenOCD/GDB, pyOCD, and TI MSPM0G3507.

### Completed

- Added `src\core\acquisition_sources.py` with descriptors for:
  - `swd`
  - `keil_watch`
  - `serial_waveform`
  - `openocd_gdb`
  - `pyocd`
  - `ti_mspm0g3507`
- Each source now advertises:
  - label and short label,
  - active/ready/route-only/planned state,
  - transport,
  - intrusive level,
  - recommended and maximum sampling rate,
  - read/write capability,
  - intended use case and safety notes.
- Added a compact `示波采集来源` selector to the Debug Workbench boundary strip.
  - SWD and Keil Watch are directly selectable.
  - Serial waveform routes to the serial assistant page.
  - OpenOCD/GDB, pyOCD, and TI MSPM0G3507 are visible but disabled as planned
    entries, so the UI does not pretend they are live yet.
- Debug diagnostics now include acquisition source rows, so screenshots and
  probes can tell whether scope data is coming from SWD, Keil Watch, or another
  planned route.
- Normalized saved scope source configuration so unknown future values fall back
  to SWD instead of leaving the app in a mixed state.

### Verified

- `python -m py_compile src\core\acquisition_sources.py src\ui\debug_workbench_tab.py src\ui\gui.py tools\acquisition_sources_probe.py tools\ui_debug_workbench_probe.py tools\ui_keil_watch_scope_probe.py`
  - PASS.
- `python tools\acquisition_sources_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-acquisition-sources --width 1440 --height 900`
  - PASS.
  - Screenshots:
    - `tools\ui-debug-workbench-acquisition-sources\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-acquisition-sources\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-acquisition-sources\03_debug_workbench_narrow.png`
- `python tools\ui_keil_watch_scope_probe.py`
  - PASS.
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\debug_workbench_model_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.

### Notes

- The selector is intentionally an acquisition-source boundary, not a full
  transport manager yet. It prevents UI ambiguity while keeping the current
  SWD, Keil Watch, and serial paths separate.
- TI is recorded as the user-requested `MSPM0G3507` target, but it remains
  disabled until the Keil basics are stable and a real TI debug path is wired.

### Next Target

- Continue real Keil debugger basics before adding more architecture:
  - make breakpoint state evidence clearer after real sync/run-to-cursor,
  - keep variable write/readback strict,
  - then expose the minimum useful "breakpoint + run/halt + variable write"
    workflow as a coherent first live-debug loop.

## Milestone 70 - Breakpoint Evidence Diagnostics

### Goal

Make Keil breakpoint evidence visible enough that the UI does not merely say
"done"; it should show what was actually observed:

- whether the remote Keil breakpoint snapshot is complete,
- how many remote breakpoints were observed,
- whether run-to-cursor created a temporary breakpoint,
- whether that temporary breakpoint was cleaned up,
- whether PC was truly read back at the requested source line.

### Completed

- Extended `KeilRunToCursorResult.diagnostic_rows()` with:
  - running-before breakpoint count,
  - after-set breakpoint count,
  - after-cleanup breakpoint count,
  - temporary breakpoint leak status.
- Added Debug Workbench diagnostics for the current remote breakpoint snapshot:
  - snapshot id,
  - completeness,
  - breakpoint count,
  - snapshot error text when present.
- Updated probes so incomplete Keil BL/read-only snapshot states are surfaced as
  evidence instead of disappearing behind a generic status.

### Verified

- `python -m py_compile src\core\keil\run_to_cursor.py src\ui\gui.py tools\keil_run_to_cursor_probe.py tools\ui_keil_run_to_cursor_probe.py tools\ui_debug_workbench_probe.py tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\keil_run_to_cursor_probe.py --json`
  - PASS.
  - Fake temporary breakpoint created and cleaned.
  - Existing breakpoint reused without `BK`.
  - Timeout and PC mismatch paths both cleaned temporary breakpoints.
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-breakpoint-evidence --width 1440 --height 900`
  - PASS.
  - Screenshots:
    - `tools\ui-debug-workbench-breakpoint-evidence\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-breakpoint-evidence\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-breakpoint-evidence\03_debug_workbench_narrow.png`
- Real F401/Keil live smoke:
  - `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json --no-write`
    - PASS.
    - Launched Keil PID `40904`, connected in 3 attempts, did not write variables.
  - `python tools\keil_run_to_cursor_probe.py --live --keil-root D:\Keil --json`
    - PASS.
    - Target address `0x08000164`.
    - Temporary breakpoint id `0`.
    - Hit PC mapped to `main.c:62`.
    - Cleanup command `BK 0`.
    - After-cleanup snapshot contained 0 breakpoints.
  - Confirmed no `UV4/uVision` process remained after the live probe.

### Notes

- Keil `BL` can return address-only breakpoints; those snapshots are now shown
  as incomplete rather than treated as silently source-mapped.
- This improves trust in the first live-debug loop without changing the command
  execution path.

### Next Target

- Keep pushing the first useful live Keil workflow:
  - make breakpoint sync and run-to-cursor feel like one coherent user flow,
  - ensure run/halt/reset status always refreshes PC and remote breakpoint
    evidence,
  - then tighten the variable write path inside the same live session.

## Milestone 71 - Runtime Control Evidence Refresh

### Goal

Verify that Keil runtime-control actions refresh the visible evidence after the
button press, not just the target state:

- Halt should show a paused status, PC evidence, and remote breakpoint snapshot.
- Run should show running state and avoid pretending the PC is stable.
- Reset and step should return to paused state with fresh PC evidence.
- Diagnostics should use Chinese action labels consistently.

### Completed

- Added `tools\ui_keil_runtime_control_probe.py`.
  - Simulates a user clicking halt, run, reset, and step through the Debug
    Workbench.
  - Uses a fake Keil backend that returns a different PC location and remote
    breakpoint snapshot after each action.
  - Asserts that the UI updates:
    - status state,
    - runtime-control diagnostics,
    - PC evidence rows,
    - remote breakpoint evidence rows,
    - marker text.
- Changed runtime-control diagnostics to render `reset`, `step`, and
  `step_over` as `复位`, `单步`, and `跨过`.

### Verified

- `python -m py_compile src\ui\gui.py tools\ui_keil_runtime_control_probe.py`
  - PASS.
- `python tools\ui_keil_runtime_control_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-runtime-control --width 1440 --height 900`
  - PASS.
  - Screenshots:
    - `tools\ui-debug-workbench-runtime-control\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-runtime-control\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-runtime-control\03_debug_workbench_narrow.png`
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- Real F401/Keil live PC-control smoke:
  - `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json --no-write`
    - PASS.
    - Launched Keil PID `78272`, connected in 3 attempts, did not write variables.
  - `python tools\keil_pc_location_probe.py --live --step --step-over --reset --keil-root D:\Keil --json`
    - PASS.
    - Initial PC readback mapped to `main.c:24`.
    - Step mapped to `main.c:25`.
    - Step-over mapped to `main.c:26`.
    - Reset mapped to `startup_stm32f401ccux.s:41`.
  - Confirmed no `UV4/uVision` process remained after the live probe.

### Notes

- The existing UI path already applied backend snapshots after runtime control.
  This milestone locks that behavior with a UI probe and fixes the remaining
  English action labels in diagnostics.

### Next Target

- Move from evidence visibility to a more usable live-debug workflow:
  - add a clearer "first live loop" path in the Debug Workbench,
  - keep breakpoint sync, run-to-cursor, run/halt, and variable write visibly
    connected,
  - then retest live variable write in the same Keil session.

## Milestone 72 - Remote Snapshot Verifies Local Breakpoints

### Goal

When Keil returns a complete remote breakpoint snapshot, local breakpoint markers
should immediately reflect that evidence. The user should not need to run a
separate sync action just to see that a breakpoint already exists in Keil.

### Completed

- Added `_mark_local_breakpoints_from_remote_snapshot()`.
  - Applies only complete remote snapshots.
  - Marks matching local file/line breakpoints as verified.
  - Preserves incomplete snapshots as evidence only; it does not mark local
    breakpoints failed just because Keil returned an incomplete or address-only
    list.
- Wired the helper into:
  - backend discovery snapshots,
  - read-only attach snapshots,
  - runtime-control snapshots,
  - run-to-cursor cleanup snapshots,
  - direct test/integration snapshot injection.
- Extended `tools\ui_keil_runtime_control_probe.py` so a local breakpoint starts
  unverified and becomes verified after the fake runtime snapshot reports the
  same file/line from Keil.

### Verified

- `python -m py_compile src\ui\gui.py tools\ui_keil_runtime_control_probe.py`
  - PASS.
- `python tools\ui_keil_runtime_control_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-remote-breakpoint-verify --width 1440 --height 900`
  - PASS.
  - Screenshots:
    - `tools\ui-debug-workbench-remote-breakpoint-verify\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-remote-breakpoint-verify\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-remote-breakpoint-verify\03_debug_workbench_narrow.png`
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.

### Notes

- The helper intentionally does not treat "missing from snapshot" as failure.
  That is safer while Keil `BL` can return incomplete or address-only entries.
- This makes the UI feel more live: remote evidence now updates gutter/breakpoint
  verification state as soon as it arrives.

### Next Target

- Tighten the first live-debug loop around variable write:
  - retest strict baseline-read/write/readback on F401,
  - make the UI surface the latest successful write/read as part of the same
    live session evidence,
  - then decide whether the next visible feature should be a compact "live loop"
    checklist or OpenOCD/pyOCD adapter work.

## Milestone 73 - Live Keil Variable Write Recheck

### Goal

Reconfirm that the first real Keil value-change path is working on hardware,
because this is the minimum useful live-debug feature the user asked to see
before more adapter architecture is added.

### Verified

- Confirmed no `UV4/uVision` process was running before the test.
- `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --json`
  - PASS.
  - Launched Keil PID `76932`.
  - Connected in 5 attempts.
  - Target/project guard matched `STM32F401`.
  - AXF existed and build was skipped.
  - Strict write path used memory/DWARF resolution, not blind command fallback.
  - Variable: `debug_setpoint`.
  - Address: `0x20000008`.
  - Write-before baseline read: `1000`.
  - Written value: `6000`.
  - Write-after readback: `6000`.
  - Result: `Keil 写变量已回读：debug_setpoint @ 0x20000008 = 6000 (memory)`.
- Confirmed no `UV4/uVision` process remained after the test.

### Notes

- This confirms the current Keil path can already do the core user-requested
  operation: connect to Keil, resolve a variable from AXF/DWARF, read baseline,
  write RAM, and verify readback.
- No code change was needed in this milestone; it is a hardware-backed checkpoint
  to prevent later UI/adapter work from drifting away from the real function.

### Next Target

- Add a compact first-live-loop surface in the Debug Workbench so the user can
  see the real workflow as one chain:
  - Keil connected,
  - PC evidence,
  - breakpoint evidence,
  - latest variable baseline/write/readback,
  - active scope acquisition source.

## Milestone 74 - Live Loop Status Strip

### Goal

Make the first Keil live-debug loop visible as one compact chain instead of
forcing the user to read through the full diagnostics table:

- session state,
- PC evidence,
- breakpoint verification,
- latest variable write/readback,
- active scope acquisition source.

### Completed

- Added a `实时闭环` strip to the Debug Workbench navigation panel.
- The strip shows five compact status chips:
  - `会话`
  - `PC`
  - `断点`
  - `写入`
  - `示波`
- Synced the strip from `MainWindow` whenever diagnostics/status evidence
  refreshes.
- The strip derives its values from real state:
  - current debug session status,
  - current PC evidence,
  - local breakpoint verified/pending/failed counts plus remote snapshot summary,
  - latest strict baseline-read/write/readback result,
  - active acquisition source descriptor.
- Kept details in tooltips and full diagnostics, so the strip stays compact.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py src\ui\gui.py tools\ui_debug_workbench_probe.py tools\ui_keil_runtime_control_probe.py`
  - PASS.
- `python tools\ui_keil_runtime_control_probe.py`
  - PASS.
  - Verifies halt updates `会话 已暂停`, `PC 已回读`, and `断点 1/1`.
  - Verifies run updates `会话 运行中` and `PC 未验证`.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-live-loop-strip --width 1440 --height 900`
  - PASS.
  - Screenshots:
    - `tools\ui-debug-workbench-live-loop-strip\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-live-loop-strip\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-live-loop-strip\03_debug_workbench_narrow.png`
- `python tools\ui_keil_watch_scope_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.

### Notes

- Visual review of the screenshot confirmed the strip fits in the left scroll
  panel without taking space from the source editor.
- This is a UI consolidation of evidence already produced by the Keil live
  path; it does not change target-side behavior.

### Next Target

- Start preparing the next adapter layer after the Keil basics:
  - keep Keil as the reference implementation,
  - sketch the shared operations OpenOCD/pyOCD/TI need to implement,
  - begin with a no-process OpenOCD/GDB adapter skeleton or TI MSPM0G3507
    metadata, depending on which unlocks the next real feature fastest.

## Milestone 75 - Debug Toolchain Metadata and TI MSPM0 Boundary

### Goal

Make the debugger expansion path explicit without pretending every backend is
already live. Keil stays the first real reference implementation; OpenOCD,
pyOCD, TI MSPM0G3507, and offline replay now have shared metadata, lifecycle
keys, safety boundaries, and UI-visible diagnostics.

### Requirement Recorded

- 调试及示波必须保留多种可选链路：
  - 原 LoopMaster `非/轻侵入式` SWD 变量示波，
  - 串口助手/VOFA-style 主动上报示波，
  - Keil / UVSOCK 调试器主控模式，
  - 后续 OpenOCD、pyOCD、TI MSPM0G3507 等 OCD/debugger 模式。
- 示波不绑定死在某一个调试器上；调试器链路也可以作为低频示波/变量观察来源。
- 每个新后端必须先声明：
  - 是否真实接入，
  - 支持哪些操作，
  - 目标 MCU/协议边界，
  - 是否允许连接探针或写目标。

### Completed

- Added `src/core/debug_toolchains.py`.
  - Describes Keil / UVSOCK as the live reference backend.
  - Describes OpenOCD / GDB and pyOCD as safe placeholders.
  - Describes TI MSPM0G3507 as a planned priority target backed by `D:\ti`
    material, with no probe connection or target writes until real hardware
    support exists.
  - Describes offline replay as a future no-hardware mode.
- Registered `ti_mspm0` in the debug backend kind/session contract.
- Extended backend registry placeholder diagnostics with toolchain phase,
  protocol, target scope, executable hint, operation list, safety boundary, and
  next action.
- Updated Debug Workbench labels so TI MSPM0G3507 appears as a selectable
  backend entry without enabling dangerous actions.
- Strengthened the UI probe:
  - backend selector must include TI MSPM0G3507,
  - switching to TI must update the main window backend,
  - diagnostics must report `计划中`, `MSPM0G3507`, and `不连接探针/不写目标`,
  - halt/run/reset/step/run-to-cursor/breakpoint sync/write buttons must stay
    disabled for the placeholder.

### Verified

- `python -m py_compile src\core\debug_toolchains.py src\core\debug_workbench.py src\core\debug_session_contract.py src\core\debug_backend_registry.py src\ui\debug_workbench_tab.py tools\debug_toolchains_probe.py tools\debug_backend_registry_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\debug_toolchains_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python tools\debug_session_contract_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-toolchains --width 1440 --height 900`
  - PASS.
  - Screenshots:
    - `tools\ui-debug-workbench-toolchains\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-toolchains\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-toolchains\03_debug_workbench_narrow.png`
- `python tools\debug_session_controller_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.

### Notes

- This milestone intentionally does not launch OpenOCD, pyOCD, TI tools, or
  extra probe sessions. It records the expansion contract and prevents unsafe UI
  actions from becoming clickable before a backend is real.
- The user specifically identified TI MSPM0G3507 as important after Keil
  breakpoints and basic debug use are complete.

### Next Target

- Continue with real Keil debugger basics first:
  - make breakpoint operations more complete from the UI,
  - keep variable write/read evidence in the same loop,
  - then use Keil as the template for OpenOCD/GDB and TI MSPM0G3507 adapters.
- Start reading `D:\ti` after the Keil breakpoint path is stable enough, to
  build an MSPM0G3507 profile and decide whether TI should route through a
  vendor tool, GDB bridge, OpenOCD-like path, or pyOCD-compatible path.

## Milestone 76 - Keil Breakpoint Sync Plan Strip

### Goal

Make Keil breakpoint synchronization understandable before the user presses the
real sync button. The workbench already had local breakpoint editing, remote
snapshot evidence, and guarded sync execution; this stage adds a compact visual
summary of the planned diff.

### Completed

- Added a `同步计划` strip above the local breakpoint table.
- The strip shows:
  - sync mode: `完整差分` or `推送本地`,
  - planned operations: add/remove/enable/disable/condition counts,
  - verification state: verified/unverified/pending plus limited operations.
- The strip is driven by the same `sync_breakpoints` transaction summary used by
  the confirmation dialog and audit tooltip, so it does not invent a separate
  breakpoint model.
- Tuned the wording from `无效` to `受限` for UI display because current Keil
  condition-breakpoint operations are deliberately blocked until command
  semantics are fully verified.
- Extended the UI probe to capture a dedicated local screenshot of the strip:
  - `tools\ui-debug-workbench-breakpoint-sync-strip\04_debug_breakpoint_sync_strip.png`

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py tools\ui_debug_workbench_probe.py tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-breakpoint-sync-strip --width 1440 --height 900`
  - PASS.
  - Screenshots:
    - `tools\ui-debug-workbench-breakpoint-sync-strip\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-breakpoint-sync-strip\02_debug_workbench_decorations.png`
    - `tools\ui-debug-workbench-breakpoint-sync-strip\03_debug_workbench_narrow.png`
    - `tools\ui-debug-workbench-breakpoint-sync-strip\04_debug_breakpoint_sync_strip.png`
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.

### Notes

- Visual check of the local strip screenshot confirmed the compact chip layout
  remains readable and does not add another large panel.
- This stage is still UI/transaction evidence only; it does not alter how Keil
  UVSOCK commands are sent.

### Next Target

- Continue Keil breakpoint basics by tightening the real sync operation around:
  - condition-breakpoint limitations,
  - remote breakpoint readback evidence after sync,
  - clearer recovery when Keil accepts a command but `BL` cannot map it back to
    source.

## Milestone 77 - Breakpoint Sync Limited-State Semantics

### Goal

Make Keil breakpoint sync results honest when some operations are intentionally
not sent. Conditional source-line breakpoints are still blocked until the exact
Keil command semantics are fully verified; they should not make successful
safe commands look like a hard Keil failure.

### Completed

- Split `KeilBreakpointSyncResult` into clearer result states:
  - `成功`: all sent commands succeeded and nothing was skipped,
  - `部分完成`: sent commands succeeded, but some limited operations were not
    sent,
  - `受限未执行`: every requested operation was limited and no command was sent,
  - `失败`: an attempted Keil command failed or the sync raised an error.
- Added result fields and audit data:
  - `completed`,
  - `partial`,
  - `blocked_by_limits`,
  - `skipped_count`,
  - `noop_count`,
  - `limited_reasons`,
  - `status`.
- Diagnostics now include:
  - `断点受限`,
  - `断点受限原因`,
  - `断点无变化` when no-op operations are present.
- Updated the Keil sync UI flow:
  - partial completion is shown as a warning, not a full failure,
  - fully limited batches show `Keil 断点同步受限未执行`,
  - audit/history event remains `executed` only when at least some safe work was
    completed.
- The confirmation dialog now lists limited operations with file, line, action,
  and reason before sending anything to Keil.

### Verified

- `python -m py_compile src\core\keil\breakpoint_sync.py src\ui\gui.py tools\keil_breakpoint_sync_probe.py tools\ui_keil_breakpoint_sync_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS.
  - Verifies partial completion when a conditional add is skipped but safe
    commands are sent.
  - Verifies `受限未执行` when all operations are limited.
  - Verifies real attempted command failures still report `失败`.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
  - Verifies confirmation text includes the limited operation reason.
  - Verifies diagnostics and warning title for `受限未执行`.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-breakpoint-sync-states --width 1440 --height 900`
  - PASS.
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\debug_transaction_shell_probe.py`
  - PASS.

### Notes

- This stage changes sync result semantics and UI truthfulness only. It still
  does not enable automatic condition-breakpoint writes.
- The current Keil path is now safer for real use: a user can see exactly which
  breakpoint actions will be sent, which are limited, and whether Keil itself
  failed.

### Next Target

- Continue with real Keil breakpoint readback:
  - refresh `BL` after sync when possible,
  - map remote breakpoint IDs and addresses back into local verification,
  - then run a live F401 breakpoint sync smoke that does not leave residual
    breakpoints behind.

## Milestone 78 - Live F401 Keil Breakpoint Sync Smoke

### Goal

Prove the normal Keil breakpoint sync path can set a real breakpoint, read it
back from Keil, map it to source, and clean it up without leaving residual
breakpoints or UV4 processes.

### Completed

- Added `tools\keil_breakpoint_sync_live_probe.py`.
  - Uses the same `KeilUvSockBackendAdapter.sync_breakpoints()` path as the
    Debug Workbench.
  - Dry-run mode resolves the target source line/address without touching
    hardware.
  - `--execute` mode first requires a complete read-only `BL` snapshot before
    setting anything.
  - Refuses to touch the target if a matching user breakpoint already exists.
  - Adds one breakpoint, identifies the new remote id by comparing before/after
    snapshots, deletes only that new remote breakpoint, and verifies no leak.
  - Uses a hard exit after `--execute` cleanup to avoid UVSOCK/Python teardown
    access violations while preserving the real probe exit code.

### Verified

- `python -m py_compile tools\keil_breakpoint_sync_live_probe.py`
  - PASS.
- `python tools\keil_breakpoint_sync_live_probe.py --json`
  - PASS.
  - Dry-run only.
  - Resolved F401 test line to `0x08000164`.
- `python tools\keil_backend_breakpoint_list_probe.py`
  - PASS.
- `python tools\keil_breakpoint_list_probe.py`
  - PASS.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS.
- Live setup:
  - `python tools\keil_auto_debug_smoke.py --keil-root D:\Keil --expected-device STM32F401 --execute --no-write --json`
  - PASS.
  - Launched Keil PID `40948`.
  - Connected in 4 attempts.
- Live breakpoint sync:
  - `python tools\keil_breakpoint_sync_live_probe.py --keil-root D:\Keil --execute --json`
  - PASS.
  - `LIVE_LASTEXIT=0`.
  - Before snapshot: complete, 0 breakpoints.
  - Add command: `BS 0x08000164`.
  - Add result: success, 1/1 command succeeded.
  - Remote readback: id `0`, address `0x08000164`, mapped to `main.c:62`.
  - Cleanup command: `BK 0`.
  - Cleanup result: success, 1/1 command succeeded.
  - After cleanup: complete, 0 breakpoints.
  - Leak check: `[]`.
- Process cleanup:
  - Closed/killed test-launched UV4 after the smoke.
  - Final process check: `NO_UVISION_PROCESS`.

### Notes

- The first live run completed and cleaned the breakpoint but reported a Windows
  access-violation exit code during interpreter teardown. The live probe now
  hard-exits only after cleanup in `--execute` mode, and the repeated live run
  returned `LIVE_LASTEXIT=0`.
- This is the first full Keil breakpoint sync proof, separate from the earlier
  run-to-cursor temporary-breakpoint proof.

### Next Target

- Pull the live sync evidence into the Debug Workbench UX:
  - after a successful sync, show the remote id/address/source mapping in the
    local breakpoint verification message,
  - keep the compact sync strip and live-loop strip aligned with the latest
    remote snapshot,
  - then move toward the first OpenOCD/GDB no-process profile or TI MSPM0G3507
    research pass.

## Milestone 79 - Remote Breakpoint Evidence in Local Verification

### Goal

Surface the evidence proven by the live F401 breakpoint sync smoke directly in
the Debug Workbench. When Keil returns a remote breakpoint id, address, or
mapped source location, the local breakpoint row should show that proof instead
of a generic "already read back" message.

### Completed

- Updated local breakpoint verification after sync:
  - complete remote snapshots now map by source path/line,
  - address-only readback can still verify a local breakpoint when the command
    resolved an address,
  - verification messages now include remote id, address, and raw mapped
    location when available.
- Changed completion logic to use `result.completed`, so partial-but-safe sync
  can still mark the breakpoints that Keil actually read back.
- Extended `tools\ui_keil_breakpoint_sync_probe.py`:
  - fake backend now returns a complete remote snapshot for full-diff sync,
  - probe asserts the local breakpoint verification message contains remote id
    evidence (`id 11`).

### Verified

- `python -m py_compile src\ui\gui.py tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-breakpoint-evidence --width 1440 --height 900`
  - PASS.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\keil_backend_breakpoint_list_probe.py`
  - PASS.
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.

### Notes

- This is the UI counterpart to Milestone 78. The live path proved that Keil can
  return `id 0` and `0x08000164`; this stage ensures those kinds of facts are
  not hidden behind generic success text.

### Next Target

- Move to the next expansion slice:
  - either start an OpenOCD/GDB no-process profile and command-preview adapter,
  - or read `D:\ti` to build the first MSPM0G3507 debugger profile plan.
  Keil remains the reference implementation for real variable writes,
  breakpoint sync, run-to-cursor, and PC/runtime evidence.

## Milestone 80 - TI MSPM0G3507 Local Toolchain Profile

### Goal

Turn the local `D:\ti` installation into structured, testable debugger metadata
without touching TI hardware. This keeps the future TI adapter grounded in real
CCS/SDK/targetdb facts while Keil remains the live reference backend.

### Completed

- Added a read-only TI MSPM0G3507 profile module:
  - discovers CCS, SDK, SysConfig, DebugServer/DSLite, GDB agent, DSS, and
    TI ARM LLVM compiler paths,
  - parses `MSPM0G3507.xml` for Cortex-M0+, TICLANG, XDS110, AP designator,
    GEL files, compiler/linker options, startup file, and key peripheral bases,
  - parses `mspm0g3507.cmd` for FLASH, SRAM, BCR, and BSL linker ranges,
  - parses the SDK SysConfig example for device/package/spin identity.
- Added `tools\ti_mspm0_profile_probe.py`:
  - asserts exact MSPM0G3507 identity,
  - verifies the local TI tool executables exist,
  - verifies FLASH `0x00000000..0x0001FFFF`,
  - verifies linker SRAM `0x20200000..0x20207FFF`,
  - verifies targetdb SYSMEM base `0x20000000`,
  - verifies the warning that linker SRAM and targetdb SYSMEM use different
    bases and must be checked before live variable writes.
- Updated the debugger toolchain descriptor:
  - TI is now described as CCS DebugServer/XDS110/MSPM0G3507 metadata already
    identified locally,
  - still explicitly safe: no probe enumeration, no target attach, no write.
- Recorded the user requirement that debug/scope data sources must stay
  selectable:
  - original non/light-intrusive memory sampling,
  - serial/VOFA-style waveform,
  - Keil/UVSOCK debugger mode,
  - future OpenOCD/GDB, pyOCD, and TI MSPM0G3507 modes.

### Verified

- `python -m py_compile src\core\ti_mspm0\profile.py src\core\debug_toolchains.py tools\ti_mspm0_profile_probe.py tools\debug_toolchains_probe.py`
  - PASS.
- `python tools\debug_toolchains_probe.py`
  - PASS.
- `python tools\ti_mspm0_profile_probe.py --json`
  - PASS.
  - Device: `MSPM0G3507`.
  - CPU: `Cortex M0+ CPU`.
  - Connection: `TIXDS110_Connection.xml`.
  - FLASH: `0x00000000-0x0001FFFF`.
  - SRAM: `0x20200000-0x20207FFF`.
  - targetdb SYSMEM base: `0x20000000`.
  - Missing TI tools: none.

### Notes

- This milestone is intentionally no-hardware and no-process. It does not launch
  CCS, DSLite, GDB agent, or DSS.
- The SRAM base mismatch is not treated as failure; it is surfaced as a safety
  warning so live TI variable access must resolve the address alias before any
  write is allowed.

### Next Target

- Continue the Keil basics first:
  - make breakpoint operations and remote evidence easier to use from the UI,
  - keep variable write/read and Halt/Run/Step paths verified,
  - then add a shared debugger/scope source selector so debugger-backed waveforms
    and non-intrusive/serial waveforms can coexist cleanly.
- After Keil basics are solid, start no-process OpenOCD/GDB command preview and
  TI MSPM0G3507 command/profile preview, with hardware writes still gated.

## Milestone 81 - Keil Remote Breakpoint Mirror in Debug Workbench

### Goal

Make Keil breakpoint sync feel like a real debugger workflow in the UI: users
should see not only the local visual breakpoints and the sync plan, but also the
latest remote breakpoint evidence read back from Keil.

### Completed

- Added a compact `Keil 远端` mirror to the Debug Workbench:
  - shows remote snapshot completeness,
  - shows remote breakpoint count,
  - lists remote id, file, line/address, enabled state, and a short raw location,
  - keeps full evidence in tooltips so long Windows paths do not make the UI
    look broken.
- Added `DebugWorkbenchTab.set_remote_breakpoint_snapshot()` and connected it
  from `MainWindow._sync_debug_command_preview()` and the explicit setter path.
- Updated the UI probes:
  - the full Debug Workbench screenshot probe now asserts the remote mirror
    chips and table rows,
  - the Keil breakpoint sync probe now asserts the remote mirror displays id
    `11` after a complete remote snapshot.
- Reworked the screenshot probe helper so full-diff breakpoint tests use the
  new workbench remote snapshot field instead of the old internal placeholder.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py src\ui\gui.py tools\ui_debug_workbench_probe.py tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-remote-breakpoints --width 1440 --height 900`
  - PASS.
  - Screenshot checked manually:
    - `tools\ui-debug-workbench-remote-breakpoints\05_debug_remote_breakpoint_mirror.png`
    - The mirror shows `快照 完整`, `远端 5`, `bp-1`, `main.c`, line `3`, and
      `main.c:3` without the ugly truncated full path.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\keil_backend_breakpoint_list_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\ti_mspm0_profile_probe.py --json`
  - PASS.

### Notes

- This stage is UI/evidence work. It does not send new Keil commands beyond the
  existing explicit breakpoint sync path.
- During probe iteration, a real state-flow issue was clarified: quick local
  breakpoint edits trigger command preview regeneration, so tests that simulate
  a complete remote snapshot must update both the workbench and the main window
  remote snapshot source.

### Next Target

- Continue Keil basics:
  - add a clearer manual refresh path for remote breakpoint snapshots,
  - keep Halt/Run/Step/Run-to-cursor evidence visible beside PC and breakpoints,
  - then move debugger-backed watch/scope into the shared acquisition selector.

## Milestone 82 - Remote Breakpoint Snapshot Refresh

### Goal

Let users explicitly refresh the Keil remote breakpoint mirror without hunting
for the generic connect action. The action must stay read-only and use the
existing safe snapshot path.

### Completed

- Added a `刷新` button in the `Keil 远端` breakpoint mirror.
- Added `remoteBreakpointRefreshRequested` from the Debug Workbench to the main
  window.
- Wired the refresh button to the existing read-only Keil snapshot flow:
  - reads target/session state,
  - reads remote breakpoint snapshot evidence,
  - does not send breakpoint mutation commands,
  - still validates backend kind and executor availability.
- Extended the full UI probe:
  - clicks the remote refresh button,
  - asserts the fake backend receives a second read-only snapshot request,
  - asserts incomplete remote evidence is shown as `快照 未完整 / 远端 0`,
  - later re-injects a complete snapshot and verifies full-diff breakpoint
    planning continues to work.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py src\ui\gui.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-remote-refresh --width 1440 --height 900`
  - PASS.
  - Screenshot checked manually:
    - `tools\ui-debug-workbench-remote-refresh\05_debug_remote_breakpoint_mirror.png`
    - The `刷新` button fits beside `快照 完整` and `远端 5` without crowding.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `git diff --check`
  - PASS.

### Notes

- This gives users a clear "read Keil again" affordance while keeping the sync
  button reserved for actual breakpoint mutations.

### Next Target

- Continue Keil basics by improving runtime action evidence:
  - show the latest Halt/Run/Step/Run-to-cursor result in the live-loop strip and
    diagnostics,
  - make PC evidence and target state transitions easier to verify from the UI,
  - then wire debugger-backed watch/scope into the shared acquisition selector.

## Milestone 83 - Keil Runtime Control Evidence

### Goal

Make Halt/Run runtime actions leave clear, inspectable UI evidence instead of
only changing the target state label.

### Completed

- Added `_debug_last_runtime_control_result` to the main window state.
- Runtime actions now publish their latest result after execution:
  - live-loop `会话` chip includes the latest action label, such as `暂停` or
    `运行`,
  - diagnostics include action, result, target running state, UVSC status, and a
    human summary.
- Kept the existing PC evidence path:
  - Halt can surface a verified PC marker when the backend snapshot contains PC
    evidence,
  - Run moves target state back to running and clears verified PC evidence unless
    the backend provides one.
- Added `tools\ui_keil_runtime_control_probe.py`:
  - fake backend runs Halt then Run,
  - asserts confirmation dialogs are requested,
  - asserts backend calls happen in order,
  - asserts diagnostics and live-loop chip show the latest action,
  - asserts Halt publishes verified PC evidence.

### Verified

- `python -m py_compile src\ui\gui.py tools\ui_keil_runtime_control_probe.py`
  - PASS.
- `python tools\ui_keil_runtime_control_probe.py`
  - PASS.
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-runtime-evidence --width 1440 --height 900`
  - PASS.
- `git diff --check`
  - PASS.

### Notes

- This is still UI/evidence work around the existing real Keil runtime control
  executor. It does not change the UVSOCK Halt/Run commands themselves.

### Next Target

- Continue toward the shared acquisition/source architecture:
  - make debugger-backed watch/scope source selection more explicit,
  - keep original light-intrusive SWD and serial waveform modes selectable,
  - prepare OpenOCD/pyOCD/TI adapters to feed the same variable/scope interface.

## Milestone 84 - Acquisition Source Capability Boundary

### Goal

Lock in the user's refined requirement that debug and waveform modes remain
selectable and explicit: original non/light-intrusive SWD scope, serial
VOFA-style waveform, Keil Watch, and future OpenOCD/pyOCD/TI debugger-backed
sources must all feed the same scope concept without pretending they have the
same risk, speed, or control surface.

### Completed

- Extended `src\core\acquisition_sources.py` from simple source labels into a
  capability contract:
  - source mode: `非/轻侵入式`, `调试器链路`, or `主动上报流`,
  - waveform read support,
  - variable read/write support,
  - breakpoint/runtime/source-visualization support,
  - whether the source owns or takes over the debug session.
- Kept the important product boundary:
  - `SWD 内存` remains the default light acquisition source and does not take
    over the debug chain,
  - `串口波形` remains a route to the serial assistant waveform page and does
    not change the main SWD/Keil source,
  - `Keil Watch` is marked as debugger-backed, low-rate waveform capable, and
    tied to breakpoint/runtime/source visualization capability,
  - OpenOCD/GDB, pyOCD, and TI MSPM0G3507 are visible as planned
    debugger-backed acquisition sources but remain disabled until real adapters
    are implemented.
- Added debugger-backend-to-acquisition-source mapping helpers so future
  adapters can advertise their waveform/variable source without hard-coding UI
  branches.
- Debug Workbench diagnostics now include the acquisition mode and capability
  rows, making screenshots and probes show when a source will or will not take
  over the debug chain.
- Updated `docs\debug_workbench_plan.md` with the stricter rule that source
  selection and debugger selection are related but not the same switch.

### Verified

- `python -m py_compile src\core\acquisition_sources.py src\ui\gui.py src\ui\debug_workbench_tab.py tools\acquisition_sources_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\acquisition_sources_probe.py`
  - PASS.
- `python tools\debug_toolchains_probe.py`
  - PASS.
- `python tools\debug_backend_registry_probe.py`
  - PASS.
- `python -m py_compile src\core\acquisition_sources.py tools\acquisition_sources_probe.py tools\ui_debug_workbench_probe.py tools\ui_keil_watch_scope_probe.py`
  - PASS.
- `python tools\acquisition_sources_probe.py`
  - PASS.
- `python tools\ui_keil_watch_scope_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-acquisition-capabilities-final --width 1440 --height 900`
  - PASS.
  - Screenshots checked manually:
    - `tools\ui-debug-workbench-acquisition-capabilities-final\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-acquisition-capabilities-final\04_debug_breakpoint_sync_strip.png`
    - `tools\ui-debug-workbench-acquisition-capabilities-final\05_debug_remote_breakpoint_mirror.png`

### Notes

- This stage is architecture and UI evidence work. It does not start Keil,
  OpenOCD, pyOCD, TI tools, or hardware probes.
- The Keil Watch probe still confirms that adding a preset to waveform switches
  the active source to `Keil Watch`, clamps the low-rate backend, and disconnects
  cleanly on close.

### Next Target

- Continue Keil basics with real breakpoint usability:
  - keep remote breakpoint refresh/sync evidence visible,
  - expose the safest user-facing breakpoint actions around the current source
    view,
  - then start wiring debugger-backed variable/watch samples into a shared
    acquisition session interface instead of only source descriptors.

## Milestone 85 - Keil Breakpoint Usability Signals

### Goal

Make the existing Keil breakpoint foundation easier to understand during real
use. The user should not have to guess whether a red gutter marker is only local,
waiting for Keil readback, or already verified by the remote debugger.

### Completed

- Renamed the top action button from `断点` to `同步断点`, keeping the action
  explicit and harder to confuse with merely toggling a local gutter marker.
- Added a selected-breakpoint status chip to the quick editor:
  - `待同步` for a local breakpoint that has not been read back,
  - `待回读` after commands were accepted but Keil breakpoint list readback is
    still incomplete,
  - `未验证` when the backend reports a verification issue,
  - `已验证` when a remote snapshot confirms the breakpoint.
- Fed the current breakpoint diff summary into the sync button tooltip:
  - full-diff vs push-local mode,
  - add/remove/enable/disable/condition counts,
  - verification counts,
  - remote snapshot incompleteness and limited-operation warnings.
- Kept all real sync behavior unchanged:
  - no new Keil commands,
  - no automatic sync,
  - existing confirmation dialog and audit log remain the execution boundary.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py tools\ui_keil_breakpoint_sync_probe.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-breakpoint-usability --width 1440 --height 900`
  - PASS.
  - Screenshots checked manually:
    - `tools\ui-debug-workbench-breakpoint-usability\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-breakpoint-usability\04_debug_breakpoint_sync_strip.png`
- Regression probes:
  - `python tools\ui_keil_runtime_control_probe.py`
    - PASS.
  - `python tools\ui_keil_run_to_cursor_probe.py`
    - PASS.
  - `python tools\ui_keil_watch_scope_probe.py`
    - PASS.
  - `python tools\debug_backend_adapter_probe.py`
    - PASS.
  - `python tools\debug_session_controller_probe.py`
    - PASS.
  - `python tools\acquisition_sources_probe.py`
    - PASS.
  - `python tools\ti_mspm0_profile_probe.py --json`
    - PASS.

### Notes

- This stage improves the breakpoint UX around the already-existing Keil sync
  executor. It does not start Keil, OpenOCD, pyOCD, TI tools, or touch hardware.
- The UI now makes the local/remote distinction visible at the exact place the
  user edits a breakpoint, which should reduce mistakes during real debugging.

### Next Target

- Continue Keil basics by making breakpoint workflows closer to a modern IDE:
  - add a clearer current-line breakpoint affordance,
  - keep run-to-cursor and regular breakpoint evidence separate,
  - then move Keil Watch samples toward the shared acquisition-session runtime.

## Milestone 86 - Current-Line Breakpoint Affordance

### Goal

Make source-level breakpoint editing feel less like a hidden gutter-only action.
Users should be able to place or remove a breakpoint on the current cursor line
without aiming at the gutter, while keeping condition editing as a separate,
explicit action.

### Completed

- Added a `当前行断点` button to the source editor header.
- The button follows cursor context:
  - enabled only when a source file is loaded,
  - shows `当前行断点` when the current line has no local breakpoint,
  - shows `移除断点` when the current line already has a local breakpoint,
  - tooltip includes the current line number and action.
- Kept `当前行条件` as a separate condition editor:
  - it creates/selects the current line breakpoint when needed,
  - its tooltip shows the current condition when one exists.
- Current-line actions now disable together when no source is loaded or source
  loading fails.
- Updated the full Debug Workbench probe so it exercises the user-facing flow:
  current-line button creates a breakpoint, condition button edits it, and the
  current-line button removes it again.

### Verified

- `python -m py_compile src\ui\debug_workbench_tab.py tools\ui_debug_workbench_probe.py`
  - PASS.
- `python tools\ui_debug_workbench_probe.py --output-dir tools\ui-debug-workbench-current-line-breakpoint --width 1440 --height 900`
  - PASS.
  - Screenshots checked manually:
    - `tools\ui-debug-workbench-current-line-breakpoint\01_debug_workbench_project.png`
    - `tools\ui-debug-workbench-current-line-breakpoint\03_debug_workbench_narrow.png`
- `python tools\ui_keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\keil_breakpoint_sync_probe.py`
  - PASS.
- `python tools\keil_command_transaction_probe.py`
  - PASS.
- `python tools\ui_keil_runtime_control_probe.py`
  - PASS.
- `python tools\ui_keil_run_to_cursor_probe.py`
  - PASS.

### Notes

- This is still local UI/UX work around the existing breakpoint model. It does
  not execute Keil commands or touch hardware.
- The gutter click path remains available; the new button is a discoverable
  affordance for the same local breakpoint model.

### Next Target

- Move from breakpoint usability back into runtime capability:
  - start a shared acquisition-session runtime contract,
  - let Keil Watch, SWD memory polling, and serial waveform eventually feed the
    same sample buffer concepts,
  - keep OpenOCD/pyOCD/TI planned sources behind no-hardware gates until each
    adapter has probes.

## Milestone 87 - Acquisition Session Sample Contract

### Goal

Start the shared runtime layer for waveform data without prematurely rewriting
the existing collector. SWD memory polling, Keil Watch, serial waveform, and
future OpenOCD/pyOCD/TI sources need a common sample/batch contract before they
can feed one scope surface cleanly.

### Completed

- Added `src\core\acquisition_session.py` with backend-neutral contracts:
  - `AcquisitionSessionState`,
  - `AcquisitionSessionContract`,
  - `AcquisitionSample`,
  - `AcquisitionBatch`.
- Added helpers to normalize transport rows into named sample values:
  - dict rows from debugger/watch transports,
  - positional rows from fast memory/serial transports,
  - missing or invalid values preserved as `NaN` instead of crashing the scope
    path.
- Added batch series conversion so future plot code can consume the same shape:
  `{variable: (timestamps, values)}`.
- Split acquisition source normalization into two meanings:
  - `normalize_acquisition_source_key()` still protects the main scope selector
    and falls route-only serial back to SWD,
  - `normalize_known_acquisition_source_key()` preserves all known source keys
    for runtime contracts and future adapters.
- Added `acquisition_source_descriptor()` for looking up serial/planned source
  descriptors without pretending they are active main-scope selections.

### Verified

- `python -m py_compile src\core\acquisition_session.py src\core\acquisition_sources.py tools\acquisition_session_probe.py tools\acquisition_sources_probe.py`
  - PASS.
- `python tools\acquisition_session_probe.py`
  - PASS.
- `python tools\acquisition_sources_probe.py`
  - PASS.
- `python tools\collector_fake_transport_probe.py`
  - PASS.
- `python tools\ui_keil_watch_scope_probe.py`
  - PASS.
- `python tools\debug_backend_adapter_probe.py`
  - PASS.

### Notes

- This stage is a no-hardware architecture contract. It does not change the
  existing `DataCollector` sampling loop yet.
- The contract intentionally keeps route-only serial representable as a source
  while the main LoopMaster scope selector still keeps serial as a navigation
  route to the serial assistant.

### Next Target

- Wire the new acquisition batch contract into one low-risk producer path first:
  - likely the fake transport / collector probe path,
  - then Keil Watch batch conversion,
  - finally serial waveform samples once the parser/plot contract is ready.
