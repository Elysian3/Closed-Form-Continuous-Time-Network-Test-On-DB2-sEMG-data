# ESP32-S3 INT8整数点积与SIMD耗时报告

**历史实验归档（2026-10-13 收尾）：** 本阶段暂时完结。以下数值、失败与设备恢复状态描述原实验当时的证据，不是当前在线设备声明，也不是端到端验收。当前状态见 [收尾记录](../../docs/project_closeout.md)，后续平台结果见 [UNO Q 教程](../../docs/uno_q_getting_started.md)。复测 CLI 原名 `profile.py` 已迁移为 `profile_autoncp.py`，以下操作入口已同步；`log/` 原始脚本和历史构建身份保留原名。

日期：2026-09-27。同一块COM3 ESP32-S3、240 MHz、2 MB Quad PSRAM/80 MHz、INT8 S1 AutoNCP768、模型代码-O3。此次只测试计算内核；默认浮点实现保留，实验后恢复原Flash固件。

**结论：** 标量整数平均90.031 ms，较浮点基线82.176 ms慢9.56%；当前SIMD实现平均81.130 ms，缩短1.27%。三者均超过50 ms特征步进预算。128步固定语料18次启动均严格通过2e-4，但1024步全零序列的C/独立整数参考核验失败，不能把本轮计时通过当作部署数值接受。

## 实验实现与范围

原版本只有权重存储为INT8；每次乘法将权重转成float，点积、偏置、非线性和隐状态均为FP32。实验把每层拼接输入动态量化一次，复用于该层四个FC，输出头再量化一次；用INT8×INT8→INT32点积，随后按输入/权重scale恢复FP32并加偏置。sigmoid、tanh、时间门、状态融合及归一化保持FP32。本实验不宣称全整数CfC。

输入scale为max(abs(x))/127，非零时下限FLT_MIN，整向量为零时scale=1；q采用半格远离零、限制[-127,127]。最大点积绝对界为758×127×127=12,225,782，INT32足够；保持输入有限值检查与失败不提交隐状态。

SIMD用S3原生16路有符号INT8指令；16字节对齐输入缓冲768 B，每行PSRAM权重复制到另一个768 B DRAM缓冲并补零至16倍数。原始行宽471/758/313未对齐，故这测的是“PSRAM取行→DRAM复制/补零→SIMD”，并非直接从PSRAM做对齐向量加载。复制、补零、每行临界区保护、点积与FP32恢复均在FC耗时内。ACCX每16元素块清零、读取并累加，仍是实验实现，不代表所有可行SIMD内核的性能上限。未增加ESP-NN依赖。

