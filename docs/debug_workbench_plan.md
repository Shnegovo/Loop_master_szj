# LoopMaster MCU Debug Workbench Plan

LoopMaster 的主方向调整为现代化嵌入式调试工作台。优先支持 Keil 作为第一条调试主控链路，LoopMaster 作为外置现代面板负责变量示波、变量写入、串口助手、记录回放和后续工具扩展；架构上必须保留 OpenOCD、pyOCD、GDB server 等多调试后端适配空间。

## 产品定位

- Keil 负责第一阶段连接调试器、控制目标芯片、保留断点和单步调试体验。
- LoopMaster 通过 Keil Debug Commands、UVSOCK 或命令桥接读写变量，避免和 Keil 抢同一个 SWD/JTAG 调试口。
- OpenOCD、pyOCD、GDB server 直连模式保留为并列调试后端，用于不打开 Keil、跨 IDE、跨探针或需要直接控制调试链的场景。
- 串口、USB、SWO/RTT、网络、文件回放等数据源进入统一采集架构，不再只围绕 CAN 或单一协议设计。
- 调试和示波必须是可选模式，不允许被单一后端绑死：
  - 保留原来的非侵入/轻侵入式变量示波和串口示波模式，适合只看数据、不接管调试链的场景。
  - Keil / OpenOCD / pyOCD / GDB server 等 OCD 调试模式作为并列选择，适合断点、单步、PC/source、变量读写和调试态示波。
  - 同一个示波界面要能消费不同来源的数据：轻量采集流、Keil 变量读数、OCD memory/watch、串口/RTT/SWO/USB、文件回放。
- UI 保持 PCL/Cockpit 风格的现代轻量工作台，目标是稳定、流畅、中文化、适合长时间调参。

## 当前基线

- 已有工作区导航：`LoopMaster` 和 `串口助手` 分离，子页面在各自工作区内展开。
- PclComboBox 已改为主窗口内部 overlay，下拉框不再使用 Qt 原生弹窗外框，避免边缘凸出和截图截不全。
- 串口助手下拉框样式已同步为外层 frame 统一画边框。
- 示波器坐标轴已禁用 pyqtgraph 自动 SI 前缀，减少轴标签重排。
- 自动滚动 X 轴已有范围缓存，避免重复 `setXRange()`。
- 多窗格多曲线时显示 FPS 上限更保守，优先保证拖动和示波稳定。
- 已有截图和流程探针覆盖：combo popup、串口助手集成、工作区导航、Halt/Run、分隔条、窄窗口、侧栏、轴按钮。
- 已验证源码入口和打包 exe 关闭流程，LoopMaster 主进程关闭后不残留。
- GitHub 仓库已公开，`v2.1.0` Release 的 `LoopMaster_v2.1.exe` 已通过未登录 HEAD 请求验证可下载。
- 已补 MIT `LICENSE` 和面向公开用户的 README；仍需补 release checksum、公开用户指南、截图、故障排查、`pyproject.toml` 和更可复现的依赖锁定策略。
- 已新增 backend-neutral debug session contract，后续 Keil/OpenOCD-GDB/pyOCD/offline replay 都应先转换成统一 `DebugSessionSnapshot` 再进入 UI/审计链。

## 长期推进规则

- 后续不再把 stage 切得过细，优先按大版本或大里程碑推进。
- 每个阶段结束时必须记录本阶段成果、验证结果、重要限制和下一个目标。
- 小 UI 调整、探针补强、局部文案和局部整理默认并入当前里程碑，不单独开小阶段。
- 只有大版本或大型里程碑收口时才进行打包和 Release。
- 需要拆分排查、实现、复核或探针验证时，可以安排多 agent 并行推进，最后统一沉淀到同一个阶段记录。
- 开源发布后必须继续记录参考来源和许可证，尤其是调试器、IDE、协议解析和 UI 动画相关开源参考；不要直接复制许可证不兼容代码。

## 已收口里程碑

`Keil Dry-Run Debugger Cohesion`

- 已把断点验证状态汇总、同步断点干跑预览、历史/审计一致性和 UI 探针补强合并为一个较大的里程碑。
- 仍保持 no-hardware、no live UVSOCK execution，不启动 Keil、不访问 ST-Link/F401CCU6、不执行断点同步或变量写入。
- 详细完成项和验证结果记录在 `docs/development_log.md`。

## Keil 调试链可行性确认

