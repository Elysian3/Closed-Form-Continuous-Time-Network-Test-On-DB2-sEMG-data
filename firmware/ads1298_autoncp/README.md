# ADS1298 + AutoNCP 候选固件

**收尾状态（2026-10-13）：本阶段暂时完结，开发暂停。** 本独立 ESP-IDF 工程与启动自检保留；真实电极持续推理、个人 ATL 和预测到 UI 未接通。以下带日期的实验结果及设备状态均为历史记录，不能当作当前在线设备状态。2026-10-04 曾恢复真实采集，但间歇异常根因未确定；NCP512 仅绑定采集基础模型来源。后续 UNO Q Linux NCP768 合成内核测试均值 11.279283 ms，不是端到端验收，见 [UNO Q 教程](../../docs/uno_q_getting_started.md) 与 [收尾记录](../../docs/project_closeout.md)。性能 CLI 已从 `profile.py` 迁移为 `profile_autoncp.py`；历史日志不改写。

**2026-09-27 整数计算实验：** 已完成三种内核、stage/clean六种构建、18次启动的严格128步核验，详见 [整数点积与SIMD耗时报告](integer_report_20260927.md)。PSRAM、240 MHz、模型-O3、编译移除分段探针时，浮点平均82.176 ms、标量整数90.031 ms、当前原生SIMD81.130 ms；标量整数慢9.56%，SIMD缩短1.27%，均超过50 ms步进预算。新增激活量化的1024步全零主机核验在第952步失败，未放宽2e-4，实验内核默认关闭。原Flash固件已恢复并读回核验；ADS1298仍读零而中止，最后停在ROM下载模式，COM3已释放。

**2026-09-27 分段基准：** 已完成四种构建、12次启动的128步连续状态测试，详见 [推理与特征耗时报告](latency_report_20260927.md)。编译移除分段时钟时，Flash平均127.518 ms、PSRAM平均82.176 ms；PSRAM矩阵路径约占带探针总时间95.67%。报告包含每层FC/激活、辅助函数及PC现有特征路径的耗时、平方和复用公式自检和复测入口。USB波形测试因握手写超时未启动，ADS1298间歇读零仍未解决。原Flash固件已恢复并读回核验，板卡停在ROM下载模式，串口已释放。

**2026-09-23 测试后状态（历史记录）：** 已完成相同 INT8 模型在 Flash 与 PSRAM 只读段中的启动推理对照。PSRAM 三轮最大单步均为 82.174 ms，Flash 恢复前后均约 127.52 ms；这只覆盖合成输入的模型内核。测试后已恢复原 Flash `-O3` 固件，写入哈希通过；由于恢复后的三次 ADS1298 配置读回均为零并重启，最后将板卡停在 ROM 下载模式，串口已关闭，未发送波形 START。间歇读零原因仍未定位；真实电极推理仍未接通。详情见下方 PSRAM 对照记录。

2026-09-20：已导出两套最新检查点，并构建包含完整 AutoNCP 数值内核的 ESP32-S3 候选固件。目标架构是 **PC 做 ATL，ESP32 做推理，UI 接收结果**。

**固件只在启动时执行合成输入模型自检，随后尝试初始化 ADS1298 原始采集。尚未接入真实电极推理。** 2026-09-21 已完成三种配置上板对比：INT16/160 MHz/-Og 为 274.8 ms，INT8/240 MHz/-Og 为 177.4 ms，最新 INT8/240 MHz/-O2 为 127.5 ms。板上保留最新版本，因本轮 ADS1298 读回为零而暂留 ROM 下载模式；此前两次正常读回不能消除间歇异常，详见下方记录。DB2 EMG 存储单位与设备输入微伏之间的转换依据、目标会话的固定 ZC 阈值仍缺失。完整信号处理、持续推理调度和预测结果到 UI 的通道也需完成，不能通过解除一个开关代替这些工作。

## 本次改动

- `../model/autoncp.c` 实现三层 wired CfC、持续隐状态、线性输出头和检查点的 µ-law 归一化。仅 ff1/ff2 使用连接掩码；time_a/time_b 保持完整。模型内部时间步为训练使用的 `ts=1`，不替换成 50 ms。
- 权重按输出行量化，默认保存在 Flash 只读段；PSRAM 对照构建在启动时搬迁整个只读段。激活和累加仍使用浮点计算，临时工作区使用内部 RAM。支持 INT8/INT16，初版基线为 INT16，另有用户指定的 INT8/240 MHz 对比版本。默认固件不要求 PSRAM；INT8 权重存储不等同于整数 SIMD 推理。
- 复用独立 ADS1298 采集实现：动态切换采样率/增益后核验 CONFIG1 和八个 CHnSET；失败时不启动采样、不更新生效参数、不发送成功响应。即使 DRDY 停止，控制任务仍能接受重试。
- 参数切换后的前三帧全部丢弃用于稳定，修复第三帧提前进入数据流的问题。
- 原始波形包格式、队列容量和队列满时的处理保持现状。尚无实测证据证明队列会满；这次没有加入 ADC 序号或丢样上报。

