# Intelligent-Military-Early-Warning-Agent

本仓库包含公开资料工作台 Demo 和既有研究案例。工作台采集公开新闻与民用航运日度聚合资料，调用独立的 Qwen3-4B 服务生成中文资料卡和摘要，并保存来源、运行历史与原始摘录。

## 启动工作台

需要 Python 3.10+。在仓库根目录安装 Web 应用依赖：

```bash
python -m pip install -e .
cp .env.example .env
bash scripts/start_demo.sh
```

Windows PowerShell：

```powershell
python -m pip install -e .
Copy-Item .env.example .env
.\scripts\start_demo.ps1
```

浏览器打开 `http://127.0.0.1:8080`。已有环境可直接使用 `python -m app` 启动；应用固定使用一个 worker。在指定 Python 环境启动时，Linux 设置 `PYTHON`，Windows 使用 `-Python` 参数。

`.env` 中 `MODEL_BASE_URL` 指向已有 OpenAI 兼容接口，默认 `http://127.0.0.1:8000/v1`，`MODEL_NAME=qwen3-4b`。需要鉴权时填写 `MODEL_API_KEY`。Web 应用不会下载模型或管理 GPU 进程。模型未运行时，仍可浏览历史案例和已有资料。

其他配置包括 `APP_HOST`、`APP_PORT`、`DATA_DIR`、请求超时、正文输入长度、`RSS_URL` 和 `PORTWATCH_ID`。数据库与本地材料默认保存在 `runtime/`；重启后会继续显示。内网部署时将 `APP_HOST` 设置为本机内网地址。应用静态资源均由自身提供，不需要 Node 或在线图表库。

## 操作

- **在线资料**：填写主题、来源、回看小时数和材料上限，点击立即运行。选回已有主题可继续更新；同一 URL 内容未变时复用已有资料卡，发生更新时保留版本。
- **来源**：GDELT 接受新闻查询词；UN News RSS 按标题与摘要中的关键词筛选，多个词为同时匹配；IMF PortWatch 读取所配置海峡的民用日度聚合记录，主题名称用于标记监测任务。
- **周期更新**：开启后由服务进程调度，关闭浏览器仍会运行。停止周期更新阻止下一轮；取消当前任务会等正在执行的来源/模型请求结束后停止后续处理。页面刷新不启动新任务。
- **运行历史**：选择轮次查看本次资料、摘要、来源状态、工具调用与耗时。有效空结果、来源失败和模型失败分别显示。模型失败材料保留，下次运行继续分析。
- **历史案例**：读取已提交的霍尔木兹案例报告、来源和 Sentinel 资产目录。图表使用实际保存的 5 条日观测，报告观察窗为 29 天。名义运力沿用源单位，不能解读为实载吨数。
- **新闻标题回放**：8 条已保存的民用航运与能源新闻按 GDELT 收录时间排序，每次只释放下一批至同一持久化和 Qwen 分析流程。原始标题之外没有新闻正文，`available_at` 未确认时留空；本次导入时间独立记录。回放摘要来自新模型调用，历史报告结论单独展示。

GDELT 与 RSS 不会互相替代；来源名称始终对应实际选择。工作台只做公开资料整理与历史展示，不生成军用目标轨迹或军事行动预测。

实现与部署记录见 [DEMO_DELIVERY.md](DEMO_DELIVERY.md)。

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

项目进度、已验证数据源、典型案例与后续计划记录在 [PROJECT_PROGRESS.md](PROJECT_PROGRESS.md)。
