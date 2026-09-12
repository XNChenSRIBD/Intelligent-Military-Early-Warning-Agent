# 第四轮交付：哈尔科夫与霍尔木兹自动历史回放

## 代码与实际运行状态

参考基线为第三轮 `37fcf7aab6a776542b0ce35a41a6ba64e16ebe83`。第四轮在现有工作区接续，具体版本为包含本文、`app/replay.py` 和双案例清单的仓库提交。

本轮实现独立历史模式、真实 GNSS 处理、专业工具、Qwen 自动分析与异常更新、持久进度和现有看板展示。两个案例使用同一条后台处理链，默认按哈尔科夫、霍尔木兹的清单顺序自动处理。

**两例均尚未执行真实业务回放，不能记为验收通过。** 当前授权仍不包含 `.16` 部署、应用重启、真实采集、模型调用和测试。本轮没有执行这些操作，也没有生成运行截图或模型调用证明。现有 `.16` 在线页面未因本轮代码改变。

## 已准备输入及明确缺项

| 案例 | GNSS 观测清单 | 事前参考清单 | 原文件实际状态 | 已准备辅助归档 |
|---|---|---|---|---|
| 哈尔科夫 | 2024-05-08 至 05-10，11 站共 32 个 daily 文件；文件名标称 30 秒 | 2024-04-10 至 04-12 共 30 个；2023-05-08 至 05-10 共 28 个 | 共 90 个条目均 `not_local`；来自旧文件状态索引，未读取本轮 RINEX | 3 天 GFZ 原始历史日记录；没有可用新闻归档 |
| 霍尔木兹 | 2026-02-27 至 03-05，3 站每天 10:00、10:15 两片，共 42 个 15 分钟文件；文件名标称 1 秒 | 02-11、02-26 同三站同期切片，共 12 个 | 共 54 个条目均 `not_local`；服务器项目目录中的当前存在性未核实 | PortWatch 20 日；8 条原标题；7 日 GPSJam；7 日 GFZ |

完整资源、相对路径、来源和覆盖说明分别位于：

- `cases/replay/kharkiv/manifest.json`
- `cases/replay/hormuz/manifest.json`

哈尔科夫清单的实际站号为 BOGI00POL、BUCU00ROU、GANP00SVK、GLSV00UKR、JOZE00POL、MDVJ00RUS、MIKL00UKR、POLV00UKR、RIGA00LVA、SULP00UKR、ZECK00RUS。这些站保持各自身份，不被改名为哈尔科夫本地站。霍尔木兹使用 BSHM00ISR、KITG00UZB、NICO00CYP。两例站点坐标来自已存 `IGSNetwork.csv`；运行时优先采用原 RINEX 头中的实际坐标、时制和采样间隔。

144 个 GNSS 条目用于准确定位待处理的原文件，并不表示 144 个文件已在本次运行环境备齐。旧报告、旧特征 CSV 和旧模型结论不作为重新计算结果。霍尔木兹每站每天只选取名义 10:00–10:30 窗口，不宣称整日高频覆盖；名义时间须以实际 RINEX 头的时制转换为准。

仓库已保存 45 个辅助 JSON：20 个 PortWatch 日记录、8 个新闻标题、7 个 GPSJam 日切片、10 个 GFZ 日切片。PortWatch 为真实归档 attributes 原值，覆盖 2026-02-14 至 03-05，其中 13 日在回放起点前、7 日在回放窗口内。规则所需 01-30 至 02-26 的前置窗口仍缺 01-30 至 02-13 共 15 日，现有输入不能满足至少 21 个有效日的参考条件。历史数值于事后取得，可能经过来源修订，不被当作当年冻结版本。

新闻保留 8 条原标题，无正文；其中 5 条在回放起点前，3 条在 02-27。GPSJam 使用已存 `hormuz_core` 日计数，GFZ 使用原始历史日字段；旧综合分数与旧结论未被转成输入。

## 准确启动方式

需要 Python 3.10+，使用工作台自己的 Python 环境。以下为**待授权执行的命令**，不是已经完成的部署记录。先将获准复用的原始文件放到清单相对路径对应的数据根，保留线上 `.env`、数据库及既有模型环境。

首次准备工作台环境：

```text
python -m pip install -e .
python -m pip install -r requirements-gnss.txt
```

Windows PowerShell，从仓库根目录运行：

