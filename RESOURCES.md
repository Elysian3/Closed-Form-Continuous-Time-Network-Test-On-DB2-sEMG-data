# 项目资源与资料归属

2026-10-13 本阶段暂时完结；状态与验证边界见 [收尾记录](docs/project_closeout.md)。以下区分仓库资料、厂商依据和本机外部文件，避免把本机路径当作随源码交付的资源。

| 资源 | 用途 | 归属与边界 |
|---|---|---|
| [TI ADS1298 datasheet](https://www.ti.com/lit/ds/symlink/ads1298.pdf) | 芯片帧格式、DRDY、SPI 时序 | 原厂依据，实际模块外围另核对 |
| [八通道固件说明](firmware/diagnostics/ads1298_bringup/README.md) | 当前协议与默认接线 | 项目维护文档，接线需核对实物 |
| [数据帧参考](reference/ads1298-data-frame.html) | 帧结构阅读辅助 | 仓库内参考，不能替代原厂规格 |
| [模块布线参考](reference/ads1298-breadboard-wiring.html) | 模块端口与布线阅读辅助 | 仓库内参考，不能单独证明人体输入条件 |
| [UNO Q 教程](docs/uno_q_getting_started.md) | 部署与内核实测 | 包含本机历史证据路径；非端到端验收 |
| [手部资产来源](ui/assets/hand/README.md) | GLB、版本、SHA256 与 MIT 归属 | 本地分发资源，许可证原文保留 |
| [视频路径研究](docs/video_joint_angle_path.md) | 历史候选方案与来源 | 未采纳提案，不是当前采购建议 |
| `D:\BaiduNetdiskDownload\ADS1298模块资料包 20250410\3-电子硬件\ADS1298 原理图.pdf` | 本批模块端口、供电、外围连接 | 外部本地资料，不随仓库发布 |
| `D:\BaiduNetdiskDownload\ADS1298模块资料包 20250410\3-电子硬件\STM32与ADS1298模块接线图.png` | 接口角色示例 | 外部本地资料，不是 ESP32 引脚分配依据 |

DB2 数据、模型检查点、原始采集、工具链及 `log/` 实测产物属于本机实验材料，不能假定克隆仓库即可获得。收尾备份位置和恢复条件见收尾记录；第三方代码及资源的许可证未改写。
