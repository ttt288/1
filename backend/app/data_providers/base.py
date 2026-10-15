from abc import ABC, abstractmethod

from app.schemas import ProductOffer


class ProductSearchProvider(ABC):
    """Common interface for local, market-research, and future web providers."""

    provider_name: str

    @abstractmethod
    def search_products(self, domain: str, query: str, limit: int = 10) -> list[ProductOffer]:
        """Return normalized product offers for the decision workflow."""
