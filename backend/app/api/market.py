from functools import lru_cache

from fastapi import APIRouter

from app.core.config import get_settings
from app.data_providers.local_product_provider import LocalProductProvider
from app.services.external_discovery_service import ExternalDiscoveryService
from app.services.jd_ranking_service import JDRankingService, match_entry
from app.schemas.market import RankingEntry

router = APIRouter(tags=["market"])


@lru_cache
def get_market_service():
    return JDRankingService(get_settings().data_dir)


@lru_cache
def get_external_discovery_service():
    return ExternalDiscoveryService(get_settings().data_dir)


def with_matches(result):
    products = LocalProductProvider(get_settings().data_dir).list_products("pet_food")
    snapshot = result.get("snapshot")
    if snapshot:
        for row in snapshot["entries"]:
            product, reason = match_entry(RankingEntry.model_validate(row), products)
            row["matched_product_id"] = product.id if product else None
            row["match_reason"] = reason
        result["matched_count"] = sum(bool(row["matched_product_id"]) for row in snapshot["entries"])
    else:
        result["matched_count"] = 0
    result["refreshing"] = get_market_service()._lock.locked()
    return result


@router.get("/market/jd")
def market_status():
    return with_matches(get_market_service().status())


@router.post("/market/jd/refresh")
def refresh_market(force: bool = False):
    return with_matches(get_market_service().refresh(force=force))


@router.get("/market/jd/discover")
def discover_market(query: str, domain: str = "general", scenario: str | None = None, refresh: bool = False):
    return get_external_discovery_service().discover(
        query=query,
        domain=domain,
        scenario=scenario,
        refresh=refresh,
    )