- 官方资料确认 UVSOCK 可让第三方 client 控制和监控 uVision，并覆盖工程配置、构建和调试能力。
- 官方 Debug Commands 文档确认 Keil 支持断点、内存、表达式、程序运行/单步等命令类别。
- 本机 `D:\Keil\Keil_v5\UV4` 已确认存在 `UV4.exe`、`uVision.com`、`UVSC.dll`、`UVSC64.dll`、`UVSCWrapper.dll`。
- `tools\keil_bridge_probe.py --keil-root D:\Keil --show-exports` 已确认首选 `UVSC64.dll`，解析到 103 个导出，关键入口包括 `UVSC_OpenConnection`、`UVSC_DBG_STATUS`、`UVSC_DBG_EXEC_CMD`、`UVSC_DBG_EVAL_EXPRESSION_TO_STR`、`UVSC_DBG_VARIABLE_SET`、`UVSC_DBG_MEM_READ`、`UVSC_DBG_MEM_WRITE`、`UVSC_DBG_START_EXECUTION`、`UVSC_DBG_STOP_EXECUTION`。
- `tools\keil_uvsock_preflight_probe.py --keil-root D:\Keil` 已确认 DLL 可加载；当前没有运行中的 uVision，因此只能证明可预检，不能宣称已连接真实会话。
- Keil live write 已经有真实 UVSOCK 路径：F401 probe 项目可通过 `debug_setpoint` 做写入/回读烟测；UI 现在会根据工程识别 F401 probe 或平衡车 F103 工程的变量预设。
- Keil Halt/Run 已经接入显式 UVSOCK runtime control：`暂停` / `运行` 会调用 start/stop execution 并回读状态，但仍必须由用户点击并确认。
- Keil PC/源码定位、复位、单步和跨过已经有真实 UVSOCK 路径：暂停态会用 `LOG+EVAL PC` 回读 PC 并映射到 AXF/DWARF 源码；`RESET` 复位、`T` 单步和 `P` 跨过都已在 ST-Link/F401 上验证会回到暂停态并刷新源码行。
- Keil Step Out 暂不开放：本机实测 `O` 在当前 F401 probe 主循环上下文没有稳定回到暂停态，不能作为用户按钮暴露。
- 平衡车参考工程已确认：`Target 1`、`STM32F103C8`、输出 `Objects\Project.axf`，当前 AXF 未生成；首批建议变量为 `SpeedLevel`、`AngleAcc_Offset`、`AnglePID.Kp/Kd`，首批示波变量为 `Angle`、`AveSpeed`、`PWML/PWMR`。
- 结论：Keil 方案真实可行，但必须分级实测：先只读连接和状态快照，再读变量/断点快照，再 opt-in 写变量、断点同步和 Halt/Run/Step/Step Over。

## 多调试后端架构方向

- 共用层：源码树、代码编辑器/装饰、断点模型、变量/Watch、示波、记录回放、审计日志、UI 状态机。
- 后端层：每个调试链实现独立 adapter，例如 `KeilUvSockBackend`、`OpenOcdGdbBackend`、`PyOcdBackend`、`OfflineReplayBackend`。
- 后端能力用 capability 暴露：attach、halt、run、step、read_variables、write_variables、breakpoint_sync、pc_location、memory_read、trace_stream。
- 断点、变量写入和运行控制统一先走 transaction/dry-run，再由后端决定是否可以执行，防止 UI 直接调用某个后端的危险动作。
- OpenOCD/pyOCD 优先走 GDB remote 或各自稳定 API；Keil 优先走 UVSOCK/Debug Commands；高速波形仍优先使用串口、SWO/ITM、RTT、USB 或网络流。
- Scope/采集层要独立于 DebugBackend：
  - `DebugBackend` 负责调试状态、断点、PC/source、step/run/halt、变量/内存读写。
  - `AcquisitionSession` 负责连续采样、时间戳、缓存、降采样和错误状态。
  - 一个后端可以提供一个或多个 acquisition source，但示波器也必须能在没有调试后端时独立运行。
  - UI 应显式区分“轻量示波模式”和“调试链示波模式”，避免用户以为普通示波会接管 ST-Link/SWD。

### 近期架构拆分要求

