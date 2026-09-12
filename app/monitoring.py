"""Deterministic PortWatch rules; no collection, model calls, or wall-clock advance."""

from copy import deepcopy
from datetime import date, timedelta
from statistics import median


_DAY = timedelta(days=1)
_OBSERVATION_FIELDS = (
    'observed_date', 'n_total', 'n_tanker', 'n_cargo', 'capacity',
    'capacity_tanker', 'capacity_cargo', 'validity', 'material_id',
    'material_version', 'url', 'available_at', 'first_seen_at', 'last_fetched_at',
)


def _observation(row):
    return {key: row.get(key) for key in _OBSERVATION_FIELDS}


def _reference(row):
    return {key: row.get(key) for key in (
        'observed_date', 'n_total', 'validity', 'material_id', 'material_version',
        'url', 'available_at',
    )}


def _valid(row):
    return row.get('validity') == 'valid' and row.get('n_total') is not None


def _baseline(day, by_date, rule):
    start = day - timedelta(days=rule['baseline_days'])
    end = day - _DAY
    samples = []
    cursor = start
    while cursor <= end:
        row = by_date.get(cursor.isoformat())
        if row and _valid(row):
            samples.append(_reference(row))
        cursor += _DAY
    value = median(item['n_total'] for item in samples) if samples else None
    return {
        'baseline': value,
        'baseline_start': start.isoformat(),
        'baseline_end': end.isoformat(),
        'baseline_valid_days': len(samples),
        'reference_samples': samples,
        'usable': len(samples) >= rule['min_baseline_days'] and value is not None and value > 0,
    }


def _ratio(row, reference):
    base = reference.get('baseline')
    return row['n_total'] / base if _valid(row) and base is not None and base > 0 else None


def _summary(status, latest, reference, recovery_count, rule):
    value = latest.get('n_total') if _valid(latest) else None
    base = reference.get('baseline')
    ratio = _ratio(latest, reference)
    if status == 'revoked':
        return '因来源数据修订撤销：原持续偏低触发条件不再成立。'
    if status == 'missing_data':
        return '当前观测缺失或主计数不可用；连续计数已中断，已有提醒保留。'
    if status == 'insufficient_reference':
        return f"参考不足：需要至少 {rule['min_baseline_days']} 个有效参考日且参考中位数大于零。"
    if status == 'active' and value is None:
        return '最新观测的主计数缺失或不可用；已有持续提醒保留，恢复连续计数已中断。'
    ratio_text = f'{ratio:.1%}' if ratio is not None else '未知'
    details = f'当前可见通行量 {value}，参考中位数 {base}，比例 {ratio_text}。'
    if status == 'candidate':
        return details + f"单日偏低，尚未形成连续 {rule['trigger_days']} 日提醒。"
    if status == 'recovering':
        return details + f"达到恢复阈值 {recovery_count}/{rule['recovery_days']} 日，尚未解除。"
    if status == 'resolved':
        return details + f"已连续 {rule['recovery_days']} 日达到冻结参考值的 {rule['recovery_ratio']:.0%}，提醒解除。"
    if status == 'active':
        return details + '持续提醒保留，尚未达到持续恢复条件。'
    return details + '未命中当前规则。'


