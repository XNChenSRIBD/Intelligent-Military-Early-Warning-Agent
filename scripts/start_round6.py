"""Start or resume the fixed historical Round 6 instance with existing dependencies."""
import os
from pathlib import Path

from dotenv import dotenv_values


root = Path(__file__).resolve().parents[1]
existing = Path('/home/xnchen/projects/Intelligent-Military-Early-Warning-Agent')
environment = {key: value for key, value in dotenv_values(existing / '.env').items() if value is not None}
environment.update(os.environ)
environment.update({
    'APP_HOST': '10.20.14.16', 'APP_PORT': '8081',
    'PIPELINE_MODE': 'case_replay', 'PIPELINE_ENABLED': 'true',
    'PIPELINE_ANALYSIS_VERSION': 'round6_observation_v1',
    'REPLAY_MANIFESTS': 'cases/round6/kharkiv/manifest.json;cases/round6/hormuz/manifest.json',
    'REPLAY_AUTOSTART': 'true', 'ACQUISITION_ENABLED': 'true',
    'DATA_DIR': '/home/xnchen/gnss_public_data/early_warning_runtime/replays/round6',
    'SERVER_RUNTIME_ROOT': '/home/xnchen/gnss_public_data/early_warning_runtime',
    'SERVER_ASSET_ROOT': '/home/xnchen/gnss_public_data/chasing_lightning',
    'REPLAY_ASSET_ROOT': '/home/xnchen/gnss_public_data/chasing_lightning',
    'MODEL_BASE_URL': 'http://127.0.0.1:8000/v1', 'MODEL_NAME': 'qwen3-4b',
})
environment.setdefault('ACQUISITION_AUTOCLEAN', 'false')
interpreter = existing / '.venv-workbench/bin/python'
Path(environment['DATA_DIR']).mkdir(parents=True, exist_ok=True)
os.chdir(root)
os.execve(str(interpreter), [str(interpreter), '-m', 'app'], environment)
