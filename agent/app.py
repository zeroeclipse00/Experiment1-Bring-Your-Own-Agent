import asyncio
from dataclasses import replace
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import ROOT, Settings
from .runner import AgentRunner
from .tools import ToolRegistry


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)
    mode: Literal["demo", "live"] = "demo"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="研读助手", version="1.0.0", description="Bring Your Own Agent · 本地资料研读与阅读排期")
    slots = asyncio.Semaphore(2)

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(ROOT / "web" / "index.html")

    @app.get("/api/health")
    async def health():
        registry = ToolRegistry(settings.data_dir)
        return {"status": "ok", "default_mode": settings.mode, "live_ready": settings.live_ready,
                "model": settings.model if settings.live_ready else "未配置",
                "materials": registry.inventory(), "tools": registry.schemas()}

    @app.post("/api/run")
    async def run(body: RunRequest):
        if not body.query.strip():
            return JSONResponse({"error": "请输入问题"}, status_code=422)
        run_settings = replace(settings, mode=body.mode)
        async with slots:
            result = await AgentRunner(run_settings).run(body.query.strip())
        return JSONResponse(result, status_code=502 if result["error"] else 200)

    return app


app = create_app()
