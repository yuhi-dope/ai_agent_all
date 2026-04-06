"""BPO業界別ルーター"""
from fastapi import APIRouter

from routers.bpo.construction import router as construction_router
from routers.bpo.manufacturing import router as manufacturing_router
from routers.bpo.dental import router as dental_router
from routers.bpo.restaurant import router as restaurant_router
from routers.bpo.common import router as common_router
from routers.bpo.realestate import router as realestate_router
from routers.bpo.professional import router as professional_router
from routers.bpo.nursing import router as nursing_router
from routers.bpo.logistics import router as logistics_router
from routers.bpo.clinic import router as clinic_router
from routers.bpo.pharmacy import router as pharmacy_router
from routers.bpo.beauty import router as beauty_router
from routers.bpo.auto_repair import router as auto_repair_router
from routers.bpo.hotel import router as hotel_router
from routers.bpo.ecommerce import router as ecommerce_router
from routers.bpo.staffing import router as staffing_router
from routers.bpo.architecture import router as architecture_router
from routers.bpo.wholesale import router as wholesale_router

bpo_router = APIRouter(prefix="/bpo", tags=["BPO"])

bpo_router.include_router(construction_router, prefix="/construction", tags=["建設業"])
bpo_router.include_router(manufacturing_router, prefix="/manufacturing", tags=["製造業"])
bpo_router.include_router(dental_router, prefix="/dental", tags=["歯科"])
bpo_router.include_router(restaurant_router, prefix="/restaurant", tags=["飲食業"])
bpo_router.include_router(common_router, prefix="/common", tags=["共通BPO"])
bpo_router.include_router(realestate_router, prefix="/realestate", tags=["不動産管理"])
bpo_router.include_router(professional_router, prefix="/professional", tags=["士業"])
bpo_router.include_router(nursing_router, prefix="/nursing", tags=["介護・福祉"])
bpo_router.include_router(logistics_router, prefix="/logistics", tags=["物流・運送"])
bpo_router.include_router(clinic_router, prefix="/clinic", tags=["医療クリニック"])
bpo_router.include_router(pharmacy_router, prefix="/pharmacy", tags=["調剤薬局"])
bpo_router.include_router(beauty_router, prefix="/beauty", tags=["美容・エステ"])
bpo_router.include_router(auto_repair_router, prefix="/auto-repair", tags=["自動車整備"])
bpo_router.include_router(hotel_router, prefix="/hotel", tags=["ホテル・旅館"])
bpo_router.include_router(ecommerce_router, prefix="/ecommerce", tags=["EC・小売"])
bpo_router.include_router(staffing_router, prefix="/staffing", tags=["人材派遣"])
bpo_router.include_router(architecture_router, prefix="/architecture", tags=["建築設計"])
bpo_router.include_router(wholesale_router, prefix="/wholesale", tags=["卸売業"])

# レート制限はエンドポイント内で check_rate_limit(user.company_id, "bpo_pipeline") を呼ぶ
# APIRouterにはmiddlewareメソッドがないため、app-levelまたはエンドポイント内で適用
