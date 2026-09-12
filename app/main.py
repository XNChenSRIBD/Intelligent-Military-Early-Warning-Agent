import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, settings
from .history import history_payload, replay_info
from .pipeline import Pipeline, alert_summary
from .online import OnlinePipeline
from .replay import CaseReplay
from .resources import ResourceManager
from .locks import SharedModelLock
from .runner import Runner
from .sources import SOURCES
from .store import Store, now


@asynccontextmanager
async def lifespan(app):
    if settings.pipeline_mode not in ('online', 'case_replay'):
        raise ValueError('PIPELINE_MODE 仅支持 online 或 case_replay')
    if settings.pipeline_mode == 'case_replay' and settings.data_dir.resolve() == (ROOT / 'runtime').resolve():
        raise ValueError('历史回放必须指定独立 DATA_DIR，不使用默认在线 runtime')
    app.state.store = Store(settings.data_dir)
    replay_instance = app.state.store.get('replay', 'instance')
    if settings.pipeline_mode == 'case_replay' and app.state.store.all('monitor') and not replay_instance:
        raise ValueError('指定目录已有在线数据，请为回放选择独立 DATA_DIR')
    if settings.pipeline_mode == 'online' and replay_instance:
        raise ValueError('该 DATA_DIR 属于历史回放，不能作为在线实例启动')
    app.state.runner = Runner(app.state.store, settings)
    app.state.acquisition = ResourceManager(settings)
    app.state.runner.model_lock = SharedModelLock(settings.server_runtime_root / 'catalog' / 'model.lock')
    pipeline_type = CaseReplay if settings.pipeline_mode == 'case_replay' else OnlinePipeline
    app.state.pipeline = pipeline_type(app.state.store, settings, app.state.runner, app.state.acquisition)
    app.state.acquisition.start()
    scheduler = asyncio.create_task(app.state.runner.schedule())
    app.state.pipeline.start()
    yield
    scheduler.cancel()
    await asyncio.gather(scheduler, return_exceptions=True)
    await app.state.pipeline.close()
    await app.state.runner.close()
    await app.state.acquisition.close()


app = FastAPI(title='公开资料工作台', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'), name='static')


class MonitorInput(BaseModel):
    id: str | None = None
    topic: str = Field(min_length=1, max_length=300)
    source: Literal['gdelt', 'rss', 'portwatch'] = 'gdelt'
    lookback_hours: int = Field(default=168, ge=1, le=2160)
    max_materials: int = Field(default=6, ge=1, le=24)
    interval_minutes: int = Field(default=30, ge=1, le=1440)


class ScheduleInput(BaseModel):
    enabled: bool


class ReplayInput(BaseModel):
    batch_size: int = Field(default=3, ge=1, le=6)


class PipelineSourceInput(BaseModel):
    id: Literal['gdelt', 'rss', 'portwatch']
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=60, le=604800)


class PipelineInput(BaseModel):
    paused: bool | None = None
    news_topic: str | None = Field(default=None, min_length=1, max_length=300)
    rss_terms: list[str] | None = Field(default=None, max_length=20)
    sources: list[PipelineSourceInput] | None = None


class ReadInput(BaseModel):
    read: bool = True


@app.get('/api/acquisition')
async def acquisition_state():
    return dict(app.state.acquisition.snapshot(), model_counts=app.state.store.work_counts())


@app.get('/api/acquisition/resources/{resource_id}')
async def acquisition_resource(resource_id: str):
    record = app.state.acquisition.view(resource_id)
    if not record:
        raise HTTPException(404, '找不到注册资源')
    return record


@app.post('/api/acquisition/policy')
async def acquisition_policy(body: dict):
    try:
        return app.state.acquisition.update_policy(body)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from error


@app.post('/api/acquisition/subscriptions')
async def acquisition_subscription(body: dict):
    try:
        return app.state.acquisition.save_subscription(body)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from error


@app.post('/api/acquisition/resources/{resource_id}/retry')
async def acquisition_retry(resource_id: str):
    if not app.state.acquisition.view(resource_id):
        raise HTTPException(404, '找不到注册资源')
    return app.state.acquisition.retry(resource_id)