- `DebugBackendSessionSnapshot` 只能保留通用字段；远端断点、PC、变量和 target 状态要抽成 backend-neutral 类型，不能继续引用 Keil 专属 snapshot。
- `DebugRuntimeState` 要逐步从 `KEIL_DISCOVERED` / `KEIL_ATTACHED` 改到通用 `DISCOVERED` / `ATTACHED`，Keil/OpenOCD/pyOCD/GDB 只作为 backend identity。
- 当前 `KeilCommandTransaction` 的 dry-run/audit/history 能力要沉到通用 `DebugCommandTransaction`；Keil 只保留 UVSOCK/Debug Commands 的 preview formatter。
- 断点 diff 逻辑要从 `src\core\keil\commands.py` 抽为通用 breakpoint planner；不同后端只负责命令翻译和远端回读解析。
- Source provider 要从 Keil `.uvprojx` 扩展成通用 `SourceManifest`：Keil 工程、ELF/DWARF、compile_commands、GDB `info sources` 和手动 source roots 都能喂同一个源码视图。
- 主窗口不能长期直接持有单个 `KeilUvSockBackendAdapter`；需要 `DebugBackendRegistry` / controller / backend selector，为 OpenOCD、pyOCD、GDB server 和 offline replay 留入口。

## 下一大里程碑

`Backend-Neutral Debug Foundation`

- 已完成第一块：抽出可扩展的调试后端 adapter 边界，`发现 Keil` 已改为通过 Keil adapter 产出状态和诊断。
- 已新增 Keil backend adapter probe，验证 discover 不连接、只读 snapshot 不启用 Halt/Run/Step/写变量/同步断点。
- 已接入 `连接` 动作为显式 opt-in 的 Keil 只读连接快照：用户点击连接时才执行 `OpenConnection -> DBG_STATUS -> CloseConnection`，并且只显示状态/诊断，不开放运行控制。
- 已加入 PC 和远端断点只读占位：当前标记为 incomplete，诊断显示“待 Keil 回读/枚举”，并喂给现有 dry-run diff，让同步断点保持等待而不是误判 ready。
- 已把 backend snapshot ID 和精简证据写入 dry-run transaction、history、tooltip 和 JSONL audit，后续 live smoke 能追溯每个 UI 决策来自哪次只读快照。
- 已抽出 backend-neutral snapshot 模型：`RemoteBreakpoint`、`RemoteBreakpointSnapshot`、`DebugPcLocation`、`TargetSnapshot`；Keil 旧类型保持兼容别名。
- 已加入 `DebugBackendRegistry`：默认仍使用 Keil / UVSOCK，且可注册 OpenOCD/GDB、pyOCD、离线回放占位后端。
- 已加入 backend-neutral `DebugCommandTransaction` shell，用于不可用后端的 dry-run/audit，不连接探针、不启动外部进程。
- 已加入 fake OpenOCD/GDB、pyOCD、离线回放的 no-hardware 探针，证明多后端入口可以先安全存在。
- 已把后端选择入口接入 Debug Workbench：Keil / UVSOCK 默认可用，OpenOCD/GDB、pyOCD、离线回放显示为已注册但 blocked 的 dry-run 占位。
- 非 Keil 后端计划文案已改为通用后端语义，不再显示 UVSOCK/Keil 专属执行步骤。
- 已加入通用 `DebugCommandHistory`，Keil 和非 Keil dry-run transaction 都能进入同一个历史/audit 预览，后端 id 会显示在历史 tooltip 中。
- 已抽出通用 `SourceManifest`：Keil `.uvprojx` 先作为首个 provider，源码树开始消费 manifest，为后续 ELF/DWARF、GDB source list 和手动 source root 留入口。
- 已加入手动 source root provider，可从普通源码目录生成 `SourceManifest`，并带文件数量上限，供后续 OpenOCD/GDB、pyOCD、离线回放复用。
- 已加入 GDB `info sources` 文本 provider，可在不启动 GDB 的情况下把已捕获 source list 转成同一套 `SourceManifest`。
- 已加入 `compile_commands.json` provider，可从 CMake/VSCode/CubeIDE 风格工程生成 `SourceManifest`，不调用编译器或外部工具。
- `SourceManifest` 已加入来源/诊断字段：provider 原始路径、解析方式、compile directory、诊断计数和 metadata，为后续 DWARF/GDB 路径映射做准备。
- 已加入 ELF/DWARF source provider，可解析已捕获的 `readelf -wl` 行号表，或通过现有 readelf 路径从 AXF/ELF 生成同一套 `SourceManifest`。
- 已把非 Keil `SourceManifest` 接入 Debug Workbench 源码树预览：OpenOCD/GDB、pyOCD、离线回放占位后端能复用当前源码树或安全 fallback，不再只有空后端下拉框。
- 已加入源码来源选择器和路径诊断 chips，可显式选择自动、Keil 工程、编译数据库、源码根、ELF/DWARF 和 GDB 文本预览；后两者当前仅显示待接入，不自动启动外部工具。
- 已加入 debug backend lifecycle metadata：Keil、OpenOCD/GDB、pyOCD、离线回放可以先声明 worker/process、opt-in、shutdown/report 参与方式，仍不启动外部调试进程。
- 已加入显式 source provider 配置：可选择 `compile_commands.json`、源码根，或粘贴已捕获的 GDB `info sources` / `readelf -wl` 文本；缺失源码会进入 chips/诊断表和源码树 `(缺失)` 节点。
- 已加入缺失源码映射提示：按缺失目录汇总数量、原始路径示例和解析来源，先显示在诊断表与 chip tooltip 中。
- 已加入 data-only 源码重映射预览：可把一个缺失目录映射到本地源码根，重建预览 manifest 并显示缺失数量变化。
- 已加入用户可点的源码映射动作：当前清单有缺失路径时可选择本地源码根，应用 remap 预览并保存选择。
- 继续禁止自动写变量、同步断点、Halt/Run/Step，直到下一轮 opt-in 执行里程碑。

