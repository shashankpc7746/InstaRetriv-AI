"""Local entry point: `python run.py`.

Auto-reload is enabled only when APP_ENV=development. In production use the
uvicorn command from Procfile / render.yaml instead.
"""

import uvicorn

from app.config import settings


def main() -> None:
    reload_enabled = settings.app_env.strip().lower() == "development"
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=reload_enabled,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
