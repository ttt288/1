from app.agents import LLMAdapter
from app.schemas import ProductOffer, Question
from app.schemas.ai import AIDynamicQuestionPlan


class QuestionService:
    def __init__(self, llm_adapter: LLMAdapter | None = None) -> None:
        self.llm_adapter = llm_adapter
        self.last_mode = "rules"
        self.last_error: str | None = None

    def generate_questions(
        self,
        domain_config: dict,
        products: list[ProductOffer],
        scenario: str | None = None,
    ) -> list[Question]:
        self.last_mode = "rules"
        self.last_error = None
        dimensions = domain_config["question_dimensions"]
        relevant_dimensions = self._select_relevant_dimensions(dimensions, products, domain_config, scenario)
        if domain_config.get("domain") == "general":
            llm_questions = self._general_questions_by_llm(products, relevant_dimensions)
            if llm_questions:
                self.last_mode = "llm"
                return llm_questions
            external_question = self._general_external_question(products)
            if external_question:
                existing = [item for item in relevant_dimensions if item["id"] != external_question["id"]]
                relevant_dimensions = [external_question] + existing
        return [Question.model_validate(item) for item in relevant_dimensions[:7]]

    def _general_questions_by_llm(self, products: list[ProductOffer], fallback_dimensions: list[dict]) -> list[Question]:
        if (
            not products
            or not self.llm_adapter
            or not self.llm_adapter.enabled
            or not self.llm_adapter.sdk_available()
        ):
            return []

        compact_products = [
            {
                "rank": product.market_rank,
                "title": product.name[:120],
                "claims": product.features.get("detected_claims", [])[:8],
                "strengths": product.strengths[:3],
                "weaknesses": product.weaknesses[:2],
                "risk_tags": product.risk_tags[:2],
            }
            for product in products[:12]
        ]
        fallback_ids = [item["id"] for item in fallback_dimensions[:4]]
        result = self.llm_adapter.generate_json(
            schema_model=AIDynamicQuestionPlan,
            system_prompt=(
                "你是 Dynamic Question Agent，负责为消费决策生成最少但区分度最高的问题。"
                "你只能基于候选商品标题、已抽取卖点、缺点和风险标签生成问题，不能新增商品事实。"
                "问题必须能改变排序，避免问已经能从 query 或候选中确定的信息。"
                "优先询问候选之间差异最大的维度，例如形态、核心功能、使用场景、兼容性、续航、风险边界。"
                "如果候选没有结构化价格，不要生成具体价格区间问题，只能保留预算策略问题。"
                "每个问题 2-6 个选项，中文简短，最多 5 个问题。"
            ),
            user_payload={
                "products": compact_products,
                "has_structured_price": any(product.price > 0 or product.unit_price for product in products),
                "fallback_question_ids": fallback_ids,
                "required_common_questions": ["budget_range", "response_tone"],
            },
            max_output_tokens=600,
        )
        if not isinstance(result, AIDynamicQuestionPlan):
            self.last_error = self.llm_adapter.last_error if self.llm_adapter else "LLM unavailable"
            return []
        questions = []
        seen = set()
        has_structured_price = any(product.price > 0 or product.unit_price for product in products)
        for item in result.questions:
            if item.id in seen:
                continue
            if not has_structured_price and self._looks_like_price_question(item.label, item.reason, item.options):
                continue
            seen.add(item.id)
            questions.append(Question(
                id=item.id,
                label=self._normalize_cn(item.label),
                reason=self._normalize_cn(item.reason),
                answer_type="single_choice",
                options=[self._normalize_cn(option) for option in item.options],
                required=True,
                source="product_difference",
                evidence=["llm_product_difference"],
            ))
        questions = questions[:3]
        if not any(question.id == "budget_range" for question in questions):
            budget = next((item for item in fallback_dimensions if item["id"] == "budget_range"), None)
            if budget:
                questions.append(Question.model_validate(budget))
        if not any(question.id == "response_tone" for question in questions):
            tone = next((item for item in fallback_dimensions if item["id"] == "response_tone"), None)
            if tone:
                questions.append(Question.model_validate(tone))
        return questions[:5]

    def _looks_like_price_question(self, label: str, reason: str, options: list[str]) -> bool:
        text = " ".join([label, reason, *options])
        return any(token in text for token in ["价格", "价位", "预算区间", "低于", "高于", "元", "¥"])

    def _normalize_cn(self, text: str) -> str:
        return text.replace("長", "长").replace("臺", "台").replace("裏", "里")

    def _general_external_question(self, products: list[ProductOffer]) -> dict | None:
        claims: dict[str, int] = {}
        for product in products:
            for claim in product.features.get("detected_claims", []):
                claims[claim] = claims.get(claim, 0) + 1
        options = [claim for claim, _ in sorted(claims.items(), key=lambda item: item[1], reverse=True)[:6]]
        if len(options) < 2:
            return None
        return {
            "id": "product_claim",
            "label": "这些热榜候选里，你最想优先哪类卖点？",
            "reason": "系统从当前召回的热榜标题中抽取了高频卖点，用它来区分不同候选，而不是使用固定问卷。",
            "answer_type": "single_choice",
            "options": options,
            "required": True,
            "source": "product_difference",
            "evidence": ["external_hotlist_title_claims"],
        }

    def _select_relevant_dimensions(
        self,
        dimensions: list[dict],
        products: list[ProductOffer],
        domain_config: dict,
        scenario: str | None,
    ) -> list[dict]:
        if not products:
            return dimensions

        signals = self._product_difference_signals(products)
        scenario_focus = self._scenario_focus(domain_config, scenario)
        priority: list[tuple[int, dict]] = []

        for dimension in dimensions:
            enriched_dimension = dict(dimension)
            enriched_dimension["reason"] = self._dynamic_reason(dimension, signals)
            enriched_dimension["source"] = self._question_source(dimension["id"], scenario_focus)
            enriched_dimension["evidence"] = self._question_evidence(dimension["id"], signals)
            score = self._dimension_score(enriched_dimension["id"], signals)
            if enriched_dimension["id"] in scenario_focus:
                score += 40 - scenario_focus.index(enriched_dimension["id"]) * 3
            priority.append((score, enriched_dimension))

        priority.sort(key=lambda item: item[0], reverse=True)
        sorted_by_signal = [item for _, item in priority]
        if not scenario_focus:
            return sorted_by_signal

        by_id = {item["id"]: item for item in sorted_by_signal}
        focused = [by_id[item_id] for item_id in scenario_focus if item_id in by_id]
        focused_ids = {item["id"] for item in focused}
        remaining = [item for item in sorted_by_signal if item["id"] not in focused_ids]
        return focused + remaining

    def _product_difference_signals(self, products: list[ProductOffer]) -> dict[str, bool]:
        life_stages = {product.features.get("life_stage") for product in products}
        palatability = {product.features.get("palatability") for product in products}
        digestive = {bool(product.features.get("digestive_support")) for product in products}
        protein = {product.features.get("protein_level") for product in products}
        categories = {product.features.get("category") for product in products}
        health_goals = {product.features.get("health_goal") for product in products}
        ingredient_quality = {product.features.get("ingredient_quality") for product in products}
        unit_prices = [
            product.unit_price
            for product in products
            if product.unit_price is not None and product.unit_price > 0
        ]
        price_spread = max(unit_prices) / min(unit_prices) if len(unit_prices) >= 2 else 1
        risk_tags = {tag for product in products for tag in product.risk_tags}

        return {
            "domain": products[0].domain if products else None,
            "life_stage_diff": len(life_stages - {None}) > 1,
            "palatability_diff": len(palatability - {None}) > 1,
            "digestive_diff": len(digestive) > 1,
            "protein_diff": len(protein - {None}) > 1,
            "category_diff": len(categories - {None}) > 1,
            "health_goal_diff": len(health_goals - {None}) > 1,
            "ingredient_quality_diff": len(ingredient_quality - {None}) > 1,
            "price_spread": price_spread >= 1.8,
            "medical_risk": "medication_conflict_warning" in risk_tags,
            "allergy_risk": "allergy_warning" in risk_tags,
            "style_question": True,
            "life_stage_counts": life_stages - {None},
            "palatability_counts": palatability - {None},
            "category_counts": categories - {None},
            "health_goal_counts": health_goals - {None},
            "min_unit_price": min(unit_prices) if unit_prices else None,
            "max_unit_price": max(unit_prices) if unit_prices else None,
        }

    def _scenario_focus(self, domain_config: dict, scenario: str | None) -> list[str]:
        if not scenario:
            return []
        for item in domain_config.get("sub_scenarios", []):
            if item["id"] == scenario:
                return item.get("focus_dimensions", [])
        return []

    def _dimension_score(self, dimension_id: str, signals: dict[str, bool]) -> int:
        mapping = {
            "pet_age_stage": ["life_stage_diff"],
            "digestive_issue": ["digestive_diff", "protein_diff"],
            "picky_eater": ["palatability_diff"],
            "budget_range": ["price_spread"],
            "priority": ["price_spread", "digestive_diff", "palatability_diff", "ingredient_quality_diff"],
            "health_goal": ["health_goal_diff", "category_diff"],
            "medical_context": ["medical_risk"],
            "allergy": ["allergy_risk"],
            "response_tone": ["style_question"],
        }
        score = 1
        for signal in mapping.get(dimension_id, []):
            if signals.get(signal):
                score += 10
        return score

    def _dynamic_reason(self, dimension: dict, signals: dict) -> str:
        dimension_id = dimension["id"]
        if dimension_id == "pet_age_stage" and signals.get("life_stage_diff"):
            values = "、".join(sorted(signals.get("life_stage_counts", [])))
            return f"候选商品覆盖{values}等阶段，先确认年龄阶段，避免幼猫买到成猫专用粮。"
        if dimension_id == "digestive_issue" and signals.get("digestive_diff"):
            return "候选商品里同时存在肠胃友好型和高蛋白/高冻干型，需要先判断软便、呕吐或肠胃敏感程度。"
        if dimension_id == "picky_eater" and signals.get("palatability_diff"):
            values = "、".join(sorted(signals.get("palatability_counts", [])))
            return f"候选商品适口性分为{values}，挑食程度会影响是否优先冻干、肉含量和适口性。"
        if dimension_id == "budget_range" and signals.get("price_spread"):
            min_price = signals.get("min_unit_price")
            max_price = signals.get("max_unit_price")
            unit_label = "/kg" if signals.get("domain") == "pet_food" else "/计价单位"
            return f"候选商品参考单价约从 ¥{min_price:.2f}{unit_label} 到 ¥{max_price:.2f}{unit_label}，预算策略会明显改变 Top 排序。"
        if dimension_id == "priority":
            return "候选商品卖点分散在成分、功能、适口性、热度和单价上，需要选择主目标来调整排序权重。"
        if dimension_id == "health_goal" and signals.get("health_goal_diff"):
            values = "、".join(sorted(signals.get("health_goal_counts", [])))
            return f"候选保健品覆盖{values}，先确认目标，避免把不同细分类目混在一起推荐。"
        if dimension_id == "allergy" and signals.get("allergy_risk"):
            return "候选商品中有鱼类、乳制品或其他潜在过敏相关成分，需要先排除不适合项。"
        if dimension_id == "medical_context" and signals.get("medical_risk"):
            return "保健品可能涉及服药、慢病、孕期或儿童使用边界，推荐前必须确认安全上下文。"
        return dimension["reason"]

    def _question_source(self, dimension_id: str, scenario_focus: list[str]) -> str:
        if dimension_id == "response_tone":
            return "communication_preference"
        if dimension_id in scenario_focus:
            return "scenario_playbook"
        return "product_difference"

    def _question_evidence(self, dimension_id: str, signals: dict) -> list[str]:
        mapping = {
            "pet_age_stage": ["life_stage_diff"],
            "digestive_issue": ["digestive_diff", "protein_diff"],
            "picky_eater": ["palatability_diff"],
            "budget_range": ["price_spread"],
            "priority": ["price_spread", "digestive_diff", "palatability_diff", "ingredient_quality_diff"],
            "health_goal": ["health_goal_diff", "category_diff"],
            "medical_context": ["medical_risk"],
            "allergy": ["allergy_risk"],
            "response_tone": ["style_question"],
        }
        return [signal for signal in mapping.get(dimension_id, []) if signals.get(signal)]