@app.post('/api/acquisition/resources/{resource_id}/fetch')
async def acquisition_fetch(resource_id: str):
    if not app.state.acquisition.view(resource_id):
        raise HTTPException(404, '找不到注册资源')
    return app.state.acquisition.retry(resource_id, fetch=True)


@app.post('/api/acquisition/cleanup')
async def acquisition_cleanup():
    return app.state.acquisition.request_cleanup()


@app.post('/api/acquisition/cases/{case_id}/acquire')
async def acquisition_case(case_id: str):
    registered = sorted({dependency['resource_id'] for dependency in app.state.acquisition.dependencies()
                         if dependency.get('case_id') == case_id})
    if not registered:
        raise HTTPException(404, '该历史案例尚未在服务器登记；启动对应回放实例后会自动登记')
    for identifier in registered:
        record = app.state.acquisition.view(identifier) or {}
        if record.get('compute_status') != 'completed':
            app.state.acquisition.retry(identifier)
    app.state.pipeline.wakeup.set()
    return {'case_id': case_id, 'registered_count': len(registered), 'status': 'queued'}


def run_summary(run):
    return {key: value for key, value in run.items() if key not in ('steps', 'input', 'result')}


def get_monitor(monitor_id):
    monitor = app.state.store.get('monitor', monitor_id)
    if monitor is None:
        raise HTTPException(404, '找不到该监测主题')
    return monitor


def require_online():
    if settings.pipeline_mode == 'case_replay':
        raise HTTPException(400, '当前为隔离的历史自动回放实例，资料由案例清单释放')


@app.get('/')
async def index():
    return FileResponse(ROOT / 'app' / 'static' / 'index.html')


@app.get('/api/state')
async def state():
    return {'monitors': app.state.store.all('monitor'),
            'runs': [run_summary(run) for run in app.state.store.recent('run', 40)],
            'active_run_id': app.state.runner.active_run_id, 'model': app.state.runner.model,
            'sources': SOURCES, 'replay': replay_info(), 'server_time': now(),
            'portwatch': dict(portwatch_scope(), rule=settings.portwatch_rule,
                              default_interval_minutes=1440)}


@app.post('/api/monitors')
async def save_monitor(body: MonitorInput):
    require_online()
    data = body.model_dump(exclude={'id'})
    data['topic'] = data['topic'].strip()
    if not data['topic']:
        raise HTTPException(422, '请输入监测主题')
    store = app.state.store
    previous = get_monitor(body.id) if body.id else None
    if previous and previous.get('pipeline_owned'):
        raise HTTPException(400, '自动监测请使用流水线配置入口')
    if previous and previous.get('mode') == 'replay':
        raise HTTPException(400, '历史回放主题不能改为在线主题')
    if previous and (previous['source'] != data['source'] or
                     (previous['topic'] != data['topic'] and data['source'] != 'portwatch')):
        # A new topic/source needs its own material history; changing just the window keeps it.
        previous = None
    if previous is None:
        previous = dict(id=uuid4().hex, mode='online', enabled=False,
                        last_success_at=None, last_error=None, created_at=now())
        if data['source'] == 'portwatch' and 'interval_minutes' not in body.model_fields_set:
            data['interval_minutes'] = 1440
    elif 'interval_minutes' not in body.model_fields_set:
        data['interval_minutes'] = previous['interval_minutes']
    previous.update(data)
    return store.save('monitor', previous)


@app.post('/api/monitors/{monitor_id}/run')
async def run_now(monitor_id: str):
    require_online()
    monitor = get_monitor(monitor_id)
    if monitor.get('pipeline_owned'):
        raise HTTPException(400, '该监测由自动流水线调度')
    try:
        return app.state.runner.start(monitor_id, mode=monitor.get('mode', 'online'))
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.post('/api/monitors/{monitor_id}/schedule')
async def schedule_monitor(monitor_id: str, body: ScheduleInput):
    require_online()
    monitor = get_monitor(monitor_id)
    if monitor.get('pipeline_owned'):
        raise HTTPException(400, '自动监测请使用流水线暂停或来源开关')
    if monitor.get('mode') == 'replay':
        raise HTTPException(400, '历史回放使用分批回放按钮')
    monitor.update(enabled=body.enabled, next_run_at=now())
    return app.state.store.save('monitor', monitor)


