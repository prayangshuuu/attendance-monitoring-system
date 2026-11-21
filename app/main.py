from fastapi import FastAPI

from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME)

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
