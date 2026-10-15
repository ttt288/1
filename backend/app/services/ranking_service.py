from app.schemas import ProductOffer, RecommendationItem, RiskResult, UserProfile


class RankingService:
    def rank(
        self,
        products: list[ProductOffer],
        risk_results: list[RiskResult],
        user_profile: UserProfile,
    ) -> list[RecommendationItem]:
        risk_by_product = {risk.product_id: risk for risk in risk_results}
        score_context = self._score_context(products, user_profile)
        items = [
            self._score_product(product, risk_by_product[product.id], user_profile, score_context)
            for product in products
        ]
        if any(not risk_by_product[item.product.id].blocked for item in items):
            items = [item for item in items if not risk_by_product[item.product.id].blocked]
        items.sort(key=lambda item: item.final_score, reverse=True)
        for index, item in enumerate(items, start=1):
            item.rank = index
        return items

    def _score_product(
        self,
        product: ProductOffer,
        risk: RiskResult,
        user_profile: UserProfile,
        score_context: dict,
    ) -> RecommendationItem:
        answers = user_profile.scenario_profile
        suitability = self._suitability_score(product, answers)
        safety = max(0, 100 - risk.risk_score)
        value_match = self._value_score(product, answers, score_context)
        preference_match = self._preference_score(product, answers)
        popularity = product.popularity_score
        weights = self._weights(answers)
        if product.domain == "general":
            weights = {
                "suitability": 0.46,
                "safety": 0.18,
                "value_match": 0.10,
                "popularity": 0.06,
                "preference_match": 0.20,
            }

        final_score = (
            suitability * weights["suitability"]
            + safety * weights["safety"]
            + value_match * weights["value_match"]
            + popularity * weights["popularity"]
            + preference_match * weights["preference_match"]
        )
        if risk.blocked:
            final_score = min(final_score, 45)
        if self._age_mismatch(product, answers):
            final_score = min(final_score, 52)
        if self._high_budget_non_value_case(answers):
            if self._budget_positioning(product):
                final_score = max(0, final_score - 8)
            if self._quality_positioning(product):
                final_score = min(100, final_score + 4)
        if product.domain == "pet_food" and answers.get("priority") == "美毛泪痕":
            if self._coat_positioning(product):
                final_score = min(100, final_score + 14)
            else:
                final_score = max(0, final_score - 12)
        if answers.get("_scenario") == "kitten_growth":
            if product.features.get("life_stage") == "幼猫":
                final_score = min(100, final_score + 30)
            elif product.features.get("life_stage") == "全阶段":
                final_score = max(0, final_score - 18)

        return RecommendationItem(
            product=product,
            rank=0,
            final_score=round(final_score, 2),
            score_breakdown={
                "suitability": round(suitability, 2),
                "safety": round(safety, 2),
                "value_match": round(value_match, 2),
                "popularity": round(popularity, 2),
                "preference_match": round(preference_match, 2),
            },
            suitable_reasons=self._reasons(product, answers),
            tradeoffs=product.weaknesses[:3],
            risk_notes=risk.notes,
            confidence=0.78 if risk.risk_level != "high" else 0.62,
        )

    def _score_context(self, products: list[ProductOffer], user_profile: UserProfile) -> dict:
        unit_prices = [
            product.unit_price
            for product in products
            if product.unit_price is not None and product.unit_price > 0
        ]
        return {
            "min_unit_price": min(unit_prices) if unit_prices else 0,
            "max_unit_price": max(unit_prices) if unit_prices else 0,
            "weights": self._weights(user_profile.scenario_profile),
        }

    def _weights(self, answers: dict) -> dict[str, float]:
        priority = answers.get("priority") or answers.get("health_goal")
        if priority == "性价比":
            return {
                "suitability": 0.18,
                "safety": 0.12,
                "value_match": 0.45,
                "popularity": 0.15,
                "preference_match": 0.10,
            }
        if priority in ["肠胃稳定", "肠胃调理"]:
            return {
                "suitability": 0.38,
                "safety": 0.28,
                "value_match": 0.14,
                "popularity": 0.08,
                "preference_match": 0.12,
            }
        return {
            "suitability": 0.35,
            "safety": 0.25,
            "value_match": 0.20,
            "popularity": 0.10,
            "preference_match": 0.10,
        }

    def _suitability_score(self, product: ProductOffer, answers: dict) -> float:
        score = 55.0
        target_text = " ".join(product.target_users + product.strengths)
        for value in answers.values():
            if isinstance(value, str) and value and value in target_text:
                score += 12
        if product.domain == "pet_food":
            if answers.get("priority") == "美毛泪痕" and self._coat_positioning(product):
                score += 24
            if answers.get("digestive_issue") in ["经常", "偶尔"] and product.features.get("digestive_support"):
                score += 22
            if answers.get("picky_eater") in ["很挑食", "有一点"] and product.features.get("palatability") == "high":
                score += 18
            if answers.get("pet_age_stage") == product.features.get("life_stage"):
                score += 12
            if answers.get("pet_age_stage") == "幼猫" and product.features.get("life_stage") == "全阶段":
                score += 4
            if answers.get("_scenario") == "kitten_growth":
                if product.features.get("life_stage") == "幼猫":
                    score += 36
                elif product.features.get("life_stage") == "全阶段":
                    score -= 18
            if self._age_mismatch(product, answers):
                score -= 38
        if product.domain == "supplement":
            if answers.get("health_goal") == product.features.get("health_goal"):
                score += 30
        if product.domain == "general" and answers.get("product_claim"):
            if answers["product_claim"] in product.features.get("detected_claims", []):
                score += 24
        if product.domain == "general":
            match = self._general_match(product, answers)
            score += match["hit_score"]
            score -= match["conflict_score"]
        return min(score, 100)

    def _budget_score(self, product: ProductOffer, answers: dict) -> float:
        budget = str(answers.get("budget_range", ""))
        if "预算优先" in budget:
            return self._budget_first_score(product)
        if "预算不敏感" in budget:
            return 86 if self._quality_positioning(product) else 72
        if "100元以内" in budget:
            return 100 if product.price <= 100 else max(20, 100 - (product.price - 100) * 0.6)
        if "150元以内" in budget:
            return 100 if product.price <= 150 else max(20, 100 - (product.price - 150) * 0.4)
        if "150-250元" in budget or "100-200元" in budget:
            return 100 if product.price <= 250 else 65
        return 85

    def _budget_first_score(self, product: ProductOffer) -> float:
        if product.unit_price is None:
            return 70.0
        if product.unit_price <= 45:
            return 100
        if product.unit_price <= 70:
            return 82
        if product.unit_price <= 95:
            return 62
        return 45

    def _value_score(self, product: ProductOffer, answers: dict, score_context: dict) -> float:
        budget_score = self._budget_score(product, answers)
        min_unit = score_context["min_unit_price"]
        max_unit = score_context["max_unit_price"]
        if product.unit_price is None or max_unit <= min_unit:
            unit_score = 70.0
        else:
            unit_score = 100 - ((product.unit_price - min_unit) / (max_unit - min_unit)) * 80
        priority = answers.get("priority") or answers.get("health_goal")
        if (
            ("以上" in str(answers.get("budget_range", "")) or "预算不敏感" in str(answers.get("budget_range", "")))
            and priority != "性价比"
        ):
            return 82.0
        if priority == "性价比":
            return min(100, unit_score * 0.65 + budget_score * 0.25 + product.popularity_score * 0.10)
        return min(100, budget_score * 0.75 + unit_score * 0.25)

    def _preference_score(self, product: ProductOffer, answers: dict) -> float:
        priority = answers.get("priority") or answers.get("health_goal")
        if not priority:
            if product.domain == "general":
                match = self._general_match(product, answers)
                return max(35, min(100, 55 + match["hit_score"] - match["conflict_score"]))
            return 65
        text = " ".join(product.strengths + product.target_users)
        if product.domain == "pet_food" and priority == "美毛泪痕":
            return 96 if self._coat_positioning(product) else 45
        if product.domain == "general":
            match = self._general_match(product, answers)
            return max(35, min(100, 55 + match["hit_score"] - match["conflict_score"]))
        return 90 if str(priority) in text else 60

    def _age_mismatch(self, product: ProductOffer, answers: dict) -> bool:
        if product.domain != "pet_food":
            return False
        age_stage = answers.get("pet_age_stage")
        life_stage = product.features.get("life_stage")
        if not age_stage:
            return False
        if age_stage in product.avoid_for:
            return True
        if age_stage == "幼猫":
            return life_stage not in ["幼猫", "全阶段"]
        if age_stage == "成猫":
            return life_stage == "幼猫"
        if age_stage == "老年猫":
            return life_stage == "幼猫"
        return False

    def _high_budget_non_value_case(self, answers: dict) -> bool:
        priority = answers.get("priority") or answers.get("health_goal")
        budget = str(answers.get("budget_range", ""))
        return ("以上" in budget or "预算不敏感" in budget) and priority != "性价比"

    def _budget_positioning(self, product: ProductOffer) -> bool:
        text = " ".join(product.target_users + product.strengths + product.features.get("market_signals", []))
        return any(token in text for token in ["性价比", "预算优先", "预算150元以内", "预算150-250元", "入门价格", "质价比"])

    def _quality_positioning(self, product: ProductOffer) -> bool:
        text = " ".join(product.target_users + product.strengths + product.features.get("market_signals", []))
        return any(token in text for token in ["原料透明", "高品质配方", "进口品牌", "国产高端", "高肉含量"])

    def _coat_positioning(self, product: ProductOffer) -> bool:
        text = " ".join(
            product.target_users
            + product.strengths
            + product.features.get("market_signals", [])
            + product.features.get("main_ingredients", [])
        )
        return any(token in text for token in ["美毛", "泪痕", "鱼油", "三文鱼"])

    def _reasons(self, product: ProductOffer, answers: dict) -> list[str]:
        reasons: list[str] = []
        if product.domain == "pet_food" and answers.get("priority") == "美毛泪痕":
            if self._coat_positioning(product):
                reasons.append("匹配美毛泪痕主目标")
        reasons.extend(product.strengths[:2])
        if product.domain == "pet_food" and answers.get("digestive_issue") in ["经常", "偶尔"]:
            if product.features.get("digestive_support"):
                reasons.append("匹配肠胃敏感场景")
        if product.domain == "pet_food" and answers.get("pet_age_stage") == product.features.get("life_stage"):
            reasons.append(f"匹配{answers['pet_age_stage']}阶段")
        if product.domain == "pet_food" and answers.get("pet_age_stage") == "幼猫" and product.features.get("life_stage") == "全阶段":
            reasons.append("可覆盖幼猫阶段，但需核对具体月龄")
        if product.domain == "supplement" and answers.get("health_goal") == product.features.get("health_goal"):
            reasons.append(f"匹配{answers['health_goal']}需求")
        if product.domain == "general" and answers.get("product_claim") in product.features.get("detected_claims", []):
            reasons.append(f"匹配{answers['product_claim']}卖点")
        if product.domain == "general":
            match = self._general_match(product, answers)
            for answer in match["matched_answers"][:3]:
                reasons.append(f"匹配{answer}偏好")
            for conflict in match["conflicts"][:2]:
                reasons.append(f"需注意{conflict}可能不完全匹配")
        deduped = list(dict.fromkeys(reasons))
        return deduped[:4]

    def _general_match(self, product: ProductOffer, answers: dict) -> dict:
        text = self._general_product_text(product)
        matched_answers: list[str] = []
        conflicts: list[str] = []
        hit_score = 0.0
        conflict_score = 0.0
        for raw_value in answers.values():
            if not isinstance(raw_value, str) or not raw_value:
                continue
            if raw_value in ["温柔可爱", "直接理性", "专业详细", "轻松鼓励", "预算优先", "预算不敏感"]:
                continue
            terms, negative_terms = self._general_answer_terms(raw_value)
            hits = [term for term in terms if term and term in text]
            negatives = [term for term in negative_terms if term and term in text]
            if hits:
                matched_answers.append(raw_value)
                hit_score += min(24, 10 + len(hits) * 5)
            elif raw_value in text:
                matched_answers.append(raw_value)
                hit_score += 12
            if negatives:
                conflicts.append(raw_value)
                conflict_score += min(22, 10 + len(negatives) * 6)
        return {
            "hit_score": hit_score,
            "conflict_score": conflict_score,
            "matched_answers": list(dict.fromkeys(matched_answers)),
            "conflicts": list(dict.fromkeys(conflicts)),
        }

    def _general_product_text(self, product: ProductOffer) -> str:
        return " ".join(
            [
                product.name,
                product.brand,
                " ".join(product.strengths),
                " ".join(product.target_users),
                " ".join(product.features.get("detected_claims", [])),
            ]
        )

    def _general_answer_terms(self, value: str) -> tuple[list[str], list[str]]:
        mapping = {
            "持久": ["持妆", "恒久", "不脱妆", "持久", "DW"],
            "持妆": ["持妆", "恒久", "不脱妆", "持久", "DW"],
            "持妆强": ["持妆", "恒久", "不脱妆", "持久", "DW"],
            "控油": ["控油", "油皮", "持妆", "恒久"],
            "日常妆": ["自然", "裸妆", "轻薄", "日常", "通勤", "不挑皮"],
            "日常通勤": ["自然", "裸妆", "轻薄", "日常", "通勤", "不挑皮"],
            "工作妆": ["持妆", "自然", "通勤", "不挑皮"],
            "晚宴妆": ["遮瑕", "持妆", "恒久"],
            "拍照妆": ["遮瑕", "持妆", "高清"],
            "约会妆": ["自然", "服帖", "水光", "轻薄"],
            "中性": ["自然", "中性", "不挑皮", "通用"],
            "常规肌肤": ["自然", "常规", "不挑皮", "通用"],
            "冷调": ["冷调", "冷白", "冷象牙", "1C0", "2C0"],
            "暖调": ["暖调", "暖白", "1W1"],
            "油皮": ["油皮", "控油", "持妆", "恒久"],
            "干皮": ["干皮", "沁水", "滋润", "水光", "服帖"],
            "混合性": ["不挑皮", "混合", "持妆", "服帖"],
            "敏感肌": ["敏感", "温和", "低刺激", "不挑皮"],
            "遮瑕": ["遮瑕", "高遮瑕", "痘印"],
            "轻薄": ["轻薄", "自然", "裸妆"],
            "便携": ["轻薄", "便携", "小新", "Air"],
            "续航": ["续航", "办公", "Air"],
            "游戏": ["游戏", "电竞", "RTX", "天选", "拯救者"],
            "性能": ["性能", "标压", "RTX", "酷睿", "锐龙", "Pro"],
            "办公": ["办公", "轻薄", "商务"],
        }
        negative_mapping = {
            "中性": ["暖调", "冷调", "冷白", "暖白", "1W1", "1C0", "2C0"],
            "日常妆": ["厚重", "高遮瑕"],
            "日常通勤": ["厚重", "高遮瑕"],
            "干皮": ["控油", "油皮"],
            "油皮": ["滋润", "水光", "干皮"],
            "便携": ["游戏本", "电竞", "厚重"],
            "续航": ["游戏本", "电竞"],
        }
        terms = [value]
        negatives: list[str] = []
        for key, mapped_terms in mapping.items():
            if key in value or value in key:
                terms.extend(mapped_terms)
        for key, mapped_terms in negative_mapping.items():
            if key in value or value in key:
                negatives.extend(mapped_terms)
        return list(dict.fromkeys(terms)), list(dict.fromkeys(negatives))
