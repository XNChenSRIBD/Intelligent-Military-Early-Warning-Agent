"""Sequential historical cases using the existing durable analysis consumer."""

import asyncio
import csv
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .config import ROOT
from .monitoring import evaluate
from .pipeline import Pipeline, alert_summary, instant, material_input, material_ref
from .store import now


def utc(value):
    if len(value) == 10:
        value += 'T00:00:00+00:00'
    stamp = instant(value)
    if stamp.tzinfo is None:
        raise ValueError('回放时间必须声明时区')
    return stamp.astimezone(timezone.utc)


class CaseReplay(Pipeline):
    def __init__(self, store, config, runner, acquisition):
        if store.all('monitor') and not store.get('replay', 'instance'):
            raise ValueError('case_replay 必须使用独立 DATA_DIR，不能使用已有在线数据库')
        self.store, self.config, self.runner = store, config, runner
        self.acquisition = acquisition
        self.wakeup, self.collectors, self.tasks = asyncio.Event(), {}, []
        self.stopping = False
        self.definitions = {}
        self.case_order = []
        instance, _ = store.insert_once('replay', {
            'id': 'instance', 'instance_id': uuid4().hex, 'created_at': now(), 'status': 'queued'})
        self.instance_id = instance['instance_id']
        store.insert_once('pipeline', {'id': 'main', 'paused': not config.replay_autostart,
            'sources': [], 'news_topic': '历史观测质量回放', 'rss_terms': [], 'created_at': now()})
        for entry in config.replay_manifests:
            path = Path(entry)
            path = path if path.is_absolute() else ROOT / path
            try:
                definition = json.loads(path.read_text(encoding='utf-8-sig'))
                self.register_case(definition)
            except (OSError, ValueError, KeyError) as error:
                case_id = path.parent.name
                if case_id not in self.case_order:
                    self.case_order.append(case_id)
                store.insert_once('replay_case', {'id': case_id, 'case_id': case_id,
                    'label': case_id, 'status': 'blocked', 'reason': f'输入清单无法读取：{path.name}；{type(error).__name__}',
                    'coverage': {'missing_required': ['可读取的案例清单']}, 'total_batches': 0,
                    'released_batches': 0, 'completed_batches': 0, 'quality_status': 'not_run',
                    'instance_id': self.instance_id})
        for work in store.all('work'):
            if work['status'] == 'running':
                work.update(status='queued', next_retry_at=now(), error='重启后续接同一 as_of')
                store.save('work', work)
        # Reconstruct completion from the retained work, including upgrades
        # from a version that counted an awaiting batch as completed.
        for case_id in self.case_order:
            case = store.get('replay_case', case_id)
            if not case or not case.get('released_batches'):
                continue
            works = self.case_works(case_id)
            completed = 0
            for index in range(case['released_batches']):
                if any(work['status'] != 'completed' for work in works if work.get('batch_index') == index):
                    break
                completed = index + 1
            if case.get('completed_batches') != completed:
                case['completed_batches'] = completed
                if case['status'] not in ('blocked', 'incomplete'):
                    case['status'] = 'analyzing'
                store.save('replay_case', case)

    def register_case(self, definition):
        case_id = definition['case_id']
        self.case_order.append(case_id)
        start, end = utc(definition['start_at']), utc(definition['end_at'])
        resources = list(definition.get('resources', []))
        if any(resource['kind'] == 'portwatch' for resource in resources):
            available_days = {resource['observed_start'][:10] for resource in resources if resource['kind'] == 'portwatch'}
            for offset in range(self.config.portwatch_baseline_days, 0, -1):
                day = (start - timedelta(days=offset)).date()
                if day.isoformat() not in available_days:
                    resources.append({'id': f'{case_id}-portwatch-{day}', 'kind': 'portwatch',
                        'root': 'assets', 'path': f'external_intel/portwatch/daily/{self.config.portwatch_id}/{day}.json',
                        'role': 'baseline', 'observed_start': day.isoformat() + 'T00:00:00Z',
                        'observed_end': (day + timedelta(days=1)).isoformat() + 'T00:00:00Z',
                        'available_at': None, 'source': 'IMF PortWatch / ArcGIS Daily_Chokepoints_Data',
                        'url': self.config.portwatch_url, 'portid': self.config.portwatch_id,
                        'release_assumption': '事后取得的历史日值，日窗结束模拟释放；历史首次发布时间未知'})
        ids = [resource['id'] for resource in resources]
        if len(ids) != len(set(ids)):
            raise ValueError('同案例资源编号不能重复')
        batches = {}
        for resource in resources:
            resource = deepcopy(resource)
            release = utc(resource.get('replay_release_at') or resource['observed_end'])
            if resource.get('available_at') and release < utc(resource['available_at']):
                raise ValueError('资源不能早于可信发布时间释放')
            if resource.get('kind') == 'gnss' and release < utc(resource['observed_end']):
                raise ValueError('GNSS 文件不能在观测窗口结束前释放')
            resource.update(case_id=case_id, registered_id=case_id + '--' + resource['id'],
                            replay_release_at=release.isoformat(), replay_start=definition['start_at'])
            metadata = next((station for station in definition.get('stations', [])
                             if station.get('station') == resource.get('station') or
                             station.get('id') == resource.get('station')), None)
            if metadata:
                resource['coordinates'] = metadata
            if resource.get('role') == 'baseline':
                if utc(resource['observed_start']) >= start or utc(resource['observed_end']) > start or release > start:
                    raise ValueError('参考资源必须完整位于回放起点之前')
            elif start <= utc(resource['observed_start']) < end and release <= end:
                # Daily files are released after their observation window, never at that day's midnight.
                day_end = release.replace(hour=0, minute=0, second=0, microsecond=0)
                if release != day_end:
                    day_end += timedelta(days=1)
                as_of = min(day_end, end).isoformat()
                batches.setdefault(as_of, []).append(resource['registered_id'])
            saved, _ = self.store.insert_once('replay_resource', dict(resource, id=resource['registered_id'],
                status='registered', material_id=None, imported_at=None))
            if resource['kind'] in ('gnss', 'portwatch'):
                prior_case = self.store.get('replay_case', case_id) or {}
                if prior_case.get('status') == 'completed' and saved.get('catalog_id') and self.acquisition.view(saved['catalog_id']):
                    continue
                consumer = self.consumer_id(case_id)
                managed = self.acquisition.require(resource, consumer, metadata={
                    'instance_id': self.instance_id, 'case_id': case_id, 'data_dir': str(self.config.data_dir.resolve()),
                    'role': resource.get('role'), 'registered_id': resource['registered_id'],
                    'legacy_cache_dir': str(self.config.data_dir / 'gnss_cache')})
                saved['catalog_id'] = managed['id']
                self.store.save('replay_resource', saved)
        definition = dict(definition, batches=[{'as_of': key, 'resource_ids': value}
                                             for key, value in sorted(batches.items())])
        self.definitions[case_id] = definition
        self.store.insert_once('replay_case', {
            'id': case_id, 'case_id': case_id, 'label': definition['label'], 'status': 'queued',
            'instance_id': self.instance_id, 'as_of': None, 'total_batches': len(batches),
            'released_batches': 0, 'completed_batches': 0, 'baseline_ready': False,
            'coverage': definition.get('coverage', {}), 'stations': definition.get('stations', []),
            'gnss_resources': sum(resource['kind'] == 'gnss' and resource.get('role') != 'baseline' for resource in resources),
            'processed_resources': 0, 'quality_status': 'not_run', 'created_at': now(), 'errors': [],
        })
        for source in ('gnss', 'portwatch', 'news', 'context'):
            identifier = self.monitor_id(case_id, source)
            self.store.insert_once('monitor', {'id': identifier, 'mode': 'case_replay',
                'pipeline_owned': True, 'source': source, 'scope_id': self.scope_id(case_id),
                'case_id': case_id, 'topic': definition['label'], 'enabled': False,
                'max_materials': 3, 'lookback_hours': 48, 'interval_minutes': 1440,
                'created_at': now(), 'last_success_at': None})

    def scope_id(self, case_id):
        return f'replay:{self.instance_id}:{case_id}'

    def consumer_id(self, case_id):
        return self.scope_id(case_id)

    def monitor_id(self, case_id, kind):
        return f'replay-{self.instance_id[:8]}-{case_id}-{kind}'

    def configure(self, changes):
        if set(changes) - {'paused'}:
            raise ValueError('回放输入由案例清单确定；此入口只暂停或恢复')
        return super().configure(changes)

    def resource_path(self, resource):
        base = ROOT if resource.get('root') == 'repo' else self.config.replay_asset_root
        path = (base / resource['path']).resolve()
        if not path.is_relative_to(base.resolve()):
            raise ValueError('资源路径超出已配置数据根目录')
        return path

    def resources(self, case_id, *, as_of=None, imported_only=False):
        return [resource for resource in self.store.all('replay_resource')
                if resource['case_id'] == case_id
                and (not imported_only or resource.get('material_id'))
                and (not as_of or utc(resource['replay_release_at']) <= utc(as_of))]

    def case_works(self, case_id):
        return [work for work in self.store.all('work') if work.get('case_id') == case_id
                and not work.get('superseded')]

    def recent_alerts(self, limit=50):
        return [alert for alert in super().recent_alerts(limit * 2) if not alert.get('superseded')
                and (not alert.get('case_id') or not alert.get('as_of') or
                     utc(alert['as_of']) <= utc(self.store.get('replay_case', alert['case_id']).get('as_of') or alert['as_of']))][:limit]

    def sync_resources(self, case):
        """A source/result change reopens the same instance; fetch timestamps do not."""
        available = {}
        for resource in self.resources(case['case_id']):
            if not resource.get('catalog_id'):
                continue
            state = self.acquisition.view(resource['catalog_id']) or {}
            if state.get('result_version') and state.get('compute_status') in ('completed', 'not_required'):
                available[resource['id']] = state['result_version']
        previous = case.get('available_versions', {})
        changed = [identifier for identifier, version in available.items() if previous.get(identifier) != version]
        if not changed or any(work['status'] == 'running' for work in self.case_works(case['case_id'])):
            return case
        definition = self.definitions[case['case_id']]
        changed_resources = [self.store.get('replay_resource', identifier) for identifier in changed]
        baseline_changed = any(resource.get('role') == 'baseline' for resource in changed_resources)
        first = 0 if baseline_changed else min(
            (index for index, batch in enumerate(definition['batches']) if set(changed) & set(batch['resource_ids'])),
            default=case.get('released_batches', 0))
        if not baseline_changed and first >= case.get('released_batches', 0):
            case['available_versions'] = available
            self.store.save('replay_case', case)
            return case
        first = min(first, case.get('released_batches', 0))
        if first < case.get('released_batches', 0):
            self.restore_before(case['case_id'], first)
        for work in self.case_works(case['case_id']):
            if work.get('batch_index', -1) >= first:
                work.update(superseded=True, superseded_at=now())
                if work['status'] in ('queued', 'retry_wait'):
                    work['status'] = 'superseded'
                self.store.save('work', work)
        case.update(available_versions=available, run_revision=case.get('run_revision', 0) + 1,
            released_batches=first, completed_batches=min(case.get('completed_batches', 0), first), baseline_ready=False,
            as_of=definition['batches'][first - 1]['as_of'] if first else definition['start_at'],
            status='preparing', quality_status='pending', completed_at=None, reason=None,
            revision_reason={'resource_ids': changed, 'from_batch': first, 'at': now()})
        self.store.save('replay_case', case)
        return case

    def restore_before(self, case_id, index):
        checkpoint = self.store.get('replay_checkpoint', f'{case_id}:{index - 1}') if index else None
        with self.store.atomic():
            for alert in self.store.all('alert'):
                if alert.get('case_id') != case_id or alert.get('superseded'):
                    continue
                self.store.save('alert_version', dict(deepcopy(alert), id=uuid4().hex, alert_id=alert['id']))
                alert.update(superseded=True, analysis_stale=True)
                self.store.save('alert', alert)
            for alert in (checkpoint or {}).get('alerts', []):
                self.store.save('alert', dict(alert, superseded=False))
            monitor_id = self.monitor_id(case_id, 'portwatch')
            if (checkpoint or {}).get('numeric_state'):
                self.store.save('monitor_state', checkpoint['numeric_state'])
            else:
                with self.store.connect() as db:
                    db.execute("DELETE FROM records WHERE kind='monitor_state' AND id=?", (monitor_id,))

    def checkpoint(self, case, index):
        self.store.save('replay_checkpoint', {'id': f"{case['case_id']}:{index}",
            'case_id': case['case_id'], 'as_of': case['as_of'], 'revision': case.get('run_revision', 0),
            'alerts': [deepcopy(alert) for alert in self.recent_alerts(200) if alert.get('case_id') == case['case_id']],
            'numeric_state': self.store.get('monitor_state', self.monitor_id(case['case_id'], 'portwatch'))})

    def case_summary(self, case):
        works = self.case_works(case['case_id'])
        resources = self.resources(case['case_id'])
        resource_counts = {status: sum(resource.get('status') == status for resource in resources)
                           for status in ('registered', 'waiting_resource', 'processed', 'missing', 'failed')}
        return dict(case, input_version=case.get('run_revision', 0), resource_counts=resource_counts,
            queued=sum(work['status'] in ('queued', 'running', 'retry_wait') for work in works),
            failed=sum(work['status'] == 'failed' for work in works),
            processed_resources=sum(resource.get('status') == 'processed' and resource['kind'] == 'gnss'
                                    and resource.get('role') != 'baseline' for resource in resources))

    def snapshot(self):
        payload = super().snapshot()
        cases = [self.case_summary(self.store.get('replay_case', case_id)) for case_id in self.case_order]
        analyzing = next((work.get('case_id') for work in self.store.all('work') if work['status'] == 'running'), None)
        current = next((case for case in cases if case['case_id'] == analyzing), None)
        current = current or next((case for case in cases if case['status'] in ('processing', 'analyzing')), None)
        current = current or next((case for case in cases if case['status'] not in ('completed', 'incomplete', 'blocked')), None)
        status = 'running' if current else 'completed' if cases and all(case['status'] == 'completed' for case in cases) else 'incomplete'
        payload.update(mode='case_replay', scope='历史 PNT/GNSS 观测质量自动回放',
            replay={'instance_id': self.instance_id, 'current_case_id': current['case_id'] if current else None,
                    'status': status, 'cases': cases},
            summary='历史批次自动回放中' if current else '两个案例处理已结束，请分别查看完成状态与输入缺口')
        if payload['paused']:
            payload['summary'] = '历史回放已暂停，保留当前批次与计算结果'
        payload['recent_work'] = [dict({key: work.get(key) for key in ('id', 'kind', 'status', 'created_at',
            'completed_at', 'next_retry_at', 'error', 'case_id', 'as_of', 'batch_id')},
            decision=work.get('assessment', {}).get('decision'), statement=work.get('assessment', {}).get('statement'))
            for work in self.store.recent('work', 12)]
        return payload

    async def schedule(self):
        while not self.stopping:
            if not self.running():
                await self.idle()
                continue
            for case_id in self.case_order:
                if case_id not in self.definitions:
                    continue
                case = self.sync_resources(self.store.get('replay_case', case_id))
                if case['status'] in ('completed', 'incomplete', 'blocked'):
                    continue
                try:
                    await self.advance(case)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    case = self.store.get('replay_case', case_id)
                    case.update(status='blocked', reason=str(error), updated_at=now(), quality_status='technical_failure')
                    self.store.save('replay_case', case)
            await self.idle()

    async def advance(self, case):
        case_id = case['case_id']
        definition = self.definitions[case_id]
        if not case['baseline_ready']:
            if case['status'] not in ('preparing', 'waiting_resources'):
                case.update(status='preparing', started_at=case.get('started_at') or now())
                self.store.save('replay_case', case)
            as_of = utc(definition['start_at']).isoformat()
            for resource in self.resources(case_id, as_of=as_of):
                if resource.get('role') == 'baseline':
                    await self.import_resource(case, resource, as_of)
            if not self.running():
                return
            baselines = [resource for resource in self.resources(case_id) if resource.get('role') == 'baseline']
            pending = [resource for resource in baselines if resource.get('status') == 'waiting_resource']
            case = self.store.get('replay_case', case_id)
            progress = dict(baseline_ready=not pending,
                baseline_status={'prepared': sum(resource.get('status') == 'processed' for resource in baselines),
                    'pending': len(pending), 'unavailable': sum(resource.get('status') in ('missing', 'failed') for resource in baselines)},
                as_of=as_of if not case.get('released_batches') else case.get('as_of'),
                status='waiting_resources' if pending else 'running')
            if any(case.get(key) != value for key, value in progress.items()):
                case.update(progress)
                self.store.save('replay_case', case)
            if pending:
                return
            if not case['released_batches']:
                self.prepare_numeric(case_id, as_of, baseline=True)
        works = self.case_works(case_id)
        failures = [work for work in works if work['status'] == 'failed']
        if failures:
            case.update(status='blocked', quality_status='technical_failure',
                        reason=failures[-1].get('error') or '分析失败，保存位置后处理下一例')
            self.store.save('replay_case', case)
            return
        if any(work['status'] != 'completed' for work in works):
            return
        if case.get('completed_batches', 0) < case['released_batches']:
            case['completed_batches'] = case['released_batches']
            self.checkpoint(case, case['released_batches'] - 1)
            self.store.save('replay_case', case)
        if case['released_batches'] == len(definition['batches']):
            self.finish_case(case)
            return
        batch_index = case['released_batches']
        batch = definition['batches'][batch_index]
        if case.get('as_of') != batch['as_of'] or case['status'] not in ('processing', 'waiting_resources'):
            case.update(status='processing', as_of=batch['as_of'])
            self.store.save('replay_case', case)
        for identifier in batch['resource_ids']:
            resource = self.store.get('replay_resource', identifier)
            await self.import_resource(case, resource, batch['as_of'])
        pending = [identifier for identifier in batch['resource_ids']
                   if self.store.get('replay_resource', identifier).get('status') == 'waiting_resource']
        if pending:
            case = self.store.get('replay_case', case_id)
            if case.get('status') != 'waiting_resources' or case.get('waiting_resources') != len(pending):
                case.update(status='waiting_resources', waiting_resources=len(pending))
                self.store.save('replay_case', case)
            return
        if not self.running():
            return
        with self.store.atomic():
            self.enqueue_batch(case_id, batch_index, batch)
            case = self.store.get('replay_case', case_id)
            case.update(status='analyzing', released_batches=batch_index + 1, as_of=batch['as_of'], updated_at=now())
            self.store.save('replay_case', case)
        self.wakeup.set()

    async def import_resource(self, case, resource, as_of):
        if not self.running():
            return
        identifier, case_id = resource['id'], case['case_id']
        path = self.resource_path(resource)
        try:
            if resource.get('catalog_id'):
                state = self.acquisition.view(resource['catalog_id']) or {}
                raw_version = state.get('result_version')
                if state.get('compute_status') not in ('completed', 'not_required') or not raw_version:
                    unavailable = state.get('status') in ('source_missing', 'auth_required') or state.get('compute_status') == 'failed'
                    progress = dict(status='failed' if unavailable else 'waiting_resource',
                        error=state.get('error'), acquisition_status=state.get('status'),
                        compute_status=state.get('compute_status'), next_retry_at=state.get('next_retry_at'))
                    if any(resource.get(key) != value for key, value in progress.items()):
                        resource.update(progress)
                        self.store.save('replay_resource', resource)
                    return
                baselines_version = sorted((r['id'], r.get('material_id')) for r in self.resources(case_id, as_of=as_of)
                                           if r.get('role') == 'baseline' and r.get('material_id') and r['kind'] == 'gnss')
                signature = [raw_version, baselines_version if resource.get('role') != 'baseline' else []]
                if resource.get('processing_signature') == json.loads(json.dumps(signature)) and resource.get('material_id'):
                    return
                raw = self.acquisition.result(resource['catalog_id'])
                if raw is None:
                    resource.update(status='waiting_resource', error='等待可复用结果或重新取得原件')
                    self.store.save('replay_resource', resource)
                    return
            elif resource.get('status') == 'processed':
                return
            else:
                raw, signature = None, None
            if resource['kind'] == 'gnss':
                from .gnss import contextualize_result
                baselines = self.gnss_results(case_id, as_of, baseline_only=True)
                computed = contextualize_result(raw, resource, as_of=as_of,
                    case_id=case_id, baseline_results=baselines)
                computed['input_revision'] = resource.get('input_revision', 0) + 1
                if computed.get('status') in ('failed', 'unavailable'):
                    raise ValueError(computed.get('error') or computed.get('summary') or 'GNSS 处理失败')
                text = json.dumps(dict(self.compact_gnss(computed), input_revision=computed['input_revision']), ensure_ascii=False)
                # Keep the plotted/cited signals; the complete raw feature array lives in the shared catalog.
                selected = {signal['signal'] for signal in self.compact_gnss(computed)['signals']}
                retained = dict(computed, series=[point for point in computed.get('series', []) if point.get('signal') in selected])
                payload = {'computed': retained}
            else:
                payload = raw if raw is not None else self.read_archive(resource, path, as_of)
                text = json.dumps(payload, ensure_ascii=False)
            data = {'title': resource.get('title') or f"{resource.get('station') or resource['kind']} · {resource['observed_start']}",
                'url': '/api/replay/resources/' + identifier, 'publisher': resource.get('source') or resource['kind'],
                'text': text, 'content_kind': 'computed_observation' if resource['kind'] == 'gnss' else 'archived_data',
                'source': resource['kind'], 'mode': 'case_replay', 'case_id': case_id, 'as_of': as_of,
                'resource_id': identifier, 'file_name': path.name, 'observed_at': resource['observed_start'],
                'available_at': resource.get('available_at'), 'replay_release_at': resource['replay_release_at'],
                'fetched_at': now(), 'time_note': resource.get('release_assumption') or '按已声明历史窗口模拟到达，非当年实时可用性证明',
                'analysis_status': 'pipeline', 'archive': payload}
            if resource['kind'] == 'gnss':
                data.update(self.gnss_material_presentation(computed, resource['replay_release_at']))
            if resource['kind'] == 'news':
                article = payload.get('article', payload)
                data.update(title=article.get('title') or data['title'], text=article.get('text') or '',
                            content_kind=article.get('content_kind') or ('article_text' if article.get('text') else 'title_only'),
                            original_url=article.get('url'), publisher=article.get('publisher') or article.get('domain') or data['publisher'])
            revision = resource.get('input_revision', 0) + 1
            run_id = f"import:{self.instance_id}:{identifier}:{revision}"
            with self.store.atomic():
                self.store.insert_once('run', {'id': run_id, 'monitor_id': self.monitor_id(case_id, resource['kind'] if resource['kind'] in ('gnss', 'portwatch', 'news') else 'context'),
                    'mode': 'case_replay_import', 'case_id': case_id, 'status': 'completed', 'started_at': now(),
                    'finished_at': now(), 'steps': [], 'as_of': as_of})
                material, _ = self.store.add_material(self.monitor_id(case_id, resource['kind'] if resource['kind'] in ('gnss', 'portwatch', 'news') else 'context'), run_id, data)
                resource.update(status='processed', material_id=material['id'], imported_at=now(), as_of=as_of,
                    error=None, processing_signature=signature, input_revision=revision,
                    catalog_version=raw_version if resource.get('catalog_id') else None)
                if resource['kind'] == 'gnss':
                    computed.update(id=identifier, material_id=material['id'], role=resource.get('role'),
                                    replay_release_at=resource['replay_release_at'])
                    retained.update(id=identifier, material_id=material['id'], role=resource.get('role'),
                                    replay_release_at=resource['replay_release_at'],
                                    catalog_version=raw_version)
                    self.store.save('gnss_result', retained)
                self.store.save('replay_resource', resource)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = str(error).replace(str(path), path.name).replace(str(self.config.replay_asset_root), '[数据根目录]')
            resource.update(status='missing' if isinstance(error, FileNotFoundError) else 'failed',
                            error=message, attempted_at=now())
            self.store.save('replay_resource', resource)
            saved = self.store.get('replay_case', case_id)
            saved['errors'].append({'resource_id': identifier, 'error': message})
            self.store.save('replay_case', saved)

    def read_archive(self, resource, path, as_of):
        if path.suffix.lower() == '.csv':
            with path.open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
            field = resource.get('date_field') or resource.get('time_field')
            if not field:
                raise ValueError('归档表必须声明可核查的时间字段')
            visible = [row for row in rows if row.get(field) and
                       utc(resource['observed_start']) <= utc(row[field]) < utc(resource['observed_end'])
                       and utc(row[field]) <= utc(as_of)]
            return {'rows': visible, 'units': resource.get('units'), 'source': resource.get('source')}
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
        # A manifest JSON resource is a dated record/slice, not a final report or whole future archive.
        if not isinstance(payload, dict):
            raise ValueError('历史 JSON 资源须为清单声明的单条观测或日期切片')
        return payload

    @staticmethod
    def gnss_material_presentation(computed, replay_release_at):
        return {'observed_at': computed['window_start'],
            'time_note': (f"RINEX已解析，原始时制为{computed['time_system']}；实际观测覆盖"
                          f"{computed['window_start']}至{computed['window_end']}（UTC），"
                          f"采样间隔{computed['interval_seconds']}秒。按已声明时点"
                          f"{replay_release_at}模拟释放；不代表当年实际获取或发布时间。")}

    def refresh_gnss_presentation(self, work):
        # Repair only the forthcoming input from its already cited immutable
        # material; keep its comparison, release time and prior attempts intact.
        if work.get('kind') != 'gnss' or work.get('status') == 'completed':
            return
        observations = {item['resource_id']: item
            for item in work['input'].get('program', {}).get('observations', [])}
        changed = False
        for material in work['input'].get('materials', []):
            saved = self.store.get_material(material['id'])
            computed = (saved or {}).get('archive', {}).get('computed')
            if not computed:
                continue
            presentation = self.gnss_material_presentation(computed, material.get('replay_release_at'))
            for key, value in presentation.items():
                if material.get(key) != value:
                    material[key], changed = value, True
            observation = observations.get(computed['resource_id'])
            if observation is None:
                continue
            if observation.get('status') != computed.get('status'):
                observation['status'], changed = computed.get('status'), True
            signals = {signal['signal']: signal for signal in computed.get('signals', [])}
            for signal in observation.get('signals', []):
                original = signals.get(signal['signal'])
                if original is None:
                    continue
                for key in ('strength_p10', 'unit_source'):
                    if key not in signal or signal[key] != original.get(key):
                        signal[key], changed = original.get(key), True
        if changed:
            self.store.save('work', work)

    @staticmethod
    def compact_gnss(result):
        compact = {key: result.get(key) for key in ('resource_id', 'station', 'window_start', 'window_end',
            'status', 'interval_seconds', 'time_system', 'display_time_system', 'processing_version', 'summary',
            'reference_method', 'parse_issues', 'notes')}
        signals = sorted(result.get('signals', []), key=lambda signal: signal['signal'])
        compact['signals'] = [{key: signal.get(key) for key in ('signal', 'unit', 'value_kind', 'cnr_p10',
            'strength_p10', 'unit_source', 'valid_count', 'valid_ratio', 'matched_window_count', 'baseline_window_p10_median',
            'delta_window_p10_median')} for signal in signals[:3]]
        compact['available_signals'] = [signal['signal'] for signal in signals]
        compact['other_signal_count'] = max(0, len(signals) - 3)
        compact['selection_note'] = '初始统计按信号码字母序展示，其他信号可用同站历史工具补查；未按异常大小筛选'
        return compact

    def gnss_results(self, case_id, as_of, baseline_only=False, full=True):
        saved = [result for result in self.store.all('gnss_result') if result.get('case_id') == case_id
                and utc(result['replay_release_at']) <= utc(as_of)
                and (not baseline_only or result.get('role') == 'baseline')]
        if not full:
            return saved
        from .gnss import contextualize_result
        baselines, observations = [], []
        for item in sorted(saved, key=lambda value: value.get('role') != 'baseline'):
            resource = self.store.get('replay_resource', item['id'])
            raw = self.acquisition.result(resource['catalog_id'], version=item.get('catalog_version')) if resource and resource.get('catalog_id') else None
            result = contextualize_result(raw, resource, as_of=as_of, case_id=case_id,
                         baseline_results=baselines) if raw else deepcopy(item)
            result.update(id=item['id'], resource_id=item['id'], material_id=item['material_id'],
                          replay_release_at=item['replay_release_at'], role=item.get('role'))
            (baselines if item.get('role') == 'baseline' else observations).append(result)
        return baselines + observations

    def prepare_numeric(self, case_id, as_of, baseline=False):
        visible = [resource for resource in self.resources(case_id, as_of=as_of, imported_only=True)
                   if resource['kind'] == 'portwatch']
        if not visible:
            return [], None
        monitor_id = self.monitor_id(case_id, 'portwatch')
        rows = []
        source_key = 'case_replay:' + self.scope_id(case_id) + ':portwatch'
        for resource in visible:
            material = self.store.get_material(resource['material_id'])
            payload = material['archive']
            attributes = payload.get('attributes', payload)
            day = resource['observed_start'][:10]
            rows.append({'observed_date': day, 'attributes': attributes,
                         'material': dict(material, text=json.dumps(attributes, ensure_ascii=False, sort_keys=True),
                                          content_kind='aggregate', analysis_status='not_required')})
        previous = self.store.get('monitor_state', monitor_id)
        before = [alert for alert in self.store.all('alert') if alert.get('monitor_id') == monitor_id]
        run_id = f'pw:{self.instance_id}:{case_id}:{as_of}'
        with self.store.atomic():
            self.store.upsert_portwatch(monitor_id, run_id, rows,
                                        source_key, self.config.portwatch_id, now())
            self.store.insert_once('run', {'id': run_id, 'monitor_id': monitor_id,
                'mode': 'case_replay_import', 'case_id': case_id, 'status': 'completed',
                'started_at': now(), 'finished_at': now(), 'steps': [], 'as_of': as_of,
                'summary': '写入已释放的 PortWatch 历史日记录'})
        current_rows = [row for row in self.store.portwatch_rows(source_key, self.config.portwatch_id)
                        if row['observed_date'] <= max(item['observed_date'] for item in rows)]
        before = [alert for alert in before if not alert.get('superseded') and
                  (not alert.get('as_of') or utc(alert['as_of']) <= utc(as_of))]
        output = evaluate(current_rows, previous, before, self.config.portwatch_rule, now(),
                          monitor_id, source_key, self.config.portwatch_id)
        old_by_id = {alert['id']: alert for alert in before}
        targets = []
        for alert in output['alerts']:
            old = old_by_id.get(alert['id'])
            if baseline and alert['status'] == 'resolved':
                continue
            changed = old is None or old['evidence_version'] != alert['evidence_version']
            alert = {**(old or {}), **alert}
            alert.update(case_id=case_id, mode='case_replay', as_of=as_of,
                         replay_release_at=as_of, scope_id=self.scope_id(case_id), origin_type='numeric_rule',
                         title='历史 PortWatch 可见通行量持续偏低提醒', observed_at=alert['last_evaluated_date'])
            if changed:
                alert.update(analysis_status='queued', analysis_stale=bool((old or {}).get('analysis')),
                    display_status='pending_analysis', published_at=(old or {}).get('published_at') or now(), updated_at=now())
                targets.append({'id': alert['id'], 'evidence_version': alert['evidence_version']})
            self.store.save('alert', alert)
        state = dict(output['state'], series=output['series'], case_id=case_id, as_of=as_of,
                     scope={'source_key': source_key, 'portid': self.config.portwatch_id})
        self.store.save('monitor_state', state)
        self.store.save('portwatch_source', {'id': monitor_id, 'source_key': source_key, 'portid': self.config.portwatch_id,
            'status': 'historical', 'error': None, 'last_checked_at': now(), 'last_success_at': now(),
            'range_start': min(row['observed_date'] for row in rows), 'range_end': max(row['observed_date'] for row in rows)})
        return targets, state

    def enqueue_batch(self, case_id, index, batch):
        as_of = batch['as_of']
        input_version = self.store.get('replay_case', case_id).get('run_revision', 0)
        resources = [self.store.get('replay_resource', identifier) for identifier in batch['resource_ids']]
        current = [resource for resource in resources if resource.get('material_id')]
        for kind in ('gnss', 'portwatch', 'news'):
            subset = [resource for resource in current if resource['kind'] == kind]
            if not subset:
                continue
            groups = ([[resource] for resource in subset] if kind == 'news' else
                      [subset[offset:offset + 3] for offset in range(0, len(subset), 3)] if kind == 'gnss' else [subset])
            for group_index, group in enumerate(groups):
                logical_id = f'replay:{self.instance_id}:{case_id}:{index}:{kind}:{group_index}'
                work_id = f'{logical_id}:v{input_version}:{self.config.pipeline_analysis_version}'
                materials = [material_input(self.store.get_material(resource['material_id'])) for resource in group]
                payload = {'id': work_id, 'kind': kind, 'mode': 'case_replay', 'case_id': case_id,
                           'as_of': as_of, 'materials': materials, 'origin': 'historical_replay',
                           'replay_release_at': as_of}
                targets = []
                if kind == 'gnss':
                    results = [result for result in self.gnss_results(case_id, as_of) if result['id'] in {r['id'] for r in group}]
                    payload.update(program={'observations': [self.compact_gnss(result) for result in results]},
                                   tools={'station_history': '本例已释放的同站同信号历史统计',
                                          'multistation_check': '本例已释放的同期多站统计'})
                    # Structured statistics replace raw arrays in the short model input.
                    for material in materials:
                        material['text'] = ''
                elif kind == 'portwatch':
                    targets, state = self.prepare_numeric(case_id, as_of)
                    payload['program'] = {key: state.get(key) for key in ('current_status', 'summary', 'latest_value',
                        'latest_observed_date', 'baseline', 'ratio', 'baseline_start', 'baseline_end', 'baseline_valid_days', 'rule')}
                    payload['program']['alerts'] = deepcopy(targets)
                    for material in materials:
                        material['text'] = ''
                    for target in targets:
                        alert = self.store.get('alert', target['id'])
                        alert['queued_work_id'] = work_id
                        self.store.save('alert', alert)
                work = {'id': work_id, 'scope_id': self.scope_id(case_id), 'monitor_id': self.monitor_id(case_id, kind),
                    'case_id': case_id, 'as_of': as_of, 'batch_index': index,
                    'batch_id': f'{case_id}-{index + 1}', 'mode': 'case_replay',
                    'input_version': input_version, 'logical_id': logical_id,
                    'kind': kind, 'source': kind, 'input': payload, 'material_ids': [item['id'] for item in materials],
                    'resource_ids': [resource['id'] for resource in group], 'numeric_alerts': targets,
                    'analysis_version': self.config.pipeline_analysis_version, 'status': 'queued',
                    'attempts': 0, 'created_at': now(), 'next_retry_at': now(), 'error': None}
                self.store.insert_once('work', work)

    def work_context_alerts(self, work):
        self.refresh_gnss_presentation(work)
        visible = [alert for alert in self.context_alerts(work['scope_id']) if alert.get('case_id') == work['case_id']
                   and utc(alert.get('as_of') or alert['replay_release_at']) <= utc(work['as_of'])]
        if work['kind'] == 'portwatch' and 'alerts' not in work['input'].get('program', {}):
            # Resume already queued inputs with the rule targets they originally
            # recorded, without admitting a newer alert version or recalculating.
            numeric = {alert['id']: alert for alert in visible if alert.get('origin_type') == 'numeric_rule'}
            work['input'].setdefault('program', {})['alerts'] = [deepcopy(target)
                for target in work.get('numeric_alerts', [])
                if target['id'] in numeric
                and target['evidence_version'] == numeric[target['id']].get('evidence_version')]
            self.store.save('work', work)
        return visible

    async def tool(self, name, args, batch, allowed, run):
        case_id, as_of = batch[0]['case_id'], batch[0]['as_of']
        visible = self.resources(case_id, as_of=as_of, imported_only=True)
        by_material = {resource['material_id']: resource for resource in visible}
        if name == 'read_material':
            identifier = args.get('material_id')
            if identifier not in by_material:
                return {'materials': [], 'error': '该材料不属于本案例当前 as_of 的可见版本'}
            material = self.store.get_material(identifier)
            allowed[identifier] = material_input(material)
            return {'materials': [allowed[identifier]], 'detail': '已释放的历史归档；没有访问在线来源'}
        if name == 'search_news':
            query = args.get('query', '').casefold().split()
            selected = []
            for resource in visible:
                if resource['kind'] not in ('news', 'gpsjam', 'space_weather'):
                    continue
                material = self.store.get_material(resource['material_id'])
                if query and not all(term in (material['title'] + ' ' + material['text']).casefold() for term in query):
                    continue
                selected.append(material_input(material))
            selected = selected[:3]
            allowed.update({material['id']: material for material in selected})
            return {'materials': selected, 'detail': '仅检索本案例已释放归档；未联网补查',
                    'coverage': {'as_of': as_of, 'case_id': case_id, 'complete': False}}
        from .gnss import station_history, multistation_check
        results = self.gnss_results(case_id, as_of)
        registered = {result.get('station') for result in results}
        if name == 'station_history':
            if args.get('station') not in registered:
                return {'materials': [], 'error': '站点尚未在本案例当前时点释放'}
            output = station_history(results, station=args['station'], signal=args.get('signal'), as_of=as_of)
        elif name == 'multistation_check':
            stations = args.get('stations') or list(registered)
            if any(station not in registered for station in stations):
                return {'materials': [], 'error': '只能比较本案例已释放的站点'}
            output = multistation_check(results, as_of=as_of, stations=stations, signal=args.get('signal'))
        else:
            return {'materials': [], 'error': '不支持的历史补查工具'}
        used = set(output.get('resource_ids', []))
        references = [result['material_id'] for result in results if result.get('material_id') and result['id'] in used]
        record = {'title': name + ' · ' + as_of, 'url': '/api/replay/tools/' + uuid4().hex,
            'text': json.dumps(output, ensure_ascii=False), 'content_kind': 'computed_observation',
            'source': 'gnss', 'case_id': case_id, 'mode': 'case_replay', 'as_of': as_of,
            'observed_at': as_of, 'available_at': None, 'replay_release_at': as_of,
            'fetched_at': now(), 'analysis_status': 'pipeline', 'tool_name': name,
            'source_material_ids': references, 'computed': output}
        saved, _ = self.store.add_material(self.monitor_id(case_id, 'gnss'), run['id'], record)
        material = material_input(saved)
        allowed[saved['id']] = material
        return {'materials': [material], 'result': output, 'detail': '根据本版处理的已释放观测计算',
                'case_id': case_id, 'as_of': as_of}

    async def analyze(self, batch):
        await super().analyze(batch)
        with self.store.atomic():
            for original in batch:
                work = self.store.get('work', original['id'])
                if work['status'] == 'retry_wait' and (work.get('isolated') or work['attempts'] >= self.config.replay_max_attempts):
                    work.update(status='failed', isolated=True, next_retry_at=None,
                                error='本例分析未完成，保留位置：' + (work.get('error') or '持续失败'))
                    self.store.save('work', work)
                    self.mark_numeric(work, 'failed')
                    for pending in self.case_works(work['case_id']):
                        if pending['status'] in ('queued', 'retry_wait'):
                            pending.update(status='failed', error='本例因持续分析故障停止推进，输入和位置已保留')
                            self.store.save('work', pending)
                            self.mark_numeric(pending, 'failed')
        for original in batch:
            work = self.store.get('work', original['id'])
            if work['status'] == 'completed':
                identifiers = [resource.get('catalog_id') for resource in self.resources(work['case_id'])
                               if resource['id'] in work.get('resource_ids', []) and resource.get('catalog_id')]
                if work.get('published_alert_ids'):
                    self.acquisition.mark_anomaly(identifiers)
                self.acquisition.release(self.consumer_id(work['case_id']), identifiers, pin=True,
                    evidence={'work_id': work['id'], 'case_id': work['case_id'], 'as_of': work['as_of'],
                        'program': work['input'].get('program'), 'assessment': work.get('assessment'),
                        'tools': work.get('tool_results'), 'resource_ids': identifiers})

    def publish(self, work, assessment, result, materials):
        if work['kind'] == 'portwatch':
            super().publish(work, assessment, result, materials)
        else:
            timestamp = now()
            work['published_alert_ids'] = []
            if assessment['decision'] in ('candidate', 'update'):
                prior = assessment.get('existing_alert_id')
                emission = self.store.get('replay_emission', work.get('logical_id', work['id']))
                retained_id = emission.get('alert_id') if emission else None
                alert = self.store.get('alert', prior) if prior else None
                expected_type = 'gnss_observation' if work['kind'] == 'gnss' else 'news_clue'
                if prior and (not alert or alert.get('case_id') != work['case_id'] or
                              alert.get('origin_type') != expected_type or utc(alert['as_of']) > utc(work['as_of'])):
                    raise ValueError('更新对象必须是本案例、本类证据及当前时点可见的异常')
                if not alert:
                    old_version = self.store.get('alert', retained_id) if retained_id else None
                    alert = {'id': retained_id or 'case-alert-' + uuid4().hex, 'origin_type': expected_type,
                        'scope_id': work['scope_id'], 'monitor_id': work['monitor_id'], 'case_id': work['case_id'],
                        'mode': 'case_replay', 'detected_at': timestamp, 'published_at': timestamp,
                        'origin': 'historical_replay', 'analysis_history': [],
                        'evidence_version': (old_version or {}).get('evidence_version', 0)}
                if alert.get('analysis'):
                    alert['analysis_history'].append(dict(alert['analysis'], evidence_refs=alert.get('evidence_refs')))
                status = assessment.get('observation_status' if work['kind'] == 'gnss' else 'news_status') or 'active'
                analysis = dict(assessment, model=result['model'], completed_at=timestamp,
                                work_id=work['id'], analysis_version=work['analysis_version'],
                                case_id=work['case_id'], as_of=work['as_of'])
                alert.update(title=assessment['title'], summary=assessment['statement'], status=status,
                    display_status='resolved' if status == 'resolved' else 'revised' if prior else 'new',
                    analysis=analysis, analysis_status='completed', analysis_stale=False,
                    evidence_refs=[material_ref(materials[identifier]) for identifier in assessment['evidence_refs']],
                    resource_ids=work['resource_ids'], evidence_version=alert['evidence_version'] + 1,
                    updated_at=timestamp, read_at=None, as_of=work['as_of'], replay_release_at=work['as_of'],
                    observed_at=work['input']['materials'][0].get('observed_at'),
                    available_at=work['input']['materials'][0].get('available_at'),
                    first_seen_at=work['input']['materials'][0].get('first_seen_at'), superseded=False)
                self.store.save('alert', alert)
                self.store.save('replay_emission', {'id': work.get('logical_id', work['id']), 'alert_id': alert['id']})
                work['published_alert_ids'].append(alert['id'])
                current = self.store.get('pipeline', 'main')
                current['last_published_at'] = timestamp
                self.store.save('pipeline', current)
            work.update(status='completed', completed_at=timestamp, error=None,
                assessment=dict(assessment, model=result['model'], completed_at=timestamp, work_id=work['id']),
                tool_results=result['tool_results'], attempts_detail=result['attempts'])
            self.store.save('work', work)
        work['result_quality'] = result.get('quality_status', 'assessed')
        self.store.save('work', work)

    def finish_case(self, case):
        resources = self.resources(case['case_id'])
        works = self.case_works(case['case_id'])
        failures = [resource for resource in resources if resource.get('status') in ('missing', 'failed')]
        observed = [resource for resource in resources if resource['kind'] == 'gnss' and resource.get('role') != 'baseline'
                    and resource.get('status') == 'processed']
        technical = [work for work in works if work.get('result_quality') == 'budget_exhausted']
        missing = []
        results = self.gnss_results(case['case_id'], case['as_of'], full=False) if case.get('as_of') else []
        if not any(result.get('role') != 'baseline' and result.get('status') == 'computed' for result in results):
            missing.append('尚无本版程序从原始 GNSS 文件得到的有效观测统计')
        if observed and not any(result.get('role') != 'baseline' and
                               result.get('summary', {}).get('reference_status') == 'available' for result in results):
            missing.append('尚无同站、同信号及可比 UTC 时段的 GNSS 事前参考')
        if any(resource['kind'] == 'portwatch' for resource in resources):
            start = utc(self.definitions[case['case_id']]['start_at']).date()
            reference_start = start - timedelta(days=self.config.portwatch_baseline_days)
            rows = self.store.portwatch_rows('case_replay:' + self.scope_id(case['case_id']) + ':portwatch', self.config.portwatch_id)
            valid = [row for row in rows if reference_start.isoformat() <= row['observed_date'] < start.isoformat()
                     and row.get('validity') == 'valid']
            if len(valid) < self.config.portwatch_min_baseline_days:
                missing.append(f'PortWatch 回放起点前只有 {len(valid)} 个有效参考日，规则要求至少 {self.config.portwatch_min_baseline_days} 日')
        pending = [resource for resource in resources if resource.get('status') in ('waiting_resource', 'registered')]
        complete = bool(observed) and not missing and not failures and not pending and bool(works) and not technical
        runs = [run for run in self.store.all('run') if run.get('case_id') == case['case_id']
                and run.get('mode') == 'pipeline_analysis']
        model_requests = sum(len(step.get('detail', {}).get('attempts', [])) for run in runs for step in run.get('steps', []))
        tool_calls = sum(tool.get('name') in ('station_history', 'multistation_check')
                         for run in runs for step in run.get('steps', [])
                         for tool in step.get('detail', {}).get('tools', []))
        case.update(status='completed' if complete else 'incomplete', completed_at=now(),
            quality_status=('evidence_limited' if complete and all(work.get('assessment', {}).get('decision') == 'insufficient_evidence' for work in works)
                            else 'assessed' if complete else 'technical_failure' if technical else 'input_incomplete'),
            completed_batches=case['released_batches'],
            reason=None if complete else '所需输入、可比较基线或有效分析尚未齐备；见覆盖与处理记录',
            runtime_missing=missing, failed_resources=len(failures), model_requests=model_requests,
            professional_tool_calls=tool_calls, dynamic_tools_status='observed' if tool_calls else 'not_demonstrated',
            processed_inputs=sum(work['status'] == 'completed' for work in works))
        self.store.save('replay_case', case)
        if complete:
            self.acquisition.release(self.consumer_id(case['case_id']), pin=False,
                evidence={'case_id': case['case_id'], 'instance_id': self.instance_id,
                    'as_of': case['as_of'], 'quality_status': case['quality_status'],
                    'gnss': results, 'baseline': case.get('baseline_status'),
                    'alerts': [alert for alert in self.recent_alerts(200) if alert.get('case_id') == case['case_id']]})

    def case_view(self, case_id):
        case = self.store.get('replay_case', case_id)
        if not case:
            return None
        as_of = case.get('as_of')
        series, summaries = [], []
        if as_of:
            for result in self.gnss_results(case_id, as_of, full=False):
                if result.get('role') == 'baseline':
                    continue
                summaries.append(self.compact_gnss(result))
                for point in result.get('series', []):
                    stamp = point.get('time') or point.get('bin_start') or point.get('window_start')
                    if stamp and utc(stamp) <= utc(as_of):
                        series.append(dict(point, time=stamp, station=result['station'], material_id=result['material_id'],
                                           resource_id=result['id'], interval_seconds=result.get('interval_seconds'),
                                           processing_version=result.get('processing_version'), time_system=result.get('time_system')))
        works = sorted(self.case_works(case_id), key=lambda work: work['created_at'], reverse=True)
        return dict(self.case_summary(case), gnss={'series': series, 'summary': summaries, 'units': 'per_signal'},
            recent_work=[dict({key: work.get(key) for key in ('id', 'kind', 'status', 'created_at', 'completed_at',
                'next_retry_at', 'error', 'case_id', 'as_of', 'batch_index', 'batch_id', 'input_version')},
                decision=work.get('assessment', {}).get('decision'), statement=work.get('assessment', {}).get('statement'))
                for work in works[:40]],
            portwatch_monitor_id=self.monitor_id(case_id, 'portwatch'),
            alerts=[alert_summary(alert) for alert in self.recent_alerts(100) if alert.get('case_id') == case_id])

    def resource_view(self, identifier):
        resource = self.store.get('replay_resource', identifier)
        if not resource:
            return None
        case = self.store.get('replay_case', resource['case_id'])
        if not case.get('as_of') or utc(resource['replay_release_at']) > utc(case['as_of']):
            return None
        return {key: resource.get(key) for key in ('id', 'case_id', 'kind', 'source', 'station', 'observed_start',
            'observed_end', 'available_at', 'replay_release_at', 'release_assumption', 'interval_seconds',
            'status', 'material_id', 'imported_at', 'error')} | {'file_name': Path(resource['path']).name,
                'filename': Path(resource['path']).name, 'url': resource.get('url'),
                'time_note': resource.get('release_assumption'),
                'processing_version': 'rinex-cnr-window-v1' if resource['kind'] == 'gnss' else None,
                'catalog_id': resource.get('catalog_id'), 'input_version': resource.get('input_revision'),
                'raw_deleted_at': ((self.acquisition.view(resource['catalog_id']) or {}).get('raw_deleted_at')
                                   if resource.get('catalog_id') else None)}
