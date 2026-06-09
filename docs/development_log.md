# LoopMaster Development Log

This log records each development stage, what changed, how it was verified, and the next target. Keep it updated before each commit that closes a stage.

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
