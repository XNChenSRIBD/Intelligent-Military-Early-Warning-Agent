# Data Sources and Download Endpoints

本文件只记录公开下载入口和精确产品标识，不镜像原始数据。检索日期为 `2026-08-05`；在线数据可能继续修订或更新。

## Source ownership

| 数据 | 对外发布者 | 底层提供者/物理来源 | 属性 | 本案例状态 |
|---|---|---|---|---|
| IMF PortWatch | IMF，联合牛津大学开发 | 联合国全球平台汇聚的 AIS；UNGP文档记录过 exactEarth、ORBCOMM、FleetMon 等可能供应商，但本PortWatch图层的具体馈源组合未公开确认 | 国际组织公开聚合产品，底层含商业 AIS | 已使用公开日度聚合 |
| WTO霍尔木兹贸易跟踪器 | WTO | AXSMarine（Signal Group）的 AIS 与专有货流模型 | 国际组织公开发布、商业底层模型 | 已使用公开指数 |
| Sentinel-1/2 | 欧盟 Copernicus，由 ESA 实施 | Sentinel卫星 SAR/光学传感器 | 公共部门地球观测 | 已分析7个核心开放资产；另有同轨场景待处理 |
| Natural Earth | Natural Earth开放制图项目 | 公共领域地理数据 | 公共领域 | 用作粗陆地掩膜 |
| Global Fishing Watch SAR | 非营利组织GFW | Sentinel-1及GFW检测/匹配流程 | 非营利混合来源 | 未取得2026窗口数据 |
| 逐船原始历史 AIS | 商业供应链 | 具体供应商取决于数据授权合同 | 商业/受限 | 未取得、未使用 |

“公开发布”不等于“政府原始传感器数据”。PortWatch 和 WTO 是公开可访问的分析产品，但二者仍依赖商业 AIS 数据供应链；Sentinel 则是欧盟公共卫星的物理观测。

## 1. IMF PortWatch

- 项目入口：<https://portwatch.imf.org/>
- IMF/牛津大学发布说明：<https://www.imf.org/en/news/articles/2023/11/13/pr23390-imf-university-oxford-launch-portwatch-platform-monitor-simulate-trade-disruptions>
- 霍尔木兹日度咽喉点图层：<https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/Daily_Chokepoints_Data/FeatureServer/0>
- 日度港口图层：<https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/Daily_Ports_Data/FeatureServer/0>
- 联合国全球平台AIS来源说明：<https://millenniumindicators.un.org/wiki/spaces/AIS/pages/57999715/AIS%2Bdata%2Bat%2Bthe%2BUN%2BGlobal%2BPlatform>

霍尔木兹查询参数：

```text
where=portid='chokepoint6' AND date >= DATE '2026-02-14' AND date <= DATE '2026-03-14'
outFields=*
orderByFields=date
returnGeometry=false
f=json
```

伊朗港口查询参数：

```text
where=ISO3='IRN' AND date >= DATE '2026-02-14' AND date <= DATE '2026-03-14'
outFields=*
orderByFields=date,portid
returnGeometry=false
f=json
```

本案例实际获取29条霍尔木兹日记录，以及15个伊朗港口的435条港口日记录。港口汇总因异常尖峰未用于核心结论。

## 2. WTO–AXSMarine Strait of Hormuz Trade Tracker

- 仪表板：<https://datalab.wto.org/Strait-of-Hormuz-Trade-Tracker>
- 方法说明：<https://datalab.wto.org/sites/default/files/2026-03/methodological_notes.pdf>
- 原油外运CSV：<https://wtomais.blob.core.windows.net/strait-of-hormuz-tracker/voy_intake_index_curde_oil_export.csv>
- LNG外运CSV：<https://wtomais.blob.core.windows.net/strait-of-hormuz-tracker/voy_intake_index_lng_export.csv>
- 化肥相关产品外运CSV：<https://wtomais.blob.core.windows.net/strait-of-hormuz-tracker/voy_intake_index_fertilizer_export.csv>
- 农产品输入CSV：<https://wtomais.blob.core.windows.net/strait-of-hormuz-tracker/voy_intake_index_agricultural_product_import.csv>

注意：WTO源文件中的原油URL确实拼写为 `curde_oil`，下载时不要自行更正。原油、LNG和化肥使用 `voy_load_date`；农产品输入使用 `voy_disch_date`。

## 3. Sentinel-1 SAR and Sentinel-2 optical

官方目录与下载：

- Copernicus Data Space：<https://dataspace.copernicus.eu/>
- OData目录/下载文档：<https://documentation.dataspace.copernicus.eu/APIs/OData.html>
- STAC文档：<https://documentation.dataspace.copernicus.eu/APIs/STAC.html>
- Sentinel-1任务说明：<https://sentiwiki.copernicus.eu/web/s1-mission>
- Sentinel-2任务说明：<https://sentiwiki.copernicus.eu/web/s2-mission>

免登录开放镜像：

- Element 84 Earth Search STAC：<https://earth-search.aws.element84.com/v1>
- Sentinel-1 COG对象：`https://sentinel-s1-l1c.s3.amazonaws.com/`
- Sentinel-2 L2A COG对象：`https://sentinel-cogs.s3.us-west-2.amazonaws.com/`

物理来源仍然是欧盟Copernicus/ESA Sentinel任务；Element 84与AWS是检索和托管层，不是传感器提供者。

CDSE筛选出的21景官方SAFE产品、产品UUID、预计字节数、MD5及认证下载URL见 [`data/sentinel_selected_products.csv`](data/sentinel_selected_products.csv)。实际分析使用的3个S1 VV COG、2个S2 visual和2个S2 SCL的免登录精确链接、产品名、字节数和ETag见 [`data/sentinel_analysis_assets.csv`](data/sentinel_analysis_assets.csv)。两个清单可用 `source_product` 连接；带分段后缀的S3多段ETag不是内容MD5，不能替代独立SHA-256校验。

场景选择：

- S1 R166：`2026-02-16 / 2026-02-28 / 2026-03-12`，已分析中部同轨VV资产；
- S1 R57：`2026-02-14 / 2026-02-26 / 2026-03-10`，已编目但尚未完成分析；
- S2 R120 `40REP/40REQ`：`2026-02-23 / 2026-02-28 / 2026-03-05`；当前仅完成 `40REP` 前两期visual/SCL探索。

## 4. Natural Earth land mask

- 来源页：<https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-land/>
- 使用的GeoJSON镜像：<https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson>
- 检索时SHA-256：`1ac90796408bc6ad6911d69448485d3c4dbf2190370080368a09976e1c9f7416`

该掩膜仅用于排除明显陆地/岸线亮散射，不足以精确描述港池、平台、浮标、防波堤、填海和小岛。

## 5. Global Fishing Watch SAR — not acquired

- API文档：<https://globalfishingwatch.org/our-apis/documentation>
- 数据下载门户：<https://globalfishingwatch.org/data-download/>
- 目标数据集：`public-global-sar-presence:latest`
- 匿名matched图层：<https://www.arcgis.com/home/item.html?id=a5d62eb811ea4652ae73fa19f0e0b576>
- 匿名unmatched图层：<https://www.arcgis.com/home/item.html?id=a5d3b270fd4349bfa1a080ca315b9c28>

2026窗口的API/下载门户需要登录或token，本案例未取得记录；匿名ArcGIS图层的公开时间维只到2025-12-31，不能代替本锚点数据。因此GFW不计入分析结论。

## Machine-readable manifest

完整入口、查询条件、检索时间和使用状态见 [`data/source_manifest.json`](data/source_manifest.json)。所有凭据均未写入仓库。
