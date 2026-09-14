"""Build a self-contained historical observation report from saved local facts."""

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("saved_numeric_facts", ROOT / "tools/numeric_explanation_check/facts.py")
numeric = importlib.util.module_from_spec(spec)
spec.loader.exec_module(numeric)


def num(value):
    return Decimal(str(value))


def fmt(value, places=4):
    if value is None:
        return "未提供"
    text = f"{num(value):,.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def plain(value):
    if value is None:
        return "空值（null）"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return fmt(value)
    if isinstance(value, list):
        return "；".join(plain(item) for item in value) or "未提供"
    if isinstance(value, dict):
        return "；".join(f"{key}：{plain(item)}" for key, item in value.items())
    if isinstance(value, str) and value[:4].isdigit() and value[4:5] == "-" and "T" in value:
        return instant(value)
    return str(value)


def h(value):
    return escape(str(value), quote=True)


def instant(value):
    if not value:
        return "未提供"
    text = str(value)
    if "T" not in text:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        pass
    return text + "（时区未注明）"


def boundary_relation(value, lower=None, upper=None):
    """Only infer the relationship to boundaries actually present."""
    if value is None:
        return {"relation": "missing_observation", "text": "本次值未提供"}
    if lower is None and upper is None:
        return {"relation": "missing_reference", "text": "参考边界未提供"}
    if lower is not None and upper is not None and num(lower) > num(upper):
        return {"relation": "conditions_pending", "text": "参考上下界次序待确认"}
    comparisons = []
    for name, reference in (("lower", lower), ("upper", upper)):
        if reference is not None:
            delta = num(value) - num(reference)
            comparisons.append({"boundary": name, "difference": float(delta),
                                "relation": "below" if delta < 0 else "above" if delta > 0 else "equal"})
    if lower is not None and upper is not None:
        if num(lower) == num(upper):
            delta = num(value) - num(lower)
            text = "等于参考值" if delta == 0 else ("低于" if delta < 0 else "高于") + "参考值"
            relation = "equal" if delta == 0 else "below" if delta < 0 else "above"
        elif num(value) < num(lower):
            delta, relation, text = num(value) - num(lower), "below", "低于参考下界"
        elif num(value) > num(upper):
            delta, relation, text = num(value) - num(upper), "above", "高于参考上界"
        else:
            delta, relation = None, "within"
            text = "等于参考下界" if num(value) == num(lower) else "等于参考上界" if num(value) == num(upper) else "位于给定参考范围内"
    else:
        reference = lower if lower is not None else upper
        delta = num(value) - num(reference)
        side = "下界" if lower is not None else "上界"
        relation = ("below" if delta < 0 else "above" if delta > 0 else "equal") + "_" + ("lower" if lower is not None else "upper")
        text = ("低于" if delta < 0 else "高于" if delta > 0 else "等于") + "已知" + side
    return {"relation": relation, "text": text, "boundary_difference": float(delta) if delta is not None else None,
            "comparisons": comparisons, "unprovided_boundaries": [key for key, val in (("lower", lower), ("upper", upper)) if val is None]}


def interval_relations(row):
    endpoints = defaultdict(list)
    changes = []
    for index, interval in enumerate(row["intervals"]):
        start, end = interval["start"], interval["end"]
        changes.append({"start": start, "end": end, **numeric.difference(start["value"], end["value"])})
        for endpoint in (start, end):
            if endpoint.get("date"):
                endpoints[endpoint["date"]].append({"value": endpoint["value"], "interval_index": index,
                    "version": endpoint.get("version", endpoint.get("material_version", interval.get("version"))),
                    "material_id": endpoint.get("material_id")})
    shared = []
    for date, occurrences in endpoints.items():
        if len(occurrences) < 2:
            continue
        values_equal = len({num(item["value"]) for item in occurrences}) == 1
        versions = [item["version"] for item in occurrences]
        known = all(value is not None for value in versions)
        material_ids = [item["material_id"] for item in occurrences]
        partial_material_identity = any(material_ids) and not all(material_ids)
        same_material = not any(material_ids) or all(material_ids) and len(set(material_ids)) == 1
        same_version = known and len(set(map(str, versions))) == 1 and same_material
        status = ("conditions_pending" if not known or partial_material_identity or not row.get("unit") else
                  "version_difference" if not same_version else "consistent" if values_equal else "conflicting_same_version")
        shared.append({"date": date, "occurrences": occurrences, "values_equal": values_equal, "version_comparison": status})
    return {"adjacent_changes": changes, "shared_dates": shared,
            "principle": "比较共同日期的记录值及版本，不比较两段差值符号是否相同。"}


