from app.data_providers import ProductSearchProvider
from app.schemas import ProductOffer
from app.services.cache_service import TTLCache


class ProductSearchService:
    def __init__(self, providers: list[ProductSearchProvider]) -> None:
        self.providers = providers
        self.cache: TTLCache[list[ProductOffer]] = TTLCache(ttl_seconds=600)
        self.last_cache_hit = False

    def search(self, domain: str, query: str, limit: int = 10) -> list[ProductOffer]:
        versions = [provider.cache_version() for provider in self.providers if hasattr(provider, "cache_version")]
        cache_key = f"{domain}:{query.strip()}:{limit}:{versions}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            self.last_cache_hit = True
            return cached

        self.last_cache_hit = False
        seen: set[str] = set()
        results: list[ProductOffer] = []

        for provider in self.providers:
            for product in provider.search_products(domain=domain, query=query, limit=limit):
                if product.id in seen:
                    continue
                seen.add(product.id)
                results.append(product)
                if len(results) >= limit:
                    self.cache.set(cache_key, results)
                    return results

        self.cache.set(cache_key, results)
        return results
