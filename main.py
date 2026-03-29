import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from config import settings
from database import connect_db, disconnect_db
from middleware import RateLimitMiddleware, RequestLoggingMiddleware
from routes import auth, users, expenses, approvals, payments, ocr, notifications, company

# ─── Logging Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─── Lifespan (startup / shutdown) ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting Executive Ledger API...")
    # Ensure upload directory exists
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(f"{settings.upload_dir}/receipts").mkdir(parents=True, exist_ok=True)
    Path(f"{settings.upload_dir}/ocr_temp").mkdir(parents=True, exist_ok=True)

    await connect_db()
    logger.info("✅ Application ready")
    yield
    logger.info("🛑 Shutting down...")
    await disconnect_db()


# ─── App Initialization ────────────────────────────────────────────────────────
app = FastAPI(
    title="Executive Ledger — Expense Management API",
    description="""
## Smart Expense Reimbursement System

Production-ready backend for multi-level expense approval with OCR and Stripe payments.

### Key Features
- 🔐 JWT Authentication with RBAC (Admin / Manager / Employee)
- 📸 OCR Receipt Scanning (Tesseract + OpenCV)
- 💱 Multi-currency with real-time conversion
- ✅ Dynamic multi-step approval workflows
- 💳 Stripe payment integration
- 🔔 In-app notification system
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ─── Middleware ────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Lock down in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    RateLimitMiddleware,
    requests_per_window=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window,
)
app.add_middleware(RequestLoggingMiddleware)


# ─── Static File Serving ───────────────────────────────────────────────────────
if Path(settings.upload_dir).exists():
    app.mount("/files", StaticFiles(directory=settings.upload_dir), name="uploads")


# ─── Global Exception Handler ──────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )


# ─── Routes ────────────────────────────────────────────────────────────────────
app.include_router(auth.router,          prefix="/api/v1")
app.include_router(users.router,         prefix="/api/v1")
app.include_router(expenses.router,      prefix="/api/v1")
app.include_router(approvals.router,     prefix="/api/v1")
app.include_router(payments.router,      prefix="/api/v1")
app.include_router(ocr.router,           prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(company.router,       prefix="/api/v1")


# ─── Health Check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health():
    from database import client
    try:
        await client.admin.command("ping")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    return {
        "status": "healthy",
        "version": "1.0.0",
        "database": db_status,
    }


@app.get("/", tags=["System"])
async def root():
    return {
        "message": "Executive Ledger API",
        "docs": "/docs",
        "health": "/health",
        "version": "1.0.0",
    }