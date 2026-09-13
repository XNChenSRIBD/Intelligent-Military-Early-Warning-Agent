"""Persisted, object-scoped historical judgments; no collection or model calls."""

from copy import deepcopy


RISK_OBJECTS = {
    'gnss': 'gnss_observation_quality',
    'portwatch': 'maritime_visible_flow',
}
OBJECT_LABELS = {
    'gnss_observation_quality': '接收站观测质量',
    'maritime_visible_flow': '海峡可见通行量',
}
SPATIAL_LIMITS = {
    'gnss_observation_quality': '结论仅对应实际接收站的可比观测；站点不等于案例城市或海峡的直接覆盖，不定位异常源，也不归因为人为干扰。',
    'maritime_visible_flow': '结论对应 PortWatch 海峡 AIS 派生可见通行量；名义运力不等于实载货量，计数不能代表全面停航或冲突预测。',
}


def successful_tools(work, *, executed_only=True):
    return [tool for tool in work.get('tool_results', [])
            if tool.get('chosen_by') == 'model' and (not executed_only or tool.get('executed', True))
            and not tool.get('error') and not (tool.get('result') or {}).get('error')
            and (tool.get('result') or {}).get('status') not in ('failed', 'unavailable', 'insufficient_coverage')
            and _tool_result(tool).get('status') not in ('failed', 'unavailable', 'insufficient_coverage')
            and (tool.get('result') or {}).get('materials')]


def _tool_result(tool):
    output = tool.get('result') or tool.get('output') or {}
    return output.get('result', output) if isinstance(output, dict) else {}


def _strings(values):
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def explanation_coverage(work):
    """Describe final reference selection without changing the model's judgment."""
    assessment = work.get('assessment', {})
    selected = set(assessment.get('evidence_refs') or [])
    selected_facts = set(assessment.get('fact_refs') or [])
    for fact in assessment.get('fact_evidence') or []:
        if fact.get('id') in selected_facts:
            selected.update(fact.get('material_ids') or [])
    items, unaddressed = [], []
    for tool in successful_tools(work, executed_only=False):
        ids = [material['id'] for material in (tool.get('result') or {}).get('materials', [])]
        cited = sorted(selected.intersection(ids))
        delivered = work['input']['id'] in (tool.get('followup_input_ids') or [])
        item = {'source': 'tool_return', 'tool': tool.get('name'), 'question': tool.get('question'),
                'material_ids': ids, 'selected_material_ids': cited,
                'delivered_to_final_judgment': delivered,
                'final_reference_status': 'selected' if cited else 'not_selected'}
        items.append(item)
        if delivered and not cited:
            summary = ('多站观测的共同变化与差异，尚未在本次结论中解释。'
                       if tool.get('name') == 'multistation_check' else
                       '这项补充证据尚未纳入本次结论的解释。')
            unaddressed.append({'kind': 'summary_detail', 'origin': 'program_reference_summary',
                'status': 'returned_not_selected', 'tool': tool.get('name'),
                'question': tool.get('question'), 'material_ids': ids, 'summary': summary})
    environment = work['input'].get('program', {}).get('environment_context') or {}
    for row in environment.get('rows', []):
        identifier = row['material_id']
        cited = identifier in selected
        items.append({'source': 'environment_context', 'date': row.get('date'),
            'material_ids': [identifier], 'selected_material_ids': [identifier] if cited else [],
            'delivery': 'initial_input', 'final_reference_status': 'selected' if cited else 'not_selected'})
        if not cited:
            unaddressed.append({'kind': 'alternative_explanation', 'origin': 'program_reference_summary',
                'status': 'input_not_selected', 'date': row.get('date'), 'material_ids': [identifier],
                'summary': '该日环境指标未用于本次结论，相关自然因素仍未排除。'})
    return {'basis': 'stored_input_tool_returns_and_final_references', 'items': items,
            'unaddressed': unaddressed,
            'note': '引用只表示最终选择了该材料；工具成功或材料被引用均不自动表示调查问题已经解决。'}


