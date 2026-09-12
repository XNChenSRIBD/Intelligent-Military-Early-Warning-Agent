import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.material_dir = directory / 'materials'
        self.material_dir.mkdir(exist_ok=True)
        self.database = directory / 'demo.sqlite3'
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
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, kind, record):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO records VALUES (?, ?, ?)',
                       (kind, record['id'], json.dumps(record, ensure_ascii=False)))
        return record

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
                                analysis_status='pending', analysis=None, error=None)
                db.execute('INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?)',
                           (existing['id'], monitor_id, url, existing['version'], 'pending',
                            json.dumps(existing, ensure_ascii=False)))
            db.execute('INSERT OR IGNORE INTO run_materials VALUES (?, ?)',
                       (run_id, existing['id']))
        if created:
            (self.material_dir / (existing['id'] + '.json')).write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding='utf-8')
        return existing, created

    def materials(self, monitor_id, run_id=None):
        with self.connect() as db:
            if run_id:
                rows = db.execute('SELECT m.data FROM materials m JOIN run_materials r '
                                  'ON r.material_id=m.id WHERE m.monitor_id=? AND r.run_id=? '
                                  'ORDER BY m.rowid DESC', (monitor_id, run_id)).fetchall()
            else:
                rows = db.execute('SELECT data FROM materials WHERE monitor_id=? '
                                  'ORDER BY rowid DESC', (monitor_id,)).fetchall()
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
