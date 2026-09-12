"""Feed subscribed GNSS observations into the existing online analysis queue."""

import asyncio
import json
from copy import deepcopy
from uuid import uuid4

from .gnss import contextualize_result, station_history, multistation_check
from .pipeline import Pipeline, material_input, material_ref, instant
from .replay import CaseReplay
from .store import now


class OnlinePipeline(Pipeline):
    def __init__(self, store, config, runner, acquisition):
        super().__init__(store, config, runner)
        self.acquisition = acquisition

    def start(self):
        super().start()
        self.tasks.append(asyncio.create_task(self.consume_gnss()))

    async def consume_gnss(self):
        while not self.stopping:
            if self.running():
                for subscription in self.acquisition.subscriptions():
                    if not subscription.get('enabled'):
                        continue
                    for item in self.acquisition.ready_subscription_resources(subscription['id'], limit=3):
                        try:
                            self.ingest_gnss(subscription, item)
                        except Exception as error:
                            self.store.save('gnss_ingest_error', {'id': item['id'], 'error': str(error), 'updated_at': now()})
                    self.refresh_reference_inputs(subscription)
            await asyncio.sleep(2)

    def reference_snapshot(self, subscription, as_of):
        snapshots = []
        for identifier in sorted(set(subscription.get('baseline_resource_ids', []))):
            item = self.acquisition.view(identifier) or {}
            obtained = item.get('result_saved_at') or item.get('acquired_at')
            if (item.get('compute_status') != 'completed' or not item.get('result_version')
                    or (obtained and instant(obtained) > instant(as_of))):
                continue
            snapshots.append({'catalog_id': identifier, 'result_version': item['result_version'],
                              'resource': deepcopy(item['resource']), 'obtained_at': obtained or as_of})
        return snapshots

    def reference_results(self, snapshots, scope, as_of, observation=None):
        results = []
        for snapshot in snapshots:
            definition = snapshot['resource']
            if instant(snapshot['obtained_at']) > instant(as_of):
                continue
            if observation and (definition.get('station') != observation.get('station')
                    or not definition.get('observed_end')
                    or instant(definition['observed_end']) > instant(observation['observed_start'])):
                continue
            raw = self.acquisition.result(snapshot['catalog_id'], version=snapshot['result_version'])
            resource = dict(definition, id=snapshot['catalog_id'], role='baseline',
                            replay_release_at=snapshot['obtained_at'],
                            replay_start=(observation or {}).get('observed_start'))
            if raw:
                result = contextualize_result(raw, resource, as_of=as_of, case_id=scope)
            else:
                material = self.store.get_material(snapshot.get('material_id')) if snapshot.get('material_id') else None
                if not material or not material.get('computed'):
                    continue
                result = deepcopy(material['computed'])
                result.setdefault('notes', []).append('原固定版本完整特征已不可读；仅返回该版本已保留信号窗口')
            result.update(resource_id=snapshot['catalog_id'], case_id=scope, role='baseline',
                          replay_release_at=snapshot['obtained_at'], material_id=snapshot.get('material_id'))
            results.append(result)
        return results

    @staticmethod
    def retained_result(computed):
        compact = CaseReplay.compact_gnss(computed)
        selected = {signal['signal'] for signal in compact['signals']}
        return dict(compact, series=[point for point in computed.get('series', []) if point.get('signal') in selected],
                    role=computed.get('role'), case_id=computed.get('case_id'),
                    baseline_resource_ids=computed.get('baseline_resource_ids', []))

    def save_reference_materials(self, snapshots, scope, as_of, run_id):
        saved_snapshots = []
        for snapshot in snapshots:
            record_id = f"{scope}:{snapshot['catalog_id']}:{snapshot['result_version']}"
            saved = self.store.get('gnss_baseline_version', record_id)
            if saved:
                saved_snapshots.append(dict(snapshot, material_id=saved['material_id']))
                continue
            results = self.reference_results([snapshot], scope, as_of)
            if not results:
                continue
            retained = self.retained_result(results[0])
            compact = CaseReplay.compact_gnss(results[0])
            material, _ = self.store.add_material(scope, run_id, {
                'title': f"配置参考 · {retained.get('station')} · {retained.get('window_start')}",
                'url': '/api/acquisition/resources/' + snapshot['catalog_id'], 'source': 'gnss', 'mode': 'online',
                'scope_id': scope, 'case_id': scope, 'as_of': snapshot['obtained_at'],
                'resource_id': snapshot['catalog_id'], 'content_kind': 'computed_observation',
                'text': json.dumps(dict(compact, result_version=snapshot['result_version'], role='baseline'), ensure_ascii=False),
                'observed_at': retained.get('window_start'), 'available_at': snapshot['resource'].get('available_at'),
                'fetched_at': snapshot['obtained_at'], 'analysis_status': 'pipeline', 'computed': retained})
            self.store.insert_once('gnss_baseline_version', {'id': record_id, 'material_id': material['id'],
                'scope_id': scope, 'catalog_id': snapshot['catalog_id'], 'result_version': snapshot['result_version'],
                'obtained_at': snapshot['obtained_at']})
            saved_snapshots.append(dict(snapshot, material_id=material['id']))
        return saved_snapshots

    def refresh_reference_inputs(self, subscription):
        scope = 'online-gnss-' + subscription['id']
        snapshot = self.reference_snapshot(subscription, now())
        signature = {'configured': sorted(set(subscription.get('baseline_resource_ids', []))),
                     'versions': [[item['catalog_id'], item['result_version']] for item in snapshot],
                     'analysis_version': self.config.pipeline_analysis_version}
        state = self.store.get('gnss_reference_scan', scope) or {'id': scope, 'pending': []}
        if state.get('signature') != signature:
            state.update(signature=signature, pending=[item['id'] for item in self.store.all('gnss_input_state')
                         if item['scope_id'] == scope])
            self.store.save('gnss_reference_scan', state)
        for state_id in list(state['pending'])[:3]:
            previous = self.store.get('gnss_input_state', state_id)
            try:
                if previous:
                    self.ingest_gnss(subscription, {'id': previous['catalog_id'], 'resource': previous['resource'],
                                                  'result_version': previous['result_version']})
            except Exception as error:
                self.store.save('gnss_ingest_error', {'id': state_id, 'error': str(error), 'updated_at': now()})
                continue
            state['pending'].remove(state_id)
            self.store.save('gnss_reference_scan', state)

    def ingest_gnss(self, subscription, item):
        identifier = item.get('resource_id') or item['id']
        version = item['result_version']
        raw = self.acquisition.result(identifier, version=version)
        if raw is None:
            return
        as_of = now()
        resource = dict(item['resource'], id=identifier, registered_id=identifier,
                        role='observation', replay_release_at=as_of)
        scope = 'online-gnss-' + subscription['id']
        state_id = scope + ':' + identifier
        previous = self.store.get('gnss_input_state', state_id) or {}
        snapshots = self.reference_snapshot(subscription, as_of)
        baseline = self.reference_results(snapshots, scope, as_of, observation=resource)
        resource['replay_start'] = resource['observed_start']
        computed = contextualize_result(raw, resource, as_of=as_of, case_id=scope, baseline_results=baseline)
        # Signature contains the actual matching reference support, not fetching
        # times or unrelated station/signal changes in a configured reference file.
        comparison = [[point.get(key) for key in ('signal', 'window_start', 'window_end', 'baseline_p10',
                       'delta_p10', 'baseline_days', 'baseline_resource_ids')]
                      for point in computed.get('series', []) if point.get('delta_p10') is not None]
        signature = {'result_version': version, 'comparison': comparison,
                     'analysis_version': self.config.pipeline_analysis_version}
        if previous.get('signature') == signature:
            self.acquisition.consume_subscription_resource(subscription['id'], identifier, version, previous['work_id'])
            return
        revision = previous.get('comparison_revision', 0) + 1
        key = f"{scope}:{identifier}:{version}:comparison-{revision}:{self.config.pipeline_analysis_version}"
        compact = CaseReplay.compact_gnss(computed)
        retained = self.retained_result(computed)
        source = {'id': scope, 'scope_id': scope, 'monitor_id': scope, 'source': 'gnss'}
        snapshots = self.save_reference_materials(snapshots, scope, as_of, 'import:' + key)
        with self.store.atomic():
            self.store.insert_once('monitor', {'id': scope, 'scope_id': scope, 'source': 'gnss',
                'mode': 'online', 'pipeline_owned': True, 'enabled': False, 'topic': subscription.get('name') or 'GNSS 观测质量',
                'max_materials': 3, 'lookback_hours': 72, 'interval_minutes': 15, 'created_at': as_of})
            self.store.insert_once('run', {'id': 'import:' + key, 'monitor_id': scope, 'mode': 'gnss_import',
                'status': 'completed', 'started_at': as_of, 'finished_at': as_of, 'steps': []})
            material, _ = self.store.add_material(scope, 'import:' + key, {
                'title': f"{resource.get('station')} · {resource['observed_start']}",
                'url': '/api/acquisition/resources/' + identifier, 'source': 'gnss', 'mode': 'online',
                'scope_id': scope, 'case_id': scope, 'as_of': as_of, 'resource_id': identifier,
                'content_kind': 'computed_observation', 'text': json.dumps(dict(compact, result_version=version,
                    comparison_revision=revision), ensure_ascii=False),
                'observed_at': computed.get('window_start'), 'available_at': resource.get('available_at'),
                'fetched_at': as_of, 'first_seen_at': as_of, 'analysis_status': 'pipeline', 'computed': retained,
                'time_note': '实际来源观测；系统获取时间另存，参考不足不视为正常'})
            self.store.insert_once('gnss_online_result', {'id': key, 'catalog_id': identifier, 'scope_id': scope,
                'material_id': material['id'], 'received_at': as_of, 'resource': resource,
                'result_version': version, 'comparison_revision': revision, 'baseline_snapshot': snapshots, 'retained': retained})
            payload = {'id': key, 'kind': 'gnss', 'mode': 'online', 'scope_id': scope, 'case_id': scope,
                'as_of': as_of, 'origin': 'observation', 'materials': [dict(material_input(material), text='')],
                'program': {'observations': [compact], 'reference_count': len(computed.get('baseline_resource_ids', [])),
                            'comparison_revision': revision},
                'tools': {'station_history': '本订阅截至工作截止时间已取得的同站观测',
                          'multistation_check': '本订阅已取得的同期多站描述统计'}}
            self.new_work(key, source, payload, mode='online', case_id=scope, as_of=as_of,
                subscription_id=subscription['id'], material_ids=[material['id']],
                resource_ids=[identifier], result_version=version, baseline_snapshot=snapshots,
                input_entry_id=key, comparison_revision=revision)
            self.store.save('gnss_input_state', {'id': state_id, 'scope_id': scope, 'catalog_id': identifier,
                'resource': resource, 'result_version': version, 'signature': signature,
                'comparison_revision': revision, 'work_id': key, 'material_id': material['id'],
                'baseline_versions': [[reference['catalog_id'], reference['result_version']] for reference in snapshots]})
        self.acquisition.consume_subscription_resource(subscription['id'], identifier, version, key)
        self.wakeup.set()

    def work_context_alerts(self, work):
        alerts = super().work_context_alerts(work)
        if work['kind'] == 'gnss':
            return [alert for alert in alerts if not alert.get('as_of') or instant(alert['as_of']) <= instant(work['as_of'])]
        return alerts

    async def tool(self, name, args, batch, allowed, run):
        work = batch[0]
        if work['kind'] != 'gnss':
            return await super().tool(name, args, batch, allowed, run)
        if name == 'read_material':
            material = self.store.get_material(args.get('material_id'))
            if not material or material.get('scope_id') != work['scope_id'] or instant(material['as_of']) > instant(work['as_of']):
                return {'materials': [], 'error': '材料不在本订阅该工作截止时间的可见范围'}
            allowed[material['id']] = material_input(material)
            return {'materials': [allowed[material['id']]], 'detail': '保存的实际观测材料'}
        if name not in ('station_history', 'multistation_check'):
            return {'materials': [], 'error': '本 GNSS 订阅只提供实际观测读取和已注册专业工具'}
        entries = sorted([entry for entry in self.store.all('gnss_online_result')
            if entry['scope_id'] == work['scope_id'] and instant(entry['received_at']) <= instant(work['as_of'])],
            key=lambda entry: entry['received_at'], reverse=True)
        latest = {}
        for entry in entries:
            latest.setdefault(entry['catalog_id'], entry)
        results, sources = [], {}
        snapshots = work.get('baseline_snapshot', [])
        baseline = self.reference_results(snapshots, work['scope_id'], work['as_of'])
        for result in baseline:
            if result.get('material_id'):
                sources[result['resource_id']] = result['material_id']
        for entry in list(latest.values())[:40]:
            raw = self.acquisition.result(entry['catalog_id'], version=entry['result_version'])
            # A delayed tool call uses immutable catalog/material versions, never
            # the catalog's latest replacement or the current subscription config.
            bounded = raw is None
            raw = raw or deepcopy(entry['retained'])
            resource = dict(entry['resource'], replay_start=entry['resource']['observed_start'])
            result = contextualize_result(raw, resource, as_of=work['as_of'], case_id=work['scope_id'],
                                          baseline_results=baseline)
            if bounded:
                result.setdefault('notes', []).append('完整固定版本缓存不可读；本次仅有已保留信号窗口')
            result.update(resource_id=entry['catalog_id'], id=entry['catalog_id'], case_id=work['scope_id'],
                          material_id=entry['material_id'], replay_release_at=entry['received_at'], role='observation')
            results.append(result)
            sources[entry['catalog_id']] = entry['material_id']
        registered = {result.get('station') for result in results}
        results.extend(baseline)
        if name == 'station_history':
            if args.get('station') not in registered:
                return {'materials': [], 'error': '本站尚无本订阅可用观测'}
            output = station_history(results, station=args['station'], signal=args.get('signal'),
                                     as_of=work['as_of'], case_id=work['scope_id'])
        else:
            stations = args.get('stations') or sorted(registered)
            if set(stations) - registered:
                return {'materials': [], 'error': '只能选择本订阅已有观测的站点'}
            output = multistation_check(results, stations=stations, signal=args.get('signal'),
                                        as_of=work['as_of'], case_id=work['scope_id'])
        output['baseline_versions'] = [{'resource_id': item['catalog_id'], 'result_version': item['result_version'],
                                        'material_id': item.get('material_id')} for item in snapshots
                                       if item['catalog_id'] in output.get('resource_ids', [])]
        output['coverage_note'] = '限本订阅原工作截止时间已取得的最近 40 个观测版本及工作中固定的配置参考；无参考时差值为空；已回收完整缓存只提供保留信号'
        saved, _ = self.store.add_material(work['monitor_id'], run['id'], {
            'title': name + ' · ' + work['as_of'], 'url': '/api/gnss/tools/' + uuid4().hex,
            'source': 'gnss', 'scope_id': work['scope_id'], 'case_id': work['scope_id'], 'as_of': work['as_of'],
            'mode': 'online', 'content_kind': 'computed_observation', 'text': json.dumps(output, ensure_ascii=False),
            'computed': output, 'source_material_ids': [sources[key] for key in output.get('resource_ids', []) if key in sources],
            'observed_at': work['as_of'], 'fetched_at': now(), 'analysis_status': 'pipeline'})
        allowed[saved['id']] = material_input(saved)
        return {'materials': [allowed[saved['id']]], 'result': output}

    def publish(self, work, assessment, result, materials):
        if work['kind'] != 'gnss':
            return super().publish(work, assessment, result, materials)
        timestamp = now()
        analysis = dict(assessment, model=result['model'], completed_at=timestamp,
                        work_id=work['id'], as_of=work['as_of'])
        work['published_alert_ids'] = []
        if assessment['decision'] in ('candidate', 'update'):
            identifier = assessment.get('existing_alert_id') or 'gnss-' + work['id']
            alert = self.store.get('alert', identifier)
            if alert and (alert['scope_id'] != work['scope_id'] or alert['origin_type'] != 'gnss_observation'
                          or instant(alert['as_of']) > instant(work['as_of'])):
                raise ValueError('GNSS 更新对象须为本订阅当前截止时间可见的观测异常')
            alert = alert or {'id': identifier, 'scope_id': work['scope_id'], 'monitor_id': work['monitor_id'],
                'case_id': work['scope_id'], 'mode': 'online', 'origin_type': 'gnss_observation',
                'origin': 'observation', 'detected_at': timestamp, 'published_at': timestamp,
                'evidence_version': 0, 'analysis_history': []}
            if alert.get('analysis'):
                alert['analysis_history'].append(dict(alert['analysis'], evidence_refs=alert.get('evidence_refs')))
            status = assessment.get('observation_status') or 'active'
            alert.update(title=assessment['title'], summary=assessment['statement'], status=status,
                analysis=analysis, analysis_status='completed', analysis_stale=False, as_of=work['as_of'],
                evidence_refs=[material_ref(materials[key]) for key in assessment['evidence_refs']],
                resource_ids=work['resource_ids'], evidence_version=alert['evidence_version'] + 1,
                updated_at=timestamp, observed_at=work['input']['materials'][0].get('observed_at'),
                read_at=None, display_status='resolved' if status == 'resolved' else 'revised' if assessment['decision'] == 'update' else 'new')
            self.store.save('alert', alert)
            work['published_alert_ids'].append(identifier)
        work.update(status='completed', completed_at=timestamp, error=None, assessment=analysis,
                    result_quality=result.get('quality_status'), tool_results=result['tool_results'], attempts_detail=result['attempts'])
        self.store.save('work', work)

    async def analyze(self, batch):
        await super().analyze(batch)
        for original in batch:
            work = self.store.get('work', original['id'])
            if work['kind'] == 'gnss' and work['status'] == 'completed':
                if work.get('published_alert_ids'):
                    self.acquisition.mark_anomaly(work['resource_ids'])
                self.acquisition.release('subscription:' + work['subscription_id'], work['resource_ids'],
                    evidence={'work_id': work['id'], 'program': work['input'].get('program'),
                        'assessment': work.get('assessment'), 'tools': work.get('tool_results')})
