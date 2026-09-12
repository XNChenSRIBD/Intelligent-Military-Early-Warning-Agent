import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


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
