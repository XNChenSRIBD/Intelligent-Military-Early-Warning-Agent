import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def _portwatch_number(value, integer=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, 'missing'
    if isinstance(value, bool):
        return None, 'unparseable'
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None, 'unparseable'
    if not number.is_finite():
        return None, 'non_finite'
    if number < 0:
        return None, 'negative'
    if integer and number != number.to_integral_value():
        return None, 'non_integer'
    parsed = int(number) if integer else float(number)
    if not integer and not math.isfinite(parsed):
        return None, 'non_finite'
    return parsed, 'valid'


class Store:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.material_dir = directory / 'materials'
        self.material_dir.mkdir(exist_ok=True)
        self.database = directory / 'demo.sqlite3'
        self._transaction_db = None
        self._pending_material_files = {}
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY (kind, id));
                CREATE TABLE IF NOT EXISTS materials (
                    id TEXT PRIMARY KEY, monitor_id TEXT NOT NULL,
                    url TEXT NOT NULL, version INTEGER NOT NULL,
                    analysis_status TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS material_monitor ON materials(monitor_id, url);
                CREATE TABLE IF NOT EXISTS run_materials (
                    run_id TEXT NOT NULL, material_id TEXT NOT NULL,
                    PRIMARY KEY (run_id, material_id));
                CREATE TABLE IF NOT EXISTS portwatch_daily (
                    source_key TEXT NOT NULL, portid TEXT NOT NULL,
                    observed_date TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY (source_key, portid, observed_date));
            ''')

    @contextmanager
    def connect(self):
        if self._transaction_db is not None:
            yield self._transaction_db
            return
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def atomic(self):
        """Group synchronous store calls into one commit; do not await inside."""
        if self._transaction_db is not None:
            yield self._transaction_db
            return
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        self._transaction_db = db
        self._pending_material_files = {}
        committed = False
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
            committed = True
        except BaseException:
            db.rollback()
            raise
        finally:
            files = self._pending_material_files if committed else {}
            self._transaction_db = None
            self._pending_material_files = {}
            db.close()
        for material in files.values():
            self._write_material_file(material)

    def _write_material_file(self, material):
        if self._transaction_db is not None:
            self._pending_material_files[material['id']] = dict(material)
            return
        (self.material_dir / (material['id'] + '.json')).write_text(
            json.dumps(material, ensure_ascii=False, indent=2), encoding='utf-8')

    def save(self, kind, record):
        with self.connect() as db:
            if kind == 'work':
                previous = db.execute('SELECT data FROM records WHERE kind=? AND id=?',
                                      (kind, record['id'])).fetchone()
                if previous:
                    record = dict(record, created_at=json.loads(previous['data'])['created_at'])
            db.execute('INSERT OR REPLACE INTO records VALUES (?, ?, ?)',
                       (kind, record['id'], json.dumps(record, ensure_ascii=False)))
        return record

    def insert_once(self, kind, record):
        """Return the existing durable work without resetting its state."""
        record = dict(record)
        record.setdefault('created_at', now())
        with self.connect() as db:
            cursor = db.execute('INSERT OR IGNORE INTO records VALUES (?, ?, ?)',
                                (kind, record['id'], json.dumps(record, ensure_ascii=False)))
            created = cursor.rowcount == 1
            row = db.execute('SELECT data FROM records WHERE kind=? AND id=?',
                             (kind, record['id'])).fetchone()
        return json.loads(row['data']), created

    def get(self, kind, record_id):
        with self.connect() as db:
            row = db.execute('SELECT data FROM records WHERE kind=? AND id=?',
                             (kind, record_id)).fetchone()
        return json.loads(row['data']) if row else None

    def all(self, kind):
        with self.connect() as db:
            rows = db.execute('SELECT data FROM records WHERE kind=? ORDER BY rowid DESC',
                              (kind,)).fetchall()
        return [json.loads(row['data']) for row in rows]

    def recent(self, kind, limit=50):
        with self.connect() as db:
            rows = db.execute('''SELECT data FROM records WHERE kind=?
                ORDER BY COALESCE(julianday(json_extract(data, '$.updated_at')),
                    julianday(json_extract(data, '$.created_at')),
                    julianday(json_extract(data, '$.started_at')),
                    julianday(json_extract(data, '$.published_at')),
                    julianday(json_extract(data, '$.detected_at')), 0) DESC, rowid DESC
                LIMIT ?''', (kind, limit)).fetchall()
        return [json.loads(row['data']) for row in rows]

    def due_work(self, timestamp, limit=50):
        with self.connect() as db:
            rows = db.execute('''SELECT data FROM records WHERE kind='work'
                AND json_extract(data, '$.status') IN ('queued', 'retry_wait')
                AND (json_extract(data, '$.next_retry_at') IS NULL
                    OR julianday(json_extract(data, '$.next_retry_at')) <= julianday(?))
                ORDER BY julianday(json_extract(data, '$.created_at')) ASC, rowid ASC
                LIMIT ?''', (timestamp, limit)).fetchall()
        return [json.loads(row['data']) for row in rows]

    def work_counts(self):
        counts = dict.fromkeys(('queued', 'running', 'completed', 'retry_wait', 'failed'), 0)
        with self.connect() as db:
            rows = db.execute('''SELECT json_extract(data, '$.status') AS status, COUNT(*) AS count
                FROM records WHERE kind='work' GROUP BY json_extract(data, '$.status')''').fetchall()
        counts.update({row['status']: row['count'] for row in rows if row['status']})
        return counts

    def add_material(self, monitor_id, run_id, material):
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(material['url'])
        url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path,
                          parts.query, ''))
        with self.connect() as db:
            rows = db.execute('SELECT data FROM materials WHERE monitor_id=? AND url=? '
                              'ORDER BY version DESC', (monitor_id, url)).fetchall()
            previous = [json.loads(row['data']) for row in rows]
            existing = next((item for item in previous
                             if all(item.get(key) == material.get(key)
                                    for key in ('title', 'text', 'content_kind'))), None)
            # A transient full-text failure should not downgrade an existing article.
            if not existing and material.get('content_kind') == 'title_only':
                existing = next((item for item in previous
                                 if item.get('title') == material.get('title')), None)
            created = existing is None
            if created:
                existing = dict(material, id=uuid4().hex, monitor_id=monitor_id, url=url,
                                version=max((item['version'] for item in previous), default=0) + 1,
                                analysis_status=material.get('analysis_status', 'pending'),
                                analysis=None, error=None,
                                first_seen_at=material.get('first_seen_at') or material.get('fetched_at') or now())
                db.execute('INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?)',
                           (existing['id'], monitor_id, url, existing['version'], existing['analysis_status'],
                            json.dumps(existing, ensure_ascii=False)))
            db.execute('INSERT OR IGNORE INTO run_materials VALUES (?, ?)',
                       (run_id, existing['id']))
        if created:
            self._write_material_file(existing)
        return existing, created

    def get_material(self, material_id):
        with self.connect() as db:
            row = db.execute('SELECT data FROM materials WHERE id=?', (material_id,)).fetchone()
        return json.loads(row['data']) if row else None

    def portwatch_rows(self, source_key, portid, start=None, end=None):
        query = 'SELECT data FROM portwatch_daily WHERE source_key=? AND portid=?'
        parameters = [source_key, portid]
        if start is not None:
            query += ' AND observed_date>=?'
            parameters.append(start)
        if end is not None:
            query += ' AND observed_date<=?'
            parameters.append(end)
        with self.connect() as db:
            rows = db.execute(query + ' ORDER BY observed_date ASC', parameters).fetchall()
        return [json.loads(row['data']) for row in rows]

    def upsert_portwatch(self, monitor_id, run_id, rows, source_key, portid, fetched_at):
        current = {item['observed_date']: item for item in self.portwatch_rows(source_key, portid)}
        counts = {'new_count': 0, 'revised_count': 0, 'unchanged_count': 0, 'changed_dates': []}
        for row in sorted(rows, key=lambda item: item['observed_date']):
            day = row['observed_date']
            attributes = row['attributes']
            previous = current.get(day)
            same = previous is not None and json.dumps(previous['attributes'], sort_keys=True) == json.dumps(attributes, sort_keys=True)
            if same:
                daily = dict(previous, last_fetched_at=fetched_at)
                self.attach(run_id, daily['material_id'])
                counts['unchanged_count'] += 1
            else:
                # Keep one material lineage for this source/date, including across monitors.
                prior_material = self.get_material(previous['material_id']) if previous else None
                owner = prior_material['monitor_id'] if prior_material else monitor_id
                material, _ = self.add_material(
                    owner, run_id, dict(row['material'], attributes=attributes, analysis_status='not_required'))
                n_total, validity = _portwatch_number(attributes.get('n_total'), integer=True)
                daily = {
                    'source_key': source_key, 'portid': portid, 'observed_date': day,
                    'attributes': attributes, 'n_total': n_total, 'validity': validity,
                    'material_id': material['id'], 'material_version': material['version'],
                    'url': material['url'], 'available_at': material.get('available_at'),
                    'first_seen_at': previous['first_seen_at'] if previous else fetched_at,
                    'last_fetched_at': fetched_at,
                }
                for field in ('n_tanker', 'n_cargo', 'capacity', 'capacity_tanker', 'capacity_cargo'):
                    daily[field] = _portwatch_number(attributes.get(field), integer=field.startswith('n_'))[0]
                counts['revised_count' if previous else 'new_count'] += 1
                counts['changed_dates'].append(day)
            with self.connect() as db:
                db.execute('INSERT OR REPLACE INTO portwatch_daily VALUES (?, ?, ?, ?)',
                           (source_key, portid, day, json.dumps(daily, ensure_ascii=False)))
            current[day] = daily
        return counts

    def materials(self, monitor_id, run_id=None):
        with self.connect() as db:
            if run_id:
                rows = db.execute('SELECT m.data FROM materials m JOIN run_materials r '
                                  "ON r.material_id=m.id JOIN records run ON run.kind='run' AND run.id=r.run_id "
                                  "WHERE json_extract(run.data, '$.monitor_id')=? AND r.run_id=? "
                                  'ORDER BY m.rowid DESC', (monitor_id, run_id)).fetchall()
            else:
                rows = db.execute('''SELECT m.data FROM materials m WHERE m.monitor_id=? OR EXISTS (
                    SELECT 1 FROM run_materials r JOIN records run ON run.kind='run' AND run.id=r.run_id
                    WHERE r.material_id=m.id AND json_extract(run.data, '$.monitor_id')=?)
                    ORDER BY m.rowid DESC''', (monitor_id, monitor_id)).fetchall()
        return [json.loads(row['data']) for row in rows]

    def attach(self, run_id, material_id):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO run_materials VALUES (?, ?)',
                       (run_id, material_id))

    def update_material(self, material):
        with self.connect() as db:
            db.execute('UPDATE materials SET analysis_status=?, data=? WHERE id=?',
                       (material['analysis_status'], json.dumps(material, ensure_ascii=False),
                        material['id']))
