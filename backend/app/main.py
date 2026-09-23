"""Run from repository root: python -m uvicorn backend.app.main:app."""

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .api.routes import router
from .api.store import AnalysisStore

ROOT = Path(__file__).resolve().parents[2]


def create_app(data_dir: Path | None = None, output_dir: Path | None = None) -> FastAPI:
    store = AnalysisStore(Path(data_dir or os.getenv("DATA_DIR", ROOT / "data")),
                          Path(output_dir or os.getenv("OUTPUT_DIR", ROOT / "outputs")))

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        store.initialize()
        yield
        if store.worker and store.worker.is_alive():
            store.worker.join(timeout=10)

    application = FastAPI(title="Граф денег — локальный AML API", version="1.0.0",
                          description="Объяснимые признаки и гипотезы по неполному графу. Локальные данные и Copilot без внешнего API.",
                          lifespan=lifespan)
    application.state.store = store
    application.add_middleware(CORSMiddleware,
                               allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "http://127.0.0.1:4173"],
                               allow_credentials=False, allow_methods=["GET", "POST"],
                               allow_headers=["Content-Type"])
    application.include_router(router)
    frontend_dist = ROOT / "frontend" / "dist"

    @application.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, detail="API endpoint не найден")
        if not (frontend_dist / "index.html").is_file():
            return JSONResponse({"message": "API запущен. Соберите frontend командой npm run build или откройте dev-сервер.",
                                 "docs": "/docs"}, status_code=200 if not path else 404)
        requested = (frontend_dist / path).resolve()
        if not requested.is_relative_to(frontend_dist.resolve()):
            raise HTTPException(404, detail="Файл не найден")
        if requested.is_file():
            return FileResponse(requested)
        if path.startswith("assets/") or "." in Path(path).name:
            raise HTTPException(404, detail="Файл не найден")
        return FileResponse(frontend_dist / "index.html")

    return application


app = create_app()
