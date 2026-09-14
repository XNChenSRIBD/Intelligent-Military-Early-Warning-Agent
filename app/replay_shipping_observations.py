"""Daily comparisons of the saved PortWatch observations used by the replay map."""

import csv
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from statistics import median


DATA_DIR = Path(__file__).resolve().parents[1] / 'cases' / 'observations' / 'hormuz'
REFERENCE_DAYS = 28
MIN_REFERENCE_DAYS = 21
METRICS = (
    ('n_total', '通行总船次', '艘次'),
    ('n_tanker', '油轮船次', '艘次'),
    ('capacity', '总名义运力', '源数据名义运力单位'),
    ('capacity_tanker', '油轮名义运力', '源数据名义运力单位'),
    ('capacity_per_ship', '每船平均名义运力', '源数据名义运力单位/艘次'),
    ('tanker_capacity_per_tanker', '每艘油轮平均名义运力', '源数据名义运力单位/艘次'),
)
TITLES = {
    'normal': '判定正常，持续监测',
    'attention': '可能出现异常',
    'warning': '明确异常',
    'unavailable': '正在积累参考观测',
}


def _quantile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _percentile(values, value):
    return 100 * (sum(item < value for item in values)
                  + sum(item == value for item in values) / 2) / len(values)


def _change(current, previous):
    return None if current is None or previous in (None, 0) else (current / previous - 1) * 100


def _values(row):
    result = {key: float(row[key]) for key, _, _ in METRICS[:4]}
    result['capacity_per_ship'] = (
        result['capacity'] / result['n_total'] if result['n_total'] else None)
    result['tanker_capacity_per_tanker'] = (
        result['capacity_tanker'] / result['n_tanker'] if result['n_tanker'] else None)
    return result


def _metric(key, label, unit, current, previous, reference):
    values = [item[key] for item in reference if item[key] is not None]
    base = median(values) if values else None
    valid = current is not None and len(values) >= MIN_REFERENCE_DAYS
    low = _quantile(values, .05) if values else None
    high = _quantile(values, .95) if values else None
    state, direction = 'normal', None
    if valid:
        if current < low or current > high:
            state = 'warning'
        elif current < _quantile(values, .10) or current > _quantile(values, .90):
            state = 'attention'
        if state != 'normal':
            direction = 'below' if current < base else 'above'
    else:
        state = 'unavailable'
    return {
        'key': key, 'label': label, 'unit': unit,
        'reference': round(base, 4) if base is not None else None,
        'reference_low': round(low, 4) if low is not None else None,
        'reference_high': round(high, 4) if high is not None else None,
        'reference_min': min(values) if values else None,
        'reference_max': max(values) if values else None,
        'reference_days': len(values),
        'current': round(current, 4) if current is not None else None,
        'previous': round(previous, 4) if previous is not None else None,
        'change_pct': _change(current, previous),
        'reference_change_pct': _change(current, base),
        'percentile': round(_percentile(values, current), 2) if valid else None,
        'state': state, 'direction': direction,
    }


def _number(value):
    return f'{value:,.0f}'


def _evidence(metric):
    value = _number(metric['current'])
    reference = _number(metric['reference'])
    suffix = ' 艘次' if metric['unit'] == '艘次' else ''
    result = f"{metric['label']}：{value}{suffix}，此前 {metric['reference_days']} 日中位数为 {reference}{suffix}"
    change = metric['change_pct']
    if change is not None:
        verb = '减少' if change < 0 else '增加'
        if abs(change) < .05:
            result += '；与前一天相同'
        else:
            result += f"；较前一天{verb} {abs(change):.1f}%"
    return result + '。'


