"""Replay saved station measurements with their earlier, same-slot references."""

import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[1] / 'cases' / 'observations' / 'kharkiv'


def _number(value):
    if value in (None, ''):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _utc(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _station_row(raw):
    current = _number(raw['snr_p10'])
    reference = _number(raw['baseline_snr_p10_mean'])
    delta = current - reference if current is not None and reference is not None else None
    low_delta = _number(raw['delta_low30'])
    satellite_delta = _number(raw['delta_active_sv'])
    comparable = delta is not None and int(raw['baseline_days'] or 0) > 0
    # Existing screening rules; every row and every window follows the same rule.
    quick = comparable and delta <= -3 and (
        (low_delta is not None and low_delta >= .03)
        or (satellite_delta is not None and satellite_delta <= -1.5))
    strong = comparable and delta <= -5 and (
        (low_delta is not None and low_delta >= .05)
        or (satellite_delta is not None and satellite_delta <= -2))
    return {
        'station': raw['station'], 'system': raw['system'],
        'system_name': raw['system_name'], 'reference': reference,
        'current': current, 'delta': delta, 'unit': 'dB-Hz',
        'delta_unit': 'dB', 'metric': 'snr_p10',
        'metric_label': '载噪比第10百分位（较弱信号）',
        'baseline_days': int(raw['baseline_days'] or 0),
        'baseline_std': _number(raw['baseline_snr_p10_std']),
        'state': ('warning' if strong else 'attention' if quick else
                  'normal' if comparable else 'unavailable'),
        'quick': bool(quick), 'strong': bool(strong),
        'window_start': _utc(raw['start_time']),
        'window_end': _utc(raw['end_time']),
        'low_signal_fraction': _number(raw['low_snr_frac_lt30']),
        'reference_low_signal_fraction': _number(raw['baseline_low30_mean']),
        'low_signal_fraction_delta': low_delta,
        'active_satellites': _number(raw['active_sv_mean']),
        'reference_active_satellites': _number(raw['baseline_active_sv_mean']),
        'active_satellites_delta': satellite_delta,
        'epoch_count': int(raw['epoch_count'] or 0),
        'measurement_count': int(raw['snr_valid_count'] or 0),
    }


def _station_sentence(row):
    return (f"{row['station'][:4]} 站：往日同一时段 "
            f"{row['reference']:.1f}，本次 {row['current']:.1f} dB-Hz，"
            f"下降 {-row['delta']:.1f} dB。")


def _frame(rows, source):
    by_system = defaultdict(list)
    for row in rows:
        by_system[row['system']].append(row)
    systems = []
    for system, observations in by_system.items():
        strong = sorted({r['station'] for r in observations if r['strong']})
        quick = sorted({r['station'] for r in observations if r['quick']})
        systems.append({
            'system': system, 'system_name': observations[0]['system_name'],
            'station_count': len({r['station'] for r in observations}),
            'strong_station_count': len(strong), 'strong_stations': strong,
            'quick_station_count': len(quick), 'quick_stations': quick,
        })
    systems.sort(key=lambda item: (-item['strong_station_count'],
                                  -item['quick_station_count'], item['system']))
    leading = systems[0]
    quick_rows = [row for row in rows if row['quick']]
    state = ('warning' if leading['strong_station_count'] >= 3 else
             'attention' if quick_rows else 'normal')
    rows.sort(key=lambda row: (not row['strong'], not row['quick'],
                              row['delta'] if row['delta'] is not None else math.inf,
                              row['station'], row['system']))
    station_count = len({row['station'] for row in rows})
    if state == 'warning':
        count = leading['strong_station_count']
        title = f"{count} 个 IGS 接收站同时出现明显信号下降"
        summary = f"同一时段，{count} 个 IGS 接收站的 {leading['system_name']} 信号同时明显减弱。"
        evidence = [f"同步站点：{'、'.join(station[:4] for station in leading['strong_stations'])}。"]
    elif state == 'attention':
        count = len({row['station'] for row in quick_rows})
        title = f'{count} 个 IGS 接收站的信号出现变化'
        summary = f'{count} 个 IGS 接收站的信号比往日减弱。'
        evidence = [f"出现变化的站点：{'、'.join(sorted({r['station'][:4] for r in quick_rows}))}。"]
    else:
        title = '本时段接收站指标正常'
        summary = f'对比 {station_count} 个 IGS 接收站同一时段的历史观测，本次未见明显信号下降。'
        evidence = [f'本次比较 {station_count} 个站点、{len(systems)} 个卫星系统。',
                    '各站与自身同一时段的历史信号比较。']
    if quick_rows:
        # Examples follow the synchronised system and measured drop, never station ID order.
        examples = [row for row in rows if row['quick']]
        if state == 'warning':
            examples = [row for row in rows if row['strong'] and
                        row['system'] == leading['system']]
        evidence.extend(_station_sentence(row) for row in examples[:2])
    return {
        'time': max(row['window_end'] for row in rows),
        'start': min(row['window_start'] for row in rows),
        'state': state, 'title': title, 'summary': summary,
        'brief_evidence': evidence[:3], 'station_rows': rows,
        'details': {
            'station_count': station_count, 'observation_count': len(rows),
            'system_count': len(systems), 'systems': systems,
            'metric': source['metric'], 'metric_label': source['metric_label'],
            'rule': {
                'quick': source['quick_rule'], 'strong': source['strong_rule'],
                'synchrony': source['synchrony'], 'states': source['state_mapping'],
            },
            'source': source['source_csv'], 'rule_source': source['rule_source'],
            'baseline_method': source['baseline_method'],
            'baseline_dates': source['baseline_dates'],
            'baseline_sources': source['baseline_sources'],
        },
    }


@lru_cache(maxsize=1)
def load_frames() -> list[dict]:
    """All saved windows, in time order; no event labels or later observations."""
    source = json.loads((DATA_DIR / 'source.json').read_text(encoding='utf-8'))
    windows = defaultdict(list)
    with (DATA_DIR / 'station_windows.csv').open(encoding='utf-8-sig', newline='') as handle:
        for raw in csv.DictReader(handle):
            row = _station_row(raw)
            windows[row['window_start']].append(row)
    return [_frame(windows[start], source) for start in sorted(windows)]
