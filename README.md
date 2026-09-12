# Intelligent-Military-Early-Warning-Agent

本仓库包含公开资料自动监测工作台和既有研究案例。工作台启动后按保存的配置采集公开新闻与民用航运日度数据，由新资料或证据变化触发独立 Qwen3-4B 分析，自动发布、更新异常记录。前端用于查看看板、调整监测配置和暂停；日常运行不需要提问、创建会话或点击分析。

## 启动工作台

需要 Python 3.10+。首次安装且没有 `.env` 时，在仓库根目录执行：

```bash
python -m pip install -e .
cp .env.example .env
python -m app
```

Windows PowerShell：

```powershell
python -m pip install -e .
Copy-Item .env.example .env
python -m app
```

浏览器打开 `http://127.0.0.1:8080`。已有环境保留原 `.env` 和 `DATA_DIR`，直接使用 `python -m app` 启动，不用示例覆盖部署配置。应用固定使用一个 worker；原 `scripts/start_demo.sh` 和 `scripts/start_demo.ps1` 入口仍可使用。在指定 Python 环境启动脚本时，Linux 设置 `PYTHON`，Windows 使用 `-Python` 参数。

`.env` 中 `MODEL_BASE_URL` 指向已有 OpenAI 兼容接口，默认 `http://127.0.0.1:8000/v1`，`MODEL_NAME=qwen3-4b`。需要鉴权时填写 `MODEL_API_KEY`。Web 应用不会下载模型或管理 GPU 进程。模型暂不可用时，采集继续推进，待分析工作自动退避重试；数值规则命中保留并显示 Qwen 待分析。

其他配置包括 `APP_HOST`、`APP_PORT`、`DATA_DIR`、请求超时、正文输入长度、`RSS_URL` 和 `PORTWATCH_ID`。数据库与本地材料默认保存在 `runtime/`；重启后会继续显示。内网部署时将 `APP_HOST` 设置为本机内网地址。应用静态资源均由自身提供，不需要 Node 或在线图表库。

## 自动监测与看板

- **自动启动**：`PIPELINE_ENABLED=true` 为默认值。首次启动建立霍尔木兹海峡及民用航运新闻配置，只将 `pipeline_owned` 来源交给自动流水线；旧试验主题和历史回放不会因此自动启用。来源检查与单个 Qwen 消费者独立运行，模型等待不会占住全部采集。
- **来源与周期**：新闻默认每 5 分钟检查，PortWatch 默认每 24 小时检查；这是检查周期，不是来源的更新速度。新闻首次回看 48 小时，后续保留 30 分钟重叠窗口。GDELT 按收录时间分窗获取，未覆盖窗口保存后续接；采集量不受每批 3 条的模型上限限制。RSS 只读取 `RSS_URL` 实际 feed，任一 `PIPELINE_RSS_TERMS` 关键词匹配即可，名称沿用 feed 标题。默认 UN News 不是完整航运新闻库，历史覆盖记为未知。
- **自动分析与发布**：新材料或修订自动入队；新闻最多 3 条一批，PortWatch 每轮业务输入变化整理成一份数值报告。Qwen 可自动补读本批材料或检索同范围新闻，默认最多 2 次工具调用、4 次模型请求，格式修复计入预算。无新线索和证据不足同样保存处理结果，重复获取不重复发布。
- **自动看板**：打开页面后约每 2 秒读取新状态，显示采集、分析、积压、来源日期及异常动态。选择异常查看数值规则、Qwen 结论、材料版本、原始链接与处理时间。来源或模型故障出现在运行状态区；它们不构成航运异常，也不会解除既有异常。
- **配置和暂停**：左栏可修改主题、RSS 关键词、允许来源与周期，并暂停或恢复流水线。暂停状态和已保存配置保存在 SQLite，跨重启保留；暂停不再派发新工作，正在处理的返回结果仍保存。关闭浏览器不改变启停状态。环境总开关为 `false` 时，前端恢复按钮不能绕过它。
- **续接**：已返回的新闻候选先保存正文读取进度，暂停或重启后续接。材料、待分析工作与采集进度一起提交；异常发布与分析完成标记一起提交。临时故障按退避及 `Retry-After` 重试，模型整体退避期间采集继续。输出格式持续失败的批次拆成单条并隔离延后，不依赖人工点击继续。

`PIPELINE_*` 完整配置见 [.env.example](.env.example)。主题、RSS 关键词、允许来源和周期用于首次生成保存配置，之后通过看板更新；分析预算、回看窗口等工程参数在启动时读取。第三轮文件、接口和实际交付状态见 [ROUND3_DELIVERY.md](ROUND3_DELIVERY.md)。

## 维护入口与历史案例

