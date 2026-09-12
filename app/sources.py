"""Collect public news from GDELT or the explicitly selected UN News feed."""

from __future__ import annotations

import asyncio
import html
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

import httpx


SOURCES = [
    {"id": "gdelt", "name": "GDELT 新闻"},
    {"id": "rss", "name": "UN News RSS"},
    {"id": "portwatch", "name": "IMF PortWatch 日度聚合"},
]
GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"


def _stamp(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


def _news_time(value: str) -> str | None:
    if not value:
        return None
    try:
        if len(value) == 16 and "T" in value and value.endswith("Z"):
            return _stamp(datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc))
        parsed = parsedate_to_datetime(value) if "," in value else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return _stamp(parsed.astimezone(timezone.utc))
    except (ValueError, TypeError):
        return None


class _ArticleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self.description = ""
        self._paragraph: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "p":
            self._paragraph = []
        if tag == "meta":
            values = dict(attrs)
            name = (values.get("name") or values.get("property") or "").lower()
            if name in ("description", "og:description") and not self.description:
                self.description = values.get("content") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag == "p" and self._paragraph is not None:
            text = " ".join("".join(self._paragraph).split())
            if text:
                self.paragraphs.append(text)
            self._paragraph = None

    def handle_data(self, data: str) -> None:
        if self._paragraph is not None and not self._skip:
            self._paragraph.append(data)


class _PlainHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain(value: str) -> str:
    parser = _PlainHTML()
    parser.feed(html.unescape(value))
    return " ".join(" ".join(parser.parts).split())


async def _read(client: httpx.AsyncClient, url: str, config: Any, **kwargs: Any) -> tuple[str, str]:
    async with client.stream("GET", url, **kwargs) as response:
        response.raise_for_status()
        chunks = bytearray()
        async for chunk in response.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) > config.request_max_bytes:
                raise ValueError(f"来源响应超过 {config.request_max_bytes} 字节")
        return chunks.decode(response.encoding or "utf-8", errors="replace"), response.headers.get("content-type", "")


async def _article(client: httpx.AsyncClient, material: dict[str, Any], config: Any) -> dict[str, Any]:
    try:
        body, content_type = await _read(client, material["url"], config)
        if content_type and "html" not in content_type.lower():
            raise ValueError("文章链接未返回 HTML 正文")
        parser = _ArticleHTML()
        parser.feed(body)
        text = "\n\n".join(parser.paragraphs)
        kind = "article_text"
        if not text and parser.description.strip():
            text = " ".join(parser.description.split())
            kind = "snippet"
        if not text:
            raise ValueError("页面未提供可提取的段落或摘要")
        material.update(
            text=text[: config.article_chars],
            content_kind=kind,
            original_text_chars=len(text),
            text_truncated=len(text) > config.article_chars,
        )
    except (httpx.HTTPError, ValueError) as exc:
        material["extraction_error"] = str(exc)
    return material


def _material(title: str, url: str, publisher: str, date: str | None, source: str, fetched_at: str, text: str = "") -> dict[str, Any]:
    return {
        "title": title,
        "url": url,
        "publisher": publisher,
        "text": text,
        "content_kind": "snippet" if text else "title_only",
        "observed_at": date,
        "available_at": date if source == "rss" else None,
        "time_note": (
            "RSS pubDate 为来源标注的发布时间" if source == "rss" and date
            else "GDELT seendate 为收录时间，来源发布时间未提供" if source == "gdelt"
            else "来源未提供发布时间"
        ),
        "fetched_at": fetched_at,
        "source": source,
        "mode": "online",
    }


