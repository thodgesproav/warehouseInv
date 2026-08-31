from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .notification_api import router as notification_router
from .procurement_api import router as procurement_router
from .notifications import DeliveryWorker
from .setup_api import router as setup_router, setup_required, prepare_security
from .config import settings
from .database import initialise
from .inventory import get_provider
from .inventory.local_excel import LocalExcelInventoryProvider
from .inventory.local_sync import LocalSyncInventoryProvider
from .inventory.base import InventoryError, ItemNotFound, StockConflict, InsufficientStock

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("inventory")
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialise()
    prepare_security()
    provider = get_provider()
    deliveries = DeliveryWorker()
    lock = threading.Lock()
    app.state.workers_started = False

    def start_workers():
        with lock:
            if app.state.workers_started or setup_required(): return
            if isinstance(provider, LocalExcelInventoryProvider): provider.prepare_workbook()
            if isinstance(provider, LocalSyncInventoryProvider): provider.start()
            deliveries.start()
            app.state.workers_started = True

    app.state.start_workers = start_workers
    if setup_required():
        log.info('First-run setup required. Retrieve the setup code from %s/setup-token', settings.database_path.parent)
    else: start_workers()
    log.info("Started %s with %s", settings.app_name, settings.inventory_provider)
    try:
        yield
    finally:
        if app.state.workers_started:
            if isinstance(provider, LocalSyncInventoryProvider): provider.stop()
            deliveries.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.middleware('http')
async def private_api_responses(request, call_next):
    if request.url.path.startswith('/api/') and not request.url.path.startswith('/api/setup/') and setup_required():
        return JSONResponse({'detail': 'Complete first-run setup before using the app'}, status_code=503, headers={'Cache-Control': 'no-store'})
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


app.include_router(router)
app.include_router(notification_router)
app.include_router(procurement_router)
app.include_router(setup_router)


@app.get('/healthz', include_in_schema=False)
def health():
    required = setup_required()
    started = getattr(app.state, 'workers_started', False)
    return JSONResponse({'status': 'setup_required' if required else 'ready' if started else 'starting'},
                        status_code=200 if required or started else 503)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Default validation responses can echo passwords and signed URLs in input fields.
    errors = [{'loc': error['loc'], 'msg': error['msg'], 'type': error['type']} for error in exc.errors()]
    return JSONResponse(status_code=422, content={'detail': errors})


@app.exception_handler(InventoryError)
async def inventory_error_handler(request, exc):
    status = 404 if isinstance(exc, ItemNotFound) else 409 if isinstance(exc, (StockConflict, InsufficientStock)) else 503
    return JSONResponse(status_code=status, content={'detail': str(exc)})

frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend.exists():
    assets = frontend / "assets"
    if assets.exists(): app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = (frontend / path).resolve()
        if target.is_relative_to(frontend.resolve()) and target.is_file(): return FileResponse(target)
        if path.startswith('api/'): return JSONResponse({'detail': 'Not found'}, status_code=404)
        return FileResponse(frontend / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def root(): return {"message": settings.app_name, "docs": "/docs"}