## 参数身份

以下路径均相对于项目根目录。两个检查点均为 AutoNCP768，层宽 455/303/10，输入为八通道交错 RMS/ZC 共 16 维，输出为 J10。窗口 200 ms、步进 50 ms；输出逆归一化后仍是原数据手套坐标，不能直接当作解剖角度。

| 用途 | 检查点 | SHA256 |
| --- | --- | --- |
| 最新 source630 源模型 | `log/db2_paper_cfc_finetune/new_stateful_test_12_weighted_ch2ch3_x2_lr3e-5_ep30_20260909_194121/checkpoints/S1_autoncp_cfc_pretrain.pt` | `dbbcf0ba7830757d6551aff078874564cb8dda65bf78833930328232fd20aae1` |
| 最新完成的 S1 弱模板 ATL | `log/db2_paper_cfc_finetune/source33_template_noquery_atl60_lr3e5_20260914_095931/weak_template_final.pt` | `f245f89db2866fc6c06d3ce4fbe48d8db62834bda7708fa6a07ff31f4c322a24` |

两个上板版本都嵌入第二套参数，用于数值验证。它是历史 **S1** 适配结果，不是本次佩戴者的新模型。INT16 导出目录分别为 `log/esp32_autoncp_20260920/source630_int16/` 和 `log/esp32_autoncp_20260920/weak_template_s1_int16/`；INT8 对比导出为 `log/esp32_autoncp_20260921/weak_template_s1_int8/`。

每个导出目录包含尺寸/权重头文件、启动自检向量、`manifest.json` 和主机验证生成的 `host_c_parity.json`。后者绑定检查点、C 源文件和权重头文件的 SHA256；生成文件位于忽略的 `log/` 中，可按以下步骤重建。

## 构建与核验

在项目根目录的 Windows PowerShell 运行。使用本机已有 Python 3.13、Torch/ncps、MinGW GCC 和 ESP-IDF 6.0.2，不自动安装依赖。

```powershell
Set-Location 'D:\Project Antikythera'
$modelPython = 'C:\Users\14148\AppData\Local\Programs\Python\Python313\python.exe'
$checkpoint = 'log/db2_paper_cfc_finetune/source33_template_noquery_atl60_lr3e5_20260914_095931/weak_template_final.pt'
$modelExport = 'log/esp32_autoncp_20260920/weak_template_s1_int16'

& $modelPython src/hardwareOperation/export_autoncp.py $checkpoint --output-dir $modelExport --weight-bits 16
& $modelPython src/hardwareOperation/verify_autoncp.py $checkpoint --model-dir $modelExport
& $modelPython firmware/ads1298_autoncp/build.py --model-dir $modelExport --size
```

切换到源模型时同时更换 `$checkpoint` 和 `$modelExport`。构建必须显式选择导出目录，避免静默使用另一套权重。

`build.py` 使用本机 `C:/esp/v6.0.2/esp-idf` 与 `C:/Espressif` 内的既有工具链，通过子进程环境指定路径，不改变系统配置。该安装没有 Python constraints 快照，因此仅跳过快照检查，仍运行 IDF 自带的依赖检查。在已配置好的其他 ESP-IDF 环境中，也可从本目录运行 `idf.py -DIDF_TARGET=esp32s3 -DMODEL_DIR=<导出目录绝对路径> build size`。

**这些命令只构建，不烧录。** 当前分区按 4 MB Flash 配置。2026-09-21 已在 COM3 检测到 4 MB Flash，芯片报告内置 2 MB PSRAM；本固件仍不启用 PSRAM。原工程的 2 MB Flash 配置不代表这块板的实物容量。

## 构建结果与运行链

INT16/160 MHz 基线应用镜像为 `build/ads1298_autoncp.bin`，大小 **3,829,280 字节**，SHA256：

```text
3f146ad23b1c650df1a962d8a76ade50293edc52d78878eeb80f216f42a836fb
```

