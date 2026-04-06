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
# ── コア6業界 + 共通 BPOルーター ──
from routers.bpo import construction as bpo_construction
from routers.bpo import manufacturing as bpo_manufacturing
from routers.bpo import clinic as bpo_clinic
from routers.bpo import nursing as bpo_nursing
from routers.bpo import realestate as bpo_realestate
from routers.bpo import logistics as bpo_logistics
from routers.bpo import common as bpo_common
from routers.bpo import backoffice as bpo_backoffice
from routers.bpo import professional as bpo_professional

# ── 凍結業種（パートナー主導で復活）──
# from routers.bpo import dental as bpo_dental          # 歯科 → 医療・福祉に将来統合
# from routers.bpo import restaurant as bpo_restaurant    # 飲食
# from routers.bpo import beauty as bpo_beauty            # 美容
# from routers.bpo import auto_repair as bpo_auto_repair  # 自動車整備
# from routers.bpo import hotel as bpo_hotel              # ホテル
# from routers.bpo import ecommerce as bpo_ecommerce      # EC
# from routers.bpo import staffing as bpo_staffing        # 人材派遣
# from routers.bpo import architecture as bpo_architecture  # 建築設計
# from routers.bpo import pharmacy as bpo_pharmacy        # 調剤薬局 → 医療・福祉に将来統合
from routers import onboarding
from routers import accuracy
from routers import consent
from routers import visualization
from routers import marketing
from routers import jobs as jobs_router
from routers import sales
from routers import crm
from routers import support
from routers import upsell
from routers import learning
from routers import webhooks
from routers import webhooks_gws
from routers import model_status
from routers import billing
from routers import approvals
from routers import knowledge_graph
from routers import mfa as mfa_router_module
from routers import partner as partner_router_module
from routers import security_admin as security_admin_router

# SOC2準備: セキュリティミドルウェア
from security.audit_middleware import AuditLogMiddleware
from security.headers_middleware import SecurityHeadersMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # 営業BPOスケジューラ起動（ENABLE_SALES_SCHEDULER=1 の場合のみ）
    if os.environ.get("ENABLE_SALES_SCHEDULER", "").lower() in ("1", "true", "yes"):
        from workers.bpo.sales.scheduler import start_scheduler
        await start_scheduler()
    # BPOオーケストレータ起動（ENABLE_BPO_ORCHESTRATOR=1 の場合のみ）
    if os.environ.get("ENABLE_BPO_ORCHESTRATOR", "").lower() in ("1", "true", "yes"):
        from workers.bpo.manager.orchestrator import start_orchestrator
        await start_orchestrator()
    yield
    # BPOオーケストレータ停止
    try:
        from workers.bpo.manager.orchestrator import stop_orchestrator
        await stop_orchestrator()
    except Exception:
        pass
    # 営業BPOスケジューラ停止
    try:
        from workers.bpo.sales.scheduler import stop_scheduler
        await stop_scheduler()
    except Exception:
        pass


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

_env = os.environ.get("ENVIRONMENT", "development")
_allowed_methods = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"] if _env in ("production", "staging") else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cors_origins],
    allow_credentials=True,
    allow_methods=_allowed_methods,
    allow_headers=["*"],
)

# SOC2準備: セキュリティヘッダー（全レスポンスに付与）
app.add_middleware(SecurityHeadersMiddleware)

# SOC2準備: 監査ログミドルウェア（ip_address/user_agentをリクエストstateに付与）
app.add_middleware(AuditLogMiddleware)

# セキュリティ: グローバル例外ハンドラ（本番で内部エラー情報を隠蔽）
from security.error_handler import install_error_handlers
install_error_handlers(app)

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

# ── コア6業界 + 共通 BPOルーター ──
app.include_router(bpo_construction.router, prefix="/api/v1/bpo/construction", tags=["construction-bpo"])
app.include_router(bpo_manufacturing.router, prefix="/api/v1/bpo/manufacturing", tags=["manufacturing-bpo"])
app.include_router(bpo_clinic.router, prefix="/api/v1/bpo/clinic", tags=["clinic-bpo"])
app.include_router(bpo_nursing.router, prefix="/api/v1/bpo/nursing", tags=["nursing-bpo"])
app.include_router(bpo_realestate.router, prefix="/api/v1/bpo/realestate", tags=["realestate-bpo"])
app.include_router(bpo_logistics.router, prefix="/api/v1/bpo/logistics", tags=["logistics-bpo"])
app.include_router(bpo_common.router, prefix="/api/v1/bpo/common", tags=["common-bpo"])
app.include_router(bpo_backoffice.router, prefix="/api/v1/bpo/backoffice", tags=["backoffice-bpo"])
app.include_router(bpo_professional.router, prefix="/api/v1/bpo/professional", tags=["professional-bpo"])

