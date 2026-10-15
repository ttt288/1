from app.agents import LLMAdapter
from app.schemas import RecommendationItem
from app.schemas.ai import AIResponseSummary


class ResponseAdaptationService:
    def __init__(self, llm_adapter: LLMAdapter | None = None) -> None:
        self.llm_adapter = llm_adapter

    def build_summary(
        self,
        *,
        domain: str,
        ranked: list[RecommendationItem],
        communication_profile: dict,
    ) -> str:
        if not ranked:
            return "当前没有足够候选商品生成推荐。"

        if self.llm_adapter and self.llm_adapter.enabled and self.llm_adapter.sdk_available():
            llm_summary = self._build_with_llm(domain, ranked, communication_profile)
            if llm_summary:
                return llm_summary

        return self._build_by_rules(domain, ranked, communication_profile)

    def _build_with_llm(
        self,
        domain: str,
        ranked: list[RecommendationItem],
        communication_profile: dict,
    ) -> str | None:
        compact_products = [
            {
                "rank": item.rank,
                "name": item.product.name,
                "price": item.product.price,
                "unit_price": item.product.unit_price,
                "source_type": item.product.source_type,
                "data_quality": item.product.data_quality,
                "score": item.final_score,
                "best_score_dimension": max(item.score_breakdown.items(), key=lambda pair: pair[1])[0],
                "reasons": item.suitable_reasons[:3],
                "tradeoffs": item.tradeoffs[:2],
                "risk_notes": item.risk_notes[:2],
            }
            for item in ranked[:3]
        ]
        result = self.llm_adapter.generate_json(
            schema_model=AIResponseSummary,
            system_prompt=(
                "你是 Response Adaptation Agent。根据排序结果生成中文推荐摘要。"
                "必须尊重给定排名，不要新增商品事实，不要承诺保健品疗效或医疗诊断。"
                "如果 source_type 是 external_search_archetype，只能称为搜索兜底候选方案，不能称为热榜、榜单或真实 SKU。"
                "如果 source_type 是 jd_search_result 或 llm_web_search_jd_candidate，只能称为京东搜索候选。"
                "如果 source_type 是 external_hotlist，只能称为京东榜单候选或外部榜单候选，不要称为搜索兜底。"
                "如果 data_quality 包含 price_unverified，必须提醒价格和参数待核验。"
                "语气根据 communication_profile 调整，控制在 80 字以内。"
            ),
            user_payload={
                "domain": domain,
                "communication_profile": communication_profile,
                "top_products": compact_products,
            },
        )
        if isinstance(result, AIResponseSummary):
            return result.summary
        return None

    def _build_by_rules(self, domain: str, ranked: list[RecommendationItem], communication_profile: dict) -> str:
        top = ranked[0]
        best_dimension = max(top.score_breakdown.items(), key=lambda item: item[1])[0]
        gap = f"优势最明显的是{self._score_label(best_dimension)}。"

        if domain == "supplement":
            base = f"更推荐 {top.product.name}，因为它在目标匹配、安全边界和预算之间更平衡。{gap}"
        elif domain == "general":
            base = f"当前更推荐 {top.product.name} 这个方向，因为它和本次需求更匹配。{gap}"
            if top.product.source_type == "external_search_archetype":
                base += " 这是搜索兜底候选方案，价格和参数需要再核验。"
        else:
            base = f"更推荐 {top.product.name}，因为它和当前宠物情况、预算及风险偏好更匹配。{gap}"
        return self._apply_tone(base, communication_profile.get("tone", "warm_and_practical"))

    def _score_label(self, key: str) -> str:
        return {
            "suitability": "需求适配",
            "safety": "风险安全",
            "value_match": "性价比",
            "popularity": "热度",
            "preference_match": "偏好匹配",
        }.get(key, key)

    def _apply_tone(self, text: str, tone: str) -> str:
        if tone == "cute_supportive":
            return f"我会更偏向推荐这个：{text} 先把适合和不适合分清楚，选择就安心很多。"
        if tone == "direct_rational":
            return f"结论：{text}"
        if tone == "professional_detailed":
            return f"综合适配度、风险、安全边界、预算和偏好权重后，{text}"
        if tone == "relaxed_encouraging":
            return f"{text} 思路很清楚：先避开不适合的，再选匹配度最高的。"
        return text
