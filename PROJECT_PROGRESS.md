# Project Progress

Last updated: 2026-09-12

## 公开资料工作台 Demo（2026-09-12）

新增单进程 FastAPI 应用、同源中文前端、SQLite 持久化、后台周期运行、GDELT / UN News RSS / IMF PortWatch 采集和 Qwen3-4B 摘要接口。新闻卡片关联原始 URL、获取时间、材料版本和实际模型调用；模型失败材料留待下一轮分析。既有霍尔木兹结果以历史快照展示，另回收 8 条民用航运与能源新闻标题用于按顺序分批回放。

运行说明见 [README.md](README.md)，本次部署与实际资料记录见 [DEMO_DELIVERY.md](DEMO_DELIVERY.md)。下文保留既有研究方向及历史进度，不代表 Web Demo 提供了这些研究能力。

## 1. Project Goal

本项目目标是构建一个面向军政行动早期态势感知的多源异动预警与根因分析 Agent。核心原则是：LLM Core 不直接参与信号计算，只负责读取信号处理前端的结构化报告，完成流程编排、工具选择、反证逻辑和证据卡输出。

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

4. SP Tool 4: AIS / ADS-B / NOTAM Check
   - 当前仍处于待完善阶段。
   - 目标是补充行动侧证据，包括航路/海路管制、船机密度变化、NOTAM 限制空域等。

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

待完善：

- AIS
- ADS-B 原始/历史接口
- NOTAM 实时与历史 API
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

## 7. Next Steps

1. 将 IGS / CDDIS download skill 接入 harness，作为 SP Tool 2 的数据获取前端。
2. 固化 evidence card schema，统一正例、弱例、负例的判读字段。
3. 扩展空间天气/太阳活动反证模块。
4. 接入 NOTAM、AIS、ADS-B，用于行动侧先兆验证。
5. 将 mini-GDELT topic mining 输出标准化为 JSONL / CSV 双格式。
6. 在 Qwen3-4B LLM Core 中只保留编排、摘要、反证选择和证据卡生成逻辑。