应用分区起始 `0x10000`、容量 `0x3f0000`（4,128,768 字节），剩余 299,488 字节。该 `.bin` 是应用镜像，完整启动还依赖对应构建生成的 bootloader 和分区表；它不是从 Flash 地址零开始写入的合并镜像。

IDF 大小报告：Flash Data 3,654,980 字节，Flash Code 97,310 字节，DIRAM 静态占用 79,557 字节、链接器报告余量 262,203 字节，IRAM 16,384 字节。DIRAM 数据不包含后续动态分配的全部任务栈、队列和堆开销，不能当作完整运行时内存测量。INT16 权重本体 3,577,072 字节，浮点参数 24,872 字节，均放在只读区。

启动顺序：

1. 在 USB 二进制协议启动前，复位模型隐状态，执行 12 步固定的合成归一化特征。
2. 与导出的量化后 ncps 参考输出逐关节对照，绝对差异上限 0.0002；失败则终止启动。
3. 打印参数身份、自检误差和单步计算计时，再次清空隐状态。三种配置上板初测的最大单步耗时分别约 274.8/177.4/127.5 ms，计时范围不代表端到端延迟。
4. 初始化 ADS1298，读取 ID、核验配置，再启动原有命令接收和原始八通道采集。二进制协议启动后不插入模型文本或预测包。

## 已有验证证据

两套完整模型各进行 128 步持续状态的主机 C/ncps 对照。输入是固定随机种子的归一化合成特征，前八步为零。下面是各输出的最大绝对差异；Python 参考使用同一量化权重。

| J10 输出 | 源模型 C/Python | S1 ATL C/Python | S1 FP32/INT16 量化差异 |
| --- | ---: | ---: | ---: |
| 拇指 MCP（CH2） | 1.7434e-6 | 1.3411e-6 | 0.00051503 |
| 拇指 IP（CH3） | 1.5497e-6 | 1.1101e-6 | 0.00074925 |
| 食指 MCP（CH5） | 1.8924e-6 | 1.9819e-6 | 0.00078759 |
| 食指 PIP（CH6） | 1.4603e-6 | 2.1309e-6 | 0.00079361 |
| 中指 MCP（CH8） | 2.7418e-6 | 1.6689e-6 | 0.00062376 |
| 中指 PIP（CH9） | 1.6391e-6 | 1.6987e-6 | 0.00093532 |
| 无名指 MCP（CH12） | 1.4603e-6 | 1.7732e-6 | 0.00112975 |
| 无名指 PIP（CH13） | 1.8328e-6 | 1.6838e-6 | 0.00134099 |
| 小指 MCP（CH16） | 1.6242e-6 | 1.6019e-6 | 0.00107256 |
| 小指 PIP（CH17） | 1.4007e-6 | 1.7807e-6 | 0.00128597 |

全部隐状态的最大差异分别为 1.2041e-5、1.4380e-5，小于核验阈值 0.0002。S1 INT8 在同一输入上的最差关节量化差异约 0.280608，INT16 约 0.001341，因此本次候选选择 INT16。逐关节完整量化结果保存在各自 `manifest.json`，包括源模型和初始 INT8 导出。

这些是**数值一致性证据**，不是实际肌电预测精度、趋势一致性或时延验收。相关回归检查：

```powershell
& $modelPython -m unittest discover -s tests -p 'test_autoncp_firmware.py'
& $modelPython -m unittest discover -s tests -p 'test_ads1298_bringup_contract.py'
```

本次通过 3 个 AutoNCP 测试和 14 个 ADS1298 测试，覆盖 INT8/INT16 C 内核、归一化/逆变换、状态复位、非法输入/数值溢出不推进状态、错误模型契约拒绝、寄存器读回失败/重试、三帧稳定期和无 DRDY 时的控制响应。旧 DenseCfC 的 13 个测试也通过。

## 2026-09-21 INT16/160 MHz 基线上板初测

按用户要求停止备份，整片擦除后写入 bootloader、分区表和应用，三部分均通过写入哈希校验。应用镜像仍为上文 SHA256 对应的 3,829,280 字节版本，源代码和模型参数未为本次测试改变。使用本机 esptool 5.3.1 自带的 legacy stub 1 完成烧录，未更改工具安装。

芯片为 ESP32-S3 rev0.2，COM3，实测 Flash 4 MB，芯片信息报告 PSRAM 2 MB。基线启动日志确认 CPU 160 MHz、Flash 80 MHz DIO；PSRAM 未启用。基线使用 SDK 的 DEBUG 优化配置，模型编译命令为 `-Og`；240 MHz 和性能优化构建对比见后续两节。

