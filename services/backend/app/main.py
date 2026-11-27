from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.config import missing_config
from app.database import database_health
from app.database import pool
from contextlib import asynccontextmanager
from app.storage.r2 import storage_health
from app.api.search import router as search_router
from app.api.catalog import router as catalog_router
from app.api.shares import router as shares_router

@asynccontextmanager
async def lifespan(app):
    yield
    if pool.cache_info().currsize:
        pool().close()
        pool.cache_clear()

app = FastAPI(title="Dialogue Library API", version="0.1.0", lifespan=lifespan)

@app.get("/api/v1/health/live")
@app.get("/health/live", include_in_schema=False)
def live():
    return {"status": "ok", "service": "api"}

@app.get("/api/v1/health/ready")
@app.get("/health/ready", include_in_schema=False)
def ready():
    missing = missing_config()
    database = database_health()
    storage = storage_health()
    ok = database['status'] == 'ok' and storage['status'] == 'ok'
    return JSONResponse(status_code=200 if ok else 503, content={
        "status": "ready" if ok else "not_ready", "missing": missing,
        "connectivity_checked": database['status'] == 'ok',
        "database": database,
        "storage": storage,
        "reason": "ready" if ok else "cloud_check_failed",
    })

from app.api.review import router as review_router
app.include_router(review_router)
app.include_router(search_router)
app.include_router(catalog_router)
app.include_router(shares_router)
