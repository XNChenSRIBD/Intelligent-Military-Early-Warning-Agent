"""Shared server acquisition, raw-feature reuse and managed-cache ownership.

Every database operation has its own short connection. Only the process holding
the OS lock performs I/O work; clients merely register durable dependencies.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

from .config import ROOT

GIB = 1024 ** 3
POLICY = {'raw_hours': 72, 'anomaly_days': 7, 'failed_days': 7, 'part_hours': 24,
          'features_days': 90, 'logs_days': 30, 'quota_gib': 50, 'high_gib': 45,
          'target_gib': 35, 'autoclean': False}
PARSING_FIELDS = ('station', 'interval_seconds', 'time_system', 'utc_offset_seconds',
                  'time_conversion_source', 'time_conversion_valid_until', 'signal_unit',
                  'signal_unit_source', 'coordinates')
ACTIVE = ('downloading', 'computing')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def dt(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(timezone.utc)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.writing')
    pending.write_text(dumps(value), encoding='utf-8')
    pending.replace(path)


def beneath(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


class ResourceManager:
    def __init__(self, config):
        self.config = config
        self.asset_root = Path(config.server_asset_root).resolve()
        self.runtime_root = Path(config.server_runtime_root).resolve()
        self.catalog = self.runtime_root / 'catalog'
        self.results = self.runtime_root / 'results'
        self.evidence = self.runtime_root / 'evidence'
        self.temp = self.asset_root / 'tmp' / 'early_warning'
        for directory in (self.catalog, self.results, self.evidence, self.temp):
            directory.mkdir(parents=True, exist_ok=True)
        self.database = self.catalog / 'resources.sqlite3'
        self.client = str(Path(config.data_dir).resolve())
        self.enabled = bool(getattr(config, 'acquisition_enabled', False))
        self.owner = None
        self.loop_task = None
        self.workers = {}
        self.stopping = False
        self._fair_turn = 0
        self._gnss_claim_serial = 0
        self._gnss_last_served = {}
        self._gnss_claims = Counter()
        self.discovering = set()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dependencies(resource_id TEXT, consumer_id TEXT, data TEXT NOT NULL,
                    PRIMARY KEY(resource_id, consumer_id));
                CREATE TABLE IF NOT EXISTS records(kind TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS versions(resource_id TEXT, version TEXT, data TEXT NOT NULL,
                    PRIMARY KEY(resource_id,version));
                CREATE TABLE IF NOT EXISTS delivered(subscription_id TEXT, resource_id TEXT, version TEXT, work_id TEXT,
                    PRIMARY KEY(subscription_id,resource_id,version));
            ''')
            policy = dict(POLICY, autoclean=bool(getattr(config, 'acquisition_autoclean', False)))
            db.execute('INSERT OR IGNORE INTO records VALUES (?,?,?)', ('policy', 'main', dumps(policy)))
            self._put(db, 'client', self.client, {'id': self.client, 'data_dir': self.client, 'registered_at': stamp()})

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _put(db, kind, identifier, value):
        db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (kind, identifier, dumps(value)))

    def record(self, kind, identifier='main'):
        with self.db() as db:
            row = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        return json.loads(row['data']) if row else None

    def records(self, kind):
        with self.db() as db:
            rows = db.execute('SELECT data FROM records WHERE kind=?', (kind,)).fetchall()
        return [json.loads(row['data']) for row in rows]

    def save_record(self, kind, value, identifier=None):
        with self.db() as db:
            self._put(db, kind, identifier or value.get('id', 'main'), value)
        return value

    def _get(self, identifier):
        with self.db() as db:
            row = db.execute('SELECT data FROM resources WHERE id=?', (identifier,)).fetchone()
        return json.loads(row['data']) if row else None

    def _all(self):
        with self.db() as db:
            rows = db.execute('SELECT data FROM resources').fetchall()
        return [json.loads(row['data']) for row in rows]

    def _update(self, identifier, **changes):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM resources WHERE id=?', (identifier,)).fetchone()
            if not row:
                return None
            value = json.loads(row['data'])
            value.update(changes, updated_at=stamp())
            db.execute('UPDATE resources SET data=? WHERE id=?', (dumps(value), identifier))
        return value

    def _path(self, resource):
        root = ROOT if resource.get('root') == 'repo' else self.asset_root
        relative = Path(resource.get('path') or '')
        if relative.is_absolute() or not beneath(root / relative, root):
            raise ValueError('资源路径必须相对于已配置资产根或仓库根')
        return (root / relative).resolve()

    def _identity(self, resource):
        if resource['kind'] == 'gnss':
            parsed = urlsplit(resource.get('url') or '')
            url = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ''))
            return dumps({'kind': 'gnss', 'url': url or resource.get('path'), 'station': resource.get('station')})
        if resource['kind'] == 'portwatch':
            source = resource.get('source_key') or resource.get('url') or self.config.portwatch_url
            return dumps({'kind': 'portwatch', 'source': source.rstrip('/').casefold(),
                          'portid': resource.get('portid', 'chokepoint6'), 'date': str(resource['observed_start'])[:10]})
        return dumps({key: resource.get(key) for key in ('kind', 'root', 'path')})

    @staticmethod
    def _metadata(resource):
        if resource.get('kind') == 'gnss':
            from .gnss import parsing_metadata
            return parsing_metadata(resource)
        return {key: resource.get(key) for key in PARSING_FIELDS}

    @contextmanager
    def _file_guard(self):
        """Serialize short file reads/deletes with consumer registration across clients."""
        handle = (self.catalog / 'resource-operation.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b'0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            handle.close()

    def require(self, resource, consumer_id, metadata=None):
        with self._file_guard():
            return self._require(resource, consumer_id, metadata)

    def _require(self, resource, consumer_id, metadata=None):
        """Register a request. This performs only targeted local file metadata reads."""
        resource = dict(resource)
        metadata = dict(metadata or {})
        path = self._path(resource)
        identity = self._identity(resource)
        stat = path.stat() if path.is_file() else None
        source_stat = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns} if stat else None
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            found = db.execute('SELECT data FROM resources WHERE identity=?', (identity,)).fetchone()
            if found:
                item = json.loads(found['data'])
                # Fetch times never create a content or business version.
                changed = stat and item.get('source_stat') != source_stat
                changed = changed or self._metadata(item['resource']) != self._metadata(resource)
                if item['kind'] == 'gnss' and item.get('result_version'):
                    from .gnss import PROCESSING_VERSION, RAW_SCHEMA_VERSION
                    changed = changed or item.get('processing_version') != PROCESSING_VERSION or item.get('raw_schema_version') != RAW_SCHEMA_VERSION
                    changed = changed or (item.get('version_identity') or {}).get('metadata') != self._metadata(resource)
                if changed and item.get('compute_status') != 'computing' and item.get('status') != 'downloading':
                    item.update(resource=resource, raw_present=bool(stat), source_stat=source_stat,
                                compute_status='queued', status='reused_original' if stat else 'queued', error=None,
                                next_retry_at=stamp())
                elif stat:
                    item.update(raw_present=True, bytes=stat.st_size)
                elif item.get('result_path') and Path(item['result_path']).is_file() and item.get('compute_status') == 'completed':
                    item.update(status='reused_result', result_reused=True, raw_present=False)
            else:
                identifier = str(resource.get('id') or 'resource-' + uuid4().hex)
                if db.execute('SELECT 1 FROM resources WHERE id=?', (identifier,)).fetchone():
                    identifier = 'resource-' + uuid4().hex
                item = {'id': identifier, 'resource': resource, 'kind': resource['kind'],
                    'station': resource.get('station'), 'filename': path.name, 'path': str(path),
                    'observed_start': resource.get('observed_start'), 'observed_end': resource.get('observed_end'),
                    'status': 'reused_original' if stat else 'queued', 'compute_status': 'queued' if stat else 'waiting',
                    'raw_present': bool(stat), 'managed': False, 'bytes': stat.st_size if stat else 0,
                    'total_bytes': stat.st_size if stat else resource.get('expected_bytes'), 'source_stat': source_stat,
                    'source_identity': None, 'result_path': None, 'result_version': None,
                    'result_summary': None, 'result_saved_at': None, 'raw_deleted_at': None,
                    'original_reused': bool(stat), 'result_reused': False, 'downloaded': False,
                    'attempts': 0, 'compute_attempts': 0, 'next_retry_at': stamp(), 'error': None,
                    'created_at': stamp(), 'updated_at': stamp(), 'reserved_bytes': 0, 'temp_bytes': 0}
            dependency = {'resource_id': item['id'], 'consumer_id': consumer_id, 'client_data_dir': self.client,
                'requested_id': resource.get('id'), 'case_id': metadata.get('case_id'),
                'batch_id': metadata.get('batch_id'), 'role': resource.get('role'),
                'needs_raw': bool(metadata.get('needs_raw', False)), 'pin_result': bool(metadata.get('pin_result', True)),
                'released': False, 'metadata': metadata, 'updated_at': stamp()}
            previous = db.execute('SELECT data FROM dependencies WHERE resource_id=? AND consumer_id=?',
                                  (item['id'], consumer_id)).fetchone()
            if previous:
                # Repeated polling must not silently recreate a released dependency.
                old = json.loads(previous['data'])
                dependency.update({key: old[key] for key in ('released', 'pin_result', 'needs_raw', 'evidence_path') if key in old})
                if old.get('result_version') != item.get('result_version'):
                    dependency.update(released=False, pin_result=bool(metadata.get('pin_result', True)))
            if (item.get('compute_status') == 'completed' and not dependency.get('released')
                    and (not item.get('result_path') or not Path(item['result_path']).is_file())):
                item.update(compute_status='queued' if stat else 'waiting',
                            status='reused_original' if stat else 'queued', next_retry_at=stamp())
            dependency['result_version'] = item.get('result_version')
            db.execute('INSERT OR REPLACE INTO resources VALUES (?,?,?)', (item['id'], identity, dumps(item)))
            db.execute('INSERT OR REPLACE INTO dependencies VALUES (?,?,?)', (item['id'], consumer_id, dumps(dependency)))
        return item

    def dependencies(self, identifier=None):
        with self.db() as db:
            rows = db.execute('SELECT data FROM dependencies' + (' WHERE resource_id=?' if identifier else ''),
                              (identifier,) if identifier else ()).fetchall()
        return [json.loads(row['data']) for row in rows]

    def result(self, identifier, version=None):
        with self._file_guard():
            return self._result(identifier, version)

    def _result(self, identifier, version=None):
        if version is not None:
            with self.db() as db:
                row = db.execute('SELECT data FROM versions WHERE resource_id=? AND version=?', (identifier, version)).fetchone()
            item = json.loads(row['data']) if row else None
        else:
            item = self._get(identifier)
        if not item or item.get('compute_status') != 'completed' or not item.get('result_path'):
            return None
        path = Path(item['result_path'])
        if not path.is_file():
            if version is None and any(not dep.get('released') or dep.get('pin_result') for dep in self.dependencies(identifier)):
                self._update(identifier, compute_status='queued' if item['raw_present'] else 'waiting',
                             status='reused_original' if item['raw_present'] else 'queued', next_retry_at=stamp(),
                             error='所需完整特征缓存不可用，已登记重算或重取')
            return None
        if item['kind'] == 'gnss':
            if version is not None:
                raw = json.loads(path.read_text(encoding='utf-8'))
                if (raw.get('source_identity', {}).get('sha256') != item.get('source_identity', {}).get('sha256')
                        or raw.get('processing_version') != item.get('processing_version')):
                    return None
                return raw
            from .gnss import load_raw_cache
            result = load_raw_cache(path, item['resource'], item.get('source_identity'))
            if result is None and any(not dep.get('released') or dep.get('pin_result') for dep in self.dependencies(identifier)):
                self._update(identifier, compute_status='queued' if item['raw_present'] else 'waiting',
                             status='reused_original' if item['raw_present'] else 'queued', next_retry_at=stamp(),
                             error='特征缓存与当前处理解释不兼容，等待重算')
            return result
        return json.loads(path.read_text(encoding='utf-8-sig'))

    def view(self, identifier):
        item = self._get(identifier)
        if not item:
            return None
        return {**item, 'acquisition_status': item['status'], 'raw_exists': item['raw_present'],
                'acquired_at': item.get('fetched_at'), 'computed_at': item.get('result_saved_at'),
                'consumers': self.dependencies(identifier)}

    def update_policy(self, values):
        unknown = set(values) - set(POLICY)
        if unknown:
            raise ValueError('未知保留参数：' + ', '.join(sorted(unknown)))
        policy = {**self.record('policy'), **values}
        for key in POLICY:
            if key == 'autoclean':
                if not isinstance(policy[key], bool):
                    raise ValueError('autoclean 必须是布尔值')
            elif isinstance(policy[key], bool) or not isinstance(policy[key], (int, float)) or not 0 < policy[key] < 100000:
                raise ValueError(key + ' 必须是有限正数')
        if not policy['target_gib'] < policy['high_gib'] < policy['quota_gib']:
            raise ValueError('需要 0 < 回收目标 < 高水位 < 额度')
        return self.save_record('policy', policy, 'main')

    def save_subscription(self, values):
        identifier = str(values.get('id') or 'subscription-' + uuid4().hex)
        old = self.get_subscription(identifier) or {}
        item = {**old, **{key: value for key, value in values.items() if key in (
            'source', 'product', 'stations', 'hours', 'enabled', 'interval_seconds', 'start_date', 'end_date',
            'utc_offset_seconds', 'time_conversion_source', 'time_conversion_valid_until', 'baseline_resource_ids')}}
        item.update(id=identifier, updated_at=stamp())
        item.setdefault('source', 'cddis')
        item.setdefault('product', 'daily')
        item.setdefault('enabled', True)
        item.setdefault('hours', [f'{hour:02d}' for hour in range(24)])
        item.setdefault('interval_seconds', 900 if item['product'] == 'highrate' else 3600)
        item.setdefault('next_check_at', stamp())
        item.setdefault('created_at', stamp())
        item.setdefault('scan_cursor', None)
        item.setdefault('coverage_through', None)
        item.setdefault('baseline_resource_ids', [])
        if item['source'] not in ('cddis', 'bkg') or item['product'] not in ('daily', 'highrate'):
            raise ValueError('订阅来源仅支持 cddis/bkg，产品仅支持 daily/highrate')
        if not isinstance(item.get('stations'), list) or not item['stations'] or any(
                not isinstance(station, str) or len(station) != 9 or not station.isalnum() for station in item['stations']):
            raise ValueError('请提供实际的九字符站点列表')
        item['stations'] = sorted(set(station.upper() for station in item['stations']))
        if not isinstance(item['hours'], list) or any(str(hour).zfill(2) not in {f'{n:02d}' for n in range(24)} for hour in item['hours']):
            raise ValueError('hours 必须是 00 至 23 的小时列表')
        item['hours'] = sorted(set(str(hour).zfill(2) for hour in item['hours']))
        if not item['hours']:
            item['hours'] = [f'{hour:02d}' for hour in range(24)]
        if not isinstance(item['baseline_resource_ids'], list):
            raise ValueError('baseline_resource_ids 必须是已计算GNSS资源ID列表')
        for resource_id in item['baseline_resource_ids']:
            baseline = self._get(resource_id)
            if not baseline or baseline['kind'] != 'gnss':
                raise ValueError('基线必须来自已登记的GNSS资源；尚待计算的资源保持参考不足')
            self.require(baseline['resource'], 'subscription-baseline:' + identifier, {'pin_result': True})
        for old_id in set(old.get('baseline_resource_ids', [])) - set(item['baseline_resource_ids']):
            self.release('subscription-baseline:' + identifier, [old_id])
        if not isinstance(item['enabled'], bool) or not isinstance(item['interval_seconds'], int) or item['interval_seconds'] < 60:
            raise ValueError('订阅自动开关或检查周期无效')
        for key in ('start_date', 'end_date'):
            if item.get(key):
                datetime.strptime(item[key], '%Y-%m-%d')
        if item.get('start_date') and item.get('end_date') and item['start_date'] > item['end_date']:
            raise ValueError('订阅结束日期不能早于开始日期')
        return self.save_record('subscription', item)

    def get_subscription(self, identifier):
        return self.record('subscription', identifier)

    def subscriptions(self):
        return self.records('subscription')

    def ready_subscription_resources(self, subscription_id, limit=50, after=None):
        wanted = {dep['resource_id'] for dep in self.dependencies() if dep['consumer_id'] == 'subscription:' + subscription_id}
        with self.db() as db:
            delivered = {(row['resource_id'], row['version']) for row in db.execute(
                'SELECT resource_id,version FROM delivered WHERE subscription_id=?', (subscription_id,))}
        items = [item for item in self._all() if item['id'] in wanted and item.get('compute_status') == 'completed'
                 and (item['id'], item.get('result_version')) not in delivered]
        items.sort(key=lambda item: item.get('observed_end') or '', reverse=True)
        # Reserve one old item so current discovery cannot starve durable gaps.
        chosen = items[:max(1, min(int(limit), 100))]
        if len(items) > len(chosen) and chosen:
            chosen[-1] = items[-1]
        return [{'id': item['id'], 'resource_id': item['id'], 'result_version': item['result_version'],
                 'resource': item['resource'], 'computed_at': item.get('result_saved_at'),
                 'observed_start': item.get('observed_start'), 'observed_end': item.get('observed_end')} for item in chosen]

    def consume_subscription_resource(self, subscription_id, resource_id, result_version, work_id):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO delivered VALUES (?,?,?,?)',
                       (subscription_id, resource_id, result_version, work_id))

    def retry(self, identifier, fetch=False):
        item = self._get(identifier)
        if not item:
            raise ValueError('资源不存在')
        if item['status'] == 'downloading' or item['compute_status'] == 'computing':
            return item
        if fetch:
            present = Path(item['path']).is_file()
            return self._update(identifier, status='reused_original' if present else 'queued',
                                compute_status='queued' if present else 'waiting', force_fetch=not present,
                                next_retry_at=stamp(), error=None, manual_requested=True)
        return self._update(identifier, status='reused_original' if item['raw_present'] else 'queued',
                            compute_status='queued' if item['raw_present'] else 'waiting', next_retry_at=stamp(),
                            manual_requested=True, error=None)

    def request_cleanup(self):
        pending = next((request for request in self.records('cleanup_request') if request['status'] == 'queued'), None)
        if pending:
            return pending
        request = {'id': uuid4().hex, 'requested_at': stamp(), 'status': 'queued'}
        return self.save_record('cleanup_request', request)

    def mark_anomaly(self, resource_ids):
        for identifier in resource_ids:
            item = self._get(identifier)
            if item and not item.get('anomaly_at'):
                self._update(identifier, anomaly_at=stamp())

    def release(self, consumer_id, resource_ids=None, *, pin=False, evidence=None):
        selected = [dep for dep in self.dependencies() if dep['consumer_id'] == consumer_id
                    and (resource_ids is None or dep['resource_id'] in resource_ids)]
        path = None
        if evidence is not None and selected:
            path = self.evidence / 'consumers' / (uuid4().hex + '.json')
            write_json(path, {'consumer_id': consumer_id, 'resource_ids': [dep['resource_id'] for dep in selected],
                             'saved_at': stamp(), 'evidence': evidence})
        for dep in selected:
            if path:
                dep['evidence_path'] = str(path)
            dep.update(released=True, needs_raw=False, pin_result=bool(pin), updated_at=stamp())
            with self.db() as db:
                db.execute('UPDATE dependencies SET data=? WHERE resource_id=? AND consumer_id=?',
                           (dumps(dep), dep['resource_id'], consumer_id))

    def _acquire_lock(self):
        handle = (self.catalog / 'coordinator.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b'0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            return False
        self.owner = handle
        # This can run only after the old OS owner is gone, never on a TTL.
        for item in self._all():
            if item['status'] == 'downloading' or item['compute_status'] == 'computing':
                self._update(item['id'], status='queued' if item['status'] == 'downloading' else item['status'],
                             compute_status='queued', reserved_bytes=0, next_retry_at=stamp(),
                             error='前任协调器退出，按已保存的资源与结果续接')
            if item.get('cleaning'):
                self._update(item['id'], cleaning=False)
        self.save_record('coordinator', {'pid': os.getpid(), 'client_data_dir': self.client,
                                       'started_at': stamp(), 'status': 'running'}, 'main')
        return True

    def start(self):
        if self.loop_task is None:
            self.stopping = False
            self.loop_task = asyncio.create_task(self._loop())

    async def close(self):
        self.stopping = True
        if self.loop_task:
            await self.loop_task
        if self.workers:
            await asyncio.gather(*(task for _, task in self.workers.values()), return_exceptions=True)
        if self.owner:
            self.owner.close()
            self.owner = None
        self.loop_task = None

    async def _loop(self):
        while not self.stopping:
            try:
                if self.enabled and (self.owner or self._acquire_lock()):
                    await self._cycle()
            except Exception as exc:
                self.save_record('coordinator', {'pid': os.getpid(), 'status': 'error', 'error': str(exc)[:1200],
                                               'at': stamp()}, 'main')
            await asyncio.sleep(1)

    def _launch(self, item, phase, coroutine):
        async def execute():
            try:
                await coroutine
            finally:
                self.workers.pop(item['id'], None)
        task = asyncio.create_task(execute())
        self.workers[item['id']] = (phase, task)

    def _claim_work(self, identifier, phase):
        with self._file_guard(), self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM resources WHERE id=?', (identifier,)).fetchone()
            item = json.loads(row['data'])
            if item.get('cleaning') or item['status'] == 'downloading' or item['compute_status'] == 'computing':
                return None
            if phase == 'compute':
                item.update(compute_status='computing', compute_attempts=item.get('compute_attempts', 0) + 1)
            else:
                item.update(status='downloading', attempts=item['attempts'] + 1, error=None)
            item['updated_at'] = stamp()
            db.execute('UPDATE resources SET data=? WHERE id=?', (dumps(item), identifier))
        return item

    async def _cycle(self):
        current = datetime.now(timezone.utc)
        phase_counts = Counter(phase for phase, _ in self.workers.values())
        if phase_counts['download'] < 2:
            await self._discovery(current)
        subscriptions = {item['id']: item for item in self.subscriptions()}
        wanted = set()
        consumer_groups = defaultdict(dict)
        for dependency in self.dependencies():
            consumer = dependency['consumer_id']
            if consumer.startswith(('subscription:', 'subscription-baseline:')):
                subscription = subscriptions.get(consumer.split(':', 1)[1])
                if not subscription or not subscription['enabled']:
                    continue
                group = 'subscription:' + subscription['id']
            else:
                group = 'case:' + dependency['case_id'] if dependency.get('case_id') else consumer
            if not dependency.get('released') or dependency.get('pin_result'):
                wanted.add(dependency['resource_id'])
                roles = consumer_groups[dependency['resource_id']]
                if roles.get(group) != 'baseline':
                    roles[group] = dependency.get('role')
        items = [item for item in self._all() if item['id'] not in self.workers
                 and (item['id'] in wanted or item.get('manual_requested'))
                 and not item.get('cleaning')
                 and (not item.get('next_retry_at') or dt(item['next_retry_at']) <= current)]
        items.sort(key=lambda item: (item.get('observed_end') or '', item['created_at']), reverse=True)
        self._fair_turn += 1
        if self._fair_turn % 3 == 0:
            items.sort(key=lambda item: item['created_at'])
        # GNSS turns advance only on a successful parser claim. Least recently
        # served groups also give the third and later consumers their turns.
        groups = defaultdict(list)
        for item in items:
            if item['kind'] != 'gnss':
                continue
            roles = consumer_groups.get(item['id'])
            if not roles:
                case_id = item['resource'].get('case_id')
                roles = {'case:' + case_id if case_id else 'manual': item['resource'].get('role')}
            for group, role in roles.items():
                groups[group].append((item, role))
        gnss_order, selected_group = [], {}
        for group in sorted(groups, key=lambda key: (self._gnss_last_served.get(key, 0), key)):
            entries = groups[group]
            if group.startswith('case:'):
                entries.sort(key=lambda pair: (pair[1] != 'baseline', pair[0].get('observed_start') or '',
                                               pair[0].get('observed_end') or '', pair[0]['created_at'], pair[0]['id']))
            else:
                # Each online subscription gets two latest turns, then one
                # oldest-gap turn, independently of how many cases are active.
                entries.sort(key=lambda pair: (pair[0].get('observed_end') or '', pair[0]['created_at'], pair[0]['id']),
                             reverse=self._gnss_claims[group] % 3 != 2)
            for item, _ in entries:
                if item['id'] not in selected_group:
                    selected_group[item['id']] = group
                    gnss_order.append(item)
        ordered_gnss = iter(gnss_order)
        items = [next(ordered_gnss) if item['kind'] == 'gnss' else item for item in items]
        for item in items:
            if item.get('compute_status') == 'completed' and not item.get('force_fetch'):
                continue
            path = Path(item['path'])
            if path.is_file() and not item.get('force_fetch'):
                # Small saved JSON records do not occupy the single GNSS parser.
                phase = 'compute' if item['kind'] == 'gnss' else 'import'
                if phase_counts[phase] < 1:
                    item = self._claim_work(item['id'], 'compute')
                    if not item:
                        continue
                    phase_counts[phase] += 1
                    if phase == 'compute':
                        group = selected_group[item['id']]
                        self._gnss_claim_serial += 1
                        self._gnss_last_served[group] = self._gnss_claim_serial
                        self._gnss_claims[group] += 1
                    self._launch(item, phase, self._compute(item))
                continue
            if item.get('result_path') and not item.get('force_fetch'):
                from .gnss import load_raw_cache
                cached = (load_raw_cache(item['result_path'], item['resource'], item.get('source_identity'))
                          if item['kind'] == 'gnss' else Path(item['result_path']).is_file())
                if cached:
                    self._update(item['id'], result_reused=True, status='reused_result', compute_status='completed')
                    continue
            if phase_counts['download'] < 2:
                if item['kind'] == 'gnss' and not self._reserve(item):
                    continue
                item = self._claim_work(item['id'], 'download')
                if not item:
                    continue
                phase_counts['download'] += 1
                self._launch(item, 'download', self._download(item))
        cleanup = self.record('cleanup') or {}
        requests = [request for request in self.records('cleanup_request') if request['status'] == 'queued']
        due = not cleanup.get('next_at') or dt(cleanup['next_at']) <= current
        daily = not cleanup.get('next_daily_at') or dt(cleanup['next_daily_at']) <= current
        if (requests or (self.record('policy')['autoclean'] and (due or daily))) and not any(
                phase == 'cleanup' for phase, _ in self.workers.values()):
            self._launch({'id': '__cleanup'}, 'cleanup', self._cleanup(daily=daily, requested=requests))

    def _reserve(self, item):
        policy = self.record('policy')
        storage = self.storage()
        if storage['managed_raw_bytes'] + storage['temp_bytes'] + storage['reserved_bytes'] >= policy['high_gib'] * GIB:
            self._update(item['id'], status='waiting_space', error='受管缓存达到高水位，等待可回收空间',
                         next_retry_at=(datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat())
            if policy['autoclean']:
                self.request_cleanup()
            return False
        expected = item.get('total_bytes') or item['resource'].get('expected_bytes') or 64 * 1024 ** 2
        reserved = min(max(int(expected), 1024 ** 2), int(policy['quota_gib'] * GIB))
        if storage['managed_raw_bytes'] + storage['temp_bytes'] + storage['reserved_bytes'] + reserved > policy['quota_gib'] * GIB:
            self._update(item['id'], status='waiting_space', error='等待缓存容量预留', next_retry_at=stamp())
            return False
        self._update(item['id'], reserved_bytes=reserved)
        return True

    async def _download(self, item):
        from .acquisition_sources import download_resource
        identifier = item['id']
        temp_dir = self.temp / identifier
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._update(identifier, temp_path=str(temp_dir))
        def progress(current, total):
            self._update(identifier, temp_bytes=current, total_bytes=total,
                         reserved_bytes=max(0, (total or 64 * 1024 ** 2) - current))
            if item['kind'] == 'gnss':
                usage = self.storage()
                if usage['managed_raw_bytes'] + usage['temp_bytes'] + usage['reserved_bytes'] > self.record('policy')['quota_gib'] * GIB:
                    raise OSError('storage_wait: 已到受管缓存额度，保留断点并等待空间')
        try:
            local = self._local_portwatch(item) if item['kind'] == 'portwatch' else None
            if local is not None:
                write_json(Path(item['path']), local)
                response = {'status': 'downloaded', 'bytes': Path(item['path']).stat().st_size, 'local_reuse': True}
            elif item['resource'].get('root') == 'repo':
                response = {'status': 'source_missing', 'error': '仓库归档资源不存在，未构造在线替代来源'}
            else:
                response = await download_resource(item['resource'], Path(item['path']), temp_dir, progress)
            if response.get('status') == 'downloaded':
                path = Path(item['path'])
                stat = path.stat()
                self._update(identifier, status='reused_original' if response.get('local_reuse') else 'ready',
                    managed=True, raw_present=True, raw_deleted_at=None, bytes=stat.st_size, total_bytes=stat.st_size,
                    source_stat={'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns},
                    source_identity=response.get('identity'), source_etag=response.get('source_etag'),
                    source_last_modified=response.get('source_last_modified'), fetched_at=stamp(),
                    downloaded=not response.get('local_reuse'), original_reused=bool(response.get('local_reuse')),
                    compute_status='queued', reserved_bytes=0, temp_bytes=0, force_fetch=False, error=None, next_retry_at=stamp())
            else:
                mapped = {'awaiting_source': 'waiting_source', 'auth_unavailable': 'auth_required',
                          'source_missing': 'source_missing', 'storage_wait': 'waiting_space'}.get(response.get('status'), 'retry_wait')
                delay = response.get('retry_after_seconds') or min(3600, 60 * 2 ** min(item['attempts'], 6))
                if mapped == 'source_missing':
                    delay = max(delay, 86400)
                elif mapped == 'auth_required':
                    delay = max(delay, 3600)
                self._update(identifier, status=mapped, error=response.get('error') or response.get('status'),
                    reserved_bytes=0, next_retry_at=(datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat())
        except Exception as exc:
            self._update(identifier, status='waiting_space' if 'storage_wait' in str(exc) else 'retry_wait',
                reserved_bytes=0, error=str(exc)[:1200],
                next_retry_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())

    def _local_portwatch(self, item):
        """Read only registered application databases for this source and date."""
        resource = item['resource']
        date = str(resource['observed_start'])[:10]
        portid = resource.get('portid', 'chokepoint6')
        for client in self.records('client'):
            database = Path(client['data_dir']) / 'demo.sqlite3'
            if not database.is_file():
                continue
            try:
                connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
                try:
                    rows = connection.execute('SELECT source_key,data FROM portwatch_daily WHERE portid=? AND observed_date=?', (portid, date)).fetchall()
                finally:
                    connection.close()
                for row in rows:
                    data = json.loads(row[1])
                    expected_source = resource.get('source_key') or resource.get('url') or self.config.portwatch_url
                    if data.get('attributes') and row[0].rstrip('/').casefold() == expected_source.rstrip('/').casefold():
                        return {'attributes': data['attributes'], 'source_key': data.get('source_key'),
                                'available_at': data.get('available_at'), 'fetched_at': data.get('last_fetched_at'),
                                'provenance': 'registered_application_portwatch_daily'}
            except sqlite3.OperationalError:
                continue
        return None

    async def _compute(self, item):
        identifier = item['id']
        try:
            if item['kind'] == 'gnss':
                from .gnss import compute_raw
                raw = await asyncio.to_thread(compute_raw, item['resource'], Path(item['path']), self.results,
                    identity=item.get('source_identity'), temp_dir=self.temp / identifier / 'compute')
                path = Path(raw['raw_cache_path'])
                identity = raw['source_identity']
                version_identity = {'source': {key: identity.get(key) for key in ('sha256', 'size')}, 'processing_version': raw['processing_version'],
                                    'metadata': raw.get('parsing_metadata')}
                summary = {key: raw.get(key) for key in ('processing_version', 'source_identity', 'station',
                    'observed_start', 'observed_end', 'window_start', 'window_end', 'interval_seconds',
                    'time_system', 'time_conversion_source', 'signal_unit', 'signals', 'quality', 'summary', 'limitations')}
            else:
                raw = json.loads(Path(item['path']).read_text(encoding='utf-8-sig'))
                identity = raw.get('attributes', raw)
                version_identity = identity
                path = self.results / identifier / (uuid4().hex + '.json')
                write_json(path, raw)
                summary = raw if item['kind'] == 'portwatch' else {key: raw.get(key) for key in ('title', 'date', 'source', 'content_kind')}
            previous = self._get(identifier)
            version = previous.get('result_version') if previous.get('version_identity') == version_identity else uuid4().hex
            if item['kind'] != 'gnss':
                final_path = self.results / identifier / (version + '.json')
                if final_path.is_file():
                    path.unlink()
                else:
                    path.replace(final_path)
                path = final_path
            evidence_path = self.evidence / 'resources' / identifier / (version + '.json')
            write_json(evidence_path, {'resource_id': identifier, 'resource': item['resource'], 'source_identity': identity,
                'result_version': version, 'summary': summary, 'saved_at': stamp()})
            completed = stamp()
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT data FROM resources WHERE id=?', (identifier,)).fetchone()
                saved = json.loads(row['data'])
                saved.update(compute_status='completed', result_path=str(path), result_version=version,
                    version_identity=version_identity, result_summary=summary, evidence_path=str(evidence_path),
                    source_identity=identity, processing_version=raw.get('processing_version'),
                    raw_schema_version=raw.get('raw_schema_version'),
                    result_saved_at=previous.get('result_saved_at') if version == previous.get('result_version') else completed,
                    result_bytes=path.stat().st_size, source_stat={key: raw.get('source_identity', {}).get(key)
                        for key in ('size', 'mtime_ns')} if item['kind'] == 'gnss' else saved.get('source_stat'),
                    error=None, updated_at=completed, next_retry_at=None, manual_requested=False)
                db.execute('UPDATE resources SET data=? WHERE id=?', (dumps(saved), identifier))
                db.execute('INSERT OR REPLACE INTO versions VALUES (?,?,?)', (identifier, version, dumps(saved)))
        except Exception as exc:
            self._update(identifier, compute_status='failed', error=str(exc)[:1500],
                next_retry_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())

    async def _discovery(self, current):
        from .acquisition_sources import discover
        for subscription in self.subscriptions():
            if not subscription['enabled'] or (subscription.get('next_check_at') and dt(subscription['next_check_at']) > current):
                continue
            try:
                self.discovering.add(subscription['id'])
                # Catch up one durable date per scan alongside latest published products.
                request = dict(subscription)
                request['temp_dir'] = str(self.temp / ('discovery-' + subscription['id']))
                subscription['temp_path'] = request['temp_dir']
                start = subscription.get('start_date')
                end = subscription.get('end_date')
                cursor = subscription.get('scan_cursor') or start
                if cursor and 'T' in cursor:
                    cursor = cursor[:10]
                newest = current.date() - timedelta(days=1 if subscription['product'] == 'daily' else 0)
                if end:
                    newest = min(newest, datetime.strptime(end, '%Y-%m-%d').date())
                dates = {newest.isoformat()}
                if subscription['product'] == 'daily':
                    dates.update((newest - timedelta(days=n)).isoformat() for n in range(3))
                if cursor and cursor <= newest.isoformat():
                    dates.add(cursor)
                persisted_gaps = [gap for gap in self.records('discovery_gap') if gap['subscription_id'] == subscription['id']
                                  and gap.get('status') != 'resolved']
                if persisted_gaps:
                    dates.add(min(gap['observed_start'][:10] for gap in persisted_gaps))
                if start:
                    dates = {day for day in dates if day >= start}
                request['dates'] = sorted(dates)
                response = await discover(request, now=current)
                found_slots = {(resource.get('station'), dt(resource['observed_start']), dt(resource['observed_end']))
                               for resource in response.get('resources', [])}
                for gap in persisted_gaps:
                    if (gap.get('station'), dt(gap['observed_start']), dt(gap['observed_end'])) in found_slots:
                        gap.update(status='resolved', resolved_at=stamp())
                        self.save_record('discovery_gap', gap)
                for gap in response.get('gaps', []):
                    gap = dict(gap, id=subscription['id'] + ':' + gap['id'], subscription_id=subscription['id'], updated_at=stamp())
                    self.save_record('discovery_gap', gap)
                for resource in response.get('resources', []):
                    for key in ('utc_offset_seconds', 'time_conversion_source', 'time_conversion_valid_until'):
                        if subscription.get(key) is not None:
                            resource[key] = subscription[key]
                    self.require(resource, 'subscription:' + subscription['id'], {'subscription_id': subscription['id']})
                status = response.get('status', 'ok')
                subscription.update(last_scan_at=stamp(), status=status, error=response.get('error'), scans=response.get('scans', []),
                    next_check_at=(current + timedelta(seconds=response.get('retry_after_seconds') or subscription['interval_seconds'])).isoformat())
                if status in ('ok', 'complete', 'discovered'):
                    subscription['scan_cursor'] = ((datetime.strptime(cursor, '%Y-%m-%d').date() + timedelta(days=1)).isoformat()
                                                   if cursor and cursor < newest.isoformat() else newest.isoformat())
                # Coverage follows successful discovered resources, never just a scan timestamp.
                deps = {dep['resource_id'] for dep in self.dependencies() if dep['consumer_id'] == 'subscription:' + subscription['id']}
                resources = sorted((item for item in self._all() if item['id'] in deps), key=lambda item: item.get('observed_end') or '')
                covered = None
                for item in resources:
                    if item['compute_status'] != 'completed':
                        break
                    covered = item.get('observed_end')
                subscription['coverage_through'] = covered
                unresolved = [gap for gap in self.records('discovery_gap') if gap['subscription_id'] == subscription['id']
                              and gap.get('status') != 'resolved']
                if unresolved:
                    earliest = min(dt(gap['observed_start']) for gap in unresolved)
                    if covered and dt(covered) > earliest:
                        subscription['coverage_through'] = earliest.isoformat()
                subscription['gap_count'] = len(unresolved)
                subscription['recent_gaps'] = sorted(unresolved, key=lambda gap: gap['observed_start'])[:8]
                self.save_record('subscription', subscription)
            except Exception as exc:
                subscription.update(status='retry_wait', error=str(exc)[:1200], last_scan_at=stamp(),
                    next_check_at=(current + timedelta(minutes=5)).isoformat())
                self.save_record('subscription', subscription)
            finally:
                self.discovering.discard(subscription['id'])

    def storage(self):
        items = self._all()
        raw = sum(item.get('bytes', 0) for item in items if item.get('raw_present') and item.get('managed'))
        unmanaged = sum(item.get('bytes', 0) for item in items if item.get('raw_present') and not item.get('managed'))
        extra = self.record('storage') or {}
        ranges = [item for item in items if item.get('observed_start') and item.get('observed_end')]
        with self.db() as db:
            versions = [json.loads(row['data']) for row in db.execute('SELECT data FROM versions')]
        cache_sizes = {version['result_path']: version.get('result_bytes', 0) for version in versions if version.get('result_path')}
        return {'managed_raw_bytes': raw, 'unmanaged_raw_bytes': unmanaged,
            'temp_bytes': sum(item.get('temp_bytes', 0) for item in items),
            'reserved_bytes': sum(item.get('reserved_bytes', 0) for item in items),
            'features_bytes': sum(cache_sizes.values()),
            'evidence_bytes': extra.get('evidence_bytes', 0), 'reports_bytes': extra.get('reports_bytes', 0),
            'database_bytes': extra.get('database_bytes', self.database.stat().st_size),
            'quota_bytes': int(self.record('policy')['quota_gib'] * GIB),
            'free_disk_bytes': shutil.disk_usage(self.asset_root).free,
            'observed_range': {'start': min((item['observed_start'] for item in ranges), default=None),
                               'end': max((item['observed_end'] for item in ranges), default=None)},
            'unmanaged_scope': '仅已登记的旧原件；未遍历或接管整个旧资产目录'}

    def snapshot(self):
        items = self._all()
        counts = Counter(item['status'] for item in items)
        counts['computing'] = sum(item['compute_status'] == 'computing' for item in items)
        counts['computed'] = sum(item['compute_status'] == 'completed' for item in items)
        counts['ready'] = counts['computed']
        counts['failed'] = sum(item['compute_status'] == 'failed' for item in items)
        cases = defaultdict(lambda: defaultdict(int))
        by_id = {item['id']: item for item in items}
        case_ids = defaultdict(set)
        for dep in self.dependencies():
            if dep.get('case_id'):
                case_ids[dep['case_id']].add(dep['resource_id'])
        for case_id, identifiers in case_ids.items():
            missing_dates = []
            valid_reference = 0
            for identifier in identifiers:
                item = by_id[identifier]
                target = cases[case_id]
                target['required'] += int(item['kind'] == 'gnss')
                if item['kind'] == 'gnss':
                    for flag in ('original_reused', 'result_reused', 'downloaded'):
                        target[flag] += int(bool(item.get(flag)))
                    target['computed'] += int(item['compute_status'] == 'completed')
                    target['pending'] += int(item['compute_status'] not in ('completed', 'failed'))
                    target['pending_download'] += int(not item['raw_present'] and item['compute_status'] != 'completed')
                    target['pending_compute'] += int(item['raw_present'] and item['compute_status'] in ('queued', 'waiting', 'computing'))
                    target['missing'] += int(item['status'] == 'source_missing')
                    target['failed'] += int(item['compute_status'] == 'failed' or item['status'] == 'auth_required')
                elif item['kind'] == 'portwatch':
                    if item['compute_status'] != 'completed':
                        missing_dates.append(str(item['observed_start'])[:10])
                    elif item['resource'].get('role') == 'baseline':
                        value = (item.get('result_summary') or {}).get('attributes', {}).get('n_total')
                        valid_reference += int(isinstance(value, (int, float)) and value >= 0)
            cases[case_id]['portwatch_missing_dates'] = sorted(missing_dates)
            cases[case_id]['portwatch_valid_reference_days'] = valid_reference
        recent = sorted(items, key=lambda item: item['updated_at'], reverse=True)[:30]
        fields = ('id', 'kind', 'station', 'filename', 'observed_start', 'observed_end', 'status', 'compute_status',
            'bytes', 'total_bytes', 'error', 'attempts', 'next_retry_at', 'managed', 'raw_present', 'raw_deleted_at',
            'result_version', 'updated_at', 'original_reused', 'result_reused', 'downloaded')
        return {'enabled': self.enabled, 'coordinator': self.record('coordinator'),
            'status': 'coordinator' if self.owner else ('client' if self.enabled else 'disabled'),
            'counts': dict(counts), 'recent_tasks': [{**{key: item.get(key) for key in fields},
                'acquisition_status': item['status'], 'raw_exists': item['raw_present']} for item in recent],
            'cases_counts': {key: dict(value) for key, value in cases.items()}, 'storage': self.storage(),
            'paths': {'asset_root': str(self.asset_root), 'runtime_root': str(self.runtime_root),
                      'catalog': str(self.catalog), 'evidence': str(self.evidence)},
            'policy': self.record('policy'), 'cleanup': self.record('cleanup') or {}, 'subscriptions': self.subscriptions()}

    async def _cleanup(self, daily=False, requested=()):
        result = await asyncio.to_thread(self._clean_files, daily)
        for request in requested:
            request.update(status='completed', completed_at=stamp(), result=result)
            self.save_record('cleanup_request', request)

    def _clean_files(self, daily):
        current = datetime.now(timezone.utc)
        policy = self.record('policy')
        items = self._all()
        usage = self.storage()
        capacity = usage['managed_raw_bytes'] + usage['temp_bytes'] >= policy['high_gib'] * GIB
        target = policy['target_gib'] * GIB
        records, freed = [], 0
        for item in sorted(items, key=lambda value: value.get('result_saved_at') or value['created_at']):
            if item['id'] in self.workers or item['status'] == 'downloading' or item['compute_status'] == 'computing':
                continue
            deps = self.dependencies(item['id'])
            raw_needed = any(dep.get('needs_raw') and not dep.get('released') for dep in deps)
            if item.get('temp_path') and beneath(item['temp_path'], self.temp):
                directory = Path(item['temp_path'])
                if directory.is_dir():
                    for path in directory.rglob('*'):
                        if not path.is_file() or not beneath(path, self.temp):
                            continue
                        age = (current - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)).total_seconds()
                        if age >= policy['part_hours'] * 3600 or ('compute' in path.parts):
                            self._delete(path, item, 'inactive_temporary', records)
                    remaining = sum(path.stat().st_size for path in directory.rglob('*') if path.is_file())
                    self._update(item['id'], temp_bytes=remaining)
            if (item['kind'] == 'gnss' and item.get('managed') and item.get('raw_present')
                    and not raw_needed and beneath(item['path'], self.asset_root)):
                completed = item.get('compute_status') == 'completed' and item.get('evidence_path') and Path(item['evidence_path']).is_file()
                anchor = item.get('result_saved_at') if completed else item.get('fetched_at') or item['created_at']
                ttl = policy['raw_hours'] * 3600 if completed else policy['failed_days'] * 86400
                if item.get('anomaly_at'):
                    anchor = max(anchor, item['anomaly_at'])
                    ttl = policy['anomaly_days'] * 86400
                expired = (current - dt(anchor)).total_seconds() >= ttl
                quota = capacity and completed and usage['managed_raw_bytes'] + usage['temp_bytes'] - freed > target
                if expired or quota:
                    reason = 'capacity' if quota else ('ttl_processed' if completed else 'ttl_unprocessed')
                    if not completed:
                        self.save_record('gap', {'id': uuid4().hex, 'resource_id': item['id'], 'at': stamp(),
                            'reason': '未完成原件到期；保留来源及重取依据', 'resource': item['resource'], 'error': item.get('error')})
                    removed = self._delete(Path(item['path']), item, reason, records)
                    if removed is not None:
                        freed += removed
                        self._update(item['id'], raw_present=False, raw_deleted_at=stamp(), raw_delete_reason=reason,
                                     bytes=0, error=item.get('error'))
        if daily:
            self._clean_feature_versions(current, policy['features_days'], records)
        for subscription in self.subscriptions():
            if subscription['id'] in self.discovering or not subscription.get('temp_path'):
                continue
            directory = Path(subscription['temp_path'])
            if not beneath(directory, self.temp) or not directory.is_dir():
                continue
            for path in directory.rglob('*'):
                if path.is_file() and beneath(path, self.temp) and (
                        current - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)).total_seconds() >= policy['part_hours'] * 3600:
                    removed = self._delete_file(path, {'id': subscription['id']}, 'inactive_discovery_temporary', records)
                    freed += removed or 0
        previous = self.record('cleanup') or {}
        local = current.astimezone(timezone(timedelta(hours=8)))
        daily_next = local.replace(hour=3, minute=30, second=0, microsecond=0)
        if daily_next <= local:
            daily_next += timedelta(days=1)
        result = {'last_at': stamp(), 'next_at': (current + timedelta(hours=1)).isoformat(),
            'last_daily_at': stamp() if daily else previous.get('last_daily_at'),
            'next_daily_at': daily_next.isoformat(), 'freed_bytes': freed, 'items': sum(not row.get('error') for row in records),
            'recent_records': records[-30:], 'error': None}
        self.save_record('cleanup', result, 'main')
        if daily:
            self._archive_clients(policy['logs_days'])
        self._storage_files()
        return result

    def _delete(self, path, item, reason, records):
        with self._file_guard():
            active = self._get(item['id'])
            if active and (active['status'] == 'downloading' or active['compute_status'] == 'computing'):
                return None
            deps = self.dependencies(item['id'])
            if reason in ('capacity', 'ttl_processed', 'ttl_unprocessed') and any(
                    dep.get('needs_raw') and not dep.get('released') for dep in deps):
                return None
            if reason == 'full_features_ttl':
                with self.db() as db:
                    references = [json.loads(row['data']) for row in db.execute('SELECT data FROM versions')]
                for reference in references:
                    if reference.get('result_path') == str(path) and any(
                            dep.get('pin_result') or not dep.get('released') for dep in self.dependencies(reference['id'])):
                        return None
            return self._delete_file(path, item, reason, records)

    def _delete_file(self, path, item, reason, records):
        entry = {'id': uuid4().hex, 'at': stamp(), 'resource_id': item['id'], 'filename': path.name, 'reason': reason}
        try:
            size = path.stat().st_size if path.is_file() else 0
            path.unlink(missing_ok=True)
            entry['bytes'] = size
        except OSError as exc:
            entry.update(bytes=0, error=str(exc))
            size = None
        records.append(entry)
        self.save_record('cleanup_event', entry)
        return size

    def _clean_feature_versions(self, current, days, records):
        with self.db() as db:
            versions = [json.loads(row['data']) for row in db.execute('SELECT data FROM versions')]
        groups = defaultdict(list)
        for version in versions:
            if version['kind'] == 'gnss' and version.get('result_path'):
                groups[version['result_path']].append(version)
        for filename, references in groups.items():
            if not beneath(filename, self.results) or any(
                    not reference.get('result_saved_at') or (current - dt(reference['result_saved_at'])).total_seconds() < days * 86400
                    or not reference.get('evidence_path') or not Path(reference['evidence_path']).is_file() for reference in references):
                continue
            removed = self._delete(Path(filename), references[0], 'full_features_ttl', records)
            if removed is None:
                continue
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                for reference in references:
                    reference.update(result_path=None, result_bytes=0, full_features_deleted_at=stamp())
                    db.execute('UPDATE versions SET data=? WHERE resource_id=? AND version=?',
                               (dumps(reference), reference['id'], reference['result_version']))
                    row = db.execute('SELECT data FROM resources WHERE id=?', (reference['id'],)).fetchone()
                    current_record = json.loads(row['data'])
                    if current_record.get('result_path') == filename:
                        current_record.update(result_path=None, result_bytes=0, full_features_deleted_at=stamp())
                        db.execute('UPDATE resources SET data=? WHERE id=?', (dumps(current_record), reference['id']))

    def _archive_clients(self, days):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        for client in self.records('client'):
            path = Path(client['data_dir']) / 'demo.sqlite3'
            if not path.is_file():
                continue
            # Only old terminal runs are compacted. Active queue and all material/alert
            # records stay untouched; full important calls are archived first.
            try:
                connection = sqlite3.connect(path, timeout=5)
                try:
                    alerts = [row[0] for row in connection.execute("SELECT data FROM records WHERE kind='alert'")]
                    references = '\n'.join(alerts)
                    linked_runs = set()
                    for work_id, work_data in connection.execute("SELECT id,data FROM records WHERE kind='work'"):
                        work = json.loads(work_data)
                        if work_id in references and work.get('run_id'):
                            linked_runs.add(work['run_id'])
                    rows = connection.execute("SELECT id,data FROM records WHERE kind='run'").fetchall()
                    for identifier, serialized in rows:
                        run = json.loads(serialized)
                        if run.get('status') not in ('completed', 'failed', 'cancelled', 'empty'):
                            continue
                        if (run.get('finished_at') or run.get('started_at') or stamp()) >= cutoff or run.get('logs_compacted_at'):
                            continue
                        has_tools = any(step.get('detail', {}).get('tools') for step in run.get('steps', []) if isinstance(step, dict))
                        if identifier in references or identifier in linked_runs or has_tools:
                            destination = self.evidence / 'model_runs' / (uuid4().hex + '.json')
                            write_json(destination, {'client_data_dir': client['data_dir'], 'run': run})
                            run['archive_path'] = str(destination)
                        run.update(steps=[], attempts=[], logs_compacted_at=stamp())
                        with connection:
                            connection.execute("UPDATE records SET data=? WHERE kind='run' AND id=? AND data=?",
                                               (dumps(run), identifier, serialized))
                finally:
                    connection.close()
            except (sqlite3.Error, OSError) as exc:
                self.save_record('maintenance_error', {'id': uuid4().hex, 'at': stamp(), 'error': str(exc)[:1000]})

    def _storage_files(self):
        def size(directory):
            return sum(path.stat().st_size for path in directory.rglob('*') if path.is_file()) if directory.is_dir() else 0
        databases = self.database.stat().st_size
        for client in self.records('client'):
            path = Path(client['data_dir']) / 'demo.sqlite3'
            if path.is_file():
                databases += path.stat().st_size
        self.save_record('storage', {'evidence_bytes': size(self.evidence), 'reports_bytes': size(self.runtime_root / 'reports'),
                                    'database_bytes': databases, 'updated_at': stamp()}, 'main')
