# Project Progress

Last updated: 2026-09-12

## 公开资料工作台 Demo（2026-09-12）

新增单进程 FastAPI 应用、同源中文前端、SQLite 持久化、后台周期运行、GDELT / UN News RSS / IMF PortWatch 采集和 Qwen3-4B 摘要接口。新闻卡片关联原始 URL、获取时间、材料版本和实际模型调用；模型失败材料留待下一轮分析。既有霍尔木兹结果以历史快照展示，另回收 8 条民用航运与能源新闻标题用于按顺序分批回放。

运行说明见 [README.md](README.md)，本次部署与实际资料记录见 [DEMO_DELIVERY.md](DEMO_DELIVERY.md)。下文保留既有研究方向及历史进度，不代表 Web Demo 提供了这些研究能力。

Status snapshot: the public-data Iran / Strait of Hormuz case package has been merged into `main`. The repository now records source endpoints and derived results without committing raw AIS, satellite rasters, credentials or server paths.

## 1. Project Goal

本项目目标是构建一个面向军政行动早期态势感知的多源异动预警与根因分析 Agent。核心原则是：LLM Core 不直接参与信号计算，只负责读取信号处理前端的结构化报告，完成流程编排、工具选择、反证逻辑和证据卡输出。

### Current Status Snapshot

| Workstream | Status | Current evidence / gap |
|---|---|---|
| Topic monitoring | Partial | GDELT / 新闻可用于 topic heat；结构化 mini-GDELT 输出仍待固化 |
| GNSS ground observations | Implemented / case-tested | IGS / CDDIS下载skill已入库；哈尔科夫、伊朗和委内瑞拉案例已完成不同强度的CNR验证 |
| Maritime public aggregates | Case-integrated | IMF PortWatch与WTO–AXSMarine已用于霍尔木兹历史回溯，但二者同属AIS依赖家族 |
| EO maritime cross-check | Partial | Sentinel-1 R166和Sentinel-2 `40REP`已完成探索性分析；S1 R57仍待处理 |
| GFW SAR presence | Source identified / data unavailable | 2026窗口需要账户或token，当前未取得记录 |
| Vessel-level AIS / ADS-B / NOTAM | Pending | 尚无逐船、逐航班的完整历史权限或稳定历史API |
| Production warning harness | Pending | 阈值冻结、无前视回测、证据卡schema和自动编排尚未完成 |

## 2. Current Harness / Loop Design

当前 loop 按照四类 SP Tool + LLM Core 组织：

1. SP Tool 1: Topic Mining
   - 从 GDELT / 新闻源读取事件文本。
   - 抽取主体、地区、行动类型、关键词和 topic heat。
   - 当 heat 明显升温时触发 GNSS 侧数据接入。

2. SP Tool 2: CNR Detection
   - 从 NASA CDDIS / IGS 下载目标地区及对照站 RINEX/CRINEX 数据。
   - 对 RINEX `S*` 观测量计算 CNR/C/N0 低尾差分。
   - 主要指标包括 `Delta CNR p10`、`median Delta CNR p10`、低 CNR 比例、活跃卫星数变化。

3. SP Tool 3: C/L/D Correlation
   - 检查 CNR、载波相位、伪距和多普勒之间的一致性。
   - 用于区分单一 CNR 低尾下探、接收机链路问题、传播扰动和更复杂的人为干扰可能性。

4. SP Tool 4: Maritime / Aviation / EO Check
   - 已接入 IMF PortWatch 与 WTO–AXSMarine 的公开宏观指标，并加入 Sentinel-1/2 独立EO旁证流程。
   - PortWatch与WTO均依赖AIS，只能作为一个依赖家族，不能重复计为两票独立确认。
   - 逐船历史AIS、ADS-B原始/历史接口、NOTAM与NAVTEX等行动侧数据仍待接入。
   - EO输出目前是亮散射或亮像素候选，不是确认船舶、单船身份或连续航迹。

LLM Core 的职责是读取每个工具输出的 summary、manifest、evidence card 和反证项，决定是否继续监控、扩大范围、执行反证或生成最终结论。

## 3. Data Sources

已接入或已验证：

