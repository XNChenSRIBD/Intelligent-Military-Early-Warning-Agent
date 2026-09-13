"""Write the bounded Round 6 case configuration; no network or business execution."""
import argparse
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare(comparator_resources=None):
    for case_id in ('kharkiv', 'hormuz'):
        definition = json.loads((ROOT / 'cases/replay' / case_id / 'manifest.json').read_text(encoding='utf-8'))
        definition['analysis_scope'] = 'historical_observation_quality_and_civil_maritime_flow'
        definition['primary_risk_object'] = 'gnss_observation_quality' if case_id == 'kharkiv' else 'maritime_visible_flow'
        definition['configuration_version'] = 'round6_observation_v1'
        definition['label'] = '哈尔科夫 · 历史观测' if case_id == 'kharkiv' else '霍尔木兹 · 历史观测'
        definition['spatial_note'] = (
            '观测来自乌克兰及东欧实际接收站，未包含哈尔科夫市内站；不定位异常源。'
            if case_id == 'kharkiv' else
            'GNSS站位于以色列、乌兹别克斯坦、塞浦路斯；航运为海峡AIS派生日聚合，不是局地GNSS或逐船观测。')
        if case_id == 'hormuz':
            definition['start_at'] = '2026-02-23T00:00:00Z'
            definition['end_at'] = '2026-03-09T00:00:00Z'
            definition['product_starts'] = {'gnss': '2026-02-27T00:00:00Z'}
            existing = {r['observed_start'][:10]: r for r in definition['resources'] if r['kind'] == 'portwatch'}
            cursor = date(2026, 1, 26)
            while cursor <= date(2026, 3, 8):
                key = cursor.isoformat()
                if key not in existing:
                    resource = {'id': f'hormuz-portwatch-{key}', 'kind': 'portwatch', 'root': 'assets',
                        'path': f'external_intel/portwatch/daily/chokepoint6/{key}.json',
                        'observed_start': key+'T00:00:00Z',
                        'observed_end': (cursor+timedelta(days=1)).isoformat()+'T00:00:00Z',
                        'available_at': None, 'portid': 'chokepoint6',
                        'source': 'IMF PortWatch / ArcGIS Daily_Chokepoints_Data',
                        'url': 'https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/Daily_Chokepoints_Data/FeatureServer/0',
                        'release_assumption': '历史首次公开时间未知；日窗结束仅为模拟释放，不证明当时可得。'}
                    definition['resources'].append(resource)
                    existing[key] = resource
                existing[key]['role'] = 'baseline' if key < '2026-02-23' else 'observation'
                cursor += timedelta(days=1)
            if comparator_resources:
                references = json.loads(Path(comparator_resources).read_text(encoding='utf-8-sig'))
                definition['resources'].extend(references['resources'])
        for resource in definition['resources']:
            resource['replay_start'] = definition.get('product_starts', {}).get(resource['kind'], definition['start_at'])
            if resource['kind'] not in ('gnss', 'portwatch') and resource['observed_start'] >= definition['start_at']:
                resource['role'] = 'observation'
            resource.pop('availability', None)
            # R5 provenance stays in the original manifest; current availability comes from the catalog.
            resource.pop('coverage_note', None)
        definition['coverage'] = {
            'scope': definition['spatial_note'],
            'availability_basis': '历史首次发布时间未核实，按资源声明的日窗或切片结束模拟释放。',
            'missing_required': [],
            'assessment_note': '实际资源可用性、参考支持与缺口由本轮台账和判定记录生成。',
        }
        folder = ROOT / 'cases/round6' / case_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'manifest.json').write_text(json.dumps(definition, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(f'{case_id}: {len(definition["resources"])} registered resources; {definition["start_at"]} to {definition["end_at"]}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparator-resources', type=Path)
    prepare(parser.parse_args().comparator_resources)
