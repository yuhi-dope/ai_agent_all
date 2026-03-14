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
    ingestion,
    knowledge,
    digital_twin,
    proactive,
    execution,
    connector,
    dashboard,
    genome,
)


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
    "http://localhost:3000,http://localhost:3001",
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
app.include_router(ingestion.router, prefix="/api/v1", tags=["ingestion"])
app.include_router(knowledge.router, prefix="/api/v1", tags=["knowledge"])
app.include_router(digital_twin.router, prefix="/api/v1", tags=["digital_twin"])
app.include_router(proactive.router, prefix="/api/v1", tags=["proactive"])
app.include_router(execution.router, prefix="/api/v1", tags=["execution"])
app.include_router(connector.router, prefix="/api/v1", tags=["connector"])
app.include_router(dashboard.router, prefix="/api/v1", tags=["dashboard"])
app.include_router(genome.router, prefix="/api/v1", tags=["genome"])


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}
