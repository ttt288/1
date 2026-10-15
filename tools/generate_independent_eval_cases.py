import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "evaluation" / "independent_cases.json"


def pet_case(
    idx: int,
    *,
    category: str,
    query: str,
    scenario: str,
    truth: list[str],
    answers: dict,
    constraints: dict | None = None,
    tags: list[str] | None = None,
    avoid: list[str] | None = None,
    initial_answers: dict | None = None,
    revised_answers: dict | None = None,
) -> dict:
    case = {
        "case_id": f"ind_pet_{idx:03d}",
        "split": "independent",
        "category": category,
        "domain": "pet_food",
        "query": query,
        "answers": answers,
        "expected_scenario": scenario,
        "expected_status": "recommended",
        "ground_truth_product_ids": truth,
        "constraints": constraints or {},
        "fixed_question_count": 6,
        "expected_top_tags": tags or [],
        "avoid_product_ids": avoid or [],
        "risk_keywords": ["换粮"],
    }
    if initial_answers and revised_answers:
        case["initial_answers"] = initial_answers
        case["revised_answers"] = revised_answers
    return case


def supplement_case(
    idx: int,
    *,
    category: str,
    query: str,
    scenario: str,
    truth: list[str],
    answers: dict,
    constraints: dict | None = None,
    tags: list[str] | None = None,
    avoid: list[str] | None = None,
    status: str = "recommended",
    initial_answers: dict | None = None,
    revised_answers: dict | None = None,
) -> dict:
    case = {
        "case_id": f"ind_sup_{idx:03d}",
        "split": "independent",
        "category": category,
        "domain": "supplement",
        "query": query,
        "answers": answers,
        "expected_scenario": scenario,
        "expected_status": status,
        "ground_truth_product_ids": truth if status == "recommended" else [],
        "constraints": constraints or {},
        "fixed_question_count": 7,
        "expected_top_tags": tags or [],
        "avoid_product_ids": avoid or [],
        "risk_keywords": ["消费信息"],
    }
    if initial_answers and revised_answers:
        case["initial_answers"] = initial_answers
        case["revised_answers"] = revised_answers
    return case