- **旧在线主题**：保留原主题、立即运行、周期更新和资料卡接口，供已有任务维护。它们与默认自动流水线分开管理；页面刷新不会启动一次新业务工作。
- **运行历史**：查看单次工作时读取材料、模型输入输出、工具结果与耗时。常规看板轮询只返回近期工作和异常摘要。
- **历史案例**：读取已提交的霍尔木兹案例报告、来源和 Sentinel 资产目录。图表使用实际保存的 5 条日观测，报告观察窗为 29 天。名义运力沿用源单位，不能解读为实载吨数。
- **新闻标题回放**：8 条已保存的民用航运与能源新闻按 GDELT 收录时间排序，每次只释放下一批至同一持久化和 Qwen 分析流程。原始标题之外没有新闻正文，`available_at` 未确认时留空；本次导入时间独立记录。回放摘要来自新模型调用，历史报告结论单独展示。

GDELT 与 RSS 不会互相替代；来源名称始终对应实际选择。工作台只做公开资料整理与历史展示，不生成军用目标轨迹或军事行动预测。

第一轮实现与部署记录见 [DEMO_DELIVERY.md](DEMO_DELIVERY.md)，不作为后续轮次的运行结果。

## PortWatch 日度指标与持续偏低提醒

自动流水线按 PortWatch 周期获取最近 90 个自然日内的全部可用记录，按源日期排序；该窗口不受新闻材料上限影响。看板保留日度指标、参考值、提醒与证据详情。旧 PortWatch 主题的手动更新仅作为维护入口，保留原周期与启停状态。

默认规则 `pw_lowflow_v1`：取候选首日之前 28 个自然日的有效 `n_total` 中位数作为参考，至少需要 21 个有效日且中位数大于零。连续两个自然日严格低于该参考的 50% 时形成一条持续偏低提醒。参考值从候选首日冻结；连续两个自然日达到冻结参考的 80% 后解除。缺日或无效主计数打断连续计数，已有提醒保留。50% 到 80% 之间表示尚未达到恢复条件。

这些是未校准的工程默认参数，不是 IMF 官方阈值。未命中时页面显示“未命中当前规则”。`n_total` 是 PortWatch 的 AIS 派生可见通行计数，辅助船型字段及名义运力可在日明细查看。数据截至日期与本次获取时间分别显示，缺日断线。

首次同步只保留初始化结束时仍活跃的提醒，标记为“初始化发现”；不会把窗口内已结束的历史片段批量发布为新提醒。后续源数值修订会更正相关提醒，原触发依据失效时标记“因数据修订撤销”，保留原证据与旧材料版本。

数值采集、参考统计、触发和解除由程序完成。第三轮自动将新数值或变化证据交给 Qwen 说明，无需点击“生成解释”；相同业务输入不重复分析，证据变化后旧解释标为旧版本。模型故障时显示“程序规则已触发，Qwen 待分析”并自动重试。新闻线索独立发布，不能改写这条数值规则，也不自动作为变化原因。旧主题的按需解释接口仍用于维护。

参数集中在 `.env.example` 的 `PW_*` 项和 `app/config.py`，修改规则参数时应使用新的 `PW_RULE_ID`。更新应用后首次正常启动会追加 `portwatch_daily` 表，保留既有数据库和材料。继续使用原来的 `.env`、`DATA_DIR` 和启动入口，无需安装新依赖。

第二轮代码、部署与业务运行状态见 [ROUND2_DELIVERY.md](ROUND2_DELIVERY.md)。

## 双案例历史自动回放

第四轮增加 `PIPELINE_MODE=case_replay`，沿用同一应用、Qwen 消费队列和异常看板，依次处理哈尔科夫、霍尔木兹。启动后自动装入事前参考、按日释放观测、真实解析 RINEX、分析并更新异常；无需提问或逐批点击。默认仍是第三轮 `online` 模式。

输入配置为 [哈尔科夫清单](cases/replay/kharkiv/manifest.json) 和 [霍尔木兹清单](cases/replay/hormuz/manifest.json)。GNSS 原文件通过 `REPLAY_ASSET_ROOT` 加清单相对路径定位；仓库中的 45 份辅助历史 JSON 使用 `root=repo`。当前 144 个 GNSS 条目只是旧索引定位，本地没有对应原文件，服务器当前存在性尚未核实。霍尔木兹 PortWatch 仍缺 2026-01-30 至 02-13 的前置日记录，不能将已有 13 日称为完整参考。

在获准运行的工作台环境准备依赖和原文件后，从仓库根目录启动。以下 8081 是独立端口示例，尚不表示该端口或部署已经获准；模型地址应为该环境已运行的 vLLM 接口：