@lru_cache(maxsize=1)
def load_frames() -> list[dict]:
    """Return chronological observations using at least 21 prior reference days.

    Frame times are observation dates, not when the later archive was acquired.
    The same percentile rule is applied to every date, using prior rows only.
    """
    with (DATA_DIR / 'portwatch_daily.csv').open(encoding='utf-8-sig', newline='') as stream:
        target_rows = {row['date']: row for row in csv.DictReader(stream)}
    with (DATA_DIR / 'baseline.csv').open(encoding='utf-8-sig', newline='') as stream:
        combined = {}
        for row in csv.DictReader(stream):
            row['date'] = row.get('date') or row['observed_date']
            combined[row['date']] = row
    combined.update(target_rows)
    rows = sorted(combined.values(), key=lambda row: row['date'])
    source = json.loads((DATA_DIR / 'source.json').read_text(encoding='utf-8'))
    history, frames = [], []
    for row in rows:
        day = date.fromisoformat(row['date'])
        values = _values(row)
        if row['date'] not in target_rows:
            history.append({'date': day, 'values': values})
            continue
        prior = [entry for entry in history
                 if day - timedelta(days=REFERENCE_DAYS) <= entry['date'] < day]
        previous = history[-1]['values'] if history and history[-1]['date'] == day - timedelta(days=1) else {}
        if len(prior) >= MIN_REFERENCE_DAYS:
            metrics = [_metric(key, label, unit, values[key], previous.get(key),
                               [entry['values'] for entry in prior])
                       for key, label, unit in METRICS]
            strong = [metric for metric in metrics if metric['state'] == 'warning']
            changed = [metric for metric in metrics if metric['state'] == 'attention']
            state = 'warning' if len(strong) >= 2 else 'attention' if strong or changed else 'normal'
            ranked = sorted(
                [metric for metric in metrics if metric['current'] is not None],
                key=lambda item: (
                    item['state'] == 'warning', item['state'] == 'attention',
                    abs(item['change_pct'] or 0)), reverse=True)
            day_text = f'{day.month}月{day.day}日'
            abnormal = strong or changed
            if abnormal:
                lower = [item['label'] for item in abnormal if item['direction'] == 'below']
                upper = [item['label'] for item in abnormal if item['direction'] == 'above']
                parts = []
                if lower:
                    parts.append('、'.join(lower[:2]) + '低于此前常见范围')
                if upper:
                    parts.append('、'.join(upper[:2]) + '高于此前常见范围')
                summary = f"{day_text}，{'；'.join(parts)}。"
            else:
                summary = f'{day_text}，船次和名义运力均处于此前观测的常见范围。'
            frames.append({
                'time': f'{(day + timedelta(days=1)).isoformat()}T00:00:00Z',
                'start': f'{day.isoformat()}T00:00:00Z',
                'state': state, 'title': TITLES[state], 'summary': summary,
                'brief_evidence': [_evidence(item) for item in ranked[:3]],
                'metrics': metrics,
                'details': {
                    'reference_start': prior[0]['date'].isoformat(),
                    'reference_end': prior[-1]['date'].isoformat(),
                    'reference_days': len(prior),
                    'reference_kind': '此前观测日的中位数与经验分位范围',
                    'rules': [
                        '使用观测日前最多 28 日的已保存数据，至少有 21 日后开始比较。',
                        '单项低于此前第 5 百分位或高于第 95 百分位，记为明显偏离；第 10 至第 90 百分位外的其他值记为变化。',
                        '同日有两项及以上明显偏离，显示明确异常；只有一项明显偏离或其他变化，显示可能出现异常；其余显示正常。',
                        '船次、合计运力与平均运力描述同一批船舶，不作为多个独立数据源计票。',
                    ],
                    'strong_metric_count': len(strong),
                    'changed_metric_count': len(changed),
                    'source': source,
                    'observation_date': day.isoformat(),
                    'time_basis': source['time_basis'],
                },
            })
        else:
            frames.append({
                'time': f'{(day + timedelta(days=1)).isoformat()}T00:00:00Z',
                'start': f'{day.isoformat()}T00:00:00Z',
                'state': 'unavailable', 'title': TITLES['unavailable'],
                'summary': f'已有 {len(prior)} 日参考观测，积累到 21 日后开始比较。',
                'brief_evidence': [f"当日通行 {_number(values['n_total'])} 艘次，其中油轮 {_number(values['n_tanker'])} 艘次。"],
                'metrics': [_metric(key, label, unit, values[key], previous.get(key),
                                    [entry['values'] for entry in prior])
                            for key, label, unit in METRICS],
                'details': {
                    'reference_start': prior[0]['date'].isoformat() if prior else None,
                    'reference_end': prior[-1]['date'].isoformat() if prior else None,
                    'reference_days': len(prior),
                    'rules': ['使用观测日前最多 28 日的已保存数据，至少有 21 日后开始比较。'],
                    'source': source,
                    'observation_date': day.isoformat(),
                    'time_basis': source['time_basis'],
                },
            })
        history.append({'date': day, 'values': values})
    return frames