# ── 凍結業種（パートナー主導で復活）──
# app.include_router(bpo_dental.router, prefix="/api/v1/bpo/dental", tags=["dental-bpo"])
# app.include_router(bpo_restaurant.router, prefix="/api/v1/bpo/restaurant", tags=["restaurant-bpo"])
# app.include_router(bpo_beauty.router, prefix="/api/v1/bpo/beauty", tags=["beauty-bpo"])
# app.include_router(bpo_auto_repair.router, prefix="/api/v1/bpo/auto-repair", tags=["auto-repair-bpo"])
# app.include_router(bpo_hotel.router, prefix="/api/v1/bpo/hotel", tags=["hotel-bpo"])
# app.include_router(bpo_ecommerce.router, prefix="/api/v1/bpo/ecommerce", tags=["ecommerce-bpo"])
# app.include_router(bpo_staffing.router, prefix="/api/v1/bpo/staffing", tags=["staffing-bpo"])
# app.include_router(bpo_architecture.router, prefix="/api/v1/bpo/architecture", tags=["architecture-bpo"])
# app.include_router(bpo_professional.router, prefix="/api/v1/bpo/professional", tags=["professional-bpo"])
# app.include_router(bpo_pharmacy.router, prefix="/api/v1/bpo/pharmacy", tags=["pharmacy-bpo"])
app.include_router(onboarding.router, prefix="/api/v1", tags=["onboarding"])
app.include_router(accuracy.router, prefix="/api/v1", tags=["accuracy"])
app.include_router(consent.router, prefix="/api/v1", tags=["consent"])
app.include_router(visualization.router, prefix="/api/v1", tags=["visualization"])

# Marketing / SFA / CRM / CS / Upsell / Learning / Webhooks
app.include_router(marketing.router, prefix="/api/v1", tags=["marketing"])
app.include_router(jobs_router.router, prefix="/api/v1", tags=["jobs"])
app.include_router(sales.router, prefix="/api/v1", tags=["sales"])
app.include_router(crm.router, prefix="/api/v1", tags=["crm"])
app.include_router(support.router, prefix="/api/v1", tags=["support"])
app.include_router(upsell.router, prefix="/api/v1", tags=["upsell"])
app.include_router(learning.router, prefix="/api/v1", tags=["learning"])
app.include_router(webhooks.router, prefix="/api/v1", tags=["webhooks"])
app.include_router(webhooks_gws.router, prefix="/api/v1", tags=["webhooks-gws"])
app.include_router(model_status.router, prefix="/api/v1", tags=["model-status"])
app.include_router(billing.router, prefix="/api/v1", tags=["billing"])
app.include_router(approvals.router, prefix="/api/v1", tags=["approvals"])
app.include_router(knowledge_graph.router, prefix="/api/v1", tags=["knowledge-graph"])
app.include_router(mfa_router_module.router, prefix="/api/v1", tags=["mfa"])
app.include_router(partner_router_module.router, prefix="/api/v1", tags=["partner", "marketplace"])
app.include_router(security_admin_router.router, prefix="/api/v1", tags=["security-admin"])


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/debug/env")
async def debug_env():
    """デバッグ用（本番・ステージング以外でのみ有効）"""
    env = os.environ.get("ENVIRONMENT", "development")
    if env in ("production", "staging"):
        return {"status": "debug disabled"}
    # development のみ: 設定有無のみ返す（値は返さない）
    return {
        "environment": env,
        "supabase_url_set": bool(os.environ.get("SUPABASE_URL")),
        "service_key_set": bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
        "gemini_key_set": bool(os.environ.get("GEMINI_API_KEY")),
        "cors_origins_set": bool(os.environ.get("CORS_ORIGINS")),
        # URL・キーの値は一切返さない
    }
