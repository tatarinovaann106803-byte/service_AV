"""Public website API. Private geometry is never part of this HTTP contract."""

import logging
import os
from threading import BoundedSemaphore

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .coefficients import product_catalog
from .config import STATIC
from .optimization import optimize, public_result
from .public_schemas import PublicRequest
from .schemas import InputModel
from .storage import get_calculation, json_compatible, save_calculation, select_variant
from .weather import WeatherUnavailable, fetch_weather, season_weather

logger = logging.getLogger(__name__)
app = FastAPI(title="Агривольтаика — варианты проекта", version="4.0.0")
capacity = BoundedSemaphore(2)
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]},
    )


@app.get("/")
def root():
    return {"service": "Agrivoltaic decision support", "version": "4.0.0", "calculator": "/calculator"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/calculator", include_in_schema=False)
def page():
    return FileResponse(STATIC / "index.html")


@app.get("/products")
def products():
    return product_catalog()


@app.post("/calculate")
def calculate(request: PublicRequest):
    if not capacity.acquire(blocking=False):
        raise HTTPException(429, "Сервис занят расчётами. Повторите запрос немного позже.")
    try:
        record = json_compatible(optimize(request))
        calculation_id = save_calculation(request.model_dump(), record)
        return {"success": True, "data": public_result(request, record, calculation_id)}
    except WeatherUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.exception("Calculation failed")
        raise HTTPException(503, "Не удалось завершить расчёт. Повторите запрос позже.") from exc
    finally:
        capacity.release()


@app.get("/calculations/{calculation_id}")
def calculation(calculation_id: str):
    record = get_calculation(calculation_id)
    if record is None:
        raise HTTPException(404, "Расчёт не найден")
    return {"success": True, "data": public_result(PublicRequest(**record["inputs"]), record, calculation_id)}


class Selection(InputModel):
    variant_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@app.post("/calculations/{calculation_id}/selection")
def selection(calculation_id: str, request: Selection):
    try:
        record = select_variant(calculation_id, request.variant_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if record is None:
        raise HTTPException(404, "Расчёт не найден")
    return {"success": True, "calculation_id": calculation_id, "selected_variant_id": record["selected_variant_id"]}


@app.get("/weather")
def weather_preview(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    year: int = Query(ge=1984, le=2100),
    start_month: int = Query(default=4, ge=1, le=12),
    end_month: int = Query(default=9, ge=1, le=12),
):
    try:
        return season_weather(fetch_weather(lat, lon, year), start_month, end_month)
    except WeatherUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


def tilda_origins():
    return [
        origin.strip().rstrip("/")
        for origin in os.getenv("TILDA_ORIGINS", "").split(",")
        if origin.strip().startswith(("https://", "http://localhost:", "http://127.0.0.1:"))
    ]


@app.get("/integration/config")
def integration_config():
    return {"parent_origins": tilda_origins()}
