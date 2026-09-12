import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from time import perf_counter
from uuid import uuid4

from .history import replay_materials
from .llm import ModelError, analyze, explain_alert
from .monitoring import evaluate
from .sources import collect, collect_portwatch_series
from .store import now


class Runner:
    def __init__(self, store, config):
        self.store = store
        self.config = config
        self.task = None
        self.active_run_id = None
        self.cancel_requested = False
        self.model_lock = asyncio.Lock()
        self.source_locks = {}
        self.model = store.get('model', 'qwen') or {
            'id': 'qwen', 'name': config.model_name, 'status': 'unknown',
            'last_error': None, 'last_call_at': None}
        for run in store.all('run'):
            if run['status'] in ('running', 'queued', 'cancelling'):
                run.update(status='interrupted', stage='interrupted', finished_at=now())
                store.save('run', run)
                if run.get('alert_id'):
                    alert = store.get('alert', run['alert_id'])
                    if alert and (alert.get('explanation') or {}).get('status') in ('pending', 'running'):
                        alert['explanation'].update(status='failed', error='解释任务因应用重启中断')
                        store.save('alert', alert)

    def start(self, monitor_id, mode='online', batch_size=3):
        if self.task and not self.task.done():
            raise RuntimeError('已有维护任务运行，本次请求未创建运行')
        run = {
            'id': uuid4().hex, 'monitor_id': monitor_id, 'mode': mode,
            'status': 'queued', 'stage': 'queued', 'started_at': now(),
            'finished_at': None, 'new_count': 0, 'analyzed_count': 0,
            'failed_count': 0, 'error': None, 'source_status': 'pending',
            'steps': [], 'summary': '', 'input_count': 0}
        self.store.save('run', run)
        self.active_run_id = run['id']
        self.cancel_requested = False
        self.task = asyncio.create_task(self.execute(run, batch_size))
        return run

    def source_lock(self, monitor):
        source = monitor['source']
        scope = (self.config.portwatch_url, self.config.portwatch_id) if source == 'portwatch' else (
            self.config.rss_url if source == 'rss' else monitor['topic'])
        key = (source, scope)
        return self.source_locks.setdefault(key, asyncio.Lock())

    def cancel(self, run_id):
        run = self.store.get('run', run_id)
        if run_id == self.active_run_id and self.task and not self.task.done():
            self.cancel_requested = True
            run.update(status='cancelling')
            self.store.save('run', run)
        return run

    def save_run(self, run, **changes):
        run.update(changes)
        if self.cancel_requested and run['status'] == 'running':
            run['status'] = 'cancelling'
        self.store.save('run', run)

    def step(self, run, tool, status, elapsed_ms, detail):
        run['steps'].append(dict(tool=tool, status=status, elapsed_ms=elapsed_ms, detail=detail))
        self.store.save('run', run)

    async def analyze_batch(self, run, batch):
        self.save_run(run, stage='analyzing')
        started = perf_counter()
        try:
            async with self.model_lock:
                result = await analyze(batch, self.config)
        except ModelError as error:
            elapsed = round((perf_counter() - started) * 1000)
            for material in batch:
                material.update(analysis_status='failed', error=str(error))
                self.store.update_material(material)
            run['failed_count'] += len(batch)
            run['error'] = str(error)
            self.model.update(status='unavailable', last_error=str(error), last_call_at=now())
            self.store.save('model', self.model)
            self.step(run, 'qwen.chat.completions', 'failed', elapsed,
                      {'error': str(error), 'material_ids': [m['id'] for m in batch],
                       'attempts': error.attempts})
            return False
        by_id = {card['material_id']: card for card in result['cards']}
        for material in batch:
            card = by_id[material['id']]
            material.update(analysis_status='done', error=None,
                            analysis=dict(card, run_id=run['id'], monitor_id=run['monitor_id'],
                                          mode=run['mode'], model=result['model'],
                                          elapsed_ms=result['elapsed_ms']))
            self.store.update_material(material)
        self.model.update(status='ready', name=result['model'], last_error=None, last_call_at=now())
        self.store.save('model', self.model)
        run['analyzed_count'] += len(batch)
        run['summary'] += ('\n\n' if run['summary'] else '') + result['summary']
        self.step(run, 'qwen.chat.completions', 'ok', result['elapsed_ms'],
                  {'material_ids': [m['id'] for m in batch], 'model': result['model'],
                   'input_excerpt': [{'id': m['id'], 'title': m['title'],
                                      'text': m.get('text', '')[:self.config.article_chars]}
                                     for m in batch], 'attempts': result['attempts']})
        return True

    async def execute(self, run, batch_size):
        monitor = self.store.get('monitor', run['monitor_id'])
        try:
            self.save_run(run, status='running', stage='collecting')
            if monitor['source'] == 'portwatch' and run['mode'] != 'replay':
                await self.execute_portwatch(run, monitor)
                return
            started = perf_counter()
            if run['mode'] == 'replay':
                imported = replay_materials()
                # Only release the next chronological batch, never the final report.
                cursor = monitor.get('replay_cursor', 0)
                materials = imported[cursor:cursor + batch_size]
                source = {'materials': materials, 'status': 'ok' if materials else 'empty',
                          'error': None, 'detail': '历史新闻标题，按 GDELT 收录时间顺序导入'}
            else:
                async with self.source_lock(monitor):
                    source = await collect(monitor, self.config)
            self.save_run(run, source_status=source['status'])
            self.step(run, 'history.read' if run['mode'] == 'replay' else monitor['source'] + '.collect',
                      source['status'], round((perf_counter() - started) * 1000), source['detail'])
            for material in source['materials']:
                _, created = self.store.add_material(monitor['id'], run['id'], material)
                run['new_count'] += int(created)
            current = self.store.get('monitor', monitor['id'])
            if source['status'] != 'source_failed':
                current['last_collected_at'] = now()
                if run['mode'] == 'replay':
                    current['replay_cursor'] = cursor + len(source['materials'])
            else:
                run['error'] = source['error']
            self.store.save('monitor', current)
            self.save_run(run, stage='stored')
            candidates = self.store.materials(monitor['id'])
            pending = sorted((m for m in candidates if m['analysis_status'] in ('pending', 'failed')),
                             key=lambda m: (m.get('observed_at') or m['fetched_at'], m['id']))
            pending = pending[:monitor['max_materials']]
            run['input_count'] = len(pending)
            for material in pending:
                self.store.attach(run['id'], material['id'])
            for offset in range(0, len(pending), 3):
                if self.cancel_requested:
                    break
                if not await self.analyze_batch(run, pending[offset:offset + 3]):
                    break
            if self.cancel_requested:
                status = 'cancelled'
            elif run['failed_count']:
                status = 'model_failed'
            elif source['status'] == 'source_failed':
                status = 'source_failed'
            elif not pending:
                status = 'empty'
            else:
                status = 'completed'
            self.save_run(run, status=status, stage=status, finished_at=now())
            # Preserve schedule changes made while the network/model call was in flight.
            current = self.store.get('monitor', monitor['id'])
            current.update(last_error=run['error'])
            if status in ('completed', 'empty'):
                current['last_success_at'] = now()
            self.store.save('monitor', current)
        except asyncio.CancelledError:
            self.save_run(run, status='interrupted', stage='interrupted', finished_at=now())
            raise
        except Exception as error:
            self.save_run(run, status='failed', stage='failed', error=str(error), finished_at=now())
        finally:
            self.active_run_id = None

    async def execute_portwatch(self, run, monitor):
        started = perf_counter()
        async with self.source_lock(monitor):
            source = await collect_portwatch_series(self.config)
        scope = {'source_key': self.config.portwatch_url.rstrip('/'),
                 'portid': self.config.portwatch_id}
        source_state = self.store.get('portwatch_source', monitor['id']) or {'id': monitor['id']}
        source_state.update(last_checked_at=source['checked_at'],
                            status=source['status'], error=source['error'], **scope)
        self.save_run(run, source_status=source['status'])
        self.step(run, 'portwatch.series', source['status'],
                  round((perf_counter() - started) * 1000), source['detail'])
        if source['status'] == 'source_failed' or self.cancel_requested:
            status = 'source_failed' if source['status'] == 'source_failed' else 'cancelled'
            self.store.save('portwatch_source', source_state)
            self.save_run(run, status=status, stage=status, error=source['error'], finished_at=now())
            current = self.store.get('monitor', monitor['id'])
            current['last_error'] = source['error']
            self.store.save('monitor', current)
            return
        self.save_run(run, stage='stored')
        counts = self.store.upsert_portwatch(monitor['id'], run['id'], source['rows'],
                                            scope['source_key'], scope['portid'], source['checked_at'])
        self.save_run(run, new_count=counts['new_count'], revised_count=counts['revised_count'],
                      unchanged_count=counts['unchanged_count'], input_count=len(source['rows']))
        previous = self.store.get('monitor_state', monitor['id'])
        self.save_run(run, stage='evaluating')
        evaluation_started = perf_counter()
        evaluated_at = now()
        rows = self.store.portwatch_rows(scope['source_key'], scope['portid'])
        previous_alerts = [a for a in self.store.all('alert') if a['monitor_id'] == monitor['id']]
        output = evaluate(rows=rows, previous_state=previous, previous_alerts=previous_alerts,
                          rule=self.config.portwatch_rule, checked_at=evaluated_at,
                          monitor_id=monitor['id'], **scope)
        for alert in output['alerts']:
            self.store.save('alert', alert)
        monitoring = dict(output['state'], series=output['series'], last_run_id=run['id'])
        self.store.save('monitor_state', monitoring)
        source_state.update(last_success_at=source['checked_at'],
                            range_start=source['range_start'], range_end=source['range_end'])
        self.store.save('portwatch_source', source_state)
        self.step(run, 'portwatch.evaluate', 'ok', round((perf_counter() - evaluation_started) * 1000),
                  {'new_days': counts['new_count'], 'revised_days': counts['revised_count'],
                   'unchanged_days': counts['unchanged_count'], 'rule': self.config.portwatch_rule,
                   'result': monitoring['summary']})
        status = 'completed' if counts['new_count'] or counts['revised_count'] else 'empty'
        summary = (f"新增 {counts['new_count']} 个观测日，修订 {counts['revised_count']} 日，"
                   f"未变 {counts['unchanged_count']} 日。{monitoring['summary']}")
        self.save_run(run, status=status, stage=status, summary=summary, finished_at=now())
        current = self.store.get('monitor', monitor['id'])
        current.update(last_collected_at=source['checked_at'], last_success_at=now(), last_error=None)
        self.store.save('monitor', current)

    def start_explanation(self, alert):
        existing = alert.get('explanation') or {}
        if existing.get('status') == 'completed' and existing.get('evidence_version') == alert['evidence_version']:
            return {'alert': alert, 'run': None, 'cached': True}
        if self.task and not self.task.done():
            run = self.store.get('run', self.active_run_id)
            if run.get('alert_id') == alert['id']:
                return {'alert': alert, 'run': run, 'cached': False}
            raise RuntimeError('当前已有任务运行，请在该任务结束后生成解释')
        run = {
            'id': uuid4().hex, 'monitor_id': alert['monitor_id'], 'mode': 'explanation',
            'alert_id': alert['id'], 'evidence_version': alert['evidence_version'],
            'status': 'queued', 'stage': 'queued', 'started_at': now(), 'finished_at': None,
            'new_count': 0, 'analyzed_count': 0, 'failed_count': 0, 'error': None,
            'source_status': 'not_required', 'steps': [], 'summary': '', 'input_count': 1,
        }
        alert['explanation'] = {'status': 'pending', 'evidence_version': alert['evidence_version'],
                                'error': None, 'generated_at': None}
        self.store.save('alert', alert)
        self.store.save('run', run)
        self.active_run_id = run['id']
        self.cancel_requested = False
        self.task = asyncio.create_task(self.execute_explanation(run, deepcopy(alert['evidence'])))
        return {'alert': alert, 'run': run, 'cached': False}

    async def execute_explanation(self, run, evidence):
        try:
            alert = self.store.get('alert', run['alert_id'])
            if self.cancel_requested:
                alert['explanation'].update(status='failed', error='解释已取消')
                self.store.save('alert', alert)
                self.save_run(run, status='cancelled', stage='cancelled', finished_at=now())
                return
            alert['explanation']['status'] = 'running'
            self.store.save('alert', alert)
            self.save_run(run, status='running', stage='explaining')
            async with self.model_lock:
                result = await explain_alert(evidence, self.config)
            alert = self.store.get('alert', run['alert_id'])
            explanation_status = ('completed' if alert['evidence_version'] == run['evidence_version'] else 'stale')
            alert['explanation'] = {
                'status': explanation_status, 'text': result['text'],
                'material_ids': result['material_ids'], 'model': result['model'],
                'elapsed_ms': result['elapsed_ms'], 'generated_at': now(),
                'evidence_version': run['evidence_version'], 'error': None,
            }
            self.store.save('alert', alert)
            self.model.update(status='ready', name=result['model'], last_error=None, last_call_at=now())
            self.store.save('model', self.model)
            self.step(run, 'qwen.explain_alert', 'ok', result['elapsed_ms'],
                      {'alert_id': alert['id'], 'evidence_version': run['evidence_version'],
                       'model': result['model'], 'attempts': result['attempts']})
            status = 'cancelled' if self.cancel_requested else 'completed'
            self.save_run(run, status=status, stage=status, analyzed_count=1,
                          summary=result['text'], finished_at=now())
        except asyncio.CancelledError:
            alert = self.store.get('alert', run['alert_id'])
            alert['explanation'].update(status='failed', error='解释任务因应用停止中断')
            self.store.save('alert', alert)
            self.save_run(run, status='interrupted', stage='interrupted', finished_at=now())
            raise
        except Exception as error:
            alert = self.store.get('alert', run['alert_id'])
            alert['explanation'].update(status='failed', error=str(error))
            self.store.save('alert', alert)
            if isinstance(error, ModelError):
                self.model.update(status='unavailable', last_error=str(error), last_call_at=now())
                self.store.save('model', self.model)
                self.step(run, 'qwen.explain_alert', 'failed',
                          sum(a.get('elapsed_ms', 0) for a in error.attempts),
                          {'error': str(error), 'attempts': error.attempts})
            self.save_run(run, status='model_failed', stage='model_failed',
                          failed_count=1, error=str(error), finished_at=now())
        finally:
            self.active_run_id = None

    async def schedule(self):
        while True:
            await asyncio.sleep(2)
            if self.task and not self.task.done():
                continue
            timestamp = now()
            for monitor in self.store.all('monitor'):
                if not monitor.get('pipeline_owned') and monitor.get('enabled') and monitor.get('next_run_at', '') <= timestamp:
                    monitor['next_run_at'] = (datetime.now(timezone.utc) + timedelta(
                        minutes=monitor['interval_minutes'])).isoformat()
                    self.store.save('monitor', monitor)
                    self.start(monitor['id'])
                    break

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
