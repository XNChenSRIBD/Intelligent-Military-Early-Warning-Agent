const $ = (id) => document.getElementById(id);
const ui = {
  mode: localStorage.getItem('workspace-mode') || 'online',
  monitorId: localStorage.getItem('workspace-monitor') || '',
  runId: localStorage.getItem('workspace-run') || '',
  materialId: null,
  detail: null,
  state: null,
  materials: [],
  runs: [],
  history: null,
  refreshing: false,
  saving: false,
  formLoaded: false,
  materialSignature: '',
  metrics: null,
  alerts: [],
  alertId: null,
  evidenceMaterial: null,
  portwatchSignature: '',
};
const labels = {
  unknown: '状态未知', ready: '可用', available: '可用', online: '在线', ok: '成功', success: '成功', succeeded: '已完成', completed: '已完成',
  failed: '失败', error: '失败', unavailable: '不可用', disabled: '未启用', idle: '等待运行', pending: '待分析', pending_analysis: '待分析',
  running: '运行中', queued: '等待处理', collecting: '采集资料', fetching: '获取材料', analyzing: '模型分析', summarizing: '生成本轮摘要',
  cancelled: '已取消', canceled: '已取消', cancelling: '正在取消', interrupted: '运行中断', empty: '无新增材料', no_new: '无新增材料',
  title_only: '仅标题', summary: '来源摘要', full_text: '正文', article: '正文', aggregate: '日度聚合', replay: '原始材料回放', history: '结果快照', snapshot: '结果快照',
  stored: '材料已保存', source_failed: '来源获取失败', model_failed: '模型分析失败', done: '已分析', snippet: '来源摘要', article_text: '正文', invalid_output: '输出格式错误',
  normalize: '整理材料', fetch: '获取材料', collect: '采集资料', llm: 'Qwen 分析', analyze: 'Qwen 分析', persist: '保存材料', save: '保存结果',
  not_required: '无需自动分析', evaluate: '规则判定', evaluating: '规则判定', explaining: '生成提醒解释', explanation: '提醒解释',
  insufficient_reference: '参考不足', not_triggered: '未命中当前规则', candidate: '单日偏低', active: '持续提醒', recovering: '恢复中', resolved: '已解除', revoked: '因数据修订撤销', missing_data: '观测缺失',
  initialization: '初始化发现', update: '后续更新', revision: '来源修订', stale: '旧证据版本',
};
const label = (value) => labels[value] || value || '—';
const isRunning = (run) => run && ['running', 'queued', 'cancelling'].includes(run.status);
const format = (value) => value == null || value === '' ? '未声明' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
const date = (value) => {
  if (!value) return '未声明';
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return String(value);
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('zh-CN', {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,timeZoneName:'short'});
};
const duration = (ms) => ms == null ? '—' : Number(ms) < 1000 ? `${Math.round(ms)} ms` : `${(Number(ms) / 1000).toFixed(1)} 秒`;
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}
function badge(text, type = '') { return el('span', `badge ${type}`, text); }
function notify(text = '') { $('notice').textContent = text; $('notice').hidden = !text; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : format(result.detail || result.error || response.statusText));
  return result;
}
function remember() {
  localStorage.setItem('workspace-mode', ui.mode);
  localStorage.setItem('workspace-monitor', ui.monitorId);
  localStorage.setItem('workspace-run', ui.runId);
}
function currentMonitor() { return ui.state?.monitors.find((item) => String(item.id) === String(ui.monitorId)); }
function currentRun() { return ui.runs.find((item) => String(item.id) === String(ui.runId)) || ui.runs[0] || null; }
function activeRun() { return ui.state?.runs.find((item) => String(item.id) === String(ui.state.active_run_id)); }
function isPortWatch() { return (currentMonitor()?.source || $('source').value) === 'portwatch'; }
function renderSourceSettings() {
  const portwatch = $('source').value === 'portwatch';
  $('topic-label').textContent = portwatch ? '监测名称' : '主题 / 关键词';
  $('news-settings').hidden = portwatch;
  $('portwatch-settings').hidden = !portwatch;
  $('lookback').disabled = portwatch;
  $('limit').disabled = portwatch;
  $('run-button').textContent = portwatch ? '▶ 立即更新' : '▶ 立即运行';
  const config = ui.state?.portwatch;
  if (portwatch && config) {
    const rule = config.rule;
    $('portwatch-settings').replaceChildren(el('strong', '', `${config.name} · ${config.portid}`), el('p', '', '名称仅用于标记任务，不作为地理查询词。主指标为 n_total。'), el('p', '', `指标历史 ${rule.history_days} 天 · 参考窗口 ${rule.baseline_days} 天，至少 ${rule.min_baseline_days} 个有效日。`), el('p', '', `连续 ${rule.trigger_days} 天低于参考值的 ${rule.trigger_ratio * 100}% 触发；连续 ${rule.recovery_days} 天达到 ${rule.recovery_ratio * 100}% 解除。候选起冻结参考值。`), el('p', 'muted', '日度是观测粒度；来源不一定每日发布新记录。'));
  }
}
function setMode(mode) {
  ui.mode = mode;
  remember();
  const history = mode === 'history';
  $('online-controls').hidden = history;
  $('online-content').hidden = history;
  $('history-controls').hidden = !history;
  $('history-content').hidden = !history;
  $('online-tab').classList.toggle('active', !history);
  $('history-tab').classList.toggle('active', history);
  $('online-tab').setAttribute('aria-current', history ? 'false' : 'page');
  $('history-tab').setAttribute('aria-current', history ? 'page' : 'false');
  $('content-kicker').textContent = history ? '历史案例 / HORMUZ' : '资料流';
  $('content-title').textContent = history ? '霍尔木兹海峡 · 民用航运宏观资料' : currentMonitor()?.topic || '从主题开始，持续整理公开资料';
  $('mode-badge').textContent = history ? '历史 · 结果快照' : currentRun()?.mode === 'replay' ? '原始材料回放' : '在线资料';
  ui.detail = null;
  if (history) loadHistory(); else renderOnline();
}
function fillForm(monitor) {
  $('topic').value = monitor?.topic || 'shipping';
  if (monitor) $('source').value = monitor.source;
  const lookback = String(monitor?.lookback_hours || 72);
  if (![...$('lookback').options].some((option) => option.value === lookback)) $('lookback').add(new Option(`${lookback} 小时`, lookback));
  $('lookback').value = lookback;
  $('limit').value = monitor?.max_materials || 5;
  $('interval').value = monitor?.interval_minutes || ($('source').value === 'portwatch' ? ui.state?.portwatch?.default_interval_minutes || 1440 : 30);
  ui.formLoaded = true;
  renderSourceSettings();
}
function renderSourceStatus() {
  const portwatch = currentMonitor()?.source === 'portwatch';
  const latest = ui.state?.runs.find((run) => String(run.monitor_id) === String(ui.monitorId) && (!portwatch || run.mode !== 'explanation'));
  const metrics = String(ui.metrics?.monitor_id) === String(ui.monitorId) ? ui.metrics : null;
  const sourceStatus = portwatch ? metrics?.source_status || latest?.source_status : latest?.source_status;
  $('source-status').textContent = `来源 · ${sourceStatus ? typeof sourceStatus === 'string' ? label(sourceStatus) : Object.entries(sourceStatus).map(([key, value]) => `${key} ${typeof value === 'object' ? label(value.status) : label(value)}`).join(' / ') : '尚未运行'}`;
  $('source-status').title = $('source-status').textContent;
}
function renderState() {
  const state = ui.state;
  const model = state.model || {name:'Qwen',status:'unknown'};
  $('model-status').textContent = `${model.name || 'Qwen'} · ${label(model.status)}`;
  $('model-status').title = model.last_error || (model.last_call_at ? `最近调用：${date(model.last_call_at)}` : '尚无模型调用记录');
  $('model-status').className = `status-item ${['ready','available','ok','online'].includes(model.status) ? 'good' : ['failed','error','unavailable'].includes(model.status) ? 'bad' : ''}`;
  const selected = currentMonitor();
  const latest = state.runs.find((run) => String(run.monitor_id) === String(ui.monitorId));
  renderSourceStatus();
  $('last-success').textContent = selected?.last_success_at ? date(selected.last_success_at) : '尚无记录';
  const monitorSelect = $('monitor-select');
  monitorSelect.replaceChildren(new Option('新建主题', ''));
  state.monitors.forEach((monitor) => monitorSelect.add(new Option(monitor.topic, monitor.id)));
  monitorSelect.value = ui.monitorId;
  const sourceSelect = $('source');
  const previousSource = sourceSelect.value;
  sourceSelect.replaceChildren();
  state.sources.forEach((source) => sourceSelect.add(new Option(source.name, source.id)));
  if (previousSource && state.sources.some((source) => source.id === previousSource)) sourceSelect.value = previousSource;
  if (!ui.formLoaded) fillForm(selected);
  renderSourceSettings();
  const replayMonitor = selected?.mode === 'replay';
  $('schedule-button').textContent = selected?.enabled ? '停止周期更新' : '开始周期更新';
  $('save-button').disabled = replayMonitor;
  $('schedule-button').disabled = replayMonitor;
  $('schedule-info').textContent = replayMonitor ? '当前为历史材料回放，请在历史案例页回放下一批。' : selected?.enabled ? `周期更新已开启 · 每 ${selected.interval_minutes} 分钟运行` : '手动运行 · 周期更新未开启';
  const active = activeRun();
  $('cancel-button').disabled = !isRunning(active) || active?.status === 'cancelling';
  $('running-dot').classList.toggle('active', Boolean(isRunning(active)));
  $('run-button').disabled = replayMonitor || Boolean(state.active_run_id) || ui.saving;
  $('current-stage').textContent = active ? label(active.stage || active.status) : latest ? label(latest.status) : '等待运行';
  $('current-runtime').textContent = active ? `开始于 ${date(active.started_at)} · 已用 ${duration(Date.now() - new Date(active.started_at).getTime())}` : latest ? `最近运行 ${date(latest.started_at)}${latest.error ? ` · ${latest.error}` : ''}` : '运行后显示真实处理进度';
  if (ui.mode === 'online') {
    $('content-title').textContent = isPortWatch() ? `${ui.state.portwatch?.name || '霍尔木兹海峡'} · ${selected?.topic || '可见通行量监测'}` : selected?.topic || '从主题开始，持续整理公开资料';
    $('content-kicker').textContent = isPortWatch() ? '民用航运 / PORTWATCH' : '资料流';
    $('mode-badge').textContent = isPortWatch() ? '在线 · 数值监测' : currentRun()?.mode === 'replay' ? '原始材料回放' : '在线资料';
  }
  const replay = state.replay || ui.history?.replay;
  if (replay) {
    $('replay-button').disabled = !replay.available || Boolean(isRunning(active));
    $('replay-description').textContent = replay.available ? `${replay.label} · ${replay.count} 条可用材料，按顺序分批处理。` : replay.label || '当前案例仅提供既有结果快照，暂无可回放的原始材料。';
  }
}
async function refresh() {
  if (ui.refreshing) return;
  ui.refreshing = true;
  try {
    ui.state = await api('/api/state');
    if (ui.monitorId && !currentMonitor()) { ui.monitorId = ''; ui.runId = ''; }
    if (!ui.monitorId && !ui.formLoaded && ui.state.monitors.length) ui.monitorId = String(ui.state.monitors[0].id);
    renderState();
    if (ui.monitorId) await loadMaterials();
    else { ui.materials = []; ui.runs = []; ui.metrics = null; ui.alerts = []; if (ui.mode === 'online') renderOnline(); }
    remember();
  } catch (error) { notify(error.message); }
  finally { ui.refreshing = false; }
}
async function loadMaterials() {
  const monitorId = ui.monitorId;
  const runId = ui.runId;
  const path = `/api/monitors/${encodeURIComponent(monitorId)}`;
  const portwatch = currentMonitor()?.source === 'portwatch';
  const [result, metrics, alerts] = await Promise.all([
    api(`${path}/materials${runId ? `?run_id=${encodeURIComponent(runId)}` : ''}`),
    portwatch ? api(`${path}/metrics`) : null,
    portwatch ? api(`${path}/alerts`) : null,
  ]);
  if (monitorId !== ui.monitorId || runId !== ui.runId) return;
  ui.materials = result.materials;
  ui.runs = result.runs;
  ui.metrics = metrics;
  ui.alerts = alerts?.alerts || [];
  renderSourceStatus();
  if (ui.mode === 'online') renderOnline();
}
async function saveMonitor() {
  if (!$('monitor-form').reportValidity()) return null;
  const monitor = await api('/api/monitors', {
    ...(ui.monitorId ? {id:ui.monitorId} : {}),
    topic:$('topic').value.trim(), source:$('source').value,
    lookback_hours:Number($('lookback').value), max_materials:Number($('limit').value), interval_minutes:Number($('interval').value),
  });
  ui.monitorId = String(monitor.id);
  remember();
  return monitor;
}
async function action(callback) {
  if (ui.saving) return;
  ui.saving = true;
  notify();
  $('run-button').disabled = true;
  try { await callback(); await refresh(); }
  catch (error) { notify(error.message); }
  finally { ui.saving = false; if (ui.state) renderState(); }
}
function renderOnline() {
  const run = currentRun();
  const portwatch = isPortWatch();
  $('portwatch-content').hidden = !portwatch;
  $('news-overview').hidden = portwatch;
  $('material-list').hidden = portwatch;
  $('pw-legacy-fold').hidden = !portwatch;
  $('material-count').hidden = portwatch;
  $('online-list-title').textContent = portwatch ? '运行记录' : '资料列表';
  $('content-kicker').textContent = portwatch ? '民用航运 / PORTWATCH' : '资料流';
  $('content-title').textContent = portwatch ? `${ui.state?.portwatch?.name || '霍尔木兹海峡'} · ${currentMonitor()?.topic || '可见通行量监测'}` : currentMonitor()?.topic || '从主题开始，持续整理公开资料';
  if (portwatch) renderPortWatch();
  $('new-count').textContent = run?.new_count ?? '—';
  $('analyzed-count').textContent = run?.analyzed_count ?? '—';
  $('failed-count').textContent = run?.failed_count ?? '—';
  $('material-count').textContent = ui.materials.length;
  const runSelect = $('run-select');
  runSelect.replaceChildren(new Option('全部运行', ''));
  ui.runs.forEach((item) => runSelect.add(new Option(`${date(item.started_at)} · ${label(item.status)}${item.mode === 'replay' ? ' · 回放' : ''}`, item.id)));
  runSelect.value = ui.runId;
  $('run-summary-button').hidden = !run;
  if (run) {
    const summary = run.summary ? format(run.summary) : run.error || `${label(run.stage || run.status)} · 新增 ${run.new_count || 0} 条，已分析 ${run.analyzed_count || 0} 条`;
    $('run-summary-button').textContent = `${run.mode === 'replay' ? '原始材料回放' : '本轮处理'} · ${label(run.status)}\n${summary.slice(0, 170)}${summary.length > 170 ? '…' : ''}\n查看摘要与处理步骤 →`;
  }
  const signature = JSON.stringify([ui.materials, ui.materialId, portwatch]);
  if (signature !== ui.materialSignature) {
    ui.materialSignature = signature;
    const list = $(portwatch ? 'pw-legacy-materials' : 'material-list');
    const materials = portwatch ? ui.materials.filter((material) => material.analysis_status !== 'not_required') : ui.materials;
    list.replaceChildren();
    if (!materials.length) {
      const empty = el('div', 'empty-state');
      empty.append(el('span', 'empty-symbol', '⌁'), el('h3', '', portwatch ? '当前没有旧版资料卡' : run ? (run.error ? '本轮处理未完成' : '当前没有资料') : '资料将在这里汇集'), el('p', '', portwatch ? '日度材料可从数值明细与提醒证据打开。' : run?.error || (run ? '本轮没有新增资料，或材料仍在获取中。' : '设置主题并运行，查看来源、摘要与处理进度。')));
      list.append(empty);
    }
    materials.forEach((material) => {
      const card = el('button', `material-card${String(material.id) === String(ui.materialId) ? ' active' : ''}`);
      card.type = 'button';
      const top = el('div', 'card-top');
      top.append(el('span', 'publisher', material.publisher || material.source), badge(label(material.analysis_status), ['failed','error'].includes(material.analysis_status) ? 'error' : material.analysis ? 'accent' : 'waiting'));
      const meta = el('div', 'card-meta');
      meta.append(el('span', '', date(material.observed_at || material.available_at || material.fetched_at)), el('span', '', label(material.content_kind)), el('span', '', material.mode === 'replay' ? '原始材料回放' : '在线资料'), el('span', '', `版本 ${material.version || 1}`));
      card.append(top, el('h3', '', material.title), el('p', 'card-excerpt', material.analysis?.summary ? format(material.analysis.summary) : material.text || '来源只提供标题。'), meta);
      card.addEventListener('click', () => { ui.materialId = material.id; ui.detail = 'material'; renderOnline(); });
      list.append(card);
    });
    if (!portwatch) renderArticleChart();
  }
  const detailScroll = document.querySelector('.details-panel').scrollTop;
  if (ui.detail === 'alert' && portwatch) {
    const alert = ui.alerts.find((item) => String(item.id) === String(ui.alertId));
    if (alert) renderAlert(alert); else emptyDetail();
  }
  else if (ui.detail === 'evidence-material' && ui.evidenceMaterial) renderMaterial(ui.evidenceMaterial);
  else if (ui.detail === 'run') renderRunDetail(run);
  else if (ui.detail === 'material' && ui.materials.some((item) => String(item.id) === String(ui.materialId))) renderMaterial(ui.materials.find((item) => String(item.id) === String(ui.materialId)));
  else if (run && !ui.detail) { ui.detail = 'run'; renderRunDetail(run); }
  else if (!ui.detail) emptyDetail();
  document.querySelector('.details-panel').scrollTop = detailScroll;
  $('mode-badge').textContent = portwatch ? '在线 · 数值监测' : run?.mode === 'replay' ? '原始材料回放' : '在线资料';
}
function emptyDetail() {
  const empty = el('div', 'empty-state detail-empty');
  empty.append(el('span', 'empty-symbol', '≡'), el('h3', '', '查看资料细节'), el('p', '', '选择一条资料，查看模型摘要、原始摘录与引用来源。'));
  $('detail-content').replaceChildren(empty);
}
function section(title, parent = $('detail-content')) {
  const node = el('section', 'detail-section');
  node.append(el('h3', '', title));
  parent.append(node);
  return node;
}
function facts(values, parent) {
  const list = el('dl', 'detail-facts');
  values.forEach(([key, value]) => list.append(el('dt', '', key), el('dd', '', format(value))));
  parent.append(list);
}
function link(url, title, parent) {
  if (!url) return;
  const parsed = new URL(url);
  if (!['http:','https:'].includes(parsed.protocol)) return;
  const anchor = el('a', '', title || url);
  anchor.href = parsed.href;
  anchor.target = '_blank';
  anchor.rel = 'noopener noreferrer';
  parent.append(anchor);
}
function renderMaterial(material) {
  const parent = $('detail-content');
  parent.replaceChildren(badge(material.mode === 'replay' ? '原始材料回放' : '在线资料', 'accent'), el('h2', 'detail-heading', material.title), el('p', 'detail-meta', `${material.publisher || material.source} · ${label(material.content_kind)} · 版本 ${material.version || 1}`));
  const analysis = section(material.analysis_status === 'not_required' ? '数值材料' : 'Qwen 资料摘要');
  analysis.append(el('p', '', material.analysis_status === 'not_required' ? '该记录用于结构化数值监测；提醒详情可按需生成解释。' : material.analysis?.summary ? format(material.analysis.summary) : `当前状态：${label(material.analysis_status)}`));
  if (material.error) analysis.append(el('p', '', material.error));
  if (material.analysis) {
    facts([['模型', material.analysis.model], ['调用耗时', duration(material.analysis.elapsed_ms)]], analysis);
    renderReferences(material.analysis.material_ids || [material.id], analysis);
  }
  const source = section('引用来源');
  link(material.url, material.title || material.url, source);
  source.append(el('p', 'micro muted', `材料 ID：${material.id}`));
  const excerpt = section('实际获取的原文 / 摘录');
  excerpt.append(el('p', 'excerpt', material.text || '仅取得标题，未取得正文。'));
  if (material.extraction_error) excerpt.append(el('p', '', material.extraction_error));
  const times = section('时间信息');
  facts([['观测时间', material.observed_at], ['来源发布时间', material.available_at], ['本次获取时间', material.fetched_at]], times);
  if (material.time_note) times.append(el('p', '', material.time_note));
  renderSteps(currentRun(), section('本轮处理步骤'));
}
function renderReferences(ids, parent) {
  const container = el('div');
  ids.forEach((id) => {
    const button = el('button', 'reference-button', `材料 ${id}`);
    button.type = 'button';
    const material = ui.materials.find((item) => String(item.id) === String(id));
    button.disabled = !material;
    button.addEventListener('click', () => { ui.materialId = material.id; ui.detail = 'material'; renderOnline(); });
    container.append(button);
  });
  parent.append(container);
}
function renderRunDetail(run) {
  if (!run) { emptyDetail(); return; }
  const parent = $('detail-content');
  parent.replaceChildren(badge(run.mode === 'replay' ? '原始材料回放' : '在线资料', 'accent'), el('h2', 'detail-heading', '本轮摘要与处理记录'), el('p', 'detail-meta', `${date(run.started_at)} · ${label(run.status)}`));
  const portwatch = currentMonitor()?.source === 'portwatch';
  const summary = section(portwatch ? run.mode === 'explanation' ? '提醒解释运行' : '数值处理结果' : '本次模型生成的摘要');
  if (run.summary) {
    summary.append(el('p', '', typeof run.summary === 'object' ? format(run.summary.summary || run.summary) : run.summary));
    if (typeof run.summary === 'object' && run.summary.material_ids) renderReferences(run.summary.material_ids, summary);
  } else summary.append(el('p', 'muted', run.error || (portwatch ? isRunning(run) ? '本轮处理正在进行。' : '本轮记录已保存，数值判断不依赖模型摘要。' : isRunning(run) ? '运行尚在进行，摘要将在模型调用完成后显示。' : '本轮未生成模型摘要。')));
  const runFacts = [['运行 ID', run.id], ['运行状态', label(run.status)], ['当前阶段', label(run.stage)], ['开始时间', date(run.started_at)], ['结束时间', date(run.finished_at)], ['失败次数', run.failed_count ?? 0]];
  if (portwatch && run.mode !== 'explanation') runFacts.push(['新增观测日',run.new_count ?? 0],['修订日',run.revised_count ?? 0],['未变化日',run.unchanged_count ?? 0]);
  else runFacts.push(['新增 / 已分析', `${run.new_count ?? 0} / ${run.analyzed_count ?? 0}`]);
  facts(runFacts, section('运行信息'));
  if (run.error) section('本轮错误').append(el('p', '', run.error));
  if (run.source_status) section('来源状态').append(el('p', '', typeof run.source_status === 'string' ? label(run.source_status) : format(run.source_status)));
  renderSteps(run, section('真实处理步骤与耗时'));
}
function renderSteps(run, parent) {
  if (!run?.steps?.length) { parent.append(el('p', 'muted', '尚无处理步骤记录。')); return; }
  const list = el('ul', 'step-list');
  run.steps.forEach((step) => {
    const item = el('li');
    const top = el('div', 'step-top');
    top.append(el('span', '', `${label(step.tool)} · ${label(step.status)}`), el('span', 'step-time', duration(step.elapsed_ms)));
    item.append(top);
    if (step.detail && typeof step.detail !== 'object') item.append(el('p', 'step-detail', format(step.detail)));
    if (step.detail && typeof step.detail === 'object') {
      const detail = step.detail;
      const attempts = detail.attempts || [];
      const model = detail.model || attempts[0]?.request?.model;
      if (model) item.append(el('p', 'step-detail', `模型：${model}`));
      const excerpts = detail.input_excerpt || [];
      if (excerpts.length) {
        item.append(el('p', 'step-detail', `输入材料 · ${excerpts.length} 条`));
        excerpts.forEach((input) => {
          const fold = el('details', 'asset-block');
          fold.append(el('summary', '', input.title || `材料 ${input.id}`), el('p', 'excerpt', input.text));
          item.append(fold);
        });
      } else if (detail.material_ids) {
        detail.material_ids.forEach((id) => {
          const material = ui.materials.find((entry) => String(entry.id) === String(id));
          item.append(el('p', 'step-detail', material?.title || `材料 ${id}`));
        });
      }
      if (attempts.length) {
        const repairs = attempts.filter((attempt) => attempt.kind === 'format_repair').length;
        item.append(el('p', 'step-detail', `调用 ${attempts.length} 次 · 格式修复 ${repairs} 次`));
        attempts.forEach((attempt, index) => {
          item.append(el('p', 'step-detail', `第 ${attempt.attempt || index + 1} 次 · ${attempt.kind === 'format_repair' ? '格式修复' : '资料分析'} · ${label(attempt.status)} · ${duration(attempt.elapsed_ms)}`));
          if (attempt.response_text) {
            const fold = el('details', 'asset-block');
            fold.append(el('summary', '', `查看第 ${attempt.attempt || index + 1} 次模型输出`), el('p', 'excerpt', attempt.response_text));
            item.append(fold);
          }
        });
      }
    }
    list.append(item);
  });
  parent.append(list);
}
function svgNode(tag, attributes, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  Object.entries(attributes).forEach(([key,value]) => node.setAttribute(key,value));
  if (text != null) node.textContent = text;
  return node;
}
const numeric = (value) => typeof value === 'number' && Number.isFinite(value);
const number = (value) => numeric(value) ? value.toLocaleString('zh-CN', {maximumFractionDigits:2}) : '缺失';
function renderPortWatch() {
  const metrics = ui.metrics;
  const state = metrics?.state;
  const rule = metrics?.rule || ui.state?.portwatch?.rule;
  const frozen = ['candidate','active','recovering','resolved'].includes(state?.current_status) || Boolean(state?.active_alert_id);
  $('portwatch-current-note').hidden = !ui.runId;
  $('pw-latest').textContent = number(state?.latest_value);
  $('pw-observed-date').textContent = metrics?.latest_observed_date || '尚无观测';
  $('pw-baseline-label').textContent = frozen ? '冻结参考中位数' : '近期参考中位数';
  $('pw-baseline').textContent = number(state?.baseline);
  $('pw-ratio').textContent = numeric(state?.ratio) ? `当前 / 参考 ${number(state.ratio * 100)}%` : '当前 / 参考 —';
  $('pw-status').textContent = state ? label(state.current_status) : '尚无观测';
  $('pw-status').className = `metric-status ${['active','recovering'].includes(state?.current_status) ? 'lowflow-text' : ''}`;
  $('pw-data-date').textContent = `数据截至 ${metrics?.latest_observed_date || '—'}`;
  $('pw-freshness').textContent = `数据截至 ${metrics?.latest_observed_date || '—'} · 最近获取时间：${date(metrics?.last_success_at)}${metrics?.days_since_observation > 0 ? ` · 尚未收到后续观测（距该观测 ${metrics.days_since_observation} 天）` : ''}${state?.data_gaps?.length ? ` · 观测缺口：${state.data_gaps.join('、')}` : ''}`;
  $('pw-source-info').textContent = metrics?.error ? `本次来源更新未完成：${metrics.error}。当前显示已保存结果。` : metrics?.last_checked_at ? `最近检查：${date(metrics.last_checked_at)} · 来源状态：${label(metrics.source_status)}` : '点击立即更新获取数值序列。';
  $('pw-rule-summary').textContent = `${state?.summary || ''}${rule ? ` 规则 ${rule.id}：候选日前 ${rule.baseline_days} 个自然日中位数；连续 ${rule.trigger_days} 日 < ${rule.trigger_ratio * 100}% 触发，连续 ${rule.recovery_days} 日 ≥ ${rule.recovery_ratio * 100}% 解除。工程默认参数，未校准，非 IMF 官方阈值。` : ''}`;
  const signature = JSON.stringify([metrics, ui.alerts, $('pw-alert-filter').value, ui.alertId]);
  if (signature === ui.portwatchSignature) return;
  ui.portwatchSignature = signature;
  renderPortWatchChart(metrics);
  const filter = $('pw-alert-filter').value;
  const alerts = ui.alerts.filter((item) => filter === 'all' || (filter === 'active' ? ['active','recovering'].includes(item.status) : ['resolved','revoked'].includes(item.status)));
  $('pw-alert-count').textContent = ui.alerts.length;
  const list = $('pw-alert-list');
  list.replaceChildren();
  if (!alerts.length) {
    const empty = el('div', 'empty-state compact-empty');
    empty.append(el('h3', '', '暂无符合筛选的持续提醒'), el('p', '', state?.current_status === 'candidate' ? '单日偏低，尚未形成持续提醒。' : state ? '仅列出已经满足持续条件并保存的提醒。' : '更新后根据实际观测显示规则结果。'));
    list.append(empty);
  }
  alerts.forEach((alert) => {
    const card = el('button', `material-card alert-card${String(alert.id) === String(ui.alertId) ? ' active' : ''}`);
    card.type = 'button';
    const top = el('div', 'card-top');
    top.append(el('span', 'publisher', metrics?.scope?.name || '霍尔木兹海峡'), badge(label(alert.status), ['active','recovering'].includes(alert.status) ? 'waiting' : ''));
    card.append(top, el('h3', '', 'PortWatch 可见通行量持续偏低提醒'), el('p', 'card-excerpt', alert.summary), el('div', 'card-meta', `观测 ${alert.started_on} 至 ${alert.resolved_on || alert.last_evaluated_date || '—'} · ${label(alert.origin)}`), el('p', 'alert-time', `系统首次记录 ${date(alert.detected_at)}`));
    card.addEventListener('click', () => { ui.alertId = alert.id; ui.detail = 'alert'; renderOnline(); document.querySelector('.details-panel').scrollTop = 0; });
    list.append(card);
  });
  const observations = metrics?.observations || [];
  $('pw-observation-count').textContent = `· ${observations.length} 日`;
  $('pw-observations').replaceChildren();
  appendDailyTable([...observations].reverse(), $('pw-observations'), true);
}
function renderPortWatchChart(metrics) {
  const parent = $('pw-chart');
  parent.replaceChildren();
  const legend = $('pw-chart-legend');
  legend.replaceChildren();
  const styles = [['n_total','#087f76','可见通行量'],['baseline','#71858c','参考中位数（候选及提醒期间冻结）'],['trigger_threshold','#c38535','触发阈值'],['recovery_threshold','#75975a','解除阈值']];
  [...styles, ['alert','#fae7cc','提醒观测区间']].forEach(([,color,name]) => { const item = el('span', 'legend-item'); const line = el('span', 'legend-line'); line.style.background = color; item.append(line, el('span','',name)); legend.append(item); });
  if (!metrics?.series?.length) { parent.append(el('div', 'chart-empty', '尚无已保存的日度序列')); return; }
  const byDate = new Map(metrics.series.map((row) => [row.observed_date, row]));
  const start = metrics.range_start || metrics.series[0].observed_date;
  const end = metrics.range_end || metrics.series[metrics.series.length - 1].observed_date;
  const days = [];
  for (let stamp = Date.parse(`${start}T00:00:00Z`); stamp <= Date.parse(`${end}T00:00:00Z`); stamp += 86400000) {
    const day = new Date(stamp).toISOString().slice(0, 10);
    days.push(byDate.get(day) || {observed_date:day, n_total:null});
  }
  const width = 640, height = 250, left = 43, right = 15, top = 18, bottom = 32;
  const chart = svgNode('svg', {viewBox:`0 0 ${width} ${height}`, class:'chart-svg portwatch-chart', role:'img', 'aria-label':`PortWatch 日度可见通行量，${start} 至 ${end}；缺失日期断线。`});
  const max = Math.max(1, ...days.flatMap((row) => styles.map(([key]) => numeric(row[key]) ? row[key] : 0))) * 1.08;
  const x = (index) => left + index / Math.max(days.length - 1, 1) * (width - left - right);
  const y = (value) => height - bottom - value / max * (height - top - bottom);
  const position = (day) => (Date.parse(`${day}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000;
  ui.alerts.filter((alert) => alert.status !== 'revoked').forEach((alert) => {
    const from = Math.max(0, position(alert.started_on));
    const to = Math.min(days.length - 1, position(alert.resolved_on || metrics.latest_observed_date || end));
    if (to < from) return;
    const band = svgNode('rect', {x:x(from),y:top,width:Math.max(x(to)-x(from),3),height:height-top-bottom,fill:'#fae7cc',opacity:'.7'});
    band.append(svgNode('title', {}, `${label(alert.status)}：${alert.started_on} 至 ${alert.resolved_on || metrics.latest_observed_date}`));
    chart.append(band);
  });
  for (let tick = 0; tick <= 4; tick++) {
    const value = max * tick / 4;
    chart.append(svgNode('line',{x1:left,y1:y(value),x2:width-right,y2:y(value),class:'grid'}), svgNode('text',{x:left-7,y:y(value)+3,'text-anchor':'end'},number(value)));
  }
  [...styles].reverse().forEach(([key,color]) => {
    let path = '', connected = false;
    days.forEach((row,index) => {
      if (!numeric(row[key])) { connected = false; return; }
      path += `${connected ? 'L' : 'M'}${x(index)},${y(row[key])} `;
      connected = true;
    });
    chart.append(svgNode('path',{d:path,fill:'none',stroke:color,'stroke-width':key === 'n_total' ? 2.3 : 1.4,...(key !== 'n_total' ? {'stroke-dasharray':key === 'baseline' ? '6 3' : '3 4'} : {})}));
  });
  days.forEach((row,index) => {
    if (numeric(row.n_total)) {
      const point = svgNode('circle',{cx:x(index),cy:y(row.n_total),r:2.2,fill:'#087f76'});
      point.append(svgNode('title',{},`${row.observed_date}：${number(row.n_total)} · 参考 ${number(row.baseline)} · ${label(row.rule_status)}`));
      chart.append(point);
    }
    if (index === 0 || index === days.length - 1 || index % Math.ceil(days.length / 5) === 0) chart.append(svgNode('text',{x:x(index),y:height-9,'text-anchor':index === 0 ? 'start' : index === days.length - 1 ? 'end' : 'middle'},row.observed_date.slice(5)));
  });
  parent.append(chart, el('p', '', '缺日与无效主计数断线；有效零值按零显示。阴影表示提醒所依据的观测日期，不表示当时已发出提醒。'));
}
function appendDailyTable(rows, parent, showDetail = false) {
  if (!rows.length) { parent.append(el('p', 'small muted', '尚无记录。')); return; }
  const table = el('table', 'daily-table');
  const head = el('thead'), header = el('tr');
  ['观测日期','n_total','油轮 / 货船','材料版本'].forEach((name) => header.append(el('th', '', name)));
  head.append(header);
  const body = el('tbody');
  rows.forEach((row) => {
    const tr = el('tr'), day = el('td');
    if (showDetail) { const button = el('button','text-button',row.observed_date); button.type = 'button'; button.addEventListener('click',() => { ui.detail = 'pw-observation'; renderDailyDetail(row); document.querySelector('.details-panel').scrollTop = 0; }); day.append(button); }
    else day.textContent = row.observed_date;
    const reference = el('td');
    appendMaterialLink(row, reference);
    tr.append(day, el('td','',number(row.n_total)), el('td','',`${number(row.n_tanker)} / ${number(row.n_cargo)}`), reference);
    body.append(tr);
  });
  table.append(head, body);
  const scroll = el('div', 'table-scroll');
  scroll.append(table);
  parent.append(scroll);
}
function appendMaterialLink(row, parent) {
  if (row.material_id) {
    const button = el('button', 'reference-button', `材料 ${row.material_id}${row.material_version != null ? ` · v${row.material_version}` : ''}`);
    button.type = 'button';
    button.addEventListener('click', async () => {
      try { ui.evidenceMaterial = await api(`/api/materials/${encodeURIComponent(row.material_id)}`); ui.detail = 'evidence-material'; renderMaterial(ui.evidenceMaterial); document.querySelector('.details-panel').scrollTop = 0; }
      catch (error) { notify(error.message); }
    });
    parent.append(button);
  }
  if (row.url) { const p = el('p','source-query'); link(row.url, '原始查询', p); parent.append(p); }
}
function dailyFacts(row, parent) {
  facts([['观测日期',row.observed_date],['可见通行量',number(row.n_total)],['油轮计数',number(row.n_tanker)],['货船计数',number(row.n_cargo)],['名义合计运力',number(row.capacity)],['油轮名义运力',number(row.capacity_tanker)],['货船名义运力',number(row.capacity_cargo)],['主计数状态',row.validity],['来源发布时间',row.available_at ? date(row.available_at) : '未知'],['系统首次见到',date(row.first_seen_at)],['最近获取时间',date(row.last_fetched_at)]],parent);
  parent.append(el('p','small muted','计数为 PortWatch AIS 派生可见通行量；名义运力沿用来源单位，不表示实际载货量。'));
  appendMaterialLink(row, parent);
}
function renderDailyDetail(row) {
  $('detail-content').replaceChildren(badge('PortWatch · 日度数值','accent'), el('h2','detail-heading',`${row.observed_date} · 已保存观测`));
  dailyFacts(row,section('结构化来源字段'));
}
function evidenceFold(title, rows, parent, key) {
  const fold = el('details','asset-block table-scroll');
  fold.dataset.fold = key;
  fold.append(el('summary','',title));
  appendDailyTable(rows,fold);
  parent.append(fold);
}
function savedEvidenceFold(title, evidence, parent, key) {
  const fold = el('details','asset-block');
  fold.dataset.fold = key;
  fold.append(el('summary','',title));
  const computed = evidence.computed;
  const rule = evidence.rule;
  facts([['保存时状态',label(evidence.status)],['程序参考中位数',number(computed.baseline)],['参考日期范围',`${computed.baseline_start} 至 ${computed.baseline_end}`],['有效参考日',computed.baseline_valid_days],['规则标识',rule.id],['触发阈值',`${number(computed.trigger_threshold)}（连续 ${rule.trigger_days} 日严格低于）`],['解除阈值',`${number(computed.recovery_threshold)}（连续 ${rule.recovery_days} 日达到）`],['偏低首日',computed.started_on],['第二个命中日',computed.triggered_on],['最新评估日期',computed.latest_observed_date],['解除依据日期',computed.resolved_on]],fold);
  evidenceFold('触发记录与材料版本',evidence.observations.trigger,fold,`${key}-trigger`);
  evidenceFold(`参考样本 · ${evidence.reference_samples.length} 日`,evidence.reference_samples,fold,`${key}-reference`);
  parent.append(fold);
}
function renderAlert(alert) {
  const parent = $('detail-content');
  const openFolds = ui.renderedAlertId === alert.id ? [...parent.querySelectorAll('details[open]')].map((node) => node.dataset.fold) : [];
  ui.renderedAlertId = alert.id;
  parent.replaceChildren(badge(label(alert.status), ['active','recovering'].includes(alert.status) ? 'waiting' : ''), el('h2','detail-heading','PortWatch 可见通行量持续偏低提醒'), el('p','detail-meta',`${ui.metrics?.scope?.name || '霍尔木兹海峡'} · ${label(alert.origin)} · ${alert.id}`));
  const conclusion = section('程序数值结论');
  conclusion.append(el('p','',alert.summary));
  if (alert.origin === 'initialization') conclusion.append(el('p','small muted','初始化时发现已存在的持续偏低条件。起点为观测依据日期，系统首次记录时间见下方。'));
  const latest = alert.latest_observation;
  facts([['当前状态',label(alert.status)],['偏低首日',alert.started_on],['第二个命中日',alert.triggered_on],['最新评估日期',alert.last_evaluated_date],['解除依据日期',alert.resolved_on],['最新 / 冻结参考',numeric(latest?.n_total) && alert.baseline > 0 ? `${number(latest.n_total / alert.baseline * 100)}%` : '—']],conclusion);
  const trigger = section('触发日期与实际数值');
  appendDailyTable(alert.trigger_observations || [],trigger);
  const rule = alert.rule;
  const reference = section('冻结参考与规则快照');
  facts([['中位数 B',number(alert.baseline)],['参考起止',`${alert.baseline_start} 至 ${alert.baseline_end}`],['有效日数',alert.baseline_valid_days],['规则标识',rule.id],['历史 / 参考窗口',`${rule.history_days} / ${rule.baseline_days} 天`],['最少参考日',rule.min_baseline_days],['触发条件',`连续 ${rule.trigger_days} 日 < ${rule.trigger_ratio * 100}% × B（${number(alert.baseline * rule.trigger_ratio)}）`],['解除条件',`连续 ${rule.recovery_days} 日 ≥ ${rule.recovery_ratio * 100}% × B（${number(alert.baseline * rule.recovery_ratio)}）`]],reference);
  evidenceFold(`参考样本明细 · ${(alert.reference_samples || []).length} 日`, alert.reference_samples || [], reference, 'reference');
  const current = section('最新观测与恢复');
  facts([['已连续恢复',`${alert.recovery_count || 0} / ${rule.recovery_days} 日`],['观测缺口',alert.data_gaps?.length ? alert.data_gaps.join('、') : '无已记录缺口']],current);
  if (latest) dailyFacts(latest,current);
  if (alert.status === 'active' && latest?.n_total >= alert.baseline * rule.trigger_ratio) current.append(el('p','','尚未达到持续恢复条件。'));
  facts([['系统首次记录',date(alert.detected_at)],['系统记录解除',date(alert.resolved_at)],['来源最近获取',date(ui.metrics?.last_success_at)],['来源发布时间',latest?.available_at ? date(latest.available_at) : '未知'],['当前证据版本',alert.evidence_version]],section('时间与证据版本'));
  if (alert.original_evidence || alert.changes?.length) {
    const changes = section('状态变化与来源修订');
    if (alert.original_evidence) savedEvidenceFold('首次记录的证据',alert.original_evidence,changes,'original-evidence');
    (alert.changes || []).forEach((change,index) => {
      if (typeof change === 'string') { changes.append(el('p','small',change)); return; }
      const reason = change.type === 'source_revision' ? change.reason || '因来源修订更正' : change.type === 'resolved' ? '满足持续恢复条件，提醒解除' : '规则状态变化';
      const observation = change.type === 'source_revision' ? `修订观测日期：${(change.observed_dates || []).join('、')}` : `观测依据日期：${change.observed_date || '—'}`;
      changes.append(el('p','small',`${reason} · ${label(change.previous_status)} → ${label(change.status)}\n系统记录：${date(change.at)}\n${observation} · 证据版本 ${change.evidence_version}`));
      if (change.previous_evidence) savedEvidenceFold('查看本次修订前的证据',change.previous_evidence,changes,`previous-evidence-${index}`);
    });
  }
  const explain = section('Qwen 按需解释');
  const explanation = alert.explanation;
  const currentEvidence = explanation?.evidence_version === alert.evidence_version && explanation?.status !== 'stale';
  const explanationLabels = {pending:'未生成',running:'生成中',completed:'已生成',failed:'失败',stale:'旧证据版本'};
  explain.append(el('p','small muted','解释仅使用已保存的提醒证据；状态、数值与判定由程序确定。'));
  facts([['解释状态',explanation ? currentEvidence ? explanationLabels[explanation.status] || label(explanation.status) : '旧证据版本' : '未生成'],['生成时间',date(explanation?.generated_at)],['解释证据版本',explanation?.evidence_version],['模型',explanation?.model]],explain);
  if (explanation?.text) explain.append(el('p',currentEvidence ? '' : 'stale-explanation',explanation.text));
  if (explanation?.error) explain.append(el('p','',explanation.error));
  (explanation?.material_ids || []).forEach((id) => appendMaterialLink({material_id:id},explain));
  const button = el('button','primary full',explanation?.status === 'running' ? '正在生成解释…' : explanation?.status === 'completed' && currentEvidence ? '已生成当前证据解释' : explanation?.status === 'failed' ? '重新生成解释' : '生成解释');
  button.type = 'button';
  button.disabled = Boolean(ui.state?.active_run_id) || ui.saving || (explanation?.status === 'completed' && currentEvidence);
  button.addEventListener('click', () => action(async () => {
    const result = await api(`/api/alerts/${encodeURIComponent(alert.id)}/explain`, {});
    ui.alerts = ui.alerts.map((item) => item.id === alert.id ? result.alert : item);
    if (result.run) ui.state.active_run_id = result.run.id;
    ui.detail = 'alert'; ui.alertId = alert.id;
  }));
  explain.append(button);
  if (ui.state?.active_run_id && explanation?.status !== 'running') explain.append(el('p','small muted','当前已有任务运行，完成后可生成解释。'));
  parent.querySelectorAll('details').forEach((node) => { node.open = openFolds.includes(node.dataset.fold); });
}
function renderArticleChart() {
  const parent = $('article-chart');
  parent.replaceChildren();
  const counts = new Map();
  ui.materials.forEach((material) => {
    const stamp = material.observed_at || material.available_at || material.fetched_at;
    const key = stamp ? String(stamp).slice(0,10) : '日期未声明';
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  const entries = [...counts].sort(([a],[b]) => a.localeCompare(b));
  if (!entries.length) { parent.append(el('div','chart-empty','取得资料后显示实际数量')); return; }
  const width = 540, height = 120, left = 24, right = 12, top = 19, bottom = 27;
  const max = Math.max(...entries.map(([,value]) => value));
  const chart = svgNode('svg',{viewBox:`0 0 ${width} ${height}`,class:'chart-svg',role:'img','aria-label':'按资料时间统计的实际材料数量'});
  chart.append(svgNode('line',{x1:left,y1:height-bottom,x2:width-right,y2:height-bottom,class:'grid'}));
  const cell = (width-left-right)/entries.length;
  entries.forEach(([key,value],index) => {
    const x = left+cell*index+cell/2, barWidth = Math.min(cell*.42,36), barHeight = value/max*(height-top-bottom);
    const rect = svgNode('rect',{x:x-barWidth/2,y:height-bottom-barHeight,width:barWidth,height:barHeight,rx:3,fill:'#76b7a7'});
    rect.append(svgNode('title',{},`${key}：${value} 条`));
    chart.append(rect,svgNode('text',{x,y:height-bottom-barHeight-6,'text-anchor':'middle',class:'chart-value'},value));
    if (entries.length < 9 || index % Math.ceil(entries.length/7) === 0 || index === entries.length-1) chart.append(svgNode('text',{x,y:height-7,'text-anchor':'middle'},key.length===10 ? key.slice(5) : key));
  });
  parent.append(chart);
}
function lineChart(title, unit, rows, series, note) {
  const card = el('div','chart-card');
  const heading = el('div','section-heading');
  heading.append(el('h3','',title),el('span','tiny-label',unit));
  card.append(heading);
  const width=560,height=190,left=38,right=18,top=16,bottom=29;
  const values=rows.flatMap((row)=>series.map((item)=>item.value(row))).filter((value)=>typeof value==='number');
  if (!values.length) { card.append(el('p','muted','该指标没有可绘制的观测数值。')); return card; }
  const ceiling=Math.ceil(Math.max(...values)/4/10)*10*4 || 1;
  const x=(index)=>left+(rows.length===1 ? (width-left-right)/2 : index/(rows.length-1)*(width-left-right));
  const y=(value)=>height-bottom-value/ceiling*(height-top-bottom);
  const chart=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,class:'chart-svg',role:'img','aria-label':`${title}，${rows.length} 条原报告观测，单位 ${unit}`});
  for(let tick=0;tick<=4;tick++) {
    const value=ceiling*tick/4;
    chart.append(svgNode('line',{x1:left,y1:y(value),x2:width-right,y2:y(value),class:'grid'}),svgNode('text',{x:left-9,y:y(value)+3,'text-anchor':'end'},value));
  }
  rows.forEach((row,index)=>chart.append(svgNode('text',{x:x(index),y:height-8,'text-anchor':'middle'},row.date.slice(5))));
  series.forEach((item)=>{
    let path='';
    let connected=false;
    rows.forEach((row,index)=>{
      const value=item.value(row);
      if(typeof value!=='number') { connected=false; return; }
      path+=`${connected?'L':'M'} ${x(index)} ${y(value)} `;
      connected=true;
    });
    chart.append(svgNode('path',{d:path,fill:'none',stroke:item.color,'stroke-width':2.1,'stroke-linejoin':'round'}));
    rows.forEach((row,index)=>{
      const value=item.value(row);
      if(typeof value!=='number') return;
      const dot=svgNode('circle',{cx:x(index),cy:y(value),r:3.7,fill:'white',stroke:item.color,'stroke-width':2,tabindex:0,role:'button','aria-label':`${row.date} ${item.name} ${value}`});
      dot.append(svgNode('title',{},`${row.date} · ${item.name}：${value}`));
      dot.addEventListener('click',()=>renderObservation(row));
      dot.addEventListener('keydown',(event)=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();renderObservation(row);}});
      chart.append(dot);
    });
  });
  const legend=el('div','chart-legend');
  series.forEach((item)=>{const itemNode=el('span','legend-item');const stroke=el('span','legend-line');stroke.style.background=item.color;itemNode.append(stroke,document.createTextNode(item.name));legend.append(itemNode);});
  card.append(chart,legend,el('p','',note));
  return card;
}
async function loadHistory() {
  try {
    if(!ui.history) ui.history=await api('/api/history');
    if(ui.mode!=='history') return;
    const history=ui.history,report=history.freight,metadata=report.report_metadata,rows=report.daily_observations;
    $('history-date').textContent=metadata.generated_date;
    $('history-window').textContent=`${metadata.observation_window.start} 至 ${metadata.observation_window.end}`;
    $('history-intro').textContent=`报告包含 ${rows.length} 条日度观测，覆盖 ${rows[0]?.date || '—'} 至 ${rows[rows.length-1]?.date || '—'}。按观测日期展示的历史快照。`;
    $('history-charts').replaceChildren(
      lineChart('PortWatch 过峡船舶 / 航次','计数 / 日',rows,[{name:'总计',color:'#138978',value:r=>r.portwatch?.n_total},{name:'油轮',color:'#74aaa0',value:r=>r.portwatch?.n_tanker},{name:'货船',color:'#9bacbf',value:r=>r.portwatch?.n_cargo}],report.metric_definitions.n_total),
      lineChart('WTO 商品运输指数','2025 年 1—3 月日均 = 100',rows,[{name:'原油出口',color:'#138978',value:r=>r.wto?.crude_oil_export_index},{name:'LNG 出口',color:'#d5a163',value:r=>r.wto?.lng_export_index}],report.wto_cross_check.availability_limit.operational_implication)
    );
    const documents=$('history-documents');
    documents.replaceChildren();
    [ ['原报告摘要','既有结果 · 日期与解释限制',renderReport],['来源与时间信息',`${history.sources.sources.length} 项来源记录`,renderSources],['Sentinel 资产目录',`${history.assets.reduce((count,asset)=>count+asset.rows.length,0)} 条目录记录 · 结果摘要`,renderAssets] ].forEach(([title,description,callback])=>{
      const button=el('button','history-document');button.type='button';const content=el('span');content.append(el('strong','',title),el('small','',description));button.append(content,el('span','','↗'));button.addEventListener('click',callback);documents.append(button);
    });
    const replay=history.replay;
    $('replay-button').disabled=!replay.available || Boolean(isRunning(activeRun()));
    $('replay-description').textContent=replay.available?`${replay.label} · ${replay.count} 条可用材料，按顺序分批处理。`:replay.label || '当前案例仅提供既有结果快照，暂无可回放的原始材料。';
    renderReport();
  } catch(error) {notify(error.message);}
}
function historyHeader(title,description) {
  $('detail-content').replaceChildren(badge('历史 · 结果快照','accent'),el('h2','detail-heading',title),el('p','detail-meta',description));
}
function renderReport() {
  ui.detail='history-report';
  const report=ui.history.freight;
  historyHeader('原报告摘要',`原报告生成于 ${report.report_metadata.generated_date}`);
  section('原报告结论').append(el('p','',report.executive_assessment.main_conclusion));
  section('原报告的时间解释').append(el('p','',report.executive_assessment.recommended_wording));
  const limitations=section('原报告解释限制'),list=el('ul');
  report.limitations.forEach((text)=>list.append(el('li','',text)));
  limitations.append(list);
  const sources=section('报告引用来源');
  report.data_sources.forEach((source)=>{const p=el('p');if(source.url)link(source.url,source.name,p);else p.textContent=source.name;sources.append(p);});
}
function renderObservation(row) {
  ui.detail='history-observation';
  historyHeader(`${row.date} · 日度观测`,'数值来自已有报告的 daily_observations');
  facts([['总船 / 航次',row.portwatch?.n_total],['油轮计数',row.portwatch?.n_tanker],['货船计数',row.portwatch?.n_cargo],['名义合计运力',row.portwatch?.capacity]],section('PortWatch 原报告数值'));
  section('运力口径').append(el('p','',ui.history.freight.metric_definitions.capacity),el('p','',ui.history.freight.metric_definitions.capacity_unit));
  facts([['原油出口',row.wto?.crude_oil_export_index],['LNG 出口',row.wto?.lng_export_index],['化肥出口',row.wto?.fertilizer_export_index],['农产品进口',row.wto?.agricultural_import_index]],section('WTO 商品运输指数'));
  section('指数口径').append(el('p','',ui.history.freight.metric_definitions.wto_index));
  facts([['观测日期',row.date],['历史发布时间',null],['报告生成日期',ui.history.freight.report_metadata.generated_date]],section('时间信息'));
  section('原报告时间说明').append(el('p','',ui.history.freight.wto_cross_check.availability_limit.operational_implication));
}
function renderSources() {
  ui.detail='history-sources';
  historyHeader('来源与时间信息',`来源清单生成于 ${ui.history.sources.generated_at_utc}`);
  ui.history.sources.sources.forEach((source)=>{
    const block=section(source.publisher || source.id);
    facts([['来源 ID',source.id],['原记录状态',source.status],['获取时间',source.retrieved_at_utc],['历史发布时间',source.available_at]],block);
    if(source.classification)block.append(el('p','',source.classification));
    if(source.reason)block.append(el('p','',source.reason));
    const sourceLinks=[['服务入口',source.service_url],['公开看板',source.dashboard_url],['方法说明',source.methodology_url],['官方目录',source.official_catalog],['来源页面',source.source_page],['API 文档',source.api_documentation]];
    sourceLinks.forEach(([name,url])=>{if(url){const p=el('p');link(url,name,p);block.append(p);}});
    (source.files || []).forEach((file)=>{const p=el('p');link(file.url,file.indicator,p);block.append(p);});
  });
}
function renderAssets() {
  ui.detail='history-assets';
  historyHeader('Sentinel 资产目录','已保存的产品标识与结果摘要');
  const report=ui.history.freight.eo_cross_check;
  section('原报告观测说明').append(el('p','',report.sentinel_1_r166.limitation),el('p','',report.sentinel_2_40rep.limitation));
  const eo=ui.history.eo;
  facts(eo.sentinel_1_r166.default_threshold.raw_candidate_counts.map((item)=>[item.date,item.count]),section('Sentinel-1 原始亮散射候选数'));
  facts(eo.sentinel_2_40rep.bright_candidate_counts.map((item)=>[item.date,item.count]),section('Sentinel-2 亮目标候选数'));
  section('原结果口径').append(el('p','',eo.sentinel_1_r166.interpretation),el('p','',eo.sentinel_2_40rep.interpretation));
  ui.history.assets.forEach((asset)=>{
    const block=section(asset.filename);
    asset.rows.forEach((row,index)=>{
      const detail=el('details','asset-block');
      detail.append(el('summary','',row.product_name || row.item_id || row.id || row.asset_name || `资产记录 ${index+1}`));
      const list=el('dl','detail-facts');
      Object.entries(row).forEach(([key,value])=>{list.append(el('dt','',key));const dd=el('dd');if(/^https?:\/\//.test(String(value)))link(value,'打开资产来源',dd);else dd.textContent=format(value);list.append(dd);});
      detail.append(list);block.append(detail);
    });
  });
}
$('online-tab').addEventListener('click',()=>setMode('online'));
$('history-tab').addEventListener('click',()=>setMode('history'));
$('new-monitor').addEventListener('click',()=>{ui.monitorId='';ui.runId='';ui.materialId=null;ui.alertId=null;ui.detail=null;ui.materials=[];ui.runs=[];ui.metrics=null;ui.alerts=[];fillForm(null);remember();renderState();renderOnline();$('topic').focus();});
$('monitor-select').addEventListener('change',async(event)=>{ui.monitorId=event.target.value;ui.runId='';ui.materialId=null;ui.alertId=null;ui.detail=null;ui.metrics=null;ui.alerts=[];fillForm(currentMonitor());remember();await refresh();});
$('source').addEventListener('change',()=>{const portwatch=$('source').value==='portwatch';if(portwatch && currentMonitor()?.source!=='portwatch')$('interval').value=ui.state?.portwatch?.default_interval_minutes || 1440;else if(!ui.monitorId && !portwatch)$('interval').value=30;renderSourceSettings();if(!ui.monitorId)renderOnline();});
$('pw-alert-filter').addEventListener('change',renderPortWatch);
$('run-select').addEventListener('change',async(event)=>{ui.runId=event.target.value;ui.detail='run';ui.materialId=null;remember();try{await loadMaterials();}catch(error){notify(error.message);}});
$('monitor-form').addEventListener('submit',(event)=>{event.preventDefault();action(async()=>{const monitor=await saveMonitor();if(!monitor)return;const run=await api(`/api/monitors/${encodeURIComponent(monitor.id)}/run`,{});ui.runId=String(run.id);ui.detail='run';remember();});});
$('save-button').addEventListener('click',()=>action(async()=>{const monitor=await saveMonitor();if(monitor)notify('主题已保存。');}));
$('schedule-button').addEventListener('click',()=>action(async()=>{const enabled=!currentMonitor()?.enabled;const monitor=await saveMonitor();if(!monitor)return;await api(`/api/monitors/${encodeURIComponent(monitor.id)}/schedule`,{enabled});}));
$('cancel-button').addEventListener('click',()=>action(async()=>{const run=activeRun();if(run)await api(`/api/runs/${encodeURIComponent(run.id)}/cancel`,{});}));
$('refresh-button').addEventListener('click',()=>{notify();refresh();});
$('run-summary-button').addEventListener('click',()=>{ui.detail='run';renderRunDetail(currentRun());document.querySelector('.details-panel').scrollTop=0;});
$('replay-button').addEventListener('click',()=>action(async()=>{const run=await api('/api/history/replay',{batch_size:3});ui.monitorId=String(run.monitor_id);ui.runId=String(run.id);ui.formLoaded=false;ui.detail='run';remember();setMode('online');}));
setMode(ui.mode);
refresh();
setInterval(refresh,4000);