def calculate(kind, row):
    if kind == "comparison":
        if not row.get("comparison_allowed") or not row.get("unit"):
            result = {"relation": "conditions_pending", "text": "比较单位或条件待确认"}
        else:
            result = boundary_relation(row.get("value"), row.get("lower"), row.get("upper"))
        result["missing_fields"] = [key for key in ("value", "unit", "signal", "sampling_seconds", "window_start", "window_end") if row.get(key) is None]
        return result
    if kind == "coverage":
        denominator = Decimal(1)
        for factor in row["denominator_factors"]:
            denominator *= num(factor)
        ratio = None if denominator == 0 else num(row["valid_count"]) / denominator
        return {"denominator": float(denominator), "valid_ratio": float(ratio) if ratio is not None else None,
                "valid_percent": float(ratio * 100) if ratio is not None else None}
    if kind == "change":
        observations = row["observations"]
        if not row.get("comparison_allowed"):
            return {"status": "conditions_pending", "changes": [], "reference_comparisons": []}
        return {"status": "same_field_comparison", "changes": [
            {"start": start, "end": end, **numeric.difference(start["value"], end["value"])}
            for start, end in zip(observations, observations[1:])],
            "reference_comparisons": [{"final_date": observations[-1]["date"], "reference_date": ref["date"],
                "final_value": observations[-1]["value"], "reference_value": ref["value"],
                **boundary_relation(observations[-1]["value"], ref["value"], ref["value"])} for ref in row["references"]]}
    if kind == "intervals":
        return interval_relations(row)
    return {"origin": "来源声明", "recomputed": False}