def _gnss_judgment(work):
    program = work['input'].get('program', {})
    inventory = program.get('evidence_inventory', {})
    assessment = work.get('assessment', {})
    tools = successful_tools(work, executed_only=False)
    tool_material_ids = {material['id'] for tool in tools if tool.get('name') in ('station_history', 'multistation_check')
                         for material in (tool.get('result') or {}).get('materials', [])}
    selected_ids = set(assessment.get('fact_refs') or [])
    facts = [fact for fact in assessment.get('fact_evidence', []) if fact.get('id') in selected_ids
             and fact.get('domain') == 'gnss' and fact.get('role') == 'comparison'
             and fact.get('source') == 'program_computation'
             and set(fact.get('material_ids') or []) & tool_material_ids
             and (fact.get('comparison') or {}).get('unit') == 'dB-Hz'
             and (fact.get('comparison') or {}).get('sample_relation') in ('below', 'above')]
    departures = [fact['comparison'] for fact in facts]
    support = assessment.get('descriptive_support') or {}
    supported = (bool(departures) and assessment.get('decision') in ('candidate', 'update')
                 and bool(assessment.get('model_interpretation'))
                 and all(support.get(key) for key in ('comparison_basis', 'observed_change', 'history_context', 'spatial_scope'))
                 and bool(assessment.get('evidence_refs'))
                 and work.get('result_quality') != 'analysis_explanation_issue')
    state = 'attention' if supported else None
    if supported:
        compared = departures[0]
        title = '接收站信号变化值得继续核查'
        summary = '已核查的接收站观测偏离有限历史样本，需继续核实变化原因；结论仅限这些实际站点。'
        direction = '低于' if compared['sample_relation'] == 'below' else '高于'
        brief = [f'已核对的 IGS 接收站信号质量{direction}同站历史样本范围。']
        if all(isinstance(compared.get(key), (int, float)) for key in ('current_p10', 'sample_min', 'sample_max')):
            brief.append(f"当前约 {compared['current_p10']:.1f} dB-Hz，参考约 {compared['sample_min']:.1f}～"
                         f"{compared['sample_max']:.1f} dB-Hz，来自 {compared.get('reference_day_count')} 天样本。")
        brief.append('样本偏离是核查线索，尚不能据此确定异常原因。')
    elif inventory.get('observation_present'):
        title = '当前观测尚不足以作出异常判断'
        summary = '已取得接收站观测；现有比较尚不能支持明确的异常或正常判定。'
        brief = ['已取得真实接收站观测，缺口集中在比较依据和解释范围。']
        if assessment.get('decision') == 'no_anomaly':
            title = '已核对观测未提供足够异常支持'
            summary = '当前比较没有形成受支持的异常线索；这不等于案例城市或海峡已被判定正常。'
        if inventory.get('reference_present'):
            brief.append('已有同站参考；按单位、信号和实际时间窗分别核对。')
    else:
        title = '暂未形成观测判断'
        summary = '本次可见资料尚不能支持接收站观测质量判定。'
        brief = []
    limitations = _strings(list(inventory.get('limitations') or []) + list(assessment.get('limitations') or []))
    if work.get('result_quality') == 'analysis_explanation_issue':
        limitations.append('本次分析解释与输入事实仍不一致，未采纳其业务结论。')
    availability = {
        'status': 'descriptive_only' if inventory.get('reference_present') else
                  'reference_limited' if inventory.get('observation_present') else 'observation_unavailable',
        'observation_present': bool(inventory.get('observation_present')),
        'reference_present': bool(inventory.get('reference_present')),
        'coverage_complete': inventory.get('coverage_complete', False),
        'limitations': limitations,
        'gaps': deepcopy(assessment.get('evidence_gaps') or []),
    }
    return dict(state=state, title=title, summary=summary, brief_evidence=brief[:3],
                availability=availability, supported_by=departures,
                observed_objects=sorted({item['station'] for item in departures}) if supported else inventory.get('observed_stations', []),
                rule={'id': 'gnss_descriptive_history_v1', 'red_condition': None,
                      'normal_condition': None,
                      'attention_condition': '真实工具返回可比历史样本偏离，且模型结合具体参考、范围与限制支持进一步核查；样本范围不是显著性或异常阈值。'},
                interpretation_limits=limitations)


def _maritime_judgment(work):
    program = work['input'].get('program', {})
    assessment = work.get('assessment', {})
    status, active = program.get('current_status'), program.get('active_alert_id')
    rule = program.get('rule') or {}
    enough = (program.get('baseline_valid_days', 0) >= rule.get('min_baseline_days', 21)
              and program.get('baseline') is not None and program['baseline'] > 0)
    value = program.get('latest_value')
    if active or status in ('active', 'recovering'):
        state, title = 'warning', '海峡可见通行量持续偏低'
        summary = '已满足连续低通行量条件，尚未满足持续恢复条件。'
    elif status == 'candidate':
        state, title = 'attention', '海峡通行量出现单日偏低'
        summary = '单日可见通行量低于历史参考的一半，需要继续观察能否持续。'
    elif status in ('not_triggered', 'resolved') and enough and value is not None:
        state, title = 'normal', '当前通行量未触发持续偏低规则'
        summary = '当前可见通行量与参考满足评价要求，继续监测。'
        if status == 'resolved':
            title, summary = '海峡通行量已满足恢复条件', '连续两日达到冻结参考的八成，原持续偏低提醒已解除。'
    else:
        state, title = None, '暂未形成通行量判断'
        summary = '本次日值或历史参考尚不能支持完整的规则评价。'
    brief = []
    if value is not None:
        brief.append(f"最新日通行量为 {value:g} 艘次（{program.get('latest_observed_date')}）。")
    if enough:
        brief.append(f"历史参考为 {program['baseline']:g} 艘次，来自 {program['baseline_valid_days']} 个有效日。")
    if state == 'warning':
        brief.append('连续两日低于冻结参考的 50%；未因缺少新资料自动解除。')
    elif state == 'attention':
        brief.append('目前为单日候选，尚未满足连续两日条件。')
    return dict(state=state, title=title, summary=summary, brief_evidence=brief[:3],
                availability={'status': 'sufficient' if enough and value is not None else 'limited',
                              'observation_present': value is not None, 'reference_present': enough,
                              'gaps': program.get('data_gaps', []),
                              'limitations': list(assessment.get('limitations') or [])},
                supported_by=deepcopy(program.get('trigger_observations') or program.get('observations') or []),
                observed_objects=['PortWatch / ' + str(program.get('portid', 'chokepoint6'))],
                rule=deepcopy(rule), interpretation_limits=list(assessment.get('limitations') or []))