| 启动轮次 | 自检步数 | 本轮最大单步耗时 | 最大数值自检差异 |
| --- | ---: | ---: | ---: |
| 1 | 12 | 274.775 ms | 5.96046e-7 |
| 2 | 12 | 274.775 ms | 5.96046e-7 |
| 3 | 12 | 274.774 ms | 5.96046e-7 |

三轮均核验到预期 S1 参数 SHA256、INT16、768 维状态和 10 维输出。计时只包围 `autoncp_step`，包括模型计算和状态提交，不包括自检输出比较、步间等待、采集/滤波/特征计算、归一化或 UI 传输。只有每轮最大值，未记录逐步均值或分位数；合成启动自检也不代表持续采集负载。**当前最慢单步已超过 200 ms，不能据此宣称满足整体延迟目标，更未达到每 50 ms 一帧的处理预算。**

每轮模型自检之后均出现 `ADS1298 ID: 0x00`、`CONFIG1 expected 0x84, got 0x00`，导致配置校验中止并重启。这个失败发生在模型计时之后，不改变已测到的推理耗时；它只证明当前 ADS 通信未通过，尚未定位接线、供电或其他原因。测试结束后让芯片停在 ROM 下载模式，并关闭串口，避免持续重启；候选固件仍在 Flash 中，复位会再次运行。

原始日志和结构化报告：`log/esp32_latency_20260921/` 下的 `erase.log`、`flash.log`、`boot_01.log` 至 `boot_03.log`、`park_bootloader.log` 和 `summary.json`。三轮日志哈希、模型身份及写入校验已核对；`capture_boot.py` 保留本次复位和串口计时采集方法。

## 2026-09-21 INT8/240 MHz 对比

按用户要求改为相同 S1 检查点的 INT8 权重和 240 MHz CPU。独立构建只改变 CPU 频率配置及导出权重位宽，模型 C 源码、Flash 80 MHz DIO、`-Og` 编译设置和不启用 PSRAM 均与基线一致。两项因素同时改变，未测量各自独立贡献。

| 配置 | 三次启动各自的最大单步耗时 | 应用镜像大小 |
| --- | --- | ---: |
| INT16 / 160 MHz / -Og | 274.775、274.775、274.774 ms | 3,829,280 字节 |
| INT8 / 240 MHz / -Og | 177.353、177.353、177.352 ms | 2,040,768 字节 |

比较两组最大值，计算耗时缩短 **35.455%**。INT8 三轮均运行 12 步并通过数值自检，最大输出差异 3.72529e-7；另有完整 128 步主机 C/量化 ncps 对照通过，最大输出差异 1.654029e-6、最大隐状态差异 9.790063e-6。

这些自检验证量化后模型的 C 实现。INT8 相对原始 FP32 模型的合成输入量化差异仍较大：最差关节最大绝对归一化差异 0.280608，INT16 为 0.001341。它不是百分比或实测角度误差，也不能证明运动趋势相同。全部十关节结果在导出目录 `manifest.json` 和本次 `summary.json` 中。

**177.4 ms 只覆盖模型计算。尚未测量完整链路，也未达到 50 ms 一帧的处理预算。** 当前是 INT8 权重、浮点激活/累加，未启用整数矩阵加速。

该组首轮 ADS1298 仍读回为零并重启；后两轮读到 ID `0x92`、CONFIG1 `0x84`、CONFIG2 `0x00`、CONFIG3 `0xC0`，八个通道配置全部符合预期，没有出现中止日志。此前异常原因仍未定位，后两次正常不能代替持续采集压力测试。第三轮结束后保留 INT8/240 MHz 固件运行并关闭串口，没有发送启动波形传输命令。

构建与原始记录位于 `log/esp32_latency_20260921/int8_240/`：`build/ads1298_autoncp.bin`、`sdkconfig`、`build.log`、`flash.log`、`build_identity.json`、三轮 `boot_*.log/json` 和 `summary.json`。三个烧录部分均通过写入哈希校验。应用镜像 SHA256：

```text
15754948ef42e6c432b42323a4562927f78fe1cc1bf2e54d0c38c2466f4c47a4
```

构建脚本支持指定独立配置/输出目录，保留基线产物。使用本次已有配置重建：