CSS = """
:root{--ink:#173044;--muted:#5e717c;--line:#dce4e7;--paper:#fff;--canvas:#f3f6f7;--accent:#166979;--wash:#eaf3f4}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--canvas);color:var(--ink);font:15px/1.7 'Segoe UI','Microsoft YaHei',sans-serif}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}a:focus-visible,summary:focus-visible{outline:3px solid #19849a;outline-offset:4px}header{background:#fff;border-bottom:1px solid var(--line)}.top{max-width:1300px;margin:auto;padding:18px 34px;display:flex;justify-content:space-between;align-items:center;gap:24px}.brand{font-weight:700;letter-spacing:.04em;color:var(--ink)}.small{font-size:12px;color:var(--muted)}nav{display:flex;gap:8px;flex-wrap:wrap}nav a{padding:7px 15px;border-radius:7px;color:var(--muted)}nav a.active{background:var(--wash);color:var(--accent);font-weight:650}.wrap{max-width:1300px;margin:auto;padding:32px 34px 60px}.hero{padding:32px 36px;background:#18384b;color:white;border-radius:16px;margin-bottom:26px}.eyebrow{font-size:12px;letter-spacing:.1em;color:#9dd0d8;margin:0 0 8px}h1{font-size:32px;line-height:1.35;margin:0 0 13px}h2{font-size:21px;line-height:1.45;margin:0 0 12px}h3{font-size:16px;margin:18px 0 8px}p{margin:8px 0 14px}.hero p{color:#d8e5eb;max-width:900px}.hero .subline{margin-top:20px;padding-top:15px;border-top:1px solid #3a5668;font-size:13px;color:#b8d1db}.layout{display:grid;grid-template-columns:minmax(0,1fr) 195px;gap:24px}.content{min-width:0}.toc{position:sticky;top:20px;align-self:start;padding:10px 0 12px 18px;border-left:2px solid var(--line);font-size:13px}.toc a{display:block;margin:12px 0;line-height:1.55;color:var(--muted)}.toc strong{font-size:12px;color:var(--ink)}.panel{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:26px;margin-bottom:22px;scroll-margin-top:20px}.section-num{font-size:12px;letter-spacing:.06em;color:var(--accent);display:block;margin-bottom:7px}.lead{color:var(--muted);font-size:14px;max-width:850px}.tag{display:inline-block;background:var(--wash);color:var(--accent);font-size:12px;padding:3px 9px;border-radius:4px;margin:4px 7px 4px 0}.table-scroll{overflow-x:auto;margin:17px 0 8px}table{border-collapse:collapse;width:100%;font-size:13px;line-height:1.65}th{background:#f3f7f8;text-align:left;color:#506570;font-size:12px;font-weight:600;padding:11px 12px;border-bottom:1px solid var(--line);white-space:nowrap}td{padding:15px 12px;border-bottom:1px solid #e9eef0;vertical-align:top}tbody tr:last-child td{border-bottom:none}.comparison{min-width:750px}.comparison td:first-child{min-width:119px}.comparison td:nth-child(2){min-width:150px}.comparison td:nth-child(4){min-width:165px}.value{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}.result{font-weight:650;color:#185c73}.hint{display:block;font-size:12px;color:var(--muted);margin-top:5px}.metric-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0}.metric{padding:18px;background:#f1f6f7;border-radius:8px}.metric span{display:block;font-size:12px;color:var(--muted);margin-bottom:6px}.metric strong{font-size:24px;font-variant-numeric:tabular-nums}.notes{padding:14px 17px;background:#f6f8f9;border-left:3px solid #afc6ce;border-radius:0 6px 6px 0;font-size:13px;margin:16px 0}.notes p:last-child{margin-bottom:0}.facts-list{display:grid;grid-template-columns:minmax(135px,1fr) minmax(0,3fr);gap:0;margin:12px 0}.facts-list dt,.facts-list dd{margin:0;padding:10px 8px;border-bottom:1px solid #edf1f2;overflow-wrap:anywhere}.facts-list dt{font-size:13px;color:var(--muted)}.facts-list dd{font-size:14px}details{margin-top:16px;border-top:1px solid var(--line)}summary{cursor:pointer;padding:13px 0 4px;color:var(--accent);font-size:13px;font-weight:650}.source-item{margin-top:16px;padding:16px;background:#f7f9fa;border:1px solid #e4eaed;border-radius:7px;font-size:12px}.source-item p{margin:6px 0}.source-item code{font-size:11px;overflow-wrap:anywhere;white-space:pre-wrap}blockquote{margin:10px 0;padding:10px 14px;border-left:3px solid #b6c8d1;background:#fff;font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}.case-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}.case-card{display:block;color:var(--ink);background:#fff;padding:30px;border:1px solid var(--line);border-radius:14px;transition:box-shadow .15s,transform .15s}.case-card:hover{text-decoration:none;box-shadow:0 10px 28px #14324410;transform:translateY(-2px)}.case-card .open{display:block;margin-top:26px;padding-top:18px;border-top:1px solid var(--line);color:var(--accent);font-weight:650}.case-card p{color:var(--muted);font-size:14px}.count{color:var(--accent);font-size:12px;letter-spacing:.08em}.remaining li{margin-bottom:10px}.footer{font-size:12px;color:var(--muted);padding:18px 2px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}.footer a{margin-right:18px}.record-box{padding:3px 0 12px}.nowrap{white-space:nowrap}
@media(max-width:1000px){.layout{grid-template-columns:1fr}.toc{display:none}.wrap{padding:22px}.hero{padding:26px}.panel{padding:20px}.top{padding:15px 22px}.small.top-note{display:none}}
@media(max-width:640px){body{font-size:14px}.top{align-items:flex-start;flex-direction:column;gap:10px}.wrap{padding:16px 12px 32px}.hero{padding:22px}h1{font-size:27px}.case-grid{grid-template-columns:1fr;gap:16px}.metric-grid{grid-template-columns:1fr}.facts-list{grid-template-columns:1fr}.facts-list dt{padding-bottom:0;border-bottom:0}.panel{padding:18px 14px}.case-card{padding:24px}nav a{padding:6px 10px}}
@media print{header,.toc,.footer{display:none}.wrap{padding:0}.layout{display:block}.panel{break-inside:avoid}.hero{background:white;color:#173044;border:1px solid #ddd}.hero p,.hero .subline{color:#506570}.table-scroll{overflow:visible}table.comparison{min-width:0}details{display:block}}
"""