```powershell
.\scripts\start_case_replay.ps1 -AssetRoot '..\..\public_data_chasing_lightning' -DataDir '.\runtime-round4' -ModelBaseUrl 'http://127.0.0.1:8000/v1' -Port 8081
```

`AssetRoot` 上述相对位置适用于原工作区布局；其他机器须替换为其真实原始数据根。`ModelBaseUrl` 使用部署环境内可达的既有 vLLM 地址，示例表示模型与工作台同机。8081 仅是与线上 8080 分开的端口示例，使用前仍须有对应运行授权。

Linux 使用同一应用入口，等价环境配置如下；命令中的资产根应指向实际准备的目录：

```bash
PIPELINE_MODE=case_replay \
REPLAY_MANIFESTS='cases/replay/kharkiv/manifest.json;cases/replay/hormuz/manifest.json' \
REPLAY_ASSET_ROOT=../../public_data_chasing_lightning \
REPLAY_AUTOSTART=true \
PIPELINE_ENABLED=true \
PIPELINE_ANALYSIS_VERSION=gnss_replay_v1 \
DATA_DIR=./runtime-round4 \
MODEL_BASE_URL=http://127.0.0.1:8000/v1 \
APP_HOST=127.0.0.1 APP_PORT=8081 \
python -m app
```

