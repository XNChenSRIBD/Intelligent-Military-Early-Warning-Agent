"""Persistent, data-driven collection and one serial model consumer."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .llm import ModelError, assess_update
from .monitoring import evaluate
from .sources import collect_portwatch_series, read_material, search_news
from .store import now


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def after(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def material_input(material):
    return {key: material.get(key) for key in (
        'id', 'version', 'title', 'text', 'content_kind', 'url', 'source',
        'observed_at', 'available_at', 'first_seen_at', 'time_note', 'extraction_error')}


def material_ref(material):
    return {key: material.get(key) for key in (
        'id', 'version', 'title', 'url', 'source', 'observed_at', 'available_at', 'first_seen_at')}


def alert_summary(alert):
    fields = ('id', 'origin_type', 'scope_id', 'monitor_id', 'title', 'status',
              'display_status', 'analysis_status', 'published_at', 'updated_at', 'detected_at',
              'read_at', 'observed_at', 'available_at', 'evidence_version', 'summary', 'origin',
              'analysis_stale', 'started_on', 'resolved_on', 'last_evaluated_date', 'queued_work_id')
    result = {key: alert.get(key) for key in fields}
    if alert.get('analysis'):
        result['analysis'] = {key: alert['analysis'].get(key) for key in (
            'statement', 'limitations', 'completed_at', 'model', 'work_id')}
    return result


class Pipeline:
    def __init__(self, store, config, runner):
        self.store, self.config, self.runner = store, config, runner
        self.wakeup = asyncio.Event()
        self.collectors = {}
        self.tasks = []
        self.stopping = False
        saved = store.get('pipeline', 'main')
        if saved is None:
            scope_id = 'civil-shipping-news-1'
            sources = []
            for source, name in [('gdelt', 'GDELT 新闻'), ('rss', '已配置 RSS'),
                                 ('portwatch', 'IMF PortWatch · 霍尔木兹海峡')]:
                sources.append({
                    'id': source, 'source': source, 'name': name,
                    'scope_id': scope_id if source != 'portwatch' else 'civil-hormuz',
                    'monitor_id': 'pipeline-' + source,
                    'enabled': source in config.pipeline_sources,
                    'interval_seconds': config.pipeline_portwatch_interval if source == 'portwatch'
                    else config.pipeline_news_interval,
                })
            saved = {'id': 'main', 'paused': False, 'news_topic': config.pipeline_news_topic,
                     'rss_terms': list(config.pipeline_rss_terms), 'sources': sources,
                     'created_at': now(), 'updated_at': now()}
            store.insert_once('pipeline', saved)
        for source in saved['sources']:
            self.ensure_monitor(source, saved)
        # No browser action is needed to resume interrupted inputs or source checks.
        for work in store.all('work'):
            if work['status'] == 'running':
                work.update(status='queued', next_retry_at=now(), error='应用重启后自动续接')
                store.save('work', work)
        for source in store.all('pipeline_source'):
            if source.get('status') == 'collecting':
                source.update(status='queued', next_check_at=now(), error='应用重启后自动续接')
                store.save('pipeline_source', source)

    def ensure_monitor(self, source, settings):
        monitor = self.store.get('monitor', source['monitor_id']) or {
            'id': source['monitor_id'], 'mode': 'online', 'enabled': False,
            'pipeline_owned': True, 'created_at': now(), 'last_success_at': None,
            'last_error': None, 'max_materials': self.config.pipeline_batch_size,
            'lookback_hours': self.config.pipeline_lookback_hours,
        }
        monitor.update(source=source['source'], scope_id=source['scope_id'],
                       topic='霍尔木兹海峡可见通行量' if source['source'] == 'portwatch' else settings['news_topic'],
                       interval_minutes=source['interval_seconds'] / 60)
        self.store.save('monitor', monitor)

    def running(self):
        return self.config.pipeline_enabled and not self.store.get('pipeline', 'main')['paused']

    def start(self):
        self.tasks = [asyncio.create_task(self.schedule()), asyncio.create_task(self.consume())]

    def configure(self, changes):
        current = self.store.get('pipeline', 'main')
        news_changed = any(key in changes and changes[key] != current[key]
                           for key in ('news_topic', 'rss_terms'))
        current.update({key: value for key, value in changes.items()
                        if key in ('paused', 'news_topic', 'rss_terms')})
        if news_changed:
            revision = uuid4().hex[:12]
            for source in current['sources']:
                if source['source'] != 'portwatch':
                    source.update(scope_id='civil-shipping-news-' + revision,
                                  monitor_id='pipeline-' + source['source'] + '-' + revision)
        for change in changes.get('sources', []):
            source = next((item for item in current['sources'] if item['id'] == change['id']), None)
            if source is None:
                raise ValueError('配置只能选择现有 GDELT、RSS 和 PortWatch 来源')
            source.update({key: value for key, value in change.items()
                           if key in ('enabled', 'interval_seconds')})
        current['updated_at'] = now()
        with self.store.atomic():
            self.store.save('pipeline', current)
            for source in current['sources']:
                self.ensure_monitor(source, current)
        self.wakeup.set()
        return self.snapshot()

    def source_state(self, source):
        return self.store.get('pipeline_source', source['monitor_id']) or {
            'id': source['monitor_id'], 'source': source['source'], 'scope_id': source['scope_id'],
            'status': 'queued', 'attempts': 0, 'next_check_at': now(),
        }

    def recent_alerts(self, limit=50):
        alerts = self.store.recent('alert', max(limit * 3, 100))
        items = [alert for alert in alerts if alert.get('origin_type')]
        return sorted(items, key=lambda item: item.get('updated_at') or item['detected_at'], reverse=True)[:limit]

    def snapshot(self):
        settings = self.store.get('pipeline', 'main')
        sources = []
        for source in settings['sources']:
            progress = self.source_state(source)
            sources.append(dict(source, **{key: value for key, value in progress.items()
                                         if key not in ('id', 'scope_id', 'source', 'input_versions')}))
        counts = self.store.work_counts()
        backlog = sum(counts.get(key, 0) for key in ('queued', 'running', 'retry_wait'))
        fault = any(source.get('error') for source in sources if source['enabled'])
        summary = ('持续监测中，最近一批已发布或更新异常' if settings.get('last_batch_published')
                   else '持续监测中，本轮未发布新异常')
        if backlog:
            summary += f'；待处理 {backlog} 项'
        if fault or counts.get('retry_wait'):
            summary += '；存在来源或分析重试，详见运行状态'
        status = 'disabled' if not self.config.pipeline_enabled else 'paused' if settings['paused'] else 'running'
        if status != 'running':
            summary = ('流水线配置未启用' if status == 'disabled' else '流水线已暂停') + f'；保留待处理 {backlog} 项'
        return {
            'enabled': self.config.pipeline_enabled, 'paused': settings['paused'], 'status': status,
            'scope': '民用航运公开报道与霍尔木兹海峡可见通行量',
            'config': {'news_topic': settings['news_topic'], 'rss_terms': settings['rss_terms'],
                       'analysis_version': self.config.pipeline_analysis_version,
                       'batch_size': self.config.pipeline_batch_size},
            'sources': sources, 'counts': counts, 'summary': summary,
            'last_collected_at': settings.get('last_collected_at'),
            'last_analyzed_at': settings.get('last_analyzed_at'),
            'last_published_at': settings.get('last_published_at'),
            'model_retry_at': settings.get('model_retry_at'),
            'alerts': [alert_summary(alert) for alert in self.recent_alerts()],
            'recent_work': [dict({key: work.get(key) for key in ('id', 'kind', 'status', 'created_at',
                'completed_at', 'next_retry_at', 'error')}, decision=work.get('assessment', {}).get('decision'),
                statement=work.get('assessment', {}).get('statement')) for work in self.store.recent('work', 10)],
        }

    async def idle(self):
        try:
            await asyncio.wait_for(self.wakeup.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass
        self.wakeup.clear()

    async def schedule(self):
        while not self.stopping:
            self.collectors = {key: task for key, task in self.collectors.items() if not task.done()}
            if self.running():
                settings = self.store.get('pipeline', 'main')
                for source in settings['sources']:
                    if len(self.collectors) >= max(1, self.config.pipeline_source_concurrency):
                        break
                    progress = self.source_state(source)
                    if (source['enabled'] and source['id'] not in self.collectors and
                            instant(progress['next_check_at']) <= datetime.now(timezone.utc)):
                        self.collectors[source['id']] = asyncio.create_task(self.collect(source, deepcopy(settings)))
            await self.idle()

    def delay(self, attempts, retry_after=None, isolated=False):
        seconds = min(self.config.pipeline_retry_max_seconds,
                      self.config.pipeline_retry_seconds * 2 ** min(max(attempts - 1, 0), 12))
        if isolated:
            seconds = self.config.pipeline_retry_max_seconds
        return max(seconds, retry_after or 0)

    def new_work(self, identifier, source, payload, **extra):
        work = {'id': identifier, 'scope_id': source['scope_id'], 'monitor_id': source['monitor_id'],
                'source': source['source'], 'kind': payload['kind'], 'input': payload,
                'analysis_version': self.config.pipeline_analysis_version,
                'status': 'queued', 'created_at': now(), 'attempts': 0,
                'next_retry_at': now(), 'error': None, **extra}
        return self.store.insert_once('work', work)

    async def read_article(self, material):
        pending = self.store.get('article_retry', material['url'])
        if pending and instant(pending['next_retry_at']) > datetime.now(timezone.utc):
            return {'material': dict(material, extraction_error=pending['error']),
                    'error': pending['error'], 'status': 'retry_wait'}
        result = await read_material(material, self.config)
        wait = result.get('retry_after_seconds')
        if wait:
            self.store.save('article_retry', {'id': material['url'], 'next_retry_at': after(wait),
                                              'error': result.get('error')})
        return result

    async def collect(self, source, settings):
        progress = self.source_state(source)
        run = {'id': uuid4().hex, 'monitor_id': source['monitor_id'], 'mode': 'pipeline_collection',
               'status': 'running', 'stage': 'collecting', 'started_at': now(), 'finished_at': None,
               'source_status': 'pending', 'new_count': 0, 'analyzed_count': 0, 'failed_count': 0,
               'steps': [], 'error': None, 'summary': ''}
        self.store.save('run', run)
        progress.update(status='collecting', last_checked_at=now())
        self.store.save('pipeline_source', progress)
        result = None
        try:
            monitor = self.store.get('monitor', source['monitor_id'])
            async with self.runner.source_lock(monitor):
                if source['source'] == 'portwatch':
                    result = await collect_portwatch_series(self.config)
                else:
                    end = datetime.now(timezone.utc)
                    cursor = progress.get('scanned_until') or progress.get('covered_until')
                    start = (instant(cursor) - timedelta(minutes=self.config.pipeline_overlap_minutes)
                             if cursor else end - timedelta(hours=self.config.pipeline_lookback_hours))
                    outstanding = (progress.get('coverage') or {}).get('uncovered_windows')
                    if outstanding and progress.get('batch_start') and progress.get('batch_end'):
                        start, end = instant(progress['batch_start']), instant(progress['batch_end'])
                    progress.update(batch_start=start.isoformat(), batch_end=end.isoformat())
                    buffer = self.store.get('pipeline_buffer', source['monitor_id'])
                    if buffer and buffer.get('status') == 'pending':
                        result = buffer['result']
                        settings = buffer['settings']
                        progress.update(batch_start=buffer['batch_start'], batch_end=buffer['batch_end'])
                    else:
                        result = await search_news(source['source'], settings['news_topic'], start, end, self.config,
                                                   terms=settings['rss_terms'], max_windows=self.config.pipeline_search_windows,
                                                   windows=outstanding or None,
                                                   gaps=(progress.get('coverage') or {}).get('gaps') if outstanding else None)
                        buffer = {'id': source['monitor_id'], 'status': 'pending', 'result': result,
                                  'settings': settings, 'processed_count': 0,
                                  'batch_start': start.isoformat(), 'batch_end': end.isoformat()}
                        self.store.save('pipeline_buffer', buffer)
                    # Body reads are source work; the model consumer proceeds independently.
                    for offset in range(buffer['processed_count'], len(result['materials']), 2):
                        if not self.running():
                            progress.update(status='queued', next_check_at=now())
                            self.store.save('pipeline_source', progress)
                            run.update(status='paused', finished_at=now())
                            self.store.save('run', run)
                            return
                        reads = await asyncio.gather(*(self.read_article(item)
                                                      for item in result['materials'][offset:offset + 2]))
                        for index, read in enumerate(reads, offset):
                            result['materials'][index] = read.get('material') or result['materials'][index]
                        buffer.update(result=result, processed_count=offset + len(reads))
                        self.store.save('pipeline_buffer', buffer)
            timestamp = now()
            failed = result['status'] in ('source_failed', 'unavailable')
            retry_needed = failed or bool(result.get('error'))
            with self.store.atomic():
                if source['source'] == 'portwatch' and not failed:
                    self.save_numeric(source, result, run, progress)
                elif source['source'] == 'portwatch':
                    numeric_source = self.store.get('portwatch_source', source['monitor_id']) or {
                        'id': source['monitor_id'], 'source_key': self.config.portwatch_url.rstrip('/'),
                        'portid': self.config.portwatch_id}
                    numeric_source.update(status=result['status'], error=result.get('error'), last_checked_at=timestamp)
                    self.store.save('portwatch_source', numeric_source)
                elif source['source'] != 'portwatch':
                    self.save_news(source, settings, result, run, progress)
                    self.store.save('pipeline_buffer', {'id': source['monitor_id'], 'status': 'completed'})
                progress.update(last_checked_at=timestamp, status=result['status'], error=result.get('error'))
                if result.get('coverage'):
                    progress['coverage'] = result['coverage']
                    covered = result['coverage'].get('covered_until')
                    if covered:
                        progress['covered_until'] = covered
                    if result['coverage'].get('scanned_until'):
                        progress['scanned_until'] = result['coverage']['scanned_until']
                    gaps = {str(gap): gap for gap in progress.get('coverage_gaps', [])}
                    gaps.update({str(gap): gap for gap in result['coverage'].get('gaps', [])})
                    progress['coverage_gaps'] = list(gaps.values())
                if retry_needed:
                    progress['attempts'] = progress.get('attempts', 0) + 1
                    progress['next_check_at'] = after(self.delay(progress['attempts'], result.get('retry_after_seconds')))
                else:
                    progress.update(attempts=0, last_success_at=timestamp,
                                    next_check_at=after(max(source['interval_seconds'], result.get('retry_after_seconds') or 0)))
                self.store.save('pipeline_source', progress)
                run.update(status='source_failed' if failed else 'completed', stage='finished',
                           source_status=result['status'], error=result.get('error'), finished_at=timestamp,
                           summary=result.get('detail', ''))
                run['steps'].append({'tool': source['source'] + '.collect', 'status': result['status'],
                                     'detail': {'coverage': result.get('coverage'), 'error': result.get('error')}})
                self.store.save('run', run)
                monitor = self.store.get('monitor', source['monitor_id'])
                monitor.update(last_collected_at=timestamp, last_error=result.get('error'))
                if not failed:
                    monitor['last_success_at'] = timestamp
                self.store.save('monitor', monitor)
                current = self.store.get('pipeline', 'main')
                if not failed:
                    current['last_collected_at'] = timestamp
                self.store.save('pipeline', current)
        except asyncio.CancelledError:
            progress.update(status='queued', next_check_at=now(), error='应用停止，重启后续接')
            self.store.save('pipeline_source', progress)
            run.update(status='interrupted', finished_at=now())
            self.store.save('run', run)
            raise
        except Exception as error:
            progress['attempts'] = progress.get('attempts', 0) + 1
            progress.update(status='retry_wait', error=str(error),
                            next_check_at=after(self.delay(progress['attempts'])))
            self.store.save('pipeline_source', progress)
            run.update(status='failed', error=str(error), finished_at=now())
            self.store.save('run', run)
        finally:
            self.wakeup.set()

    def save_news(self, source, settings, result, run, progress):
        progress.setdefault('initialization_cutoff', progress.get('batch_end') or now())
        for candidate in result['materials']:
            material, created = self.store.add_material(source['monitor_id'], run['id'],
                                                        dict(candidate, analysis_status='pipeline'))
            run['new_count'] += int(created)
            key = f"news:{source['scope_id']}:{material['id']}:{material['version']}:{self.config.pipeline_analysis_version}"
            self.store.save('news_current', {'id': source['monitor_id'] + ':' + material['url'],
                'material_id': material['id'], 'version': material['version']})
            initial = (instant(material['observed_at']) <= instant(progress['initialization_cutoff'])
                       if material.get('observed_at') else not progress.get('initialized_at'))
            payload = {'id': material['id'], 'kind': 'news', 'materials': [material_input(material)],
                       'origin': 'initialization' if initial else 'update',
                       'scope': {'description': '民用航运公开报道', 'news_topic': settings['news_topic'],
                                 'rss_terms': settings['rss_terms'] if source['source'] == 'rss' else None},
                       'coverage': {key: result.get('coverage', {}).get(key) for key in
                                    ('complete', 'truncated', 'history_coverage', 'feed_title')}}
            affected = []
            if created and material['version'] > 1:
                for alert in self.context_alerts(source['scope_id']):
                    if alert.get('origin_type') == 'news_clue' and any(
                            ref.get('url') == material['url'] for ref in alert.get('evidence_refs', [])):
                        alert.update(analysis_stale=True, analysis_status='queued', queued_work_id=key,
                                     updated_at=now(), display_status='pending_analysis')
                        self.store.save('alert', alert)
                        affected.append(alert['id'])
            self.new_work(key, source, payload, news_topic=settings['news_topic'], rss_terms=settings['rss_terms'],
                          material_ids=[material['id']], material_url=material['url'],
                          news_alerts=affected, source_config=deepcopy(source))
        if result['status'] not in ('source_failed', 'unavailable'):
            progress.setdefault('initialized_at', now())
        dates = [m.get('observed_at') for m in result['materials'] if m.get('observed_at')]
        if dates:
            progress['latest_observed_date'] = max(dates)

    def save_numeric(self, source, result, run, progress):
        scope = {'source_key': self.config.portwatch_url.rstrip('/'), 'portid': self.config.portwatch_id}
        counts = self.store.upsert_portwatch(source['monitor_id'], run['id'], result['rows'],
                                            scope['source_key'], scope['portid'], result['checked_at'])
        run.update({key: counts[key] for key in ('new_count', 'revised_count', 'unchanged_count')})
        previous = self.store.get('monitor_state', source['monitor_id'])
        old_alerts = [a for a in self.store.all('alert') if a.get('monitor_id') == source['monitor_id']]
        rows = self.store.portwatch_rows(**scope)
        output = evaluate(rows, previous, old_alerts, self.config.portwatch_rule, now(), source['monitor_id'], **scope)
        state = dict(output['state'], series=output['series'], last_run_id=run['id'])
        inputs = {'rule': self.config.portwatch_rule, 'versions': state['input_versions'],
                  'analysis_version': self.config.pipeline_analysis_version}
        changed = bool(rows) and progress.get('input_versions') != inputs
        revision = progress.get('input_revision', 0) + int(changed)
        key = f"numeric:{source['scope_id']}:{source['monitor_id']}:{revision}:{self.config.pipeline_analysis_version}"
        old_by_id = {alert['id']: alert for alert in old_alerts}
        changed_alerts = []
        for alert in output['alerts']:
            old = old_by_id.get(alert['id'])
            evidence_changed = old is None or old.get('evidence_version') != alert['evidence_version']
            if changed and old and old.get('analysis', {}).get('analysis_version') not in (None, self.config.pipeline_analysis_version):
                evidence_changed = True
            alert = dict(old or {}, **alert)
            alert.update(origin_type='numeric_rule', scope_id=source['scope_id'],
                         title='PortWatch 可见通行量持续偏低提醒', observed_at=alert['last_evaluated_date'],
                         available_at=None)
            trigger_seen = [row['first_seen_at'] for row in rows if row['observed_date'] in
                            (alert['started_on'], alert['triggered_on'])]
            alert['first_seen_at'] = (old or {}).get('first_seen_at') or (min(trigger_seen) if trigger_seen else None)
            if evidence_changed:
                alert.update(published_at=(old or {}).get('published_at') or now(), updated_at=now(),
                             analysis_status='queued', queued_work_id=key, read_at=None,
                             display_status='pending_analysis', analysis_stale=bool((old or {}).get('analysis')))
                changed_alerts.append({'id': alert['id'], 'evidence_version': alert['evidence_version']})
                current = self.store.get('pipeline', 'main')
                current['last_published_at'] = now()
                current['last_batch_published'] = True
                self.store.save('pipeline', current)
            self.store.save('alert', alert)
        self.store.save('monitor_state', state)
        self.store.save('portwatch_source', dict(id=source['monitor_id'], **scope,
            status=result['status'], error=None, last_checked_at=result['checked_at'],
            last_success_at=result['checked_at'], range_start=result['range_start'], range_end=result['range_end']))
        progress.update(input_versions=inputs, input_revision=revision,
                        latest_observed_date=state['latest_observed_date'])
        if not changed:
            return
        selected = list(rows[-3:])
        changed_rows = [row for row in rows if row['observed_date'] in counts['changed_dates']]
        selected.extend(changed_rows[:2])
        relevant = [alert for alert in output['alerts'] if alert['id'] in {a['id'] for a in changed_alerts}]
        for alert in relevant[:2]:
            selected.extend(alert.get('trigger_observations', []))
        ids = list(dict.fromkeys(row['material_id'] for row in selected if row.get('material_id')))
        materials = [material_input(self.store.get_material(identifier)) for identifier in ids]
        # Program values are authoritative. The full series stays in SQLite, outside the model context.
        for material in materials:
            material['text'] = ''
        program = {key: state.get(key) for key in ('current_status', 'summary', 'latest_observed_date',
            'latest_value', 'baseline', 'baseline_start', 'baseline_end', 'baseline_valid_days', 'ratio', 'data_gaps')}
        program.update(rule=self.config.portwatch_rule, changes={key: counts[key] for key in (
            'new_count', 'revised_count', 'unchanged_count')},
            observations=[{key: row.get(key) for key in ('observed_date', 'n_total', 'validity', 'material_id')}
                          for row in selected[:9]],
            alerts=[{'id': alert['id'], 'status': alert['status'], 'summary': alert['summary'],
                     'baseline': alert['baseline'], 'evidence_version': alert['evidence_version']}
                    for alert in relevant])
        self.new_work(key, source, {'id': key, 'kind': 'portwatch', 'materials': materials,
            'program': program, 'origin': 'initialization' if not previous else 'update'},
            material_ids=ids, numeric_alerts=changed_alerts, input_revision=revision,
            source_config=deepcopy(source))

    async def consume(self):
        while not self.stopping:
            if not self.running():
                await self.idle()
                continue
            retry_at = self.store.get('pipeline', 'main').get('model_retry_at')
            if retry_at and instant(retry_at) > datetime.now(timezone.utc):
                await self.idle()
                continue
            due = self.store.due_work(now(), limit=30)
            if not due:
                await self.idle()
                continue
            first = due[0]
            batch = [first]
            if first['kind'] == 'news' and not first.get('single_retry'):
                batch += [work for work in due[1:] if work['kind'] == 'news'
                          and work['scope_id'] == first['scope_id']
                          and work['analysis_version'] == first['analysis_version']
                          and not work.get('single_retry')][:max(0, min(3, self.config.pipeline_batch_size) - 1)]
            await self.analyze(batch)

    def context_alerts(self, scope_id):
        return [alert for alert in self.recent_alerts(100) if alert['scope_id'] == scope_id][:5]

    async def analyze(self, batch):
        run = {'id': uuid4().hex, 'monitor_id': batch[0]['monitor_id'], 'mode': 'pipeline_analysis',
               'status': 'queued', 'stage': 'analyzing', 'started_at': now(), 'finished_at': None,
               'input_count': len(batch), 'new_count': 0, 'analyzed_count': 0, 'failed_count': 0,
               'source_status': 'not_required', 'steps': [], 'summary': '', 'error': None}
        tool_materials = {m['id']: m for work in batch for m in work['input']['materials']}
        try:
            async with self.runner.model_lock:
                if not self.running() or self.stopping:
                    return
                with self.store.atomic():
                    run['status'] = 'running'
                    self.store.save('run', run)
                    for work in batch:
                        work.update(status='running', attempts=work['attempts'] + 1,
                                    started_at=now(), run_id=run['id'])
                        self.store.save('work', work)
                        self.mark_numeric(work, 'running')
                contexts = self.context_alerts(batch[0]['scope_id'])
                async def handle_tool(name, args):
                    return await self.tool(name, args, batch, tool_materials, run)
                result = await assess_update([work['input'] for work in batch],
                    [{key: alert.get(key) for key in ('id', 'origin_type', 'title', 'status', 'observed_at')}
                     | {'summary': alert.get('summary', '')[:180]} for alert in contexts],
                    self.config, handle_tool)
                with self.store.atomic():
                    by_id = {item['input_id']: item for item in result['assessments']}
                    for work in batch:
                        self.publish(work, by_id[work['input']['id']], result, tool_materials)
                    self.runner.model.update(status='ready', name=result['model'], last_error=None, last_call_at=now())
                    self.store.save('model', self.runner.model)
                    current = self.store.get('pipeline', 'main')
                    current['last_analyzed_at'] = now()
                    current['model_retry_at'] = None
                    current['last_batch_published'] = any(work.get('published_alert_ids') for work in batch)
                    self.store.save('pipeline', current)
                    run.update(status='completed', stage='completed', finished_at=now(), analyzed_count=len(batch),
                               summary='；'.join(item['statement'] for item in result['assessments']),
                               steps=[{'tool': 'qwen.assess_update', 'status': 'ok',
                                       'elapsed_ms': result['elapsed_ms'],
                                       'detail': {'attempts': result['attempts'], 'tools': result['tool_results']}}])
                    self.store.save('run', run)
        except asyncio.CancelledError:
            with self.store.atomic():
                for work in batch:
                    latest = self.store.get('work', work['id'])
                    if latest['status'] != 'completed':
                        latest.update(status='queued', next_retry_at=now(), error='应用停止，重启后自动续接')
                        self.store.save('work', latest)
                        self.mark_numeric(latest, 'queued')
                run.update(status='interrupted', finished_at=now())
                self.store.save('run', run)
            raise
        except Exception as error:
            invalid = isinstance(error, ModelError) and error.code in ('invalid_model_output', 'length')
            with self.store.atomic():
                for work in batch:
                    work.update(status='retry_wait', error=str(error), single_retry=invalid or work.get('single_retry', False),
                                isolated=invalid and work['attempts'] >= 3,
                                next_retry_at=after(self.delay(work['attempts'],
                                    getattr(error, 'retry_after_seconds', None), invalid and work['attempts'] >= 3)))
                    self.store.save('work', work)
                    self.mark_numeric(work, 'retry_wait')
                if isinstance(error, ModelError):
                    self.runner.model.update(status='unavailable', last_error=str(error), last_call_at=now())
                    self.store.save('model', self.runner.model)
                    if not invalid:
                        current = self.store.get('pipeline', 'main')
                        current['model_retry_at'] = max(work['next_retry_at'] for work in batch)
                        self.store.save('pipeline', current)
                run.update(status='model_failed', stage='retry_wait', finished_at=now(),
                           failed_count=len(batch), error=str(error),
                           steps=[{'tool': 'qwen.assess_update', 'status': 'failed',
                                   'detail': {'error': str(error), 'attempts': getattr(error, 'attempts', []),
                                              'tools': getattr(error, 'tool_results', [])}}])
                self.store.save('run', run)
        finally:
            self.wakeup.set()

    def mark_numeric(self, work, status):
        for target in work.get('numeric_alerts', []) + [{'id': identifier} for identifier in work.get('news_alerts', [])]:
            alert = self.store.get('alert', target['id'])
            if alert and alert.get('queued_work_id') == work['id']:
                alert.update(analysis_status=status, updated_at=now())
                self.store.save('alert', alert)

    async def tool(self, name, args, batch, allowed, run):
        if name == 'read_material':
            identifier = args.get('material_id')
            if identifier not in allowed:
                return {'materials': [], 'error': '只能补读本批真实提供的材料编号'}
            original = self.store.get_material(identifier)
            if original['source'] == 'portwatch':
                return {'materials': [material_input(original)], 'detail': '已保存的结构化日记录'}
            result = await self.read_article(deepcopy(original))
            candidates = [result.get('material') or original]
        elif name == 'search_news':
            work = batch[0]
            settings = self.store.get('pipeline', 'main')
            # A fixed source, topic and time range prevent model-supplied URLs or unbounded searches.
            source = next((s for s in settings['sources'] if s['enabled'] and s['source'] in ('gdelt', 'rss')
                           and (work['kind'] == 'portwatch' or s['scope_id'] == work['scope_id'])), None)
            if source is None:
                return {'materials': [], 'error': '此范围没有可用新闻来源'}
            end = datetime.now(timezone.utc)
            start = end - timedelta(hours=self.config.pipeline_lookback_hours)
            monitor = self.store.get('monitor', source['monitor_id'])
            query = args.get('query', '').replace('"', ' ').replace('\\', ' ').strip()[:160]
            topic = f"({settings['news_topic']}) \"{query}\"" if query and source['source'] == 'gdelt' else settings['news_topic']
            async with self.runner.source_lock(monitor):
                result = await search_news(source['source'], topic, start, end,
                                           self.config, terms=settings['rss_terms'], max_windows=1,
                                           limit=3 if source['source'] == 'gdelt' else None)
            candidates = result['materials']
            if query and source['source'] == 'rss':
                candidates = [item for item in candidates if all(term.casefold() in
                              (item['title'] + ' ' + item.get('text', '')).casefold() for term in query.split())]
            candidates = candidates[:3]
        else:
            return {'materials': [], 'error': '不支持的补读动作'}
        materials = []
        with self.store.atomic():
            for candidate in candidates:
                owner = original['monitor_id'] if name == 'read_material' else source['monitor_id']
                if name == 'search_news':
                    pointer = self.store.get('news_current', owner + ':' + candidate['url'])
                    existing = self.store.get_material(pointer['material_id']) if pointer else None
                    if existing and existing['title'] == candidate['title'] and existing['content_kind'] == 'article_text':
                        candidate = existing
                saved, _ = self.store.add_material(owner, run['id'], dict(candidate, analysis_status='pipeline'))
                item = material_input(saved)
                allowed[item['id']] = item
                materials.append(item)
                # Supplementary inputs also get one persistent processing record.
                parent = next((work for work in batch if identifier in work.get('material_ids', [])), batch[0]) if name == 'read_material' else batch[0]
                if name == 'read_material':
                    monitor = self.store.get('monitor', original['monitor_id'])
                    source_config = {'id': monitor['source'], 'source': monitor['source'],
                                     'scope_id': monitor['scope_id'], 'monitor_id': monitor['id']}
                else:
                    source_config = source
                if saved['source'] != 'portwatch':
                    self.store.save('news_current', {'id': owner + ':' + saved['url'],
                        'material_id': saved['id'], 'version': saved['version']})
                    key = f"news:{source_config['scope_id']}:{saved['id']}:{saved['version']}:{self.config.pipeline_analysis_version}"
                    if key not in {work['id'] for work in batch}:
                        self.new_work(key, source_config, {'id': saved['id'], 'kind': 'news',
                            'materials': [item], 'origin': 'update'}, material_ids=[saved['id']],
                            material_url=saved['url'],
                            source_config=deepcopy(source_config), news_topic=self.store.get('pipeline', 'main')['news_topic'],
                            rss_terms=self.store.get('pipeline', 'main')['rss_terms'])
        return {'materials': materials, 'coverage': result.get('coverage'), 'error': result.get('error'),
                'detail': result.get('detail')}

    def publish(self, work, assessment, result, materials):
        timestamp = now()
        analysis = {**assessment, 'model': result['model'], 'completed_at': timestamp,
                    'work_id': work['id'], 'analysis_version': work['analysis_version']}
        references = [material_ref(materials[identifier]) for identifier in assessment['evidence_refs']]
        work['published_alert_ids'] = []
        if work['kind'] == 'news':
            current = self.store.get('news_current', work['monitor_id'] + ':' +
                                     (work.get('material_url') or work['input']['materials'][0]['url']))
            if current and current['material_id'] != work['input']['id'] and current['material_id'] not in assessment['evidence_refs']:
                work.update(status='completed', completed_at=timestamp, error=None,
                            assessment=dict(analysis, stale=True), tool_results=result['tool_results'],
                            attempts_detail=result['attempts'])
                self.store.save('work', work)
                return
        if work['kind'] == 'portwatch':
            for target in work.get('numeric_alerts', []):
                alert = self.store.get('alert', target['id'])
                if alert.get('queued_work_id') != work['id'] or alert['evidence_version'] != target['evidence_version']:
                    continue
                if alert.get('analysis'):
                    alert.setdefault('analysis_history', []).append(alert['analysis'])
                alert.update(analysis=dict(analysis, evidence_version=target['evidence_version']),
                             analysis_stale=False, analysis_status='insufficient_evidence'
                             if assessment['decision'] == 'insufficient_evidence' else 'completed',
                             evidence_refs=references, updated_at=timestamp,
                             display_status='resolved' if alert['status'] == 'resolved' else
                             'revised' if alert['origin'] == 'revision' or alert['status'] == 'revoked' else
                             'new' if not alert.get('analysis') else 'ongoing')
                self.store.save('alert', alert)
                work['published_alert_ids'].append(alert['id'])
        elif assessment['decision'] in ('candidate', 'update'):
            previous_id = assessment.get('existing_alert_id')
            alert = self.store.get('alert', previous_id or 'news-' + work['input']['id'])
            if alert and (alert.get('scope_id') != work['scope_id'] or alert.get('origin_type') != 'news_clue'):
                raise ValueError('新闻线索只能更新本范围的既有新闻记录')
            if alert is None:
                alert = {'id': 'news-' + work['input']['id'], 'origin_type': 'news_clue',
                         'scope_id': work['scope_id'], 'monitor_id': work['monitor_id'],
                         'detected_at': timestamp, 'published_at': timestamp, 'evidence_version': 0,
                         'origin': work['input']['origin'], 'status': 'active', 'analysis_history': []}
            elif alert.get('analysis'):
                alert.setdefault('analysis_history', []).append(dict(alert['analysis'], evidence_refs=alert.get('evidence_refs', [])))
            first = work['input']['materials'][0]
            status = assessment.get('news_status') or 'active'
            alert.update(title=assessment['title'], summary=assessment['statement'], status=status,
                         analysis=analysis, analysis_stale=False, analysis_status='completed', evidence_refs=references,
                         evidence_version=alert['evidence_version'] + 1, updated_at=timestamp,
                         observed_at=first.get('observed_at'), available_at=first.get('available_at'),
                         first_seen_at=first.get('first_seen_at'), read_at=None,
                         display_status='resolved' if status == 'resolved' else 'revised' if previous_id else 'new')
            self.store.save('alert', alert)
            work['published_alert_ids'].append(alert['id'])
            current = self.store.get('pipeline', 'main')
            current['last_published_at'] = timestamp
            self.store.save('pipeline', current)
        for identifier in work.get('news_alerts', []):
            alert = self.store.get('alert', identifier)
            if alert.get('queued_work_id') == work['id'] and identifier not in work['published_alert_ids']:
                alert.update(analysis_status='insufficient_evidence', analysis_stale=True,
                             latest_assessment=analysis, updated_at=timestamp)
                self.store.save('alert', alert)
        work.update(status='completed', completed_at=timestamp, error=None, assessment=analysis,
                    tool_results=result['tool_results'], attempts_detail=result['attempts'])
        self.store.save('work', work)

    async def close(self):
        self.stopping = True
        self.wakeup.set()
        for task in self.tasks + list(self.collectors.values()):
            task.cancel()
        await asyncio.gather(*self.tasks, *self.collectors.values(), return_exceptions=True)
