import asyncio
from datetime import datetime, timedelta, timezone
from time import perf_counter
from uuid import uuid4

from .history import replay_materials
from .llm import ModelError, analyze
from .sources import collect
from .store import now


class Runner:
    def __init__(self, store, config):
        self.store = store
        self.config = config
        self.task = None
        self.active_run_id = None
        self.cancel_requested = False
        self.model = store.get('model', 'qwen') or {
            'id': 'qwen', 'name': config.model_name, 'status': 'unknown',
            'last_error': None, 'last_call_at': None}
        for run in store.all('run'):
            if run['status'] in ('running', 'queued', 'cancelling'):
                run.update(status='interrupted', stage='interrupted', finished_at=now())
                store.save('run', run)

    def start(self, monitor_id, mode='online', batch_size=3):
        if self.task and not self.task.done():
            return self.store.get('run', self.active_run_id)
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
            started = perf_counter()
            if run['mode'] == 'replay':
                imported = replay_materials()
                # Only release the next chronological batch, never the final report.
                cursor = monitor.get('replay_cursor', 0)
                materials = imported[cursor:cursor + batch_size]
                source = {'materials': materials, 'status': 'ok' if materials else 'empty',
                          'error': None, 'detail': '历史新闻标题，按 GDELT 收录时间顺序导入'}
            else:
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
            pending = sorted((m for m in candidates if m['analysis_status'] != 'done'),
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

    async def schedule(self):
        while True:
            await asyncio.sleep(2)
            if self.task and not self.task.done():
                continue
            timestamp = now()
            for monitor in self.store.all('monitor'):
                if monitor.get('enabled') and monitor.get('next_run_at', '') <= timestamp:
                    monitor['next_run_at'] = (datetime.now(timezone.utc) + timedelta(
                        minutes=monitor['interval_minutes'])).isoformat()
                    self.store.save('monitor', monitor)
                    self.start(monitor['id'])
                    break

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
