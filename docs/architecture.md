# 架构、数据契约与验证边界

**2026-10-13：本阶段暂时完结，开发暂停。** 真实 EMG 到预测界面未验收。清理和最终检查见 [收尾记录](project_closeout.md)，操作导航见 [根 README](../README.md)。

## 两条数据路径

| 层次 | DB2 研究路径 | 物理采集与候选部署 |
|---|---|---|
| 输入 | DB2 原始 12 通道，可选择一基通道子集 | ADS1298 八路差分输入、ESP32-S3 传输 |
| 预处理 | 离线 DC 去除、50 Hz notch、20–450 Hz bandpass；零相位滤波使用未来样本 | 保留因果 notch/bandpass；完整训练/在线滤波一致性未验收 |
| 窗口 | 默认 200 ms / 50 ms，末样本加显式目标偏移 | 候选为 200 ms / 50 ms |
| 特征 | CLI 默认 RMS；API 保留 MAV/MAVS/WL/ZC/SSC/RMS | NCP768 固定八通道交错 RMS/ZC，共 16 维 |
| 模型 | Dense CfC 与 AutoNCP CfC | NCP768 启动自检，未接入真实电极持续推理 |
| 输出 | J10 原数据手套坐标、逐目标评估 | UI 为公共模板引导，非个人预测 |

维度相同不足以证明源模型与设备相容。设备包声明输入微伏，新会话写盘转换为 V；DB2 `.mat` 中 `emg` 存储单位依据仍缺失，不能猜测比例。固定 ZC 阈值、归一化、真实 ATL 和推理传输也需独立完成。`/api/train`、`/api/live` 保持显式拒绝。

## 代码入口

| 路径 | 职责 |
|---|---|
| `src/dataflow/datapreprocess.py` | DB2 读取、离线滤波 |
| `src/dataflow/SwRectify.py` | 滑窗与目标对齐 |
| `src/dataflow/feature_extraction.py` | 按需特征及共用 μ-law 数学 |
| `src/dataflow/doa_mapping.py` | J10 选择及历史映射 API |
| `src/deep learning/train.py` | 模型、研究配置、状态批次、训练评估库 |
| `src/deep learning/run_db2_paper_cfc_finetune.py` | 预训练、支持/查询隔离、ATL 与实验记录 CLI |
| `src/hardwareOperation/export_weights.py` | Dense 导出，要求归一化依据 |
| `src/hardwareOperation/export_autoncp.py` / `verify_autoncp.py` | AutoNCP 导出及量化参考一致性 |
| `src/hardwareOperation/live_emg.py` | 因果滤波与 RMS/ZC，未接入真实模型归一化 |
| `firmware/main/` | portable Dense C 库和 fixture，无可刷应用入口 |
| `firmware/model/` | AutoNCP C 运行时及实验内核 |
| `firmware/ads1298_autoncp/` | 独立 ESP-IDF 应用、构建、自检与计时 |
| `firmware/diagnostics/` | 独立 ADS1298 bring-up / ADC GPIO 探针 |
| `ui/` | 串口采集、保存、引导与渲染 |

性能入口改为 `firmware/ads1298_autoncp/profile_autoncp.py`，避免旧名 `profile.py` 遮蔽标准库；历史日志保留原名与构建身份。

## 目标、特征与归一化

J10 一基 glove 通道 `2,3,5,6,8,9,12,13,16,17`，零基 `1,2,4,5,7,8,11,12,15,16`。API 另保留 `doa5`、`glove_columns`。逆变换输出为原数据尺度，无解剖标定依据时不称为度。

特征按通道优先、通道内 `feature_order` 排列。只计算请求特征，ZC/SSC 需要阈值时才校准。改变通道或特征顺序后不能沿用不匹配检查点。

