"""Descriptive civil PortWatch statistics; the existing low-flow rule is unchanged."""
from datetime import date, timedelta
from math import isfinite
from statistics import median

FIELDS = ('n_total', 'n_tanker', 'n_cargo', 'capacity', 'capacity_tanker', 'capacity_cargo')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _row(row):
    raw = row.get('attributes', row)
    result = {field: raw.get(field) if _number(raw.get(field)) else None for field in FIELDS}
    result['observed_date'] = row.get('observed_date', raw.get('date'))
    for target, numerator, denominator in (
        ('capacity_per_ship', 'capacity', 'n_total'),
        ('tanker_capacity_per_tanker', 'capacity_tanker', 'n_tanker')):
        result[target] = result[numerator] / result[denominator] if result[numerator] is not None and result[denominator] and result[denominator] > 0 else None
    result['material_id'] = row.get('material_id')
    result['available_at'] = row.get('available_at')
    return result


def composition_changes(previous, current):
    """Same arithmetic for target days and registered historical reference pairs."""
    fields = (*FIELDS, 'capacity_per_ship', 'tanker_capacity_per_tanker')
    changes = {}
    for field in fields:
        a, b = (current or {}).get(field), (previous or {}).get(field)
        changes[field] = {'current': a, 'previous': b,
            'difference': a-b if a is not None and b is not None else None,
            'change_pct': (a/b-1)*100 if a is not None and b is not None and b > 0 else None}
    return changes


def historical_comparator(rows, definition, resource_references):
    """Describe two actually obtained days, without importing a later outcome label."""
    days = definition['dates']
    by_day = {row['observed_date']: _row(row) for row in rows if row.get('observed_date') in days}
    missing = [day for day in days if day not in by_day]
    previous, current = by_day.get(days[0]), by_day.get(days[1])
    return {'kind': 'maritime_history', 'id': definition['id'],
        'title': '已登记历史船流构成参考 · ' + ' → '.join(days),
        'status': 'computed' if not missing else 'incomplete',
        'reference_use': 'historical_composition_reference_not_independent_validation',
        'selection_basis': '本轮开始前研究文件已登记的前两段日期窗口；仅复取这四日的原始值，不导入旧报告数值、后续表现或判断。',
        'dates': days, 'rows': [by_day[day] for day in days if day in by_day],
        'changes': composition_changes(previous, current), 'missing_dates': missing,
        'source_resource_references': resource_references,
        'available_at': None, 'replay_release_at': '2026-02-23T00:00:00Z',
        'units': {'counts': '源产品 AIS 可见船舶或航次数',
                  'capacity': '来源名义运力字段，归档未核定绝对单位；不解释为实载货量',
                  'capacity_per_ship': '同一来源名义运力字段 / 可见船次'},
        'limitations': ['历史首次公开时间未知，只按声明的模拟到达参与回放。',
            '两段用作历史参考后不再作为独立对照；不能据此报告泛化准确率、误报率或事件预测能力。',
            '没有后续日期数据，也不导入旧报告的后续恢复或事件标签。',
            '缺日与分母为零均保留空值；两日构成变化不形成新的红色规则。']}


def composition_question():
    return ('本次相邻两日总船次、油轮/货船船次、名义运力及单位船次名义运力各如何变化？'
            '从已登记历史归档选择一段两日窗口，比较相同分类字段的实际数值、变化方向和幅度，'
            '说明它支持哪些构成解释、哪些仍不能确定。')


def maritime_evidence(rows, as_of):
    visible = sorted((_row(row) for row in rows if str(row.get('observed_date', '')) < str(as_of)[:10]), key=lambda item: item['observed_date'])
    if not visible:
        return {'as_of': as_of, 'observation_present': False, 'reference_present': False, 'investigation_questions': []}
    current = visible[-1]
    previous_day = (date.fromisoformat(current['observed_date']) - timedelta(days=1)).isoformat()
    previous = next((row for row in visible if row['observed_date'] == previous_day), None)
    fields = (*FIELDS, 'capacity_per_ship', 'tanker_capacity_per_tanker')
    changes = composition_changes(previous, current)
    reference_rows = visible[-29:-1]
    reference = {}
    for field in fields:
        values = [row[field] for row in reference_rows if row[field] is not None]
        reference[field] = {'days': len(values), 'minimum': min(values) if values else None,
            'median': median(values) if values else None, 'maximum': max(values) if values else None}
    questions = []
    # A compositional question is descriptive, not a new threshold or warning rule.
    count_change = changes['n_total']['change_pct']
    capacity_change = changes['capacity_per_ship']['change_pct']
    if count_change is not None and capacity_change is not None and count_change != 0 and capacity_change != 0:
        questions.append({'kind': 'composition_context',
            'question': composition_question(),
            'tool': 'read_material', 'arguments': {},
            'resolved': False})
    return {'as_of': as_of, 'observation_present': True, 'reference_present': bool(reference_rows),
        'latest': current, 'previous': previous, 'changes': changes,
        'recent_daily': visible[-5:], 'reference_range': {'start': reference_rows[0]['observed_date'] if reference_rows else None,
            'end': reference_rows[-1]['observed_date'] if reference_rows else None, 'summary': reference},
        'investigation_questions': questions,
        'metric_notes': ['n_total/n_tanker/n_cargo为AIS派生的可见船舶或航次数。',
            'capacity为来源名义运力字段，不当作实载货量；单位船次运力是组成代理。',
            '上述历史样本范围为描述，不是显著性概率，也不形成新红色规则。',
            'PortWatch与WTO属AIS相关证据；历史首次可得未知时只支持模拟到达回放。']}
