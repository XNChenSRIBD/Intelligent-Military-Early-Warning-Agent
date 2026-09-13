/* Read-only regional briefing. Loaded after app.js. */
const briefingState = {alerts: new Map(), signatures: new WeakMap(), request: 0};

function briefingTime(value) {
  if (!value) return '观测时间未注明';
  return isCaseReplay() ? utcDate(value) : date(value);
}

function briefingContext() {
  const replay = isCaseReplay();
  const selected = replay ? selectedReplaySummary() : null;
  const detail = replay && ui.replayCase?.case_id === ui.replayCaseId ? ui.replayCase : null;
  const region = replay ? detail || selected : null;
  const alerts = pipelineAlerts().filter((item) => !replay || item.case_id === ui.replayCaseId);
  const works = (detail?.recent_work || ui.pipeline?.recent_work || []).filter((item) =>
    !replay || item.case_id === ui.replayCaseId);
  const metrics = !replay || detail?.portwatch_monitor_id === ui.metrics?.monitor_id ? ui.metrics : null;
  return {replay, region, detail, alerts, works, metrics,
    key: replay ? ui.replayCaseId : 'online-hormuz',
    name: replay ? (ui.replayCaseId.includes('kharkiv') ? '哈尔科夫' : ui.replayCaseId.includes('hormuz') ? '霍尔木兹海峡' : region?.label || '请选择区域') : '霍尔木兹海峡'};
}

function briefingPrimary(alerts) {
  const active = (item) => !['resolved', 'revoked'].includes(item.status);
  const rank = (item) => item.origin_type === 'numeric_rule' && ['active', 'recovering'].includes(item.status)
    ? 0 : active(item) ? 1 : 2;
  return [...alerts].sort((left, right) => rank(left) - rank(right)
    || String(right.updated_at || right.published_at || '').localeCompare(String(left.updated_at || left.published_at || '')))[0] || null;
}

function briefingAlertDetail(summary, contextKey) {
  if (!summary) return null;
  const key = `${summary.id}:${summary.evidence_version ?? 'unversioned'}`;
  const saved = briefingState.alerts.get(key);
  if (!saved) {
    briefingState.alerts.set(key, {loading: true});
    api(`/api/alerts/${encodeURIComponent(summary.id)}`).then((detail) => {
      briefingState.alerts.set(key, {detail});
      if (briefingState.alerts.size > 60) briefingState.alerts.delete(briefingState.alerts.keys().next().value);
      if (briefingContext().key === contextKey) renderSituationBrief();
    }).catch(() => {
      briefingState.alerts.set(key, {unavailable: true});
      if (briefingContext().key === contextKey) renderSituationBrief();
    });
  }
  // Current summary status remains authoritative while its saved evidence is cached.
  return saved?.detail ? {...saved.detail, ...summary} : summary;
}

