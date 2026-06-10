# STM32F401CCU6 Keil Variable Probe

This is a small Keil uVision / MDK-ARM firmware project for validating
LoopMaster variable read/write and ST-Link debug flows on an STM32F401CCU6.

The project is intentionally bare-metal and self-contained:

- `main.c` defines volatile globals that are safe to inspect or overwrite.
- `startup_stm32f401ccux.s` provides a minimal Cortex-M4 reset vector.
- `f401_variable_probe.sct` places Flash at `0x08000000` and SRAM at
  `0x20000000`.
- `F401VariableProbe.uvprojx` selects the Keil `STM32F401CCUx` device.

## Debug Variables

Use these names from Keil Watch, LoopMaster, or another debugger:

| Symbol | Type | Purpose |
| --- | --- | --- |
| `debug_setpoint` | `volatile int32_t` | Write this to drive the simple feedback loop. |
| `debug_feedback` | `volatile int32_t` | Read/write simulated feedback. |
| `debug_counter` | `volatile int32_t` | Free-running loop counter. |
| `debug_gain` | `volatile float` | Write a gain value; clamped to `[-100.0, 100.0]`. |
| `debug_error` | `volatile int32_t` | Last `setpoint - feedback` value. |
| `debug_flags` | `volatile uint32_t` | Bit 0 toggles periodically for a visible heartbeat. |

## Build Expectations

Open `F401VariableProbe.uvprojx` in Keil uVision 5 or newer and build the
`STM32F401CCU6 Variable Probe` target.

Install the `Keil::STM32F4xx_DFP` device pack first. The project references
`Keil.STM32F4xx_DFP.3.1.1`, but newer compatible STM32F4xx_DFP versions are
expected to work because the source does not depend on HAL or CMSIS headers.
The DFP supplies the uVision device selection, SVD, debug configuration data,
and Flash algorithms; this project supplies its own minimal startup file.

The target uses:

- Device: `STM32F401CCUx`
- Flash: `0x08000000`, `0x00040000` bytes
- SRAM: `0x20000000`, `0x00010000` bytes
- Debug info: enabled
- Optimization: disabled
- MicroLIB: enabled
- Hex output: enabled

If uVision reports missing flash programming data, open **Options for Target**,
confirm the selected device is `STM32F401CCUx`, then choose the STM32F4 256 KB
Flash algorithm under **Utilities**.

## Validation Flow

1. Build the target and flash with ST-Link.
2. Start a debug session and run to `main`.
3. Add the debug variables above to Watch or LoopMaster.
4. While running, write values such as:
   - `debug_setpoint = 5000`
   - `debug_gain = 5.0`
   - `debug_feedback = 0`
5. Confirm `debug_error`, `debug_feedback`, `debug_counter`, and `debug_flags`
   continue changing and that written values can be read back.
