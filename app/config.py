import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
SERVER_ASSETS = '/home/xnchen/gnss_public_data/chasing_lightning' if os.name != 'nt' else str(ROOT.parents[1] / 'public_data_chasing_lightning')
SERVER_RUNTIME = '/home/xnchen/gnss_public_data/early_warning_runtime' if os.name != 'nt' else str(ROOT / 'runtime-server')


@dataclass
class Settings:
    data_dir: Path = Path(os.getenv('DATA_DIR', str(ROOT / 'runtime')))
    model_base_url: str = os.getenv('MODEL_BASE_URL', 'http://127.0.0.1:8000/v1')
    model_name: str = os.getenv('MODEL_NAME', 'qwen3-4b')
    model_api_key: str = os.getenv('MODEL_API_KEY', '')
    model_timeout: float = float(os.getenv('MODEL_TIMEOUT', '120'))
    model_max_tokens: int = int(os.getenv('MODEL_MAX_TOKENS', '1800'))
    disable_thinking: bool = os.getenv('DISABLE_THINKING', 'true').lower() == 'true'
    source_timeout: float = float(os.getenv('SOURCE_TIMEOUT', '25'))
    request_max_bytes: int = int(os.getenv('REQUEST_MAX_BYTES', '2000000'))
    article_chars: int = int(os.getenv('ARTICLE_CHARS', '1800'))
    rss_url: str = os.getenv('RSS_URL', 'https://news.un.org/feed/subscribe/en/news/all/rss.xml')
    portwatch_url: str = os.getenv('PORTWATCH_URL', 'https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/Daily_Chokepoints_Data/FeatureServer/0')
    portwatch_id: str = os.getenv('PORTWATCH_ID', 'chokepoint6')
    portwatch_history_days: int = int(os.getenv('PW_HISTORY_DAYS', '90'))
    portwatch_rule_id: str = os.getenv('PW_RULE_ID', 'pw_lowflow_v1')
    portwatch_baseline_days: int = int(os.getenv('PW_BASELINE_DAYS', '28'))
    portwatch_min_baseline_days: int = int(os.getenv('PW_MIN_BASELINE_DAYS', '21'))
    portwatch_trigger_ratio: float = float(os.getenv('PW_TRIGGER_RATIO', '0.5'))
    portwatch_trigger_days: int = int(os.getenv('PW_TRIGGER_DAYS', '2'))
    portwatch_recovery_ratio: float = float(os.getenv('PW_RECOVERY_RATIO', '0.8'))
    portwatch_recovery_days: int = int(os.getenv('PW_RECOVERY_DAYS', '2'))
    pipeline_enabled: bool = os.getenv('PIPELINE_ENABLED', 'true').lower() == 'true'
    pipeline_news_topic: str = os.getenv('PIPELINE_NEWS_TOPIC', '(Hormuz OR "Strait of Hormuz") (shipping OR tanker OR cargo OR maritime)')
    pipeline_rss_terms: tuple = tuple(term.strip() for term in os.getenv(
        'PIPELINE_RSS_TERMS', 'Hormuz,shipping,tanker,maritime').split(',') if term.strip())
    pipeline_sources: tuple = tuple(source.strip() for source in os.getenv(
        'PIPELINE_SOURCES', 'gdelt,rss,portwatch').split(',') if source.strip())
    pipeline_news_interval: int = int(os.getenv('PIPELINE_NEWS_INTERVAL', '300'))
    pipeline_portwatch_interval: int = int(os.getenv('PIPELINE_PORTWATCH_INTERVAL', '86400'))
    pipeline_lookback_hours: int = int(os.getenv('PIPELINE_LOOKBACK_HOURS', '48'))
    pipeline_overlap_minutes: int = int(os.getenv('PIPELINE_OVERLAP_MINUTES', '30'))
    pipeline_batch_size: int = int(os.getenv('PIPELINE_BATCH_SIZE', '3'))
    pipeline_analysis_version: str = os.getenv('PIPELINE_ANALYSIS_VERSION', 'shipping_v1')
    pipeline_tool_calls: int = int(os.getenv('PIPELINE_TOOL_CALLS', '2'))
    pipeline_model_requests: int = int(os.getenv('PIPELINE_MODEL_REQUESTS', '4'))
    pipeline_source_concurrency: int = int(os.getenv('PIPELINE_SOURCE_CONCURRENCY', '2'))
    pipeline_search_windows: int = int(os.getenv('PIPELINE_SEARCH_WINDOWS', '8'))
    pipeline_retry_seconds: int = int(os.getenv('PIPELINE_RETRY_SECONDS', '60'))
    pipeline_retry_max_seconds: int = int(os.getenv('PIPELINE_RETRY_MAX_SECONDS', '3600'))
    pipeline_mode: str = os.getenv('PIPELINE_MODE', 'online')
    replay_manifests: tuple = tuple(value.strip() for value in os.getenv(
        'REPLAY_MANIFESTS', 'cases/replay/kharkiv/manifest.json;cases/replay/hormuz/manifest.json').split(';') if value.strip())
    server_asset_root: Path = Path(os.getenv('SERVER_ASSET_ROOT', os.getenv('REPLAY_ASSET_ROOT', SERVER_ASSETS)))
    server_runtime_root: Path = Path(os.getenv('SERVER_RUNTIME_ROOT', SERVER_RUNTIME))
    acquisition_enabled: bool = os.getenv('ACQUISITION_ENABLED', 'false').lower() == 'true'
    acquisition_autoclean: bool = os.getenv('ACQUISITION_AUTOCLEAN', 'false').lower() == 'true'
    replay_asset_root: Path = Path(os.getenv('SERVER_ASSET_ROOT', os.getenv('REPLAY_ASSET_ROOT', SERVER_ASSETS)))
    replay_autostart: bool = os.getenv('REPLAY_AUTOSTART', 'true').lower() == 'true'
    replay_max_attempts: int = int(os.getenv('REPLAY_MAX_ATTEMPTS', '3'))

    @property
    def portwatch_rule(self):
        return {
            'id': self.portwatch_rule_id, 'history_days': self.portwatch_history_days,
            'baseline_days': self.portwatch_baseline_days,
            'min_baseline_days': self.portwatch_min_baseline_days,
            'trigger_ratio': self.portwatch_trigger_ratio,
            'trigger_days': self.portwatch_trigger_days,
            'recovery_ratio': self.portwatch_recovery_ratio,
            'recovery_days': self.portwatch_recovery_days,
        }


settings = Settings()