## 下一轮优先级

1. 退出残留硬化
   - 已加入 shutdown report，关闭步骤会记录 stop timers、backend shutdown request、sampling、serial、config save、backend disconnect 的耗时和失败原因。
   - 已补充 sampling、slow-sampling、serial-worker、stuck-serial-worker 进程级关闭探针。
   - 未来真正引入 Keil/OpenOCD/pyOCD debug worker 时，必须先接入 backend lifecycle metadata 和 shutdown report，再开放 opt-in 执行。
   - 继续保持关闭期禁止新的 pyOCD/Keil/串口读写进入。

2. 架构底座
   - 在源码来源选择器后续接入已保存 remap 的自动重放，并清楚显示重放结果。
   - 继续把 Keil transaction UI typing 迁移到通用 `DebugCommandTransaction`，Keil/OpenOCD/pyOCD/GDB 分别实现命令预览和执行器。
   - 抽出 `Transport`：Keil、Serial、pyOCD、文件回放、未来 USB/RTT/网络都走统一接口。
   - 抽出 `Decoder`：Raw、CSV、FireWater、JustFloat、HEX、后续自定义协议注册。
   - 抽出 `AcquisitionSession`：统一日志、样本、时间戳、错误状态和缓冲区。
   - 抽出 `ScopePlot`：LoopMaster 变量示波和串口示波共用曲线、降采样、Y 轴策略和游标能力。
   - 抽出工具注册表：后续新增工具只注册工作区、页面、数据源和配置，不继续堆进 `gui.py`。

3. UI 边角精修
   - 全线中文化，技术缩写如 ELF、SWD、FPS、CSV、ASCII、HEX 可保留。
   - 修变量树展开 branch 的凸出感，统一 indentation、branch 宽度和选中背景。
   - 折叠侧栏时隐藏 splitter 残线，展开后恢复可拖动手柄。
   - 进一步减轻卡片边框、阴影和内边距，减少“框边边大”的感觉。
   - 串口工作区标题和状态更明确，不让用户误以为仍在 LoopMaster 变量页。

## Keil 主控集成路线

### 阶段 1：可行性桥接

- 检测正在运行的 uVision 实例和当前工程。
- 读取当前 AXF/ELF 路径，复用 LoopMaster 现有符号和 DWARF 解析。
- 通过 Keil Debug Commands 或 UVSOCK 执行最小命令：读取表达式、读取内存、写内存、查询 target 状态。
- UI 增加调试后端选择：`Keil 主控`、`pyOCD 直连`、`离线回放`。

### 阶段 2：安全写变量

- 变量写入优先通过 Keil 评估表达式或写 RAM 地址。
- 写入前检查类型，只允许基础数值类型和枚举。
- 默认只允许 RAM 地址窗口，不写 Flash、不写外设寄存器、不写结构体整体和函数指针。
- 写入流程：读旧值 -> 编码新值 -> 写入 -> 读回校验 -> 失败回滚。
- Keil 主控时 Halt/Run 以 Keil 状态为准，LoopMaster 不直接抢探针。
- 下一步必须把 build/launch/connect/write/readback 串成一个显式事务：
  F401 probe 用 `debug_setpoint`，平衡车工程用 `SpeedLevel`，并在缺少 AXF 时先构建。

### 阶段 3：Keil 模式示波

- 先支持低到中频变量读取，目标是稳定可用，不追求最高采样率。
- 明确提示 Keil 桥接采样的现实限制：它更适合 Watch、慢速变量、参数确认，不一定适合 1 kHz 以上高速示波。
- 高速波形优先走串口、SWO/ITM、SEGGER RTT、USB CDC/HID、UDP 或 pyOCD 独立模式。
- 示波 UI 保持一致，用户只切换数据源，不切换使用习惯。

