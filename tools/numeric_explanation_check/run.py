"""Eight fixed tasks, two inputs, at most one HTTP request per task/condition."""

import argparse
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone
from time import perf_counter
from urllib import error, request

from facts import compute_facts

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SYSTEM = (
    "你负责说明输入表中的数字。用简短、自然的中文，先直接回答所问问题，再给必要数字。"
    "只使用给出的数据和字段定义。具体没有依据的推断列入unsupported_inferences，没有则为空数组。"
    "supporting_row_ids引用实际输入行的row_id。只输出一个JSON对象，不输出Markdown或推理过程。"
    '字段固定为：{"answer":"简短回答","supporting_row_ids":["输入行编号"],'
    '"unsupported_inferences":["具体未获支持的推断"]}。'
)
REVIEW_DIMENSIONS = ("numbers_and_relations", "answers_original_question", "units_windows_groups_missing",
                     "unsupported_inferences", "row_citations")


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def save(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def configuration(env_file, profile):
    values = {}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    if profile:
        text = Path(profile).read_text(encoding="utf-8")
        values["MODEL_BASE_URL"] = re.search(r"^- API: `([^`]+)`", text, re.M)[1]
        values["MODEL_NAME"] = re.search(r"^- Model name: `([^`]+)`", text, re.M)[1]
    for key in ("MODEL_BASE_URL", "MODEL_NAME", "MODEL_API_KEY", "MODEL_TIMEOUT",
                "MODEL_MAX_TOKENS", "DISABLE_THINKING"):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def structural_check(content, rows):
    try:
        answer = json.loads(content)
    except (ValueError, TypeError) as exc:
        return None, {"json_structure": "fail", "row_ids": "not_checked", "detail": str(exc)}
    keys = {"answer", "supporting_row_ids", "unsupported_inferences"}
    valid = (isinstance(answer, dict) and set(answer) == keys
             and isinstance(answer["answer"], str) and bool(answer["answer"].strip())
             and isinstance(answer["supporting_row_ids"], list)
             and all(isinstance(v, str) for v in answer["supporting_row_ids"])
             and isinstance(answer["unsupported_inferences"], list)
             and all(isinstance(v, str) for v in answer["unsupported_inferences"]))
    if not valid:
        return answer, {"json_structure": "fail", "row_ids": "not_checked", "detail": "字段或类型不符合三字段格式"}
    real_ids = {row["row_id"] for row in rows}
    bad_ids = sorted(set(answer["supporting_row_ids"]) - real_ids)
    return answer, {"json_structure": "pass", "row_ids": "fail" if bad_ids else "pass",
                    "unknown_row_ids": bad_ids, "semantic_review": "pending"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="实际执行固定的最多16次请求")
    parser.add_argument("--env-file", type=Path, default=ROOT / (".env" if (ROOT / ".env").is_file() else ".env.example"))
    parser.add_argument("--connection-profile", type=Path, help="已有Qwen连接技能文件；只读API和模型名")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/round9/ROUND9_NUMERIC_EXPLANATION_RESULTS.json")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("结果文件已存在；本轮不重复调用或覆盖已保存的回答。")
    tasks = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))["tasks"]
    config = configuration(args.env_file, args.connection_profile)
    parameters = {"model": config.get("MODEL_NAME", "qwen3-4b"), "temperature": 0,
                  "max_tokens": int(config.get("MODEL_MAX_TOKENS", "1800")),
                  "response_format": {"type": "json_object"}}
    if config.get("DISABLE_THINKING", "true").lower() == "true":
        parameters["chat_template_kwargs"] = {"enable_thinking": False}
    result = {
        "created_at": now(), "experiment": "round9_fixed_numeric_explanation",
        "model_execution": "not_executed", "requests_attempted": 0, "responses_received": 0,
        "connection_source": {"env_file": str(args.env_file), "profile": str(args.connection_profile) if args.connection_profile else None,
                              "endpoint": config.get("MODEL_BASE_URL"), "environment_overrides": [k for k in config if k in os.environ and k != "MODEL_API_KEY"]},
        "parameters": parameters, "system_prompt": SYSTEM, "timeout_seconds": float(config.get("MODEL_TIMEOUT", "120")),
        "request_contract_source": "app/llm.py:_complete (read only; not imported)",
        "conditions": {"A": "原始表与问题", "B": "相同表与问题，加程序事实"},
        "scope": "8项匿名简化任务；不是历史完整上下文复现", "runs": [],
    }
    for task in tasks:
        facts = compute_facts(task["rows"])
        for condition in ("A", "B"):
            model_input = {"question": task["question"], "rows": task["rows"]}
            if condition == "B":
                model_input["program_facts"] = facts
            result["runs"].append({
                "task_id": task["id"], "condition": condition, "actual_input": model_input,
                "program_facts": facts, "program_facts_sent": condition == "B", "status": "not_executed",
                "model_output": None, "response_text": None, "raw_response_body": None, "mechanical_check": None,
                "review": {"status": "not_checked", "dimensions": {name: "not_checked" for name in REVIEW_DIMENSIONS},
                           "note": "尚无模型回答可供检查", "specific_quotes": []},
            })
    if len(result["runs"]) != 16:
        parser.error("本轮固定八题、两个条件。")
    save(args.output, result)
    if not args.execute or not config.get("MODEL_BASE_URL"):
        result["not_executed_reason"] = "未请求执行" if not args.execute else "未配置已有接口"
        save(args.output, result)
        return
    endpoint = config["MODEL_BASE_URL"].rstrip("/") + "/chat/completions"
    secret = config.get("MODEL_API_KEY", "")
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + (secret or "EMPTY")}
    for run in result["runs"]:
        payload = {**parameters, "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(run["actual_input"], ensure_ascii=False, separators=(",", ":"))},
        ]}
        run.update(status="request_started", started_at=now())
        result["requests_attempted"] += 1
        save(args.output, result)
        started = perf_counter()
        unavailable = False
        try:
            req = request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers)
            with request.urlopen(req, timeout=result["timeout_seconds"]) as response:
                body = response.read().decode("utf-8")
                run["http_status"] = response.status
            run["raw_response_body"] = body
            result["responses_received"] += 1
            envelope = json.loads(body)
            choice = envelope["choices"][0]
            content = choice["message"]["content"]
            run.update(response_text=content, response_model=envelope.get("model"),
                       usage=envelope.get("usage"), finish_reason=choice.get("finish_reason"))
            parsed, check = structural_check(content, run["actual_input"]["rows"])
            run.update(model_output=parsed, mechanical_check=check,
                       status="response_received" if check["json_structure"] == "pass" and choice.get("finish_reason") != "length" else "format_error")
            run["review"].update(status="review_needed", note="需逐项阅读原答；结构检查不判断自然语言是否正确")
        except error.HTTPError as exc:
            run.update(status="http_error", http_status=exc.code,
                       raw_response_body=exc.read().decode("utf-8", errors="replace"))
            unavailable = exc.code in (401, 403, 404, 429, 502, 503, 504)
        except (error.URLError, TimeoutError, OSError) as exc:
            run.update(status="connection_failed", error=str(exc).replace(secret, "[redacted]") if secret else str(exc))
            unavailable = True
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            run.update(status="format_error", error=str(exc))
        run.update(finished_at=now(), elapsed_seconds=round(perf_counter() - started, 3))
        result["model_execution"] = "executed" if result["responses_received"] else "not_executed"
        save(args.output, result)
        print(f'{run["task_id"]}/{run["condition"]}: {run["status"]}', flush=True)
        if unavailable:
            result["not_executed_reason"] = "接口不可用，保留本次失败；剩余项未执行，不重试。"
            break
    result["finished_at"] = now()
    result["model_execution"] = ("executed" if result["responses_received"] == 16 else
                                 "partially_executed" if result["responses_received"] else "not_executed")
    save(args.output, result)


if __name__ == "__main__":
    main()