```powershell
python -m pip install -e .
python -m pip install -r requirements-gnss.txt
.\scripts\start_case_replay.ps1 -AssetRoot '..\..\public_data_chasing_lightning' -DataDir '.\runtime-round4' -ModelBaseUrl 'http://127.0.0.1:8000/v1' -Port 8081
```

该资产路径对应原工作区的相对位置；其他机器设置为其真实数据根。启动脚本保留 `.env`，通过进程环境选定两例、自动启动和独立数据目录。GNSS 可选依赖装在工作台 Python 环境，不修改模型服务环境。完整 Linux 启动方式、配置、缓存与两例状态见 [ROUND4_DELIVERY.md](ROUND4_DELIVERY.md)。

看板新增案例、回放截止时间、处理进度、GNSS 分窗曲线及专业工具依据，保留 PortWatch 图和旧历史参考。GNSS 统计来自本版程序；同站历史和多站比较只读取本例已释放结果。真实 dB-Hz 值与未注明单位或离散质量值分开表示，缺测和参考不足不画成正常曲线。回放结束后 Web 保持可查看，正常重启沿用同一 `DATA_DIR` 续接。

第四轮目前是代码与部分输入交付，尚未部署、执行真实回放或取得模型业务轨迹；当前 .16 页面不会因此变化。

## 既有研究方向

- `mini-GDELT` / 新闻主题监测作为第一 monitor，用于识别 topic 升温、主体、事件类型与 heat。
- 当 topic heat 触发 hard gate 后，接入 NASA CDDIS / IGS 地面站 RINEX 数据。
- 信号处理前端计算 CNR/C/N0 差分、跨站同步、C/L/D 一致性与历史同窗对照。
- LLM Core 只读取前端报告，执行流程编排、反证检查和证据卡生成，不直接参与数值计算。

## Repository Layout

```text
app/                       # FastAPI、后台运行器、SQLite、采集与模型客户端
  static/                  # 同源中文工作台
scripts/start_demo.sh      # Linux 启动
scripts/start_demo.ps1     # Windows 启动
pyproject.toml             # Web 应用依赖
.env.example               # 部署配置示例
cases/
  iran-hormuz-2026-02-28/
    README.md
    DATA_SOURCES.md
    data/
    results/
skills/
  igs-cddis-api-download/
    SKILL.md
    scripts/
      igs_cddis_api.py
PROJECT_PROGRESS.md
README.md
```

## Case Studies

- [Iran / Strait of Hormuz, 2026-02-28](cases/iran-hormuz-2026-02-28/README.md)：整理 IMF PortWatch、WTO–AXSMarine、Sentinel-1/2 与 GFW 可用性，包含下载入口、货运历史类比、预警时间线和 EO 探索结果；仓库不保存原始大数据或凭据。

## IGS / CDDIS Download Skill

`skills/igs-cddis-api-download` 提供 NASA CDDIS / IGS 地面站数据获取与下载能力：

- `highrate`: 1 Hz、15 分钟 RINEX/CRINEX `.crx.gz`
- `daily`: 30 s、日文件 RINEX/CRINEX `.crx.gz`
- 支持 Earthdata Login / CDDIS 认证
- 支持日期、DOY、小时窗口、站点列表、并发下载
- 自动生成 `index_inventory.tsv` 与 `manifest.tsv`
- 下载后执行 gzip 校验

凭证不会写入仓库。运行时使用 `.netrc` 或环境变量：

```powershell
$env:EARTHDATA_USERNAME="YOUR_USERNAME"
$env:EARTHDATA_PASSWORD="YOUR_PASSWORD"

python skills\igs-cddis-api-download\scripts\igs_cddis_api.py configure-netrc
```

示例 high-rate 下载：

```powershell
python skills\igs-cddis-api-download\scripts\igs_cddis_api.py download `
  --product highrate `
  --dates 2026-02-27,2026-02-28 `
  --hours 03-12 `
  --stations BSHM00ISR,NICO00CYP,DJIG00DJI `
  --base public_data_chasing_lightning `
  --out-name hormuz_highrate `
  --jobs 8
```

更多说明见 [skills/igs-cddis-api-download/SKILL.md](skills/igs-cddis-api-download/SKILL.md)。

## Progress Notes

第五轮服务器数据入口、下载/计算队列、原实例续接及缓存页面见 [ROUND5_DELIVERY.md](ROUND5_DELIVERY.md)。服务器启动入口为 `bash scripts/start_server_data.sh online` / `replay`；先准备独立工作台环境并按部署授权更新原应用。在线实例默认接续已有 `runtime`，原件与资源台账由 `.16` 保存，浏览器关闭不决定后台推进。下载、模型、部署及清理的实际运行状态以该交付报告为准。

项目进度、已验证数据源、典型案例与后续计划记录在 [PROJECT_PROGRESS.md](PROJECT_PROGRESS.md)。
