from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import execution, me, org, products, requests, tasks, users
from app.config import get_settings
from app.errors import AppError


def create_app() -> FastAPI:
    app = FastAPI(title="Wakeup API", version="0.1.0")

    origins = get_settings().cors_origins
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code, content={"code": exc.code, "message": exc.message}
        )

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    for r in (
        me.router,
        requests.router,
        users.router,
        org.router,
        products.router,
        tasks.router,
        execution.router,
    ):
        app.include_router(r, prefix="/api")
    return app


app = create_app()
