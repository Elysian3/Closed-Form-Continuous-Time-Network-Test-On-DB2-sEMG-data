# Project Antikythera

Project Antikythera 研究面向资源受限假肢控制的 sEMG 连续手指目标解码，保留 DB2 训练、ATL、模型导出、八通道采集和本地界面。

**2026-10-13：本阶段暂时完结，开发暂停。** 已完成采集与模型内核验证；真实肌电到预测界面的端到端验收尚未完成。清理、保留依据及最终验证见 [项目收尾记录](docs/project_closeout.md)。

## 当前能力与边界

| 路径 | 已具备的能力 | 验证边界 |
|---|---|---|
| DB2 研究 | Dense CfC、AutoNCP CfC、J10、μ-law、源预训练及支持集 ATL | 研究协议与设备部署分别验证 |
| ADS1298 / ESP32-S3 | 八通道原始采集、参数读回、AutoNCP 启动自检 | 真实电极持续推理及预测通道未接通；间歇异常根因未确定 |
| 本地 UI | 串口采集、原始保存、40×6 与两动作计划、J10 引导 | 公共模板不是个人预测；真实 `/api/train`、`/api/live` 显式阻塞 |
| UNO Q | Linux 原生 C NCP768 内核与量化参考一致性核验 | 合成输入短测；无 ADC、真实 EMG 或端到端验收 |

UNO Q 4 GB / Debian 13.2 上，NCP768 INT8 权重、FP32 运算内核平均 **11.279283 ms**，p95 **11.330313 ms**，p99 **11.362135 ms**，最大 **11.375834 ms**。三个独立进程各运行 128 步、丢弃前 16 步，共 336 样本。历史 ESP32-S3 原始循环均值 **82.1763 ms**。结果不能代替准确性或动作到屏幕延迟；条件见 [UNO Q 报告兼教程](docs/uno_q_getting_started.md)。

## 数据与模型契约

DB2 原始 EMG 为 12 通道，CLI 可显式选择通道和特征；不能与 ADS1298 物理八通道混为一谈。硬件候选 NCP768 使用八通道交错 RMS/ZC，共 **16 维输入**，完整状态 768（455/303/10 三层），输出 J10。权重 INT8 不表示全整数运算；时间步保持 `ts=1`。

J10 一基 glove 通道顺序 `2,3,5,6,8,9,12,13,16,17`，零基 `1,2,4,5,7,8,11,12,15,16`。逆归一化后仍是原数据手套坐标，不宣称解剖角度。两动作记录绑定的 NCP512 仅证明基础检查点来源，不能写成已部署模型。

研究 CLI 默认 Dense、RMS、12 通道和 μ-law `mu=255`；底层 API 保留多特征及其他研究配置。恢复与导出必须匹配模型族、通道、特征顺序、归一化和目标契约。200 ms 窗长、50 ms 步进、8 窗覆盖 550 ms 历史，不等同于计算延迟。

统计仅由监督源数据拟合。支持/查询按目标重复隔离；ATL 中未评分目标标签物理替换为 NaN，查询 EMG 仅可作为无标签因果上下文。ATL 交替 GAN 优化，无 GRL；固定轮数返回末轮，查询标签不参与训练或选模。见 [架构与协议](docs/architecture.md)。

## 入口导航

| 入口 | 用途 |
|---|---|
| [训练 CLI](src/deep%20learning/run_db2_paper_cfc_finetune.py) / [训练库](src/deep%20learning/train.py) | Dense / AutoNCP、状态训练、ATL 与评估 |
| [数据流](src/dataflow/) / [硬件工具](src/hardwareOperation/) | 特征、映射、归一化、导出及 C 数值核验 |
| [八通道固件](firmware/diagnostics/ads1298_bringup/README.md) | 独立 ESP-IDF ADS1298 工程 |
| [AutoNCP 固件](firmware/ads1298_autoncp/README.md) | 独立 ESP-IDF 工程、启动自检、性能复现 |
| [ADC GPIO 探针](firmware/diagnostics/adc_gpio_probe/README.md) | 芯片引脚能力诊断 |
| [采集界面](ui/README.md) / [设计约定](DESIGN.md) | 启动、保存、流程和阻塞 |
| [资源清单](RESOURCES.md) / [视频历史研究](docs/video_joint_angle_path.md) | 硬件依据和未采纳提案 |

`firmware/main/` 仅保留 portable Dense C 内核、特征和历史五输出 fixture；根 `firmware/` 已不再是可刷 ESP-IDF 应用。正式构建使用独立工程。Dense fixture 声明 `TARGET_NORMALIZATION_AVAILABLE=0`，仅供显式允许 fixture 的主机测试，不能作为 J10 部署产物。

## 本机启动与复现

仓库根目录使用已安装依赖的 Python 3.13：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py'
& '.\ui\start.ps1'
```

依赖为 NumPy、SciPy、Matplotlib、PyTorch、ncps。DB2 私有数据放在 `src/data/DB2/DB2_s{id}/S{id}_E{exercise}_A1.mat`。数据、检查点、原始采集及本机工具不随源码自动提供。浏览器检查及两动作启动见 [UI README](ui/README.md)。

重跑研究先查看参数、记录配置并使用新输出目录：

```powershell
python "src/deep learning/run_db2_paper_cfc_finetune.py" --help
python firmware/ads1298_autoncp/profile_autoncp.py --help
```

性能工具从 `profile.py` 迁移为 `profile_autoncp.py`，避免遮蔽标准库。`log/` 中旧名称及历史失败保留，未因收尾改写为已通过。
