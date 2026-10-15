from app.schemas import ProductOffer, RiskResult, UserProfile


class RiskService:
    def evaluate(self, product: ProductOffer, user_profile: UserProfile) -> RiskResult:
        domain = product.domain
        answers = user_profile.scenario_profile
        notes: list[str] = []
        score = 10.0
        blocked = False

        if "external_data_unverified" in product.risk_tags:
            notes.append("该候选来自外部公开榜单，参数、规格和价格需要人工核验。")
            score += 10

        if domain == "supplement":
            if "no_medical_diagnosis" in product.risk_tags:
                notes.append("仅做消费信息整理，不作为医疗诊断或治疗建议。")
            if answers.get("medical_context") in ["有", "不确定"]:
                notes.append("存在孕期、慢性病、正在服药或儿童使用等情况时，需要先咨询专业人士。")
                score += 45
                blocked = True
            if answers.get("allergy") in ["有", "不确定"] and "allergy_warning" in product.risk_tags:
                notes.append("该产品可能涉及过敏相关成分，需要优先核对配料表。")
                score += 25

        if domain == "pet_food":
            if "transition_needed" in product.risk_tags:
                notes.append("换粮建议 7-10 天逐步过渡。")
            if answers.get("digestive_issue") in ["经常", "偶尔"] and product.features.get("digestive_support"):
                notes.append("肠胃敏感猫需要少量试吃并观察便便状态。")
            if answers.get("digestive_issue") in ["经常", "偶尔"] and "high_fat_sensitive_stomach" in product.risk_tags:
                notes.append("肠胃敏感时需要谨慎选择高脂或高冻干比例产品。")
                score += 30
            if answers.get("pet_age_stage") in product.avoid_for:
                notes.append("该产品和当前年龄阶段不完全匹配。")
                score += 35
                blocked = True
            if self._hard_age_mismatch(product, answers):
                notes.append("该产品属于必须排除的年龄阶段不匹配项。")
                score += 55
                blocked = True
            allergen_hits = self._pet_allergen_hits(product, answers)
            if allergen_hits:
                notes.append(f"用户存在过敏/低敏诉求，需避开相关成分：{'、'.join(allergen_hits)}。")
                score += 55 if answers.get("allergy") == "有" else 35
                blocked = True

        if score >= 60:
            level = "high"
        elif score >= 30:
            level = "medium"
        else:
            level = "low"

        return RiskResult(
            product_id=product.id,
            risk_level=level,
            risk_score=min(score, 100),
            notes=notes,
            blocked=blocked,
        )

    def _hard_age_mismatch(self, product: ProductOffer, answers: dict) -> bool:
        age_stage = answers.get("pet_age_stage")
        life_stage = product.features.get("life_stage")
        if not age_stage or product.domain != "pet_food":
            return False
        if age_stage == "幼猫":
            return life_stage not in ["幼猫", "全阶段"]
        if age_stage in ["成猫", "老年猫"]:
            return life_stage == "幼猫"
        return False

    def _pet_allergen_hits(self, product: ProductOffer, answers: dict) -> list[str]:
        if product.domain != "pet_food" or answers.get("allergy") not in ["有", "不确定"]:
            return []
        avoid_ingredients = answers.get("avoid_ingredients") or []
        if not avoid_ingredients and answers.get("allergy") == "不确定":
            avoid_ingredients = ["鱼肉", "鱼油", "三文鱼"]
        ingredient_text = " ".join(
            [
                product.name,
                product.brand,
                *product.features.get("main_ingredients", []),
                *product.strengths,
                *product.weaknesses,
                *product.target_users,
                *product.avoid_for,
            ]
        )
        return list(dict.fromkeys(token for token in avoid_ingredients if token and token in ingredient_text))