- NASA CDDIS / IGS RINEX
  - high-rate: 1 Hz, 15-minute `.crx.gz`
  - daily: 30 s, 1-day `.crx.gz`
  - 可用于 CNR/C/N0、载波相位、伪距、多普勒等 GNSS 观测量分析。

- GDELT / 新闻
  - 用作 topic monitor，不直接替代信号证据。
  - 后续 mini-GDELT agent 可由本地小模型抽取事件结构化 JSON。

- GPSJam
  - 主要提供民航 ADS-B 侧推导的 GNSS 干扰强度栅格。
  - 更适合事后验证和区域态势佐证，不宜单独作为事前强预警。

- IMF PortWatch
  - 已获取霍尔木兹海峡 `2026-02-14` 至 `2026-03-14` 的29条日度记录，并使用2019年以来历史序列进行类比回测。
  - 产品是公开的AIS派生聚合指标，不是逐船历史AIS；底层商业馈源的具体组合未公开确认。
  - 伊朗15港口的435条港口日记录已下载，但存在 `Bandar-E Pars Terminal` 集中尖峰，未用于核心结论。

- WTO–AXSMarine Strait of Hormuz Trade Tracker
  - 已获取原油、LNG、化肥相关产品和农产品四类公开指数。
  - WTO公开发布，AXSMarine（Signal Group）提供AIS与专有货流模型；与PortWatch不是独立证据家族。
  - 标记为2月27日的外运数据通常到2月28日17:00 CET才可见，因此只能作为回溯性弱领先或同期异常。

- Copernicus Sentinel-1 / Sentinel-2
  - 欧盟Copernicus/ESA公共卫星数据；已编目21景官方产品及其下载URL。
  - 已分析3个S1 R166 VV资产，以及S2 `40REP` 两期visual与SCL共4个资产。
  - S1 R57的2月14日、2月26日和3月10日同轨序列仍待完成，其中2月26日距锚点约34小时。

- Natural Earth
  - 使用1:10m陆地多边形作为粗岸线排除掩膜。
  - 不能替代精细港池、平台、浮标、防波堤和填海边界。

待完善：

- 逐船历史 AIS
- ADS-B 原始/历史接口
- NOTAM 实时与历史 API
- Global Fishing Watch 2026窗口SAR presence
- NAVTEX / 港口公告等行动侧公开信息
- 空间天气/太阳活动反证数据源

## 4. New Skill: IGS / CDDIS API Download

已新增 skill:

```text
skills/igs-cddis-api-download/
  SKILL.md
  scripts/igs_cddis_api.py
```

能力：

- 使用 Earthdata Login / CDDIS 认证。
- 支持 `highrate` 与 `daily` 产品。
- 支持站点、日期、小时窗口过滤。
- 输出下载 manifest 和 index inventory。
- 下载后校验 gzip 完整性。

安全原则：

- 仓库内不保存明文用户名和密码。
- 运行时通过 `.netrc` 或 `EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD` 注入。

## 5. Case Study Status

### Kharkiv

锚点：2024-05-10 哈尔科夫方向热冲突升级。

当前结论：

- `2024-05-08 / 2024-05-09 / 2024-05-10` 均出现高于控制期的 GNSS CNR 压力。
- `2024-05-09 T-24h` 的综合风险分数最高，具备一定事前先兆特征。
- 已加入远端旁证窗口 `2024-04-10`，用于对比非爆发窗口下的同UTC时段曲线。
- C/L/D 粗筛未给出强人工干扰闭环，因此结论仍是 PNT 环境压力强验证，而非直接证明 jamming/spoofing。

### Iran / Strait of Hormuz

锚点：2026-02-28 霍尔木兹相关事件窗口。

当前结论：

