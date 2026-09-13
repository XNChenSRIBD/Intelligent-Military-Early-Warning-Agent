"""Export saved Round 6 business results without starting the application.

Example:
  python scripts/export_round6_results.py --data-dir /runtime/replays/round6 \
    --catalog-db /runtime/catalog/resources.sqlite3 --output-dir /runtime/replays/round6/reports

Optional --frontend-evidence JSON maps case IDs to actual review evidence:
  {"kharkiv": {"status": "reviewed", "snapshot_id": "...", "screenshots": ["..."],
                "card_report_consistent": true, "backend_logs_hidden": true},
   "online_8080": {"status": "unchanged", "basis": "..."}}
Each case may also supply assessment_scope_notes with work_id, run_id,
reviewed_at, question and next_action from review of that saved result.
This file reports observations; it never grants a pass from an absent review.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


CASES = {'kharkiv': '哈尔科夫', 'hormuz': '霍尔木兹'}
RISK_LABELS = {'gnss_observation_quality': '实际接收站观测质量',
               'maritime_visible_flow': '海峡 AIS 可见通行量'}
STATE_LABELS = {None: '本次不出具三色判断', 'normal': '判定正常',
                'attention': '发现可疑信号', 'warning': '明确预警信号'}
RELATION_LABELS = {'before_event_date': '早于历史对照日期',
                   'same_calendar_date': '与历史对照同一日期',
                   'after_event_date': '晚于历史对照日期'}
TERMINAL_FAILURES = {'failed', 'missing', 'source_missing', 'auth_required', 'blocked'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def read_records(database):
    """A SQLite read transaction gives all exports the same saved record snapshot."""
    records = defaultdict(list)
    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        for kind, value in db.execute('SELECT kind, data FROM records'):
            records[kind].append(json.loads(value))
    return records


def read_catalog(database, identifiers):
    if not database:
        return {}
    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        result = {}
        for identifier in sorted(identifiers):
            row = db.execute('SELECT data FROM resources WHERE id=?', (identifier,)).fetchone()
            if row:
                result[identifier] = json.loads(row[0])
        return result


def moment(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except ValueError:
        return None


def since(value, start):
    value, start = moment(value), moment(start)
    return bool(value and start and value >= start)


def picked(value, fields):
    return {field: value.get(field) for field in fields}


def active(records, kind, case_id):
    return [row for row in records.get(kind, []) if row.get('case_id') == case_id and not row.get('superseded')]


def tool_outcome(tool):
    output = tool.get('result') or tool.get('output') or {}
    output = output if isinstance(output, dict) else {}
    detail = output.get('result') if isinstance(output.get('result'), dict) else output
    status = detail.get('status') or output.get('status') or tool.get('status')
    error = tool.get('error') or output.get('error') or detail.get('error')
    successful = (not error and status not in {'failed', 'unavailable', 'insufficient_coverage'}
                  and bool(output.get('materials') or status == 'computed'))
    return output, detail, status, error, successful


def investigation_results(works, retained_tools=(), runs=(), decisions=()):
    choices, executed, successful, reused = 0, 0, 0, 0
    chains, failures = [], []
    program_calls = Counter()
    program_snapshots = set()
    program_reads = set()
    work_ids = {row['id'] for row in works}
    input_ids = {row.get('input', {}).get('id', row['id']) for row in works}
    coverage_by_work = {row.get('work_id'): row.get('investigation', {}).get('explanation_coverage', {}) for row in decisions}
    retained = [row for row in retained_tools if row.get('work_id') in work_ids]
    def tool_key(work_id, name, question, arguments):
        return (work_id, name, question or '', json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False))
    retained_by_key = {tool_key(row['work_id'], row['name'], row.get('question'), row.get('arguments')): row
                       for row in retained}
    for work in works:
        calculation_key = (work.get('case_id'), work.get('risk_object', work.get('kind')),
                           work.get('as_of'), work.get('input_version'))
        if calculation_key not in program_snapshots:
            program_snapshots.add(calculation_key)
            for name in work.get('input', {}).get('program_calculations', []):
                program_calls[str(name)] += 1
        for read in work.get('input', {}).get('program_archive_reads', []):
            for identifier in read.get('material_ids', []):
                program_reads.add((calculation_key, read.get('source'), identifier))
        for ordinal, tool in enumerate(work.get('tool_results', []), 1):
            if tool.get('chosen_by') != 'model':
                continue
            choices += 1
            did_execute = tool.get('executed', True)
            executed += int(did_execute)
            reused += int(not did_execute)
            output, detail, status, error, ok = tool_outcome(tool)
            successful += int(did_execute and ok)
            assessment = work.get('assessment') or {}
            original = retained_by_key.get(tool_key(work['id'], tool.get('name'), tool.get('question'), tool.get('arguments')))
            returned_ids = [row['id'] for row in output.get('materials', []) if row.get('id')]
            followup_ids = tool.get('followup_input_ids') or []
            followup = bool(followup_ids and work.get('input', {}).get('id', work['id']) in followup_ids)
            final_judgment_formed = (work.get('status') == 'completed'
                and work.get('result_quality') == 'assessed'
                and not assessment.get('analysis_explanation_problem'))
            reference_use = next((item for item in coverage_by_work.get(work['id'], {}).get('items', [])
                if item.get('source') == 'tool_return' and item.get('tool') == tool.get('name')
                and set(item.get('material_ids', [])) == set(returned_ids)), {})
            final_reference_selected = reference_use.get('final_reference_status') == 'selected'
            question_specific = bool(tool.get('question') and tool['question'].strip() != tool.get('name'))
            gaps = assessment.get('evidence_gaps') or []
            chain = {
                'work_id': work['id'], 'tool_ordinal': ordinal, 'as_of': work.get('as_of'),
                'question': tool.get('question'), 'model_selected': True,
                'question_specific': question_specific,
                'model_attempt': tool.get('model_attempt'), 'tool': tool.get('name'),
                'arguments': tool.get('arguments'), 'executed': did_execute, 'reused': not did_execute,
                'return_status': status or ('returned_materials' if ok else 'unconfirmed'),
                'return_material_ids': returned_ids,
                'return_summary': picked(detail, ('coverage', 'coverage_complete', 'reference_groups',
                    'descriptive_departures', 'unavailable_or_incomparable_stations', 'reason', 'limits')),
                'error': error, 'returned_to_same_judgment': followup,
                'final_judgment_formed': final_judgment_formed,
                'final_reference_selected': final_reference_selected,
                'followup_decision': assessment.get('decision') if followup else None,
                'followup_statement': assessment.get('statement') if followup else None,
                'followup_model_interpretation': assessment.get('model_interpretation') if followup else None,
                'question_resolution': [{'question': row.get('question'), 'resolved': row.get('resolved'),
                                        'resolution': row.get('resolution')} for row in gaps],
                'record_reference': f"records/work/{work['id']}#tool_results/{ordinal - 1}",
                'original_execution_record_id': (original or {}).get('id'),
                'actual_execution_preserved': did_execute or original is not None,
                'complete_chain': bool(question_specific and ok and followup and final_judgment_formed and final_reference_selected),
            }
            chains.append(chain)
            if not ok:
                failures.append(picked(chain, ('work_id', 'tool', 'arguments', 'return_status', 'error')))
    run_choices = [tool for run in runs for step in run.get('steps', [])
                   for tool in step.get('detail', {}).get('tools', [])
                   if tool.get('chosen_by') == 'model' and input_ids.intersection(tool.get('scope', {}).get('input_ids', []))]
    if run_choices:
        choices = len(run_choices)
        reused = sum(not tool.get('executed', True) for tool in run_choices)
    if retained:
        executed = len(retained)
        successful = sum(tool_outcome({'output': row['output']})[-1] for row in retained)
    return {'model_selected_count': choices, 'model_executed_count': executed,
            'model_successful_execution_count': successful, 'model_result_reuse_count': reused,
            'program_calculation_count': sum(program_calls.values()),
            'program_calculations_by_name': dict(program_calls),
            'program_archive_read_count': len(program_reads),
            'program_archive_material_count': len({row[2] for row in program_reads}),
            'complete_chain_count': sum(row['complete_chain'] and row['actual_execution_preserved'] for row in chains),
            'chains': chains, 'failed_or_incomplete_tools': failures,
            'count_note': '这里统计当前有效判断所用的模型调查。程序计算项目按对象、as_of、证据版本去重，解释修订复用原数值不重复算计算；不是函数调用性能统计。自主执行从唯一replay_tool_result记录计数，包含故障续接前已真实完成的调用；缓存读取不重复算执行。所有版本历史执行总量另列。'}


def resource_results(resources, catalog, run_start):
    identifiers = {row['catalog_id'] for row in resources if row.get('catalog_id')} | {
        identifier for row in resources for identifier in row.get('source_catalog_ids', [])}
    items = {identifier: catalog[identifier] for identifier in identifiers if identifier in catalog}
    new_downloads = [row for row in items.values() if row.get('downloaded') and since(row.get('fetched_at'), run_start)]
    new_results = [row for row in items.values() if since(row.get('result_saved_at'), run_start)]
    consumed_ids = {row['catalog_id'] for row in resources if row.get('catalog_id') and row.get('status') == 'processed'}
    preexisting_results = [row for identifier, row in items.items() if identifier in consumed_ids
        and row.get('compute_status') == 'completed' and moment(row.get('result_saved_at'))
        and moment(run_start) and moment(row['result_saved_at']) < moment(run_start)]
    resource_times = []
    for row in resources:
        managed = catalog.get(row.get('catalog_id'), {})
        resource_times.append({**picked(row, ('id', 'kind', 'role', 'station', 'observed_start', 'observed_end',
            'available_at', 'replay_release_at', 'release_assumption', 'imported_at', 'status', 'catalog_id')),
            'system_acquired_at': managed.get('fetched_at'),
            'acquired_time_note': '共享台账最近一次实际取得时间；不冒充历史首次公开时间或未记录的系统首次取得时间。',
            'first_system_acquired_at': managed.get('first_fetched_at'),
            'result_saved_at': managed.get('result_saved_at')})
    complete_catalog = len(items) == len(identifiers)
    by_kind = {}
    for kind in sorted({row['kind'] for row in resources}):
        group = [row for row in resources if row['kind'] == kind]
        by_kind[kind] = {'registered': len(group), 'observation': sum(row.get('role') != 'baseline' for row in group),
                        'reference': sum(row.get('role') == 'baseline' for row in group),
                        'statuses': dict(Counter(row.get('status', 'unknown') for row in group))}
    return {
        'registered_count': len(resources), 'by_kind': by_kind,
        'processed_count': sum(row.get('status') == 'processed' for row in resources),
        'missing_or_failed': [picked(row, ('id', 'kind', 'station', 'role', 'status', 'error'))
                              for row in resources if row.get('status') in TERMINAL_FAILURES],
        'pending': [picked(row, ('id', 'kind', 'status', 'error')) for row in resources
                    if row.get('status') != 'processed' and row.get('status') not in TERMINAL_FAILURES],
        'catalog_matched_count': len(items), 'catalog_counts_complete': complete_catalog,
        'historical_reference_source_resources': len({identifier for row in resources for identifier in row.get('source_catalog_ids', [])}),
        'catalog_original_reused_count': sum(bool(row.get('original_reused')) for row in items.values()) if catalog else None,
        'catalog_result_reused_count': sum(bool(row.get('result_reused')) for row in items.values()) if catalog else None,
        'preexisting_results_consumed_count': len(preexisting_results) if catalog and run_start else None,
        'preexisting_results_consumed_by_kind': dict(Counter(row['kind'] for row in preexisting_results)),
        'compatible_calculated_resources': sum(row.get('compute_status') == 'completed' for row in items.values()) if catalog else None,
        'new_downloads_since_run_start': len(new_downloads) if catalog and run_start else None,
        'new_result_versions_since_run_start': len(new_results) if catalog and run_start else None,
        'accounting_start': run_start,
        'new_download_bytes_since_start': sum(row.get('bytes', 0) or 0 for row in new_downloads) if catalog else None,
        'new_result_version_bytes_since_start': sum(row.get('result_bytes', 0) or 0 for row in new_results) if catalog else None,
        'count_note': '按catalog_id去重。旧特征使用量要求本例已导入且result_saved_at早于本轮起点；台账result_reused是另一流程标记，为0不表示没有读取旧特征。新补取仅计实际fetched_at在起点后的下载。没有基期占用记录时不推算总容量净变化。',
        'products': sorted({str(row.get('product') or row.get('source') or row['kind']) for row in resources}),
        'stations': sorted({row['station'] for row in resources if row.get('station')}),
        'resource_times': resource_times,
    }


def timing_relation(first, annotation):
    if not first:
        return {'status': 'not_triggered', 'strict_lead_hours': None, 'calendar_date_relation': None}
    timing = first.get('timing') or {}
    as_of, event_date = first.get('as_of'), annotation.get('event_date')
    relation = None
    if as_of and event_date:
        day = as_of[:10]
        relation = 'before_event_date' if day < event_date else 'same_calendar_date' if day == event_date else 'after_event_date'
    return {'status': 'simulated_arrival_only' if timing.get('historical_availability') != 'verified' else 'requires_event_precision',
            'as_of': as_of, 'observation_end': timing.get('observation_end'),
            'availability_basis': timing.get('availability_basis'), 'calendar_date_relation': relation,
            'strict_lead_hours': None,
            'reason': '按回放截止与事件来源日期作日历对照；历史可得性未经证实或事件时刻/时区不明确，不计算精确小时提前量。'}


def case_result(case_id, records, catalog, annotations, frontend, run_start):
    case = next(iter(active(records, 'replay_case', case_id)), {'case_id': case_id, 'status': 'not_run'})
    resources, works = active(records, 'replay_resource', case_id), active(records, 'work', case_id)
    decisions = sorted(active(records, 'replay_decision', case_id), key=lambda row: (row.get('as_of', ''), row.get('published_at', '')))
    snapshots = sorted(active(records, 'replay_snapshot', case_id), key=lambda row: (row.get('as_of', ''), row.get('published_at', '')))
    latest_snapshot = snapshots[-1] if snapshots else None
    case_runs = [row for row in records.get('run', []) if row.get('case_id') == case_id and row.get('mode') == 'pipeline_analysis']
    inputs = resource_results(resources, catalog, run_start)
    investigation = investigation_results(works, records.get('replay_tool_result', []), case_runs, decisions)
    all_case_tools = [row for row in records.get('replay_tool_result', []) if row.get('case_id') == case_id]
    investigation['all_versions_preserved_execution_count'] = len(all_case_tools)
    investigation['all_versions_preserved_successful_execution_count'] = sum(
        tool_outcome({'output': row['output']})[-1] for row in all_case_tools)
    investigation['historical_execution_note'] = '所有版本总量包括后续被修订替代的真实工具执行；旧解释不作为当前业务支持，但调用事实不抹去。'
    objects = {}
    timeline = []
    for risk in sorted({row['risk_object'] for row in decisions}):
        rows = [row for row in decisions if row['risk_object'] == risk]
        first = {state: next((row for row in rows if row.get('state') == state), None)
                 for state in ('normal', 'attention', 'warning')}
        last = rows[-1]
        objects[risk] = {'label': RISK_LABELS.get(risk, risk),
            'latest': picked(last, ('id', 'revision_id', 'as_of', 'state', 'title', 'summary', 'brief_evidence',
                'scope_limit', 'availability', 'rule', 'evidence_refs', 'evidence_relations', 'timing')),
            'first_states': {state: picked(row, ('id', 'as_of', 'published_at', 'state')) if row else None
                             for state, row in first.items()},
            'updates': [{'id': row['id'], 'as_of': row['as_of'], 'state': row.get('state'), 'transition': row.get('transition')}
                        for row in rows[1:]],
            'recovery': [picked(row, ('id', 'as_of', 'transition', 'summary')) for row in rows
                         if row.get('transition') and row['transition'].get('from') in ('attention', 'warning')
                         and row['transition'].get('to') == 'normal'],
            'continued_warning': last.get('state') == 'warning',
            'historical_relations': {state: timing_relation(first[state], annotations.get(case_id, {}))
                                     for state in ('attention', 'warning')}}
    for row in decisions:
        timeline.append(picked(row, ('id', 'revision_id', 'case_id', 'risk_object', 'analysis_version', 'as_of',
            'evidence_version', 'state', 'title', 'summary', 'brief_evidence', 'availability', 'transition',
            'scope_limit', 'evidence_snapshot', 'timing', 'work_id', 'published_at')))
    uncompleted = [picked(row, ('id', 'kind', 'status', 'as_of', 'error', 'result_quality'))
                   for row in works if row.get('status') != 'completed']
    explanation_issues = [row['id'] for row in works if row.get('result_quality') == 'analysis_explanation_issue'
                          or row.get('assessment', {}).get('analysis_explanation_problem')]
    budget_issues = [row['id'] for row in works if row.get('result_quality') in ('budget_exhausted', 'investigation_incomplete')]
    inventories = [{'work_id': row['id'], 'as_of': row.get('as_of'), **row.get('input', {}).get('program', {}).get('evidence_inventory', {})}
                   for row in works if row.get('kind') == 'gnss']
    raw_gaps = [(row['id'], gap) for row in works for gap in row.get('assessment', {}).get('evidence_gaps', []) if not gap.get('resolved')]
    unresolved = [{'work_id': identifier, **gap, 'origin': 'saved_model_gap_not_independently_verified',
        'next_action': (f"先核对该工作已有 {gap['tool']} 返回与原缺口；已有答案时完善解释，尚未回答时才进行对应调查"
                        if gap.get('tool') else '先对照该工作已保存的单位、参考与来源信息，区分解释问题和真实资料缺口后处理')}
                  for identifier, gap in raw_gaps]
    unresolved += [{'resource_id': row['id'], 'question': row.get('error') or '该资源尚未成功进入判断',
                    'kind': row.get('status'), 'next_action': '沿当前台账资源的已注册来源续接获取或处理'}
                   for row in inputs['missing_or_failed'] + inputs['pending']]
    unresolved += [{'work_id': identifier, 'question': '本次分析尚未形成可支持的完整解释或调查',
                    'next_action': '读取该工作真实返回，修复已记录的具体解释或预算断点后从此工作续接'}
                   for identifier in explanation_issues + budget_issues]
    evidence_use = [{'work_id': row['work_id'], 'as_of': row['as_of'],
        **row.get('investigation', {}).get('explanation_coverage', {})} for row in decisions if row.get('work_id')]
    unaddressed_evidence = [{'work_id': row['work_id'], **item,
        'question': item.get('question') or item.get('summary'),
        'next_action': '结合已保存的该项证据，明确其对当前有限结论的支持、限制或差异；无须重新下载原件'}
        for row in evidence_use for item in row.get('unaddressed', [])]
    unresolved += unaddressed_evidence
    completed_batches = case.get('completed_batches', 0)
    backend_done = bool(works and decisions and case.get('total_batches') and completed_batches == case['total_batches']
                        and not uncompleted and not inputs['missing_or_failed'] and not inputs['pending'] and not budget_issues)
    frontend_case = frontend.get(case_id, {'status': 'not_reviewed', 'reason': 'SQLite不能证明实机页面和截图已验收。'})
    work_by_id = {row['id']: row for row in works}
    case_run_ids = {row['id'] for row in case_runs}
    scope_notes = []
    for note in frontend_case.get('assessment_scope_notes', []):
        reviewed_work = work_by_id.get(note.get('work_id'))
        if (not reviewed_work or reviewed_work.get('status') != 'completed'
                or reviewed_work.get('run_id') != note.get('run_id')
                or note.get('run_id') not in case_run_ids or not note.get('question')):
            continue
        scope_notes.append({**picked(note, ('work_id', 'run_id', 'reviewed_at', 'question', 'next_action')),
            'as_of': reviewed_work.get('as_of'), 'origin': 'delivery_review_of_saved_work'})
    unresolved += scope_notes
    snapshot_ids = set((latest_snapshot or {}).get('decision_ids', {}).values())
    existing_ids = {row['id'] for row in decisions}
    model_attempts = sum(len(step.get('detail', {}).get('attempts', [])) for row in case_runs for step in row.get('steps', []))
    budget_adjustments = [{'run_id': run['id'], 'attempt': attempt.get('attempt'),
        **attempt['budget_adjustment']} for run in case_runs for step in run.get('steps', [])
        for attempt in step.get('detail', {}).get('attempts', []) if attempt.get('budget_adjustment')]
    backend_done = backend_done and model_attempts > 0
    page_reviewed = (frontend_case.get('status') == 'reviewed'
                     and frontend_case.get('card_report_consistent') is True
                     and frontend_case.get('backend_logs_hidden') is True
                     and frontend_case.get('snapshot_id') == (latest_snapshot or {}).get('id'))
    resume = frontend_case.get('execution_resume') or {
        'status': 'not_demonstrated_by_records', 'basis': '单次完成和重复attempt不等于已经实演进程重启续接。'}
    return {
        'case_id': case_id, 'label': CASES[case_id], 'instance_status': case.get('status'),
        'analysis_versions': sorted({row['analysis_version'] for row in works if row.get('analysis_version')}),
        'input_execution': {**inputs, 'total_batches': case.get('total_batches'), 'released_batches': case.get('released_batches'),
            'completed_batches': completed_batches, 'work_counts': dict(Counter(row.get('status') for row in works)),
            'unfinished_work': uncompleted, 'saved_model_attempts': model_attempts,
            'conditional_fact_corrections': budget_adjustments,
            'model_attempt_count_note': '统计本案例全部已保存analysis run（包括失败与后续被替代的运行），不删除失败记录或只报末次成功。',
            'superseded_work_count': sum(row.get('case_id') == case_id and bool(row.get('superseded')) for row in records.get('work', [])),
            'restart_resume': resume},
        'data_interpretation': {'inventories_by_as_of': inventories, 'analysis_explanation_issues': explanation_issues,
            'assessment_scope_notes': scope_notes,
            'evidence_use_by_as_of': evidence_use,
            'incomplete_analysis_work_ids': budget_issues,
            'descriptive_by_as_of': [{'as_of': row['as_of'], 'work_id': row['id'], 'kind': row['kind'],
                'comparison_fragments': row.get('input', {}).get('program', {}).get('comparison_fragments', []),
                'flow_context': row.get('input', {}).get('program', {}).get('flow_context'),
                'environment_context': row.get('input', {}).get('program', {}).get('environment_context', []),
                'archived_references': row.get('input', {}).get('program', {}).get('archive_index', []),
                'model_interpretation': row.get('assessment', {}).get('model_interpretation'),
                'selected_facts': row.get('assessment', {}).get('fact_evidence', [])}
                for row in sorted(works, key=lambda item: item['as_of']) if row.get('status') == 'completed'],
            'statistics_note': '完整信号目录、有效样本和参考日期组来自实际保存输入；不同p10口径不相减，receiver_units不标作dB-Hz。'},
        'autonomous_investigation': investigation, 'risk_objects': objects,
        'temporal_evaluation': {'annotation': annotations.get(case_id), 'strict_lead_hours': None,
            'historical_availability': 'not_verified',
            'delay_components': {'source_publication_wait': None, 'replay_batch_wait': None,
                'rule_persistence': 'pw_lowflow_v1要求连续两日；触发as_of见航运时间线' if case_id == 'hormuz' else None,
                'compute_and_queue': [{'work_id': row['id'], 'queued_at': row.get('created_at'),
                    'started_at': row.get('started_at'), 'completed_at': row.get('completed_at')} for row in works]},
            'evaluation_only': True, 'military_prediction_validation': 'not_validated'},
        'frontend': {**frontend_case, 'latest_snapshot_id': (latest_snapshot or {}).get('id'),
            'persisted_snapshot_references_resolve': bool(snapshot_ids and snapshot_ids.issubset(existing_ids)),
            'online_8080': frontend.get('online_8080', {'status': 'not_reviewed'})},
        'acceptance_layers': {
            'engineering_execution': {'status': 'completed' if backend_done and page_reviewed and resume.get('status') == 'demonstrated' else 'partial',
                'backend_completed': backend_done, 'page_reviewed': page_reviewed,
                'restart_resume_demonstrated': resume.get('status') == 'demonstrated'},
            'evidence_and_autonomous_investigation': {
                'status': ('demonstrated' if not explanation_issues and not budget_issues and not uncompleted
                           and not unaddressed_evidence and not scope_notes else 'partial')
                    if investigation['complete_chain_count'] else 'not_demonstrated',
                'investigation_demonstrated': bool(investigation['complete_chain_count']),
                'complete_chain_count': investigation['complete_chain_count'], 'explanation_issue_count': len(explanation_issues),
                'incomplete_analysis_count': len(budget_issues) + len(uncompleted),
                'assessment_scope_note_count': len(scope_notes),
                'unaddressed_evidence_count': len(unaddressed_evidence)},
            'observation_anomaly': {'status': 'object_scoped_results_saved' if objects else 'not_formed',
                'states': {risk: value['latest']['state'] for risk, value in objects.items()},
                'note': '各对象分别成立或无法判断；观测闭环不替代热冲突事前预警验收。'},
            'historical_prior_availability': {'status': 'not_verified', 'strict_lead_hours': None},
            'original_military_early_warning_goal': {'status': 'not_validated',
                'reason': '事件注释仅用于独立历史先后评价，本轮不建设或证明军事行动预测。'}},
        'unresolved_items': unresolved,
        'business_report_file': f'ROUND6_{case_id.upper()}_REPORT.md',
        'timeline_file': f'ROUND6_{case_id.upper()}_TIMELINE.json',
    }, timeline


def markdown_report(result, timeline):
    execution, investigation = result['input_execution'], result['autonomous_investigation']
    layers = result['acceptance_layers']
    lines = [f"# {result['label']}历史观测业务报告", '',
        f"本报告从同一已保存结果快照生成。案例状态：{result['instance_status']}。", '',
        '## 业务结论', '']
    for value in result['risk_objects'].values():
        last = value['latest']
        lines += [f"### {value['label']}", '', f"**{last['title']}**（{STATE_LABELS.get(last['state'])}，截至 {last['as_of']}）", '',
                  last['summary'], '', *['- ' + text for text in last.get('brief_evidence') or []], '', last['scope_limit'], '']
        for limit in last.get('availability', {}).get('limitations', []):
            lines.append('- ' + str(limit))
        lines += ['', f"结论引用：`{last['id']}`；修订：`{last['revision_id']}`。", '']
        lines += ['| 首次状态 | 回放可见截止 | 时间评价 |', '|---|---|---|']
        for state in ('attention', 'warning'):
            first = value['first_states'][state]
            relation = value['historical_relations'][state]
            timing_text = (RELATION_LABELS.get(relation.get('calendar_date_relation'), '无法核定日历关系') +
                           '；仅为模拟到达，不证明历史事前可得') if first else '未触发'
            lines.append(f"| {STATE_LABELS[state]} | {first['as_of'] if first else '未触发'} | {timing_text} |")
        if value['recovery']:
            lines += ['', '已记录恢复或撤销：' + '；'.join(row['as_of'] for row in value['recovery']) + '。', '']
        elif value['continued_warning']:
            lines += ['', '截至最后观测仍未达到规则恢复条件，预警持续；没有新资料不会自动解除。', '']
        else:
            lines += ['', '当前没有已记录的预警恢复转换；不据此推断未观测时段的状态。', '']
    if not result['risk_objects']:
        lines += ['尚无已保存业务判断，不能由资源数量推断案例正常或异常。', '']
    lines += ['## 数据与执行', '',
        f"已完成批次 {execution['completed_batches']} / {execution['total_batches']}；已保存模型请求 {execution['saved_model_attempts']}。", '',
        f"登记资源 {execution['registered_count']}，导入成功 {execution['processed_count']}，缺失或失败 {len(execution['missing_or_failed'])}，待处理 {len(execution['pending'])}。", '',
        f"实际站点：{'、'.join(execution['stations']) or '无已登记站点'}。", '',
        f"台账原件复用：{execution['catalog_original_reused_count']}；已导入本轮以前保存的兼容特征：{execution['preexisting_results_consumed_count']}；统计起点后新下载：{execution['new_downloads_since_run_start']}。", '',
        execution['count_note'], '']
    descriptions = result['data_interpretation']['descriptive_by_as_of']
    flows = [row for row in descriptions if row.get('flow_context')]
    if flows:
        lines += ['## 日度船流构成', '',
            '以下为真实日表及其派生值；名义运力不是实际货物装载量，单位船次名义运力只用于同产品构成对照。', '',
            '| 观测日 | 总船次 | 油轮船次 | 货船船次 | 名义总运力 | 每船次名义运力 | 每船次运力日变化 |',
            '|---|---:|---:|---:|---:|---:|---:|']
        def display(value):
            return '不可计算' if value is None else f'{value:,.2f}' if isinstance(value, (float, int)) else str(value)
        for item in flows:
            flow = item['flow_context']
            latest = flow.get('latest', {})
            change = flow.get('changes', {}).get('capacity_per_ship', {}).get('change_pct')
            lines.append('| ' + ' | '.join(display(latest.get(key)) for key in
                ('observed_date', 'n_total', 'n_tanker', 'n_cargo', 'capacity', 'capacity_per_ship')) +
                ' | ' + (display(change) + '%' if change is not None else '不可计算') + ' |')
        lines += ['', '完整分类运力、前日值、原始引用及两段历史参考索引保存在机器结果同一 as_of 下。', '']
    fragments = [(item, fragment, group) for item in descriptions for fragment in item['comparison_fragments']
                 for group in fragment.get('reference_groups', [])]
    if fragments:
        lines += ['## GNSS 描述性历史比较', '',
            '表中片段按可比覆盖、站名与信号代码选择，未按异常幅度选择；完整信号目录保留在对应判断输入中。各月份独立比较，有限样本范围不是异常概率。', '',
            '| 可见截止 | 实际站点 / 信号 | 参考日期 | 配对窗口数 | 窗口差值中位数 dB |',
            '|---|---|---|---:|---:|']
        for item, fragment, group in fragments:
            value = group.get('window_difference_median')
            value = f'{value:.3f}' if isinstance(value, (int, float)) else '不可比'
            lines.append(f"| {item['as_of']} | {fragment['station']} / {fragment['signal']} | " +
                         '、'.join(group.get('dates', [])) + f" | {group.get('matched_window_count')} | {value} |")
        lines += ['']
    environments = [(item, item['environment_context']) for item in descriptions
                    if isinstance(item.get('environment_context'), dict)]
    if environments:
        lines += ['## 归档环境参考', '',
            '以下指标从该判断原目录中已经可见的 GFZ 归档读取，属于程序资料整理。日期单独列示，不能用全球背景指标直接判定某站变化原因；历史首次可得时间仍未核实。', '',
            '| 判断可见截止 | 资料日期 | Kp 三小时序列 | Ap 日值 | F10.7 观测 / 调整值 |',
            '|---|---|---|---|---|']
        def env_value(value):
            if value is None:
                return '原件未提供'
            if isinstance(value, list):
                return '、'.join(str(item) for item in value)
            return str(value)
        for item, context in environments:
            for row in context.get('rows', []):
                lines.append(f"| {item['as_of']} | {row['date']}（{row['relation']}） | " +
                    env_value(row.get('Kp_3hour')) + ' | ' + env_value(row.get('Ap')) + ' | ' +
                    env_value(row.get('F10_7_observed')) + ' / ' + env_value(row.get('F10_7_adjusted')) + ' |')
        context = environments[-1][1]
        lines += ['', '原件时间系统：' + str(context.get('time_system')) + '；单位：' +
            json.dumps(context.get('units'), ensure_ascii=False) + '。', '',
            '完整 ap 序列、版本标识及逐条材料引用保存在同一机器结果的 environment_context 中。', '']
    lines += ['## 实际自主调查', '',
        f"模型选择 {investigation['model_selected_count']} 次；真实执行 {investigation['model_executed_count']} 次；成功执行 {investigation['model_successful_execution_count']} 次；复用既有返回 {investigation['model_result_reuse_count']} 次。", '',
        f"程序必做计算项目 {investigation['program_calculation_count']} 次，与自主工具分开计数。", '',
        f"程序归档读取 {investigation['program_archive_read_count']} 个判断/材料对，涉及 {investigation['program_archive_material_count']} 份独立归档；不计为模型选择工具或重新计算。", '']
    lines += [f"包括被替代判断在内，本例所有版本共保留 {investigation['all_versions_preserved_execution_count']} 次真实工具执行。", '',
              investigation['count_note'], '']
    corrections = execution.get('conditional_fact_corrections') or []
    if corrections:
        lines += ['本例实际使用的条件性第五次事实纠正如下。只用于已完成两项必要专业核查、第四次才出现事实冲突且此前未修复的判断；没有增加工具或一般重试。', '',
            '| 实际运行 | 请求 | 结果 |', '|---|---:|---|']
        for correction in corrections:
            lines.append(f"| {correction['run_id']} | {correction['attempt']} | {correction.get('outcome', '已保存使用记录')} |")
        lines.append('')
    if investigation['chains']:
        lines += [f"其中 {investigation['complete_chain_count']} 条保存了问题、模型选择、真实返回及同一判断中的最终引用。返回成功、被引用与问题得到解释分别记录，不能互相代替。", '',
            '| 可见截止 | 工具 | 真实返回 | 回到同一判断 | 最终引用 | 引用链完整 |', '|---|---|---|---|---|---|']
        for chain in investigation['chains']:
            values = ['是' if chain[key] else '否' for key in (
                'returned_to_same_judgment', 'final_reference_selected', 'complete_chain')]
            lines.append(f"| {chain['as_of']} | {chain['tool']} | {chain['return_status']} | " + ' | '.join(values) + ' |')
        example = next((row for row in investigation['chains'] if row['complete_chain']), investigation['chains'][0])
        facts = (example['followup_statement'] or '').splitlines()
        lines += ['', '一条实际调查链：', '', f"- 问题：{example['question'] or '未记录'}",
            f"- 模型选择：{example['tool']}；参数：`{json.dumps(example['arguments'], ensure_ascii=False)}`。",
            f"- 真实返回：{example['return_status']}；材料：{', '.join(example['return_material_ids']) or '无'}。",
            '- 同一判断保留的部分事实：' + ('；'.join(facts[:2]) or '尚未形成可支持的事实说明'),
            '- 模型后续解释原文：' + (example['followup_model_interpretation'] or example['followup_decision'] or '未形成'),
            f"- 原记录引用：`{example['record_reference']}`。", '',
            '完整工具参数、返回、最终引用情况和各次后续判断均保留在机器结果的 autonomous_investigation.chains 中；原始请求留在既有数据库。仅写工具名称的问题原文照录，不将程序预置问题冒称模型自己的完整提问，也不计入上述具体问题引用链。', '']
    if not investigation['chains']:
        lines += ['尚未演示模型自主调查，不能把程序计算或已注册工具记为通过。', '']
    lines += ['## 状态时间线', '', '| 可见截止 as_of | 对象 | 状态 | 结论 |', '|---|---|---|---|']
    for row in timeline:
        lines.append(f"| {row['as_of']} | {RISK_LABELS.get(row['risk_object'], row['risk_object'])} | {STATE_LABELS.get(row['state'])} | {row['title']} |")
    annotation = result['temporal_evaluation'].get('annotation') or {}
    lines += ['', '## 独立历史评价', '',
        f"历史对照定义：{annotation.get('event_definition', '未核定')}；日期：{annotation.get('event_date', '未知')}；精度：{annotation.get('precision', '未知')}。", '',
        f"来源：[{annotation.get('source_title', '未提供')}]({annotation.get('source_url', '')})。", '',
        annotation.get('time_note', '缺少精确时刻或时区。'), '',
        '各对象首黄、首红与事件日期的日历关系保存在机器结果中。来源历史可得性未证实，严格提前小时数为空；模拟释放不能变成真实历史提前预警。', '',
        '热冲突预警目标保持单独未验证。', '', '## 分层完成状态', '']
    layer_names = {'engineering_execution': '工程执行闭环',
        'evidence_and_autonomous_investigation': '证据与自主核查闭环',
        'observation_anomaly': '观测异常业务', 'historical_prior_availability': '历史事前可得性',
        'original_military_early_warning_goal': '原热冲突预警目标'}
    layer_states = {'completed': '完成', 'demonstrated': '已演示', 'partial': '部分完成',
        'not_demonstrated': '未演示', 'object_scoped_results_saved': '已按对象保存结论',
        'not_verified': '未验证', 'not_validated': '未验证', 'not_formed': '未形成'}
    for name, layer in layers.items():
        lines.append(f"- {layer_names[name]}：{layer_states.get(layer['status'], layer['status'])}。")
    evidence_layer = layers['evidence_and_autonomous_investigation']
    lines += ['', f"实际调查链 {evidence_layer['complete_chain_count']} 条；解释问题 {evidence_layer['explanation_issue_count']} 项；尚未纳入解释的工具或环境证据 {evidence_layer['unaddressed_evidence_count']} 项。调查链已演示不代表全部待核问题均已解决。"]
    lines += ['', '## 尚未解决的具体问题', '',
        '以下保留模型未解问题、未采用的证据及交付复核注记。模型原始缺口表述不等于已核实的数据缺失；部分工具已经返回，下一步应先核对已有答案与解释，不能仅因再次出现工具名就重跑或重新下载。各项来源和对应工作保存在机器结果中。', '']
    unresolved_counts = Counter((item.get('question'), item.get('next_action')) for item in result['unresolved_items'])
    for (question, action), count in unresolved_counts.items():
        scope = f'（涉及 {count} 项判断或材料）' if count > 1 else ''
        lines.append(f"- {question}{scope} 后续动作：{action}。")
    if not result['unresolved_items']:
        lines.append('没有额外结构化缺口记录；历史可得性和事件预测能力仍按上述独立层级保留未验证。')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--catalog-db', type=Path)
    parser.add_argument('--run-started-at', help='Actual Round 6 accounting start, including any pre-start acquisitions')
    parser.add_argument('--annotations', type=Path, default=Path(__file__).resolve().parents[1] / 'cases/round6/evaluation_annotations.json')
    parser.add_argument('--frontend-evidence', type=Path)
    args = parser.parse_args()
    records = read_records(args.data_dir / 'demo.sqlite3')
    catalog_ids = {row['catalog_id'] for row in records.get('replay_resource', []) if row.get('catalog_id')} | {
        identifier for row in records.get('replay_resource', []) for identifier in row.get('source_catalog_ids', [])}
    catalog = read_catalog(args.catalog_db, catalog_ids)
    annotations = read_json(args.annotations).get('cases', {})
    frontend = read_json(args.frontend_evidence) if args.frontend_evidence else {}
    instance = next((row for row in records.get('replay', []) if row.get('id') == 'instance'), {})
    run_start = args.run_started_at or instance.get('created_at')
    result = {'schema_version': 'round6-case-results-v1', 'generated_at': datetime.now(timezone.utc).isoformat(),
        'source_database': str((args.data_dir / 'demo.sqlite3').resolve()),
        'instance_id': instance.get('instance_id'), 'accounting_start': run_start,
        'annotation_source': str(args.annotations.resolve()), 'cases': {}}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for case_id in CASES:
        case, timeline = case_result(case_id, records, catalog, annotations, frontend, run_start)
        result['cases'][case_id] = case
        (args.output_dir / case['timeline_file']).write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding='utf-8')
        (args.output_dir / case['business_report_file']).write_text(markdown_report(case, timeline), encoding='utf-8')
    target = args.output_dir / 'ROUND6_CASE_RESULTS.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(target))


if __name__ == '__main__':
    main()
