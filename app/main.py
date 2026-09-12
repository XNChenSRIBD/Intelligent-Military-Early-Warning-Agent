import asyncio
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, settings
from .history import history_payload, replay_info
from .runner import Runner
from .sources import SOURCES
from .store import Store, now


@asynccontextmanager
async def lifespan(app):
    app.state.store = Store(settings.data_dir)
    app.state.runner = Runner(app.state.store, settings)
    scheduler = asyncio.create_task(app.state.runner.schedule())
    yield
    scheduler.cancel()
    await asyncio.gather(scheduler, return_exceptions=True)
    await app.state.runner.close()


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


def get_monitor(monitor_id):
    monitor = app.state.store.get('monitor', monitor_id)
    if monitor is None:
        raise HTTPException(404, '找不到该监测主题')
    return monitor


@app.get('/')
async def index():
    return FileResponse(ROOT / 'app' / 'static' / 'index.html')


@app.get('/api/state')
async def state():
    return {'monitors': app.state.store.all('monitor'), 'runs': app.state.store.all('run'),
            'active_run_id': app.state.runner.active_run_id, 'model': app.state.runner.model,
            'sources': SOURCES, 'replay': replay_info(), 'server_time': now()}


@app.post('/api/monitors')
async def save_monitor(body: MonitorInput):
    data = body.model_dump(exclude={'id'})
    data['topic'] = data['topic'].strip()
    if not data['topic']:
        raise HTTPException(422, '请输入监测主题')
    store = app.state.store
    previous = get_monitor(body.id) if body.id else None
    if previous and previous.get('mode') == 'replay':
        raise HTTPException(400, '历史回放主题不能改为在线主题')
    if previous and any(previous[key] != data[key] for key in ('topic', 'source')):
        # A new topic/source needs its own material history; changing just the window keeps it.
        previous = None
    if previous is None:
        previous = dict(id=uuid4().hex, mode='online', enabled=False,
                        last_success_at=None, last_error=None, created_at=now())
    previous.update(data)
    return store.save('monitor', previous)


@app.post('/api/monitors/{monitor_id}/run')
async def run_now(monitor_id: str):
    monitor = get_monitor(monitor_id)
    return app.state.runner.start(monitor_id, mode=monitor.get('mode', 'online'))


@app.post('/api/monitors/{monitor_id}/schedule')
async def schedule_monitor(monitor_id: str, body: ScheduleInput):
    monitor = get_monitor(monitor_id)
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
            'runs': [run for run in app.state.store.all('run') if run['monitor_id'] == monitor_id]}


@app.get('/api/history')
async def history():
    return history_payload()


@app.post('/api/history/replay')
async def replay(body: ReplayInput):
    if not replay_info()['available']:
        raise HTTPException(400, '没有可回放的原始新闻输入')
    store = app.state.store
    monitor = store.get('monitor', 'history-news')
    if monitor is None:
        monitor = dict(id='history-news', topic='民用航运与能源价格历史新闻', source='history',
                       mode='replay', lookback_hours=168, max_materials=6, interval_minutes=30,
                       enabled=False, replay_cursor=0, last_success_at=None, last_error=None)
        store.save('monitor', monitor)
    return app.state.runner.start(monitor['id'], mode='replay', batch_size=body.batch_size)