@app.post('/api/runs/{run_id}/cancel')
async def cancel_run(run_id: str):
    run = app.state.runner.cancel(run_id)
    if run is None:
        raise HTTPException(404, '找不到该运行')
    return run


@app.get('/api/monitors/{monitor_id}/materials')
async def monitor_materials(monitor_id: str, run_id: str | None = None):
    get_monitor(monitor_id)
    return {'materials': app.state.store.materials(monitor_id, run_id),
            'runs': [run_summary(run) for run in app.state.store.recent('run', 100)
                     if run['monitor_id'] == monitor_id]}


@app.get('/api/runs/{run_id}')
async def run_detail(run_id: str):
    run = app.state.store.get('run', run_id)
    if run is None:
        raise HTTPException(404, '找不到该运行')
    if run.get('archive_path'):
        path = Path(run['archive_path']).resolve()
        if path.is_relative_to((settings.server_runtime_root / 'evidence').resolve()):
            try:
                archived = json.loads(path.read_text(encoding='utf-8'))
                if archived.get('run', {}).get('id') == run_id:
                    return dict(archived['run'], archive_path=run['archive_path'], archived=True)
            except (OSError, ValueError):
                return dict(run, archive_unavailable=True)
    return run


@app.get('/api/pipeline')
async def pipeline_state():
    payload = app.state.pipeline.snapshot()
    payload.setdefault('mode', 'online')
    return payload


@app.get('/api/replay/cases/{case_id}')
async def replay_case(case_id: str):
    if settings.pipeline_mode != 'case_replay':
        raise HTTPException(404, '当前不是历史回放实例')
    record = app.state.pipeline.case_view(case_id)
    if record is None:
        raise HTTPException(404, '找不到该回放案例')
    return record


@app.get('/api/replay/resources/{resource_id}')
async def replay_resource(resource_id: str):
    if settings.pipeline_mode != 'case_replay':
        raise HTTPException(404, '当前不是历史回放实例')
    record = app.state.pipeline.resource_view(resource_id)
    if record is None:
        raise HTTPException(404, '该资源尚未释放或不属于此实例')
    return record


@app.post('/api/pipeline/config')
async def pipeline_config(body: PipelineInput):
    changes = body.model_dump(exclude_none=True)
    if 'news_topic' in changes:
        changes['news_topic'] = changes['news_topic'].strip()
        if not changes['news_topic']:
            raise HTTPException(422, '新闻主题词不能为空')
    if 'rss_terms' in changes:
        changes['rss_terms'] = [term.strip() for term in changes['rss_terms'] if term.strip()]
    try:
        return app.state.pipeline.configure(changes)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get('/api/pipeline/alerts')
async def pipeline_alerts(limit: int = 50):
    return {'alerts': [alert_summary(alert) for alert in app.state.pipeline.recent_alerts(max(1, min(limit, 100)))]}


@app.get('/api/work/{work_id}')
async def work_detail(work_id: str):
    work = app.state.store.get('work', work_id)
    if work is None:
        raise HTTPException(404, '找不到该分析工作')
    return work


def portwatch_scope():
    return {'source_key': settings.portwatch_url.rstrip('/'),
            'portid': settings.portwatch_id, 'name': '霍尔木兹海峡'}


def portwatch_monitor(monitor_id):
    monitor = get_monitor(monitor_id)
    if monitor['source'] != 'portwatch':
        raise HTTPException(400, '此主题不是 PortWatch 数值监测')
    return monitor


def get_alert(alert_id):
    alert = app.state.store.get('alert', alert_id)
    if alert is None:
        raise HTTPException(404, '找不到该提醒')
    return alert


@app.get('/api/materials/{material_id}')
async def material_detail(material_id: str):
    material = app.state.store.get_material(material_id)
    if material is None:
        raise HTTPException(404, '找不到该材料版本')
    return material


