# 公开资料工作台交付记录

日期：2026-09-12。起始提交：`08409ea371e46b1b122219e6ad6fa83b641cb8fc`。

## 实现

单个 FastAPI 应用提供中文三栏工作台、SQLite 持久化和后台运行器，Web 端口为 8080；Qwen3-4B 保持独立服务。应用启动日志记录了 `Application startup complete`。

`app/sources.py` 回收旧 mini-GDELT 的真实 ArtList 请求方式，提供 GDELT、UN News RSS 和 IMF PortWatch 日度聚合适配器。`app/llm.py` 回收原 Qwen OpenAI 兼容请求路径，按当前 vLLM 的顶层 `chat_template_kwargs` 关闭 thinking。`app/store.py`、`runner.py`、`main.py` 和同源静态前端为本次新增。

`app/history.py` 直接读取原案例文件。图表使用原报告现有的 5 条日观测；Sentinel 页面展示资产目录和既有结果。`data/news_replay.json` 回收 8 条公开民用航运与能源价格新闻标题，保留原 URL、来源及 GDELT 收录时间，每次释放下一批材料给与在线模式相同的持久化和模型流程。

## 启动

新环境在仓库根目录执行：

```bash
python -m pip install -e .
cp .env.example .env
python -m app
```

Windows 将第二条替换为 `Copy-Item .env.example .env`。也可使用 `bash scripts/start_demo.sh` 或 `.\scripts\start_demo.ps1`。

本次部署直接使用服务器原有 Python 环境启动，已有依赖为 FastAPI 0.136.3、uvicorn 0.50.2、httpx 0.28.1、Pydantic 2.13.4、python-dotenv 1.2.2。依赖清单记录这些版本。原模型环境为 vLLM 0.24.0，响应模型标识为 `qwen3-4b`，上下文 8192 tokens；沿用原启动脚本，GPU 显存比例设置为 0.55。

部署时设置 `APP_HOST` 为服务器的内网地址；`MODEL_BASE_URL` 为同机模型的 `/v1` 地址；`DATA_DIR` 默认 `runtime/`。项目运行配置在服务器 `.env`，材料与运行记录在 `runtime/demo.sqlite3` 和 `runtime/materials/`。浏览器访问配置的主机地址与 8080 端口。

## 首批实际材料

以下时间为 UTC，材料和完整模型调用记录保存在部署实例的运行历史中。

| 来源与操作 | 运行 ID | 时间 | 实际结果 |
|---|---|---|---|
| GDELT，主题 `shipping`，最近 7 天 | `9d797f077bd04e0aa50ec62041043d37` | 08:29:37–08:29:48 | 服务返回 HTTP 429；记录为来源失败，未产生材料 |
| UN News RSS，主题 `climate`，最近 30 天 | `f337dcd1097d4731bf70bf903d9fac8b` | 08:30:33–08:30:46 | 新增 3 条真实文章，3 条 Qwen 资料卡，失败 0；模型调用 8254 ms |
| 已保存新闻标题，回放第一批 3 条 | `bbe7b7c177284f299cb335e7eebebba8` | 08:31:24–08:31:35 | 新增 3 条历史标题，3 条新 Qwen 资料卡，失败 0 |
| IMF PortWatch，民用航运日度聚合 | `df8a7f019f5546b6ab85bec93375c46c` | 08:32:25–08:32:37 | 新增 3 条日度聚合记录，3 条 Qwen 资料卡，失败 0；观测日期为 2026-09-04 至 09-06 |

RSS 本轮材料涉及尼泊尔洪水、气候灾害影响学生上学及联合国气候新闻。模型输入来自实际文章摘录，逐卡关联材料编号。PortWatch 本轮通过来源清单中的公开 ArcGIS 接口，读取霍尔木兹海峡的真实船型计数与名义运力字段。历史标题回放摘要涉及油价、油轮运价与民用航运风险；只使用已释放的标题，没有读取最终案例报告。

GDELT 限流记录与 RSS 成功记录独立保存，未将 RSS 标记为 GDELT。周期更新默认关闭，用户可以在页面开始更新或继续回放剩余标题。
