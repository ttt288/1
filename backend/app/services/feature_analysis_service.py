from collections import Counter

from app.schemas import FeatureAnalysis, FeatureInsight, ProductOffer


class FeatureAnalysisService:
    def analyze(self, domain: str, products: list[ProductOffer]) -> FeatureAnalysis:
        if domain == "pet_food":
            insights = self._pet_food_insights(products)
        elif domain == "supplement":
            insights = self._supplement_insights(products)
        else:
            insights = self._general_insights(products)
        return FeatureAnalysis(domain=domain, total_products=len(products), insights=insights)

    def _pet_food_insights(self, products: list[ProductOffer]) -> list[FeatureInsight]:
        insights: list[FeatureInsight] = []
        life_stage_counts = self._feature_counts(products, "life_stage")
        palatability_counts = self._feature_counts(products, "palatability")
        digestive_count = sum(1 for item in products if item.features.get("digestive_support"))
        function_terms = self._term_counts(products, ["美毛", "泪痕", "冻干", "幼猫", "肠胃", "性价比", "成分更好"])
        unit_prices = [item.unit_price for item in products if item.unit_price]

        if len(life_stage_counts) > 1:
            insights.append(
                FeatureInsight(
                    dimension="life_stage",
                    label="年龄阶段差异",
                    summary=self._counter_summary(life_stage_counts, "候选商品覆盖不同年龄阶段"),
                    values=list(life_stage_counts.keys()),
                    product_count=len(products),
                    related_question_ids=["pet_age_stage"],
                    importance="high",
                )
            )

        if unit_prices:
            min_price = min(unit_prices)
            max_price = max(unit_prices)
            if max_price / min_price >= 1.8:
                insights.append(
                    FeatureInsight(
                        dimension="unit_price",
                        label="单价跨度明显",
                        summary=f"候选商品参考单价约从 ¥{min_price:.2f}/kg 到 ¥{max_price:.2f}/kg，长期喂养成本差异明显。",
                        values=[f"¥{min_price:.2f}", f"¥{max_price:.2f}"],
                        product_count=len(products),
                        related_question_ids=["budget_range", "priority"],
                        importance="high",
                    )
                )

        if digestive_count and digestive_count != len(products):
            insights.append(
                FeatureInsight(
                    dimension="digestive_support",
                    label="肠胃支持差异",
                    summary=f"{digestive_count}/{len(products)} 个候选商品强调肠胃支持，需要确认是否软便或肠胃敏感。",
                    values=["有肠胃支持", "普通配方"],
                    product_count=len(products),
                    related_question_ids=["digestive_issue"],
                    importance="high",
                )
            )

        if len(palatability_counts) > 1:
            insights.append(
                FeatureInsight(
                    dimension="palatability",
                    label="适口性差异",
                    summary=self._counter_summary(palatability_counts, "候选商品适口性分布不同"),
                    values=list(palatability_counts.keys()),
                    product_count=len(products),
                    related_question_ids=["picky_eater"],
                    importance="medium",
                )
            )

        if function_terms:
            insights.append(
                FeatureInsight(
                    dimension="functional_claims",
                    label="功能诉求差异",
                    summary=self._counter_summary(function_terms, "候选商品卖点集中在多个方向"),
                    values=list(function_terms.keys()),
                    product_count=sum(function_terms.values()),
                    related_question_ids=["priority"],
                    importance="high",
                )
            )

        return insights[:6]

    def _general_insights(self, products: list[ProductOffer]) -> list[FeatureInsight]:
        insights: list[FeatureInsight] = []
        dimension_counts = self._feature_counts(products, "decision_dimension")
        claim_terms = Counter(
            claim
            for product in products
            for claim in product.features.get("detected_claims", [])
        )
        unverified_count = sum(1 for item in products if item.features.get("price_unverified"))

        if len(dimension_counts) > 1:
            insights.append(
                FeatureInsight(
                    dimension="decision_dimension",
                    label="决策维度差异",
                    summary=self._counter_summary(dimension_counts, "当前候选方案主要按不同决策维度区分"),
                    values=list(dimension_counts.keys()),
                    product_count=len(products),
                    related_question_ids=["product_claim", "priority"],
                    importance="high",
                )
            )

        if len(claim_terms) >= 2:
            top_claims = dict(claim_terms.most_common(8))
            insights.append(
                FeatureInsight(
                    dimension="detected_claims",
                    label="核心卖点差异",
                    summary=self._counter_summary(Counter(top_claims), "候选方案的主要卖点分布不同"),
                    values=list(top_claims.keys()),
                    product_count=sum(top_claims.values()),
                    related_question_ids=["product_claim"],
                    importance="high",
                )
            )

        if unverified_count:
            insights.append(
                FeatureInsight(
                    dimension="data_quality",
                    label="价格参数待核验",
                    summary=f"{unverified_count}/{len(products)} 个候选来自搜索兜底或外部热榜，价格、SKU 和参数不参与硬排序。",
                    values=["价格待核验", "参数待核验"],
                    product_count=unverified_count,
                    related_question_ids=["budget_range"],
                    importance="medium",
                )
            )

        return insights[:6]

    def _supplement_insights(self, products: list[ProductOffer]) -> list[FeatureInsight]:
        insights: list[FeatureInsight] = []
        category_counts = self._feature_counts(products, "category")
        goal_counts = self._feature_counts(products, "health_goal")
        allergen_count = sum(1 for item in products if item.features.get("contains_allergen"))
        quality_counts = self._feature_counts(products, "ingredient_quality")
        unit_prices = [item.unit_price for item in products if item.unit_price]

        if len(goal_counts) > 1:
            insights.append(
                FeatureInsight(
                    dimension="health_goal",
                    label="目标需求差异",
                    summary=self._counter_summary(goal_counts, "候选保健品覆盖不同目标需求"),
                    values=list(goal_counts.keys()),
                    product_count=len(products),
                    related_question_ids=["health_goal"],
                    importance="high",
                )
            )

        if len(category_counts) > 1:
            insights.append(
                FeatureInsight(
                    dimension="category",
                    label="品类差异",
                    summary=self._counter_summary(category_counts, "候选商品并非同一细分类目"),
                    values=list(category_counts.keys()),
                    product_count=len(products),
                    related_question_ids=["health_goal"],
                    importance="high",
                )
            )

        if allergen_count:
            insights.append(
                FeatureInsight(
                    dimension="allergen",
                    label="过敏风险差异",
                    summary=f"{allergen_count}/{len(products)} 个候选商品可能涉及鱼类、乳制品等过敏相关成分。",
                    values=["含潜在过敏源", "未标记过敏源"],
                    product_count=len(products),
                    related_question_ids=["allergy", "medical_context"],
                    importance="high",
                )
            )

        if len(quality_counts) > 1:
            insights.append(
                FeatureInsight(
                    dimension="ingredient_quality",
                    label="成分清晰度差异",
                    summary=self._counter_summary(quality_counts, "候选商品成分清晰度不同"),
                    values=list(quality_counts.keys()),
                    product_count=len(products),
                    related_question_ids=["health_goal", "response_tone"],
                    importance="medium",
                )
            )

        if unit_prices:
            min_price = min(unit_prices)
            max_price = max(unit_prices)
            if max_price / min_price >= 1.8:
                insights.append(
                    FeatureInsight(
                        dimension="daily_cost",
                        label="每日成本差异",
                        summary=f"候选商品单粒/袋成本约从 ¥{min_price:.2f} 到 ¥{max_price:.2f}。",
                        values=[f"¥{min_price:.2f}", f"¥{max_price:.2f}"],
                        product_count=len(products),
                        related_question_ids=["budget_range"],
                        importance="medium",
                    )
                )

        return insights[:6]

    def _feature_counts(self, products: list[ProductOffer], key: str) -> Counter:
        return Counter(str(item.features.get(key)) for item in products if item.features.get(key))

    def _term_counts(self, products: list[ProductOffer], terms: list[str]) -> Counter:
        counter: Counter = Counter()
        for product in products:
            text = " ".join(product.strengths + product.weaknesses + product.target_users)
            for term in terms:
                if term in text:
                    counter[term] += 1
        return counter

    def _counter_summary(self, counter: Counter, prefix: str) -> str:
        parts = [f"{key} {value} 个" for key, value in counter.most_common(4)]
        return f"{prefix}：" + "，".join(parts) + "。"
