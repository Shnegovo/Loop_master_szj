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
