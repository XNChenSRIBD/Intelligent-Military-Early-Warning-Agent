# Intelligent-Military-Early-Warning-Agent

面向军政事件早期态势感知的多源异动预警与根因分析 Agent 项目。当前重点是构建一个“LLM 负责编排、信号处理前端负责计算”的 GNSS / 新闻 / GPSJam / 后续 AIS-ADS-B-NOTAM 融合验证框架。

## Current Focus

- `mini-GDELT` / 新闻主题监测作为第一 monitor，用于识别 topic 升温、主体、事件类型与 heat。
- 当 topic heat 触发 hard gate 后，接入 NASA CDDIS / IGS 地面站 RINEX 数据。
- 信号处理前端计算 CNR/C/N0 差分、跨站同步、C/L/D 一致性与历史同窗对照。
- LLM Core 只读取前端报告，执行流程编排、反证检查和证据卡生成，不直接参与数值计算。

## Repository Layout

```text
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

项目进度、已验证数据源、典型案例与后续计划记录在 [PROJECT_PROGRESS.md](PROJECT_PROGRESS.md)。
