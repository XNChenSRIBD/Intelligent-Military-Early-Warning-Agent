"""Read configured public sources, with retrieval separate from article reading."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import httpx


SOURCES = [
    {"id": "gdelt", "name": "GDELT 新闻"},
    {"id": "rss", "name": "已配置 RSS（以实际 feed 标题为准）"},
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


def _retry_after(exc: Exception) -> float | None:
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    value = exc.response.headers.get('Retry-After', '').strip()
    if not value:
        return None
    try:
        seconds = float(value)
        return seconds if 0 <= seconds < float('inf') else None
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
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
        article_url = material['url']
        for _ in range(6):
            if not _public_article_url(article_url):
                raise ValueError('文章原链接或跳转目标不是公开 HTTP 链接')
            try:
                body, content_type = await _read(client, article_url, config, follow_redirects=False)
                break
            except httpx.HTTPStatusError as exc:
                location = exc.response.headers.get('location')
                if not exc.response.is_redirect or not location:
                    raise
                article_url = urljoin(article_url, location)
        else:
            raise ValueError('文章链接跳转次数超过限制')
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
            extraction_status="ok", extraction_error=None, resolved_url=article_url,
            retry_after_seconds=None,
        )
    except (httpx.HTTPError, ValueError) as exc:
        material["extraction_error"] = str(exc)
        material["extraction_status"] = "failed"
        material["retry_after_seconds"] = _retry_after(exc)
    material['article_read_at'] = _stamp()
    material['text_quality'] = material.get('content_kind', 'title_only')
    return material


def _public_article_url(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or '').lower()
    if parsed.scheme not in ('http', 'https') or not hostname or hostname in ('localhost', 'localhost.localdomain') or hostname.endswith('.local'):
        return False
    try:
        return ipaddress.ip_address(hostname).is_global
    except ValueError:
        return '.' in hostname


async def read_material(material: dict[str, Any], config: Any) -> dict[str, Any]:
    """Read only the original URL of a supplied, source-retrieved news material."""
    result = dict(material)
    if material.get('source') not in ('gdelt', 'rss') or not _public_article_url(material.get('url', '')):
        result.update(extraction_status='failed', extraction_error='仅可补读已检索新闻材料的公开原始链接')
    else:
        async with httpx.AsyncClient(timeout=config.source_timeout, follow_redirects=False,
                                     headers={'User-Agent': 'PublicNewsDemo/0.1'}) as client:
            result = await _article(client, result, config)
    failed = result.get('extraction_status') == 'failed'
    return {
        'material': result, 'materials': [result],
        'status': 'source_failed' if failed else 'ok',
        'error': result.get('extraction_error'),
        'detail': '正文读取失败，保留已有标题或摘要' if failed else '已读取来源文章文本',
        'retry_after_seconds': result.get('retry_after_seconds'),
    }


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
        "source": source, "first_seen_at": fetched_at,
        "mode": "online",
    }


async def search_news(source: str, topic: str, start: datetime, end: datetime, config: Any,
                      *, terms: list[str] | None = None, max_windows: int = 8,
                      limit: int | None = None,
                      windows: list[dict[str, str]] | None = None,
                      gaps: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Retrieve candidate titles/snippets; the pipeline analysis batch does not cap collection."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError('新闻时间窗口必须包含时区')
    start = start.astimezone(timezone.utc).replace(microsecond=0)
    end = end.astimezone(timezone.utc).replace(microsecond=0)
    if end < start or max_windows < 1 or (limit is not None and limit < 1):
        raise ValueError('新闻时间窗口或获取预算无效')
    coverage: dict[str, Any] = {
        'requested_start': _stamp(start), 'requested_end': _stamp(end),
        'complete': False, 'covered_until': None, 'scanned_until': None, 'truncated': False,
        'uncovered_windows': [], 'gaps': [dict(gap) for gap in (gaps or [])], 'requests': 0,
    }
    materials: dict[str, dict[str, Any]] = {}
    error = None
    retry_after = None
    if source == 'rss' and not config.rss_url:
        return {'materials': [], 'status': 'unavailable', 'error': '未配置 RSS 来源地址',
                'detail': '该 RSS 来源不可用，其他已配置来源可继续运行',
                'coverage': coverage, 'retry_after_seconds': None}
    try:
        async with httpx.AsyncClient(timeout=config.source_timeout, follow_redirects=True,
                                     headers={'User-Agent': 'PublicNewsDemo/0.1'}) as client:
            if source == 'gdelt':
                if windows is None:
                    pending = [(start, end)]
                else:
                    pending = []
                    for window in windows:
                        left = datetime.fromisoformat(window['start'].replace('Z', '+00:00'))
                        right = datetime.fromisoformat(window['end'].replace('Z', '+00:00'))
                        if left.tzinfo is None or right.tzinfo is None or not start <= left <= right <= end:
                            raise ValueError('续接分窗必须位于原批次起止时间内且包含时区')
                        pending.append((left.astimezone(timezone.utc), right.astimezone(timezone.utc)))
                    pending = sorted(set(pending))
                while pending and coverage['requests'] < max_windows:
                    window_start, window_end = pending.pop(0)
                    coverage['requests'] += 1
                    try:
                        payload, _ = await _read(client, GDELT_ENDPOINT, config, params={
                            'query': topic, 'mode': 'ArtList', 'format': 'json',
                            'maxrecords': 250, 'sort': 'DateAsc',
                            'startdatetime': window_start.strftime('%Y%m%d%H%M%S'),
                            'enddatetime': window_end.strftime('%Y%m%d%H%M%S'),
                        })
                        articles = json.loads(payload)['articles']
                        if not isinstance(articles, list):
                            raise ValueError('GDELT 未返回新闻列表')
                    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                        pending.insert(0, (window_start, window_end))
                        error, retry_after = str(exc), _retry_after(exc)
                        break
                    fetched_at = _stamp()
                    clipped = False
                    for article in articles:
                        url = article.get('url', '').strip()
                        title = article.get('title', '').strip()
                        if not title or not url:
                            continue
                        if url not in materials and limit is not None and len(materials) >= limit:
                            clipped = True
                            continue
                        snippet = _plain(article.get('snippet') or article.get('description') or '')
                        material = _material(title, url, article.get('domain') or 'GDELT',
                                             _news_time(article.get('seendate', '')), source,
                                             fetched_at, snippet[:config.article_chars])
                        material.update(source_url=GDELT_ENDPOINT, source_name='GDELT 新闻',
                                        original_text_chars=len(snippet),
                                        text_truncated=len(snippet) > config.article_chars,
                                        text_quality=material['content_kind'])
                        materials.setdefault(url, material)
                    if clipped:
                        pending.insert(0, (window_start, window_end))
                        break
                    if len(articles) >= 250:
                        seconds = int((window_end - window_start).total_seconds())
                        if seconds <= 1:
                            coverage['gaps'].append({
                                'start': _stamp(window_start), 'end': _stamp(window_end),
                                'reason': '同一最小时间窗达到来源 250 条上限',
                            })
                        else:
                            midpoint = window_start + timedelta(seconds=seconds // 2)
                            pending[0:0] = [(window_start, midpoint), (midpoint, window_end)]
                    if limit is not None and len(materials) >= limit and pending:
                        break
                coverage['uncovered_windows'].extend({
                    'start': _stamp(left), 'end': _stamp(right),
                    'reason': '来源读取失败' if error else '本轮获取预算用尽，等待后续续接',
                } for left, right in pending)
                # Retryable windows and irreducible source-cap gaps remain distinct.
                # Scanning can cross a recorded gap; complete coverage cannot.
                remaining_starts = [datetime.fromisoformat(window['start'].replace('Z', '+00:00'))
                                    for window in coverage['uncovered_windows']]
                gap_starts = [datetime.fromisoformat(gap['start'].replace('Z', '+00:00'))
                              for gap in coverage['gaps']]
                cursor = min(remaining_starts + gap_starts, default=end)
                scanned = min(remaining_starts, default=end)
                coverage.update(
                    covered_until=_stamp(cursor) if cursor > start else None,
                    scanned_until=_stamp(scanned) if scanned > start else None,
                    complete=not coverage['uncovered_windows'] and not coverage['gaps'] and error is None,
                    truncated=bool(coverage['uncovered_windows'] or coverage['gaps']),
                    history_coverage='按 GDELT 收录时间排序及分窗获取', source_limit=250,
                )
                if coverage['complete']:
                    coverage['covered_until'] = _stamp(end)
            elif source == 'rss':
                if not config.rss_url:
                    raise ValueError('未配置 RSS 来源地址')
                coverage['requests'] = 1
                payload, _ = await _read(client, config.rss_url, config)
                feed = ET.fromstring(payload)
                channel = feed.find('channel')
                if channel is None:
                    raise ValueError('RSS 响应缺少 channel，未返回新闻频道')
                feed_title = _plain(channel.findtext('title', '')) or config.rss_url
                configured_terms = [str(term).strip().casefold() for term in
                                    (terms if terms is not None else topic.split()) if str(term).strip()]
                available_dates = []
                undated_count = 0
                matched_count = 0
                fetched_at = _stamp()
                for item in channel.findall('item'):
                    observed_at = _news_time(item.findtext('pubDate', ''))
                    if observed_at:
                        observed = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
                        available_dates.append(observed_at)
                        if not start <= observed <= end:
                            continue
                    else:
                        undated_count += 1
                    title = _plain(item.findtext('title', ''))
                    snippet = _plain(item.findtext('description', ''))
                    haystack = (title + ' ' + snippet).casefold()
                    if configured_terms and not any(term in haystack for term in configured_terms):
                        continue
                    url = item.findtext('link', '').strip()
                    if not title or not url or url in materials:
                        continue
                    matched_count += 1
                    if limit is not None and len(materials) >= limit:
                        continue
                    material = _material(title, url, feed_title, observed_at, source,
                                         fetched_at, snippet[:config.article_chars])
                    material.update(source_url=config.rss_url, source_name=feed_title,
                                    feed_title=feed_title, original_text_chars=len(snippet),
                                    text_truncated=len(snippet) > config.article_chars,
                                    text_quality=material['content_kind'])
                    materials[url] = material
                coverage.update(
                    feed_snapshot_complete=matched_count <= len(materials),
                    truncated=matched_count > len(materials), feed_title=feed_title,
                    feed_url=config.rss_url, history_coverage='unknown',
                    available_start=min(available_dates, default=None),
                    available_end=max(available_dates, default=None), undated_count=undated_count,
                    limitation='仅覆盖当前 RSS feed 实际列出的条目；无法确认历史窗口完整性，未标注日期的条目保留未知时间',
                )
            else:
                raise ValueError(f'未配置新闻来源：{source}')
    except (httpx.HTTPError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        error, retry_after = str(exc), _retry_after(exc)
    result_materials = sorted(materials.values(), key=lambda item: (item.get('observed_at') or '', item['url']))
    status = 'source_failed' if error and not result_materials else 'partial' if error or coverage['truncated'] else 'ok' if result_materials else 'empty'
    detail = f'检索 {len(result_materials)} 条新闻标题或摘要'
    if source == 'rss':
        detail += '；仅代表当前配置 feed 的实际内容'
    if coverage['truncated']:
        detail += '；时间窗口尚未完整覆盖'
    return {'materials': result_materials, 'status': status, 'error': error, 'detail': detail,
            'coverage': coverage, 'retry_after_seconds': retry_after}


async def collect_portwatch_series(config: Any) -> dict[str, Any]:
    """Read the complete configured calendar window before returning any rows."""
    fetched = datetime.now(timezone.utc)
    checked_at = _stamp(fetched)
    end = fetched.date()
    start = end - timedelta(days=config.portwatch_history_days - 1)
    result = {
        "checked_at": checked_at, "range_start": start.isoformat(),
        "range_end": end.isoformat(), "rows": [], "error": None,
    }
    query_url = config.portwatch_url.rstrip("/") + "/query"
    portid = config.portwatch_id.replace("'", "''")
    params = {
        "where": f"portid='{portid}' AND date >= DATE '{start}' AND date <= DATE '{end}'",
        "outFields": "*", "returnGeometry": "false", "orderByFields": "date ASC",
        "resultRecordCount": config.portwatch_history_days, "resultOffset": 0, "f": "json",
    }
    rows = {}
    try:
        async with httpx.AsyncClient(timeout=config.source_timeout, follow_redirects=True,
                                     headers={"User-Agent": "PublicNewsDemo/0.1"}) as client:
            while True:
                payload, _ = await _read(client, query_url, config, params=params)
                received_at = _stamp()
                product = json.loads(payload)
                if not isinstance(product, dict):
                    raise ValueError("PortWatch 未返回日度记录对象")
                if "error" in product:
                    raise ValueError("PortWatch 返回错误：" + json.dumps(product["error"], ensure_ascii=False))
                features = product["features"]
                if not isinstance(features, list):
                    raise ValueError("PortWatch 未返回日度记录列表")
                for feature in features:
                    attributes = feature["attributes"]
                    raw_date = attributes["date"]
                    if isinstance(raw_date, (int, float)) and not isinstance(raw_date, bool):
                        day = datetime.fromtimestamp(raw_date / 1000, timezone.utc).date()
                    elif isinstance(raw_date, str):
                        day = date.fromisoformat(raw_date[:10])
                    else:
                        raise ValueError("PortWatch 日度记录缺少可解析的观测日期")
                    if not start <= day <= end:
                        raise ValueError("PortWatch 返回了请求日期范围外的记录")
                    day_text = day.isoformat()
                    if day_text in rows:
                        raise ValueError("PortWatch 返回重复日期，无法确定完整的当前日序列")
                    row_params = {
                        "where": f"portid='{portid}' AND date = DATE '{day_text}'",
                        "outFields": "*", "returnGeometry": "false", "f": "json",
                    }
                    text = json.dumps(attributes, ensure_ascii=False, sort_keys=True)
                    material = _material(
                        f"IMF PortWatch {day_text} · {config.portwatch_id} · 总船次 {attributes.get('n_total', '未提供')}",
                        query_url + "?" + urlencode(row_params), "IMF PortWatch", day_text,
                        "portwatch", received_at, text,
                    )
                    material.update(
                        attributes=attributes, content_kind="aggregate", available_at=None,
                        time_note="observed_at 为产品日度观测参考日期，来源未提供该记录的发布时间",
                        original_text_chars=len(text), text_truncated=False, analysis_status="not_required",
                    )
                    rows[day_text] = {"observed_date": day_text, "attributes": attributes, "material": material}
                if not product.get("exceededTransferLimit", False):
                    break
                if not features:
                    raise ValueError("PortWatch 标明仍有下一页但未返回记录，日序列获取不完整")
                params["resultOffset"] += len(features)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError, OSError) as exc:
        return dict(result, checked_at=_stamp(), status="source_failed", error=str(exc),
                    retry_after_seconds=_retry_after(exc), detail="PortWatch 日序列获取失败，本次未提交部分数据")
    ordered = [rows[day] for day in sorted(rows)]
    return dict(result, checked_at=_stamp(), rows=ordered, status="ok" if ordered else "empty", retry_after_seconds=None,
                detail=f"获取 {len(ordered)} 个 PortWatch 观测日" if ordered else "PortWatch 在该日期范围未返回观测")


async def collect(monitor: dict[str, Any], config: Any) -> dict[str, Any]:
    """Run one selected-source collection; never switch sources implicitly."""
    source = monitor["source"]
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=monitor["lookback_hours"])
    fetched_at = _stamp(now)
    if source in ('gdelt', 'rss'):
        result = await search_news(source, monitor['topic'], start, now, config,
                                   limit=monitor['max_materials'])
        if result['materials']:
            reads = await asyncio.gather(*(read_material(item, config) for item in result['materials']))
            result['materials'] = [read['material'] for read in reads]
            body_count = sum(item['content_kind'] == 'article_text' for item in result['materials'])
            result['detail'] += f'，其中 {body_count} 条已提取文章段落'
        return result
    materials: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=config.source_timeout, follow_redirects=True, headers={"User-Agent": "PublicNewsDemo/0.1"}) as client:
            if source == "portwatch":
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
    except (httpx.HTTPError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        return {"materials": [], "status": "source_failed", "error": str(exc),
                "retry_after_seconds": _retry_after(exc), "detail": "所选来源采集失败"}
    if not materials:
        return {"materials": [], "status": "empty", "error": None, "detail": "所选来源在该时间范围未返回匹配资料"}
    return {"materials": materials, "status": "ok", "error": None,
            "retry_after_seconds": None, "detail": f"采集 {len(materials)} 条 IMF PortWatch 日度聚合记录"}