function briefingReferences(alert) {
  const references = alert?.evidence_refs?.length ? alert.evidence_refs : alert?.analysis?.evidence_refs || [];
  const seen = new Set();
  return references.map((item) => typeof item === 'string' ? {id: item} : item).filter((item) => {
    const id = item.material_id || item.id;
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function briefingEvidence(alert, context) {
  const cards = [];
  if (alert?.origin_type === 'numeric_rule') {
    const latest = alert.latest_observation;
    if (numeric(latest?.n_total)) cards.push({label: '最新可见通行量', value: `${number(latest.n_total)} 船／航次`,
      note: latest.observed_date || alert.last_evaluated_date, material: latest.material_id});
    if (numeric(alert.baseline)) cards.push({label: '实际历史参考', value: `${number(alert.baseline)} 船／航次`,
      note: `${alert.baseline_start || '—'} 至 ${alert.baseline_end || '—'}${alert.baseline_valid_days != null ? ` · ${alert.baseline_valid_days} 个有效日的中位数` : ''}`, panel: 'portwatch'});
    const trigger = (alert.trigger_observations || []).filter((item) => numeric(item.n_total));
    if (trigger.length) cards.push({label: '触发依据', value: trigger.map((item) => number(item.n_total)).join(' / ') + ' 船／航次',
      note: trigger.map((item) => item.observed_date).filter(Boolean).join('、'), panel: 'portwatch'});
  }
  for (const reference of briefingReferences(alert)) {
    if (cards.length === 3) break;
    cards.push({label: reference.source === 'gnss' ? 'GNSS 观测依据' : reference.source === 'portwatch' ? '航运观测依据' : '引用材料',
      value: reference.title || '已保存的原始材料',
      note: reference.observed_at ? briefingTime(reference.observed_at) : '来源观测时间未注明',
      material: reference.material_id || reference.id});
  }
  if (!alert && !cards.length) {
    const latest = [...(context.detail?.gnss?.summary || [])].sort((a, b) =>
      String(b.window_end || '').localeCompare(String(a.window_end || '')))[0];
    for (const signal of (latest?.signals || []).slice(0, 3)) {
      const value = numeric(signal.cnr_p10) ? `${number(signal.cnr_p10)} dB-Hz`
        : numeric(signal.strength_p10) ? `${number(signal.strength_p10)} ${signal.unit || '原记录单位'}` : null;
      if (value) cards.push({label: `${latest.station} · ${signal.signal}`, value,
        note: `窗口低分位观测值 · ${briefingTime(latest.window_start)}`, panel: 'gnss', seriesKey: `${latest.station} / ${signal.signal}`});
    }
  }
  return cards.slice(0, 3);
}

function briefingConclusion(alert, context) {
  if (alert) {
    const numericAlert = alert.origin_type === 'numeric_rule';
    const persistent = numericAlert && ['active', 'recovering'].includes(alert.status);
    return {title: numericAlert ? ({active:'可见通行量持续偏低',recovering:'航运通行量正在恢复',resolved:'航运通行量预警已解除',revoked:'航运通行量预警已撤销'}[alert.status] || '可见通行量变化提醒') : alert.title || '已发布的观测线索',
      statement: numericAlert ? alert.summary || '已保存程序规则判定，详细依据可展开查看。'
        : (!alert.analysis_stale && alert.analysis?.statement) || alert.summary || '已有观测线索，详见实际引用材料。',
      tone: persistent ? 'warning' : ['resolved', 'revoked'].includes(alert.status) ? 'neutral' : 'caution',
      status: alert.status, statusText: label(alert.status),
      time: alert.last_evaluated_date || alert.observed_at || context.region?.as_of};
  }
  const completed = context.works.find((work) => work.status === 'completed' && work.decision);
  const insufficient = context.works.some((work) => work.status === 'completed' && work.decision === 'insufficient_evidence');
  return {title: context.key.includes('kharkiv') ? '尚未发布 GNSS 预警' : completed ? '当前未发布预警' : '尚未形成预警结论',
    statement: insufficient ? '部分已保存分析的证据仍不足，现有资料尚未形成已发布预警。' : completed?.statement || (context.replay
      ? '结论以该区域已保存的观测和引用材料为依据。尚无结论时，请查看下方已有资料。'
      : '目前没有可展示的已发布结论，可查看已获取的航运数据和公开报道。'),
    tone: insufficient ? 'caution' : 'neutral', status: insufficient ? 'insufficient_evidence' : 'unassessed',
    statusText: insufficient ? '部分分析证据不足' : '暂无新结论',
    time: context.metrics?.latest_observed_date || context.region?.as_of};
}

function briefingReplace(parent, signature, render) {
  if (briefingState.signatures.get(parent) === signature) return;
  const focused = parent.contains(document.activeElement) ? document.activeElement.dataset.briefAction : null;
  briefingState.signatures.set(parent, signature);
  parent.replaceChildren();
  render();
  if (focused) parent.querySelector(`[data-brief-action="${focused}"]`)?.focus({preventScroll: true});
}

function briefingButton(title, action, callback, className = 'reference-button') {
  const button = el('button', className, title);
  button.type = 'button'; button.dataset.briefAction = action;
  button.addEventListener('click', () => Promise.resolve().then(callback).catch((error) => notify(error.message)));
  return button;
}

function briefingSourceOptions(context) {
  const sources = ui.pipeline?.sources || [];
  const gnssCount = context.detail?.gnss?.summary?.length;
  const gnssConnected = context.replay ? Boolean(context.region?.gnss_resources || gnssCount)
    : sources.some((source) => source.source === 'gnss');
  const options = [{kind: 'gnss', title: 'GNSS 观测', enabled: gnssConnected,
    note: !gnssConnected ? '未接入' : numeric(gnssCount) ? `${number(gnssCount)} 份已处理观测` : '查看已保存观测'}];
  const dailyRows = context.metrics?.observations || [];
  const shipping = context.alerts.some((alert) => alert.origin_type === 'numeric_rule') || dailyRows.length > 0
    || (!context.replay && sources.some((source) => source.source === 'portwatch'));
  if (shipping) options.push({kind: 'portwatch', title: '航运数据', enabled: true,
    note: dailyRows.length ? `${dailyRows.length} 日实际记录` : '查看已保存的航运依据'});
  const newsWorks = context.works.filter((work) => work.kind === 'news');
  const newsSources = context.replay ? [] : sources.filter((source) => ['gdelt', 'rss'].includes(source.source));
  const newsAvailable = newsWorks.length > 0 || newsSources.length > 0
    || context.alerts.some((alert) => alert.origin_type === 'news_clue');
  options.push({kind: 'news', title: '公开报道', enabled: newsAvailable,
    note: newsWorks.length ? `最近 ${newsWorks.length} 条分析记录` : newsSources.length ? `${newsSources.length} 个已配置来源`
      : newsAvailable ? '查看已保存报道' : '暂无可展示记录'});
  return options;
}

async function openBriefingMaterial(reference) {
  const id = typeof reference === 'string' ? reference : reference.material_id || reference.id;
  const request = ++briefingState.request;
  ui.detail = 'evidence-material';
  openDetailPanel('原始证据');
  $('detail-content').replaceChildren(el('p', 'muted', '正在读取已保存材料…'));
  try {
    const material = await api(`/api/materials/${encodeURIComponent(id)}`);
    if (request !== briefingState.request || ui.detail !== 'evidence-material') return;
    ui.evidenceMaterial = material;
    renderMaterial(material);
  } catch (error) {
    if (request === briefingState.request && ui.detail === 'evidence-material')
      $('detail-content').replaceChildren(el('p', 'source-error', error.message));
  }
}

async function openBriefingWork(workOrId) {
  const id = typeof workOrId === 'string' ? workOrId : workOrId.id;
  const request = ++briefingState.request;
  ui.detail = 'briefing-work';
  openDetailPanel('已保存分析');
  $('detail-content').replaceChildren(el('p', 'muted', '正在读取已保存分析…'));
  try {
    const work = await api(`/api/work/${encodeURIComponent(id)}`);
    if (request !== briefingState.request || ui.detail !== 'briefing-work') return;
    const parent = $('detail-content'), assessment = work.assessment;
    parent.replaceChildren(el('h2', 'detail-heading', assessment?.title || '已保存的分析结论'));
    parent.append(el('p', 'detail-meta', work.as_of ? `观测截止 ${briefingTime(work.as_of)}` : `保存于 ${date(work.completed_at)}`),
      el('p', '', assessment?.statement || '该记录尚未保存分析结论。'));
    const limitations = Array.isArray(assessment?.limitations) ? assessment.limitations : [];
    limitations.forEach((item) => parent.append(el('p', 'small muted', item)));
    const inputs = new Map((work.input?.materials || []).map((item) => [String(item.id), item]));
    const references = assessment?.evidence_refs || [];
    if (references.length) {
      const block = el('section', 'detail-section'); block.append(el('h3', '', '结论引用'));
      references.forEach((reference, index) => {
        const materialId = typeof reference === 'string' ? reference : reference.material_id || reference.id;
        const material = inputs.get(String(materialId));
        block.append(briefingButton(material?.title || `查看引用材料 ${index + 1}`, `work-material-${index}`,
          () => openBriefingMaterial(materialId)));
      });
      parent.append(block);
    }
    const record = el('details', 'asset-block');
    record.append(el('summary', '', '查看实际处理记录'));
    record.addEventListener('toggle', () => {
      if (!record.open || record.dataset.loaded) return;
      record.dataset.loaded = 'true';
      appendWorkExecution(work, record);
    });
    parent.append(record);
  } catch (error) {
    if (request === briefingState.request && ui.detail === 'briefing-work')
      $('detail-content').replaceChildren(el('p', 'source-error', error.message));
  }
}

function openBriefingReports(context) {
  ui.detail = 'briefing-reports';
  openDetailPanel('区域分析报告');
  const parent = $('detail-content');
  parent.replaceChildren(el('h2','detail-heading','已保存分析报告'),el('p','detail-meta',context.name));
  const list = el('div','material-list');
  context.works.filter((work) => work.status === 'completed').forEach((work) => {
    const button = briefingButton('',`report-${work.id}`,() => openBriefingWork(work),'material-card');
    button.append(el('span','publisher',{gnss:'GNSS 观测',portwatch:'航运数据',news:'公开报道'}[work.kind] || '区域资料'),el('h3','',label(work.decision)),el('p','card-excerpt',work.statement || '查看已保存分析'),el('span','small muted',briefingTime(work.as_of || work.completed_at)));
    list.append(button);
  });
  parent.append(list);
}

function renderSituationBrief() {
  const hero = $('situation-brief'), browser = $('source-browser');
  if (!hero || !browser || !ui.pipeline) return;
  const context = briefingContext();
  const selected = briefingPrimary(context.alerts);
  const alert = briefingAlertDetail(selected, context.key);
  const conclusion = briefingConclusion(alert, context);
  const evidence = briefingEvidence(alert, context);
  const reportWork = context.works.find((work) => work.status === 'completed' && work.statement);
  const options = briefingSourceOptions(context);
  const signature = JSON.stringify([context.key, context.name, selected?.id, conclusion, evidence, reportWork?.id]);
  briefingReplace(hero, signature, () => {
    hero.dataset.tone = conclusion.tone; hero.dataset.state = conclusion.status || '';
    const heading = el('div', 'brief-top');
    heading.append(el('p', 'eyebrow', `${context.name} · 区域监测概览`),
      el('p', 'brief-observed', conclusion.time ? `观测截至 ${briefingTime(conclusion.time)}` : '观测截至时间待记录'));
    const status = el('div','brief-status'); status.append(badge(conclusion.statusText, conclusion.tone === 'warning' ? 'waiting' : ''));
    const statement = conclusion.statement || '';
    hero.append(heading, status,
      el('h2', 'brief-title', conclusion.title), el('p', 'brief-statement', statement.length > 150 ? `${statement.slice(0,150)}…` : statement));
    if (evidence.length) {
      const cards = el('div', 'evidence-grid');
      evidence.forEach((item, index) => {
        const card = el('article', 'evidence-card');
        card.append(el('p', 'evidence-label', item.label), el('strong', 'evidence-value', item.value));
        if (item.note) card.append(el('p', 'evidence-note', item.note));
        if (item.material || item.panel) card.append(briefingButton(item.material ? '查看原始材料' : '查看观测依据',
          `evidence-${index}`, () => { if (item.seriesKey) ui.gnssSeriesKey = item.seriesKey; return item.material ? openBriefingMaterial(item.material) : openSourcePanel(item.panel); }));
        cards.append(card);
      });
      hero.append(cards);
    }
    const actions = el('div', 'brief-actions');
    actions.append(briefingButton(alert ? '查看结论与完整依据' : reportWork ? '已保存分析报告' : '查看已有观测', 'report', () => {
      if (alert) return openPipelineAlert(alert.id);
      if (reportWork) return openBriefingReports(context);
      const available = options.find((option) => option.enabled);
      if (available) return openSourcePanel(available.kind);
      openDetailPanel('区域记录'); ui.detail = 'briefing-summary';
      $('detail-content').replaceChildren(el('h2', 'detail-heading', context.name),
        el('p', '', '目前没有可展示的已保存分析结论。'));
    }, 'primary'));
    if (alert && reportWork) actions.append(briefingButton('分析报告','reports',() => openBriefingReports(context)));
    if (context.alerts.length > 1) actions.append(briefingButton(`预警记录 · ${context.alerts.length}`,'alerts',() => {
      $('alert-browser').hidden = false;
      $('pipeline-alert-filter').focus({preventScroll:true});
      $('alert-browser').scrollIntoView({behavior:'smooth',block:'start'});
    }));
    hero.append(actions);
  });
  briefingReplace(browser, JSON.stringify([context.key, options]), () => {
    browser.append(el('p', 'eyebrow', '查看资料'));
    const grid = el('div', 'source-grid');
    options.forEach((option,index) => {
      const button = briefingButton('', `source-${option.kind}`, () => openSourcePanel(option.kind), 'source-tile');
      button.disabled = !option.enabled;
      button.append(el('span','source-icon',String(index+1).padStart(2,'0')),el('strong', '', option.title), el('span', 'source-count', option.note));
      grid.append(button);
    });
    browser.append(grid);
  });
}

window.renderSituationBrief = renderSituationBrief;
window.openBriefingWork = openBriefingWork;
window.openBriefingMaterial = openBriefingMaterial;
