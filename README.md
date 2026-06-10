# LoopMaster

LoopMaster 是一个面向嵌入式调试和调参的现代化桌面工作台。当前版本重点覆盖 MCU 变量示波、串口助手、源码/断点预览和后续 Keil/OpenOCD/pyOCD 调试链适配底座。

目标体验不是复刻传统 IDE 的旧窗口，而是把示波、变量、串口、源码、断点和记录回放逐步整合成一个更流畅的调试面板。

## 下载

Windows 用户可以直接下载 Release 安装包：

- [LoopMaster_v2.1.exe](https://github.com/Shnegovo/Loop_master_szj/releases/download/v2.1.0/LoopMaster_v2.1.exe)

如果 Windows Defender 或浏览器提示未知发布者，这是因为当前安装包还没有代码签名。

## 当前能力

- MCU 变量示波：解析 ELF/AXF 符号和 DWARF 类型，选择变量后通过调试探针读取 RAM 变量并绘制波形。
- 多窗格示波器：支持多曲线、多窗格、变量分配、颜色和滚动视图。
- 串口助手：支持串口收发和 VOFA 风格数据示波的基础工作流。
- 调试工作台：支持 Keil 工程源码树、源码预览、断点可视化、命令预览和后端占位切换。
- 源码来源配置：支持 Keil 工程、`compile_commands.json`、手动源码根、粘贴的 GDB `info sources` 文本、粘贴的 `readelf -wl` 文本。
- 缺失源码映射：可以把导入清单里的缺失路径映射到本地源码根，并在后续重建源码清单时自动重放。
- 关闭流程治理：应用关闭时会清理采样、串口和后台任务，避免主进程残留。

## 调试链状态

LoopMaster 正在向“现代化 Keil 外置调试面板 + 多后端调试工作台”推进。

当前安全边界：

- 已实现的是大量 dry-run、预览、解析和 UI 层能力。
- Keil/OpenOCD/pyOCD/GDB 的实时控制能力仍在分阶段建设中。
- 默认不会自动启动 Keil、OpenOCD、pyOCD、GDB 或 `readelf`。
- 默认不会自动 Halt/Run/Step、写变量、刷写 Flash 或操作目标 MCU。
- 未来任何写变量、同步断点、控制运行状态的能力都必须有明确 UI 状态、校验和日志。

## 从源码运行

建议使用 Python 3.11 或更新版本。

```powershell
git clone https://github.com/Shnegovo/Loop_master_szj.git
cd Loop_master_szj
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

也可以显式启动 scope 命令：

```powershell
python main.py scope
python main.py scope path\to\firmware.elf
```

## 常用 CLI

```powershell
python main.py info path\to\firmware.elf
python main.py symbols path\to\firmware.elf
python main.py variables path\to\firmware.elf
python main.py struct path\to\firmware.elf --list
```

## 硬件要求

基础变量示波需要：

- ARM Cortex-M 目标芯片
- CMSIS-DAP、DAPLink、ST-Link 等 pyOCD 可识别调试探针
- SWDIO、SWCLK、GND 正确连接

串口助手需要：

- Windows 可识别的串口设备
- 正确的 TX/RX/GND 接线

## 开发与验证

本仓库包含一些无硬件探针脚本，用于验证 UI、源码清单和调试工作台基础行为：

```powershell
python tools\debug_source_manifest_probe.py
python tools\ui_debug_source_provider_probe.py
python tools\debug_backend_registry_probe.py
python tools\debug_transaction_shell_probe.py
```

UI 相关修改建议同时运行对应截图探针，并人工检查输出截图。

## 路线

短期重点：

- 完善 Release 说明、校验值和后续代码签名。
- 建立 backend-neutral 的调试 session contract。
- 让 Keil、OpenOCD/GDB、pyOCD 和离线回放共享同一套源码、断点、命令、状态和审计模型。
- 在不碰硬件的 dry-run 探针里先把 UI/架构跑稳。

长期方向：

- Keil UVSOCK / Debug Commands 只读连接。
- 变量 Watch、写变量、读回校验、审计日志。
- 可视化断点、当前 PC、调用栈、寄存器、内存视图和 Fault 分析。
- SWO/ITM/RTT、USB CDC/HID、UDP/TCP、BLE、Modbus 等多数据源。
- PID 调参辅助、记录回放、硬件在环测试和报告导出。

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
