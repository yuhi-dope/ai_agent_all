"""マーケAI — エントリポイント"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from lp.app import router as lp_router

app = FastAPI(title="shachotwo-マーケAI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(lp_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