μ-law 为 `sign(x) * log1p(mu * abs(x)) / log1p(mu)`，逆变换使用 `expm1`。CLI 默认 `mu=255`，中心/尺度等身份随检查点保存；统计只由监督源数据拟合，不能重拟合查询集。底层 `CfCTrainingConfig` 保留其他研究默认值，并非纸协议 CLI 默认值副本。

默认 8 窗覆盖 `200 + 7*50 = 550 ms` 历史。`--target-offset-samples` 必须显式提供：DB2 2 kHz 下 `0` 为同步估计，`200` 为提前 100 ms 的目标。偏移、历史覆盖、计算耗时、实际动作到输出延迟分别报告。

## 模型与完整状态

`build_cfc_regressor()` 支持 `dense_cfc_linear` 和 `autoncp_cfc_linear`。Dense 使用可配置骨干和线性头。AutoNCP 固定 `sparsity_level=0.5`、seed `22222`，`hidden_units` 指总状态，motor 特征经 dropout/线性头读出。J10 十维 motor 不是完整隐藏状态。

NCP768 为 455/303/10 三层、16 输入、完整 768 状态、10 输出。C 保留训练 `ts=1`，不能因 50 ms 更新改为 `0.05`。ff1/ff2 使用连接掩码，time_a/time_b 保持完整连接。模型族不能跨族恢复；头部微调只更新线性读出，ATL 对齐 motor 特征，递归携带完整状态。

## 切分、ATL 与评估

纸协议中源受试者所选重复全部监督，仅目标按动作重复划分 support/query。兼容参数 `--train-repetitions-per-action` 指目标支持重复数。保留记录/重复来源，不能将相邻重叠窗口随机分两侧冒充独立测试。

按完整时间流推进，仅在真实 recording/stream 边界复位；动作、休息、支持/查询变化不是复位边界。预热和未评分帧可推进状态，不进入损失。ATL 前，查询及其他未评分目标标签物理替换为 NaN，仅支持标签进入监督损失。

ATL 冻结源网络，交替更新判别器和目标网络。判别器更新使用 detached 特征；目标更新冻结判别器参数，梯度经过目标特征返回目标网络。现有加速路径可以复用目标前向结果，其等价性由回归测试约束。目标损失显式为 `L_mapping + L_subject`，无 GRL 或历史 λ 调度。预训练/ATL 按请求轮数返回末轮；查询标签不参与优化、选模。

主要评估 `evaluate_chain()` 按完整目标流携带状态，仅对 query 来源掩码评分；`evaluate_split()` 是独立序列诊断。报告逐目标 MAE、RMSE、R²及尺度；数值 parity 不代替预测指标。

## 导出与物理验证

Dense 导出保留量化、归一化与维度保护。`firmware/main` 的五输出 fixture 缺目标归一化，不是 J10 设备模型，仅显式允许 fixture 的主机测试可用。

AutoNCP 按输出行量化。本次平台对比为 INT8 权重、FP32 激活/偏置/运算。整数点积、SIMD、探针等为显式实验选择，失败记录保留，见 [固件历史报告](../firmware/ads1298_autoncp/README.md)。

UNO Q 4 GB / Debian 13.2 内核平均 11.279283 ms、p95 11.330313 ms、p99 11.362135 ms、最大 11.375834 ms。3 个进程各 128 步、丢前 16，共 336 样本；历史 ESP32-S3 原始循环均值 82.1763 ms。只验证合成归一化输入与同一量化参考一致性，无持续采集、真实预测、热稳定性或端到端证明，见 [UNO Q 教程](uno_q_getting_started.md)。

UI 保留真实采集保存及 40×6 / 两动作计划。NCP512 仅绑定两动作会话基础模型来源。现有包无 ADC 序号/采样时间戳，主机记录不能证明内部零丢样；软件流程次数不等于人体信号质量合格。趋势一致、动作开始到屏幕响应小于 200 ms 的物理标准未验收，见 [UI 文档](../ui/README.md)。恢复开发先读 [收尾记录](project_closeout.md) 中未关闭边界。
