import json
from pathlib import Path
from typing import Any

from app.data_providers.base import ProductSearchProvider
from app.schemas import ProductOffer


class LocalProductProvider(ProductSearchProvider):
    provider_name = "local_seed_dataset"

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._ranking_signals = self._load_ranking_signals()

    def _load_ranking_signals(self) -> dict[str, dict[str, Any]]:
        path = self.data_dir / "market_sources" / "jd_pet_food_ranking_signals.json"
        if not path.exists():
            return {}

        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)

        signals: dict[str, dict[str, Any]] = {}
        for signal in raw.get("signals", []):
            product_id = signal.get("product_id")
            if not product_id:
                continue
            signals[product_id] = {
                **signal,
                "source_name": raw.get("source_name"),
                "source_url": raw.get("source_url"),
                "observed_at": raw.get("observed_at"),
            }
        return signals

    def list_products(self, domain: str) -> list[ProductOffer]:
        filename = "supplements.json" if domain == "supplement" else f"{domain}.json"
        path = self.data_dir / "products" / filename
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as file:
            raw_products = json.load(file)
        products = [ProductOffer.model_validate(item) for item in raw_products]
        if domain != "pet_food":
            return products
        return [self._apply_ranking_signal(product) for product in products]

    def _apply_ranking_signal(self, product: ProductOffer) -> ProductOffer:
        signal = self._ranking_signals.get(product.id)
        # Brand similarity does not establish SKU or package identity.
        if not signal or not (
            signal.get("match_status") == "verified_exact_sku"
            and product.jd_sku
            and product.jd_sku == signal.get("jd_sku")
            and product.spec == signal.get("spec")
        ):
            return product

        features = dict(product.features)
        market_signals = list(features.get("market_signals", []))
        for tag in signal.get("tags", []):
            if tag not in market_signals:
                market_signals.append(tag)
        rank_label = f"京东猫粮榜第{signal['rank']}名"
        if rank_label not in market_signals:
            market_signals.append(rank_label)
        features["market_signals"] = market_signals
        features["jd_ranking_signal"] = {
            "rank": signal["rank"],
            "review_count_label": signal.get("review_count_label"),
            "observed_at": signal.get("observed_at"),
        }

        return product.model_copy(
            update={
                "features": features,
                "popularity_score": max(product.popularity_score, signal.get("popularity_score", 0)),
                "source_type": "jd_ranking_signal+curated_product_data",
                "source_url": signal.get("source_url") or product.source_url,
                "source_name": signal.get("source_name"),
                "market_rank": signal.get("rank"),
                "review_count_label": signal.get("review_count_label"),
                "data_quality": "rank_verified_price_unverified",
                "ranking_observed_at": signal.get("observed_at"),
            }
        )

    def search_products(self, domain: str, query: str, limit: int = 10) -> list[ProductOffer]:
        products = self.list_products(domain)
        query_lower = query.lower()
        query_tokens = self._query_tokens(query_lower)

        def score(product: ProductOffer) -> float:
            text = " ".join(
                [
                    product.name,
                    product.brand,
                    " ".join(product.strengths),
                    " ".join(product.target_users),
                    " ".join(str(value) for value in product.features.values()),
                ]
            ).lower()
            keyword_score = sum(1 for token in query_tokens if token and token in text)
            jd_rank_bonus = max(0, 12 - product.market_rank) if product.market_rank else 0
            return keyword_score * 20 + product.popularity_score + jd_rank_bonus

        return sorted(products, key=score, reverse=True)[:limit]

    def _query_tokens(self, query: str) -> list[str]:
        tokens = [token for token in query.split() if token]
        domain_tokens = [
            "幼猫", "奶猫", "奶糕", "成猫", "老年猫", "全阶段",
            "美毛", "泪痕", "去泪痕", "鱼油", "三文鱼",
            "肠胃", "软便", "呕吐", "玻璃胃", "消化",
            "挑食", "爱吃", "适口性", "冻干",
            "高品质", "成分更好", "高蛋白", "无谷", "进口品牌",
            "性价比", "质价比", "便宜", "多猫", "大包装",
            "益生菌", "鱼油", "叶黄素", "钙", "维生素",
        ]
        tokens.extend(token for token in domain_tokens if token in query)
        return list(dict.fromkeys(tokens)) or [query]
