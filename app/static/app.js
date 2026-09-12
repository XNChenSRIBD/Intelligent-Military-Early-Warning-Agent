const $ = (id) => document.getElementById(id);
const ui = {
  mode: 'online',
  onlineView: 'pipeline',
  pipeline: null,
  acquisition: null,
  acquisitionSubscriptionId: '',
  acquisitionSubscriptionDirty: false,
  acquisitionPolicyDirty: false,
  acquisitionBusy: false,
  acquisitionTaskSignature: '',
  acquisitionResourceId: null,
  replayCaseId: localStorage.getItem('workspace-replay-case') || '',
  replayCase: null,
  replayCaseSignature: '',
  replayListSignature: '',
  gnssSeriesKey: '',
  gnssSignature: '',
  pipelineFormLoaded: false,
  pipelineFormDirty: false,
  pipelineAlert: null,
  pipelineDetailSignature: '',
  pipelineListSignature: '',
  runDetails: {},
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
  paused: '已暂停', enabled: '已启用', retry_wait: '等待自动重试', insufficient_evidence: '证据不足', new: '新增', ongoing: '持续', revised: '修订',
  numeric_rule: '数值规则异常', news_clue: '公开报道线索', no_anomaly: '本批未形成异常线索', monitoring: '持续监测中',
  case_replay: '历史自动回放', gnss_observation: 'GNSS 观测线索', gnss: 'GNSS 观测', blocked: '未完成 · 已阻塞',
  prepared: '输入已准备', processing: '处理观测', pending_inputs: '等待输入', partial: '部分完成',
  downloading: '正在下载', computing: '正在计算', waiting_source: '等待来源发布', auth_required: '认证不可用',
  source_missing: '来源缺失', waiting_space: '等待缓存空间', waiting_resources: '等待资源', incomplete: '尚未完成',
  original_reused: '复用已有原件', result_reused: '复用兼容结果', downloaded: '已实际下载', computed: '已计算',
  raw_deleted: '原件已清理', cleaned: '已清理', expired: '到期回收', capacity: '容量回收',
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
function currentRun() { const run = ui.runs.find((item) => String(item.id) === String(ui.runId)) || ui.runs[0] || null; return run && ui.runDetails[run.id] ? {...run,...ui.runDetails[run.id]} : run; }
function activeRun() { return ui.state?.runs.find((item) => String(item.id) === String(ui.state.active_run_id)); }
function isPortWatch() { return (currentMonitor()?.source || $('source').value) === 'portwatch'; }
function isPipeline() { return ui.mode === 'online' && ui.onlineView === 'pipeline'; }
function isCaseReplay() { return ui.pipeline?.mode === 'case_replay'; }
function selectedReplaySummary() { return ui.pipeline?.replay?.cases?.find((item) => item.case_id === ui.replayCaseId); }
function pipelineAlerts() { return isCaseReplay() ? ui.replayCase?.alerts || (ui.pipeline?.alerts || []).filter((item) => item.case_id === ui.replayCaseId) : ui.pipeline?.alerts || []; }
function utcDate(value) {
  if (!value) return '未声明';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : `${parsed.toISOString().replace('T',' ').replace('.000Z','')} UTC`;
}
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
  const acquisition = mode === 'acquisition';
  $('online-controls').hidden = history || acquisition;
  $('online-content').hidden = history || acquisition;
  $('history-controls').hidden = !history;
  $('history-content').hidden = !history;
  $('acquisition-controls').hidden = !acquisition;
  $('acquisition-content').hidden = !acquisition;
  $('acquisition-tab').classList.toggle('active', acquisition);
  $('acquisition-tab').setAttribute('aria-current',acquisition ? 'page' : 'false');
  $('online-tab').classList.toggle('active', !history && !acquisition);
  $('history-tab').classList.toggle('active', history);
  $('online-tab').setAttribute('aria-current', history || acquisition ? 'false' : 'page');
  $('history-tab').setAttribute('aria-current', history ? 'page' : 'false');
  $('content-kicker').textContent = history ? '历史案例 / HORMUZ' : '资料流';
  $('content-title').textContent = history ? '霍尔木兹海峡 · 民用航运宏观资料' : currentMonitor()?.topic || '从主题开始，持续整理公开资料';
  $('mode-badge').textContent = history ? '历史 · 结果快照' : currentRun()?.mode === 'replay' ? '原始材料回放' : '在线资料';
  ui.detail = null;
  if (history) loadHistory(); else if (acquisition) renderAcquisition(); else renderOnline();
  renderPipelineStatus();
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
  if (isPipeline()) { renderPipelineStatus(); return; }
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
  $('model-status').textContent = `${model.name || 'Qwen'} · 最近调用${model.last_call_at ? label(model.status) : '尚无记录'}`;
  $('model-status').title = model.last_error || (model.last_call_at ? `最近调用：${date(model.last_call_at)}` : '尚无模型调用记录');
  $('model-status').className = `status-item ${['failed','error','unavailable'].includes(model.status) ? 'bad' : ''}`;
  const selected = currentMonitor();
  const latest = state.runs.find((run) => String(run.monitor_id) === String(ui.monitorId));
  renderSourceStatus();
  $('last-success').textContent = selected?.last_success_at ? date(selected.last_success_at) : '尚无记录';
  const monitorSelect = $('monitor-select');
  monitorSelect.replaceChildren(new Option('新建主题', ''));
  state.monitors.filter((monitor) => !monitor.pipeline_owned).forEach((monitor) => monitorSelect.add(new Option(monitor.topic, monitor.id)));
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
  renderPipelineStatus();
}
async function refresh() {
  if (ui.refreshing || document.hidden) return;
  ui.refreshing = true;
  try {
    [ui.state, ui.pipeline] = await Promise.all([api('/api/state'), api('/api/pipeline')]);
    if (ui.monitorId && (!currentMonitor() || currentMonitor().pipeline_owned)) { ui.monitorId = ''; ui.runId = ''; }
    const manualMonitors = ui.state.monitors.filter((monitor) => !monitor.pipeline_owned);
    if (!ui.monitorId && !ui.formLoaded && manualMonitors.length) ui.monitorId = String(manualMonitors[0].id);
    renderState();
    if (ui.mode === 'acquisition') await loadAcquisition();
    else if (isPipeline()) await loadPipeline();
    else if (ui.mode === 'online' && ui.monitorId) await loadMaterials();
    else { ui.materials = []; ui.runs = []; ui.metrics = null; ui.alerts = []; if (ui.mode === 'online') renderOnline(); }
    remember();
  } catch (error) { notify(error.message); }
  finally { ui.refreshing = false; }
}
const acquisitionPolicyFields = [
  ['raw_hours','成功原件保留（小时）'],['anomaly_days','异常关联原件（天）'],['failed_days','失败原件等待（天）'],
  ['part_hours','停止任务残片（小时）'],['features_days','完整分窗特征（天）'],['logs_days','普通日志（天）'],
  ['quota_gib','受管原件与临时区额度（GiB）'],['high_gib','开始容量回收（GiB）'],['target_gib','容量回收目标（GiB）'],
];
function bytes(value) {
  if (!numeric(value)) return '未知';
  const units = ['B','KiB','MiB','GiB','TiB'];
  const index = value > 0 ? Math.min(4,Math.floor(Math.log(value)/Math.log(1024))) : 0;
  return `${number(value/1024**index)} ${units[index]}`;
}
function acquisitionCaseLabel(id) { return ui.pipeline?.replay?.cases?.find((item) => item.case_id === id)?.label || (String(id).includes('kharkiv') ? '哈尔科夫' : String(id).includes('hormuz') ? '霍尔木兹' : id); }
async function loadAcquisition() {
  const snapshot = await api('/api/acquisition');
  if (ui.mode !== 'acquisition') return;
  ui.acquisition = snapshot;
  renderAcquisition();
}
async function acquisitionAction(callback,message) {
  if (ui.acquisitionBusy) return;
  ui.acquisitionBusy = true; notify();
  document.querySelectorAll('[data-acquisition-action],#acquisition-save-subscription,#acquisition-save-policy,#acquisition-cleanup').forEach((button) => { button.disabled = true; });
  try { await callback(); notify(message || '请求已提交到服务器。'); if (ui.mode === 'acquisition') await loadAcquisition(); }
  catch (error) { notify(error.message); }
  finally { ui.acquisitionBusy = false; document.querySelectorAll('[data-acquisition-action],#acquisition-save-subscription,#acquisition-save-policy,#acquisition-cleanup').forEach((button) => { button.disabled = false; }); }
}
function acquisitionButton(text,path,body = {},callback) {
  const button = el('button','reference-button',text); button.type = 'button'; button.dataset.acquisitionAction = 'true'; button.disabled = ui.acquisitionBusy;
  button.addEventListener('click',() => acquisitionAction(async () => { await api(path,body); if (callback) await callback(); }));
  return button;
}
function fillAcquisitionSubscription(subscription) {
  ui.acquisitionSubscriptionId = subscription?.id || '';
  ui.acquisitionSubscriptionDirty = false;
  $('acquisition-subscription-select').value = ui.acquisitionSubscriptionId;
  $('acquisition-source').value = subscription?.source || 'cddis';
  $('acquisition-stations').value = (subscription?.stations || []).join('\n');
  $('acquisition-product').value = subscription?.product || 'daily';
  $('acquisition-start').value = subscription?.start_date || '';
  $('acquisition-end').value = subscription?.end_date || '';
  $('acquisition-hours').value = (subscription?.hours || []).join(', ');
  $('acquisition-interval').value = (subscription?.interval_seconds ?? ($('acquisition-product').value === 'daily' ? 3600 : 900))/60;
  $('acquisition-enabled').checked = Boolean(subscription?.enabled);
  $('acquisition-hours-field').hidden = $('acquisition-product').value !== 'highrate';
}
function acquisitionSubscriptionValues(subscription) {
  return Object.fromEntries(['id','source','product','stations','hours','start_date','end_date','enabled','interval_seconds'].filter((key) => subscription[key] !== undefined).map((key) => [key,subscription[key]]));
}
function renderAcquisition() {
  const snapshot = ui.acquisition;
  $('content-kicker').textContent = '服务器后台 / 数据获取与存储';
  $('content-title').textContent = '资源获取、计算与保留';
  $('mode-badge').textContent = '数据获取与存储';
  if (!snapshot) { $('acquisition-status').textContent = '正在读取服务器资源状态…'; return; }
  $('acquisition-status').textContent = `${snapshot.enabled ? '资源获取已启用' : '资源获取未启用'} · ${label(snapshot.status)}${snapshot.coordinator ? ` · 协调状态 ${typeof snapshot.coordinator === 'string' ? label(snapshot.coordinator) : coverageText(snapshot.coordinator)}` : ''}`;
  const counts = snapshot.counts || {};
  $('acquisition-queue-summary').textContent = [['queued','排队'],['downloading','下载'],['computing','计算'],['ready','可用结果'],['retry_wait','重试'],['waiting_source','等待来源'],['auth_required','认证缺口'],['source_missing','来源缺失'],['waiting_space','等待空间'],['failed','失败']].map(([key,title]) => `${title} ${counts[key] ?? '—'}`).join(' · ');
  const modelCounts = ui.pipeline?.counts;
  if (modelCounts) $('acquisition-queue-summary').textContent += `\n本应用分析：排队 ${modelCounts.queued ?? '—'} · 分析中 ${modelCounts.running ?? '—'} · 重试 ${modelCounts.retry_wait ?? '—'}`;
  const storage = snapshot.storage || {};
  $('acquisition-storage-summary').textContent = `受管原件 ${bytes(storage.managed_raw_bytes)} + 临时文件 ${bytes(storage.temp_bytes)} · 额度 ${bytes(storage.quota_bytes)} · 下载预留 ${bytes(storage.reserved_bytes)} · 磁盘可用 ${bytes(storage.free_disk_bytes)}`;
  const select = $('acquisition-subscription-select');
  if (document.activeElement !== select) {
    select.replaceChildren(new Option('新建订阅',''));
    (snapshot.subscriptions || []).forEach((item) => select.add(new Option(`${item.source.toUpperCase()} · ${(item.stations || []).join(', ')} · ${item.product === 'daily' ? '日文件' : '高频'}${item.enabled ? '' : ' · 暂停'}`,item.id)));
    select.value = ui.acquisitionSubscriptionId;
  }
  const subscription = (snapshot.subscriptions || []).find((item) => item.id === ui.acquisitionSubscriptionId);
  if (subscription && !ui.acquisitionSubscriptionDirty && !document.activeElement?.closest('#acquisition-subscription-form')) fillAcquisitionSubscription(subscription);
  const subStatus = $('acquisition-subscription-status'); subStatus.replaceChildren();
  if (subscription) {
    subStatus.append(el('p','',`${label(subscription.status)} · ${subscription.enabled ? '自动获取已启用' : '已暂停来源'}`),el('p','',`最近扫描 ${date(subscription.last_scan_at)} · 下次检查 ${date(subscription.next_check_at)}`),el('p','',`扫描位置 ${format(subscription.scan_cursor)}\n完整覆盖至 ${format(subscription.coverage_through)}`));
    if (subscription.error) subStatus.append(el('p','source-error',subscription.error));
    subStatus.append(acquisitionButton(subscription.enabled ? '暂停此订阅' : '恢复此订阅','/api/acquisition/subscriptions',acquisitionSubscriptionValues({...subscription,enabled:!subscription.enabled})));
  } else subStatus.append(el('p','',snapshot.subscriptions?.length ? '选择一个订阅查看来源状态，或填写站点新建。' : '尚无持续订阅。历史案例站点不会自动启用为长期订阅。'));
  const cases = $('acquisition-cases'); cases.replaceChildren();
  Object.entries(snapshot.cases_counts || {}).forEach(([id,item]) => {
    const block = el('section','acquisition-case');
    const heading = el('div','section-heading'); heading.append(el('h3','',acquisitionCaseLabel(id)),acquisitionButton('补取登记缺项',`/api/acquisition/cases/${encodeURIComponent(id)}/acquire`)); block.append(heading);
    const grid = el('div','acquisition-case-counts');
    [['required','GNSS 需求'],['original_reused','原件复用'],['result_reused','结果复用'],['downloaded','实际下载'],['computed','已计算'],['pending_download','待获取'],['pending_compute','待计算'],['pending','待处理合计'],['missing','来源缺口'],['failed','技术失败']].forEach(([key,title]) => { const stat = el('div'); stat.append(el('span','',title),el('strong','',item[key] ?? '—')); grid.append(stat); });
    block.append(grid);
    if (Array.isArray(item.portwatch_missing_dates)) block.append(el('p','small muted',`PortWatch 登记前置缺日：${item.portwatch_missing_dates.length ? item.portwatch_missing_dates.join('、') : '当前无登记缺日'}${item.portwatch_valid_reference_days != null ? ` · 已有有效参考 ${item.portwatch_valid_reference_days} 日` : ''}`));
    if (item.reason || item.error) block.append(el('p','source-error',item.reason || item.error));
    cases.append(block);
  });
  if (!Object.keys(snapshot.cases_counts || {}).length) cases.append(el('p','small muted','尚无已登记案例资源需求。'));
  else cases.append(el('p','micro muted','获取方式与计算状态分别统计；这些列不能相加作为下载总量。'));
  renderAcquisitionTasks();
  const storageView = $('acquisition-storage'); storageView.replaceChildren();
  [['managed_raw_bytes','受管压缩原件'],['temp_bytes','临时文件'],['features_bytes','完整分窗特征'],['database_bytes','数据库'],['evidence_bytes','长期证据'],['reports_bytes','报告'],['unmanaged_raw_bytes','未接管旧原件']].forEach(([key,title]) => { const stat = el('div'); stat.append(el('span','',title),el('strong','',bytes(storage[key]))); storageView.append(stat); });
  const paths = $('acquisition-paths'); paths.replaceChildren();
  facts([['服务器资产根',snapshot.paths?.asset_root],['运行根目录',snapshot.paths?.runtime_root],['共享台账',snapshot.paths?.catalog],['长期证据目录',snapshot.paths?.evidence],['数据日期范围',snapshot.observed_range || storage.observed_range || snapshot.date_range]],paths);
  if (!ui.acquisitionPolicyDirty && !document.activeElement?.closest('#acquisition-policy-form')) {
    const fields = $('acquisition-policy-fields'); fields.replaceChildren();
    acquisitionPolicyFields.forEach(([key,title]) => { const field = el('label','',title), input = el('input'); input.type = 'number'; input.min = '0.01'; input.step = 'any'; input.required = true; input.value = snapshot.policy?.[key] ?? ''; input.dataset.policyField = key; field.append(input); fields.append(field); });
    $('acquisition-autoclean').checked = Boolean(snapshot.policy?.autoclean);
  }
  const cleanup = snapshot.cleanup || {};
  $('acquisition-cleanup-status').replaceChildren(el('p','',`原件／临时区：每小时 · 上次 ${date(cleanup.last_at)} · 下次 ${date(cleanup.next_at)}`),el('p','',`完整特征／普通日志：每日 03:30（Asia/Shanghai） · 上次 ${date(cleanup.last_daily_at)} · 下次 ${date(cleanup.next_daily_at)}`),el('p','',`最近实际释放 ${bytes(cleanup.freed_bytes)} · 清理文件 ${cleanup.items ?? '—'} 个`));
  if (cleanup.error) $('acquisition-cleanup-status').append(el('p','source-error',cleanup.error));
  const records = $('acquisition-cleanup-records'); records.replaceChildren();
  (cleanup.recent_records || []).forEach((item) => { const row = el('div','cleanup-record'); row.append(el('strong','',item.filename || item.resource_id || '清理记录'),el('p','',`${date(item.at || item.cleaned_at)} · ${bytes(item.bytes)} · ${label(item.reason)}`)); if (item.error) row.append(el('p','source-error',item.error)); records.append(row); });
  if (!cleanup.recent_records?.length) records.append(el('p','small muted','尚无可展示的实际清理记录。'));
  if (!ui.detail) {
    $('detail-content').replaceChildren(badge('服务器持久资源状态','accent'),el('h2','detail-heading','查看获取与计算依据'),el('p','small muted','选择一条资源查看实际字节数、重试原因、计算版本与原件状态。维护操作提交到服务器，不依赖页面保持打开。'));
  }
}
function renderAcquisitionTasks() {
  const tasks = ui.acquisition?.recent_tasks || [], filter = $('acquisition-task-filter').value;
  const issueStates = ['retry_wait','waiting_source','auth_required','source_missing','waiting_space','failed'];
  const selected = tasks.filter((item) => filter === 'all' || filter === 'issues' && issueStates.includes(item.status) || filter === 'ready' && (item.compute_status === 'ready' || item.compute_status === 'completed' || item.status === 'ready') || filter === 'pending' && ['queued','downloading','computing','retry_wait','waiting_source','waiting_space'].includes(item.status));
  const signature = JSON.stringify([selected,filter,ui.acquisitionResourceId,ui.acquisitionBusy]);
  if (signature === ui.acquisitionTaskSignature) return;
  ui.acquisitionTaskSignature = signature;
  const parent = $('acquisition-tasks'); parent.replaceChildren();
  selected.forEach((item) => {
    const card = el('button',`material-card${item.id === ui.acquisitionResourceId ? ' active' : ''}`); card.type = 'button';
    const top = el('div','card-top'); top.append(el('span','publisher',[item.station,item.kind].filter(Boolean).join(' · ') || '资源'),badge(label(item.status),issueStates.includes(item.status) ? 'waiting' : ''));
    card.append(top,el('h3','',item.filename || item.id),el('p','card-excerpt',`获取：${label(item.acquisition_status || item.status)} · 计算：${label(item.compute_status)}\n实际接收 ${bytes(item.bytes)} / 总量 ${bytes(item.total_bytes)}${item.raw_deleted_at ? '\n原文件已清理，指标与报告保留。' : ''}`));
    if (numeric(item.total_bytes) && item.total_bytes > 0 && numeric(item.bytes)) { const progress = el('progress','resource-progress'); progress.max = item.total_bytes; progress.value = item.bytes; progress.setAttribute('aria-label','实际接收字节数'); card.append(progress); }
    card.append(el('p','alert-time',`观测 ${date(item.observed_start)} 至 ${date(item.observed_end)}\n尝试 ${item.attempts ?? '—'} 次 · 下次重试 ${date(item.next_retry_at)} · 更新 ${date(item.updated_at)}`));
    if (item.error) card.append(el('p','source-error',item.error));
    card.addEventListener('click',() => openAcquisitionResource(item.id).catch((error) => notify(error.message))); parent.append(card);
  });
  if (!selected.length) parent.append(el('p','small muted',tasks.length ? '最近任务中没有符合当前筛选的记录。' : '尚无已登记资源任务。'));
}
async function openAcquisitionResource(id) {
  ui.detail = 'acquisition-resource'; ui.acquisitionResourceId = id;
  const resource = await api(`/api/acquisition/resources/${encodeURIComponent(id)}`);
  if (ui.detail !== 'acquisition-resource' || ui.acquisitionResourceId !== id) return;
  const parent = $('detail-content'), input = resource.resource || {};
  parent.replaceChildren(badge('服务器资源与计算依据','accent'),el('h2','detail-heading',resource.filename || input.filename || id),el('p','detail-meta',id));
  if (resource.raw_deleted_at) section('原件状态').append(el('p','',`原文件已清理，指标与报告保留。清理于 ${date(resource.raw_deleted_at)}${resource.raw_delete_reason ? ` · ${label(resource.raw_delete_reason)}` : ''}`));
  facts([['来源',resource.source || input.source],['实际站点',resource.station || input.station],['产品',resource.product || input.product],['服务器位置',resource.path],['受管原件',resource.managed == null ? '未声明' : resource.managed ? '是' : '否'],['原件存在',resource.raw_present == null && resource.raw_exists == null ? '未声明' : resource.raw_present || resource.raw_exists ? '是' : '否'],['观测起点',date(resource.observed_start || input.observed_start)],['观测终点',date(resource.observed_end || input.observed_end)],['实际接收',bytes(resource.bytes)],['已知总量',bytes(resource.total_bytes)]],section('资源身份与存储'));
  facts([['获取状态',label(resource.acquisition_status || resource.status)],['计算状态',label(resource.compute_status)],['尝试次数',resource.attempts],['最近成功',date(resource.acquired_at || resource.last_success_at)],['下次重试',date(resource.next_retry_at)],['具体原因',resource.error],['处理版本',resource.result_version],['计算完成',date(resource.computed_at)]],section('获取与处理进度'));
  const sourceUrl = resource.source_url || input.source_url || resource.url || input.url;
  if (sourceUrl) link(sourceUrl,'实际登记来源',section('原始来源'));
  if (resource.result_summary) section('保留的实际观测摘要').append(el('pre','work-record',format(resource.result_summary)));
  if (resource.evidence_refs?.length) automaticReferences(resource.evidence_refs,section('已保存材料与报告依据'));
  if (resource.consumers?.length) { const block = section('消费者与续接位置'); resource.consumers.forEach((item) => block.append(el('p','small',`${item.case_id || item.subscription_id || item.instance_id || item.id || '消费者'} · 批次 ${item.batch_id || '—'} · ${label(item.status)}${item.as_of ? ` · 截止 ${utcDate(item.as_of)}` : ''}${item.input_version != null ? ` · 输入版本 ${item.input_version}` : ''}`))); }
  const actions = section('服务器维护任务');
  actions.append(acquisitionButton('重试此资源',`/api/acquisition/resources/${encodeURIComponent(id)}/retry`,{},() => openAcquisitionResource(id)),acquisitionButton('请求重新获取原件',`/api/acquisition/resources/${encodeURIComponent(id)}/fetch`,{},() => openAcquisitionResource(id)));
  const fold = el('details','asset-block'); fold.append(el('summary','','完整资源登记与保留依据'),el('pre','work-record',format(resource))); parent.append(fold);
  if (ui.mode === 'acquisition') renderAcquisitionTasks();
  document.querySelector('.details-panel').scrollTop = 0;
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
function renderPipelineStatus() {
  const pipeline = ui.pipeline;
  if (!pipeline) return;
  const paused = pipeline.paused || !pipeline.enabled;
  const replayMode = isCaseReplay(), replay = pipeline.replay;
  $('pipeline-control-title').textContent = replayMode ? '历史自动回放' : '持续监测';
  $('online-tab').textContent = replayMode ? '自动回放' : '自动监测';
  $('history-tab').textContent = replayMode ? '历史参考' : '历史案例';
  $('pipeline-form').hidden = replayMode;
  $('replay-controls').hidden = !replayMode;
  $('replay-top-status').hidden = !replayMode;
  document.body.classList.toggle('case-replay-mode', replayMode);
  $('pipeline-status').textContent = `流水线 · ${!pipeline.enabled ? '未启用' : pipeline.paused ? '已暂停' : label(pipeline.status || 'monitoring')}`;
  $('pipeline-status').className = `status-item ${paused ? '' : 'busy'}`;
  $('pipeline-enabled').textContent = !pipeline.enabled ? '未启用' : pipeline.paused ? '已暂停' : '已启用';
  $('pipeline-pause').textContent = pipeline.paused ? `恢复${replayMode ? '自动回放' : '自动监测'}` : `暂停${replayMode ? '自动回放' : '自动监测'}`;
  $('pipeline-pause').disabled = !pipeline.enabled || ui.saving;
  const scope = pipeline.scope;
  $('pipeline-scope').textContent = typeof scope === 'string' ? scope : scope?.name || scope?.label || '霍尔木兹海峡 · 民用航运公开资料';
  if (replayMode) {
    $('pipeline-scope').textContent = '哈尔科夫与霍尔木兹 · 历史观测顺序回放';
    const current = replay?.cases?.find((item) => item.case_id === replay.current_case_id);
    $('replay-top-status').textContent = `后台当前：${current?.label || replay?.current_case_id || '尚未开始'} · 截止 ${utcDate(current?.as_of || replay?.as_of)} · 本例 ${current?.completed_batches || 0}/${current?.total_batches || 0} 批 · ${label(replay?.status || current?.status || 'queued')}${current?.reason ? ` · ${current.reason}` : ''}`;
    renderReplayCases();
  }
  const counts = pipeline.counts || {};
  const backlog = (counts.queued || 0) + (counts.running || 0) + (counts.retry_wait || 0);
  $('pipeline-backlog').textContent = `待分析 ${backlog} · 重试 ${counts.retry_wait || 0}`;
  $('last-analysis').textContent = pipeline.last_analyzed_at ? date(pipeline.last_analyzed_at) : '尚无记录';
  if (isPipeline()) {
    $('mode-badge').textContent = replayMode ? '历史自动回放' : '自动监测';
    $('last-success').textContent = pipeline.last_collected_at ? date(pipeline.last_collected_at) : '尚无记录';
    const dated = (pipeline.sources || []).filter((source) => source.latest_observed_date);
    $('source-status').textContent = dated.length ? dated.map((source) => `${source.name || source.source} · 数据截至 ${date(source.latest_observed_date)}`).join(' / ') : '来源数据日期 · 尚无记录';
    $('source-status').title = $('source-status').textContent;
    if (replayMode) $('source-status').textContent = `回放实例 · ${replay?.instance_id || '尚未建立'}`;
  }
  if (replayMode) return;
  if (!ui.pipelineFormLoaded || !ui.pipelineFormDirty && document.activeElement?.closest('#pipeline-form') == null) {
    $('pipeline-news-topic').value = pipeline.config?.news_topic || '';
    $('pipeline-rss-terms').value = (pipeline.config?.rss_terms || []).join('\n');
    const parent = $('pipeline-sources');
    parent.replaceChildren();
    (pipeline.sources || []).forEach((source) => {
      const group = el('fieldset', 'pipeline-source');
      group.dataset.sourceId = source.id;
      group.append(el('legend', '', source.name || source.source));
      const enabledLabel = el('label', 'checkbox-label');
      const enabled = el('input'); enabled.type = 'checkbox'; enabled.checked = Boolean(source.enabled); enabled.dataset.field = 'enabled';
      enabledLabel.append(enabled, el('span', '', '自动检查'));
      const intervalLabel = el('label', '', '检查间隔（分钟）');
      const interval = el('input'); interval.type = 'number'; interval.min = '1'; interval.step = '1'; interval.value = Math.max(1, (source.interval_seconds || 300) / 60); interval.required = true; interval.dataset.field = 'interval';
      intervalLabel.append(interval);
      group.append(enabledLabel, intervalLabel);
      if (source.source === 'rss') group.append(el('p', 'micro muted', '仅覆盖已配置 feed 中实际返回的条目。'));
      parent.append(group);
    });
    ui.pipelineFormLoaded = true;
  }
}
async function loadPipeline() {
  let monitorId, requestedCaseId;
  if (isCaseReplay()) {
    const cases = ui.pipeline.replay?.cases || [];
    if (!cases.some((item) => item.case_id === ui.replayCaseId)) {
      ui.replayCaseId = ui.pipeline.replay?.current_case_id || cases[0]?.case_id || '';
      ui.replayCase = null; ui.replayCaseSignature = '';
    }
    const caseId = ui.replayCaseId;
    requestedCaseId = caseId;
    const signature = JSON.stringify([ui.pipeline.replay?.instance_id, selectedReplaySummary(), (ui.pipeline.alerts || []).filter((item) => item.case_id === caseId)]);
    if (caseId && signature !== ui.replayCaseSignature) {
      const detail = await api(`/api/replay/cases/${encodeURIComponent(caseId)}`);
      if (caseId !== ui.replayCaseId || !isPipeline()) return;
      ui.replayCase = detail; ui.replayCaseSignature = signature;
    }
    monitorId = ui.replayCase?.portwatch_monitor_id;
  } else monitorId = ui.pipeline?.sources?.find((item) => item.source === 'portwatch')?.monitor_id;
  const metrics = monitorId ? await api(`/api/monitors/${encodeURIComponent(monitorId)}/metrics`) : null;
  if (requestedCaseId !== undefined && requestedCaseId !== ui.replayCaseId) return;
  ui.metrics = metrics;
  ui.alerts = pipelineAlerts().filter((alert) => alert.origin_type === 'numeric_rule' || alert.rule);
  if (!isPipeline()) return;
  renderOnline();
  if (ui.detail === 'pipeline-alert' && ui.alertId) {
    const summary = pipelineAlerts().find((alert) => String(alert.id) === String(ui.alertId));
    const signature = summary ? JSON.stringify(summary) : '';
    if (signature && signature !== ui.pipelineDetailSignature) await openPipelineAlert(ui.alertId, false, signature);
  }
}
function coverageText(value) {
  if (!value) return '覆盖尚未声明';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(coverageText).join('；');
  return value.summary || value.note || value.detail || Object.entries(value).map(([key,item]) => `${key}：${typeof item === 'object' ? coverageText(item) : item}`).join('；');
}
function renderReplayCases() {
  const replay = ui.pipeline?.replay;
  const signature = JSON.stringify([replay?.cases,ui.replayCaseId,replay?.current_case_id]);
  if (signature === ui.replayListSignature) return;
  ui.replayListSignature = signature;
  const list = $('replay-case-list'); list.replaceChildren();
  (replay?.cases || []).forEach((item) => {
    const card = el('button',`replay-case${item.case_id === ui.replayCaseId ? ' active' : ''}`); card.type = 'button';
    card.setAttribute('aria-pressed',String(item.case_id === ui.replayCaseId));
    const top = el('div','card-top'); top.append(el('strong','',item.label || item.case_id),badge(label(item.status || 'queued'),item.status === 'blocked' ? 'waiting' : ''));
    card.append(top,el('p','',`已完成 ${item.completed_batches || 0}/${item.total_batches || 0} 批 · 已释放 ${item.released_batches || 0}`));
    const progress = el('progress'); progress.max = Math.max(1,item.total_batches || 0); progress.value = item.completed_batches || 0; progress.setAttribute('aria-label',`${item.label || item.case_id} 已完成批次`); card.append(progress);
    card.append(el('p','micro muted',coverageText(item.coverage)),el('p','micro muted',`观测资源 ${item.gnss_resources ?? '—'} · 已处理 ${item.processed_resources ?? '—'} · 排队 ${item.queued || 0} · 失败 ${item.failed || 0}`));
    if (item.resource_status || item.resource_counts) card.append(el('p','micro muted',`资源状态：${coverageText(item.resource_status || item.resource_counts)}`));
    if (item.baseline_status) card.append(el('p','micro muted',`基线准备：${typeof item.baseline_status === 'string' ? label(item.baseline_status) : coverageText(item.baseline_status)}`));
    if (item.input_version != null || item.current_input_version != null) card.append(el('p','micro muted',`当前批次输入版本 ${item.input_version ?? item.current_input_version}`));
    if (item.quality_status) card.append(el('p','micro muted',`结果质量：${label(item.quality_status)}`));
    if (item.reason) card.append(el('p','source-error',item.reason));
    if (item.case_id === replay.current_case_id) card.append(el('span','micro replay-current','后台当前案例'));
    card.addEventListener('click',async () => {
      ui.replayCaseId = item.case_id; localStorage.setItem('workspace-replay-case',item.case_id);
      ui.replayCase = null; ui.replayCaseSignature = ''; ui.gnssSignature = ''; ui.gnssSeriesKey = ''; ui.detail = null; ui.alertId = null; ui.metrics = null;
      renderPipeline();
      try { await loadPipeline(); } catch (error) { notify(error.message); }
    });
    list.append(card);
  });
  if (!replay?.cases?.length) list.append(el('p','small muted','尚无已配置案例；等待案例清单。'));
}
function renderGnss() {
  const detail = ui.replayCase;
  const allRows = detail?.gnss?.series || [];
  const rows = allRows.filter((row) => (row.unit || detail?.gnss?.units) === 'dB-Hz').map((row) => ({...row,time:row.time || row.bin_start || row.window_start,p10:row.p10 ?? row.cnr_p10,delta_db:row.delta_db ?? row.delta_p10,valid_samples:row.valid_samples ?? row.valid_count}));
  const groups = new Map();
  rows.forEach((row) => {
    const key = `${row.station || '未声明站点'} / ${row.signal || '未声明信号'}`;
    if (!groups.has(key)) groups.set(key,[]);
    groups.get(key).push(row);
  });
  if (!groups.has(ui.gnssSeriesKey)) ui.gnssSeriesKey = groups.keys().next().value || '';
  const signature = JSON.stringify([detail?.case_id,detail?.as_of,detail?.gnss,detail?.coverage,ui.gnssSeriesKey]);
  if (signature === ui.gnssSignature) return;
  ui.gnssSignature = signature;
  const otherUnits = [...new Set(allRows.filter((row) => (row.unit || detail?.gnss?.units) !== 'dB-Hz').map((row) => row.unit || row.value_kind || '单位未声明'))];
  $('gnss-coverage').textContent = `${coverageText(detail?.coverage || selectedReplaySummary()?.coverage)}${detail?.as_of ? ` · 仅展示截至 ${utcDate(detail.as_of)} 已释放的观测。` : ''}${otherUnits.length ? ` 另有 ${otherUnits.join('、')} 观测，原始统计保留在材料中，不作为载噪比曲线。` : ''}`;
  const select = $('gnss-series-select'); select.replaceChildren();
  groups.forEach((_,key) => select.add(new Option(key,key)));
  if (!groups.size) select.add(new Option('尚无已处理 GNSS 窗口',''));
  select.value = ui.gnssSeriesKey; select.disabled = !groups.size;
  const data = [...(groups.get(ui.gnssSeriesKey) || [])].sort((a,b) => Date.parse(a.time) - Date.parse(b.time));
  const latest = data.at(-1), summary = $('gnss-summary'); summary.replaceChildren();
  [['当前窗口 CNR p10',latest ? `${number(latest.p10)} dB-Hz` : '尚无观测'],['可比历史 p10',numeric(latest?.baseline_p10) ? `${number(latest.baseline_p10)} dB-Hz` : '参考不足'],['当前 − 历史',numeric(latest?.delta_db) ? `${number(latest.delta_db)} dB` : '无法比较']].forEach(([title,value]) => { const block = el('div'); block.append(el('span','',title),el('strong','',value)); summary.append(block); });
  const intervals = [...new Set(data.map((row) => row.interval_seconds).filter(numeric))];
  $('gnss-chart-note').textContent = `${ui.gnssSeriesKey || '实际站点 / 信号待处理'} · 原始采样 ${intervals.length ? intervals.map((item) => `${number(item)} 秒`).join(' / ') : '未声明'}。曲线点为实际分窗统计；缺口断开，无可比参考时不画参考线。描述统计与 Qwen 线索分别保存。`;
  const parent = $('gnss-chart'); parent.replaceChildren();
  const legend = $('gnss-chart-legend'); legend.replaceChildren();
  const lines = [['p10','#087f76','实际窗口 CNR p10'],...(data.some((row) => numeric(row.baseline_p10)) ? [['baseline_p10','#71858c','同站同信号可比历史 p10']] : [])];
  lines.forEach(([,color,name]) => { const item = el('span','legend-item'), line = el('span','legend-line'); line.style.background = color; item.append(line,el('span','',name)); legend.append(item); });
  const plottable = data.filter((row) => Number.isFinite(Date.parse(row.time)));
  if (!plottable.some((row) => numeric(row.p10))) parent.append(el('div','chart-empty',detail?.reason || '尚无有效 GNSS 数值；实际计算完成后自动显示。'));
  else {
    const width = 640, height = 245, left = 45, right = 18, top = 15, bottom = 43;
    const chart = svgNode('svg',{viewBox:`0 0 ${width} ${height}`,class:'chart-svg gnss-chart',role:'img','aria-label':`${ui.gnssSeriesKey} CNR p10 与实际历史参考，dB-Hz，UTC；缺口断开。`});
    const values = plottable.flatMap((row) => lines.map(([key]) => row[key]).filter(numeric));
    const low = Math.floor(Math.min(...values)-1), high = Math.ceil(Math.max(...values)+1);
    const start = Date.parse(plottable[0].time), end = Date.parse(plottable.at(-1).time);
    const x = (row) => left + (end === start ? .5 : (Date.parse(row.time)-start)/(end-start))*(width-left-right);
    const y = (value) => height-bottom-(value-low)/(high-low)*(height-top-bottom);
    const gaps = plottable.slice(1).map((row,index) => Date.parse(row.time)-Date.parse(plottable[index].time)).filter((value) => value > 0);
    const cadence = Math.min(86400000,...gaps);
    for (let tick = 0; tick <= 4; tick++) {
      const value = low+(high-low)*tick/4;
      chart.append(svgNode('line',{x1:left,y1:y(value),x2:width-right,y2:y(value),class:'grid'}),svgNode('text',{x:left-7,y:y(value)+3,'text-anchor':'end'},number(value)));
    }
    [...lines].reverse().forEach(([key,color]) => {
      let path = '', previous = null;
      plottable.forEach((row) => {
        if (!numeric(row[key])) { previous = null; return; }
        const previousEnd = previous?.window_end || previous?.end;
        const currentStart = row.window_start || row.start || row.time;
        const contiguous = previous && !previous.gap && !row.gap && (previousEnd ? Date.parse(currentStart) <= Date.parse(previousEnd) + 1000 : Date.parse(row.time)-Date.parse(previous.time) <= cadence * 1.5);
        path += `${contiguous ? 'L' : 'M'}${x(row)},${y(row[key])} `;
        const point = svgNode('circle',{cx:x(row),cy:y(row[key]),r:key === 'p10' ? 2.4 : 1.8,fill:color});
        point.append(svgNode('title',{},`${utcDate(row.time)} · ${key === 'p10' ? '当前' : '历史参考'} ${number(row[key])} dB-Hz · 原始采样 ${number(row.interval_seconds)} 秒 · 有效样本 ${row.valid_samples ?? '未声明'}`));
        chart.append(point); previous = row;
      });
      chart.append(svgNode('path',{d:path,fill:'none',stroke:color,'stroke-width':key === 'p10' ? 2.2 : 1.4,...(key === 'p10' ? {} : {'stroke-dasharray':'5 4'})}));
    });
    const tickIndices = [...new Set([0,Math.floor((plottable.length-1)/2),plottable.length-1])];
    tickIndices.forEach((index) => {
      const row = plottable[index], stamp = new Date(row.time).toISOString();
      const text = svgNode('text',{x:x(row),y:height-22,'text-anchor':index === 0 ? 'start' : index === plottable.length-1 ? 'end' : 'middle'},stamp.slice(0,10));
      text.append(svgNode('tspan',{x:x(row),dy:13},`${stamp.slice(11,16)} UTC`)); chart.append(text);
    });
    parent.append(chart);
  }
  const tableParent = $('gnss-observations'); tableParent.replaceChildren();
  if (!data.length) tableParent.append(el('p','small muted','本案例尚无已释放并完成计算的 GNSS 窗口。'));
  else {
    const table = el('table','daily-table'), head = el('thead'), tr = el('tr'), body = el('tbody');
    ['窗口 UTC','p10 / 参考','有效样本 / 比例','依据'].forEach((title) => tr.append(el('th','',title))); head.append(tr);
    [...data].reverse().forEach((row) => {
      const line = el('tr'), window = el('td'), source = el('td');
      const button = el('button','text-button',utcDate(row.time)); button.type = 'button'; button.addEventListener('click',() => renderGnssDetail(row)); window.append(button);
      appendMaterialLink(row,source);
      line.append(window,el('td','',`${number(row.p10)} / ${number(row.baseline_p10)}`),el('td','',`${row.valid_samples ?? '未声明'} / ${numeric(row.valid_ratio) ? `${number(row.valid_ratio*100)}%` : '未声明'}`),source); body.append(line);
    });
    table.append(head,body); tableParent.append(table);
  }
}
function renderGnssDetail(row) {
  ui.detail = 'gnss-observation';
  $('detail-content').replaceChildren(badge('GNSS · 本次实际统计','accent'),el('h2','detail-heading',`${row.station} · ${row.signal}`));
  facts([['案例',ui.replayCase?.label || ui.replayCaseId],['观测窗口',`${utcDate(row.window_start || row.start || row.time)} 至 ${utcDate(row.window_end || row.end || row.time)}`],['回放截止',utcDate(ui.replayCase?.as_of)],['原始采样',numeric(row.interval_seconds) ? `${number(row.interval_seconds)} 秒` : '未声明'],['原时间系统',row.time_system],['CNR p10',`${number(row.p10)} dB-Hz`],['可比历史 p10',numeric(row.baseline_p10) ? `${number(row.baseline_p10)} dB-Hz` : '参考不足'],['当前 − 历史',numeric(row.delta_db) ? `${number(row.delta_db)} dB` : '无法比较'],['有效样本',row.valid_samples],['历元数',row.epoch_count],['缺失历元',row.missing_epoch_count],['有效比例',numeric(row.valid_ratio) ? `${number(row.valid_ratio*100)}%` : '未声明'],['处理版本',row.processing_version]],section('观测与计算口径'));
  const evidence = section('原始材料与基线依据'); appendMaterialLink(row,evidence); appendResourceLinks(row,evidence);
  if (row.baseline_resource_ids?.length) { evidence.append(el('p','small muted','实际历史基线资源')); appendResourceLinks({resource_ids:row.baseline_resource_ids},evidence); }
  automaticReferences(row.baseline_refs || [],evidence);
  if (row.issues || row.limitations || row.time_note) section('质量与时间说明').append(el('p','',coverageText(row.issues || row.limitations || row.time_note)));
  document.querySelector('.details-panel').scrollTop = 0;
}
function appendResourceLinks(value,parent) {
  const resources = value.resource_ids || (value.resource_id ? [value.resource_id] : []);
  resources.forEach((entry) => {
    const id = typeof entry === 'string' ? entry : entry.id || entry.resource_id;
    if (!id) return;
    const button = el('button','reference-button',`原始资源 ${id}`); button.type = 'button';
    button.addEventListener('click',async () => {
      try {
        const resource = await api(`/api/replay/resources/${encodeURIComponent(id)}`);
        ui.detail = 'replay-resource';
        $('detail-content').replaceChildren(badge('已注册历史资源','accent'),el('h2','detail-heading',resource.filename || resource.name || id));
        facts([['资源 ID',resource.id || id],['案例',resource.case_id],['实际站点',resource.station],['观测起点',resource.observed_start || resource.start],['观测终点',resource.observed_end || resource.end],['原始采样（秒）',resource.interval_seconds],['来源发布时间',resource.available_at],['模拟释放',resource.replay_release_at],['处理版本',resource.processing_version]],section('原始输入登记'));
        if (resource.source || resource.url) { const source = section('来源引用'); source.append(el('p','',resource.source || '')); link(resource.url,'原始来源',source); }
        if (resource.time_note || resource.coverage) section('覆盖与时间假设').append(el('p','',coverageText(resource.time_note || resource.coverage)));
        if (resource.raw_deleted_at) section('原件状态').append(el('p','',`原文件已清理，指标与报告保留。清理时间 ${date(resource.raw_deleted_at)}。`));
        if (resource.catalog_id) { const button = el('button','full subtle','查看服务器存储、保留结果与重取入口'); button.type = 'button'; button.addEventListener('click',() => openAcquisitionResource(resource.catalog_id).catch((error) => notify(error.message))); $('detail-content').append(button); }
        const fold = el('details','asset-block'); fold.append(el('summary','','登记的完整资源元数据'),el('pre','work-record',format(resource))); $('detail-content').append(fold);
        document.querySelector('.details-panel').scrollTop = 0;
      } catch (error) { notify(error.message); }
    });
    parent.append(button);
  });
}
function renderPipeline() {
  const pipeline = ui.pipeline;
  const replayMode = isCaseReplay(), replayCase = ui.replayCase || selectedReplaySummary();
  $('content-kicker').textContent = replayMode ? '历史自动回放 / 已释放观测' : '持续监测 / 自动更新';
  $('content-title').textContent = replayMode ? `${replayCase?.label || ui.replayCaseId || '双案例'} · 本次观测与异常` : '霍尔木兹海峡 · 公开资料异常动态';
  renderPipelineStatus();
  $('portwatch-content').hidden = replayMode ? !ui.replayCase?.portwatch_monitor_id : !(pipeline?.sources || []).some((source) => source.source === 'portwatch');
  $('pipeline-pw-heading').hidden = $('portwatch-content').hidden;
  $('gnss-content').hidden = !replayMode;
  if (replayMode) renderGnss();
  $('pw-alert-heading').hidden = true;
  $('pw-alert-list').hidden = true;
  if (!$('portwatch-content').hidden) renderPortWatch();
  const counts = pipeline?.counts || {};
  $('pipeline-summary').textContent = !pipeline ? '正在读取监测进度…' : !pipeline.enabled ? '自动流水线未启用。' : pipeline.paused ? '自动监测已暂停；已保存的资料和结果继续可查看。' : pipeline.summary || '持续监测中，本轮未发布新异常';
  if (replayMode && pipeline?.enabled) $('pipeline-summary').textContent = `${pipeline.paused ? '自动回放已暂停。' : ''}当前查看 ${replayCase?.label || ui.replayCaseId || '待配置案例'}：${label(replayCase?.status || 'queued')}；已释放 ${replayCase?.released_batches || 0}/${replayCase?.total_batches || 0} 批，已完成 ${replayCase?.completed_batches || 0} 批。${replayCase?.reason || ''}${replayCase?.as_of ? ` 本例可见截止 ${utcDate(replayCase.as_of)}。` : ''}`;
  if (replayMode && (replayCase?.input_version != null || replayCase?.current_input_version != null)) $('pipeline-summary').textContent += ` 当前批次输入版本 ${replayCase.input_version ?? replayCase.current_input_version}；补到的资料沿原批次截止时间更新依据。`;
  $('pipeline-queue-info').textContent = `排队 ${counts.queued || 0} · 分析中 ${counts.running || 0} · 等待自动重试 ${counts.retry_wait || 0} · 已处理 ${counts.completed || 0}${pipeline?.model_retry_at ? ` · 模型下次重试 ${date(pipeline.model_retry_at)}` : ''}${pipeline?.last_published_at ? ` · 最近发布 ${date(pipeline.last_published_at)}` : ''}`;
  const sources = $('pipeline-source-dates'); sources.replaceChildren();
  (replayMode ? [] : pipeline?.sources || []).forEach((source) => {
    const row = el('div', 'pipeline-source-status');
    row.append(el('strong', '', source.name || source.source), badge(source.enabled ? label(source.status) : '已停用', source.error ? 'waiting' : ''), el('p', '', `数据截至 ${source.latest_observed_date ? date(source.latest_observed_date) : '未知'} · 最近检查 ${date(source.last_checked_at)}`));
    row.append(el('p', '', `最近成功获取 ${date(source.last_success_at)} · 下次检查 ${date(source.next_check_at)}`));
    if (source.coverage) row.append(el('p', '', typeof source.coverage === 'string' ? source.coverage : source.coverage.summary || source.coverage.detail || format(source.coverage)));
    if (source.coverage_gaps?.length) row.append(el('p', 'source-error', `累计未完整覆盖 ${source.coverage_gaps.length} 个时间窗口；最近记录：${source.coverage_gaps.slice(-3).map((gap) => typeof gap === 'string' ? gap : `${date(gap.start || gap.range_start || gap.start_at)} 至 ${date(gap.end || gap.range_end || gap.end_at)}`).join('；')}`));
    if (source.error) row.append(el('p', 'source-error', `来源检查：${source.error}`));
    sources.append(row);
  });
  const all = pipelineAlerts(), filter = $('pipeline-alert-filter').value;
  const alerts = all.filter((alert) => filter === 'all' || (filter === 'unread' ? !alert.read_at : filter === 'ended' ? ['resolved','revoked'].includes(alert.status) : !['resolved','revoked'].includes(alert.status)));
  $('pipeline-alert-count').textContent = all.length;
  const signature = JSON.stringify([alerts,ui.alertId,filter,pipeline?.paused,counts,replayMode,ui.replayCaseId]);
  if (signature !== ui.pipelineListSignature) {
    ui.pipelineListSignature = signature;
    const list = $('pipeline-alert-list'); list.replaceChildren();
    if (!alerts.length) {
      const empty = el('div', 'empty-state compact-empty');
      empty.append(el('h3', '', filter === 'all' ? '本轮未发布新异常' : '暂无符合筛选的动态'), el('p', '', (counts.queued || counts.running || counts.retry_wait) ? '仍有资料待分析或等待重试，处理进度见上方。' : replayMode ? '以本例处理状态和实际覆盖解释当前结果；参考不足或缺测不表示观测正常。' : pipeline?.paused || !pipeline?.enabled ? '已保存的异常会保留；启用后继续自动处理新资料。' : '后台继续检查来源。未发布异常不代表资料覆盖之外没有变化。'));
      list.append(empty);
    }
    alerts.forEach((alert) => {
      const card = el('button', `material-card alert-card${String(alert.id) === String(ui.alertId) ? ' active' : ''}`); card.type = 'button';
      const top = el('div', 'card-top');
      top.append(el('span', 'publisher', label(alert.origin_type || (alert.rule ? 'numeric_rule' : 'news_clue'))), badge(label(alert.display_status || alert.status), ['resolved','revoked'].includes(alert.status) ? '' : 'waiting'));
      const meta = el('div','card-meta');
      meta.append(el('span','',alert.read_at ? '已读' : '未读'),el('span','',`Qwen · ${label(alert.analysis_status || 'queued')}`));
      card.append(top, el('h3','',alert.title || (alert.origin_type === 'gnss_observation' ? 'GNSS 观测质量变化线索' : 'PortWatch 可见通行量持续偏低提醒')), el('p','card-excerpt',(!alert.analysis_stale && alert.analysis?.statement) || alert.statement || alert.summary || (alert.origin_type === 'numeric_rule' ? '程序规则已触发，Qwen 待分析。' : alert.origin_type === 'gnss_observation' ? '观测统计已保存，等待自动分析。' : '查看公开报道与分析依据。')), meta, el('p','alert-time',`所指数据 / 报道时间 ${date(alert.observed_at || alert.latest_observed_date || alert.started_on || alert.available_at)}\n系统发现 ${date(alert.detected_at)} · 发布 ${date(alert.published_at)}${alert.as_of ? `\n回放截止 ${utcDate(alert.as_of)}` : ''}`));
      card.addEventListener('click', () => openPipelineAlert(alert.id, true, JSON.stringify(alert)).catch((error) => notify(error.message)));
      list.append(card);
    });
  }
  const recent = $('pipeline-recent-runs'); recent.replaceChildren();
  const works = (replayMode && ui.replayCase?.recent_work ? ui.replayCase.recent_work : pipeline?.recent_work || []).filter((work) => !replayMode || work.case_id === ui.replayCaseId);
  works.forEach((work) => {
    const button = el('button','summary-preview',`${work.kind === 'portwatch' ? '数值证据分析' : work.kind === 'gnss' ? 'GNSS 观测分析' : '公开报道分析'} · ${label(work.decision || work.status)}\n${work.statement || work.error || `入队 ${date(work.created_at)}${work.next_retry_at ? ` · 下次重试 ${date(work.next_retry_at)}` : ''}`}`); button.type = 'button';
    button.addEventListener('click',async () => {
      ui.detail = 'pipeline-work'; ui.pipelineWorkId = work.id;
      try {
        const result = await api(`/api/work/${encodeURIComponent(work.id)}`);
        if (ui.detail !== 'pipeline-work' || ui.pipelineWorkId !== work.id) return;
        $('detail-content').replaceChildren(badge('自动分析工作','accent'),el('h2','detail-heading',label(work.decision || work.status)));
        facts([['工作 ID',result.id],['状态',label(result.status)],['入队',date(result.created_at)],['分析完成',date(result.completed_at)],['尝试次数',result.attempts],['下次重试',date(result.next_retry_at)]],section('处理记录'));
        if (result.case_id) facts([['案例',result.case_id],['回放截止',utcDate(result.as_of)],['批次',result.batch_id],['输入版本',result.input_version ?? result.batch_input_version]],section('本批历史范围'));
        section('本批结果').append(el('p','',work.statement || result.error || label(result.status)));
        appendWorkExecution(result,$('detail-content'));
        const fold = el('details','asset-block'); fold.append(el('summary','','完整输入、结论与补读记录')); const raw = el('pre','work-record'); raw.textContent = format(result); fold.append(raw); $('detail-content').append(fold);
        document.querySelector('.details-panel').scrollTop = 0;
      } catch (error) { notify(error.message); }
    });
    recent.append(button);
  });
  const runs = works.length || replayMode ? [] : (ui.state?.runs || []).filter((run) => run.mode === 'pipeline_analysis' || run.mode === 'pipeline_collection').slice(0, 8);
  if (!runs.length && !works.length) recent.append(el('p','small muted','尚无自动处理记录。'));
  runs.forEach((run) => {
    const button = el('button','summary-preview',`${run.mode === 'pipeline_analysis' ? '自动分析' : '来源检查'} · ${label(run.stage || run.status)} · ${date(run.started_at)}\n${run.error || (run.summary ? format(run.summary) : '处理进行中')}`); button.type = 'button';
    button.addEventListener('click',async () => {
      ui.detail = 'pipeline-run'; ui.pipelineRunId = run.id;
      try {
        const result = await api(`/api/runs/${encodeURIComponent(run.id)}`);
        if (ui.detail !== 'pipeline-run' || ui.pipelineRunId !== run.id) return;
        $('detail-content').replaceChildren(badge('自动处理记录','accent'),el('h2','detail-heading',run.mode === 'pipeline_analysis' ? '本批 Qwen 自动分析' : '本次来源检查'));
        facts([['状态',label(result.status)],['阶段',label(result.stage)],['开始',date(result.started_at)],['结束',date(result.finished_at)]],section('处理时间'));
        section('处理结果').append(el('p','',result.error || format(result.summary)));
        renderSteps(result,section('处理步骤'));
        const fold = el('details','asset-block'); fold.append(el('summary','','查看完整输入、输出与工具结果')); const raw = el('pre','work-record'); raw.textContent = format(result); fold.append(raw); $('detail-content').append(fold);
        document.querySelector('.details-panel').scrollTop = 0;
      } catch (error) { notify(error.message); }
    });
    recent.append(button);
  });
  if (!ui.detail) emptyDetail();
}
async function openPipelineAlert(id, scroll = true, signature = '') {
  ui.alertId = id; ui.detail = 'pipeline-alert';
  const alert = await api(`/api/alerts/${encodeURIComponent(id)}`);
  if (ui.detail !== 'pipeline-alert' || String(ui.alertId) !== String(id) || !isPipeline()) return;
  ui.pipelineAlert = alert; ui.pipelineDetailSignature = signature;
  const panel = document.querySelector('.details-panel'), previousScroll = panel.scrollTop;
  renderPipelineAlert(alert);
  panel.scrollTop = scroll ? 0 : previousScroll;
  renderPipeline();
}
function automaticReferences(refs, parent) {
  (refs || []).forEach((ref) => {
    const entry = typeof ref === 'string' ? {id:ref} : ref;
    const block = el('div', 'analysis-reference');
    if (entry.title) block.append(el('p','small',entry.title));
    appendMaterialLink({material_id:entry.material_id || entry.id,material_version:entry.material_version || entry.version,url:entry.url},block);
    appendResourceLinks(entry,block);
    if (entry.observed_at || entry.available_at || entry.first_seen_at) block.append(el('p','micro muted',`观测 ${date(entry.observed_at)} · 发布 ${date(entry.available_at)} · 首次获取 ${date(entry.first_seen_at)}`));
    parent.append(block);
  });
}
function appendWorkExecution(work,parent) {
  const block = section('实际模型与工具记录',parent);
  const attempts = work.attempts_detail || [];
  facts([['模型',work.assessment?.model || work.result?.model],['该次模型请求记录',attempts.length],['专业补查记录',(work.tool_results || []).length]],block);
  const tools = work.tool_results || [];
  if (!tools.length) block.append(el('p','small muted','该工作尚无已保存的实际补查调用记录。'));
  tools.forEach((tool,index) => {
    const fold = el('details','asset-block');
    fold.append(el('summary','',`${index+1}. ${tool.name} · 实际调用与返回`),el('pre','work-record',format({arguments:tool.arguments,result:tool.result}))); block.append(fold);
  });
  if (attempts.length) {
    const fold = el('details','asset-block');
    fold.append(el('summary','','模型请求、响应与耗时'),el('pre','work-record',format(attempts))); block.append(fold);
  }
}
function renderAutomaticAnalysis(alert) {
  const analysis = alert.analysis || {};
  const stale = Boolean(alert.analysis_stale || alert.analysis_status === 'stale');
  const block = section('Qwen 自动分析');
  block.append(el('p','small muted',`分析状态：${label(alert.analysis_status || 'queued')}`));
  const statement = analysis.statement || analysis.text;
  if (statement && !stale) block.append(el('p','',statement));
  else block.append(el('p','',alert.analysis_status === 'insufficient_evidence' ? '当前资料不足以形成有效判断；处理记录已保存，后续新资料将自动进入分析。' : alert.origin_type === 'numeric_rule' ? '程序规则状态已保存，Qwen 待自动分析。' : '资料已进入自动分析流程。'));
  if (statement && stale) { const fold = el('details','asset-block'); fold.dataset.fold = 'stale-analysis'; fold.append(el('summary','','旧证据的 Qwen 结论（待自动更新）'),el('p','stale-explanation',statement)); block.append(fold); }
  const limitations = Array.isArray(analysis.limitations) ? analysis.limitations : analysis.limitations ? [analysis.limitations] : [];
  limitations.forEach((item) => block.append(el('p','small muted',item)));
  if (alert.analysis_error || analysis.error) block.append(el('p','source-error',alert.analysis_error || analysis.error));
  facts([['模型',analysis.model],['该次分析完成',date(analysis.completed_at)],['分析配置版本',analysis.analysis_version],['该次证据版本',analysis.evidence_version || (stale ? '旧证据，见版本变化' : alert.evidence_version)]],block);
  automaticReferences(alert.evidence_refs?.length ? alert.evidence_refs : analysis.evidence_refs,section(stale ? '旧结论引用（本次分析尚未完成）' : '分析引用与来源版本'));
  const workId = alert.queued_work_id || analysis.work_id || alert.work_id || alert.analysis_work_id;
  if (workId) {
    const fold = el('details','asset-block'); fold.dataset.fold = `work-${workId}`;
    fold.append(el('summary','','展开本批输入、输出与补读记录'));
    fold.addEventListener('toggle', async () => {
      if (!fold.open || fold.dataset.loaded) return;
      fold.dataset.loaded = 'loading';
      try {
        const work = await api(`/api/work/${encodeURIComponent(workId)}`);
        facts([['工作 ID',work.id],['状态',label(work.status)],['入队时间',date(work.created_at)],['尝试次数',work.attempts ?? work.attempt_count],['下次重试',date(work.next_retry_at)],['失败原因',work.error]],fold);
        if (work.case_id) facts([['案例',work.case_id],['回放截止',utcDate(work.as_of)],['批次',work.batch_id],['输入版本',work.input_version ?? work.batch_input_version]],fold);
        appendWorkExecution(work,fold);
        const records = el('pre','work-record'); records.textContent = format(work); fold.append(records); fold.dataset.loaded = 'true';
      } catch (error) { delete fold.dataset.loaded; fold.append(el('p','source-error',error.message)); }
    });
    block.append(fold);
  }
}
function renderPipelineAlert(alert) {
  const parent = $('detail-content');
  const gnss = alert.origin_type === 'gnss_observation';
  if (alert.origin_type === 'numeric_rule' || alert.rule && !gnss) renderAlert(alert);
  else {
    parent.replaceChildren(badge(gnss ? 'GNSS 观测线索' : '公开报道线索','accent'),el('h2','detail-heading',alert.title || (gnss ? 'GNSS 观测质量变化' : '公开报道线索')),el('p','detail-meta',`${label(alert.display_status || alert.status)} · ${alert.id}`));
    const source = section(gnss ? '观测依据与待核实线索' : '资料支持的陈述');
    source.append(el('p','',alert.analysis_stale ? '来源已有新版本，当前结论待自动更新。' : alert.statement || alert.summary || alert.analysis?.statement || '查看下方本次分析与原始资料。'));
    if (gnss) source.append(el('p','small muted','数值来自实际 GNSS 处理；Qwen 描述观测变化及其证据范围。参考不足、缺测和补查失败分别保留。'));
    if (alert.origin === 'initialization' || alert.initialization) source.append(el('p','small muted','初始化回填中发现的线索，所指报道时间与系统发现时间分别列出。'));
    renderAutomaticAnalysis(alert);
    if (alert.changes?.length || alert.previous_analysis || alert.analysis_history?.length) {
      const fold = el('details','asset-block'); fold.dataset.fold = 'analysis-history'; fold.append(el('summary','','既有结论与修订依据'));
      const prior = el('pre','work-record'); prior.textContent = format({changes:alert.changes,previous_analysis:alert.previous_analysis,analysis_history:alert.analysis_history}); fold.append(prior); section('版本变化').append(fold);
    }
  }
  if (alert.case_id || alert.mode === 'case_replay') facts([['运行模式','历史自动回放'],['案例',alert.case_id],['本次分析回放截止',utcDate(alert.as_of)],['模拟释放时间',utcDate(alert.replay_release_at)],['时间假设',alert.time_note]],section('本次历史范围'));
  facts([['业务状态',label(alert.status)],['所指观测日期',date(alert.observed_at || alert.started_on)],['来源发布时间',date(alert.available_at)],['系统首次获取',date(alert.first_seen_at)],['系统发现',date(alert.detected_at)],['异常发布',date(alert.published_at)],['最近变化',date(alert.updated_at)],['已读时间',date(alert.read_at)]],section('资料与系统时间'));
  const read = el('button','full subtle',alert.read_at ? '已读' : '标为已读'); read.type = 'button'; read.disabled = Boolean(alert.read_at);
  read.addEventListener('click',async () => {
    try { await api(`/api/alerts/${encodeURIComponent(alert.id)}/read`,{read:true}); ui.pipelineDetailSignature = ''; await refresh(); }
    catch (error) { notify(error.message); }
  });
  parent.append(read);
}
function renderOnline() {
  $('pipeline-content').hidden = !isPipeline();
  $('maintenance-content').hidden = isPipeline();
  $('pipeline-view').hidden = isPipeline();
  $('gnss-content').hidden = !isPipeline() || !isCaseReplay();
  if (isPipeline()) { renderPipeline(); return; }
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
  $('pw-alert-heading').hidden = false;
  $('pw-alert-list').hidden = false;
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
  empty.append(el('span', 'empty-symbol', '≡'), el('h3', '', isPipeline() ? '查看异常依据' : '查看资料细节'), el('p', '', isPipeline() ? '选择一条动态，查看程序规则、Qwen 结论、资料版本和原始链接。' : '选择一条资料，查看模型摘要、原始摘录与引用来源。'));
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
  const parsed = new URL(url,window.location.origin);
  if (!['http:','https:'].includes(parsed.protocol)) return;
  const anchor = el('a', '', title || url);
  anchor.href = parsed.href;
  anchor.target = '_blank';
  anchor.rel = 'noopener noreferrer';
  parent.append(anchor);
}
function renderMaterial(material) {
  const parent = $('detail-content');
  parent.replaceChildren(badge(material.mode === 'case_replay' ? '历史自动回放 · 实际材料' : material.mode === 'replay' ? '原始材料回放' : '在线资料', 'accent'), el('h2', 'detail-heading', material.title), el('p', 'detail-meta', `${material.publisher || material.source} · ${label(material.content_kind)} · 版本 ${material.version || 1}`));
  const analysis = section(material.analysis_status === 'not_required' ? '数值材料' : 'Qwen 资料摘要');
  analysis.append(el('p', '', material.analysis_status === 'not_required' ? '该记录用于结构化数值监测；自动流水线将数值证据变化交给 Qwen 分析。' : material.analysis?.summary ? format(material.analysis.summary) : `当前状态：${label(material.analysis_status)}`));
  if (material.error) analysis.append(el('p', '', material.error));
  if (material.analysis) {
    facts([['模型', material.analysis.model], ['调用耗时', duration(material.analysis.elapsed_ms)]], analysis);
    renderReferences(material.analysis.material_ids || [material.id], analysis);
  }
  const source = section('引用来源');
  link(material.url, material.title || material.url, source);
  appendResourceLinks(material,source);
  source.append(el('p', 'micro muted', `材料 ID：${material.id}`));
  const excerpt = section('实际获取的原文 / 摘录');
  excerpt.append(el('p', 'excerpt', material.text || '仅取得标题，未取得正文。'));
  if (material.extraction_error) excerpt.append(el('p', '', material.extraction_error));
  const times = section('时间信息');
  facts([['观测时间', material.observed_at], ['来源发布时间', material.available_at], ['本次获取时间', material.fetched_at]], times);
  if (material.mode === 'case_replay' || material.case_id) facts([['案例',material.case_id],['观测结束',material.observed_end],['模拟释放',material.replay_release_at],['本批回放截止',material.as_of],['处理版本',material.processing_version]],times);
  if (material.time_note) times.append(el('p', '', material.time_note));
  if (!isPipeline()) renderSteps(currentRun(), section('本轮处理步骤'));
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
  if (run && !Object.prototype.hasOwnProperty.call(run, 'steps')) {
    const button = el('button','full subtle','展开本轮完整处理记录'); button.type = 'button';
    button.addEventListener('click',async () => {
      button.disabled = true;
      try { const result = await api(`/api/runs/${encodeURIComponent(run.id)}`); ui.runDetails[run.id] = result; parent.replaceChildren(el('h3','','真实处理步骤与耗时')); renderSteps(result,parent); }
      catch (error) { button.disabled = false; notify(error.message); }
    });
    parent.append(button); return;
  }
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
  $('portwatch-current-note').hidden = isPipeline() || !ui.runId;
  $('pw-latest').textContent = number(state?.latest_value);
  $('pw-observed-date').textContent = metrics?.latest_observed_date || '尚无观测';
  $('pw-baseline-label').textContent = frozen ? '冻结参考中位数' : '近期参考中位数';
  $('pw-baseline').textContent = number(state?.baseline);
  $('pw-ratio').textContent = numeric(state?.ratio) ? `当前 / 参考 ${number(state.ratio * 100)}%` : '当前 / 参考 —';
  $('pw-status').textContent = state ? label(state.current_status) : '尚无观测';
  $('pw-status').className = `metric-status ${['active','recovering'].includes(state?.current_status) ? 'lowflow-text' : ''}`;
  $('pw-data-date').textContent = `数据截至 ${metrics?.latest_observed_date || '—'}`;
  $('pw-freshness').textContent = `数据截至 ${metrics?.latest_observed_date || '—'} · 最近${isCaseReplay() ? '导入' : '获取'}时间：${date(metrics?.last_success_at)}${!isCaseReplay() && metrics?.days_since_observation > 0 ? ` · 尚未收到后续观测（距该观测 ${metrics.days_since_observation} 天）` : ''}${state?.data_gaps?.length ? ` · 观测缺口：${state.data_gaps.join('、')}` : ''}`;
  $('pw-source-info').textContent = metrics?.error ? `本次来源更新未完成：${metrics.error}。当前显示已保存结果。` : metrics?.last_checked_at ? `最近检查：${date(metrics.last_checked_at)} · 该次来源状态：${label(metrics.source_status)}` : isPipeline() ? '等待后台按配置获取数值序列。' : '点击立即更新获取数值序列。';
  if (isCaseReplay()) $('pw-source-info').textContent = metrics?.error ? `本例历史数值处理：${metrics.error}` : `历史归档按批释放 · 本例回放截止 ${utcDate(ui.replayCase?.as_of)}。曲线与规则只使用已释放日期；事后取得的历史记录不表示当年的冻结版本。`;
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
  ui.alerts.filter((alert) => alert.status !== 'revoked' && alert.started_on).forEach((alert) => {
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
  if (isPipeline() || alert.origin_type === 'numeric_rule') {
    renderAutomaticAnalysis(alert);
  } else {
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
  }
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
async function setOnlineView(view) {
  ui.onlineView = view; ui.detail = null; ui.alertId = null; ui.metrics = null; ui.alerts = []; ui.portwatchSignature = ''; ui.materialSignature = '';
  setMode('online'); await refresh();
}
$('online-tab').addEventListener('click',()=>setOnlineView('pipeline'));
$('history-tab').addEventListener('click',()=>setMode('history'));
$('acquisition-tab').addEventListener('click',()=>{setMode('acquisition');refresh();});
$('acquisition-new-subscription').addEventListener('click',()=>{fillAcquisitionSubscription(null);renderAcquisition();$('acquisition-stations').focus();});
$('acquisition-subscription-select').addEventListener('change',(event)=>{fillAcquisitionSubscription(ui.acquisition?.subscriptions?.find((item)=>item.id===event.target.value));renderAcquisition();});
$('acquisition-product').addEventListener('change',()=>{$('acquisition-hours-field').hidden=$('acquisition-product').value!=='highrate';if(!ui.acquisitionSubscriptionId)$('acquisition-interval').value=$('acquisition-product').value==='daily'?60:15;});
$('acquisition-subscription-form').addEventListener('input',()=>{ui.acquisitionSubscriptionDirty=true;});
$('acquisition-subscription-form').addEventListener('submit',(event)=>{
  event.preventDefault();
  acquisitionAction(async()=>{
    const stations=$('acquisition-stations').value.split(/[\s,，]+/).filter(Boolean).map((value)=>value.toUpperCase());
    const hourText=$('acquisition-hours').value.trim();
    const hours=$('acquisition-product').value==='highrate' && hourText ? hourText.split(/[\s,，]+/).filter(Boolean).map(Number) : [];
    if(hours.some((value)=>!Number.isInteger(value)||value<0||value>23))throw new Error('UTC 小时须为 0 至 23 的整数，以逗号分隔。');
    const subscription=await api('/api/acquisition/subscriptions',{...(ui.acquisitionSubscriptionId?{id:ui.acquisitionSubscriptionId}:{}),source:$('acquisition-source').value,stations,product:$('acquisition-product').value,start_date:$('acquisition-start').value||null,end_date:$('acquisition-end').value||null,hours,interval_seconds:Number($('acquisition-interval').value)*60,enabled:$('acquisition-enabled').checked});
    ui.acquisitionSubscriptionId=subscription.id||subscription.subscription?.id||ui.acquisitionSubscriptionId;
    ui.acquisitionSubscriptionDirty=false;
  },'订阅已保存，服务器将按自动开关与周期执行。');
});
$('acquisition-task-filter').addEventListener('change',renderAcquisitionTasks);
$('acquisition-policy-form').addEventListener('input',()=>{ui.acquisitionPolicyDirty=true;});
$('acquisition-policy-form').addEventListener('submit',(event)=>{
  event.preventDefault();
  acquisitionAction(async()=>{
    const policy=Object.fromEntries([...$('acquisition-policy-fields').querySelectorAll('[data-policy-field]')].map((field)=>[field.dataset.policyField,Number(field.value)]));
    if(!(0<policy.target_gib&&policy.target_gib<policy.high_gib&&policy.high_gib<policy.quota_gib))throw new Error('容量需满足：0 < 回收目标 < 高水位 < 总额度。');
    await api('/api/acquisition/policy',{...policy,autoclean:$('acquisition-autoclean').checked});
    ui.acquisitionPolicyDirty=false;
  },'服务器保留策略已保存。');
});
$('acquisition-cleanup').addEventListener('click',()=>acquisitionAction(()=>api('/api/acquisition/cleanup',{}),'缓存清理任务已提交；实际释放量按服务器处理结果更新。'));
$('maintenance-view').addEventListener('click',()=>setOnlineView('maintenance'));
$('pipeline-view').addEventListener('click',()=>setOnlineView('pipeline'));
$('pipeline-alert-filter').addEventListener('change',renderPipeline);
$('gnss-series-select').addEventListener('change',(event)=>{ui.gnssSeriesKey=event.target.value;renderGnss();});
$('pipeline-form').addEventListener('input',()=>{ui.pipelineFormDirty = true;});
$('pipeline-form').addEventListener('submit',(event)=>{
  event.preventDefault();
  action(async()=>{
    const sources = [...$('pipeline-sources').querySelectorAll('[data-source-id]')].map((group)=>({id:group.dataset.sourceId,enabled:group.querySelector('[data-field="enabled"]').checked,interval_seconds:Number(group.querySelector('[data-field="interval"]').value)*60}));
    await api('/api/pipeline/config',{news_topic:$('pipeline-news-topic').value.trim(),rss_terms:$('pipeline-rss-terms').value.split('\n').map((term)=>term.trim()).filter(Boolean),sources});
    ui.pipelineFormDirty = false; ui.pipelineFormLoaded = false; notify('监测配置已保存，后台按新配置继续。');
  });
});
$('pipeline-pause').addEventListener('click',()=>action(async()=>{await api('/api/pipeline/config',{paused:!ui.pipeline.paused});}));
$('new-monitor').addEventListener('click',()=>{ui.onlineView='maintenance';ui.monitorId='';ui.runId='';ui.materialId=null;ui.alertId=null;ui.detail=null;ui.materials=[];ui.runs=[];ui.metrics=null;ui.alerts=[];fillForm(null);remember();renderState();renderOnline();$('topic').focus();});
$('monitor-select').addEventListener('change',async(event)=>{ui.onlineView='maintenance';ui.monitorId=event.target.value;ui.runId='';ui.materialId=null;ui.alertId=null;ui.detail=null;ui.metrics=null;ui.alerts=[];fillForm(currentMonitor());remember();await refresh();});
$('source').addEventListener('change',()=>{const portwatch=$('source').value==='portwatch';if(portwatch && currentMonitor()?.source!=='portwatch')$('interval').value=ui.state?.portwatch?.default_interval_minutes || 1440;else if(!ui.monitorId && !portwatch)$('interval').value=30;renderSourceSettings();if(!ui.monitorId)renderOnline();});
$('pw-alert-filter').addEventListener('change',renderPortWatch);
$('run-select').addEventListener('change',async(event)=>{ui.runId=event.target.value;ui.detail='run';ui.materialId=null;remember();try{await loadMaterials();}catch(error){notify(error.message);}});
$('monitor-form').addEventListener('submit',(event)=>{event.preventDefault();ui.onlineView='maintenance';action(async()=>{const monitor=await saveMonitor();if(!monitor)return;const run=await api(`/api/monitors/${encodeURIComponent(monitor.id)}/run`,{});ui.runId=String(run.id);ui.detail='run';remember();});});
$('save-button').addEventListener('click',()=>action(async()=>{const monitor=await saveMonitor();if(monitor)notify('主题已保存。');}));
$('schedule-button').addEventListener('click',()=>action(async()=>{const enabled=!currentMonitor()?.enabled;const monitor=await saveMonitor();if(!monitor)return;await api(`/api/monitors/${encodeURIComponent(monitor.id)}/schedule`,{enabled});}));
$('cancel-button').addEventListener('click',()=>action(async()=>{const run=activeRun();if(run)await api(`/api/runs/${encodeURIComponent(run.id)}/cancel`,{});}));
$('refresh-button').addEventListener('click',()=>{notify();refresh();});
$('run-summary-button').addEventListener('click',()=>{ui.detail='run';renderRunDetail(currentRun());document.querySelector('.details-panel').scrollTop=0;});
$('replay-button').addEventListener('click',()=>action(async()=>{const run=await api('/api/history/replay',{batch_size:3});ui.onlineView='maintenance';ui.monitorId=String(run.monitor_id);ui.runId=String(run.id);ui.formLoaded=false;ui.detail='run';remember();setMode('online');}));
setMode(ui.mode);
refresh();
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
setInterval(refresh,2000);
