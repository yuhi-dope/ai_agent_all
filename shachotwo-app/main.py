"""シャチョツー（社長2号）— FastAPI entry point."""
import os
from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import (
    auth_setup,
    company,
    users,
    invitations,
    ingestion,
    knowledge,
    digital_twin,
    proactive,
    execution,
    connector,
    dashboard,
    genome,
)
from routers.bpo import construction as bpo_construction
from routers import onboarding


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    yield


app = FastAPI(
    title="シャチョツー API",
    description="会社のデジタルツインSaaS",
    version="0.1.0",
    lifespan=lifespan,
)

cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:3001,https://shachotwo-web-769477129850.asia-northeast1.run.app",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1 domain = 1 router file
app.include_router(auth_setup.router, prefix="/api/v1", tags=["auth"])
app.include_router(company.router, prefix="/api/v1", tags=["company"])
app.include_router(users.router, prefix="/api/v1", tags=["users"])
app.include_router(invitations.router, prefix="/api/v1", tags=["invitations"])
app.include_router(ingestion.router, prefix="/api/v1", tags=["ingestion"])
app.include_router(knowledge.router, prefix="/api/v1", tags=["knowledge"])
app.include_router(digital_twin.router, prefix="/api/v1", tags=["digital_twin"])
app.include_router(proactive.router, prefix="/api/v1", tags=["proactive"])
app.include_router(execution.router, prefix="/api/v1", tags=["execution"])
app.include_router(connector.router, prefix="/api/v1", tags=["connector"])
app.include_router(dashboard.router, prefix="/api/v1", tags=["dashboard"])
app.include_router(genome.router, prefix="/api/v1", tags=["genome"])

# BPO routers
app.include_router(bpo_construction.router, prefix="/api/v1/bpo/construction", tags=["construction-bpo"])
app.include_router(onboarding.router, prefix="/api/v1", tags=["onboarding"])


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/debug/env")
async def debug_env():
    """デバッグ用（本番では無効化を検討）"""
    if os.environ.get("ENVIRONMENT") == "production":
        return {"status": "debug disabled in production"}
    url = os.environ.get("SUPABASE_URL", "")
    cors = os.environ.get("CORS_ORIGINS", "NOT_SET")
    return {
        "supabase_url_set": bool(url),
        "supabase_url_prefix": url[:30] + "..." if len(url) > 30 else url,
        "service_key_set": bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")),
        "cors_origins": cors,
    }
