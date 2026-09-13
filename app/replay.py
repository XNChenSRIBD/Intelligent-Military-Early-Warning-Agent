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
from .replay_decisions import RISK_OBJECTS, OBJECT_LABELS, SPATIAL_LIMITS, build_decision, successful_tools
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
                    'label': case_id, 'status': 'blocked', 'reason': f'输入清单无法读取：{path.name}；{type(error).__name__}：{error}',
                    'registration_error': str(error),
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
            product_start = utc(definition.get('product_starts', {}).get(resource['kind'], definition['start_at']))
            resource.update(case_id=case_id, registered_id=case_id + '--' + resource['id'],
                            replay_release_at=release.isoformat(), replay_start=product_start.isoformat())
            metadata = next((station for station in definition.get('stations', [])
                             if station.get('station') == resource.get('station') or
                             station.get('id') == resource.get('station')), None)
            if metadata:
                resource['coordinates'] = metadata
            if resource.get('role') == 'baseline':
                if utc(resource['observed_start']) >= product_start or utc(resource['observed_end']) > product_start or release > product_start:
                    raise ValueError('参考资源必须完整位于回放起点之前')
            elif start <= utc(resource['observed_start']) < end and release <= end:
                # The file's declared release is the visible cutoff; 15-minute
                # products must not acquire an artificial wait to UTC midnight.
                as_of = release.isoformat()
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
        registered_case = {
            'id': case_id, 'case_id': case_id, 'label': definition['label'], 'status': 'queued',
            'instance_id': self.instance_id, 'as_of': None, 'total_batches': len(batches),
            'released_batches': 0, 'completed_batches': 0, 'baseline_ready': False,
            'coverage': definition.get('coverage', {}), 'stations': definition.get('stations', []),
            'primary_risk_object': definition.get('primary_risk_object') or
                                   ('maritime_visible_flow' if any(r['kind'] == 'portwatch' for r in resources)
                                    else 'gnss_observation_quality'),
            'product_starts': definition.get('product_starts', {}),
            'gnss_resources': sum(resource['kind'] == 'gnss' and resource.get('role') != 'baseline' for resource in resources),
            'processed_resources': 0, 'quality_status': 'not_run', 'created_at': now(), 'errors': [],
        }
        saved_case, created = self.store.insert_once('replay_case', registered_case)
        if not created and saved_case.get('status') == 'blocked' and (saved_case.get('registration_error') or
                '输入清单无法读取' in (saved_case.get('reason') or '')):
            # A manifest registration failure never had a valid batch plan.
            # Resume that same instance when the corrected plan can register.
            registered_case.update(created_at=saved_case.get('created_at', registered_case['created_at']),
                registration_errors=list(saved_case.get('registration_errors', [])) +
                    [{'reason': saved_case.get('reason'), 'recovered_at': now()}])
            self.store.save('replay_case', registered_case)
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

    def resume_failed_case(self, case_id, reason, work_ids=None):
        case = self.store.get('replay_case', case_id)
        if not case or case_id not in self.definitions:
            raise ValueError('该案例尚未成功注册')
        if work_ids:
            return self.revise_incomplete_interpretations(case, work_ids, reason)
        failed = [work for work in self.case_works(case_id) if work['status'] == 'failed']
        if not failed:
            raise ValueError('该案例没有需要续接的失败工作')
        if any(work['status'] == 'running' for work in self.case_works(case_id)):
            raise ValueError('该案例仍有正在执行的判断，不能同时重开')
        stamp = now()
        with self.store.atomic():
            for work in failed:
                work.setdefault('resume_history', []).append({'at': stamp, 'reason': reason,
                    'attempts_before': work.get('attempts', 0), 'run_id': work.get('run_id'),
                    'previous_error': work.get('error')})
                work.update(status='queued', error=None, next_retry_at=stamp, isolated=False,
                            retry_attempt_origin=work.get('attempts', 0))
                self.store.save('work', work)
                self.mark_numeric(work, 'queued')
            case.setdefault('resume_history', []).append({'at': stamp, 'reason': reason,
                'previous_reason': case.get('reason'), 'work_ids': [work['id'] for work in failed]})
            case.update(status='analyzing', quality_status='pending', reason=None, completed_at=None)
            self.store.save('replay_case', case)
            state = self.store.get('pipeline', 'main')
            state['model_retry_at'] = None
            self.store.save('pipeline', state)
        self.wakeup.set()
        return {'case_id': case_id, 'resumed_work_ids': [work['id'] for work in failed],
                'as_of': case.get('as_of'), 'reason': reason}

    def revise_incomplete_interpretations(self, case, work_ids, reason):
        selected = [self.store.get('work', identifier) for identifier in dict.fromkeys(work_ids)]
        numeric_snapshots = {}
        for work in selected:
            if (not work or work.get('case_id') != case['case_id'] or work.get('superseded')
                    or work.get('status') != 'completed' or work.get('kind') not in RISK_OBJECTS):
                raise ValueError('本入口只修订明确指定的已完成风险对象判断，须注明实际解释问题')
            if work.get('numeric_alerts'):
                if work['kind'] != 'portwatch':
                    raise ValueError('数值提醒的解释修订只适用于已保存的 PortWatch 规则快照')
                numeric_snapshots[work['id']] = self.frozen_numeric_alerts(work)
            alerts = set(work.get('published_alert_ids') or [])
            if alerts and work['id'] not in numeric_snapshots:
                if work.get('kind') != 'gnss':
                    raise ValueError('本解释入口不能修订单独的数值规则提醒')
                dependencies = [item['id'] for item in self.case_works(case['case_id'])
                    if item.get('status') == 'completed' and item['id'] not in work_ids
                    and item.get('assessment', {}).get('existing_alert_id') in alerts]
                if dependencies:
                    raise ValueError('须同时指定真实使用了原异常的后续判断：' + '；'.join(dependencies))
            if any(item.get('interpretation_of') == work['id'] and not item.get('superseded')
                   for item in self.case_works(case['case_id'])):
                raise ValueError('该工作已有解释修订，等待原修订续接即可')
        stamp, ids = now(), []
        with self.store.atomic():
            for original in selected:
                revision = original.get('interpretation_revision', 0) + 1
                identifier = original['id'] + f':interpretation-r{revision}'
                work = deepcopy(original)
                for key in ('assessment', 'attempts_detail', 'tool_results', 'result_quality', 'run_id',
                            'started_at', 'completed_at', 'published_alert_ids', 'resume_history',
                            'interpretation_numeric_results', 'interpretation_numeric_alert_changes'):
                    work.pop(key, None)
                work.update(id=identifier, interpretation_of=original['id'], interpretation_revision=revision,
                    interpretation_reason=reason, status='queued', attempts=0, created_at=stamp,
                    next_retry_at=stamp, error=None, isolated=False, retry_attempt_origin=0)
                work['interpretation_supersedes_alert_ids'] = ([] if original['id'] in numeric_snapshots
                    else list(original.get('published_alert_ids') or []))
                if original['id'] in numeric_snapshots:
                    work['interpretation_numeric_alerts'] = deepcopy(numeric_snapshots[original['id']])
                    work['interpretation_mode'] = 'frozen_numeric_explanation'
                    for alert in work['interpretation_numeric_alerts']:
                        self.store.save('alert_version', dict(deepcopy(alert), id=uuid4().hex,
                            alert_id=alert['id'], interpretation_revision_work_id=identifier,
                            version_role='before_interpretation', physical_recovery=False))
                for alert_id in work['interpretation_supersedes_alert_ids']:
                    alert = self.store.get('alert', alert_id)
                    if alert:
                        self.store.save('alert_version', dict(deepcopy(alert), id=uuid4().hex,
                            alert_id=alert_id, interpretation_revision_work_id=identifier))
                        alert.update(superseded=True, analysis_stale=True, interpretation_pending_work_id=identifier,
                                     interpretation_reason=reason)
                        self.store.save('alert', alert)
                work['input']['id'] = identifier
                work['input']['interpretation_revision'] = revision
                self.store.insert_once('work', work)
                ids.append(identifier)
            case.setdefault('interpretation_revisions', []).append({'at': stamp, 'reason': reason,
                'original_work_ids': work_ids, 'revision_work_ids': ids, 'as_of_unchanged': case.get('as_of')})
            if case['status'] in ('completed', 'incomplete'):
                case.update(status='analyzing', completed_at=None)
            self.store.save('replay_case', case)
        self.wakeup.set()
        return {'case_id': case['case_id'], 'revision_work_ids': ids,
                'as_of': case.get('as_of'), 'analysis_version_unchanged': True,
                'evidence_version_unchanged': True, 'reason': reason}

    def frozen_numeric_alerts(self, work):
        decision_id = 'decision:' + work['id']
        snapshots = sorted([item for item in self.store.all('replay_snapshot')
            if item.get('case_id') == work['case_id'] and item['as_of'] == work['as_of']
            and decision_id in item.get('decision_ids', {}).values()],
            key=lambda item: item['published_at'], reverse=True)
        frozen = []
        for target in work['numeric_alerts']:
            alert = next((alert for snapshot in snapshots for alert in snapshot.get('alerts', [])
                if alert['id'] == target['id'] and alert.get('evidence_version') == target['evidence_version']
                and alert.get('origin_type') == 'numeric_rule'), None)
            if alert is None:
                raise ValueError('该判断缺少对应版本的冻结数值告警，不能用当前告警替代历史解释')
            frozen.append(deepcopy(alert))
        return frozen

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
                if resource.get('kind') == 'maritime_history':
                    path = self.resource_path(resource)
                    if path.exists():
                        archive = json.loads(path.read_text(encoding='utf-8-sig'))
                        references = archive.get('source_resource_references', [])
                        if archive.get('status') == 'computed' and references:
                            # These versions belong to the actually materialized
                            # reference values, not a later catalog state.
                            available[resource['id']] = {'source_versions': sorted(
                                [item['catalog_id'], item.get('result_version')] for item in references)}
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
        context_changes = [resource for resource in changed_resources if resource.get('kind') == 'maritime_history']
        for resource in context_changes:
            if resource.get('material_id'):
                resource.setdefault('previous_material_ids', []).append(resource['material_id'])
            resource.update(status='registered', material_id=None, archive_signature=available[resource['id']])
            self.store.save('replay_resource', resource)
        baseline_changed = any(resource.get('role') == 'baseline' or
            (resource in context_changes and utc(resource['replay_release_at']) <= utc(definition['start_at']))
            for resource in changed_resources)
        first = 0 if baseline_changed else min(
            (index for index, batch in enumerate(definition['batches']) if set(changed) & set(batch['resource_ids'])
             or any(utc(resource['replay_release_at']) <= utc(batch['as_of']) for resource in context_changes)),
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
            for kind in ('replay_decision', 'replay_snapshot'):
                for record in self.store.all(kind):
                    if record.get('case_id') == case_id and record.get('batch_index', -1) >= index and not record.get('superseded'):
                        record.update(superseded=True, superseded_at=now())
                        self.store.save(kind, record)
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
                if resource.get('role') in ('baseline', 'context'):
                    await self.import_resource(case, resource, as_of)
                    await asyncio.sleep(0)
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
            if not self.store.get('monitor_state', self.monitor_id(case_id, 'portwatch')):
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
            self.complete_decision_batch(case, case['released_batches'] - 1)
            self.checkpoint(case, case['released_batches'] - 1)
            self.store.save('replay_case', case)
        if case['released_batches'] == len(definition['batches']):
            self.finish_case(case)
            return
        batch_index = case['released_batches']
        batch = definition['batches'][batch_index]
        # A later product start can have its own legitimate pre-observation
        # reference (e.g. GNSS 02-26 during the maritime replay begun 02-23).
        for resource in self.resources(case_id, as_of=batch['as_of']):
            if resource.get('role') == 'baseline':
                await self.import_resource(case, resource, batch['as_of'])
        if case.get('as_of') != batch['as_of'] or case['status'] not in ('processing', 'waiting_resources'):
            case.update(status='processing', as_of=batch['as_of'])
            self.store.save('replay_case', case)
        for identifier in batch['resource_ids']:
            resource = self.store.get('replay_resource', identifier)
            await self.import_resource(case, resource, batch['as_of'])
            await asyncio.sleep(0)
        pending = [identifier for identifier in batch['resource_ids']
                   if self.store.get('replay_resource', identifier).get('status') == 'waiting_resource']
        if pending:
            pending_kinds = {self.store.get('replay_resource', identifier)['kind'] for identifier in pending}
            pending_kinds.update(resource['kind'] for resource in self.resources(case_id, as_of=batch['as_of'])
                                 if resource.get('role') == 'baseline' and resource.get('status') == 'waiting_resource')
            # Publish ready objects at this same cutoff while another source
            # waits. The batch is only complete once all resources settle.
            await self.enqueue_ready_batch(case_id, batch_index, batch, skip_kinds=pending_kinds)
            case = self.store.get('replay_case', case_id)
            if case.get('status') != 'waiting_resources' or case.get('waiting_resources') != len(pending):
                case.update(status='waiting_resources', waiting_resources=len(pending))
                self.store.save('replay_case', case)
            return
        pending_baseline_kinds = {resource['kind'] for resource in self.resources(case_id, as_of=batch['as_of'])
                                  if resource.get('role') == 'baseline' and resource.get('status') == 'waiting_resource'}
        if pending_baseline_kinds & {self.store.get('replay_resource', identifier)['kind'] for identifier in batch['resource_ids']}:
            await self.enqueue_ready_batch(case_id, batch_index, batch, skip_kinds=pending_baseline_kinds)
            return
        if not self.running():
            return
        await self.enqueue_ready_batch(case_id, batch_index, batch)
        with self.store.atomic():
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
                raw, signature = None, resource.get('archive_signature')
            if resource['kind'] == 'gnss':
                from .gnss import contextualize_result
                baselines = await self.gnss_results_async(case_id, as_of, baseline_only=True, stations=[resource['station']])
                computed = await asyncio.to_thread(contextualize_result, raw, resource, as_of=as_of,
                    case_id=case_id, baseline_results=baselines)
                computed['input_revision'] = resource.get('input_revision', 0) + 1
                if computed.get('status') in ('failed', 'unavailable'):
                    raise ValueError(computed.get('error') or computed.get('summary') or 'GNSS 处理失败')
                text = json.dumps(dict(self.compact_gnss(computed), input_revision=computed['input_revision']), ensure_ascii=False)
                # Keep the plotted/cited signals; the complete raw feature array lives in the shared catalog.
                selected = set(sorted(signal['signal'] for signal in computed.get('signals', []))[:3])
                retained = dict(computed, series=[point for point in computed.get('series', []) if point.get('signal') in selected])
                payload = {'computed': retained}
            else:
                payload = raw if raw is not None else self.read_archive(resource, path, as_of)
                text = json.dumps(payload, ensure_ascii=False)
            imported_at = now()
            acquisition_times = self.resource_times(resource, payload, imported_at)
            data = {'title': resource.get('title') or f"{resource.get('station') or resource['kind']} · {resource['observed_start']}",
                'url': '/api/replay/resources/' + identifier, 'publisher': resource.get('source') or resource['kind'],
                'text': text, 'content_kind': 'computed_observation' if resource['kind'] == 'gnss' else 'archived_data',
                'source': resource['kind'], 'mode': 'case_replay', 'case_id': case_id, 'as_of': as_of,
                'resource_id': identifier, 'file_name': path.name, 'observed_at': resource['observed_start'],
                'available_at': resource.get('available_at'), 'replay_release_at': resource['replay_release_at'],
                **acquisition_times, 'time_note': resource.get('release_assumption') or '按已声明历史窗口模拟到达，非当年实时可用性证明',
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
                resource.update(status='processed', material_id=material['id'], imported_at=imported_at, as_of=as_of,
                    system_acquired_at=acquisition_times['system_acquired_at'],
                    catalog_registered_at=acquisition_times['catalog_registered_at'],
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

    def resource_times(self, resource, payload=None, imported_at=None):
        catalog = (self.acquisition.view(resource['catalog_id']) or {}) if resource.get('catalog_id') else {}
        acquired = resource.get('snapshot_fetched_at')
        basis = 'archived_snapshot_fetched_at' if acquired else 'unknown'
        if not acquired and catalog.get('fetched_at') and catalog.get('downloaded'):
            acquired, basis = catalog['fetched_at'], 'catalog_download_record'
        references = (payload or {}).get('source_resource_references', [])
        if not acquired and references and all(item.get('fetched_at') for item in references):
            acquired = max((item['fetched_at'] for item in references), key=utc)
            basis = 'all_source_records_acquired'
        return {'system_acquired_at': acquired, 'first_seen_at': acquired,
                'fetched_at': acquired, 'acquisition_time_basis': basis,
                'catalog_registered_at': catalog.get('created_at'),
                'reuse_confirmed_at': catalog.get('fetched_at') if not catalog.get('downloaded') else None,
                'imported_at': imported_at or resource.get('imported_at'),
                'acquisition_time_note': '首次取得仅使用下载或既存快照记录；原件复用登记时间单列，不代替原始取得或历史发布时间。'}

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
        program = work['input'].setdefault('program', {})
        if 'environment_context' not in program:
            context = self.gfz_environment_context(work['input'])
            if context:
                self.store.insert_once('replay_input_revision', {
                    'id': work['id'] + ':environment-context-v1', 'work_id': work['id'],
                    'case_id': work['case_id'], 'as_of': work['as_of'], 'created_at': now(),
                    'reason': '补入原快照目录已有的 GFZ 实际指标；保留此前模型输入',
                    'input': deepcopy(work['input']),
                    'source_material_ids': [row['material_id'] for row in context['rows']]})
                program['environment_context'] = context
                work.setdefault('supporting_material_ids', []).extend(
                    row['material_id'] for row in context['rows']
                    if row['material_id'] not in work.get('supporting_material_ids', []))
                work['input'].setdefault('program_archive_reads', []).append({
                    'source': 'GFZ', 'material_ids': [row['material_id'] for row in context['rows']]})
                self.store.save('work', work)
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
        from .gnss import compact_gnss
        return compact_gnss(result)

    def gnss_results(self, case_id, as_of, baseline_only=False, full=True, stations=None):
        selected_stations = set(stations) if stations is not None else None
        saved = [result for result in self.store.all('gnss_result') if result.get('case_id') == case_id
                and utc(result['replay_release_at']) <= utc(as_of)
                and (selected_stations is None or result.get('station') in selected_stations)
                and (not baseline_only or result.get('role') == 'baseline')]
        if not full:
            return saved
        from .gnss import contextualize_result
        baselines, observations = [], []
        for item in sorted(saved, key=lambda value: value.get('role') != 'baseline'):
            resource = self.store.get('replay_resource', item['id'])
            raw = self.acquisition.result(resource['catalog_id'], version=item.get('catalog_version')) if resource and resource.get('catalog_id') else None
            same_station = [baseline for baseline in baselines if baseline.get('station') == item.get('station')]
            result = contextualize_result(raw, resource, as_of=as_of, case_id=case_id,
                         baseline_results=same_station) if raw else deepcopy(item)
            result.update(id=item['id'], resource_id=item['id'], material_id=item['material_id'],
                          replay_release_at=item['replay_release_at'], role=item.get('role'))
            (baselines if item.get('role') == 'baseline' else observations).append(result)
        return baselines + observations

    async def gnss_results_async(self, case_id, as_of, baseline_only=False, stations=None):
        """Keep SQLite on the event-loop thread; compare cached arrays off-loop."""
        selected_stations = set(stations) if stations is not None else None
        saved = [result for result in self.store.all('gnss_result') if result.get('case_id') == case_id
                 and utc(result['replay_release_at']) <= utc(as_of)
                 and (selected_stations is None or result.get('station') in selected_stations)
                 and (not baseline_only or result.get('role') == 'baseline')]
        from .gnss import contextualize_result
        baselines, observations = [], []
        for item in sorted(saved, key=lambda value: value.get('role') != 'baseline'):
            resource = self.store.get('replay_resource', item['id'])
            raw = self.acquisition.result(resource['catalog_id'], version=item.get('catalog_version')) if resource and resource.get('catalog_id') else None
            same_station = [baseline for baseline in baselines if baseline.get('station') == item.get('station')]
            result = (await asyncio.to_thread(contextualize_result, raw, resource, as_of=as_of, case_id=case_id,
                                             baseline_results=same_station)) if raw else deepcopy(item)
            result.update(id=item['id'], resource_id=item['id'], material_id=item['material_id'],
                          replay_release_at=item['replay_release_at'], role=item.get('role'))
            (baselines if item.get('role') == 'baseline' else observations).append(result)
            await asyncio.sleep(0)
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

    def archive_index(self, case_id, as_of, kind):
        selected, comparators = [], []
        visible = self.resources(case_id, as_of=as_of, imported_only=True)
        for resource in sorted(visible, key=lambda item: item['replay_release_at'], reverse=True):
            if resource['kind'] not in (('space_weather', 'gpsjam') if kind == 'gnss' else ('portwatch', 'maritime_history')):
                continue
            material = self.store.get_material(resource['material_id'])
            entry = {key: material.get(key) for key in ('id', 'title', 'source', 'observed_at', 'available_at')}
            (comparators if resource['kind'] == 'maritime_history' else selected).append(entry)
        # Recent environmental products and a finite daily-table lookup index
        # are enough to select a real read; full GNSS features use its tools.
        return selected[:3] if kind == 'gnss' else selected[:5] + comparators[:2]

    def gfz_environment_context(self, payload):
        # Use only IDs already frozen in this judgment's visible archive index.
        # Daily global indicators provide context, not a station-level cause.
        if not payload.get('observation_start') or not payload.get('observation_end'):
            return None
        start = utc(payload['observation_start'])
        end = utc(payload['observation_end'])
        first_day = start.date() - timedelta(days=1)
        last_day = (end - timedelta(microseconds=1)).date()
        rows, archive = [], None
        for entry in payload.get('program', {}).get('archive_index', []):
            material = self.store.get_material(entry['id'])
            if (not material or material.get('source') != 'space_weather'
                    or material.get('case_id') != payload['case_id']
                    or utc(material['replay_release_at']) > utc(payload['as_of'])):
                continue
            data = material.get('archive', {})
            if not data.get('date') or not first_day.isoformat() <= data['date'] <= last_day.isoformat():
                continue
            values = {key: data[key] for key in ('Kp_3hour', 'ap_3hour', 'Ap',
                      'F10_7_observed', 'F10_7_adjusted', 'definitive_flag') if key in data}
            if not any(key in values for key in ('Kp_3hour', 'ap_3hour', 'Ap', 'F10_7_observed', 'F10_7_adjusted')):
                continue
            rows.append(dict(values, material_id=material['id'], date=data['date'],
                relation='同期观测日' if data['date'] >= start.date().isoformat() else '前一观测日',
                replay_release_at=material['replay_release_at'], available_at=material.get('available_at')))
            archive = data
        if not rows:
            return None
        return {'source': archive.get('source'), 'url': archive.get('url'),
                'time_system': archive.get('time_system'), 'units': archive.get('units'),
                'rows': sorted(rows, key=lambda row: row['date']),
                'scope_note': '全球环境背景指标，用于检查自然因素的解释范围，不直接确定某站变化原因。',
                'data_note': archive.get('data_note'),
                'availability_note': '历史首次发布时间未知；按日窗结束模拟释放，归档值可能事后修订。'}

    async def enqueue_ready_batch(self, case_id, index, batch, skip_kinds=()):
        prepared = None
        case = self.store.get('replay_case', case_id)
        resources = [self.store.get('replay_resource', identifier) for identifier in batch['resource_ids']]
        group = [resource for resource in resources if resource['kind'] == 'gnss' and resource.get('material_id')]
        work_id = (f"replay:{self.instance_id}:{case_id}:{batch['as_of']}:gnss_observation_quality:"
                   f"v{case.get('run_revision', 0)}:{self.config.pipeline_analysis_version}")
        if 'gnss' not in skip_kinds and group and not self.store.get('work', work_id):
            from .gnss import gnss_evidence
            results = await self.gnss_results_async(case_id, batch['as_of'])
            program = await asyncio.to_thread(gnss_evidence, results, batch['as_of'], case_id=case_id,
                                               observation_resource_ids=[resource['id'] for resource in group])
            prepared = (results, program)
        if self.running():
            with self.store.atomic():
                self.enqueue_batch(case_id, index, batch, skip_kinds=skip_kinds, gnss_prepared=prepared)

    def enqueue_batch(self, case_id, index, batch, skip_kinds=(), gnss_prepared=None):
        as_of = batch['as_of']
        input_version = self.store.get('replay_case', case_id).get('run_revision', 0)
        resources = [self.store.get('replay_resource', identifier) for identifier in batch['resource_ids']]
        current = [resource for resource in resources if resource.get('material_id')]
        for kind in RISK_OBJECTS:
            if kind in skip_kinds:
                continue
            subset = [resource for resource in current if resource['kind'] == kind]
            if not subset:
                continue
            # All arrived resources for this object and visible cutoff belong
            # to one evidence snapshot, irrespective of raw-file count.
            for group in [subset]:
                logical_id = f'replay:{self.instance_id}:{case_id}:{as_of}:{RISK_OBJECTS[kind]}'
                work_id = f'{logical_id}:v{input_version}:{self.config.pipeline_analysis_version}'
                if self.store.get('work', work_id):
                    continue
                materials = [material_input(self.store.get_material(resource['material_id'])) for resource in group]
                payload = {'id': work_id, 'kind': kind, 'mode': 'case_replay', 'case_id': case_id,
                           'as_of': as_of, 'materials': materials, 'origin': 'historical_replay',
                           'risk_object': RISK_OBJECTS[kind], 'evidence_version': input_version,
                           'analysis_version': self.config.pipeline_analysis_version,
                           'observation_start': min(resource['observed_start'] for resource in group),
                           'observation_end': max(resource['observed_end'] for resource in group),
                           'replay_release_at': as_of}
                targets = []
                if kind == 'gnss':
                    results, program = gnss_prepared
                    payload.update(program=program,
                                   tools={'station_history': '本例已释放的同站同信号历史统计',
                                          'multistation_check': '本例已释放的同期多站统计',
                                          'read_material': '读取索引中已释放的环境观测归档'})
                    payload['program_calculations'] = ['复用真实 GNSS 特征并按参考日期组比较', '当前同窗证据目录与覆盖汇总']
                    payload['program']['resource_gaps'] = [
                        {'station': resource.get('station'), 'observed_start': resource['observed_start'],
                         'observed_end': resource['observed_end'], 'reason': resource.get('error') or resource['status']}
                        for resource in resources if resource['kind'] == kind and not resource.get('material_id')]
                    # Structured statistics replace raw arrays in the short model input.
                    for material in materials:
                        material['text'] = ''
                elif kind == 'portwatch':
                    from .maritime_evidence import maritime_evidence
                    targets, state = self.prepare_numeric(case_id, as_of)
                    payload['program'] = {key: state.get(key) for key in ('current_status', 'summary', 'latest_value',
                        'latest_observed_date', 'latest_observation', 'baseline', 'ratio', 'baseline_start',
                        'baseline_end', 'baseline_valid_days', 'rule', 'candidate', 'active_alert_id', 'data_gaps', 'portid')}
                    rows = self.store.portwatch_rows('case_replay:' + self.scope_id(case_id) + ':portwatch', self.config.portwatch_id)
                    rows = [row for row in rows if row['observed_date'] <= state['latest_observed_date']]
                    originals = {resource['observed_start'][:10]: resource['material_id']
                        for resource in self.resources(case_id, as_of=as_of, imported_only=True) if resource['kind'] == 'portwatch'}
                    model_rows = [dict(row, material_id=originals.get(row['observed_date'], row.get('material_id'))) for row in rows]
                    payload['program']['flow_context'] = maritime_evidence(model_rows, as_of)
                    payload['program']['investigation_questions'] = payload['program']['flow_context'].get('investigation_questions', [])
                    payload['program']['evidence_inventory'] = {'observation_present': state.get('latest_value') is not None,
                        'reference_present': bool(state.get('baseline_valid_days')), 'reference_valid_days': state.get('baseline_valid_days')}
                    payload['program']['alerts'] = deepcopy(targets)
                    payload['program']['trigger_observations'] = [observation for target in targets
                        for observation in self.store.get('alert', target['id']).get('trigger_observations', [])]
                    payload['program_calculations'] = ['pw_lowflow_v1 连续日规则', '可见通行量、构成与名义运力描述性统计']
                    payload['tools'] = {'read_material': '读取已释放原始日表，对照构成和名义运力字段'}
                    for material in materials:
                        material['text'] = ''
                    for target in targets:
                        alert = self.store.get('alert', target['id'])
                        alert['queued_work_id'] = work_id
                        self.store.save('alert', alert)
                payload['program']['archive_index'] = self.archive_index(case_id, as_of, kind)
                if kind == 'gnss':
                    environment = self.gfz_environment_context(payload)
                    if environment:
                        payload['program']['environment_context'] = environment
                        payload['program_archive_reads'] = [{'source': 'GFZ',
                            'material_ids': [row['material_id'] for row in environment['rows']]}]
                if kind == 'portwatch':
                    # Reference and trigger materials are part of the frozen
                    # numerical evidence, without being repeated as prompt text.
                    supporting_ids = list(dict.fromkeys([sample['material_id'] for sample in state.get('reference_samples', [])
                        if sample.get('material_id')] + [row['material_id'] for row in rows if row.get('material_id')]))
                else:
                    supporting_ids = [result['material_id'] for result in results
                        if result.get('role') == 'baseline' and result.get('station') in {resource['station'] for resource in group}]
                    supporting_ids.extend(row['material_id'] for row in
                        payload['program'].get('environment_context', {}).get('rows', []))
                work = {'id': work_id, 'scope_id': self.scope_id(case_id), 'monitor_id': self.monitor_id(case_id, kind),
                    'case_id': case_id, 'as_of': as_of, 'batch_index': index,
                    'batch_id': f'{case_id}-{index + 1}', 'mode': 'case_replay',
                    'input_version': input_version, 'logical_id': logical_id,
                    'kind': kind, 'source': kind, 'input': payload, 'material_ids': [item['id'] for item in materials],
                    'resource_ids': [resource['id'] for resource in group], 'numeric_alerts': targets,
                    'supporting_material_ids': supporting_ids,
                    'analysis_version': self.config.pipeline_analysis_version, 'status': 'queued',
                    'attempts': 0, 'created_at': now(), 'next_retry_at': now(), 'error': None}
                self.store.insert_once('work', work)

    def work_context_alerts(self, work):
        self.refresh_gnss_presentation(work)
        if work.get('interpretation_mode') == 'frozen_numeric_explanation':
            return deepcopy(work['interpretation_numeric_alerts'])
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
        args = dict(args)
        question = args.pop('_question', '')
        work = batch[0]
        key = json.dumps({'work_id': work['id'], 'question': question, 'name': name, 'arguments': args,
                          'as_of': work['as_of'], 'evidence_version': work['input_version']},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        retained = self.store.get('replay_tool_result', key)
        if retained:
            output = deepcopy(retained['output'])
            for material in output.get('materials', []):
                allowed[material['id']] = material
            output.update(cached=True, retained_tool_record=key)
            return output
        output = await self.execute_replay_tool(name, args, batch, allowed, run)
        self.store.save('replay_tool_result', {'id': key, 'work_id': work['id'], 'case_id': work['case_id'],
            'as_of': work['as_of'], 'evidence_version': work['input_version'], 'name': name,
            'question': question, 'arguments': args, 'output': deepcopy(output), 'completed_at': now()})
        return output

    async def execute_replay_tool(self, name, args, batch, allowed, run):
        case_id, as_of = batch[0]['case_id'], batch[0]['as_of']
        visible = self.resources(case_id, as_of=as_of, imported_only=True)
        by_material = {resource['material_id']: resource for resource in visible}
        if name == 'read_material':
            identifier = args.get('material_id')
            fixed_ids = set(batch[0].get('material_ids', []) + batch[0].get('supporting_material_ids', [])) | set(allowed)
            if identifier not in by_material and identifier not in fixed_ids:
                return {'materials': [], 'error': '该材料不属于本案例当前 as_of 的可见版本'}
            material = self.store.get_material(identifier)
            if not material or material.get('case_id') != case_id or utc(material.get('replay_release_at') or material['as_of']) > utc(as_of):
                return {'materials': [], 'error': '该材料不属于本案例当前 as_of 的可见版本'}
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
        if name == 'station_history':
            requested_stations = [args.get('station')]
        elif name == 'multistation_check':
            requested_stations = args.get('stations') or None
        else:
            return {'materials': [], 'error': '不支持的历史补查工具'}
        results = await self.gnss_results_async(case_id, as_of, stations=requested_stations)
        registered = {result.get('station') for result in results}
        if name == 'station_history':
            if args.get('station') not in registered:
                return {'materials': [], 'error': '站点尚未在本案例当前时点释放'}
            output = await asyncio.to_thread(station_history, results, station=args['station'], signal=args.get('signal'), as_of=as_of,
                                     window_start=args.get('window_start'), window_end=args.get('window_end'))
        elif name == 'multistation_check':
            stations = args.get('stations') or list(registered)
            if any(station not in registered for station in stations):
                return {'materials': [], 'error': '只能比较本案例已释放的站点'}
            output = await asyncio.to_thread(multistation_check, results, as_of=as_of, stations=stations, signal=args.get('signal'),
                                        window_start=args.get('window_start'), window_end=args.get('window_end'))
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
        return {'status': output.get('status'), 'materials': [material], 'result': output, 'detail': '根据本版处理的已释放观测计算',
                'case_id': case_id, 'as_of': as_of}

    async def analyze(self, batch):
        await super().analyze(batch)
        with self.store.atomic():
            for original in batch:
                work = self.store.get('work', original['id'])
                if work['status'] == 'retry_wait' and (work.get('isolated') or
                        work['attempts'] - work.get('retry_attempt_origin', 0) >= self.config.replay_max_attempts):
                    work.update(status='failed', isolated=True, next_retry_at=None,
                                error='本例分析未完成，保留位置：' + (work.get('error') or '持续失败'))
                    self.store.save('work', work)
                    self.mark_numeric(work, 'failed')
                    # Keep other already queued, fixed-cutoff judgments alive.
                    # They can include an earlier interpretation revision and
                    # do not depend on this failed model response.
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
        # The model can cite previous daily values already expanded in the
        # frozen program, even when the short materials list held only today.
        # Reuse those saved records; this never fetches or reads future data.
        for identifier in assessment.get('evidence_refs', []):
            if identifier in materials:
                continue
            material = self.store.get_material(identifier)
            if (not material or material.get('case_id') != work['case_id'] or
                    utc(material.get('replay_release_at') or material['as_of']) > utc(work['as_of'])):
                raise ValueError('引用材料不属于该判断已释放的真实证据')
            materials[identifier] = material_input(material)
        if work.get('interpretation_mode') == 'frozen_numeric_explanation':
            self.publish_numeric_interpretation(work, assessment, result, materials)
        elif work['kind'] == 'portwatch':
            super().publish(work, assessment, result, materials)
        else:
            timestamp = now()
            work['published_alert_ids'] = []
            supported = work['kind'] != 'gnss' or build_decision(
                dict(work, assessment=assessment, tool_results=result['tool_results'], completed_at=timestamp,
                     result_quality=result.get('quality_status')), [])['state'] == 'attention'
            if assessment['decision'] in ('candidate', 'update') and supported:
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
        if work.get('interpretation_supersedes_alert_ids'):
            disposition = []
            for alert_id in work['interpretation_supersedes_alert_ids']:
                alert = self.store.get('alert', alert_id)
                if not alert:
                    continue
                replaced = alert_id in work.get('published_alert_ids', [])
                if not replaced:
                    alert.update(status='revoked', superseded=True, analysis_stale=False,
                        display_status='revised', revoked_at=now(), interpretation_pending_work_id=None,
                        summary='原线索因解释修订撤销：旧判断支持不成立；这不表示物理观测已经恢复。',
                        latest_assessment=deepcopy(work.get('assessment')), interpretation_reason=work['interpretation_reason'])
                    self.store.save('alert', alert)
                disposition.append({'alert_id': alert_id,
                    'action': 'replaced_with_supported_interpretation' if replaced else 'revoked_interpretation',
                    'reason': work['interpretation_reason'], 'physical_recovery': False})
            work['interpretation_alert_changes'] = disposition
        self.store.save('work', work)
        self.persist_decision(work)

    def publish_numeric_interpretation(self, work, assessment, result, materials):
        # Revise only the explanation attached to the original numerical
        # version. A later live rule version must never move backwards.
        timestamp = now()
        analysis = dict(assessment, model=result['model'], completed_at=timestamp,
                        work_id=work['id'], analysis_version=work['analysis_version'])
        references = [material_ref(materials[identifier]) for identifier in assessment['evidence_refs']]
        revised, changes = [], []
        for original in work['interpretation_numeric_alerts']:
            alert = deepcopy(original)
            if alert.get('analysis'):
                alert.setdefault('analysis_history', []).append(deepcopy(alert['analysis']))
            alert.update(analysis=dict(analysis, evidence_version=alert['evidence_version']),
                evidence_refs=references, analysis_stale=False,
                analysis_status='insufficient_evidence' if assessment['decision'] == 'insufficient_evidence' else 'completed',
                interpretation_revision_work_id=work['id'], interpretation_reason=work['interpretation_reason'])
            current = self.store.get('alert', alert['id'])
            same_version = (current and current.get('evidence_version') == alert['evidence_version']
                            and current.get('as_of') == alert.get('as_of'))
            if same_version:
                # Status, baseline, numerical evidence and validity remain
                # exactly those established by pw_lowflow_v1.
                for key in ('analysis', 'analysis_history', 'evidence_refs', 'analysis_stale', 'analysis_status',
                            'interpretation_revision_work_id', 'interpretation_reason'):
                    current[key] = deepcopy(alert[key])
                self.store.save('alert', current)
            self.store.save('alert_version', dict(deepcopy(alert), id=uuid4().hex, alert_id=alert['id'],
                version_role='revised_interpretation', physical_recovery=False))
            revised.append(alert)
            changes.append({'alert_id': alert['id'], 'evidence_version': alert['evidence_version'],
                'action': 'explanation_updated', 'current_version_updated': bool(same_version),
                'physical_recovery': False, 'rule_state_unchanged': True})
        work.update(status='completed', completed_at=timestamp, error=None, assessment=analysis,
            tool_results=result['tool_results'], attempts_detail=result['attempts'],
            published_alert_ids=[alert['id'] for alert in revised], interpretation_numeric_results=revised,
            interpretation_numeric_alert_changes=changes)

    def decisions(self, case_id, *, as_of=None, include_superseded=False):
        return sorted([item for item in self.store.all('replay_decision') if item.get('case_id') == case_id
                       and (include_superseded or not item.get('superseded'))
                       and (as_of is None or utc(item['as_of']) <= utc(as_of))],
                      key=lambda item: (utc(item['as_of']), item.get('published_at', '')))

    def persist_decision(self, work):
        if work['kind'] not in RISK_OBJECTS:
            return
        identifier = 'decision:' + work['id']
        if self.store.get('replay_decision', identifier):
            return
        ids = list(dict.fromkeys(work.get('material_ids', []) + work.get('supporting_material_ids', [])
                  + work.get('assessment', {}).get('evidence_refs', [])
                  + [material['id'] for tool in work.get('tool_results', [])
                     for material in (tool.get('result') or {}).get('materials', [])]))
        evidence = [material for identifier in ids if (material := self.store.get_material(identifier))]
        for material in evidence:
            resource = self.store.get('replay_resource', material.get('resource_id')) if material.get('resource_id') else None
            if resource:
                # Existing imported values need no recomputation when adding
                # the catalog's already recorded acquisition provenance.
                material.update(self.resource_times(resource, material.get('archive'), resource.get('imported_at')))
        risk = RISK_OBJECTS[work['kind']]
        previous = next((item for item in reversed(self.decisions(work['case_id'], as_of=work['as_of']))
                         if item['risk_object'] == risk and (not work.get('interpretation_of')
                             or utc(item['as_of']) < utc(work['as_of']))), None)
        record = build_decision(work, evidence, previous)
        if work.get('interpretation_of'):
            record.update(interpretation_of='decision:' + work['interpretation_of'],
                          interpretation_revision=work['interpretation_revision'],
                          interpretation_reason=work['interpretation_reason'],
                          interpretation_alert_changes=deepcopy(work.get('interpretation_alert_changes')
                              or work.get('interpretation_numeric_alert_changes') or []))
            if record.get('transition'):
                record['transition']['reason'] = '解释修订：' + work['interpretation_reason']
        self.store.save('replay_decision', record)
        if work.get('interpretation_of'):
            self.save_interpretation_snapshots(work, record)
        else:
            self.save_decision_snapshot(work['case_id'], work['as_of'], work['batch_index'], identifier)

    def save_interpretation_snapshots(self, work, decision):
        original_id, old_decision_id = work['interpretation_of'], 'decision:' + work['interpretation_of']
        original = self.store.get('work', original_id)
        original.update(superseded=True, interpretation_superseded_by=work['id'], superseded_at=now())
        self.store.save('work', original)
        old_decision = self.store.get('replay_decision', old_decision_id)
        if old_decision:
            old_decision.update(superseded=True, interpretation_superseded_by=decision['id'], superseded_at=now())
            self.store.save('replay_decision', old_decision)
        snapshots = [item for item in self.store.all('replay_snapshot')
                     if item.get('case_id') == work['case_id'] and not item.get('superseded')]
        original_snapshot = self.store.get('replay_snapshot', 'snapshot:' + old_decision_id)
        if original_snapshot is None:
            matching = [item for item in snapshots if item['as_of'] == work['as_of']
                        and old_decision_id in item['decision_ids'].values()]
            original_snapshot = max(matching, key=lambda item: item['published_at']) if matching else None
        latest = max(snapshots, key=lambda item: (utc(item['as_of']), item['published_at'])) if snapshots else None
        sources = {item['id']: item for item in (original_snapshot, latest) if item}
        for source in sources.values():
            snapshot = deepcopy(source)
            snapshot.update(id='snapshot:interpretation:' + work['id'] + ':' + source['id'],
                            published_at=now(), interpretation_of_snapshot=source['id'],
                            interpretation_reason=work['interpretation_reason'])
            snapshot['decision_ids'] = {risk: decision['id'] if identifier == old_decision_id else identifier
                                        for risk, identifier in snapshot['decision_ids'].items()}
            snapshot['timeline_decision_ids'] = [decision['id'] if identifier == old_decision_id else identifier
                                                 for identifier in snapshot['timeline_decision_ids']]
            snapshot['work_ids'] = [work['id'] if identifier == original_id else identifier for identifier in snapshot['work_ids']]
            if work.get('interpretation_mode') == 'frozen_numeric_explanation':
                revised = {(alert['id'], alert['evidence_version']): alert
                           for alert in work.get('interpretation_numeric_results', [])}
                snapshot['alerts'] = [deepcopy(revised.get((alert['id'], alert.get('evidence_version')), alert))
                                      for alert in snapshot.get('alerts', [])]
                snapshot['interpretation_alert_changes'] = deepcopy(work.get('interpretation_numeric_alert_changes') or [])
            else:
                changed_alert_ids = set(work.get('interpretation_supersedes_alert_ids') or [])
                snapshot['alerts'] = [alert for alert in snapshot.get('alerts', []) if alert['id'] not in changed_alert_ids]
                snapshot['alerts'] += [deepcopy(alert) for alert in self.recent_alerts(200)
                    if alert['id'] in work.get('published_alert_ids', []) and utc(alert['as_of']) <= utc(snapshot['as_of'])]
                snapshot['interpretation_alert_changes'] = deepcopy(work.get('interpretation_alert_changes') or [])
            self.store.save('replay_snapshot', snapshot)

    def empty_decision(self, case_id, risk, as_of, index):
        case = self.store.get('replay_case', case_id)
        identifier = f"decision:{case_id}:{risk}:{as_of}:v{case.get('run_revision', 0)}:unavailable"
        existing = self.store.get('replay_decision', identifier)
        if existing:
            return existing
        record = {'id': identifier, 'case_id': case_id, 'risk_object': risk,
            'risk_label': OBJECT_LABELS[risk], 'as_of': as_of, 'batch_index': index,
            'analysis_version': self.config.pipeline_analysis_version, 'evidence_version': case.get('run_revision', 0),
            'revision_id': identifier, 'state': None, 'title': '暂未形成' + OBJECT_LABELS[risk] + '判断',
            'summary': '当前可见资料尚不满足该对象的评价条件。', 'brief_evidence': [],
            'scope_limit': SPATIAL_LIMITS[risk], 'observed_objects': [], 'evidence_refs': [], 'evidence_snapshot': [],
            'availability': {'status': 'unavailable', 'gaps': ['当前截止没有可用于本次判断的完整观测和分析']},
            'timing': {'replay_release_at': as_of, 'formed_at': now(), 'published_at': now(),
                       'valid_from': as_of, 'valid_until': None, 'historical_availability': 'unverified'},
            'report': {'assessment': {}, 'program': {}, 'material_ids': [], 'observation_material_ids': []},
            'investigation': {'questions': [], 'gaps': [], 'tools': [], 'model_selected_count': 0,
                              'executed_count': 0, 'successful_count': 0, 'program_calculations': []},
            'first_states': {}, 'transition': None, 'superseded': False, 'published_at': now(), 'created_at': now()}
        self.store.save('replay_decision', record)
        return record

    def complete_decision_batch(self, case, index):
        batch = self.definitions[case['case_id']]['batches'][index]
        expected = {RISK_OBJECTS[resource['kind']] for identifier in batch['resource_ids']
                    if (resource := self.store.get('replay_resource', identifier))['kind'] in RISK_OBJECTS}
        current = self.decisions(case['case_id'], as_of=batch['as_of'])
        for risk in expected:
            if not any(item['risk_object'] == risk and item['as_of'] == batch['as_of'] for item in current):
                previous = next((item for item in reversed(current) if item['risk_object'] == risk), None)
                # A persistent warning survives a missing arrival. Other colors
                # require evaluation at the new object's observation cutoff.
                if not previous or previous.get('state') != 'warning':
                    self.empty_decision(case['case_id'], risk, batch['as_of'], index)
        self.save_decision_snapshot(case['case_id'], batch['as_of'], index,
            f"complete:{case['case_id']}:{index}:v{case.get('run_revision', 0)}")

    def save_decision_snapshot(self, case_id, as_of, index, revision):
        identifier = 'snapshot:' + revision
        if self.store.get('replay_snapshot', identifier):
            return
        case = self.store.get('replay_case', case_id)
        objects = {}
        for item in self.decisions(case_id, as_of=as_of):
            objects[item['risk_object']] = item['id']
        for risk in RISK_OBJECTS.values():
            if risk not in objects:
                objects[risk] = self.empty_decision(case_id, risk, as_of, index)['id']
        visible = self.resources(case_id, as_of=as_of, imported_only=True)
        works = [work for work in self.case_works(case_id) if work.get('status') == 'completed'
                 and utc(work['as_of']) <= utc(as_of)]
        numeric = self.store.get('monitor_state', self.monitor_id(case_id, 'portwatch'))
        if numeric and utc(numeric['as_of']) > utc(as_of):
            numeric = None
        rows = self.store.portwatch_rows('case_replay:' + self.scope_id(case_id) + ':portwatch', self.config.portwatch_id)
        rows = [row for row in rows if row['observed_date'] < as_of[:10]]
        self.store.save('replay_snapshot', {'id': identifier, 'case_id': case_id, 'as_of': as_of,
            'instance_id': self.instance_id, 'batch_index': index,
            'analysis_version': self.config.pipeline_analysis_version,
            'evidence_version': case.get('run_revision', 0), 'primary_risk_object': case['primary_risk_object'],
            'decision_ids': objects, 'work_ids': [work['id'] for work in works],
            'gnss_material_ids': [resource['material_id'] for resource in visible
                if resource['kind'] == 'gnss' and resource.get('role') != 'baseline'],
            'metrics': {'state': deepcopy(numeric or {}), 'observations': deepcopy(rows)},
            'alerts': [deepcopy(alert) for alert in self.recent_alerts(200) if alert.get('case_id') == case_id
                       and utc(alert.get('as_of') or as_of) <= utc(as_of)],
            'timeline_decision_ids': [item['id'] for item in self.decisions(case_id, as_of=as_of)],
            'published_at': now(), 'superseded': False})

    def timeline(self, case_id, *, decision_ids=None):
        items = ([self.store.get('replay_decision', identifier) for identifier in decision_ids]
                 if decision_ids is not None else self.decisions(case_id))
        return [{key: item.get(key) for key in ('id', 'risk_object', 'risk_label', 'as_of', 'state', 'title',
                 'summary', 'brief_evidence', 'transition', 'first_states', 'timing', 'revision_id',
                 'evidence_refs', 'availability')} for item in items if item]

    def decision_snapshot(self, case_id, identifier=None):
        if identifier is None:
            snapshots = [item for item in self.store.all('replay_snapshot')
                         if item.get('case_id') == case_id and not item.get('superseded')]
            record = max(snapshots, key=lambda item: (utc(item['as_of']), item['published_at'])) if snapshots else None
        else:
            record = self.store.get('replay_snapshot', identifier)
        if not record or record['case_id'] != case_id:
            return None
        objects = {risk: self.store.get('replay_decision', decision_id) for risk, decision_id in record['decision_ids'].items()}
        series, summaries = [], []
        for material_id in record['gnss_material_ids']:
            material = self.store.get_material(material_id)
            computed = (material or {}).get('archive', {}).get('computed')
            if not computed:
                continue
            summaries.append(self.compact_gnss(computed))
            for point in computed.get('series', []):
                stamp = point.get('time') or point.get('bin_start') or point.get('window_start')
                if stamp and utc(stamp) <= utc(record['as_of']):
                    series.append(dict(point, time=stamp, station=computed['station'], material_id=material_id,
                        resource_id=material.get('resource_id'), interval_seconds=computed.get('interval_seconds'),
                        processing_version=computed.get('processing_version'), time_system=computed.get('time_system')))
        works = [work for identifier in record['work_ids'] if (work := self.store.get('work', identifier))]
        works.sort(key=lambda work: (utc(work['as_of']), work.get('completed_at', '')), reverse=True)
        view = {'case_id': case_id, 'as_of': record['as_of'],
            'gnss': {'series': series, 'summary': summaries, 'units': 'per_signal'},
            'recent_work': [dict({key: work.get(key) for key in ('id', 'kind', 'status', 'created_at', 'completed_at',
                'case_id', 'as_of', 'batch_index')}, decision=work.get('assessment', {}).get('decision'),
                statement=work.get('assessment', {}).get('statement')) for work in works],
            'portwatch_monitor_id': self.monitor_id(case_id, 'portwatch'), 'alerts': record['alerts']}
        return dict(record, objects=objects, primary=objects.get(record['primary_risk_object']), view=view,
                    timeline=self.timeline(case_id, decision_ids=record['timeline_decision_ids']))

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
        complete = bool(observed) and not failures and not pending and bool(works) and not technical
        complete = complete and all(work['status'] == 'completed' for work in works)
        runs = [run for run in self.store.all('run') if run.get('case_id') == case['case_id']
                and run.get('mode') == 'pipeline_analysis']
        model_requests = sum(len(step.get('detail', {}).get('attempts', [])) for run in runs for step in run.get('steps', []))
        all_tools = [tool for work in works for tool in work.get('tool_results', [])]
        selected_tools = [tool for tool in all_tools if tool.get('chosen_by') == 'model']
        tool_calls = sum(tool.get('name') in ('station_history', 'multistation_check')
                         for work in works for tool in successful_tools(work))
        chains = [{'work_id': work['id'], 'question': tool.get('question'), 'tool': tool.get('name'),
                   'as_of': work['as_of'], 'decision': work.get('assessment', {}).get('decision')}
                  for work in works for tool in successful_tools(work)
                  if tool.get('question') and work['input']['id'] in tool.get('followup_input_ids', [])]
        explanation_issues = [work['id'] for work in works if work.get('result_quality') == 'analysis_explanation_issue']
        case.update(status='completed' if complete else 'incomplete', completed_at=now(),
            quality_status=('evidence_limited' if complete and (missing or explanation_issues or all(work.get('assessment', {}).get('decision') == 'insufficient_evidence' for work in works))
                            else 'assessed' if complete else 'technical_failure' if technical else 'input_incomplete'),
            completed_batches=case['released_batches'],
            reason=None if complete else '所需输入、可比较基线或有效分析尚未齐备；见覆盖与处理记录',
            runtime_missing=missing, failed_resources=len(failures), model_requests=model_requests,
            professional_tool_calls=tool_calls, dynamic_tools_status='observed' if tool_calls else 'not_demonstrated',
            model_selected_tools=len(selected_tools),
            model_executed_tools=sum(tool.get('executed', True) for tool in selected_tools),
            program_calculations=sum(len(work['input'].get('program_calculations', [])) for work in works),
            investigation_chains=chains, analysis_explanation_issues=explanation_issues,
            closure={'engineering_execution': 'completed' if complete else 'incomplete',
                     'evidence_investigation': 'demonstrated' if chains and not explanation_issues else 'partial' if chains else 'not_demonstrated',
                     'observation_business': {risk: next((item['state'] for item in reversed(self.decisions(case['case_id']))
                                                   if item['risk_object'] == risk), None) for risk in RISK_OBJECTS.values()},
                     'historical_availability': 'unverified', 'hot_conflict_warning': 'unverified'},
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
        return dict(self.case_summary(case), decision_snapshot=self.decision_snapshot(case_id))

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
