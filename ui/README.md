# Antikythera 本地采集界面

**2026-10-13：本阶段暂时完结，开发暂停。** 真实采集、保存、完整与两动作计划保留；真实 EMG 到预测界面未验收，训练与 live 门保持阻塞。清理及最终验证见 [项目收尾记录](../docs/project_closeout.md)。

在 Windows PowerShell 启动：

```powershell
& 'D:\Project Antikythera\ui\start.ps1'
```

浏览器访问 <http://127.0.0.1:8765>，使用支持 Web Serial 的桌面 Chrome 或 Edge。保持服务终端和采集页面开启；Ctrl+C 停止服务。端口被占用时使用 `-Port 8766`。启动器使用本机已有 Python 3.13，不自动安装依赖。服务仅监听本机；原始数据不通过静态网站公开。

2026-10-04 的两动作第一轮使用独立端口，避免接续旧的完整采集会话：

```powershell
& 'D:\Project Antikythera\ui\start.ps1' -Port 8766 -CaptureProfile two-actions
```

访问 <http://127.0.0.1:8766>。动作顺序为 DB2 公共动作 6「握拳」、5「张开五指」。每组先握拳 6 次，再张开五指 6 次，共 3 组、36 次；每次仍为准备 2 秒、跟随 5 秒、放松 2 秒，提示时间合计 5 分 24 秒，暂停和重做另计。这里的张手明确为伸展并张开五指。自然放松在准备和休息阶段记录，不能用主动张开五指代替静息。

两动作模式仅改变采集计划，复用固定公共模板，不重新生成监督曲线。会话记录 `capture_plan`，每动作使用唯一重复号 1–18，并额外保存 `group` 和 `repetition_in_group`。暂停、断连或后台切换仍要求重做当前一次。服务只恢复相同计划的会话；无计划字段的历史会话仍按原来的完整 40×6 处理，两个模式不会互相接续或改写旧记录。

新两动作会话在创建前核验 NCP512 源预训练 checkpoint 的固定 SHA256，并保存 `model_provenance`（16 输入、512 状态、10 输出）。这是后续特调的基础模型来源，不代表设备此刻已运行 NCP512 实时推理。实际模型文件为 `log/db2_paper_cfc_finetune/new_stateful_test_11_lr1e-4_ncpwiring/checkpoints/S1_autoncp_cfc_pretrain.pt`，SHA256 为 `8c214094387222409aa7a038bfcd313e2c6156904a39c038ccb141bf1cc27769`。

本轮范围为原始数据采集准备。源数据单位证据和新会话到 ATL 的训练对接仍待完成，`/api/train`、`/api/live` 继续显示实际阻塞。

## 采集

1. 设置语言和左右手，连接已运行 ADS1298 采集固件的设备。在浏览器端口选择器中选择设备；同一串口不能同时由厂商上位机占用。
2. 界面查询实际增益，确认 2000 Hz 后接收八通道信号。波形来自设备声明的输入微伏（uV）。收到数据和服务端确认保存是两个不同状态。
3. 跟随当前计划的公共模板。默认完整模式为全部 40 个动作、每动作 6 次，共 240 次、36 分钟；两动作模式为上述 36 次、5 分 24 秒。每次均为 2 秒准备、5 秒动作提示、2 秒休息；暂停和重做另计。这是固定公共模板时间线，非原始 S1 逐次精确时长。
4. 暂停、切到后台或已知通信错误会使正在进行的这一次失效；恢复时从这一次开头重做。先前完整次数和审计记录保留。没有演示快进。

波形按通道居中并独立自动缩放，旁边标注当前 ±uV 显示范围。这只改变绘图，不改变保存的原始值，也不表示各通道具有相同实际幅度。波形有起伏或收到数据都不能单独证明人体肌电质量合格。

2026-10-04 的本轮操作约定：遇到连接或采集中断，短按 ESP32 的 **RST/RESET** 后重新连接，重做当前未完成的一次，先前完成记录保留。用户已观察到该操作恢复采集并停止进一步硬件排查；具体异常状态的根因尚未确定，不能由此认定跨完整断电保留的 Flash/NVS 状态。

连接时允许启动文本在就绪前出现，但仍须收到有效参数回复。`No ADS1298 parameter reply (query 0x09).` 与 `(set 0x10).` 区分失败阶段；校验和时间限制没有放松。

虚拟手在采集阶段展示的是**动作引导**。公共模板来自 33 名源受试者，不使用本次参与者的真实手部角度。模型仍为本地连续蒙皮 GLB；可旋转视角，保留双语和左右手偏好。缺少 WebGL 或模型加载失败时有明确提示。

## 当前完成边界

已提供真实串口接入、原始数据保存、40×6 动作引导和十关节渲染接口。生产页面不再提供合成肌电或模拟训练进度。