```powershell
& $modelPython firmware/ads1298_autoncp/build.py --model-dir log/esp32_autoncp_20260921/weak_template_s1_int8 --sdkconfig log/esp32_latency_20260921/int8_240/sdkconfig --build-dir log/esp32_latency_20260921/int8_240/build --size
```

## INT8/240 MHz/-O2 上板结果

用户要求继续优化后，已构建相同 INT8 权重、240 MHz CPU 的 SDK PERF（`-O2`）版本。模型内核与导出权重哈希均保持不变，没有启用 `-ffast-math`。SDK 随优化模式自动关闭只在 DEBUG 或特定调试功能下可用的 `FREERTOS_TASK_FUNCTION_WRAPPER`；完整配置差异记录在 `build_identity.json`。

应用镜像为 `log/esp32_latency_20260921/int8_240_o2/build/ads1298_autoncp.bin`，大小 **2,030,800 字节**，SHA256 为 `5f631994c0fe73d64984f6e6eda55e9d41bd8e0524b5250080328b56146605e3`。实际编译命令已核验为 `-O2`，镜像通过分区容量检查，已有相同源码/权重的主机 `-O2` 数值核验仍适用。

用户重新接入设备并要求烧录后，bootloader、分区表和应用均完成写入并通过哈希校验。三次 USB 复位各执行 12 步启动自检，日志确认 INT8、240 MHz、相同 S1 检查点和预期应用 ELF 身份；数值自检全部通过，最大误差 6.10948e-7。三轮最大单步耗时均为 **127.542 ms**。

| 配置 | 三轮中最大的单步耗时 | 与前一配置相比 |
| --- | ---: | ---: |
| INT16 / 160 MHz / -Og | 274.775 ms | 基线 |
| INT8 / 240 MHz / -Og | 177.353 ms | 缩短 35.455% |
| INT8 / 240 MHz / -O2 | 127.542 ms | 再缩短 28.086% |

相对最初基线累计缩短 53.583%。计时仍只覆盖 `autoncp_step`，不代表完整链路或持续采集负载下的延迟；仍超过每帧 50 ms 的处理预算。INT8 相对 FP32 的量化偏差没有因 `-O2` 消失，真实动作趋势尚未验证。

这三轮 ADS1298 的 ID/CONFIG1 再次全部读回零，启动校验中止后重启；本次未对接线/供电进行诊断。此前 `-Og` 也出现过相同失败并有两轮正常，不能只据此次结果把原因归于优化选项。测试结束后芯片留在 ROM 下载模式，新固件仍在 Flash，主机串口已关闭。

```powershell
& $modelPython firmware/ads1298_autoncp/build.py --model-dir log/esp32_autoncp_20260921/weak_template_s1_int8 --sdkconfig log/esp32_latency_20260921/int8_240_o2/sdkconfig --build-dir log/esp32_latency_20260921/int8_240_o2/build --size
```

该目录保留配置、构建日志、`build_identity.json`、`flash.log`、三轮 `boot_*.log/json`、`park_bootloader.log` 和 `summary.json`。模型身份、日志哈希和三份写入校验均已核对。

## 2026-09-23 模型内核 -O3 对比

使用 `--model-o3` 可选构建开关，仅给 `firmware/model/autoncp.c` 增加源文件级 `-O3`。实际编译命令为 `-O2 ... -O3`，最后的 `-O3` 生效；采集、自检和 SDK 保留原 `-O2`，没有启用 `-ffast-math`。不传此开关会显式关闭该覆盖，默认构建行为保持不变。

```powershell
& $modelPython firmware/ads1298_autoncp/build.py --model-dir log/esp32_autoncp_20260921/weak_template_s1_int8 --sdkconfig log/esp32_latency_20260923/int8_240_model_o3/sdkconfig --build-dir log/esp32_latency_20260923/int8_240_model_o3/build --model-o3 --size
```

模型源码和权重 SHA256 与上次 `-O2` 一致，CPU 240 MHz、Flash 80 MHz DIO、PSRAM 关闭。应用镜像大小为 2,031,296 字节，比 `-O2` 增加 496 字节；SHA256 为 `e89b6d6c417404e47c3c2f5a73a9c00e47cbda23ba26e5fc39430919c60f6f78`。bootloader、分区表和应用写入均通过哈希校验，三次启动 ELF 身份与该构建相符。

| 配置 | 三次启动各自的最大单步耗时 |
| --- | --- |
| 此前全局 -O2 | 127.542、127.542、127.542 ms |
| 本次仅模型 -O3 | 127.520、127.520、127.519 ms |

