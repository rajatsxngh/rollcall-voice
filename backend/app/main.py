"""FastAPI app. From backend/: uv run uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.db import Database
from app.events import EventBus
from app.llm import LLMClient
from app.routers import events, system


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.live_db_path)
        db.create_all()
        app.state.settings = settings
        app.state.db = db
        app.state.bus = EventBus(db)
        app.state.llm = LLMClient(settings)
        try:
            yield
        finally:
            await app.state.llm.aclose()
            db.dispose()

    app = FastAPI(title="RollCall Voice", version="0.1.0", lifespan=lifespan)
    # The dashboard's dev server (M4) runs on another port.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.include_router(system.router)
    app.include_router(events.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