### 阶段 4：记录回放

- 记录变量波形、写变量操作、Halt/Run、串口日志、错误状态和截图标记。
- 支持离线回放一次调试过程，用于复盘 PID 超调、抖动、失控、掉线和采样不足。

### 阶段 5：现代化 Keil 调试前端

- LoopMaster 作为 Keil 调试器的现代外置前端，目标体验参考 VSCode/CubeIDE，而不是复刻 Keil 旧式窗口。
- 代码可视化：
  - 打开 Keil 工程/当前源文件，按文件树和标签页组织源码。
  - 当前 PC、断点、运行行、错误行和搜索命中都用现代 gutter/装饰层显示。
  - 支持源码和反汇编/地址视图之间的跳转，优先从 DWARF/AXF 和 Keil 当前上下文解析。
- 断点可视化：
  - 在 gutter 点击设置/删除/启用/禁用断点。
  - 用列表集中管理断点，显示文件、行号、命中次数、条件、启用状态。
  - 与 Keil 保持同步，LoopMaster 不直接绕过 Keil 抢调试口。
- 调试视图：
  - 运行/暂停/复位/单步/步入/步出/运行到光标。
  - Watch/局部变量/全局变量/寄存器/调用栈/RTOS 任务视图。
  - 内存视图、外设寄存器视图、Fault 分析和变量写入审计日志。
- 示波联动：
  - 变量 Watch、写变量、断点命中、Halt/Run 与示波时间轴关联。
  - 调 PID 时能看到“在哪次写参数后曲线变好/变坏”。
- 实现策略：
  - 先完成 Keil UVSOCK/Debug Command 只读连接，再做断点/代码视图，最后做写变量和复杂调试控制。
  - 前端组件必须模块化，避免继续把源码视图、断点面板、Watch、调用栈堆进 `gui.py`。
  - 后续可以参考开源 IDE/调试器实现，例如 VSCode debug adapter、Eclipse/CubeIDE 调试 UI、DAP 客户端、Monaco/QScintilla 类代码编辑器，但必须记录来源和许可证，不能直接复制不兼容代码。
  - 每个大功能都需要 UI 截图探针、无硬件假后端探针，以及在 Keil/ST-Link/F401CCU6 上的只读/写入分级验证。

## 功能路线

- PID 调参示波：阶跃响应、超调、稳态误差、收敛时间、抖动 RMS、峰值自动标注。
- 协议实验室：从样例数据生成解析规则，配置通道名、单位、比例、颜色和 Y 轴策略。
- 串口助手增强：HEX 接收、CSV 显式模式、通道配置、发送模板、循环发送、时间戳日志。
- SWO/ITM/RTT 采集：适合高速日志和嵌入式实时波形。
- USB CDC/HID、UDP/TCP、BLE、Modbus、I2C/SPI 适配器：把 LoopMaster 做成多数据源工作台。
- RTOS 视图：任务列表、CPU 占用、栈水位、队列和信号量状态。
- Fault 分析：HardFault 寄存器快照、调用栈、异常原因提示、最近变量波形定位。
- 硬件在环测试：参数扫频、自动激励、回归测试、报告导出。
- 项目档案：每块板子保存 AXF、Keil 工程、探针、变量组、串口协议、示波布局。
- AI 辅助诊断：提示振荡、饱和、采样率不足、曲线尺度不合理，但任何写变量动作都必须用户确认。

## 验证要求

- 每次 UI 改动都保留截图探针，至少覆盖工作区、串口助手、示波器多窗格、窄窗口和下拉框。
- 每次后端改动都覆盖关闭流程，至少验证空载关闭、采样中关闭、串口连接中关闭。
- Keil 集成必须先做只读探针，再做写变量，最后做示波。
- 写变量能力必须有失败回滚、读回校验、日志记录和用户确认。
- 性能指标优先看拖动体感和 p95 帧间隔，不能只看平均 FPS。

## 暂不做

- 不把固件内置遥测协议作为强制前提，它是长期增强路线。
- 不做无人确认的自动 PID 参数写入。
- 不在 Keil 已连接同一探针时再让 pyOCD 强行直连同一个调试器。
- 不继续把新功能直接堆进主窗口大文件，新增功能必须走可注册的工具模块。

## 参考

- Keil Debug Commands: https://www.keil.com/support/man/docs/uv4cl/uv4cl_debug_commands.htm
- Keil Command Window: https://www.keil.com/support/man/docs/uv4/uv4_db_dbg_outputwin.asp
- Keil UVSOCK: https://www.keil.com/support/docs/2636.htm
