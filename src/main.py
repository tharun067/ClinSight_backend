"""
Main FastAPI application for ClinSight with lifecycle management and routing.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
import time
import os

from src.config import settings
from src.database.postgres import init_db, close_db
from src.database.vector_db import init_vector_db, close_vector_db
from src.services.embedding import init_embedding_service, close_embedding_service
from src.routers import auth, patients, diagnostic, documents, imaging, labs, notes, audit

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager for startup and shutdown events.
    """
    # Startup
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    try:
        # Initialize PostgreSQL
        await init_db()
        logger.info("PostgreSQL initialized")
        
        # Initialize FAISS vector database
        await init_vector_db()
        logger.info("FAISS vector database initialized")
        
        # Initialize embedding service (BioBERT + BiomedCLIP)
        await init_embedding_service()
        logger.info("Embedding service initialized")
        
    except Exception as e:
        logger.error(f"Startup failed: {e}", exc_info=True)
        raise

    yield

    # Shutdown 
    logger.info("Shutting down application")

    try:
        await close_embedding_service()
        await close_vector_db()
        await close_db()
    except Exception as e:
        logger.error(f"Shutdown error: {e}", exc_info=True)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-powered medical diagnostic support system with multi-modal analysis and hybrid RAG",
    debug=settings.DEBUG,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request Logging Middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware to log incoming requests and their processing time."""
    start_time = time.time()
    
    # Log request
    logger.info(f"Incoming request: {request.method} {request.url.path}")
    
    response = await call_next(request)
    
    # Log response time
    process_time = (time.time() - start_time) * 1000
    logger.info(f"{request.method} {request.url.path} completed in {process_time:.2f}ms - Status: {response.status_code}")
    response.headers["X-Process-Time-ms"] = f"{process_time:.2f}"
    
    return response

# Global Exception Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions globally."""
    logger.error(f"Unhandled error on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal Server Error",
            "path": str(request.url.path)
        }
    )

# Mount static files for uploads
if os.path.exists(settings.UPLOAD_DIR):
    app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Include Routers
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(patients.router, prefix="/api/patients", tags=["Patients"])
app.include_router(diagnostic.router, prefix="/api/diagnostic", tags=["AI Diagnostic Support"])
app.include_router(documents.router, prefix="/api/documents", tags=["Documents"])
app.include_router(imaging.router, prefix="/api/imaging", tags=["Imaging Studies"])
app.include_router(labs.router, prefix="/api/labs", tags=["Labs & Vitals"])
app.include_router(notes.router, prefix="/api/notes", tags=["Clinical Notes"])
app.include_router(audit.router, prefix="/api/audit", tags=["Audit Logs"])

# Health check endpoints
@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint to verify application is running."""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "database": "connected"
    }

@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with basic application info."""
    return {
        "message": f"Welcome to {settings.APP_NAME}!",
        "version": settings.APP_VERSION,
        "docs": "/api/docs",
        "health": "/health"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )
