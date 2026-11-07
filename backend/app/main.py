"""FastAPI app. From backend/: uv run uvicorn app.main:app --port 8100 --reload"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.db import Database
from app.engine.runs import LiveRun, recover_after_restart
from app.events import EventBus
from app.llm import LLMClient
from app.routers import events, run, state, system, vapi


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.live_db_path)
        db.create_all()
        bus = EventBus(db)
        recover_after_restart(db, bus)
        app.state.settings = settings
        app.state.db = db
        app.state.bus = bus
        app.state.llm = LLMClient(settings)
        app.state.live_run = LiveRun(db, bus, settings)
        try:
            yield
        finally:
            app.state.live_run.stop()
            await app.state.llm.aclose()
            db.dispose()

    app = FastAPI(title="RollCall Voice", version="0.1.0", lifespan=lifespan)
    # The dashboard's dev server (M4) runs on another port.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.include_router(system.router)
    app.include_router(events.router)
    app.include_router(state.router)
    app.include_router(run.router)
    app.include_router(vapi.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
