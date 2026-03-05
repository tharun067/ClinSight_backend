from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging, time, os, traceback

from src.config import settings
from src.database.postgres import init_db, close_db
from src.database.vector_db import init_vector_db, close_vector_db
from src.services.embedding import init_embedding_service, close_embedding_service, ServiceNotReadyError
from src.routers import auth, patients, diagnostic, documents, imaging, labs, notes, audit

# Dev mode: set DEBUG=True in .env, or pass --reload flag, or set CLINSIGHT_DEV_MODE=1
_DEV_MODE = os.environ.get("CLINSIGHT_DEV_MODE", "0") == "1" or settings.DEBUG

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

for _noisy in (
    "sqlalchemy.engine",
    "sqlalchemy.engine.Engine",
    "sqlalchemy.pool",
    "sqlalchemy.dialects",
    "sqlalchemy.orm",
    "alembic",
    "httpx",
    "httpcore",
    "multipart",
    "passlib",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


async def _load_models_background():
    """Load heavy ML models after server is already listening on the port."""
    try:
        await init_embedding_service()
        logger.info("Embedding service initialized")
    except Exception as e:
        logger.error(f"Background model loading failed: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    try:
        await init_db()
        logger.info("PostgreSQL initialized")
        await init_vector_db()
        logger.info("FAISS vector DB initialized")
        import asyncio as _asyncio
        _asyncio.create_task(_load_models_background())
        logger.info("Model loading started in background")
    except Exception as e:
        logger.error(f"Startup failed: {e}", exc_info=True)
        raise
    yield
    logger.info("Shutting down")
    try:
        await close_embedding_service()
        await close_vector_db()
        await close_db()
    except Exception as e:
        logger.error(f"Shutdown error: {e}", exc_info=True)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-powered medical diagnostic support system",
    debug=settings.DEBUG,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    ms = (time.time() - start) * 1000
    logger.info(f"{request.method} {request.url.path} → {response.status_code} ({ms:.1f}ms)")
    response.headers["X-Process-Time-ms"] = f"{ms:.1f}"
    return response


@app.exception_handler(ServiceNotReadyError)
async def service_not_ready_handler(request: Request, exc: ServiceNotReadyError):
    return JSONResponse(
        status_code=503,
        content={"detail": str(exc)},
        headers={"Retry-After": "10"},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    logger.error(f"Unhandled error on {request.url.path}: {exc}\n{tb}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "path": str(request.url.path)},
    )


if os.path.exists(settings.UPLOAD_DIR):
    app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

app.include_router(auth.router,       prefix="/api/auth",       tags=["Authentication"])
app.include_router(patients.router,   prefix="/api/patients",   tags=["Patients"])
app.include_router(diagnostic.router, prefix="/api/diagnostic", tags=["AI Diagnostic Support"])
app.include_router(documents.router,  prefix="/api/documents",  tags=["Documents"])
app.include_router(imaging.router,    prefix="/api/imaging",    tags=["Imaging Studies"])
app.include_router(labs.router,       prefix="/api/labs",       tags=["Labs & Vitals"])
app.include_router(notes.router,      prefix="/api/notes",      tags=["Clinical Notes"])
app.include_router(audit.router,      prefix="/api/audit",      tags=["Audit Logs"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "app": settings.APP_NAME, "version": settings.APP_VERSION,
            "dev_mode": _DEV_MODE}


@app.get("/api/debug/logs", tags=["Debug"])
async def get_recent_logs(lines: int = 100):
    """
    Return the last N lines of clinsight_api.log.
    Only available when CLINSIGHT_DEV_MODE=1 or DEBUG=True.
    """
    if not _DEV_MODE:
        return JSONResponse(status_code=403, content={"detail": "Debug endpoint only available in dev mode. Run with --dev flag."})
    log_path = "clinsight_api.log"
    if not os.path.exists(log_path):
        return {"lines": [], "message": "Log file not found (logs may be printing to terminal in dev mode)"}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        recent = all_lines[-lines:]
        return {"total_lines": len(all_lines), "showing": len(recent), "lines": [l.rstrip() for l in recent]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"Could not read log file: {e}"})


@app.get("/", tags=["Root"])
async def root():
    return {"message": f"Welcome to {settings.APP_NAME}", "version": settings.APP_VERSION,
            "docs": "/api/docs", "health": "/health"}


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="ClinSight backend server")
    parser.add_argument("--host",   default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port",   type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", default=settings.DEBUG,
                        help="Enable auto-reload on code changes (default: mirrors DEBUG setting)")
    args = parser.parse_args()

    print(f"\n  ⚕  ClinSight — {settings.APP_NAME} v{settings.APP_VERSION}")
    print(f"  ⚙️  API      →  http://{args.host}:{args.port}")
    print(f"  📖 Docs     →  http://{args.host}:{args.port}/api/docs")
    print(f"  📖 ReDoc    →  http://{args.host}:{args.port}/api/redoc\n")

    uvicorn.run(
        "src.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=settings.LOG_LEVEL.lower(),
    )