已实现的候选分工为 **PC 执行 ATL、ESP32 执行模型启动自检**；后续另完成 UNO Q Linux 纯内核验证，见 [UNO Q 教程](../docs/uno_q_getting_started.md)。两条路径都未接通真实连续预测。[AutoNCP 候选固件](../firmware/ads1298_autoncp/README.md) 已上板运行启动自检。2026-09-23 的 PSRAM 只读段对照中，相同 INT8 模型的合成输入最大单步从 Flash 约 127.52 ms 降至 PSRAM 82.174 ms；两者均未测量持续采集或完整链路。当时测试后恢复 Flash 固件，ADS1298 配置读回连续三次为零并重启，设备曾停在 ROM 下载模式。2026-10-04 后续 GPIO 顺序 A/B/A 加原镜像复测共 20 次读回通过，原镜像完整恢复校验匹配；仍未确认此前故障根因。上述正常读回是当时记录，后续连接和采集仍出现间歇异常。用户短按 RST/RESET 后恢复实际采集；会话 `20261004T090533Z-2e67ba37` 的最后保存快照为暂停、293,851 行及 13/36 个软件流程合格次数，尚未完成整轮，也未验收人体信号质量。此前“等待浏览器连接”及 ROM 停车状态已被本轮采集记录更新。板上原启动自检模型仍为 768 状态；NCP512 仅作为本轮记录绑定的后续特调基础模型。现有界面支持原始采集协议，但持续电极推理及预测结果到本页面的通道尚未接通；INT8 真实效果和完整链路延迟也未验证，启动自检不代表实时控制已经可用。

**真实 ATL 和模型实时控制尚未启用。** 当前缺少可追溯的 NinaPro DB2 `.mat` 中 `emg` 存储单位依据，不能把设备 uV 猜测换算后送入源模型。`/api/train` 和 `/api/live` 会返回明确阻塞；客户端的 `scale`、`verified` 等字段不能解除它。滤波/特征模块和内部合成 ATL 测试不代表真实参与者训练已经完成。

现有固件数据包没有 ADC 序号或采样时间戳。保存的是主机接收索引和浏览器时钟，可审计已知丢包/错误，但不能证明设备内部零丢样。全部次数完成后的状态为 `capture_finished_unverified`，不等于模型就绪或最终验收通过。

用户验收标准仍是：手指运动趋势一致、实际手部动作开始到屏幕对应手指开始运动小于 200 ms，不要求角度完全一致，腕/臂不纳入。必须在实际设备和真实推理接通后，用同步物理观测验证。软件测试、公共模板动画和单步推理耗时均不能代替这一验证；未测量的 R²/时延不能显示为通过。

## 保存与技术接口

每次会话使用新的 `log/live_sessions/<时间戳>-<随机值>/` 目录：

- `manifest.json`：schema 1、确认的采样率/增益、模板身份、段和动作次数、完成及阻塞状态；`wire_units: "uV"` 表示设备传输单位，`stored_units: "V"` 表示文件单位。
- `emg.f32`：八列、小端 float32，写盘前将设备 uV 乘以 `1e-6` 转为 V，不额外滤波或归一化。文件行偏移与接收索引分别保存。
- `events.jsonl`：批次、动作提示、暂停、错误、段边界等事件，包含浏览器时钟信息。

旧会话没有 `stored_units` 字段时保留 uV 并按原单位续写，恢复时补记该单位；已有文件不自动换算，避免同一文件混用单位。

动作间等待保存确认、暂停或关闭时，已接收但未存入文件的区间会记录为 `received_gap`，与文件行号分开，不算作有效动作的一部分。服务重启时，未完成的动作失效；若崩溃前原始数据已写入、批次日志尚未提交，这些行会标记为 `recovery_unacknowledged_raw`，保留原始数据且不推定其接收索引。

模板文件固定为 `source33_template_noquery_atl60_lr3e5_20260914_095931/templates.npz` 和同目录 `templates.json`，服务核验哈希、40×181×10 曲线及目标顺序。显示坐标来自模板的固定范围，不是标定的解剖角度。J10 顺序为 CH2/3/5/6/8/9/12/13/16/17；未建模的远端关节仅作外观派生，腕和拇指对掌不冒充预测。

`acquisition.js` 负责协议和 Web Serial；`app.js` 负责流程和有序保存；`server.py` 负责本地 API、原始记录和训练门；`hand.js` 负责 WebGL。`src/hardwareOperation/live_emg.py` 提供因果 notch/bandpass 和 200 ms / 50 ms 的 RMS、ZC 特征，共用现有计算公式，尚未接入真实模型归一化。无新增运行时库。

## 可复现检查

```powershell
& 'C:\Users\14148\AppData\Local\Programs\Python\Python313\python.exe' -m unittest discover -s tests -p 'test_live_*.py'
& 'C:\Users\14148\AppData\Local\Programs\Python\Python313\python.exe' -m unittest discover -s tests -p 'test_template_atl.py'
& 'C:\Users\14148\AppData\Local\Programs\Python\Python313\python.exe' ui/tests/run_browser.py
```

两动作浏览器流程复核：

```powershell
$env:CAPTURE_PROFILE = 'two-actions'
& 'C:\Users\14148\AppData\Local\Programs\Python\Python313\python.exe' ui/tests/run_browser.py flow.cjs
Remove-Item Env:CAPTURE_PROFILE
& 'C:\Users\14148\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' ui/tests/selection.cjs
```

在项目根目录运行上述命令。浏览器测试启动临时本地服务，把合成记录写入测试临时目录并自动清理，不改动真实采集目录。使用本机已有 Node、Playwright 和 Chromium，可用 `NODE_EXE`、`PLAYWRIGHT_MODULE`、`CHROME_PATH` 指定已有工具位置。假设备仅在测试中注入，生产接口没有测试绕过。截图保存在忽略的 `ui/tests/artifacts/`。具体本次测试结果见 `.omx/plans/acquisition-atl-ui-verification.md`。

手部资源来源、版本和 MIT 许可见 `assets/hand/README.md`。