原生点积采用Espressif公开指令序列：[ESP32-S3技术参考手册](https://documentation.espressif.com/esp32-s3_technical_reference_manual_en.pdf)、[ESP-NN dot源码](https://github.com/espressif/esp-nn/blob/master/src/common/esp_nn_dot_s8_esp32s3.S#L514)。存档ELF反汇编确认ee.zero.accx、两次ee.vld.128.ip、ee.vmulas.s8.accx、rur.accx_0。每个SIMD启动先检验10种长度×3种模式，共30例，覆盖全正127、负127×正127、混合符号与补零尾部；6次SIMD启动均通过。

## 测试设计与计时边界

三个内核各stage/clean两种构建，各3次USB硬件复位，共18次启动。每轮128步：固定seed=20260920，前8步零，其余uniform[-1,1]；两套参考向量的128输入文本逐字节相同，输出参考随计算方式分别生成。每个序列只复位一次，隐状态连续。全部128步的10输出和最终768维状态都验数值，阈值固定2e-4；前16步仅不计入统计。每行统计n=3×112=336。

stage构建先运行关闭分段探针的control序列，再复位运行开启探针的stages序列；clean编译移除分段探针，只留外部总时钟。共核验2304个control步、1152个stages步，另有每启动12步自检。基准时尚未启动ADS1298采集，输入复制、golden比较、输出日志、步间vTaskDelay(1)均在总时钟外。这里是暖缓存、单owner、合成输入内核计时，不含DSP、采集、USB、UI或实际控制。临界区保护成本已计入，但未验证ADC/USB并发下行为。

## 整步耗时

单位ms；clean为主要比较依据。P50/P95/P99采用nearest rank ceil(p×n)，最大值仅代表本批样本。

| 内核/探针 | 平均 | P50 | P95 | P99 | 本批最大 |
| --- | ---: | ---: | ---: | ---: | ---: |
| INT8权重 / FP32点积 / 编译移除 | 82.176 | 82.177 | 82.189 | 82.191 | 82.195 |
| INT8权重 / FP32点积 / 运行关闭 | 82.179 | 82.180 | 82.191 | 82.194 | 82.198 |
| INT8权重 / FP32点积 / 运行开启 | 82.207 | 82.208 | 82.219 | 82.222 | 82.226 |
| INT8激活 / INT32标量点积 / 编译移除 | 90.031 | 90.031 | 90.043 | 90.046 | 90.049 |
| INT8激活 / INT32标量点积 / 运行关闭 | 90.025 | 90.025 | 90.036 | 90.040 | 90.042 |
| INT8激活 / INT32标量点积 / 运行开启 | 90.060 | 90.061 | 90.072 | 90.075 | 90.078 |
| INT8激活 / S3 SIMD点积 / 编译移除 | 81.130 | 81.131 | 81.142 | 81.146 | 81.148 |
| INT8激活 / S3 SIMD点积 / 运行关闭 | 81.151 | 81.151 | 81.163 | 81.166 | 81.169 |
| INT8激活 / S3 SIMD点积 / 运行开启 | 81.187 | 81.187 | 81.199 | 81.203 | 81.204 |

## 分段耗时

单位ms，均为stage开启时的平均值，n=336；FC含bias/scale恢复，SIMD还含复制/补零/保护。余量包含拼接、检查、状态提交、外部计时范围差和探针成本，不能全部视为时钟成本。分位数不能跨段相加。

| 部分 | 浮点 | 标量整数 | SIMD整数 |
| --- | ---: | ---: | ---: |
| L0 ff1 | 9.441 | 10.289 | 9.296 |
| L0 ff2 | 9.450 | 10.293 | 9.305 |
| L0 time_a | 9.452 | 10.292 | 9.303 |
| L0 time_b | 9.452 | 10.293 | 9.302 |
| L1 ff1 | 10.065 | 10.966 | 9.742 |
| L1 ff2 | 10.072 | 10.968 | 9.743 |
| L1 time_a | 10.069 | 10.968 | 9.742 |
| L1 time_b | 10.070 | 10.968 | 9.744 |
| L2 ff1 | 0.143 | 0.156 | 0.144 |
| L2 ff2 | 0.141 | 0.153 | 0.141 |
| L2 time_a | 0.141 | 0.154 | 0.142 |
| L2 time_b | 0.139 | 0.152 | 0.139 |
| 输出头 | 0.010 | 0.010 | 0.019 |
| 全部矩阵合计 | 78.646 | 85.663 | 76.763 |
| L0非线性/融合 | 1.977 | 1.980 | 1.974 |
| L1非线性/融合 | 1.323 | 1.325 | 1.327 |
| L2非线性/融合 | 0.042 | 0.047 | 0.042 |
| 非线性合计 | 3.342 | 3.352 | 3.343 |
| 输入量化0 | 0.000 | 0.249 | 0.249 |
| 输入量化1 | 0.000 | 0.400 | 0.400 |
| 输入量化2 | 0.000 | 0.166 | 0.166 |
| 输入量化3 | 0.000 | 0.007 | 0.007 |
| 输入量化合计 | 0.000 | 0.822 | 0.823 |
| 未归入上述段的余量 | 0.220 | 0.223 | 0.257 |
| 整步 | 82.207 | 90.060 | 81.187 |

当前SIMD矩阵段比浮点减少1.882 ms，同时新增量化0.823 ms，整步只减少约1 ms。未单独拆分复制与点积，因此不能凭此次结果断言PSRAM、复制或指令中哪一项主导；也不能把当前实现约1%的收益外推为优化过的所有INT8/SIMD实现。

| 内核 | 开启探针−运行关闭（µs） | 运行关闭−编译移除（µs） |
| --- | ---: | ---: |
| INT8权重 / FP32点积 | 28.202 | 2.792 |
| INT8激活 / INT32标量点积 | 35.616 | -6.708 |
| INT8激活 / S3 SIMD点积 | 35.476 | 21.333 |

这些是观测均值差；重新编译还会改变代码布局。小幅负差值不能解释为负的时钟开销，主要结论以clean对照为准。


两次连续时钟读数平均开销范围0.797–0.902 µs，最大2 µs，未逐段机械扣除。

## 128步核验与新增量化偏差

板端核验是C实现对各自golden的误差；激活量化偏差是整数参考对原INT8权重/FP32计算参考的差，两者不能混淆。后者并无2e-4接受承诺，也不等于真实模型预测精度。单位均为归一化模型输出。

| 内核 | 板端对自身参考最大输出差 | 最终状态最大差 | 18次固定语料结果 |
| --- | ---: | ---: | --- |
| INT8权重 / FP32点积 | 2.9988587e-06 | 8.1807375e-06 | 本内核6次均过2e-4 |
| INT8激活 / INT32标量点积 | 1.7881393e-07 | 1.7881393e-07 | 本内核6次均过2e-4 |
| INT8激活 / S3 SIMD点积 | 1.7881393e-07 | 1.7881393e-07 | 本内核6次均过2e-4 |

| J10目标 | 激活量化最大绝对差 | 平均绝对差 | RMSE |
| --- | ---: | ---: | ---: |
| glove_c1_thumb_mcp | 0.019580 | 0.004261 | 0.005492 |
| glove_c2_thumb_ip | 0.017328 | 0.004345 | 0.005779 |
| glove_c4_index_mcp | 0.033826 | 0.006396 | 0.008750 |
| glove_c5_index_pip | 0.039206 | 0.006099 | 0.008579 |
| glove_c7_middle_mcp | 0.023082 | 0.005067 | 0.006744 |
| glove_c8_middle_pip | 0.014742 | 0.004621 | 0.005820 |
| glove_c11_ring_mcp | 0.021035 | 0.005274 | 0.006947 |
| glove_c12_ring_pip | 0.018534 | 0.004361 | 0.005963 |
| glove_c15_little_mcp | 0.018778 | 0.006081 | 0.007737 |
| glove_c16_little_pip | 0.017356 | 0.004439 | 0.005983 |

128步最终768维隐状态最大偏差0.104190。输入、状态与scale经动态量化后，算法本身已变化，不能按‘只改存储类型’理解。

## 真实检查点的1024步持续递推检查

每类从零状态单独开始，主机编译同一模型C源，对独立NumPy INT32点积加ncps FP32 CfC参考核验；每步所有输出和状态都检查。仍是合成序列，不是实时动作准确度。原始输出/状态/参考数组均在NPZ保存并绑定SHA256。

| 1024步序列 | C/整数参考最大输出差 | 最大状态差 | 原2e-4 |
| --- | ---: | ---: | --- |
| uniform | 1.1920929e-07 | 2.9802322e-07 | 通过 |
| zero | 0.0077347755 | 0.029024795 | **失败** |
| smooth | 1.1920929e-07 | 2.9802322e-07 | 通过 |
| large_amplitude | 1.1920929e-07 | 2.3841858e-07 | 通过 |

**zero首次超限为零基第951步，即第952步。** free-running最大输出差约0.007735，最大状态差约0.029025；将每步上一状态强制设为同一独立参考状态，仍得到最大输出差0.006821、状态差0.002816，故不能简单归为跨步误差累积。没有放宽阈值或丢弃这类输入。CLI保存报告后返回失败退出码。

诊断在相同上一状态下：L0输入/scale/q完全相同；L1 FP32输入仅差8.94e-8，第296列缩放值分别为64.5与64.49999237，半格远离零使q变成65/64；L2差放大到0.002816并进一步改变q。将同一个C记录浮点向量分别送入C/NumPy量化器，各层q及scale完全一致。证据支持“上游FP32微差跨过动态量化的非连续边界”，未发现相同输入的量化实现不一致。这定位了失败机制，没有消除失败；目前不能接受为已验证的部署内核。

zero整数C相对原INT8权重/FP32 C的最大输出差约0.45676。下表保留四类序列的逐目标量化偏差；这些不是动作精度指标。

### uniform：整数C相对旧浮点C

| J10目标 | 最大绝对差 | 平均绝对差 | RMSE |
| --- | ---: | ---: | ---: |
| glove_c1_thumb_mcp | 0.032872 | 0.004773 | 0.006405 |
| glove_c2_thumb_ip | 0.030457 | 0.005450 | 0.007145 |
| glove_c4_index_mcp | 0.050040 | 0.006184 | 0.009137 |
| glove_c5_index_pip | 0.119107 | 0.007346 | 0.011707 |
| glove_c7_middle_mcp | 0.062710 | 0.006723 | 0.010185 |
| glove_c8_middle_pip | 0.051625 | 0.007078 | 0.009721 |
| glove_c11_ring_mcp | 0.042222 | 0.006065 | 0.008823 |
| glove_c12_ring_pip | 0.036274 | 0.006162 | 0.008365 |
| glove_c15_little_mcp | 0.038230 | 0.005789 | 0.007846 |
| glove_c16_little_pip | 0.038489 | 0.006089 | 0.008351 |

最大状态偏差0.280587，最终状态偏差0.074392。

### zero：整数C相对旧浮点C

| J10目标 | 最大绝对差 | 平均绝对差 | RMSE |
| --- | ---: | ---: | ---: |
| glove_c1_thumb_mcp | 0.456759 | 0.095292 | 0.127283 |
| glove_c2_thumb_ip | 0.420022 | 0.033894 | 0.057966 |
| glove_c4_index_mcp | 0.172921 | 0.041644 | 0.054261 |
| glove_c5_index_pip | 0.240617 | 0.054877 | 0.073159 |
| glove_c7_middle_mcp | 0.293176 | 0.043451 | 0.059420 |
| glove_c8_middle_pip | 0.128452 | 0.027133 | 0.038149 |
| glove_c11_ring_mcp | 0.318750 | 0.041917 | 0.059055 |
| glove_c12_ring_pip | 0.098493 | 0.019155 | 0.026483 |
| glove_c15_little_mcp | 0.176366 | 0.034436 | 0.046078 |
| glove_c16_little_pip | 0.089850 | 0.021264 | 0.028025 |

最大状态偏差1.968328，最终状态偏差1.874636。

### smooth：整数C相对旧浮点C

| J10目标 | 最大绝对差 | 平均绝对差 | RMSE |
| --- | ---: | ---: | ---: |
| glove_c1_thumb_mcp | 0.150138 | 0.004222 | 0.010949 |
| glove_c2_thumb_ip | 0.047120 | 0.003497 | 0.006070 |
| glove_c4_index_mcp | 0.032321 | 0.003784 | 0.005540 |
| glove_c5_index_pip | 0.063706 | 0.004490 | 0.007399 |
| glove_c7_middle_mcp | 0.076952 | 0.003864 | 0.006864 |
| glove_c8_middle_pip | 0.046762 | 0.003718 | 0.006496 |
| glove_c11_ring_mcp | 0.052413 | 0.003121 | 0.005698 |
| glove_c12_ring_pip | 0.057293 | 0.003107 | 0.006057 |
| glove_c15_little_mcp | 0.048075 | 0.003216 | 0.005692 |
| glove_c16_little_pip | 0.044983 | 0.003248 | 0.006057 |

最大状态偏差0.997233，最终状态偏差0.202396。

### large_amplitude：整数C相对旧浮点C

| J10目标 | 最大绝对差 | 平均绝对差 | RMSE |
| --- | ---: | ---: | ---: |
| glove_c1_thumb_mcp | 0.025688 | 0.005489 | 0.007091 |
| glove_c2_thumb_ip | 0.041723 | 0.006186 | 0.008350 |
| glove_c4_index_mcp | 0.053632 | 0.006880 | 0.009733 |
| glove_c5_index_pip | 0.101126 | 0.008187 | 0.012461 |
| glove_c7_middle_mcp | 0.041239 | 0.005890 | 0.008132 |
| glove_c8_middle_pip | 0.057733 | 0.006375 | 0.008779 |
| glove_c11_ring_mcp | 0.037804 | 0.005600 | 0.007646 |
| glove_c12_ring_pip | 0.050038 | 0.005402 | 0.007469 |
| glove_c15_little_mcp | 0.028514 | 0.005397 | 0.007037 |
| glove_c16_little_pip | 0.047414 | 0.005437 | 0.007589 |

最大状态偏差0.366278，最终状态偏差0.131012。

## 证据身份与复核

| 构建 | 应用字节数 | SHA256 |
| --- | ---: | --- |
| float_stage | 2073136 | `be410efcbd2dfd1037fb9ed9094efa02dcf95b81d931043731533c889e6eaefe` |
| float_clean | 2071984 | `8cf961212e2422a892879cddcf51a77daae6cf12b42cc5df64c223206a2d0d9f` |
| integer_stage | 2073552 | `0622359476fc9045ca7bbb1c4d763c386400b8dbc090773d1e4262821b657cf3` |
| integer_clean | 2072352 | `801bc5d59f181c188a49c3c672595488664a8c8f8fddeb4bde07deae140bacb4` |
| simd_stage | 2073808 | `094bc04aef05de72b316752ec4fc68c41ec356c5b0dbbdcc6956e18c4917d23a` |
| simd_clean | 2072960 | `ec8b76900db58d8ddbf04004353b3e1e97100a1ec09ad5fc966bc75c0486cdbe` |

检查点SHA256：`f245f89db2866fc6c06d3ce4fbe48d8db62834bda7708fa6a07ff31f4c322a24`。

权重头SHA256：`dac17af644764754e452690f3c00c785dcaaf9586a3c2cd88b657d7916f6349a`。

浮点corpus：`db7d58c990b74dc6359de92d052319a55f7f5508b32d1235695cb32bb8e930de`；整数corpus：`9dff710bc14638e18c0bec8ed05925dd4fd8ddf1cc15ac1a9e76fe3dee8a4758`。两个corpus因输出golden不同而哈希不同，输入已核对完全一致。

模型C源SHA256：`cf079f4ef7b9adb71f56593729861f8a03019e42fc0d2daafa968fa3a6613655`。

全部原始证据位于项目根目录`log/esp32_integer_20260927/`（被Git忽略）：6组各3个boot原始日志/JSON、summary、build_identity、烧录/构建记录、host_integer.json及NPZ、zero_step951_quantization.json/.npz及临时trace源码/诊断脚本、最终测试日志、恢复记录。复核脚本finish_report.py重新校验原始日志SHA与解析一致性、app/ELF/源码/权重/语料SHA、编译开关、存储映射、数值核验及恢复。

**六个构建共用float_stage/build工作目录。** 每次构建完成后先将BIN、ELF、map、compile_commands、config、bootloader、分区表及相关源码存入各自evidence，再允许复用工作目录。各组烧录只用自己的evidence。当前工作目录是simd_clean，不代表float_stage；compile_commands保留真实工作路径，不伪造独立构建来源。

## 复测入口与源码变更

使用本机已有Python/Torch/ncps/NumPy/MinGW GCC与ESP-IDF，未安装依赖。以下准备/构建只生成本地结果，不烧录；capture要求先烧录与参数相符的完整三份镜像，并选择没有旧boot记录的输出目录。

```powershell
$modelPython = 'C:/Users/14148/AppData/Local/Programs/Python/Python313/python.exe'
$devicePython = 'C:/Espressif/tools/python/v6.0.2/venv/Scripts/python.exe'
$checkpoint = 'log/db2_paper_cfc_finetune/source33_template_noquery_atl60_lr3e5_20260914_095931/weak_template_final.pt'
$modelExport = 'log/esp32_autoncp_20260921/weak_template_s1_int8'

& $modelPython firmware/ads1298_autoncp/profile_autoncp.py prepare $checkpoint --model-dir $modelExport --output log/esp32_integer_repeat/vectors --integer-fc
& $modelPython firmware/ads1298_autoncp/integer_reference.py $checkpoint --model-dir $modelExport --output log/esp32_integer_repeat/host.json --steps 1024
# 上述实际checkpoint零序列预计保留2e-4失败，报告保存后退出1。
& $modelPython firmware/ads1298_autoncp/build.py --model-dir $modelExport --sdkconfig log/esp32_integer_20260927/simd_clean/sdkconfig --build-dir log/esp32_integer_repeat/build --profile-dir log/esp32_integer_repeat/vectors --model-o3 --integer-fc --simd-fc --no-stage-clocks --size
& $devicePython firmware/ads1298_autoncp/profile_autoncp.py capture --vectors log/esp32_integer_repeat/vectors --output log/esp32_integer_repeat/capture --storage psram --kernel int8_simd --runs 3
$env:PYTHONPATH = 'tests'
& $modelPython -m unittest test_autoncp_firmware test_autoncp_integer test_autoncp_profile -v
```

标量整数去掉--simd-fc；分段构建去掉--no-stage-clocks；浮点构建去掉--integer-fc/--simd-fc并使用不带--integer-fc生成的参考。INTEGER/SIMD默认关闭，整数实验只允许INT8且要求profile目录，不改变默认部署计算方式。

实现触及firmware/model/autoncp.c/.h、main/model_boot.c、main/CMakeLists.txt、build.py/profile.py；新增integer_reference.py；测试涉及test_autoncp_firmware.py、test_autoncp_integer.py、test_autoncp_profile.py。仅增加实验分支、量化计时和可复核参考，未改训练、DSP或ADS驱动。9项针对性测试全部通过，包含无探针/有探针一致性、量化规则、独立整数参考、失败状态保护、profile结构新字段布局以及缺失SIMD原生自检时拒绝证据。三个Python入口语法编译通过，6份ESP-IDF构建成功。1024零序列失败另列，不计作“全部数值通过”。

## 最终设备状态

实验开始前读回核对原三份Flash镜像，实验后恢复同一bootloader/分区表/app，三份写入哈希和再次读回核验均通过。原app SHA256为`e89b6d6c417404e47c3c2f5a73a9c00e47cbda23ba26e5fc39430919c60f6f78`。恢复后12步自检通过，最大误差6.10948e-07，最大单步127.520 ms。ADS1298仍读零而中止，最后停在ROM下载模式避免重复重启，COM3释放，未启动波形流。详见restore_flash.log、restored_boot.log/json、restore_verify_and_park.log。

真实演示仍需完成稳定采集、信号单位/固定ZC阈值依据、因果DSP、持续推理和结果到UI。本轮结果支持保留现有浮点演示基线；标量整数无耗时收益，当前SIMD收益小且存在长序列数值失败，暂不作为演示默认内核。
