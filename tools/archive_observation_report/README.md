# 独立历史观测报告生成器

从已保存的本地资料生成两份可直接打开的历史事实报告。无需模型、服务器或 Node 构建链。

在仓库根目录执行：

```powershell
& 'D:/anaconda3/python.exe' -B tools/archive_observation_report/build.py --output outputs/archive_observation_report
```

也可使用已有 Python 3 环境：

```bash
python -B tools/archive_observation_report/build.py --output outputs/archive_observation_report
```

打开 `outputs/archive_observation_report/index.html`，点击档案卡片切换两例。直接复制整个输出目录即可离线查阅，页面没有在线字体、脚本、接口或图片依赖。

## 输入和输出

- `facts.json`：独立保存的最少必要输入，注明每项来自结构化摘录还是报告摘录；历史原文及其位置单独保存。
- `build.py`：按数值和字段计算关系、生成 HTML 与 `results.json`。复用 `tools/numeric_explanation_check/facts.py` 中不含 I/O 的一般差值函数，读取已有 Round9 结果用于展示历史验证计数，不导入应用或调用模型。
- 输出目录：`index.html`、`kharkiv.html`、`hormuz.html`、`results.json`、`DELIVERY.md`。

`results.json` 包含输入、原来源定位、原话摘录、计算结果及具体缺项。来源原文件不随静态包复制，页面展示其路径、章节和字段定位，不生成到缺失文件的链接。历史回答仅作为历史原文；程序计算出的正确文字不会署名为 Qwen 回答。

## 构建中的必要核对

仅核对本组件的边界关系、增幅、零分母、相邻日期及版本、单位空值、实际输入来源字段和静态本地链接。复用 Round9 的八题输入；没有额外推理、压力测试或全库检查。

一个参考边界缺失时，只计算已知一侧。共同日期是否矛盾取决于原值和来源版本，不取决于两个区间涨跌方向是否相同。窗口计数和中位差保留为来源声明，没有完整序列时不补曲线或重新计数。

本组件不修改 Round6～Round9 原文、原回答、原应用或业务状态。原 `original_goal_status=not_demonstrated`、`strict_lead_hours=null` 保留。
