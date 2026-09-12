"""Read the existing case artifacts and archived public-news titles."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "cases" / "iran-hormuz-2026-02-28"
REPLAY_PATH = CASE / "data" / "news_replay.json"
REPLAY_LABEL = "按 GDELT 收录时间顺序回放的历史新闻标题"


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def replay_info():
    available = REPLAY_PATH.is_file()
    return {
        "available": available,
        "count": len(_read_json(REPLAY_PATH)["articles"]) if available else 0,
        "label": REPLAY_LABEL,
    }


def replay_materials():
    fetched_at = datetime.now(timezone.utc).isoformat()
    return [
        {
            "title": article["title"],
            "url": article["url"],
            "publisher": article["domain"],
            "text": "",
            "content_kind": "title_only",
            "observed_at": datetime.strptime(
                article["seendate"], "%Y%m%dT%H%M%SZ"
            ).replace(tzinfo=timezone.utc).isoformat(),
            "available_at": None,
            "fetched_at": fetched_at,
            "source": "GDELT 历史新闻标题",
            "mode": "replay",
            "time_note": "observed_at 是 GDELT 收录时间，原文发布时间未确认",
        }
        for article in _read_json(REPLAY_PATH)["articles"]
    ]


def history_payload():
    assets = []
    for filename in ("sentinel_selected_products.csv", "sentinel_analysis_assets.csv"):
        with (CASE / "data" / filename).open(encoding="utf-8-sig", newline="") as handle:
            assets.append({"filename": filename, "rows": list(csv.DictReader(handle))})
    return {
        "freight": _read_json(CASE / "results" / "freight_case_report.json"),
        "eo": _read_json(CASE / "results" / "eo_candidate_summary.json"),
        "sources": _read_json(CASE / "data" / "source_manifest.json"),
        "assets": assets,
        "replay": replay_info(),
    }
