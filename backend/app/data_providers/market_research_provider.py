from app.data_providers.base import ProductSearchProvider
from app.data_providers.local_product_provider import LocalProductProvider
from app.schemas import ProductOffer


class MarketResearchProvider(ProductSearchProvider):
    """Provider facade for curated mainland China market research seed data.

    The current implementation intentionally reads normalized seed data. Future
    implementations can replace `_local` with collectors for JD ranking pages,
    pet industry data pages, or paid product APIs without changing agents.
    """

    provider_name = "market_research_seed"

    def __init__(self, local_provider: LocalProductProvider) -> None:
        self._local = local_provider

    def search_products(self, domain: str, query: str, limit: int = 10) -> list[ProductOffer]:
        return self._local.search_products(domain=domain, query=query, limit=limit)
