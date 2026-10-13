# Arduino UNO Q 上手报告与 NCP768 推理测试教程

**收尾状态（2026-10-13）：本阶段暂时完结，开发暂停。** 下文保留 2026-10-11 的已验证流程与实测值；没有重新查询设备当前连接或 `/tmp` 文件是否仍存在。真实 EMG 到预测 UI、ADS1298 到 UNO Q、长期稳定性均未验收，见 [项目收尾记录](project_closeout.md)。性能 CLI 原名 `profile.py` 已迁移为 `profile_autoncp.py`；归档测试材料不改写。

> 项目：Project Antikythera · 记录日期：2026-10-11  
> 状态：已在 UNO Q 的 Linux 侧完成 NCP768 原生 C 推理测试：平均 **11.2793 ms**，p95 **11.3303 ms**，最大 **11.3758 ms**；336 个保留样本全部低于 50 ms，10 路输出与完整 768 维状态数值核对通过。相对历史 ESP32-S3 配置的平均耗时约减少到 1/7.29。这证明本次纯内核有计算余量，尚未证明人体输入链路或端到端实时性。

第一次阅读可以先看“两个处理器”和“三个容易混淆的刷进去”，再看实测结果。**2026-10-11 测试结束时已部署程序；重跑前先检查连接及临时文件是否仍存在。** 在 Windows PowerShell 执行下面三行即可查看连接状态并重跑；结果直接显示，不覆盖已保存的三轮记录：

```powershell
$unoAdb = 'D:\Project Antikythera\log\uno_q_ncp768_20261011_1329\tools\arduino-adb32\platform-tools\adb.exe'
& $unoAdb -P 5038 devices -l
& $unoAdb -P 5038 -s 885671393 shell '/tmp/antikythera_ncp768_20261011_1329/ncp768_bench'
```

设备列表应包含 `885671393 device`，测试输出应包含 `"passed":true`。这是单轮原始记录，不能代替下文三轮汇总；若板子已经重启、提示文件不存在，则需按部署流程重新上传电脑保存的材料。

## 目录

