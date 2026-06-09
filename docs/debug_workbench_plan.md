# LoopMaster MCU Debug Workbench Plan

LoopMaster 的主方向调整为现代化嵌入式调试工作台。优先支持 Keil 作为调试主控，LoopMaster 作为外置现代面板负责变量示波、变量写入、串口助手、记录回放和后续工具扩展。

## 产品定位

- Keil 负责连接调试器、控制目标芯片、保留断点和单步调试体验。
- LoopMaster 通过 Keil Debug Commands、UVSOCK 或命令桥接读写变量，避免和 Keil 抢同一个 SWD/JTAG 调试口。
- pyOCD 直连模式保留为独立调试模式，用于不打开 Keil 或需要直接控制探针的场景。
- 串口、USB、SWO/RTT、网络、文件回放等数据源进入统一采集架构，不再只围绕 CAN 或单一协议设计。
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

## 下一轮优先级

1. 退出残留硬化
   - 关闭期先禁止新的 pyOCD/Keil/串口读写进入。
   - 采样线程、串口 worker、调试 worker 统一登记生命周期。
   - 后端断开需要真实超时，不能只给锁等待设置 timeout。
   - 增加“采样中关闭、串口连接中关闭、调试器读卡住模拟”的关闭探针。

2. 架构底座
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