@app.get('/api/monitors/{monitor_id}/metrics')
async def metrics(monitor_id: str):
    monitor = portwatch_monitor(monitor_id)
    store = app.state.store
    scope = portwatch_scope()
    current = store.get('monitor_state', monitor_id)
    if monitor.get('mode') == 'case_replay':
        scope = dict((current or {}).get('scope') or {}, name='霍尔木兹历史 PortWatch')
        scope.setdefault('source_key', '')
        scope.setdefault('portid', settings.portwatch_id)
    if current and (current.get('source_key') != scope['source_key'] or
                    current.get('portid') != scope['portid'] or
                    current.get('rule', {}).get('id') != settings.portwatch_rule_id):
        current = None
    source = store.get('portwatch_source', monitor_id) or {}
    if source.get('source_key') != scope['source_key'] or source.get('portid') != scope['portid']:
        source = {}
    today = datetime.now(timezone.utc).date()
    if monitor.get('mode') == 'case_replay' and (current or {}).get('as_of'):
        today = date.fromisoformat(current['as_of'][:10])
    end = source.get('range_end') or today.isoformat()
    start = source.get('range_start') or (today - timedelta(
        days=settings.portwatch_history_days - 1)).isoformat()
    if monitor.get('mode') == 'case_replay':
        case = store.get('replay_case', monitor['case_id']) or {}
        cutoff = case.get('as_of')
        if cutoff:
            visible_through = (datetime.fromisoformat(cutoff.replace('Z', '+00:00')) - timedelta(microseconds=1)).date().isoformat()
            end = min(end, visible_through)
            if current and current.get('as_of') and current['as_of'] > cutoff:
                current = None
    rows = store.portwatch_rows(scope['source_key'], scope['portid'], start, end)
    latest = (current or {}).get('latest_observed_date')
    return {
        'monitor_id': monitor_id, 'scope': scope, 'rule': settings.portwatch_rule,
        'state': {key: value for key, value in current.items() if key not in ('series', 'input_versions')} if current else None,
        'series': [row for row in (current or {}).get('series', [])
                   if start <= row['observed_date'] <= end],
        'observations': rows, 'range_start': start, 'range_end': end,
        'last_checked_at': source.get('last_checked_at'),
        'last_success_at': source.get('last_success_at'),
        'source_status': source.get('status', 'pending'), 'error': source.get('error'),
        'latest_observed_date': latest,
        'days_since_observation': (today - date.fromisoformat(latest)).days if latest else None,
        'current_state_only': True,
    }


@app.get('/api/monitors/{monitor_id}/alerts')
async def alerts(monitor_id: str):
    portwatch_monitor(monitor_id)
    items = [item for item in app.state.store.all('alert') if item['monitor_id'] == monitor_id]
    return {'alerts': sorted(items, key=lambda item: item['detected_at'], reverse=True)}


@app.get('/api/alerts/{alert_id}')
async def alert_detail(alert_id: str):
    return get_alert(alert_id)


@app.post('/api/alerts/{alert_id}/read')
async def read_alert(alert_id: str, body: ReadInput):
    alert = get_alert(alert_id)
    alert['read_at'] = now() if body.read else None
    return app.state.store.save('alert', alert)


@app.post('/api/alerts/{alert_id}/explain')
async def explain(alert_id: str):
    require_online()
    alert = get_alert(alert_id)
    if alert.get('origin_type'):
        raise HTTPException(400, '流水线会自动分析证据变化并重试，当前结果请在提醒详情查看')
    try:
        return app.state.runner.start_explanation(alert)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.get('/api/history')
async def history():
    return history_payload()


@app.post('/api/history/replay')
async def replay(body: ReplayInput):
    require_online()
    if not replay_info()['available']:
        raise HTTPException(400, '没有可回放的原始新闻输入')
    store = app.state.store
    monitor = store.get('monitor', 'history-news')
    if monitor is None:
        monitor = dict(id='history-news', topic='民用航运与能源价格历史新闻', source='history',
                       mode='replay', lookback_hours=168, max_materials=6, interval_minutes=30,
                       enabled=False, replay_cursor=0, last_success_at=None, last_error=None)
        store.save('monitor', monitor)
    try:
        return app.state.runner.start(monitor['id'], mode='replay', batch_size=body.batch_size)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