async def collect(monitor: dict[str, Any], config: Any) -> dict[str, Any]:
    """Run one selected-source collection; never switch sources implicitly."""
    source = monitor["source"]
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=monitor["lookback_hours"])
    fetched_at = _stamp(now)
    materials: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=config.source_timeout, follow_redirects=True, headers={"User-Agent": "PublicNewsDemo/0.1"}) as client:
            if source == "gdelt":
                payload, _ = await _read(client, GDELT_ENDPOINT, config, params={
                    "query": monitor["topic"],
                    "mode": "ArtList",
                    "format": "json",
                    "maxrecords": monitor["max_materials"],
                    "sort": "HybridRel",
                    "startdatetime": start.strftime("%Y%m%d%H%M%S"),
                    "enddatetime": now.strftime("%Y%m%d%H%M%S"),
                })
                try:
                    articles = json.loads(payload)["articles"]
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError("GDELT 未返回新闻列表：" + payload[:240]) from exc
                for article in articles[: monitor["max_materials"]]:
                    materials.append(_material(
                        article["title"], article["url"], article.get("domain", "GDELT"),
                        _news_time(article.get("seendate", "")), source, fetched_at,
                    ))
            elif source == "rss":
                payload, _ = await _read(client, config.rss_url, config)
                feed = ET.fromstring(payload)
                if feed.find("channel") is None:
                    raise ValueError("RSS 响应缺少 channel，未返回新闻频道")
                terms = monitor["topic"].casefold().split()
                for item in feed.findall("./channel/item"):
                    title = _plain(item.findtext("title", ""))
                    description = _plain(item.findtext("description", ""))
                    if not all(term in (title + " " + description).casefold() for term in terms):
                        continue
                    date = _news_time(item.findtext("pubDate", ""))
                    if date and datetime.fromisoformat(date.replace("Z", "+00:00")) < start:
                        continue
                    url = item.findtext("link", "").strip()
                    if title and url:
                        material = _material(title, url, "UN News", date, source, fetched_at, description[: config.article_chars])
                        material.update(original_text_chars=len(description), text_truncated=len(description) > config.article_chars)
                        materials.append(material)
                    if len(materials) == monitor["max_materials"]:
                        break
            elif source == "portwatch":
                query_url = config.portwatch_url.rstrip("/") + "/query"
                params = {
                    "where": f"portid='{config.portwatch_id}' AND date >= DATE '{start:%Y-%m-%d}' AND date <= DATE '{now:%Y-%m-%d}'",
                    "outFields": "*",
                    "returnGeometry": "false",
                    "orderByFields": "date DESC",
                    "resultRecordCount": monitor["max_materials"],
                    "f": "json",
                }
                payload, _ = await _read(client, query_url, config, params=params)
                product = json.loads(payload)
                if "error" in product:
                    raise ValueError("PortWatch 返回错误：" + json.dumps(product["error"], ensure_ascii=False))
                for feature in product["features"][: monitor["max_materials"]]:
                    attributes = feature["attributes"]
                    raw_date = attributes["date"]
                    date = _stamp(datetime.fromtimestamp(raw_date / 1000, timezone.utc)) if isinstance(raw_date, (int, float)) else _news_time(raw_date)
                    if date is None:
                        raise ValueError("PortWatch 日度记录缺少可解析的观测日期")
                    day = date[:10]
                    row_params = {
                        "where": f"portid='{config.portwatch_id}' AND date = DATE '{day}'",
                        "outFields": "*", "returnGeometry": "false", "f": "json",
                    }
                    units = (
                        "n_total、n_tanker、n_cargo 为 PortWatch 产品口径的每日 AIS 可见通行船舶或航次计数。"
                        "capacity 及各船型 capacity 字段为源产品的名义运力，单位保留源产品口径，非实际载货吨数。"
                        "date 为日度观测参考时间，不是发布时间。"
                    )
                    text = json.dumps({"attributes": attributes, "units": units}, ensure_ascii=False)
                    material = _material(
                        f"IMF PortWatch {day} · {config.portwatch_id} · 总船次 {attributes.get('n_total', '未提供')}",
                        query_url + "?" + urlencode(row_params), "IMF PortWatch", date, source, fetched_at, text,
                    )
                    material.update(
                        content_kind="aggregate", available_at=None,
                        time_note="observed_at 为产品日度观测参考日期，来源未提供该记录的发布时间",
                        original_text_chars=len(text), text_truncated=False,
                    )
                    materials.append(material)
            else:
                raise ValueError(f"未提供此来源：{source}")
            if source != "portwatch":
                materials = list(await asyncio.gather(*(_article(client, item, config) for item in materials)))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        return {"materials": [], "status": "source_failed", "error": str(exc), "detail": "所选来源采集失败"}
    if not materials:
        return {"materials": [], "status": "empty", "error": None, "detail": "所选来源在该时间范围未返回匹配资料"}
    if source == "portwatch":
        return {"materials": materials, "status": "ok", "error": None, "detail": f"采集 {len(materials)} 条 IMF PortWatch 日度聚合记录"}
    body_count = sum(item["content_kind"] == "article_text" for item in materials)
    return {
        "materials": materials,
        "status": "ok",
        "error": None,
        "detail": f"采集 {len(materials)} 条资料，其中 {body_count} 条已提取文章段落；其余使用来源摘要或标题",
    }
