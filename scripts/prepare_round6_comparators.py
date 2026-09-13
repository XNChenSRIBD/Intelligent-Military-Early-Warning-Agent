"""Register four source days or materialize two saved historical reference pairs.

The existing acquisition coordinator performs downloads and retains managed raw
files. This script starts no coordinator, service or model and never expands the
configured dates. Run `register`, then `materialize` once the catalog has results.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.maritime_evidence import historical_comparator
from app.resources import ResourceManager


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def raw_resource(day, definition):
    return {'id': 'round6-history-portwatch-' + day, 'kind': 'portwatch', 'root': 'assets',
        'path': f"external_intel/portwatch/daily/{definition['portid']}/{day}.json",
        'role': 'historical_reference', 'observed_start': day + 'T00:00:00Z',
        'observed_end': (date.fromisoformat(day) + timedelta(days=1)).isoformat() + 'T00:00:00Z',
        'available_at': None, 'source': 'IMF PortWatch / ArcGIS Daily_Chokepoints_Data',
        'url': definition['source_url'], 'portid': definition['portid'],
        'release_assumption': '本轮取得历史日值；来源历史首次可得时间未知，不导入未来后续表现。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('register', 'materialize'))
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--asset-root', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=ROOT / 'cases/round6/hormuz/comparators.json')
    args = parser.parse_args()
    definition = json.loads(args.config.read_text(encoding='utf-8-sig'))
    days = definition['permitted_raw_dates']
    if len(days) != 4 or len(set(days)) != 4 or len(definition['windows']) != 2:
        raise ValueError('本轮仅登记两段历史参考，共四个原始日值')
    if {day for window in definition['windows'] for day in window['dates']} != set(days):
        raise ValueError('历史参考窗口必须恰好使用四个已声明日期')
    config = Settings(data_dir=args.data_dir, server_asset_root=args.asset_root,
                      replay_asset_root=args.asset_root, server_runtime_root=args.runtime_root)
    manager = ResourceManager(config)
    registered = {}
    for day in days:
        resource = raw_resource(day, definition)
        registered[day] = manager.require(resource, 'round6:hormuz:composition-references', metadata={
            'case_id': 'hormuz', 'purpose': definition['reference_use'], 'pin_result': True})
    if args.phase == 'register':
        output = {'status': 'registered', 'dates': days,
            'resources': [{'observed_date': day, 'catalog_id': item['id'], 'status': item.get('status'),
                           'compute_status': item.get('compute_status')} for day, item in registered.items()],
            'next_action': '现有协调器按台账获取四个日值；结果到达后执行materialize。'}
        write(args.output_dir / 'ROUND6_COMPARATOR_ACQUISITION.json', output)
        print(json.dumps(output, ensure_ascii=False))
        return
    materialized, manifest_resources = [], []
    for window in definition['windows']:
        rows, references = [], []
        for day in window['dates']:
            item = manager.view(registered[day]['id'])
            raw = manager.result(item['id'])
            if raw is not None:
                rows.append({**raw, 'observed_date': day})
            references.append({'observed_date': day, 'catalog_id': item['id'],
                'result_version': item.get('result_version'), 'source_url': definition['source_url'],
                'fetched_at': item.get('fetched_at'), 'available_at': None,
                'status': item.get('status'), 'compute_status': item.get('compute_status'),
                'error': item.get('error')})
        result = historical_comparator(rows, window, references)
        target = args.asset_root / window['archive_path']
        write(target, result)
        materialized.append({'id': window['id'], 'status': result['status'],
                             'missing_dates': result['missing_dates'], 'archive_path': str(target)})
        manifest_resources.append({'id': window['id'], 'kind': 'maritime_history', 'role': 'context',
            'root': 'assets', 'path': window['archive_path'], 'title': result['title'],
            'source': 'IMF PortWatch / registered historical composition reference',
            'url': definition['source_url'], 'observed_start': window['dates'][0] + 'T00:00:00Z',
            'observed_end': (date.fromisoformat(window['dates'][1])+timedelta(days=1)).isoformat() + 'T00:00:00Z',
            'available_at': None, 'replay_release_at': '2026-02-23T00:00:00Z',
            'source_catalog_ids': [reference['catalog_id'] for reference in references],
            'release_assumption': '历史首次可得未知，目标回放起点仅模拟提供既有日期参考；不是独立对照。',
            'reference_use': definition['reference_use']})
    output = {'configuration_version': definition['configuration_version'], 'windows': materialized,
              'reference_use': definition['reference_use'], 'resources': manifest_resources}
    write(args.output_dir / 'ROUND6_COMPARATOR_RESOURCES.json', output)
    print(json.dumps(output, ensure_ascii=False))


if __name__ == '__main__':
    main()