def _replay(by_date, rule):
    """Replay saved source dates, including calendar gaps, using frozen references."""
    episodes, series = [], []
    candidate = active = None
    last = None
    cursor = date.fromisoformat(min(by_date))
    finish = date.fromisoformat(max(by_date))
    while cursor <= finish:
        day = cursor.isoformat()
        row = by_date.get(day, {'observed_date': day, 'n_total': None, 'validity': 'missing'})
        reference = active or candidate or _baseline(cursor, by_date, rule)
        status = 'not_triggered'

        if active is not None:
            active['latest_observation'] = _observation(row)
            active['last_evaluated_date'] = day
            if not _valid(row):
                active['data_gaps'].append(day)
                active['recovery_observations'] = []
                active['recovery_count'] = 0
                active['status'] = 'active'
                status = 'missing_data'
            elif row['n_total'] >= rule['recovery_ratio'] * active['baseline']:
                active['recovery_observations'].append(_reference(row))
                active['recovery_count'] = len(active['recovery_observations'])
                active['status'] = 'recovering'
                if active['recovery_count'] >= rule['recovery_days']:
                    active['status'] = 'resolved'
                    active['resolved_on'] = day
                status = active['status']
            else:
                active['recovery_observations'] = []
                active['recovery_count'] = 0
                active['status'] = 'active'
                status = 'active'
            reference = active
            if active['status'] == 'resolved':
                active = None
        else:
            if candidate is not None:
                if _valid(row) and row['n_total'] < rule['trigger_ratio'] * candidate['baseline']:
                    candidate['trigger_observations'].append(_reference(row))
                    candidate['latest_observation'] = _observation(row)
                    candidate['last_evaluated_date'] = day
                    if len(candidate['trigger_observations']) >= rule['trigger_days']:
                        active = candidate
                        active.update(status='active', triggered_on=day, resolved_on=None,
                                      recovery_count=0, recovery_observations=[], data_gaps=[])
                        episodes.append(active)
                        candidate = None
                        status = 'active'
                        reference = active
                    else:
                        status = 'candidate'
                        reference = candidate
                else:
                    candidate = None
            if active is None and candidate is None:
                reference = _baseline(cursor, by_date, rule)
                if not _valid(row):
                    status = 'missing_data'
                elif not reference['usable']:
                    status = 'insufficient_reference'
                elif row['n_total'] < rule['trigger_ratio'] * reference['baseline']:
                    candidate = dict(reference, started_on=day, trigger_observations=[_reference(row)],
                                     latest_observation=_observation(row), last_evaluated_date=day)
                    status = 'candidate'
                    reference = candidate
                else:
                    status = 'not_triggered'

        base = reference.get('baseline')
        usable = base is not None and base > 0 and reference['baseline_valid_days'] >= rule['min_baseline_days']
        frame = {
            'observed_date': day, 'n_total': row.get('n_total') if _valid(row) else None,
            'material_id': row.get('material_id'), 'material_version': row.get('material_version'),
            'baseline': base if usable else None,
            'trigger_threshold': base * rule['trigger_ratio'] if usable else None,
            'recovery_threshold': base * rule['recovery_ratio'] if usable else None,
            'rule_status': status,
        }
        series.append(frame)
        last = {'row': row, 'reference': deepcopy(reference), 'status': status,
                'episode': active or (reference if status == 'resolved' else None)}
        cursor += _DAY
    return episodes, candidate, series, last


def _evidence(alert):
    rule = alert['rule']
    latest = alert['latest_observation']
    return {
        'metric': 'PortWatch 可见通行量 n_total',
        'source_key': alert['source_key'], 'portid': alert['portid'],
        'rule': deepcopy(rule), 'status': alert['status'],
        'computed': {
            'started_on': alert['started_on'], 'triggered_on': alert['triggered_on'],
            'latest_observed_date': alert['last_evaluated_date'], 'resolved_on': alert['resolved_on'],
            'baseline': alert['baseline'], 'baseline_start': alert['baseline_start'],
            'baseline_end': alert['baseline_end'], 'baseline_valid_days': alert['baseline_valid_days'],
            'ratio': _ratio(latest, alert),
            'trigger_threshold': alert['baseline'] * rule['trigger_ratio'],
            'recovery_threshold': alert['baseline'] * rule['recovery_ratio'],
            'recovery_count': alert['recovery_count'],
        },
        'observations': {
            'trigger': deepcopy(alert['trigger_observations']),
            'latest': _reference(latest),
            'recovery': deepcopy(alert.get('recovery_observations', [])),
        },
        'reference_samples': deepcopy(alert['reference_samples']),
        'data_gaps': list(alert['data_gaps']),
        'limitations': [
            '计数和材料引用来自来源结构化字段；computed 和提醒状态由程序计算。',
            '这是 AIS 派生的可见通行计数，不等于全部船流、实际载货吨数或封航事实。',
            '阈值为未校准工程默认值；新闻未参与判定，不根据计数推断事件原因。',
            '观测日期不等于发布时间或系统首次记录提醒的时刻。',
        ],
    }


def _interval_end(episode):
    return episode.get('resolved_on') or episode['last_evaluated_date']


def _matches(episode, existing):
    if episode['started_on'] == existing['started_on']:
        return (2, 0)
    start = max(episode['started_on'], existing['started_on'])
    end = min(_interval_end(episode), _interval_end(existing))
    if start <= end:
        return (1, (date.fromisoformat(end) - date.fromisoformat(start)).days + 1)
    return (0, 0)


def _affected_dates(episode, previous, revised_dates):
    start = min(episode['baseline_start'], (previous or episode)['baseline_start'])
    end = max(_interval_end(episode), _interval_end(previous or episode))
    return [day for day in revised_dates if start <= day <= end]


def _original_trigger_holds(alert, by_date, rule):
    reference = _baseline(date.fromisoformat(alert['started_on']), by_date, rule)
    if not reference['usable']:
        return False
    start = date.fromisoformat(alert['started_on'])
    for offset in range(rule['trigger_days']):
        row = by_date.get((start + timedelta(days=offset)).isoformat())
        if not row or not _valid(row) or row['n_total'] >= rule['trigger_ratio'] * reference['baseline']:
            return False
    return True


