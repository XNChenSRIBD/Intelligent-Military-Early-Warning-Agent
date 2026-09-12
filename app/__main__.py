import os

import uvicorn

from .config import settings

uvicorn.run('app.main:app', host=os.getenv('APP_HOST', '127.0.0.1'),
            port=int(os.getenv('APP_PORT', '8080')), workers=1)
