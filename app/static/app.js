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
};
const labels = {
  unknown: '状态未知', ready: '可用', available: '可用', online: '在线', ok: '成功', success: '成功', succeeded: '已完成', completed: '已完成',
  failed: '失败', error: '失败', unavailable: '不可用', disabled: '未启用', idle: '等待运行', pending: '待分析', pending_analysis: '待分析',
  running: '运行中', queued: '等待处理', collecting: '采集资料', fetching: '获取材料', analyzing: '模型分析', summarizing: '生成本轮摘要',
  cancelled: '已取消', canceled: '已取消', cancelling: '正在取消', interrupted: '运行中断', empty: '无新增材料', no_new: '无新增材料',
  title_only: '仅标题', summary: '来源摘要', full_text: '正文', article: '正文', aggregate: '日度聚合', replay: '原始材料回放', history: '结果快照', snapshot: '结果快照',
  stored: '材料已保存', source_failed: '来源获取失败', model_failed: '模型分析失败', done: '已分析', snippet: '来源摘要', article_text: '正文', invalid_output: '输出格式错误',
  normalize: '整理材料', fetch: '获取材料', collect: '采集资料', llm: 'Qwen 分析', analyze: 'Qwen 分析', persist: '保存材料', save: '保存结果',
};
const label = (value) => labels[value] || value || '—';
const isRunning = (run) => run && ['running', 'queued', 'cancelling'].includes(run.status);
const format = (value) => value == null || value === '' ? '未声明' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
const date = (value) => {
  if (!value) return '未声明';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
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
  $('interval').value = monitor?.interval_minutes || 30;
  ui.formLoaded = true;
}
function renderState() {
  const state = ui.state;
  const model = state.model || {name:'Qwen',status:'unknown'};
  $('model-status').textContent = `${model.name || 'Qwen'} · ${label(model.status)}`;
  $('model-status').title = model.last_error || (model.last_call_at ? `最近调用：${date(model.last_call_at)}` : '尚无模型调用记录');
  $('model-status').className = `status-item ${['ready','available','ok','online'].includes(model.status) ? 'good' : ['failed','error','unavailable'].includes(model.status) ? 'bad' : ''}`;
  const selected = currentMonitor();
  const latest = state.runs.find((run) => String(run.monitor_id) === String(ui.monitorId));
  const sourceStatus = latest?.source_status;
  $('source-status').textContent = `来源 · ${sourceStatus ? typeof sourceStatus === 'string' ? label(sourceStatus) : Object.entries(sourceStatus).map(([key, value]) => `${key} ${typeof value === 'object' ? label(value.status) : label(value)}`).join(' / ') : '尚未运行'}`;
  $('source-status').title = $('source-status').textContent;
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
  const replayMonitor = selected?.mode === 'replay';
  $('schedule-button').textContent = selected?.enabled ? '停止周期更新' : '开始周期更新';
  $('save-button').disabled = replayMonitor;
  $('schedule-button').disabled = replayMonitor;
  $('schedule-info').textContent = replayMonitor ? '当前为历史材料回放，请在历史案例页回放下一批。' : selected?.enabled ? `周期更新已开启 · 每 ${selected.interval_minutes} 分钟运行` : '手动运行 · 周期更新未开启';
  const active = activeRun();
  $('cancel-button').disabled = !isRunning(active) || active?.status === 'cancelling';
  $('running-dot').classList.toggle('active', Boolean(isRunning(active)));
  $('run-button').disabled = replayMonitor || Boolean(isRunning(active)) || ui.saving;
  $('current-stage').textContent = active ? label(active.stage || active.status) : latest ? label(latest.status) : '等待运行';
  $('current-runtime').textContent = active ? `开始于 ${date(active.started_at)} · 已用 ${duration(Date.now() - new Date(active.started_at).getTime())}` : latest ? `最近运行 ${date(latest.started_at)}${latest.error ? ` · ${latest.error}` : ''}` : '运行后显示真实处理进度';
  if (ui.mode === 'online') {
    $('content-title').textContent = selected?.topic || '从主题开始，持续整理公开资料';
    $('mode-badge').textContent = currentRun()?.mode === 'replay' ? '原始材料回放' : '在线资料';
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
    else { ui.materials = []; ui.runs = []; if (ui.mode === 'online') renderOnline(); }
    remember();
  } catch (error) { notify(error.message); }
  finally { ui.refreshing = false; }
}
async function loadMaterials() {
  const monitorId = ui.monitorId;
  const runId = ui.runId;
  const result = await api(`/api/monitors/${encodeURIComponent(monitorId)}/materials${runId ? `?run_id=${encodeURIComponent(runId)}` : ''}`);
  if (monitorId !== ui.monitorId || runId !== ui.runId) return;
  ui.materials = result.materials;
  ui.runs = result.runs;
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
  const signature = JSON.stringify([ui.materials, ui.materialId]);
  if (signature !== ui.materialSignature) {
    ui.materialSignature = signature;
    const list = $('material-list');
    list.replaceChildren();
    if (!ui.materials.length) {
      const empty = el('div', 'empty-state');
      empty.append(el('span', 'empty-symbol', '⌁'), el('h3', '', run ? (run.error ? '本轮处理未完成' : '当前没有资料') : '资料将在这里汇集'), el('p', '', run?.error || (run ? '本轮没有新增资料，或材料仍在获取中。' : '设置主题并运行，查看来源、摘要与处理进度。')));
      list.append(empty);
    }
    ui.materials.forEach((material) => {
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
    renderArticleChart();
  }
  const detailScroll = document.querySelector('.details-panel').scrollTop;
  if (ui.detail === 'run') renderRunDetail(run);
  else if (ui.detail === 'material' && ui.materials.some((item) => String(item.id) === String(ui.materialId))) renderMaterial(ui.materials.find((item) => String(item.id) === String(ui.materialId)));
  else if (run && !ui.detail) { ui.detail = 'run'; renderRunDetail(run); }
  else if (!ui.detail) emptyDetail();
  document.querySelector('.details-panel').scrollTop = detailScroll;
  $('mode-badge').textContent = run?.mode === 'replay' ? '原始材料回放' : '在线资料';
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
  const analysis = section('Qwen 资料摘要');
  analysis.append(el('p', '', material.analysis?.summary ? format(material.analysis.summary) : `当前状态：${label(material.analysis_status)}`));
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
  const summary = section('本次模型生成的摘要');
  if (run.summary) {
    summary.append(el('p', '', typeof run.summary === 'object' ? format(run.summary.summary || run.summary) : run.summary));
    if (typeof run.summary === 'object' && run.summary.material_ids) renderReferences(run.summary.material_ids, summary);
  } else summary.append(el('p', 'muted', run.error || (isRunning(run) ? '运行尚在进行，摘要将在模型调用完成后显示。' : '本轮未生成模型摘要。')));
  facts([['运行 ID', run.id], ['运行状态', label(run.status)], ['当前阶段', label(run.stage)], ['开始时间', run.started_at], ['结束时间', run.finished_at], ['新增 / 已分析', `${run.new_count ?? 0} / ${run.analyzed_count ?? 0}`], ['失败次数', run.failed_count ?? 0]], section('运行信息'));
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
$('new-monitor').addEventListener('click',()=>{ui.monitorId='';ui.runId='';ui.materialId=null;ui.detail=null;ui.materials=[];ui.runs=[];fillForm(null);remember();renderState();renderOnline();$('topic').focus();});
$('monitor-select').addEventListener('change',async(event)=>{ui.monitorId=event.target.value;ui.runId='';ui.materialId=null;ui.detail=null;fillForm(currentMonitor());remember();await refresh();});
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