比较两组最大值仅减少 22 微秒（0.01725%），不足以构成有实用意义的提速，也不能凭这组小样本宣称稳定性能收益。每轮 12 步合成输入的数值自检均通过，最大差异均为 6.10948e-7，阈值为 0.0002。计时仍只包围 `autoncp_step`，没有连续采集负载或真实动作端到端验收；这次未重新运行主机 128 步 `-O3` 对照，板上证据覆盖上述启动输入。

三轮均读到 ADS1298 ID `0x92`、CONFIG1 `0x84`、CONFIG2 `0x00`、CONFIG3 `0xC0`，八个通道配置符合预期，捕获日志中未出现中止。正常启动不等于持续采集验证，也不证明此前间歇故障已经修复。第三轮后保留该版本运行，未发送波形传输 START，主机串口已关闭。

独立产物与证据位于 `log/esp32_latency_20260923/int8_240_model_o3/`：构建配置及日志、`build/compile_commands.json`、镜像、`flash.log`、`boot_01` 至 `boot_03` 的日志和 JSON、复用的 `capture_boot.py`、汇总 `summary.json`。旧 `-O2` 产物保留原目录。

## 2026-09-23 PSRAM 只读段推理对照

从上述 Flash `-O3` 配置派生独立 `sdkconfig`，仅启用 2 MB Quad PSRAM、80 MHz、`CONFIG_SPIRAM_RODATA` 和 `CONFIG_SPIRAM_USE_MEMMAP`；模型代码仍在 Flash，CPU 240 MHz、Flash 80 MHz DIO、其余源码与 INT8 权重保持一致。ESP-IDF 6.0.2 构建通过，应用镜像为 2,036,992 字节，SHA256 为 `5d5684e907a7c6f0ea6a42e360be0bee56525c93b124225c47d086b1b3f9f971`。启动日志确认找到 2 MB/80 MHz PSRAM、内存测试通过，并打印 `Read only data copied and mapped to SPIRAM`；链接器 map 确认模型权重数组位于 `.rodata`。这验证的是**整个只读段搬迁**，不是只搬模型权重。

| 同一块板的启动自检 | 三次启动各自的 12 步最大单步耗时 |
| --- | --- |
| 原 Flash 固件，测试前 | 127.520、127.519、127.519 ms |
| PSRAM 只读段固件 | 82.174、82.174、82.174 ms |
| 恢复原 Flash 固件，测试后 | 127.520、127.520、127.519 ms |

两种构建使用相同检查点、模型源码和导出权重；`autoncp.c` 最终编译选项仍为 `-O3`。九次启动的 12 步数值自检全部通过，最大误差均为 `6.10948e-7`，阈值为 `0.0002`。以 127.520 和 82.174 ms 比较，单步最大耗时减少 45.346 ms（35.56%）。这是小样本、固定合成输入的启动计时，只说明该构建在这块板上的模型内核耗时；没有测量逐步分布、持续运行、采集负载或物理端到端延迟。82.174 ms 仍超过 50 ms 特征步进。

PSRAM 固件第一次启动时 ADS1298 ID/CONFIG1 读回零，随后两次正常；恢复 Flash 后三次均读回零。两种构建都出现过该故障，现有证据不能把它归因于 PSRAM。测试结束已恢复原 Flash 镜像并核验三份写入哈希，随后把板卡停在 ROM 下载模式以避免反复重启，串口已释放。独立配置、构建与烧录日志、每次原始启动日志及 JSON、恢复日志和汇总均保存在 `log/esp32_psram_20260923/`。

## 尚未完成

- 可追溯的 DB2 存储单位依据，以及目标会话 ZC 阈值的校准与固定。
- 与训练一致的设备端因果滤波/特征计算、连续推理任务及采样/推理并行调度。
- PC ATL 结果部署到设备，以及设备预测值经协议传入 UI 的完整链路。
- 持续采集时的内存/吞吐/丢样测量，以及在实际调度下验证当前 PSRAM 构建约 82.2 ms 的模型单步与 50 ms 特征步进如何共存；Flash 构建约为 127.5 ms。两者均未证明可持续满足 20 Hz 处理预算。
- INT8 在真实数据上的逐关节效果验证；尚不能据合成输入测试确认精度或运动趋势保持不变。
- ADS1298 早期读回为零的原因定位与持续正常采集验证。
- 全部 40×6 动作采集和真实手指运动趋势、物理端到端小于 200 ms 的验收。

原始采集协议细节见 [ADS1298 说明](../diagnostics/ads1298_bringup/README.md)。
