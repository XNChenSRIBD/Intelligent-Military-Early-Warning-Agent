"""Read existing Round 6 evidence. SQLite mode=ro; JSON goes only to stdout.

Use --timeline for saved time/version metadata, or --record KIND ID / --material
ID for an exact original record. Does not import or start the application.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def pick(row, fields):
    return {key: row[key] for key in fields.split() if key in row}


def read_one(db, table, identifier, kind=None):
    if table == 'records':
        row = db.execute('SELECT data FROM records WHERE kind=? AND id=?',
                         (kind, identifier)).fetchone()
    else:
        row = db.execute('SELECT data FROM materials WHERE id=?', (identifier,)).fetchone()
    return json.loads(row[0]) if row else None


def material_metadata(material):
    result = {k: v for k, v in material.items()
              if not isinstance(v, (dict, list)) and k != 'text'}
    archive = material.get('archive') or {}
    result['archive_metadata'] = {k: v for k, v in archive.items()
                                 if not isinstance(v, (dict, list)) and k not in ('text', 'content')}
    result['archive_keys'] = list(archive)
    for key in ('source_material_ids', 'source_resource_ids'):
        if key in material:
            result[key] = material[key]
    for key in ('source_resource_references', 'source_material_ids', 'provenance'):
        if key in archive:
            result['archive_metadata'][key] = archive[key]
    computed = archive.get('computed') or {}
    result['computed_time_metadata'] = {
        k: v for k, v in computed.items()
        if k in ('station', 'interval_seconds', 'window_start', 'window_end') or 'time' in k or 'observ' in k
        if not isinstance(v, (dict, list))}
    return result


def timeline(db, catalog_path):
    records = {}
    for kind in ('replay', 'replay_case', 'replay_resource', 'replay_decision', 'work'):
        records[kind] = [json.loads(row[0]) for row in db.execute(
            'SELECT data FROM records WHERE kind=?', (kind,))]
    resources = records['replay_resource']
    materials = []
    for resource in resources:
        material = read_one(db, 'materials', resource.get('material_id'))
        if not material:
            materials.append({'resource_id': resource['id'], 'record_missing': True})
            continue
        materials.append(material_metadata(material))
    material_ids = {m.get('id') for m in materials}
    referenced_ids = {ref['id'] for d in records['replay_decision'] if not d.get('superseded')
                      for ref in d.get('evidence_refs', []) if ref.get('id')}
    for identifier in sorted(referenced_ids - material_ids):
        material = read_one(db, 'materials', identifier)
        materials.append(material_metadata(material) if material else
                         {'id': identifier, 'record_missing': True})
    catalog = {}
    identifiers = {r['catalog_id'] for r in resources if r.get('catalog_id')}
    identifiers.update(i for r in resources for i in r.get('source_catalog_ids', []))
    with sqlite3.connect(Path(catalog_path).resolve().as_uri() + '?mode=ro', uri=True) as cat:
        cat.execute('BEGIN')
        for identifier in sorted(identifiers):
            row = cat.execute('SELECT data FROM resources WHERE id=?', (identifier,)).fetchone()
            catalog[identifier] = (pick(json.loads(row[0]),
                'id kind source source_url url version created_at fetched_at first_fetched_at '
                'downloaded result_saved_at raw_path result_path compute_status status '
                'available_at published_at source_published_at metadata') if row else None)
    return {
        'replay': [pick(r, 'id instance_id mode status created_at started_at updated_at '
                        'completed_at analysis_version') for r in records['replay']],
        'cases': [pick(r, 'id case_id instance_id status as_of total_batches released_batches '
                       'completed_batches created_at completed_at updated_at run_revision '
                       'analysis_explanation_issues') for r in records['replay_case']],
        'resources': resources,
        'materials': materials,
        'catalog': catalog,
        'decisions': [pick(r, 'id case_id risk_object as_of analysis_version evidence_version '
                          'revision_id state work_id evidence_refs evidence_snapshot timing '
                          'first_states transition superseded superseded_at published_at created_at')
                      for r in records['replay_decision']],
        'works': [pick(r, 'id case_id risk_object as_of status result_quality analysis_version '
                      'input_version run_id run_ids superseded superseded_at superseded_by '
                      'created_at queued_at started_at completed_at updated_at interpretation_of '
                      'interpretation_mode') for r in records['work']],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database', default='/home/xnchen/gnss_public_data/early_warning_runtime/replays/round6/demo.sqlite3')
    p.add_argument('--catalog-db', default='/home/xnchen/gnss_public_data/early_warning_runtime/catalog/resources.sqlite3')
    p.add_argument('--timeline', action='store_true')
    p.add_argument('--record', nargs=2, action='append', default=[], metavar=('KIND', 'ID'))
    p.add_argument('--material', action='append', default=[])
    args = p.parse_args()
    if not (args.timeline or args.record or args.material):
        p.error('Choose --timeline, --record KIND ID, or --material ID')
    with sqlite3.connect(Path(args.database).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        result = {'read_at': datetime.now(timezone.utc).isoformat(),
                  'source_database': args.database, 'sqlite_mode': 'ro',
                  'timeline': timeline(db, args.catalog_db) if args.timeline else None,
                  'records': [{'kind': kind, 'id': identifier,
                               'data': read_one(db, 'records', identifier, kind)}
                              for kind, identifier in args.record],
                  'materials': [{'id': identifier, 'data': read_one(db, 'materials', identifier)}
                                for identifier in args.material]}
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