def table(headers, rows, klass=""):
    return '<div class="table-scroll"><table class="' + klass + '"><thead><tr>' + ''.join(f'<th scope="col">{h(x)}</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def dl(values):
    return '<dl class="facts-list">' + ''.join(f'<dt>{h(key)}</dt><dd>{h(plain(value))}</dd>' for key, value in values.items()) + '</dl>'


def notes(items):
    return '<div class="notes">' + ''.join(f'<p>{h(plain(item))}</p>' for item in items) + '</div>' if items else ''


def source_details(section, sources):
    chunks = []
    for identifier in dict.fromkeys(section.get("source_claim_ids", [])):
        source = sources[identifier]
        text = f'<div class="source-item"><strong>{h(identifier)}</strong><p>{h(source.get("provenance_note", "已保存资料摘录"))}</p>'
        text += f'<p>来源定位（原文件未随包复制）：<code>{h(source["source_file"])}</code></p><p>字段：<code>{h(source.get("json_pointer", ""))}</code></p>'
        for field in source.get("field_paths", []):
            text += f'<p><code>{h(plain(field))}</code></p>'
        text += f'<p>原记录：<code>{h(plain(source.get("record_ids", {})))}</code></p>'
        for quote in source.get("quotes", []):
            states = quote.get("status", "原样保留")
            states = states if isinstance(states, list) else [states]
            names = {"current_error_confirmed":"当前解释有已确认错误", "intermediate_error_corrected":"该中间错误已修正", "intermediate_error_confirmed":"中间回答曾有错误", "explanation_incomplete":"原解释未完成", "error_quote_not_found":"已审查响应未找到所指原句", "record_correct":"记录正确", "evidence_limitation":"资料范围需分清", "superseded_error_confirmed":"已被替代的旧错误回答"}
            status = "；".join(names.get(state, state) for state in states)
            text += f'<p><strong>{h(quote.get("label", "历史原文摘录"))}</strong> · {h(status)}</p><blockquote>{h(quote["text"])}</blockquote><p>原句位置：<code>{h(plain(quote.get("locator", "")))}</code></p>'
        if source.get("review_note"):
            review = source["review_note"]
            text += '<p><strong>' + h(review.get("label", "已保存的核查意见") if isinstance(review, dict) else "已保存的核查意见") + '：</strong>' + h(review.get("text", "") if isinstance(review, dict) else review) + '</p>'
        for row in section["rows"]:
            if row.get("source_claim_id") == identifier:
                origin = {"structured_excerpt": "结构化导出摘录", "report_excerpt": "报告摘录", "declared_summary": "原材料声明的汇总"}.get(row.get("origin"), "已保存资料摘录")
                text += '<p>' + h(origin) + '：<code>' + h(row["source_field"]) + '</code></p>'
        classification = source.get("classification_source")
        if classification:
            text += '<p>勘误分类来源：<code>' + h(classification["source_file"] + "#" + classification["json_pointer"]) + '</code></p>'
        chunks.append(text + '</div>')
    return '<details><summary>查看来源、历史原文与勘误</summary>' + ''.join(chunks) + '</details>' if chunks else ''


def reference_label(row):
    lower, upper = row.get("lower"), row.get("upper")
    if lower is None and upper is None:
        return "参考未提供"
    if lower is None:
        return "上界 " + fmt(upper) + '<span class="hint">下界未提供</span>'
    if upper is None:
        return "下界 " + fmt(lower) + '<span class="hint">上界未提供</span>'
    return fmt(lower) if num(lower) == num(upper) else fmt(lower) + "～" + fmt(upper)


def display_section(section):
    kind, rows = section["kind"], section["rows"]
    if kind == "comparison":
        entries = []
        for row in rows:
            cal = row["calculated"]
            label = h(row["object"]) + '<span class="hint">' + h(row["metric"]) + '</span>'
            dates = (h(instant(row["window_start"])) + '<br>至 ' + h(instant(row["window_end"]))) if row.get("window_start") and row.get("window_end") else h(row.get("window_label", "代表窗口")) + '<span class="hint">窗口起止未提供</span>'
            if row.get("as_of"):
                dates += '<span class="hint">分析截止 ' + h(instant(row["as_of"])) + '</span>'
            current = '<span class="value">' + fmt(row.get("value")) + '</span><span class="hint">' + h(row.get("unit") or "单位未注明") + '</span>'
            reference = reference_label(row) + '<span class="hint">' + h(row.get("reference_group") or "给定参考") + '</span>'
            if row.get("reference_dates"):
                reference += '<span class="hint">' + h("、".join(row["reference_dates"])) + '</span>'
            if row.get("sample_days") is not None:
                reference += '<span class="hint">' + fmt(row["sample_days"]) + ' 个参考日</span>'
            result = '<span class="result">' + h(cal["text"]) + '</span>'
            delta = cal.get("boundary_difference")
            if delta is not None and delta != 0:
                result += '<span class="hint">相差 ' + fmt(abs(delta)) + ' ' + h("dB" if row.get("unit") == "dB-Hz" else row.get("unit") or "原字段单位") + '</span>'
            meta = h(row.get("signal") or "信号未提供") + '<span class="hint">' + (fmt(row["sampling_seconds"]) + ' 秒采样' if row.get("sampling_seconds") is not None else '采样间隔未提供') + '</span>'
            entries.append([label, dates, current, reference, result, meta])
        return table(["对象／指标", "观测窗口与截止", "本次值", "参考", "本次计算", "信号与采样"], entries, "comparison")
    if kind == "groups":
        entries = []
        for row in rows:
            obj = h(row.get("object") or "已保存对象")
            if row.get("window_start") and row.get("window_end"):
                obj += '<span class="hint">' + h(instant(row["window_start"])) + '<br>至 ' + h(instant(row["window_end"])) + '</span>'
            if row.get("as_of"):
                obj += '<span class="hint">分析截止 ' + h(instant(row["as_of"])) + '</span>'
            if row.get("signal"):
                obj += '<span class="hint">' + h(row["signal"]) + ' · ' + h(row.get("unit") or "单位未提供") + (' · ' + fmt(row["sampling_seconds"]) + ' 秒采样' if row.get("sampling_seconds") is not None else '') + '</span>'
            ref = h(row.get("reference_group") or "给定参考") + '<span class="hint">' + h("、".join(row.get("reference_dates", []))) + '</span>'
            ordered = row.get("ordered_groups")
            counts = (h("；".join(item["windows"] + item["relation"] for item in ordered)) if ordered else "低于 " + fmt(row.get("below_count")) + " 窗；高于 " + fmt(row.get("above_count")) + " 窗")
            entries.append([obj, ref, fmt(row.get("reference_days")), fmt(row.get("window_count")),
                            fmt(row.get("difference_median")) + (' ' + h(row["unit"]) if row.get("difference_median") is not None and row.get("unit") else ''), counts])
        return '<span class="tag">计数与中位差：来源声明</span>' + table(["对象", "参考组", "参考天数", "配对窗口", "逐窗差值中位数", "窗口方向"], entries)
    if kind == "coverage":
        out = ''
        for row in rows:
            cal = row["calculated"]
            percent = fmt(cal["valid_percent"], 2) + "%" if cal["valid_percent"] is not None else "分母为零，无法计算"
            out += '<h3>' + h(row["object"]) + '</h3><div class="metric-grid">'
            for label, value in (("已保存窗口", fmt(row["window_seconds"]) + " 秒"), ("窗口内历元", fmt(row["epoch_count"])), ("卫星样本有效比例", percent)):
                out += f'<div class="metric"><span>{h(label)}</span><strong>{h(value)}</strong></div>'
            out += '</div>' + dl({"观测起止": instant(row.get("window_start")) + " 至 " + instant(row.get("window_end")),
                "有效比例计算": fmt(row["valid_count"]) + " / (" + " × ".join(fmt(x) for x in row["denominator_factors"]) + ")：" + percent,
                "分母来源": row["denominator_definition"], "历元覆盖比例": row["epoch_coverage_ratio"], "历元覆盖含义": row["epoch_coverage_definition"]})
        return out
    if kind == "change":
        out = ''
        for row in rows:
            out += '<h3>' + h(row["object"] + " · " + row["metric"]) + '</h3><p class="small">' + h(row.get("unit") or "同一原字段比较；绝对单位未注明") + '</p>'
            for item in row["calculated"]["changes"]:
                out += table(["比较区间", "原值 → 后值", "绝对变化", "相对变化"], [[h(item["start"]["date"] + " → " + item["end"]["date"]),
                    '<span class="value">' + fmt(item["start"]["value"]) + " → " + fmt(item["end"]["value"]) + '</span>',
                    fmt(item["difference"]), fmt(item["relative_change_percent"], 2) + '%' if item["relative_change_percent"] is not None else '起点为零，无法计算']])
            out += table(["最终观测", "历史比较日", "历史值", "绝对水平比较"], [[h(x["final_date"]) + '<br>' + fmt(x["final_value"]),
                h(x["reference_date"]), fmt(x["reference_value"]), h(x["text"]) + ' ' + fmt(abs(x["boundary_difference"]))]
                for x in row["calculated"]["reference_comparisons"]])
            out += '<p class="small">增量 = 后值 − 原值；增幅 = 增量 / 原值 × 100%。大小关系由原值计算，原表未提供统计检验。</p>'
        return out
    if kind == "intervals":
        out = ''
        for row in rows:
            cal = row["calculated"]
            out += table(["对象／指标", "观测日期区间", "分析截止", "日记录", "绝对变化"], [[h(row["object"] + " · " + row["metric"]),
                h(x["start"]["date"] + " → " + x["end"]["date"]), h(instant(row["intervals"][i].get("as_of"))), fmt(x["start"]["value"]) + " → " + fmt(x["end"]["value"]) + ' ' + h(row.get("unit") or "单位未注明"),
                ('+' if x["difference"] > 0 else '') + fmt(x["difference"])] for i, x in enumerate(cal["adjacent_changes"])])
            for shared in cal["shared_dates"]:
                text = shared["date"] + " 在两段比较中的记录值" + ("均为 " + fmt(shared["occurrences"][0]["value"]) if shared["values_equal"] else "不同：" + "、".join(fmt(x["value"]) for x in shared["occurrences"])) + "。"
                state = shared["version_comparison"]
                text += {"conditions_pending": "来源版本或单位未完全注明，版本一致性待确认。", "version_difference": "记录来自不同版本，保留版本差异。", "consistent": "同一版本的共同日期记录一致；前一段增加、后一段减少不构成数值冲突。", "conflicting_same_version": "同一版本、同一日期记录值不同，差异并列保留。"}[state]
                out += notes([text])
        return out
    out = ''
    for row in rows:
        out += '<div class="record-box"><h3>' + h(row.get("label", "已存记录")) + '</h3>' + dl(row.get("values", {})) + '</div>'
    return out


def shell(title, content, cases, active="index"):
    links = [('index', '档案首页')] + [(case["id"], case["title"]) for case in cases]
    nav = ''.join(f'<a href="{h(key)}.html" class="{"active" if key == active else ""}"' + (' aria-current="page"' if key == active else '') + f'>{h(label)}</a>' for key, label in links)
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>' + h(title) + '</title><style>' + CSS + '</style></head><body><header><div class="top"><a class="brand" href="index.html">历史观测档案</a><nav aria-label="档案切换">' + nav + '</nav><span class="small top-note">离线查阅 · 已保存资料</span></div></header><main class="wrap">' + content + '<footer class="footer"><span>本次事实说明由程序按已存资料生成。</span><span><a href="results.json" download>事实与来源数据</a><a href="DELIVERY.md">交付说明</a></span></footer></main></body></html>'


def case_page(case, cases, sources):
    hero = '<section class="hero"><p class="eyebrow">历史资料 / 事实查阅</p><h1>' + h(case["title"]) + '</h1><p>' + h(case.get("subtitle", "已保存的代表窗口与汇总")) + '</p><p>' + h(case["scope"]) + '</p><div class="subline">' + h(plain(case["period"])) + '</div></section>'
    sections, toc = '', '<strong>本页内容</strong>'
    for index, section in enumerate(case["sections"], 1):
        toc += f'<a href="#{h(section["id"])}">{index:02d} {h(section["title"])}</a>'
        sections += f'<section class="panel" id="{h(section["id"])}"><span class="section-num">{index:02d} / 已保存事实</span><h2>{h(section["title"])}</h2><p class="lead">{h(section.get("lead", ""))}</p>' + display_section(section) + notes(section.get("notes", [])) + source_details(section, sources) + '</section>'
    sections += '<section class="panel" id="remaining"><h2>这些材料还没有说明什么</h2><ul class="remaining">' + ''.join(f'<li>{h(plain(item))}</li>' for item in case["remaining"]) + '</ul><details><summary>查看原记录保留状态</summary><p class="small">original_goal_status=not_demonstrated；strict_lead_hours=null。此处沿用历史验收字段，未产生新的判断。</p></details></section>'
    toc += '<a href="#remaining">具体缺项</a>'
    return shell(case["title"], hero + '<div class="layout"><div class="content">' + sections + '</div><aside class="toc">' + toc + '</aside></div>', cases, case["id"])


def index_page(data):
    body = '<section class="hero"><p class="eyebrow">ARCHIVE / 历史观测</p><h1>从具体数值，查阅两份历史档案</h1><p>接收站观测、参考样本与航运日记录。先看原值和比较结果，再按需展开来源与历史回答。</p><div class="subline">无需登录、模型接口或联网。两份报告使用同一套呈现方式。</div></section><div class="case-grid">'
    for index, case in enumerate(data["cases"], 1):
        body += f'<a class="case-card" href="{h(case["id"])}.html"><span class="count">档案 {index:02d}</span><h2>{h(case["title"])}</h2><p>{h(case["subtitle"])}</p><span class="tag">{h(plain(case["period"]))}</span><p>{h(case["scope"])}</p><span class="open">打开档案 →</span></a>'
    body += '</div><section class="panel" style="margin-top:24px"><h2>阅读方式</h2><p class="lead">本次数值关系由程序计算；窗口计数、中位差和覆盖汇总按来源声明保留。历史模型原答单独标注，放在各节的展开区域。</p><details><summary>查看已完成的 Round9 验证记录</summary>'
    counts = data.get("round9_context", {}).get("comparison_counts", {})
    if counts:
        body += table(["既有八题验证", "完整通过", "明确错误", "未答完整", "待复核"], [[label] + [str(counts[key]["outcomes"][field]) for field in ("pass", "incorrect", "incomplete", "review_needed")] for key, label in (("A", "直接读表"), ("B", "附带程序事实"))])
    body += '<p class="small">以上沿用已完成的匿名八题记录；本次新增模型调用为 0。该组计数不作为历史案例或一般模型准确率。</p></details></section>'
    return shell("历史观测档案", body, data["cases"])


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.ids, self.details = [], set(), 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if key in attrs:
                self.links.append(attrs[key])
        self.details += tag == "details"


def local_checks(data, output):
    # Focused arithmetic cases requested by this delivery, without inference calls.
    assert boundary_relation(5, 3, None)["relation"] == "above_lower"
    assert boundary_relation(3, None, 5)["relation"] == "below_upper"
    assert boundary_relation(3, 3, 3)["relation"] == "equal"
    assert boundary_relation(4, 3, 5)["relation"] == "within"
    assert boundary_relation(None, 3, 5)["relation"] == "missing_observation"
    assert boundary_relation(3)["relation"] == "missing_reference"
    assert numeric.difference(0, 3)["relative_change_percent"] is None
    assert calculate("coverage", {"valid_count": 8, "denominator_factors": [0, 29]})["valid_ratio"] is None
    assert calculate("comparison", {"value": 40, "unit": None, "comparison_allowed": True})["relation"] == "conditions_pending"
    assert numeric.difference(143, 102414)["difference"] == 102271
    assert round(numeric.difference(143, 102414)["relative_change_percent"], 2) == 71518.18
    trial = {"unit": "次", "intervals": [{"start": {"date": "D1", "value": 2}, "end": {"date": "D2", "value": 3}, "version": "v1"}, {"start": {"date": "D2", "value": 3}, "end": {"date": "D3", "value": 2}, "version": "v1"}]}
    assert interval_relations(trial)["shared_dates"][0]["version_comparison"] == "consistent"
    del trial["intervals"][1]["version"]
    assert interval_relations(trial)["shared_dates"][0]["version_comparison"] == "conditions_pending"
    saved_inputs = json.loads((ROOT / "tools/numeric_explanation_check/inputs.json").read_text(encoding="utf-8"))["tasks"]
    for task in saved_inputs:
        old_facts = numeric.compute_facts(task["rows"])
        for row, fact in zip(task["rows"], old_facts):
            if row["kind"] == "bounds":
                current = boundary_relation(row["value"], row.get("lower"), row.get("upper"))
                assert [x["difference"] for x in current["comparisons"]] == [x["difference"] for x in fact["comparisons"]]
            if row["kind"] == "missing_fields":
                assert fact["declared"]["cnr_p10"] is None and fact["declared"]["raw_signal_strength"] == 40
    for page in output.glob("*.html"):
        parser = Links()
        parser.feed(page.read_text(encoding="utf-8"))
        for link in parser.links:
            assert "://" not in link, (page.name, "online dependency", link)
            path, _, anchor = link.partition("#")
            assert (output / path).exists() if path else anchor in parser.ids, (page.name, link)
        assert parser.details > 0
    for case in data["cases"]:
        for section in case["sections"]:
            assert section["source_claim_ids"]
            for key in section["source_claim_ids"]:
                assert key in data["sources"]
            for row in section["rows"]:
                assert row.get("source_claim_id") and row.get("source_field")
    return {"scope": "本次计算、既有八题输入、生成页面的本地链接和字段来源", "status": "passed", "model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/archive_observation_report")
    args = parser.parse_args()
    data = json.loads((HERE / "facts.json").read_text(encoding="utf-8"))
    result = deepcopy(data)
    prior = json.loads((ROOT / "reports/round9/ROUND9_NUMERIC_EXPLANATION_RESULTS.json").read_text(encoding="utf-8"))
    result["round9_context"] = {"source_file": "reports/round9/ROUND9_NUMERIC_EXPLANATION_RESULTS.json", "comparison_counts": prior["comparison_counts"], "new_model_calls": 0}
    result["generation"] = {"generated_at": datetime.now(timezone.utc).isoformat(), "attribution": "本次事实说明由程序按已存资料生成", "original_goal_status": "not_demonstrated", "strict_lead_hours": None, "runtime_modified": False, "new_model_calls": 0}
    for case_index, case in enumerate(result["cases"]):
        for section_index, section in enumerate(case["sections"]):
            for index, row in enumerate(section["rows"]):
                row["calculated"] = calculate(section["kind"], row)
                row["calculated"]["input_ref"] = f'/cases/{case_index}/sections/{section_index}/rows/{index}'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for case in result["cases"]:
        (output / (case["id"] + ".html")).write_text(case_page(case, result["cases"], result["sources"]), encoding="utf-8")
    (output / "index.html").write_text(index_page(result), encoding="utf-8")
    (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    coverage = '\n'.join(f'- {case["title"]}：{len(case["sections"])} 个事实分区；' + '、'.join(section["title"] for section in case["sections"]) + '。' for case in result["cases"])
    delivery = f'''# 独立历史观测事实报告包

两份静态报告与统一入口已实际生成。双击 [index.html](index.html) 可切换 [哈尔科夫档案](kharkiv.html) 与 [霍尔木兹档案](hormuz.html)，页面使用原生 HTML 折叠区，无需登录、服务或联网。所有来源摘录、原值、字段缺项和本次计算保存在 [results.json](results.json)。

构建命令（在仓库根目录）：

```powershell
& 'D:/anaconda3/python.exe' -B tools/archive_observation_report/build.py --output outputs/archive_observation_report
```

{coverage}

## 资料与计算

优先使用 Round7 已保存的结构化复核导出，选取其中代表窗口、日记录、元数据及原句定位；缺少直接数值字段的项目使用 Round8 报告和 Round9 输入中的明确摘录。每行分别保留来源及 origin；结构化摘录不等于重新读取完整原件或完整窗口序列。页面采用“已保存的代表窗口与汇总”，没有补造连续曲线。

参考边界、同字段差值和百分比、有效比例及相邻区间的共同日期值由本次程序计算。参考组窗口数、中位差、上下偏计数、采样及单位信息来自原资料声明。单位、窗口精确起止或来源版本缺失时具体列示；给定数字的大小关系仍按已知边界计算，不补另一侧边界。

原历史回答在折叠区域原样保留，注明中间／末答和已存勘误状态。K-C3 的中间错误已修正；H-C5 三条末答错误及容量解释缺口仍留在历史记录中。本包只是准确呈现所给事实，没有替模型生成“已修正回答”。

## 执行与核对

生成器已实际运行；本次核对限于边界关系、增幅、零分母、相邻日期与版本、单位空值、必需来源字段及静态本地链接。核对结果保存在 results.json 的 checks 中。实际浏览器查阅状态见随包交付记录；本地链接检查通过不等于已完成浏览器点击检查。

本次新增模型调用 0。原 Round6～Round9 报告、原答、检查预期、应用代码、采集器、Agent 循环、阈值、8080／8081、生产数据库及服务器环境均未改动。原 original_goal_status=not_demonstrated、strict_lead_hours=null 沿用原记录；本包交付不改变原业务验收。

## 剩余资料范围

两份档案均展示已有材料支持的部分。完整逐窗序列、原因依据及部分源版本没有全部随包提供；来源区给出原文件和字段的文字定位，没有生成到未导出文件的失效链接。GFZ 已提供与原回答未完成环境论证分开记录；NICO 缺少单位换算依据不影响另外两站已经确认的单位。
'''
    (output / "DELIVERY.md").write_text(delivery, encoding="utf-8")
    result["checks"] = local_checks(result, output)
    (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "pages": ["index.html"] + [c["id"] + ".html" for c in result["cases"]], "checks": result["checks"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