def _finish_alert(alert, previous, checked_at, revised_dates):
    """Keep original evidence and expire explanations only when their evidence changes."""
    alert['summary'] = _summary(alert['status'], alert['latest_observation'], alert,
                                alert['recovery_count'], alert['rule'])
    alert['evidence'] = _evidence(alert)
    changed = previous is not None and previous.get('evidence') != alert['evidence']
    alert['evidence_version'] = previous.get('evidence_version', 1) + int(changed) if previous else 1
    alert['original_evidence'] = deepcopy(previous.get('original_evidence') or previous.get('evidence')) if previous else deepcopy(alert['evidence'])
    alert['changes'] = deepcopy(previous.get('changes', [])) if previous else []
    alert['explanation'] = deepcopy(previous.get('explanation')) if previous else None
    if changed and alert['explanation']:
        alert['explanation']['status'] = 'stale'
    if previous and changed and revised_dates:
        alert['initial_origin'] = previous.get('initial_origin', previous['origin'])
        alert['origin'] = 'revision'
        alert['changes'].append({
            'type': 'source_revision', 'at': checked_at, 'observed_dates': revised_dates,
            'previous_status': previous['status'], 'status': alert['status'],
            'evidence_version': alert['evidence_version'],
            'previous_evidence': deepcopy(previous.get('evidence')),
            'reason': '因数据修订撤销' if alert['status'] == 'revoked' else '因来源修订更正',
        })
    elif previous and previous['status'] != alert['status']:
        alert['changes'].append({
            'type': 'resolved' if alert['status'] == 'resolved' else 'status_change',
            'at': checked_at, 'observed_date': alert['last_evaluated_date'],
            'previous_status': previous['status'], 'status': alert['status'],
            'evidence_version': alert['evidence_version'],
        })
    return alert