1. [先理解 UNO Q 上的两个处理器](#先理解-uno-q-上的两个处理器)
2. [三个容易混淆的“刷进去”](#三个容易混淆的刷进去)
3. [本次连接与测试状态](#本次连接与测试状态)
4. [连接板子的 Linux 系统](#连接板子的-linux-系统)
5. [本项目 NCP768 的部署约定](#本项目-ncp768-的部署约定)
6. [本次实际部署与测试流程](#本次实际部署与测试流程)
7. [怎样解读延迟报告](#怎样解读延迟报告)
8. [USB 不识别时的排查顺序](#usb-不识别时的排查顺序)
9. [之后接 ADS1298 要注意什么](#之后接-ads1298-要注意什么)
10. [证据与官方入口](#证据与官方入口)

## 先理解 UNO Q 上的两个处理器

UNO Q 的核心区别是同一块板上同时运行 Linux 和微控制器程序。

| 部分 | 官方规格与软件环境 | 本项目建议用途 |
|---|---|---|
| Qualcomm QRB2210 | 四核 Cortex-A53，最高 2.0 GHz；Debian Linux；2 GB 或 4 GB RAM | 首先部署 NCP768 的 C 推理程序；后续再接因果信号处理、数据记录和通信 |
| STM32U585 | Cortex-M33，最高 160 MHz；2 MB Flash、约 786 KB SRAM；Arduino / Zephyr | 后续采集 ADS1298、缓冲数据、控制输出 |

规格依据：[UNO Q 官方硬件文档](https://docs.arduino.cc/hardware/uno-q/)。

Arduino IDE 的草图面向 STM32。仅在 IDE 中上传一个程序，不会自动让模型运行在高通 CPU 上。Arduino App Lab 可以管理 Linux 应用和 MCU 草图，并通过 Bridge 连接两侧。Linux C 程序也可以通过 Linux shell 单独编译运行，适合当前先验证推理内核的任务。

本次先测 Linux 侧 CPU，不预设 GPU、DSP 或其他 AI 加速器能够直接执行我们的 CfC。不同执行后端需要独立的数值和性能验证。

## 三个容易混淆的“刷进去”

| 操作 | 实际改变什么 | 何时需要 |
|---|---|---|
| 部署 Linux 应用 | 将源文件或可执行程序复制到 Debian 中并运行 | 本次 NCP768 延迟测试，优先采用此方式 |
| 上传 STM32 草图 | 更新 MCU 上的 Arduino / Zephyr 程序 | 移植采集驱动、改变 MCU 控制逻辑时 |
| 重刷 Linux 系统镜像 | 替换板上的系统镜像，可能清除已有应用和配置 | 官方更新或系统恢复确有需要时 |

**本次测试不要求先重刷 Linux，也不要求先改 STM32 固件。** 能进入板端 Debian、能运行 C 编译器，就可以开始。若系统版本影响连接或 App Lab，按官方更新流程处理；不要把无法枚举 USB 直接等同于系统必须重刷。

官方入口：[App Lab 系统刷写指南](https://docs.arduino.cc/software/app-lab/configure/flash/)；[UNO Q 板端软件过旧的处理说明](https://support.arduino.cc/hc/en-us/articles/23170731875740-If-your-Arduino-UNO-Q-board-software-is-out-of-date)。执行重刷前应先保存需要保留的文件，具体擦除范围以对应工具提示为准。

## 本次连接与测试状态

| 项目 | 2026-10-11 测试时的证据 |
|---|---|
| Windows 曾识别板子 | 曾出现 ADB、COM4，以及 VID 2341 / PID 0078 |
| 连接进展 | 曾从 Windows 消失；13:49 恢复 WinUSB、VID 2341 / PID 0078、COM4，ADB 序列号 `885671393` |
| 用户反馈 | 未主动拔掉，指示灯亮；之后已重新插好并等待启动，后续已换端口或数据线；尚未完成首次设置 |
| ADB 检查 | ADB 37.0.1 未列出设备；官方 ADB 32.0.0 已下载、哈希匹配；当时已能进入板端 Linux |
| 测试材料 | [测试目录](<../log/uno_q_ncp768_20261011_1329/>) 中已有 C 测试程序、模型材料与汇总脚本 |
| 材料与主机自检 | 新生成的参考向量与历史 ESP32 输入语料 / 头文件一致；Windows 3 × 128 步数值检查通过；损坏参考和不完整记录被拒绝 |
| UNO Q 实测结果 | 三个独立进程已完成，336 个保留样本；平均 11.2793 ms，数值核对全部通过 |

13:41:48 的历史检查未发现对应设备；13:49 后恢复连接。恢复前原始状态保存在 [初始连接检查记录](<../log/uno_q_ncp768_20261011_1329/connection_status_initial.json>)。上述 Windows 自检验证测试材料和拒绝错误的逻辑，不是 UNO Q 推理测试。

指示灯亮证明板上至少有部分供电，不能证明 Windows USB 数据链路、Debian 启动和 ADB 服务都已经正常。旧的 COM4 记录也不证明当前仍在线。

## 连接板子的 Linux 系统

### 路径 A：USB / ADB

ADB 提供文件传输和 Linux shell。先看设备列表，再尝试 shell；列表为空时，反复推送文件没有意义。

下面是 **Windows PowerShell 操作模板**。其中路径对应本次工作目录；ADB shell、文件上传和板端环境探测已执行。先查看工具版本，不把其他版本的结果记作官方依赖版本的结果。

```powershell
Set-Location 'D:\Project Antikythera'
$unoAdb = '.\log\uno_q_ncp768_20261011_1329\tools\arduino-adb32\platform-tools\adb.exe'
& $unoAdb -P 5038 version
& $unoAdb -P 5038 devices -l
```

本次将官方 ADB 32.0.0 的服务固定在端口 5038，单独管理这次连接；下方所有 ADB 命令保持该端口。ADB 通过 USB 工作，不要求先连接 Wi-Fi。用户尚未完成首次设置，不能假定 SSH 已启用；App Lab 的首次引导用于设置板名、Linux 登录凭据和网络，按界面实际要求完成即可。若设备列表中恰好有一台确认属于 UNO Q 的设备，且状态为 `device`，再进入：

```powershell
& $unoAdb -P 5038 -s 885671393 shell
```

若有多台 Android / Arduino 设备，应使用 `-s` 和确认过的板子序列号选择目标，不能让默认选择决定部署位置。若显示未授权，应完成板端对应的授权流程，而不是把它当作模型问题。

Arduino 官方包索引列出的 UNO Q Windows 工具依赖包含 ADB 32.0.0。其归档为 [platform-tools_r32.0.0-windows.zip](https://downloads.arduino.cc/tools/android/platform-tools_r32.0.0-windows.zip)，SHA-256：

```text
41f4c7512b32cbb3f8c624c20b56326abb692a6f169b03b4b63b6c5a6fdbb08c
```

版本依据：[Arduino 官方包索引](https://downloads.arduino.cc/packages/package_index.json)。工具版本不匹配值得单独排查，但不是目前 USB 消失的已确认根因。

### 路径 B：局域网 / SSH

如果板子已经完成网络配置且 SSH 服务可用，可从同一网络连接 Debian。用户名、密码、IP 地址应以这块板子的实际设置为准；不要套用未经核实的默认账号。

```text
ssh <板端用户名>@<板端IP地址>
```

这是一条需要替换参数的语法示例，**本次尚未执行或验证 SSH 登录**。IP 可以从已经登录的板端、App Lab 或路由器的设备列表确认。首次连接应核对目标，不能将未知网络主机当作已连接的 UNO Q。

官方 Linux 操作入口：[UNO Q Debian 指南](https://docs.arduino.cc/tutorials/uno-q/debian-guide/)。

### 第一次使用 App Lab

从 [Arduino 官方软件入口](https://docs.arduino.cc/software) 找到 Arduino App Lab，安装 Windows 版本后打开。连接 USB 数据线，选择识别出的 UNO Q，按引导设置板名、Linux 登录密码及 Wi-Fi。若提示板端软件更新，先阅读具体更新内容；系统镜像重刷与普通软件更新是不同操作。

记住自己设置的板名和密码。以后 USB 模式用 ADB，板子联网后也可在 App Lab 的 Network 入口或通过 SSH 访问。App Lab 的 Examples 可以检查基本运行，但示例成功不代表 NCP768 已部署；本项目仍按下面的模型身份、数值和计时步骤验收。首次设置流程依据 [UNO Q 官方资料](https://docs.arduino.cc/resources/datasheets/ABX00162-ABX00173-datasheet.pdf)。

## 本项目 NCP768 的部署约定

本次使用已有 ESP32 基线的模型、原始循环 C 内核和同源测试输入，减少“换了硬件也换了模型”造成的比较歧义。

| 项目 | 本次配置 |
|---|---|
| 模型 | AutoNCP / CfC，三层单元数 455 / 303 / 10 |
| 输入 | 16 维归一化 RMS / ZC 特征；真实数据链路为 8 通道，每通道 RMS、ZC 交错排列 |
| 递归状态 | 完整 768 维，连续步进时保留 |
| 输出 | J10，10 个手指关节目标；具体顺序见 manifest |
| 权重与运算 | 每输出行 INT8 量化权重；激活、偏置与核心运算使用 FP32 |
| 模型时间步 | `ts = 1.0`，保持训练约定；不能因 50 ms 更新间隔而改成 0.05 |
| 权重大小 | 1,788,536 字节；其他浮点参数 24,872 字节 |
| 检查点 SHA-256 | `f245f89db2866fc6c06d3ce4fbe48d8db62834bda7708fa6a07ff31f4c322a24` |
| 输入测试流 | 128 步固定合成归一化输入，前 8 步为零；不是人体肌电采集 |
| 预热与样本 | 每个独立进程丢弃前 16 步；3 次进程运行合计 336 个计时样本 |
| 数值通过条件 | 每步 10 个输出及最终完整 768 维状态，与同一量化参考比较，绝对误差不超过 0.0002 |

依据：[本次构建清单](<../log/uno_q_ncp768_20261011_1329/build_manifest.json>)、[计时程序](<../log/uno_q_ncp768_20261011_1329/bench.c>)、[汇总程序](<../log/uno_q_ncp768_20261011_1329/summarize.py>)。

测试从已经归一化的 16 维输入开始，计时范围只覆盖 `autoncp_step()`。输入复制、结果比较和日志输出在计时范围之外。每次进程启动时重置状态，进程中的 128 步则持续携带状态，不在每一步清零。

数值通过证明移植保持了这份量化模型的行为，不能证明量化模型与原始浮点模型完全一致，也不能证明真实肌电预测准确。

## 本次实际部署与测试流程

本节记录已完成的连接、上传、源文件核查、原生编译、三次推理及结果汇总。当前已有 `payload`，不要再次运行 `prepare.py` 覆盖或重复生成材料；该脚本本来就拒绝向已有目录重新准备。

### 1. 确认板端环境

进入 ADB 或 SSH 的 **Linux shell** 后：

```sh
uname -a
uname -m
tr -d '\000' < /proc/device-tree/model
cat /etc/os-release
command -v cc
cc --version
free -h
```

记录系统、板型、架构、编译器版本和可用内存，先确认登录的是目标 UNO Q。还应保存测试前后的 CPU 调频策略、频率及温度；这些信息通常在 `/sys/devices/system/cpu/cpufreq/` 和 `/sys/class/thermal/` 下，具体节点以板端实际存在的内容为准。本次已确认板上没有 `cc` / `gcc`，`sudo -n` 需要密码，`wlan0` 为 DOWN。已有 Docker 镜像也没有编译器，因此采用下述临时容器编译方式，不向板端主系统安装编译器。

本次实机环境如下，后续比较应保留这些条件：

| 项目 | 已读取结果 |
|---|---|
| Linux 用户 / 主机名 | `arduino`，uid 1000 / `uno-q` |
| 系统 / 内核 | Debian 13.2 / `6.16.7-g0dd6551ae96b`，aarch64 |
| CPU | 4 核 Kryo-V2，最大频率 2016 MHz |
| 内存 | Linux 报告 3,848,142,848 字节，属于 4 GB 版本 |
| 调频 | `schedutil`，未锁频 |
| 现有容器环境 | `python-apps-base:0.5.0`，Debian 13.1 / GLIBC 2.41 |

依据：[板端环境记录](<../log/uno_q_ncp768_20261011_1329/board_environment_before.log>)。

### 2. 复制测试材料

退出交互式 shell，回到 **Windows PowerShell**。以下沿用前面的 `$unoAdb` 变量：

```powershell
& $unoAdb -P 5038 -s 885671393 shell 'mkdir /tmp/antikythera_ncp768_20261011_1329'
& $unoAdb -P 5038 -s 885671393 push '.\log\uno_q_ncp768_20261011_1329\payload\.' '/tmp/antikythera_ncp768_20261011_1329/'
& $unoAdb -P 5038 -s 885671393 shell
```

`/tmp` 是本次临时测试位置，重启后文件是否保留不可依赖；长期部署应另选持久目录。这里故意不使用 `mkdir -p`：目录已存在时先停下来检查内容，不覆盖前一次运行。本次测试材料已上传，7 项源文件哈希与本地构建清单一致，见[源文件核对记录](<../log/uno_q_ncp768_20261011_1329/board_source_hashes.log>)。复现时，文件复制成功后，在 Linux 侧运行 `sha256sum autoncp.c autoncp.h autoncp_dims.h autoncp_weights.h profile_vectors.h bench.c`，与本地 `build_manifest.json` 中的 `source_hashes` 对照，确认上传的是本次材料。

### 3. 本次实际编译方式：已有镜像中的临时容器

由于板子未联网且没有编译器，本次通过 USB 将电脑已有 HTTP 代理转发给板端。以下 **Windows PowerShell** 命令已用于建立转发；`7890` 是本机当时已存在的代理端口，不是 UNO Q 的通用固定配置：

```powershell
& $unoAdb -P 5038 -s 885671393 reverse tcp:17890 tcp:7890
& $unoAdb -P 5038 -s 885671393 push '.\log\uno_q_ncp768_20261011_1329\build_on_board.sh' '/tmp/antikythera_ncp768_20261011_1329/build_on_board.sh'
& $unoAdb -P 5038 -s 885671393 shell 'sh /tmp/antikythera_ncp768_20261011_1329/build_on_board.sh'
```

脚本复用板上已有的固定镜像 `sha256:bebccc6c4d26e18c475ea66874faa43abdec3668d728a5ece61081e8b267a900`，在 `--rm` 临时容器中从 Debian 官方源安装 `gcc libc6-dev`，用原定编译选项生成 `ncp768_bench`，输出文件留在挂载的测试目录。编译选项没有启用 fast-math。具体命令以[实际构建脚本](<../log/uno_q_ncp768_20261011_1329/build_on_board.sh>)为准，不需要手工抄一套不同命令。

**容器只负责构建，后续计时在板端主系统直接执行普通 ELF，不在容器内运行。** 固定基础镜像并不等于完全固定 apt 包版本，因此还应保存构建日志中的实际编译器与软件包版本。

首次 apt 失败的直接原因是板端时钟停在 2025-12-10，软件源签名尚未生效；本次没有关闭签名验证，而是先同步系统时钟后重试。日常建议完成网络配置后使用 NTP 自动校时。这次离线处理曾使用短时、仅增加 `SYS_TIME` 能力的容器同步电脑 UTC，记录在[校时日志](<../log/uno_q_ncp768_20261011_1329/board_clock_sync.log>)。若确需重现，时间应由 PowerShell 的 `(Get-Date).ToUniversalTime()` 当场生成，不能复制日志里的固定时间。

已使用 GCC 14.2.0 在板上完成原生 AArch64 编译，并确认生成程序可在主系统运行；日志见[板端构建记录](<../log/uno_q_ncp768_20261011_1329/board_build.log>)。生成 ELF 的 SHA-256 为 `a09b7e51d520ad32f107a4c9d100b0d5be68453d4d362facffff85cce726f867`。

如果以后已经在主系统配置好 C 编译器，可以直接执行下面的替代构建命令；本次未采用这一安装方式：

```sh
cd /tmp/antikythera_ncp768_20261011_1329
cc -std=c99 -O3 -Wall -Wextra -Werror -fno-fast-math bench.c autoncp.c -lm -o ncp768_bench
```

### 4. 在主系统运行三个独立进程

下面在 **板端 Linux shell** 中执行；本次三个独立进程均已运行成功。保留标准错误便于发现失败原因：

```sh
cd /tmp/antikythera_ncp768_20261011_1329
./ncp768_bench > run1.json 2> run1.stderr
./ncp768_bench > run2.json 2> run2.stderr
./ncp768_bench > run3.json 2> run3.stderr
```

逐条确认退出状态；出现编译失败、非零退出或 parity failed 时停止汇总，保留错误信息。不要为了让数值比较通过而放宽容差或打开 `-ffast-math`。

三次是独立进程运行，**不是三次板子重新启动**。程序不做主动 50 ms 调度，报告的是连续执行下的内核耗时。系统后台负载、CPU 调频和温度可能影响结果，测试记录应保留当时条件。

### 5. 拉回日志并汇总

回到 **Windows PowerShell**：

```powershell
& $unoAdb -P 5038 -s 885671393 pull '/tmp/antikythera_ncp768_20261011_1329/run1.json' '.\log\uno_q_ncp768_20261011_1329\run1.json'
& $unoAdb -P 5038 -s 885671393 pull '/tmp/antikythera_ncp768_20261011_1329/run2.json' '.\log\uno_q_ncp768_20261011_1329\run2.json'
& $unoAdb -P 5038 -s 885671393 pull '/tmp/antikythera_ncp768_20261011_1329/run3.json' '.\log\uno_q_ncp768_20261011_1329\run3.json'
```

然后在项目现有 Python 环境运行汇总脚本；这里 `python` 必须指向可用的项目解释器：

```powershell
python '.\log\uno_q_ncp768_20261011_1329\summarize.py' '.\log\uno_q_ncp768_20261011_1329\run1.json' '.\log\uno_q_ncp768_20261011_1329\run2.json' '.\log\uno_q_ncp768_20261011_1329\run3.json' --output '.\log\uno_q_ncp768_20261011_1329\summary.json'
```

汇总脚本要求三个不同路径的完整通过记录，会检查检查点 SHA、INT8 位宽、模型维度、参考输入标识、容差、逐目标误差和计时样本数量。不能将同一文件传入三次充当三次运行。主机上生成或运行的结果必须标注主机，不能写成 UNO Q 实测。

### 6. 清理本次临时网络转发

测试结束后移除这次创建的 USB 代理转发；在 **Windows PowerShell** 执行：

```powershell
& $unoAdb -P 5038 -s 885671393 reverse --remove tcp:17890
& $unoAdb -P 5038 -s 885671393 reverse --list
```

本次已执行移除命令且退出码为 0，随后转发列表为空，见[清理记录](<../log/uno_q_ncp768_20261011_1329/adb_reverse_after.log>)。只移除本次的 `17890`，不要用清除全部转发的命令影响其他会话。临时编译容器使用 `--rm`，退出后自动删除；编译器安装发生在临时容器中，板端主系统没有安装 gcc。测试二进制和日志在 `/tmp` 中，不应依赖重启后保留；电脑侧测试目录已保存复现材料与结果。

## 怎样解读延迟报告

本次结果来自 UNO Q 主系统直接执行的原生程序，非 Windows 主机结果，非容器计时。完整结果见[实测汇总](<../log/uno_q_ncp768_20261011_1329/summary.json>)。

同一检查点、同一 INT8 权重和同一参考序列，在历史 ESP32-S3 原始循环测试中平均为 **82.1763 ms**，p95 为 **82.189 ms**，最大为 **82.195 ms**。历史结果来自三次板启动，本次使用三个 Linux 进程；内存系统、编译器及调度条件也不同。它可以作为平台迁移前的参照，不能将差异全部归因于 CPU 主频。见 [ESP32 原始记录](<../log/esp32_compute_20261001/baseline_fp32/summary.json>)。

| 指标 | UNO Q 本次结果 | 含义 |
|---|---|---|
| 均值 | 11.279283 ms | 平均计算成本；不能代表最慢一次 |
| p50 | 11.274218 ms | 这批样本的中位数 |
| p95 | 11.330313 ms | 约 95% 的样本不超过该耗时 |
| p99 | 11.362135 ms | 观察较慢的尾部样本；本次样本量仍有限 |
| 最大值 | 11.375834 ms | 这批观测中最慢一次；不是未来最坏情况保证 |
| 超过 50 ms 的样本数 | 0 / 336 | 当前观测下是否出现单步超预算 |
| 逐目标 / 完整状态误差 | 全部通过 0.0002 容差 | 先确认模型行为一致，再解释速度 |

平均耗时参照比为 `82.1763125 / 11.279283369048 = 7.2856`，即本次平台配置的内核平均耗时约快 **7.29 倍**。相对于 50 ms 特征步进，按平均耗时算还剩约 **38.72 ms**，按本次观测最大值算还剩约 **38.62 ms**；这些只是计算预算差值，其他环节尚未计入。

逐目标的最大绝对误差如下，比较对象是同一量化模型的参考输出，单位为归一化输出值。**它是数值移植误差，不是预测 MAE、RMSE、R²，也不是关节角度误差。**

| 输出序号 | 目标名（检查点约定） | 最大绝对数值误差 |
|---|---|---|
| 1 | `glove_c1_thumb_mcp` | 1.29640102e-06 |
| 2 | `glove_c2_thumb_ip` | 1.38580799e-06 |
| 3 | `glove_c4_index_mcp` | 2.31713057e-06 |
| 4 | `glove_c5_index_pip` | 2.47359276e-06 |
| 5 | `glove_c7_middle_mcp` | 1.78813934e-06 |
| 6 | `glove_c8_middle_pip` | 1.69873238e-06 |
| 7 | `glove_c11_ring_mcp` | 2.03400850e-06 |
| 8 | `glove_c12_ring_pip` | 1.41561031e-06 |
| 9 | `glove_c15_little_mcp` | 2.03400850e-06 |
| 10 | `glove_c16_little_pip` | 1.46031380e-06 |

最终完整 768 维状态最大绝对误差为 **6.28083944e-06**，同样低于 `2e-4` 容差。

上表是丢弃每轮前 16 步后的统计。三轮进程的第一步分别为 **16.2659、15.5365、14.7234 ms**；包含预热的全部 384 步中，最大值为 **16.2659 ms**。这有助于理解首次调用与预热后耗时的差别；进程首次调用不代表整块板子从断电到可用的启动时间。原始每步记录及二进制身份保存在[实机验证记录](<../log/uno_q_ncp768_20261011_1329/verification.json>)。

测试后环境记录保留了 `schedutil` 状态及数分钟后的温度、频率读数。它们不是逐步温度监测，当前频率也不能当作推理期间的固定频率；本次没有锁频或做长时间热稳定性测试。

本次汇总使用 nearest-rank 分位数。只有 336 个保留样本时，p99 主要受最慢的少数几个样本影响；不应把短测试的稳定结果写成长期实时保证。

项目特征每 50 ms 更新一次。若串行处理的平均耗时持续超过 50 ms，数据会逐步积压；某一步超过 50 ms 则表示该步超出计算预算，能否随后赶上还取决于其他步骤及缓冲策略。当前模型内核低于 50 ms，给其他工作留出了余量，还需要测采集、通信、因果滤波、特征提取与输出的整体调度。

项目还使用 200 ms 滑动窗口：这是特征所需的信号历史长度，与模型计算时间是两个不同量。不能简单把 200 ms 窗长称作每次更新都新增的等待时间，也不能只报几毫秒推理就声称用户动作到输出只有几毫秒。

最终应分别报告：

- **纯内核耗时**：本次测试回答的问题。
- **连续数据链路是否跟得上 20 Hz**：持续输入、处理和传输是否积压。
- **动作到输出的实际延迟**：包含窗口策略、信号处理、传输、调度和输出环节。

## USB 不识别时的排查顺序

1. **先查 Windows 是否有当前设备。** 比较插入前后的已连接 USB / ADB / COM 设备。历史设备记录不能当作在线证据。
2. **查物理数据连接。** 使用确认支持数据的 USB-C 线，优先电脑直连，减少扩展坞与转接头；用户本次已执行重插并等待启动。
3. **区分“有电”和“系统可用”。** 指示灯亮仍可能没有 USB 数据通信；记录灯态和启动变化，但不凭灯态确定故障根因。
4. **设备已枚举后再查驱动与 ADB。** 对照官方工具版本，记录 `adb devices -l` 的具体状态。不存在 USB 枚举时，先解决这一层。
5. **如果已配置网络，尝试 SSH。** SSH 成功而 USB 失败可以缩小问题范围；本次还没有这样的证据。
6. **最后考虑官方更新或恢复。** 仅在有系统故障或版本问题证据时走官方恢复流程，先保存可读取的数据。

目前 Windows 硬件重新扫描曾被权限限制阻止，这只是某个诊断操作未完成，不等于已证明驱动损坏或板子损坏。现有证据不足以在 USB 线、主机端口、板端启动、供电和软件之间确定唯一根因。

如果一直有电但 Linux 疑似没有正常启动，官方手册说明可以长按**电源按钮**约 5 秒重启 Linux 侧；这与 STM32 的复位按钮不同。释放后等待启动，再复查 Windows 当前设备和 ADB 列表。本次已恢复连接，但现有证据不能确定是哪项操作解决了此前问题。见 [UNO Q 用户手册](https://docs.arduino.cc/tutorials/uno-q/user-manual/)。

## 之后接 ADS1298 要注意什么

UNO Q 不应按经典 5 V Arduino UNO 的电平习惯直接接线。接 ADS1298 前，必须针对实际使用的 MCU 引脚确认电平、耐压、供电和地参考，以 [UNO Q 官方硬件文档及其引脚资料](https://docs.arduino.cc/hardware/uno-q/) 为准。本次未验证 ADS1298 到 UNO Q 的接线或采集驱动。

若后续采用 STM32 采集、Linux 推理，应使用缓冲与分批通信，并测量 Bridge 的吞吐和调度抖动。8 通道、2 kHz、每帧 27 字节对应 54,000 字节/秒原始帧负载；这个计算不包括协议开销，也不能证明某种通信方式已满足要求。

实时控制输出适合由 MCU 承担，Linux 侧负责模型与较复杂处理。较大的内存和较快的 CPU 不会自动修复电极接触、参考偏置、信号单位、ZC 阈值或个人数据适配问题。

## 证据与官方入口

- [UNO Q 实测汇总](<../log/uno_q_ncp768_20261011_1329/summary.json>)
- [实机验证、文件身份与逐轮记录](<../log/uno_q_ncp768_20261011_1329/verification.json>)
- [测试后环境记录](<../log/uno_q_ncp768_20261011_1329/board_environment_after.log>)
- [已拉回且核对哈希的 AArch64 二进制](<../log/uno_q_ncp768_20261011_1329/ncp768_bench>)
- [本次构建清单与模型身份](<../log/uno_q_ncp768_20261011_1329/build_manifest.json>)
- [测试 C 程序](<../log/uno_q_ncp768_20261011_1329/bench.c>)
- [准备脚本](<../log/uno_q_ncp768_20261011_1329/prepare.py>)
- [结果校验与汇总脚本](<../log/uno_q_ncp768_20261011_1329/summarize.py>)
- [Windows 工具自检结果](<../log/uno_q_ncp768_20261011_1329/host_check/self_check.json>)
- [Windows 逐目标数值误差](<../log/uno_q_ncp768_20261011_1329/host_check/summary.json>)（不是 UNO Q 性能）
- [2026-10-11 连接证据](<../log/uno_q_ncp768_20261011_1329/connection_status.json>)
- [Arduino UNO Q 硬件文档](https://docs.arduino.cc/hardware/uno-q/)
- [UNO Q Debian 指南](https://docs.arduino.cc/tutorials/uno-q/debian-guide/)
- [Arduino App Lab 系统刷写指南](https://docs.arduino.cc/software/app-lab/configure/flash/)
- [UNO Q 板端软件过旧处理](https://support.arduino.cc/hc/en-us/articles/23170731875740-If-your-Arduino-UNO-Q-board-software-is-out-of-date)
- [Arduino 官方工具包索引](https://downloads.arduino.cc/packages/package_index.json)

**验证边界：** 文中的模型和测试约定核对自当前材料；USB / ADB、上传、源文件哈希、板端环境、原生编译、三次纯内核推理和数值核对已完成；SSH、ADS1298 采集、人体输入、端到端延迟、长期稳定性和功耗尚未验证。未执行系统重刷或 STM32 固件更新；不能把模板命令或主机运行结果当作 UNO Q 成功部署记录。


