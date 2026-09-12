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


settings = Settings()
