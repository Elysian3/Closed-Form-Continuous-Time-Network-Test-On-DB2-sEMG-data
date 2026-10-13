# 暂时收尾清理计划（2026-10-13）

用户决定暂时结束本阶段开发。本次只清理确认无行为价值的内容、修复清理验证暴露的直接阻塞，并同步项目自己维护的文档。不训练新模型，不改变数据切分、信号处理、量化算法或采集协议。

## 清理前证据与恢复

- 清理前源码、测试和项目文档共 179 个文件已保存到 `log/project_closeout_20261013/before_cleanup.zip`；对应文件哈希见同目录 `before_cleanup_manifest.json`。这是当前工作区的备份，包含原有未提交修改。
- 首次完整回归：248 项，16 个错误、1 项跳过。其中 15 个错误指向 `firmware/ads1298_autoncp/profile.py` 遮蔽 Python 标准库 `profile`，导致 PyTorch 优化器导入 `cProfile` 失败；另一个错误是 `test_pretrain_continuation.py` 将共享测试夹具导入到第三方同名 `tests` 包。详见同目录 `baseline_tests.log`。不通过删除失败测试掩盖这些问题。
- 审计已覆盖项目维护的 Python/C/浏览器代码和测试；第三方代码、数据集、检查点、原始采集与历史实测记录保留。

## 实施顺序、影响范围与验证

| 步骤 | 目的与范围 | 验证 |
|---|---|---|
| 1. 修复模块命名冲突 | 将 `firmware/ads1298_autoncp/profile.py` 改为 `profile_autoncp.py`，同步 `build.py`、CMake 提示、`tests/test_autoncp_profile.py` 和现行文档中的调用方式；增加一次从该目录导入标准库 `cProfile` 的回归检查；将 `test_pretrain_continuation.py` 的夹具导入改为测试发现目录中的实际模块 | 完整 unittest 基线重新运行，原 16 个错误应消失；保留原始失败日志 |
| 2. 退役旧应用脚手架 | 删除 `firmware/main/main.c`、`firmware/main/CMakeLists.txt`、`firmware/main/idf_component.yml`、`firmware/CMakeLists.txt`、`firmware/sdkconfig.defaults`。旧入口仅采样一轮且输出任务空转；保留 `firmware/main` 的 Dense CfC C 内核、特征处理和测试头文件，保留真实 ADS1298 与 AutoNCP 独立固件入口 | Dense C 编译与 golden parity、AutoNCP/ADS1298 host harness 测试；检查所有现行文档不再把根 firmware 当可刷应用 |
| 3. 删除冗余测试与死 helper | 删除无断言的 `ui/tests/inspect.cjs`；移除历史路径/旧属性不存在、源码缩进/文案、重复函数对象同一性等断言；删除依赖退役 main.c 的两个源码测试；精简 header 契约的来源检查；删除无调用的 `train.summarize_best_epoch` | 保留数值归一化、完整状态、ATL/query 隔离、导出拒绝、采集恢复及硬件边界测试；受影响模块和完整套件通过 |
| 4. 清理空配置与过期生成物 | 删除空 `pyproject.toml`、过期 `TODO` 和无人引用的 7 月 `graphify-out` 生成快照；将误写为远程复制命令的 `requirements.txt` 恢复为实际已使用的五个 Python 库，不增加新运行时依赖 | requirements 可解析、导入检查；生成物删除前检查目标在仓库内且已进入恢复备份 |
| 5. 更新全部项目文档 | 重写根 README 与架构导航；将 DESIGN/视频方案标为暂停或历史方案；统一 UI、固件 README、历史报告入口、资源清单和子目录 AGENTS 的当前状态；保留实验数字及上游许可证 | Markdown 链接/代码围栏检查、入口命令检查；统一说明 8 通道采集与 16 维特征、J10、UNO Q 11.279 ms 仅内核结果、真实 ATL/端到端仍未验收 |
| 6. 收尾验证 | 保存清理结果、精确删除清单和验证日志 | 完整 unittest、现有浏览器行为检查、Python 静态检查与语法检查；明确 CUDA/硬件验证缺口 |

## 保留与删除判断

- 保留可独立执行的实验/分析 CLI、公共训练 API、数据映射 API，以及所有具有不同边界或行为覆盖的测试。
- 显式阻止未经证实的源单位换算、真实 ATL/live 的逻辑属于必要失败保护，保留。
- 旧 ADC/输出空转脚手架不再作为可运行功能保留；不在收尾时补写硬件实现。
- 不修改 `log/` 内历史实验脚本与原始数据；其中旧 `profile.py` 路径属于历史命令，复现时使用新入口名，原文件可从收尾备份恢复。
- 资源许可证、厂商原文、第三方 `graphify` 工具源码保留，不改写为项目完成声明。

独立只读审计与实施分离。最终结论和实际检查结果写入 `docs/project_closeout.md`，本文件保留为实施前计划。
