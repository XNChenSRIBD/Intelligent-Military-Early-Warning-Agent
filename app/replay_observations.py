"""Numeric observation frames for the existing historical replay map."""

from datetime import datetime, timezone

from .replay_gnss_observations import load_frames as gnss_frames
from .replay_shipping_observations import load_frames as shipping_frames


def _stamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('观测时点须包含时区')
    return stamp.astimezone(timezone.utc)


def observation_snapshot(case_id, at=None):
    loaders = {'kharkiv': gnss_frames, 'hormuz': shipping_frames}
    if case_id not in loaders:
        return None
    frames = loaders[case_id]()
    if not frames:
        return None
    first_warning = next((frame for frame in frames if frame['state'] == 'warning'), None)
    first_attention = next((frame for frame in frames if frame['state'] == 'attention'), None)
    if at:
        chosen = next((frame for frame in frames if _stamp(frame['time']) == _stamp(at)), None)
        if chosen is None:
            raise ValueError('该观测时点没有回放记录')
    else:
        chosen = first_warning or first_attention or frames[-1]
    visible = [frame for frame in frames if _stamp(frame['time']) <= _stamp(chosen['time'])]
    risk = 'gnss_observation_quality' if case_id == 'kharkiv' else 'maritime_visible_flow'
    label = '接收站信号观测' if case_id == 'kharkiv' else '航运指标观测'
    identifier = f"observation:{case_id}:{chosen['time']}"
    observation = {key: chosen[key] for key in ('station_rows', 'metrics', 'details') if key in chosen}
    observation['history'] = [
        {key: frame[key] for key in ('time', 'start', 'state', 'metrics') if key in frame}
        for frame in visible
    ] if case_id == 'hormuz' else [
        {'time': frame['time'], 'start': frame['start'], 'state': frame['state'],
         'station_rows': [{key: row.get(key) for key in
             ('station', 'system', 'current', 'reference', 'state')} for row in frame['station_rows']]}
        for frame in visible
    ]
    decision = {
        'id': identifier, 'case_id': case_id, 'risk_object': risk, 'risk_label': label,
        'state': chosen['state'], 'title': chosen['title'], 'summary': chosen['summary'],
        'brief_evidence': chosen['brief_evidence'], 'as_of': chosen['time'],
        'observation_start': chosen['start'], 'analysis_version': 'historical_numeric_observations_v1',
        'observed_objects': sorted({row['station'] for row in chosen.get('station_rows', [])}),
        'evidence_refs': [], 'report': {'observation': observation},
        'timing': {'observation_start': chosen['start'], 'observation_end': chosen['time'],
                   'availability_basis': 'archived_observation_time'},
        'first_states': {state: {'as_of': frame['time'], 'observation_start': frame['start']}
                         for state, frame in [('warning', first_warning), ('attention', first_attention)]
                         if frame and _stamp(frame['time']) <= _stamp(chosen['time'])},
    }
    summaries = [{'station': station} for station in decision['observed_objects']]
    portwatch = None
    if case_id == 'hormuz':
        metric = next((row for row in chosen['metrics'] if row['key'] == 'n_total'), {})
        reference, current = metric.get('reference'), metric.get('current')
        portwatch = {'state': {'latest_value': current, 'baseline': reference,
            'ratio': current / reference if reference else None},
            'observations': [dict(observed_date=frame['start'][:10],
                **{row['key']: row.get('current') for row in frame['metrics']}) for frame in visible]}
    options = [{key: frame[key] for key in ('time', 'start', 'state', 'title')} for frame in frames]
    return {
        'id': identifier, 'case_id': case_id, 'as_of': chosen['time'],
        'primary': decision, 'primary_risk_object': risk, 'objects': {risk: decision},
        'view': {'case_id': case_id, 'as_of': chosen['time'],
                 'gnss': {'series': [], 'summary': summaries}, 'recent_work': [], 'alerts': []},
        'metrics': portwatch,
        'timeline': [dict(id=f"observation:{case_id}:{frame['time']}", risk_object=risk,
            as_of=frame['time'], observation_start=frame['start'], state=frame['state'],
            title=frame['title'], summary=frame['summary']) for frame in visible],
        'observation_replay': {'selected_at': chosen['time'], 'options': options,
            'first_warning_at': first_warning['time'] if first_warning else None,
            'first_attention_at': first_attention['time'] if first_attention else None},
    }