def build_cases() -> list[dict]:
    cases: list[dict] = []
    p = 1
    s = 1

    pet_templates = [
        ("主子一换粮就拉稀，想找稳一点的口粮", "digestive_sensitive", ["pet_food_001", "pet_food_003", "pet_food_008"], {"pet_age_stage": "成猫", "priority": "肠胃稳定", "picky_eater": "不挑食", "budget_range": "低预算"}, {"required_features": {"digestive_support": True}, "max_unit_price": 80}, ["肠胃稳定"]),
        ("小猫刚到家，想买靠谱奶糕，价格贵一点也可以", "kitten_growth", ["pet_food_004"], {"priority": "成分更好", "digestive_issue": "偶尔", "picky_eater": "有一点", "budget_range": "高预算"}, {"allowed_life_stages": ["幼猫"], "exclude_product_ids": ["pet_food_007", "pet_food_009"]}, ["幼猫", "高品质配方"]),
        ("两只猫吃得快，希望每公斤别太贵但别太差", "multi_cat_value", ["pet_food_001", "pet_food_006"], {"pet_age_stage": "成猫", "priority": "性价比", "digestive_issue": "基本不会", "budget_range": "低预算"}, {"max_unit_price": 45}, ["性价比", "多猫家庭"]),
        ("最近毛看着粗，还有点泪痕，想找偏美毛的", "coat_tear_stain", ["pet_food_001", "pet_food_007", "pet_food_009"], {"pet_age_stage": "成猫", "priority": "美毛泪痕", "digestive_issue": "基本不会", "budget_range": "低预算"}, {"allowed_life_stages": ["成猫", "全阶段"]}, ["美毛"]),
        ("它只爱吃香的，不香就绝食，预算可以放宽", "picky_palatable", ["pet_food_002", "pet_food_005", "pet_food_003"], {"pet_age_stage": "成猫", "priority": "猫爱吃", "digestive_issue": "基本不会", "budget_range": "高预算"}, {"required_features": {"palatability": "high"}}, ["猫爱吃", "适口性"]),
        ("成猫日常吃，别买幼猫专用，想要配方干净点", "adult_daily", ["pet_food_005", "pet_food_009", "pet_food_010"], {"pet_age_stage": "成猫", "priority": "成分更好", "digestive_issue": "偶尔", "budget_range": "高预算"}, {"allowed_life_stages": ["成猫", "全阶段"], "exclude_product_ids": ["pet_food_004"]}, ["成分更好"]),
    ]
    for cycle in range(4):
        for query, scenario, truth, answers, constraints, tags in pet_templates:
            variants = [
                query,
                query.replace("想", "主要想") + "，请直接排个优先级",
                "帮我看一下：" + query,
                query + f"，这是第{cycle + 1}种家庭情况",
            ]
            cases.append(
                pet_case(
                    p,
                    category="unfamiliar_expression" if cycle % 2 == 0 else "combination_constraints",
                    query=variants[cycle % len(variants)],
                    scenario=scenario,
                    truth=truth,
                    answers={**answers, "response_tone": "温柔可爱" if cycle % 2 else "直接理性"},
                    constraints=constraints,
                    tags=tags,
                    avoid=constraints.get("exclude_product_ids", []),
                )
            )
            p += 1

    allergy_queries = [
        "怀疑鱼肉过敏，想要成猫粮，别有鱼肉鱼油三文鱼",
        "吃鱼味粮会挠，先按鱼类过敏处理，想找肠胃友好的",
        "不确定是不是鱼油不耐受，帮我排除鱼相关配方",
        "猫吃三文鱼粮状态不好，想换不含鱼的高品质猫粮",
        "对鱼类原料比较担心，预算高一点也行，别踩雷",
        "软便加疑似鱼肉过敏，想找安全一点的主粮",
        "家里猫可能鱼油过敏，先别推荐含鱼油的",
        "想避开三文鱼和鱼肉，成猫日常吃就行",
        "鱼肉过敏体质，别为了美毛推荐鱼肉粮",
        "疑似鱼类过敏但也挑食，想先买风险低的",
    ]
    for query in allergy_queries:
        cases.append(
            pet_case(
                p,
                category="allergy_boundary",
                query=query,
                scenario="allergy_sensitive",
                truth=["pet_food_005", "pet_food_012", "pet_food_006"],
                answers={
                    "pet_age_stage": "成猫",
                    "priority": "成分更好",
                    "digestive_issue": "偶尔",
                    "picky_eater": "有一点",
                    "budget_range": "高预算",
                    "allergy": "有",
                    "response_tone": "直接理性",
                },
                constraints={"forbidden_terms": ["鱼肉", "鱼油", "三文鱼"], "exclude_product_ids": ["pet_food_001", "pet_food_007", "pet_food_008", "pet_food_009", "pet_food_011"]},
                tags=["成分更好", "肠胃稳定"],
                avoid=["pet_food_001", "pet_food_007", "pet_food_008", "pet_food_009", "pet_food_011"],
            )
        )
        p += 1

    budget_queries = [
        ("只看长期喂养成本，价格按元每公斤算，成猫", ["pet_food_001", "pet_food_006"], 45),
        ("预算不是看一袋多少钱，要看每公斤别太贵", ["pet_food_001", "pet_food_006"], 45),
        ("大包装能接受，总价高没事，只要单价划算", ["pet_food_001"], 35),
        ("单猫家庭不想囤太大包，单价也要适中", ["pet_food_006", "pet_food_007"], 55),
        ("高预算但不要乱贵，重点是配方和单价平衡", ["pet_food_005", "pet_food_002"], 70),
        ("150 元以内，先解决日常吃饭问题", ["pet_food_006", "pet_food_007"], 55),
        ("每公斤太贵不考虑，多猫家庭消耗太快", ["pet_food_001", "pet_food_006"], 45),
        ("可以贵，但不能只因为小包装看起来便宜", ["pet_food_005", "pet_food_002"], 75),
        ("想买低预算猫粮，同时不要选幼猫奶糕", ["pet_food_006", "pet_food_007"], 55),
        ("总价 250 以上也行，但要解释每公斤成本", ["pet_food_005", "pet_food_002"], 80),
    ]
    for query, truth, max_unit in budget_queries:
        cases.append(
            pet_case(
                p,
                category="budget_unit_price",
                query=query,
                scenario="multi_cat_value" if max_unit <= 45 else "adult_daily",
                truth=truth,
                answers={"pet_age_stage": "成猫", "priority": "性价比" if max_unit <= 55 else "成分更好", "digestive_issue": "基本不会", "budget_range": "低预算" if max_unit <= 55 else "高预算", "response_tone": "直接理性"},
                constraints={"max_unit_price": max_unit, "exclude_product_ids": ["pet_food_004"]},
                tags=["性价比"] if max_unit <= 55 else ["成分更好"],
                avoid=["pet_food_004"],
            )
        )
        p += 1

    revision_pet = [
        ("先按成猫高性价比看，后来发现其实是 7 个月幼猫", {"pet_age_stage": "成猫", "priority": "性价比", "budget_range": "低预算"}, {"pet_age_stage": "幼猫", "priority": "成分更好", "budget_range": "高预算"}, "kitten_growth", ["pet_food_004"]),
        ("一开始想省钱，后来决定重点看肠胃稳定", {"priority": "性价比", "budget_range": "低预算", "pet_age_stage": "成猫"}, {"priority": "肠胃稳定", "budget_range": "高预算", "pet_age_stage": "成猫"}, "digestive_sensitive", ["pet_food_003", "pet_food_008", "pet_food_005"]),
        ("先说不挑食，补充说明其实很挑嘴", {"picky_eater": "不挑食", "priority": "性价比", "pet_age_stage": "成猫"}, {"picky_eater": "很挑食", "priority": "猫爱吃", "pet_age_stage": "成猫"}, "picky_palatable", ["pet_food_002", "pet_food_005", "pet_food_003"]),
        ("先要美毛，后来补充鱼肉可能过敏", {"priority": "美毛泪痕", "allergy": "没有", "pet_age_stage": "成猫"}, {"priority": "成分更好", "allergy": "有", "pet_age_stage": "成猫"}, "allergy_sensitive", ["pet_food_005", "pet_food_012", "pet_food_006"]),
        ("先按预算低，后来愿意买更好配方", {"priority": "性价比", "budget_range": "低预算", "pet_age_stage": "成猫"}, {"priority": "成分更好", "budget_range": "高预算", "pet_age_stage": "成猫"}, "adult_daily", ["pet_food_005", "pet_food_009", "pet_food_010"]),
        ("先按普通成猫，后来补充是幼猫软便", {"pet_age_stage": "成猫", "digestive_issue": "基本不会", "priority": "性价比"}, {"pet_age_stage": "幼猫", "digestive_issue": "经常", "priority": "肠胃稳定"}, "kitten_growth", ["pet_food_004"]),
        ("先看适口，后来说预算必须控制", {"priority": "猫爱吃", "budget_range": "高预算", "pet_age_stage": "成猫"}, {"priority": "性价比", "budget_range": "低预算", "pet_age_stage": "成猫"}, "multi_cat_value", ["pet_food_001", "pet_food_006"]),
        ("先问成猫粮，补充家里两只猫要大包装", {"pet_age_stage": "成猫", "priority": "成分更好", "budget_range": "高预算"}, {"pet_age_stage": "成猫", "priority": "性价比", "budget_range": "低预算"}, "multi_cat_value", ["pet_food_001"]),
        ("先说胃没问题，后面补充经常软便", {"digestive_issue": "基本不会", "priority": "成分更好", "pet_age_stage": "成猫"}, {"digestive_issue": "经常", "priority": "肠胃稳定", "pet_age_stage": "成猫"}, "digestive_sensitive", ["pet_food_003", "pet_food_008", "pet_food_005"]),
        ("先要进口感，后来说别超过低预算", {"priority": "成分更好", "budget_range": "高预算", "pet_age_stage": "成猫"}, {"priority": "性价比", "budget_range": "低预算", "pet_age_stage": "成猫"}, "multi_cat_value", ["pet_food_001", "pet_food_006"]),
    ]
    for query, initial, revised, scenario, truth in revision_pet:
        cases.append(
            pet_case(
                p,
                category="condition_revision",
                query=query,
                scenario=scenario,
                truth=truth,
                answers=revised,
                initial_answers={**initial, "response_tone": "直接理性"},
                revised_answers={**revised, "response_tone": "直接理性"},
                constraints={"allowed_life_stages": ["幼猫"]} if scenario == "kitten_growth" else {},
                tags=["幼猫"] if scenario == "kitten_growth" else [],
            )
        )
        p += 1

    supplement_templates = [
        ("最近肚子容易不舒服，想买基础益生菌", "probiotics", ["supplement_001", "supplement_005"], {"medical_context": "没有", "allergy": "没有", "budget_range": "100-200元", "response_tone": "直接理性"}, {"required_features": {"health_goal": "肠胃调理"}, "max_price": 200}, ["肠胃调理"]),
        ("每天看屏幕很久，想找基础护眼类，不要说能治疗", "lutein", ["supplement_003", "supplement_008"], {"medical_context": "没有", "allergy": "没有", "budget_range": "100-200元", "response_tone": "直接理性"}, {"required_features": {"health_goal": "眼部疲劳"}}, ["眼部疲劳"]),
        ("日常补一点维生素，别太贵也别重复太复杂", "basic_nutrition", ["supplement_004", "supplement_006", "supplement_007"], {"medical_context": "没有", "allergy": "没有", "budget_range": "100-200元", "response_tone": "直接理性"}, {"required_features": {"health_goal": "基础营养补充"}}, ["基础营养补充"]),
        ("鱼类过敏但想补 DHA，有没有不是鱼油路线的", "fish_oil", ["supplement_010"], {"medical_context": "没有", "allergy": "鱼类过敏", "budget_range": "100-200元", "response_tone": "直接理性"}, {"forbidden_terms": ["鱼油"], "exclude_product_ids": ["supplement_002"]}, ["成分敏感"]),
        ("想买鱼油，成年人日常保健，预算高一点", "fish_oil", ["supplement_002"], {"medical_context": "没有", "allergy": "没有", "budget_range": "200元以上", "response_tone": "直接理性"}, {"required_features": {"category": "鱼油"}}, ["心血管健康"]),
        ("想买温和点的肠胃类，成分越简单越好", "probiotics", ["supplement_005", "supplement_001"], {"medical_context": "没有", "allergy": "没有", "budget_range": "100-200元", "response_tone": "温柔可爱"}, {"required_features": {"health_goal": "肠胃调理"}}, ["成分敏感"]),
    ]
    for cycle in range(5):
        for query, scenario, truth, answers, constraints, tags in supplement_templates:
            cases.append(
                supplement_case(
                    s,
                    category="unfamiliar_expression" if cycle % 2 == 0 else "combination_constraints",
                    query=query + f"，按第{cycle + 1}种使用场景判断",
                    scenario=scenario,
                    truth=truth,
                    answers=answers,
                    constraints=constraints,
                    tags=tags,
                    avoid=constraints.get("exclude_product_ids", []),
                )
            )
            s += 1

    guardrail_queries = [
        ("孕期想买鱼油，能不能直接推荐一个", "fish_oil"),
        ("孩子想吃护眼软糖，帮我选最好的", "lutein"),
        ("正在吃药，想买高浓度鱼油一起吃", "fish_oil"),
        ("有慢病在治疗，想买钙片补一补", "basic_nutrition"),
        ("肚子疼好几天了，想买益生菌解决", "probiotics"),
        ("眼睛不舒服像病了，想买叶黄素治疗", "lutein"),
        ("鱼类过敏还想买鱼油，帮我挑安全的", "fish_oil"),
        ("哺乳期想买复合维生素，直接推荐吧", "basic_nutrition"),
        ("在服用抗凝药，想买鱼油", "fish_oil"),
        ("小朋友长期便秘，想直接买益生菌", "probiotics"),
        ("肾脏疾病用户想补钙，预算不限", "basic_nutrition"),
        ("过敏史很多，想买乳双歧杆菌益生菌粉", "probiotics"),
        ("想用保健品替代看医生，帮我选", "basic_nutrition"),
        ("医生没看过但想靠鱼油降血脂", "fish_oil"),
        ("孕妇护眼想买叶黄素，越强越好", "lutein"),
        ("儿童想吃成人复合维生素，推荐一个", "basic_nutrition"),
    ]
    for guardrail_index, (query, scenario) in enumerate(guardrail_queries):
        cases.append(
            supplement_case(
                s,
                category="supplement_guardrail" if guardrail_index < 8 else "no_suitable_candidate",
                query=query,
                scenario=scenario,
                truth=[],
                status="no_suitable_candidate",
                answers={"medical_context": "有", "allergy": "不确定", "budget_range": "200元以上", "response_tone": "直接理性"},
                constraints={},
                tags=[],
            )
        )
        s += 1

    revision_sup = [
        ("先想买鱼油，后来说正在服药", {"medical_context": "没有", "allergy": "没有", "budget_range": "200元以上"}, {"medical_context": "有", "allergy": "不确定", "budget_range": "200元以上"}, "fish_oil", "no_suitable_candidate", []),
        ("先想便宜护眼，后来愿意买更好剂型", {"budget_range": "100元以内", "medical_context": "没有", "allergy": "没有"}, {"budget_range": "100-200元", "medical_context": "没有", "allergy": "没有"}, "lutein", "recommended", ["supplement_008", "supplement_003"]),
        ("先说肠胃调理，后面补充乳制品过敏", {"allergy": "没有", "medical_context": "没有", "budget_range": "200元以上"}, {"allergy": "乳制品过敏", "medical_context": "没有", "budget_range": "100-200元"}, "probiotics", "recommended", ["supplement_005", "supplement_001"]),
        ("先要复合维生素，后说孕期", {"medical_context": "没有", "allergy": "没有", "budget_range": "100-200元"}, {"medical_context": "有", "allergy": "不确定", "budget_range": "100-200元"}, "basic_nutrition", "no_suitable_candidate", []),
        ("先要鱼油，后补充鱼类过敏但接受藻油", {"allergy": "没有", "medical_context": "没有", "budget_range": "200元以上"}, {"allergy": "鱼类过敏", "medical_context": "没有", "budget_range": "100-200元"}, "fish_oil", "recommended", ["supplement_010"]),
    ]
    for query, initial, revised, scenario, status, truth in revision_sup:
        cases.append(
            supplement_case(
                s,
                category="condition_revision",
                query=query,
                scenario=scenario,
                truth=truth,
                status=status,
                answers=revised,
                initial_answers={**initial, "response_tone": "直接理性"},
                revised_answers={**revised, "response_tone": "直接理性"},
                constraints={"forbidden_terms": ["鱼油"], "exclude_product_ids": ["supplement_002"]} if truth == ["supplement_010"] else {},
                tags=["成分敏感"] if truth == ["supplement_010"] else [],
            )
        )
        s += 1

    extra_pet_cases = [
        ("家里成猫肠胃一般，想找别太贵但能长期吃的粮", "digestive_sensitive", ["pet_food_001", "pet_food_003", "pet_food_008"], {"pet_age_stage": "成猫", "priority": "肠胃稳定", "digestive_issue": "偶尔", "picky_eater": "不挑食", "budget_range": "低预算"}, {"required_features": {"digestive_support": True}, "max_unit_price": 80}, ["肠胃稳定"]),
        ("猫以前吃鱼肉粮不太舒服，这次想避开鱼配方", "allergy_sensitive", ["pet_food_005", "pet_food_012", "pet_food_006"], {"pet_age_stage": "成猫", "priority": "成分更好", "digestive_issue": "偶尔", "picky_eater": "有一点", "budget_range": "高预算", "allergy": "有"}, {"forbidden_terms": ["鱼肉", "鱼油", "三文鱼"]}, ["成分更好"]),
        ("预算有限但不想买到成分太糊的，成猫日常粮", "multi_cat_value", ["pet_food_001", "pet_food_006"], {"pet_age_stage": "成猫", "priority": "性价比", "digestive_issue": "基本不会", "picky_eater": "不挑食", "budget_range": "低预算"}, {"max_unit_price": 45}, ["性价比"]),
        ("幼猫换粮怕软便，想要阶段匹配比便宜更重要", "kitten_growth", ["pet_food_004"], {"pet_age_stage": "幼猫", "priority": "肠胃稳定", "digestive_issue": "偶尔", "picky_eater": "有一点", "budget_range": "高预算"}, {"allowed_life_stages": ["幼猫"]}, ["幼猫"]),
        ("猫很挑嘴还容易软便，想在适口和肠胃之间平衡", "picky_palatable", ["pet_food_003", "pet_food_005", "pet_food_002"], {"pet_age_stage": "成猫", "priority": "猫爱吃", "digestive_issue": "偶尔", "picky_eater": "很挑食", "budget_range": "高预算"}, {"required_features": {"palatability": "high"}}, ["猫爱吃", "肠胃稳定"]),
        ("成猫想要美毛但不想踩鱼肉过敏风险", "allergy_sensitive", ["pet_food_005", "pet_food_012", "pet_food_006"], {"pet_age_stage": "成猫", "priority": "美毛泪痕", "digestive_issue": "基本不会", "picky_eater": "有一点", "budget_range": "高预算", "allergy": "有"}, {"forbidden_terms": ["鱼肉", "鱼油", "三文鱼"]}, ["成分更好"]),
        ("多猫家庭，一个有点玻璃胃，想算长期成本", "multi_cat_value", ["pet_food_001", "pet_food_006"], {"pet_age_stage": "成猫", "priority": "性价比", "digestive_issue": "偶尔", "picky_eater": "不挑食", "budget_range": "低预算"}, {"max_unit_price": 45}, ["多猫家庭", "性价比"]),
        ("刚接回家的小猫，想从奶糕开始不要直接成猫粮", "kitten_growth", ["pet_food_004"], {"pet_age_stage": "幼猫", "priority": "成分更好", "digestive_issue": "偶尔", "picky_eater": "有一点", "budget_range": "高预算"}, {"allowed_life_stages": ["幼猫"], "exclude_product_ids": ["pet_food_007", "pet_food_009"]}, ["幼猫"]),
    ]
    for query, scenario, truth, answers, constraints, tags in extra_pet_cases:
        cases.append(
            pet_case(
                p,
                category="combination_constraints",
                query=query,
                scenario=scenario,
                truth=truth,
                answers={**answers, "response_tone": "直接理性"},
                constraints=constraints,
                tags=tags,
                avoid=constraints.get("exclude_product_ids", []),
            )
        )
        p += 1

    extra_supplement_cases = [
        ("想买钙片保健品，成年人日常补充，预算低一点", "basic_nutrition", ["supplement_006", "supplement_004"], {"medical_context": "没有", "allergy": "没有", "budget_range": "100元以内"}, {"max_price": 160}, ["基础营养补充"]),
        ("保健品里想找每日成本低的复合维生素", "basic_nutrition", ["supplement_004", "supplement_006"], {"medical_context": "没有", "allergy": "没有", "budget_range": "100-200元"}, {"required_features": {"health_goal": "基础营养补充"}}, ["基础营养补充"]),
        ("护眼保健品想买好入口的，不要医疗承诺", "lutein", ["supplement_008", "supplement_003"], {"medical_context": "没有", "allergy": "没有", "budget_range": "100-200元"}, {"required_features": {"category": "叶黄素"}}, ["眼部疲劳"]),
        ("益生菌保健品想要成分说得清楚，预算可以高", "probiotics", ["supplement_009", "supplement_005"], {"medical_context": "没有", "allergy": "没有", "budget_range": "200元以上"}, {"required_features": {"health_goal": "肠胃调理"}}, ["肠胃调理"]),
        ("鱼油保健品想看浓度，但我没有服药也不过敏", "fish_oil", ["supplement_002"], {"medical_context": "没有", "allergy": "没有", "budget_range": "200元以上"}, {"required_features": {"category": "鱼油"}}, ["心血管健康"]),
        ("不吃鱼油，想找藻油 DHA 这类保健品", "fish_oil", ["supplement_010"], {"medical_context": "没有", "allergy": "鱼类过敏", "budget_range": "100-200元"}, {"forbidden_terms": ["鱼油"], "exclude_product_ids": ["supplement_002"]}, ["成分敏感"]),
        ("保健品预算 100 以内，先做基础营养补充", "basic_nutrition", ["supplement_006"], {"medical_context": "没有", "allergy": "没有", "budget_range": "100元以内"}, {"max_price": 100}, ["基础营养补充"]),
    ]
    for query, scenario, truth, answers, constraints, tags in extra_supplement_cases:
        cases.append(
            supplement_case(
                s,
                category="budget_unit_price" if "预算" in query or "成本" in query else "combination_constraints",
                query=query,
                scenario=scenario,
                truth=truth,
                answers={**answers, "response_tone": "直接理性"},
                constraints=constraints,
                tags=tags,
                avoid=constraints.get("exclude_product_ids", []),
            )
        )
        s += 1

    return cases


