import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routers import detector
from app.services.dex_screener import dex_screener_client
from app.services.helius import helius_client
from app.services.solana_rpc import solana_rpc_client

settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.app_name} v{settings.version}")
    logger.info(f"Helius API Key configured: {bool(settings.helius_api_key)}")
    yield
    logger.info("Shutting down...")
    await dex_screener_client.close()
    await helius_client.close()
    await solana_rpc_client.close()


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="Detects recently launched Solana meme coins and provides trust scores to help avoid rug pulls",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(detector.router, prefix="/api/v1", tags=["Meme Coin Detector"])


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": settings.version,
        "status": "running",
        "endpoints": {
            "meme_detector": "/api/v1/solana-meme-detector",
            "docs": "/docs",
            "health": "/health"
        }
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": settings.version
    }


@app.post("/api/v1/solana-meme-detector/test")
async def test_apix_format(request: Request):
    raw_body = await request.body()
    headers = dict(request.headers)

    return {
        "received": {
            "headers": headers,
            "raw_body": raw_body.decode() if raw_body else None,
            "content_type": headers.get("content-type")
        },
        "message": "Use this endpoint to test what APIX sends"
    }