- Topic heat 在事件前升温。
- `2026-02-27 T-24h` 与远端旁证窗口在总体幅度上接近，支持其更像背景/弱波动。
- `2026-02-28 T0` 出现更明显 CNR 低尾下探和同步压力。
- BSHM 单站占比过高，严格 3 站同步证据不足，因此判为弱支持/低置信案例。
- IMF PortWatch显示，2月25日至26日总船数仅下降约23%，但总运力下降约67%、油轮运力下降约78%，属于罕见但历史上并非唯一的船队构成切换。
- 锚点前可识别2月19日与22日的非定向高波动、2月26日的构成异常和2月27日的跨指标低尾，但单次转换的历史近邻均未出现后续持续崩塌，不能单独作为定向预警。
- 3月1日后AIS派生的可见船流指标出现持续断崖；锚点前14日与后14日相比，总船数均值下降96.7%、总运力下降97.8%，但尚非独立物理确认。
- Sentinel-1 R166广域原始亮散射候选在锚点当日未断崖、到3月12日出现约19%–24%的阈值稳健下降；这些是探索性候选，不是确认船舶。
- 当前没有确认的EO事前预兆；最关键的待处理序列是S1 R57的2月14日、2月26日和3月10日同轨数据。
- WTO的2月27日低尾值存在发布滞后，最早通常在2月28日17:00 CET可见，不应回填为2月27日实时告警。
- 当前案例状态为 `retrospective_exploratory_case_study`，不构成封航事实、暗船数量、单船身份或未来事件预测。
- 详细数据源、结构化报告与限制见 [cases/iran-hormuz-2026-02-28/README.md](cases/iran-hormuz-2026-02-28/README.md)。

### Venezuela / Maduro

锚点：2026 年马杜罗相关事件。

当前结论：

- 战争/军事行动烈度相对较低。
- IGS 地面站 CNR 异动不明显。
- 更适合作为负面或弱信号案例，提示后续需要 AIS / ADS-B / NOTAM 补充行动侧数据。

## 6. Visualization Outputs

已形成用于汇报的统一图形口径：

- 左轴：`Delta CNR p10 (dB-Hz)`
- 右轴：严格同步站点数
- 蓝线：station-tail `Delta CNR p10`
- 灰线：median `Delta CNR p10`
- 橙线：strict sync stations
- 分面：同一 UTC 时段在不同日期或远端旁证窗口下的表现

注意：处理代码中仍可能出现 `snr_p10` 字段名，这是因为 RINEX 中 C/N0 类观测量以 `S*` 字段存储。汇报和物理解释中统一称为 CNR 或 C/N0，单位为 dB-Hz。

## 7. Repository Deliverables

当前已经进入仓库的主要可复用产物：

- `skills/igs-cddis-api-download/`
  - IGS / CDDIS RINEX/CRINEX索引、下载、认证与校验能力。
- `cases/iran-hormuz-2026-02-28/`
  - `README.md`：多模态案例结论、历史类比和解释边界。
  - `DATA_SOURCES.md`：公开发布者、底层提供者、商业/公共属性和下载入口。
  - `data/source_manifest.json`：机器可读数据源与访问状态。
  - `data/sentinel_selected_products.csv`：21景CDSE官方产品清单。
  - `data/sentinel_analysis_assets.csv`：7个实际分析开放资产的精确URL与ETag。
  - `results/freight_case_report.json`：货运异常、历史类比和预警时间语义。
  - `results/eo_candidate_summary.json`：S1/S2探索性候选及不可判定边界。

## 8. Next Steps

1. 将 IGS / CDDIS download skill 接入 harness，作为 SP Tool 2 的数据获取前端。
2. 固化 evidence card schema，统一正例、弱例、负例的判读字段。
3. 完成 Sentinel-1 R57 `2026-02-14 / 02-26 / 03-10` 同轨分析，并建立接近PortWatch空间定义的固定门区。
4. 取得 GFW 访问权限，下载2026窗口SAR presence，并按实际成像覆盖面积归一化matched/unmatched检测。
5. 为PortWatch/WTO案例补齐可重复运行的下载、全历史近邻扫描和无前视滚动回测程序；冻结阈值后再进入生产告警规则。
6. 接入逐船历史AIS、ADS-B、NOTAM、NAVTEX和港口公告，用于行动侧先兆与反证验证。
7. 扩展空间天气/太阳活动反证模块。
8. 将 mini-GDELT topic mining 输出标准化为 JSONL / CSV 双格式。
9. 在 Qwen3-4B LLM Core 中只保留编排、摘要、反证选择和证据卡生成逻辑。
