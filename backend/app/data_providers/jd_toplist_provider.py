from dataclasses import dataclass
from pathlib import Path

from app.data_providers.base import ProductSearchProvider
from app.schemas import ProductOffer
from app.data_providers.local_product_provider import LocalProductProvider
from app.services.jd_ranking_service import JDRankingService, match_entry


@dataclass(frozen=True)
class JDTopListProviderConfig:
    enabled: bool = False
    cache_ttl_seconds: int = 86400
    max_pages: int = 1


class JDTopListProvider(ProductSearchProvider):
    """Read exact SKU matches from the independently refreshed JD snapshot."""

    provider_name = "jd_toplist"

    def __init__(self, data_dir: Path | None = None, config: JDTopListProviderConfig | None = None) -> None:
        self.config = config or JDTopListProviderConfig(enabled=data_dir is not None)
        self.data_dir = data_dir
        self.market = JDRankingService(data_dir) if data_dir else None

    def cache_version(self):
        if not self.market:
            return "disabled"
        paths = [self.market.snapshot_path, self.data_dir / "products" / "pet_food.json"]
        return tuple(path.stat().st_mtime_ns if path.exists() else 0 for path in paths)

    def search_products(self, domain: str, query: str, limit: int = 10) -> list[ProductOffer]:
        if not self.config.enabled or not self.market or domain != "pet_food":
            return []
        snapshot = self.market.snapshot()
        if not snapshot:
            return []
        products = LocalProductProvider(self.data_dir).list_products(domain)
        results = []
        for entry in snapshot.entries:
            product, _ = match_entry(entry, products)
            if product:
                results.append(product.model_copy(update={
                    "market_rank": entry.rank,
                    "popularity_score": max(0, 100 - entry.rank),
                    "source_name": snapshot.source_name,
                    "source_url": entry.product_url,
                    "review_count_label": entry.review_count_label,
                    "ranking_observed_at": snapshot.observed_at,
                    "data_quality": "rank_verified_price_unverified",
                }))
        return results[:limit]