启动后查看 [本机回放页面](http://127.0.0.1:8081/)。浏览器不参与推进条件。Web 固定单 worker，两个案例处理结束后仍保持可查看；若运行环境需要内网访问，使用已获准的绑定地址。

| 配置 | 含义 |
|---|---|
| `PIPELINE_MODE` | 默认为 `online`；`case_replay` 选择本轮历史流程 |
| `REPLAY_MANIFESTS` | 分号分隔的案例清单，默认先哈尔科夫后霍尔木兹 |
| `REPLAY_ASSET_ROOT` | GNSS 等外部原文件根目录；`root=repo` 的已提交辅助切片从仓库读取 |
| `DATA_DIR` | 独立回放数据库与计算缓存，不能使用在线实例的数据目录 |
| `REPLAY_AUTOSTART` | 默认 `true`，首次建立实例后自动处理；已保存暂停状态跨重启保留 |
| `REPLAY_MAX_ATTEMPTS` | 默认 3；持续分析故障达到次数后保留失败位置，转入下一例 |
| `PIPELINE_ENABLED` | 既有总开关，需为 `true` 才自动工作 |
| `PIPELINE_ANALYSIS_VERSION` | 工作键中的分析版本；启动示例使用 `gnss_replay_v1` |
| `MODEL_BASE_URL` / `MODEL_NAME` | 既有模型服务地址与模型名，默认名仍为 `qwen3-4b` |

运行命令不启动或重启 vLLM，不变更 CUDA、GPU 或模型。`requirements-gnss.txt` 仅增加工作台侧 GeoRinex、Hatanaka 与 numpy。

## 实际处理路径

`app/replay.py` 复用第三轮 `Pipeline`、SQLite `records` 和持久工作队列。它读取清单，先导入回放起点前的参考，按日释放当前资料，调用真实计算函数，保存材料并入队后更新进度。当前批次成功分析或得到业务性的 `no_anomaly` / `insufficient_evidence` 后才正常推进下一批。持续技术故障、缺失原文件与实际业务结论分别保存，不因“任务结束”而自动判为通过。

`app/gnss.py` 复用旧 `process_rinex_day_metrics.py` 的 GeoRinex 读取、有限值筛选与分位统计路径，增加小型回放适配。`.gz` 先解压，Compact RINEX 由 Hatanaka 真正解码，再读取文件声明的 S* 观测。程序按实际采样间隔生成 5 分钟窗口统计，保留有效数量、比例、缺测、CNR p10、来源与处理版本 `rinex-cnr-window-v1`；30 秒日文件不插值成 1 Hz。

只有具有来源依据的 dB-Hz 单位才标为 CNR。离散质量值或未注明单位的 S* 数值单独记录；GeoRinex 的 SSI/LLI 指示标记不混入 CNR。参考只使用回放起点前、同案例、同站、同系统、精确信号、采样间隔、单位及相同 UTC 时段的已计算窗口。没有匹配参考时差值为 `null`。参考为匹配窗口 p10 的中位数，实际支持天数随结果记录，不生成新的综合风险分数或同步检测算法。

时间转换保留原时制。两例所在已确认历史区间内，GPS/Galileo/QZSS 到 UTC 使用 18 秒，BeiDou 使用 4 秒，RINEX UTC/GLONASS 观测时间不偏移；其他时段或未支持时制须有已注册的可信转换依据。依据为 [RINEX 3.05 格式](https://files.igs.org/pub/data/format/rinex305.pdf) 和 [IERS Bulletin C 71](https://datacenter.iers.org/versionMetadata.php?filename=mt%2Fbulletinc-071.txt)。该转换服务于分窗统计，不声称亚秒同步。

`assess_update()` 继续使用真实 Qwen3-4B 接口和已有有限工具循环。GNSS 输入携带 `case_id`、不可变 `as_of`、精简统计、材料引用和同案例可见异常。默认最多 2 次工具调用、4 次模型请求，格式修复计入；观测数组不逐点发送给模型。

专业工具直接调用业务函数：

- `station_history`：已释放的同站、同信号历史统计和可比较参考。省略信号时按该站已注册信号字母序选择，并返回选择说明。
- `multistation_check`：同案例已释放的同期多站统计、时间重合与覆盖限制。未指定信号时按站点覆盖数量及信号码选择，不按异常极值选择。
- `read_material`、`search_news`：在回放中仅补读本案例、截至原 `as_of` 的归档；不查询今天的在线新闻。

工具结果成为可引用材料，并保存实际请求与返回。Qwen 可以据此继续分析或直接结束；不强迫每批调用专业工具。异常更新仅接受本批提供的同案例相关记录。PortWatch 保留 `pw_lowflow_v1` 的数值、触发和恢复条件，GNSS 或新闻解释不能改写规则。

## 时间、保存与前端

资料分别记录观测窗口、可信的 `available_at`、演示用 `replay_release_at` 与本次系统导入、分析、发布时间。来源历史发布时间未知时保留空值，按窗口结束构造的到达时间明确属于模拟假设。日文件在窗口结束后释放；重试保持原 `as_of`，不会读取下一批或另一个案例。旧最终报告保留为独立参考，不进入本轮模型输入。案例日期是回溯选定，顺序回放不证明实时可用性或预警提前量。

持久资料位于独立 `DATA_DIR`：

- SQLite 保存回放实例、案例、资源、工作、材料、工具和异常版本。
- `gnss_cache/<case_id>/<registered_resource_id>.rinex-cnr-window-v1.json` 保存每个已成功计算原文件的完整分窗结果；身份使用处理版本、资源、文件名、大小、修改时间和解释元数据。
- 同目录的 `.failure.json` 记录资源处理失败，成功缓存保留。原始 RINEX 留在资产根目录，不提交大文件到仓库。

同一数据目录正常重启会续接未完成工作，复用已完成文件缓存，不重复发布已完成批次。重新从头演示使用新的回放数据目录；不清空线上状态。资源缺失、解析失败及持续模型故障的位置与原因留在案例详情。

前端保持三栏：顶部显示历史模式、当前案例、截止时间及队列；左侧展示两例与实际覆盖；中部显示异常动态、GNSS 当前与参考曲线，并保留霍尔木兹 PortWatch 图；右侧展示本次分析、真实工具结果、材料版本和注册原文件依据。没有可用 CNR 或参考时不绘制虚构曲线。自动切换下一例后，上一例仍可查看。

## 两例运行依据与剩余工作

| 项目 | 哈尔科夫 | 霍尔木兹 |
|---|---|---|
| 代码链路 | 已实现 | 已实现 |
| 必需原始 GNSS | 90 个旧索引条目，待在授权环境备齐 | 54 个旧索引条目，待在授权环境备齐 |
| 本版真实解析 | 待执行 | 待执行 |
| Qwen 请求、响应与实际模型标识 | 待执行，无本轮业务记录 | 待执行，无本轮业务记录 |
| 动态专业工具调用 | 未在业务轨迹中证明 | 未在业务轨迹中证明 |
| 实例、工作及异常 ID、起止时间 | 尚未产生 | 尚未产生 |
| 前端运行截图 | 未取得 | 未取得 |
| 业务验收结论 | 未完成 | 未完成 |

影响两例完成的剩余事项只有：在获准环境定位或准备清单对应原 RINEX；补齐霍尔木兹 15 日 PortWatch 前置数据；准备工作台 GNSS 可选依赖；获得部署、启动与真实模型回放授权后执行两例正常业务流程。实际运行后再记录输入、处理、失败、工具调用和结果依据；当前不填造通过数量、结论或截图。