def evaluate(rows, previous_state, previous_alerts, rule, checked_at,
             monitor_id, source_key, portid):
    """Return saved-state candidates, alerts, and a 90-day plot from current values.

    ``rows`` contains all locally saved current daily values, including dates older
    than the fetch window. ``checked_at`` is the caller's timezone-bearing system
    timestamp. Repeated fetch times never enter evidence comparisons.
    """
    previous_state = previous_state or {}
    if previous_state and (
            previous_state.get('source_key') != source_key
            or previous_state.get('portid') != portid
            or previous_state.get('rule', {}).get('id') != rule['id']):
        previous_state = {}
    by_date = {row['observed_date']: row for row in rows}
    previous_alerts = deepcopy(previous_alerts or [])
    rule = deepcopy(rule)
    old_through = previous_state.get('processed_through')
    initializing = not old_through
    old_versions = previous_state.get('input_versions', {})
    versions = {day: [row.get('material_id'), row.get('material_version'),
                      row.get('n_total'), row.get('validity')]
                for day, row in sorted(by_date.items())}
    revised_dates = sorted(day for day in set(versions) | set(old_versions)
                           if old_through and day <= old_through and versions.get(day) != old_versions.get(day))
    initialized_at = previous_state.get('initialized_at') or (checked_at if rows else None)
    cutoff = previous_state.get('initialization_cutoff') or (max(by_date) if by_date else None)
    if not by_date:
        state = dict(previous_state, id=monitor_id, monitor_id=monitor_id, source_key=source_key,
                     portid=portid, rule=rule, current_status='insufficient_reference',
                     latest_observed_date=None, latest_value=None, baseline=None, ratio=None,
                     baseline_start=None, baseline_end=None, baseline_valid_days=0,
                     active_alert_id=None, candidate=None, processed_through=None,
                     initialized_at=initialized_at, last_evaluated_at=checked_at, data_gaps=[],
                     summary='尚无已保存的日度观测，参考不足。', input_versions=versions,
                     initialization_cutoff=cutoff)
        return {'state': state, 'alerts': previous_alerts, 'series': []}

    episodes, candidate, series, last = _replay(by_date, rule)
    existing = [alert for alert in previous_alerts
                if alert.get('monitor_id') == monitor_id and alert.get('source_key') == source_key
                and alert.get('portid') == portid and alert.get('rule', {}).get('id') == rule['id']]
    result = {alert['id']: alert for alert in previous_alerts}
    unmatched = {alert['id']: alert for alert in existing}
    episode_ids = {}
    episode_starts = {episode['started_on'] for episode in episodes}
    for episode in episodes:
        eligible = [item for item in unmatched.values()
                    if (item['started_on'] == episode['started_on'] or item['started_on'] not in episode_starts)
                    and (not revised_dates or _original_trigger_holds(item, by_date, rule))]
        ranked = sorted(eligible,
                        key=lambda item: (_matches(episode, item), item['status'] != 'revoked'), reverse=True)
        previous = ranked[0] if ranked and _matches(episode, ranked[0])[0] else None
        affected = _affected_dates(episode, previous, revised_dates)
        if previous:
            unmatched.pop(previous['id'])
        elif initializing:
            if episode['status'] == 'resolved':
                continue
        elif not (episode['triggered_on'] > old_through or
                  (affected and (episode['triggered_on'] > cutoff or episode['status'] != 'resolved'))):
            # Historical episodes suppressed at initialization remain suppressed.
            continue

        identifier = previous['id'] if previous else f"pw_{monitor_id}_{rule['id']}_{episode['started_on']}"
        alert = deepcopy(episode)
        alert.pop('usable', None)
        alert.update(id=identifier, monitor_id=monitor_id, source_key=source_key, portid=portid,
                     rule=deepcopy(rule), detected_at=previous['detected_at'] if previous else checked_at,
                     origin=previous['origin'] if previous else ('initialization' if initializing else 'revision' if affected else 'update'))
        if previous and previous.get('initial_origin'):
            alert['initial_origin'] = previous['initial_origin']
        if alert['status'] == 'resolved':
            same_resolution = previous and previous['status'] == 'resolved' and previous.get('resolved_on') == alert['resolved_on']
            alert['resolved_at'] = previous.get('resolved_at') if same_resolution else checked_at
        else:
            alert['resolved_at'] = None
        result[identifier] = _finish_alert(alert, previous, checked_at, affected)
        episode_ids[episode['started_on']] = identifier

    for previous in unmatched.values():
        if previous['status'] == 'revoked':
            continue
        affected = _affected_dates(previous, previous, revised_dates)
        if not affected:
            # An earlier corrected recovery can merge away a later episode.
            affected = [day for day in revised_dates if day <= _interval_end(previous)]
        if not affected:
            continue
        alert = deepcopy(previous)
        alert.update(status='revoked', resolved_on=None, resolved_at=None,
                     revoked_at=checked_at, recovery_count=0, recovery_observations=[])
        # Original trigger/reference values stay visible as the withdrawn evidence.
        alert['last_evaluated_date'] = max(by_date)
        alert['latest_observation'] = _observation(by_date[max(by_date)])
        alert = _finish_alert(alert, previous, checked_at, affected)
        alert['evidence']['revision'] = {
            'reason': '原触发条件不再成立',
            'current_observations': [_reference(by_date[day]) if day in by_date else
                                     {'observed_date': day, 'n_total': None, 'validity': 'missing'}
                                     for day in affected],
        }
        result[alert['id']] = alert

    latest_date = max(by_date)
    chart_start = (date.fromisoformat(latest_date) - timedelta(days=rule['history_days'] - 1)).isoformat()
    visible_series = [item for item in series if item['observed_date'] >= chart_start]
    reference, latest, status = last['reference'], last['row'], last['status']
    active_episode = last['episode']
    active_alert_id = episode_ids.get(active_episode['started_on']) if active_episode and status != 'resolved' else None
    state = {
        'id': monitor_id, 'monitor_id': monitor_id, 'source_key': source_key, 'portid': portid,
        'rule': rule, 'current_status': status, 'latest_observed_date': latest_date,
        'latest_value': latest.get('n_total') if _valid(latest) else None,
        'latest_observation': _observation(latest),
        'baseline': reference.get('baseline'), 'ratio': _ratio(latest, reference) if reference.get('usable', True) else None,
        'baseline_start': reference['baseline_start'], 'baseline_end': reference['baseline_end'],
        'baseline_valid_days': reference['baseline_valid_days'],
        'reference_samples': deepcopy(reference['reference_samples']),
        'active_alert_id': active_alert_id, 'candidate': deepcopy(candidate),
        'processed_through': latest_date, 'initialized_at': initialized_at,
        'initialization_cutoff': cutoff, 'last_evaluated_at': checked_at,
        'data_gaps': [item['observed_date'] for item in visible_series if item['n_total'] is None],
        'summary': _summary(status, latest, reference, reference.get('recovery_count', 0), rule),
        'input_versions': versions,
    }
    alerts = sorted(result.values(), key=lambda item: (item['started_on'], item['detected_at']), reverse=True)
    return {'state': state, 'alerts': alerts, 'series': visible_series}
