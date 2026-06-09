# LoopMaster local patch notes

## v2.0 cockpit UI refresh

This build keeps the existing parser, SWD backend, collector, variable table, temporary write, and CSV export flows intact while rebuilding the Qt shell around a cockpit-tools-inspired workflow:

1. Replaced the old native tab strip with a left workspace rail backed by the same `QTabWidget` page stack, so existing tab state logic remains compatible.
2. Added a cockpit-style light card theme for the header, connection bar, workspace shell, navigation rail, tables, menus, and control cards.
3. Tuned the scope panel with softer grid/axis styling, a wider control sidebar, a calmer page transition, and lower-shadow cards.
4. Improved plotting resilience by using peak downsampling and a display-only point budget for large histories without changing collection buffers.
5. Updated local smoke defaults and added `LoopMaster_v2.spec` for the `D:\LoopMaster_v2.0` workspace.

Reference: `jlcodes99/cockpit-tools` was used as a visual and interaction reference; no external source files were copied into this project.

Validation:

```powershell
D:\LoopMaster_v1.3\.venv-build\Scripts\python.exe -m py_compile main.py src\ui\gui.py src\ui\pcl_theme.py tools\ui_robustness_smoke.py
D:\LoopMaster_v1.3\.venv-build\Scripts\python.exe main.py info --help
D:\LoopMaster_v1.3\.venv-build\Scripts\python.exe main.py variables --help
D:\LoopMaster_v1.3\.venv-build\Scripts\python.exe tools\ui_robustness_smoke.py --iterations 10 --widths 1100,1200,1500 --output-dir D:\LoopMaster_v2.0
```

Visual QA:

- `D:\LoopMaster_v2.0\ui-preview-v2.0-vars-real.png`
- `D:\LoopMaster_v2.0\ui-preview-v2.0-scope-real.png`
- `D:\LoopMaster_v2.0\ui-robustness-final.png`

## v1.3 UI refresh

This local build keeps the v1.2 data path intact and refreshes the Qt UI toward a softer PCL-style look:

1. `src/ui/pcl_theme.py` now uses rounder cards, buttons, combo boxes, menus, tabs, scrollbars, and dialogs.
2. Buttons have a lightweight hover/press motion filter that avoids Qt `QGraphicsEffect` artifacts on Windows.
3. Cards and the plot panel use softer shadows, lighter borders, and a cleaner white/blue surface treatment.
4. Page switching uses a longer, softer slide animation, and status pulse timing is eased.
5. `LoopMaster_v1.spec` builds the new executable as `LoopMaster_v1.3.exe`.

Verified with:

```powershell
D:\LoopMaster_v1.3\.venv-build\Scripts\python.exe -m py_compile main.py src\ui\gui.py src\ui\pcl_theme.py src\core\collector.py src\core\mem_backend.py src\parser\elf_parser.py src\parser\map_parser.py src\parser\variable_inventory.py
D:\LoopMaster_v1.3\.venv-build\Scripts\python.exe main.py info --help
D:\LoopMaster_v1.3\.venv-build\Scripts\pyinstaller.exe --clean -y LoopMaster_v1.spec
D:\LoopMaster_v1.3\LoopMaster_v1.3.exe
```

Real UI previews are saved as:

- `D:\LoopMaster_v1.3\ui-preview-v1.3-vars-real.png`
- `D:\LoopMaster_v1.3\ui-preview-v1.3-scope-real.png`

## v1.3 target default fix

The target selection no longer defaults to TI MSPM0G3507 after scanning.

1. `DEFAULT_TARGET` is now `cortex_m`, which is the generic STM32-friendly target for non-intrusive RAM reads.
2. The target combo starts on `STM32 全系列通用 / Cortex-M` and is editable, so exact pyOCD target names such as `stm32f103rc` can be typed directly.
3. TI support remains explicit through `TI MSPM0G3507`, `TI LP-MSPM0G3507`, or typed aliases `mspm0g3507` / `m0g3507`.
4. Probe scanning only updates the probe list; it does not change the selected target.

## v1.3 UI regression fix

1. The recent ELF action is visible again in the variable toolbar whenever the saved ELF path exists.
2. `文件` now also contains a `载入最近 ELF` action, so the recent file is reachable even from the scope tab.
3. Button feedback no longer animates widget positions, which fixes layout overlap in the connection bar.
4. The target editor now displays the canonical target name such as `cortex_m` instead of an empty placeholder.
5. Config loading accepts UTF-8 files with or without BOM, preventing recent-file loss after external edits.

This local build contains two small changes:

1. `main.py` opens the GUI when launched without arguments, so double-clicking the exe starts LoopMaster Scope.
2. `src/parser/readelf.py` parses DWARF numeric attributes with `int(value, 0)`, so hexadecimal values such as `0x7fffffff` from CMSIS-RTOS enums no longer crash variable loading.
3. `src/core/mem_backend.py` registers pyOCD probe classes explicitly, so STLink/CMSIS-DAP scanning still works if PyInstaller metadata loading is incomplete.
4. The PyInstaller build includes `libusb_package\libusb-1.0.dll` and pyOCD package metadata, which are required for STLink USB enumeration in the packed exe.

Verified with:

```powershell
python -c "from pathlib import Path; from src.parser.elf_parser import ELFParser; from src.parser.readelf import parse_debug_info; from src.parser.variable_inventory import VariableInventory; p=Path(r'D:\stm32Demo\v2.12_debug_on_screen_debug\build\Debug\TEST.elf'); db=parse_debug_info(p); print(len(VariableInventory(ELFParser(p), db, {}).generate()))"
```

```powershell
.\LoopMaster_v1.exe scan-probes --output probe-scan-check.json
```