def build_decision(work, evidence, previous=None):
    """Only consume the immutable input/output of this one judgment work."""
    risk = RISK_OBJECTS[work['kind']]
    judgment = _gnss_judgment(work) if work['kind'] == 'gnss' else _maritime_judgment(work)
    assessment = work.get('assessment', {})
    coverage = explanation_coverage(work)
    timestamp, as_of = work['completed_at'], work['as_of']
    times = [{key: material.get(key) for key in ('id', 'observed_at', 'available_at', 'first_seen_at',
             'system_acquired_at', 'catalog_registered_at', 'reuse_confirmed_at', 'imported_at',
             'acquisition_time_basis', 'acquisition_time_note', 'replay_release_at', 'time_note')} for material in evidence]
    simulated = any(item.get('available_at') is None for item in times)
    state = judgment['state']
    transition = None
    if previous is None or previous.get('state') != state:
        transition = {'from': (previous or {}).get('state'), 'to': state, 'as_of': as_of,
                      'published_at': timestamp, 'reason': judgment['summary']}
    first_states = deepcopy((previous or {}).get('first_states', {}))
    if state is not None and state not in first_states:
        first_states[state] = {'as_of': as_of, 'published_at': timestamp,
                              'observation_end': work['input'].get('observation_end')}
    return dict(judgment, id='decision:' + work['id'], case_id=work['case_id'], risk_object=risk,
        risk_label=OBJECT_LABELS[risk], analysis_version=work['analysis_version'],
        as_of=as_of, evidence_version=work['input_version'], work_id=work['id'],
        batch_index=work['batch_index'], revision_id=work['id'], scope_limit=SPATIAL_LIMITS[risk],
        previous_decision_id=(previous or {}).get('id'), transition=transition, first_states=first_states,
        evidence_snapshot=[{'id': material['id'], 'version': material.get('version'),
                            'resource_id': material.get('resource_id')} for material in evidence],
        evidence_refs=[{key: material.get(key) for key in ('id', 'version', 'title', 'source', 'url',
                       'observed_at', 'available_at', 'replay_release_at')} for material in evidence],
        evidence_relations={'support': deepcopy(judgment['supported_by']),
            'counter_or_limits': judgment['interpretation_limits'],
            'shared_sources': ['GNSS 原件与其派生统计、工具返回同源，不重复计为独立确认'] if work['kind'] == 'gnss' else
                              ['PortWatch AIS 派生日值与其构成、运力统计同源，不重复计为独立确认'],
            'unresolved_explanations': deepcopy(assessment.get('evidence_gaps') or []) + deepcopy(coverage['unaddressed'])},
        investigation={'questions': deepcopy(work['input'].get('program', {}).get('investigation_questions') or []),
                       'gaps': deepcopy(assessment.get('evidence_gaps') or []),
                       'explanation_coverage': coverage,
                       'tools': deepcopy(work.get('tool_results') or []),
                       'model_selected_count': sum(tool.get('chosen_by') == 'model' for tool in work.get('tool_results', [])),
                       'executed_count': sum(tool.get('chosen_by') == 'model' and tool.get('executed', True)
                                             for tool in work.get('tool_results', [])),
                       'successful_count': len(successful_tools(work)),
                       'program_calculations': deepcopy(work['input'].get('program_calculations') or [])},
        timing={'observation_start': work['input'].get('observation_start'),
                'observation_end': work['input'].get('observation_end'),
                'source_times': times, 'replay_release_at': as_of,
                'analysis_started_at': work.get('started_at'), 'formed_at': timestamp,
                'published_at': timestamp, 'availability_basis': 'simulated_arrival' if simulated else 'source_available_at',
                'historical_availability': 'unverified' if simulated else 'requires_source_verification',
                'valid_from': as_of, 'valid_until': None,
                'validity_basis': '适用于该回放截止与对应观测范围，按后续观测或来源修订更新；不按本次执行日期或页面刷新计时。'},
        report={'assessment': deepcopy(assessment), 'program': deepcopy(work['input'].get('program', {})),
                'material_ids': [material['id'] for material in evidence],
                'observation_material_ids': list(work.get('material_ids', []))},
        created_at=timestamp, published_at=timestamp, superseded=False)