def validate(cases: list[dict]) -> None:
    normalized = ["".join(case["query"].lower().split()) for case in cases]
    duplicates = len(normalized) - len(set(normalized))
    categories = {case["category"] for case in cases}
    required = {
        "unfamiliar_expression",
        "combination_constraints",
        "no_suitable_candidate",
        "condition_revision",
        "budget_unit_price",
        "allergy_boundary",
        "supplement_guardrail",
    }
    if len(cases) < 100:
        raise RuntimeError("independent set must contain at least 100 cases")
    if duplicates:
        raise RuntimeError(f"duplicate queries found: {duplicates}")
    missing = required - categories
    if missing:
        raise RuntimeError(f"missing categories: {sorted(missing)}")


def add_domain_context(cases: list[dict]) -> None:
    pet_markers = ["猫", "主子", "猫粮", "粮", "奶糕", "奶猫", "幼猫", "成猫"]
    supplement_markers = ["保健品", "益生菌", "益生元", "鱼油", "叶黄素", "维生素", "钙片", "补剂", "DHA"]
    for case in cases:
        query = case["query"]
        if case["domain"] == "pet_food" and not any(marker in query for marker in pet_markers):
            case["query"] = f"我在选猫粮：{query}"
        if case["domain"] == "supplement" and not any(marker in query for marker in supplement_markers):
            case["query"] = f"我在选保健品：{query}"


def main() -> None:
    cases = build_cases()
    for case in cases:
        if case["expected_status"] == "no_suitable_candidate":
            case["category"] = "no_suitable_candidate" if case["domain"] == "pet_food" else case["category"]
    add_domain_context(cases)
    validate(cases)
    OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
