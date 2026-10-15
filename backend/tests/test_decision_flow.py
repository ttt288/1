import sys
import types
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.agents.llm_adapter import LLMAdapter
from app.memory import MemoryService
from app.schemas.ai import AIDynamicQuestionPlan, AIQueryUnderstanding, AIResponseSummary
from app.schemas import MemoryItem, ProductOffer
from app.services.decision_service import DecisionService
from app.services.query_understanding_service import QueryUnderstandingService
from app.services.ranking_service import RankingService
from app.schemas import RiskResult, UserProfile


def rule_settings():
    return get_settings().model_copy(update={"llm_enabled": False, "openai_api_key": None})


def test_query_understanding_discards_null_and_invalid_question_answers() -> None:
    class NullAnswerLLM:
        enabled = True
        settings = types.SimpleNamespace(llm_query_understanding_enabled=True)

        def sdk_available(self):
            return True

        def generate_json(self, **kwargs):
            return AIQueryUnderstanding.model_validate(
                {
                    "inferred_answers": {
                        "priority": None,
                        "pet_age_stage": "null",
                        "budget_range": "未在选项中的预算",
                    }
                }
            )

    service = QueryUnderstandingService(NullAnswerLLM())
    answers = service.infer_answers("pet_food", "我想买猫粮，不知道哪个适合我", None)
    assert answers == {"budget_range": "未在选项中的预算"}

    started = DecisionService(rule_settings()).start("我想买猫粮，不知道哪个适合我", "demo_user")
    normalized = service.normalize_answers(answers, started.questions)
    assert normalized == {}


def test_pet_food_decision_flow() -> None:
    service = DecisionService(rule_settings())

    started = service.start("我想买猫粮，不知道哪个适合我", "demo_user")
    assert started.route.domain == "pet_food"
    assert started.candidate_products
    assert started.questions
    assert started.workflow_nodes
    assert any(node.tool_name == "search_products" for node in started.workflow_nodes)
    assert started.feature_analysis.total_products == len(started.candidate_products)
    assert started.feature_analysis.insights
    assert any(
        node.agent == "SupervisorAgent" and node.tool_name == "supervisor.decide"
        for node in started.workflow_nodes
    )
    assert any(node.agent == "ResearchAgent" for node in started.workflow_nodes)
    assert any(node.agent == "DecisionAgent" for node in started.workflow_nodes)
    assert any(event.agent == "SupervisorAgent" and event.event_type == "agent_decision" for event in started.trace_events)
    assert any(event.agent == "ResearchAgent" and event.step == "research.feature_analysis" for event in started.trace_events)

    answered = service.answer(
        started.session_id,
        "demo_user",
        {
            "pet_age_stage": "成猫",
            "digestive_issue": "经常",
            "picky_eater": "有一点",
            "budget_range": "预算优先",
            "priority": "肠胃稳定",
        },
    )

    assert answered.recommendation.top_products
    assert answered.workflow_nodes
    assert any(node.tool_name == "retrieve_knowledge" for node in answered.workflow_nodes)
    assert any(node.agent == "DecisionAgent" and node.tool_name == "retrieve_knowledge" for node in answered.workflow_nodes)
    assert any(node.agent == "ResponseAgent" for node in answered.workflow_nodes)
    assert answered.recommendation.top_products[0].product.domain == "pet_food"
    assert answered.recommendation.knowledge_evidence
    assert answered.recommendation.knowledge_evidence[0].chunk_id
    assert answered.recommendation.knowledge_evidence[0].retrieval_stage in {
        "embedding_keyword_hybrid",
        "embedding_vector",
        "hybrid_keyword_metadata",
    }
    assert answered.recommendation.knowledge_evidence[0].embedding_score >= 0
    assert answered.recommendation.knowledge_evidence[0].rerank_reason
    assert any(event.agent == "DecisionAgent" and event.step == "decision.knowledge_retrieval" for event in answered.trace_events)
    assert answered.runtime_metrics.cache_misses >= 1
    assert answered.trace_events


def test_kitten_growth_prefers_dedicated_kitten_food() -> None:
    service = DecisionService(rule_settings())

    started = service.start("幼猫想买便宜一点但别买成猫粮", "demo_user")
    answered = service.answer(
        started.session_id,
        "demo_user",
        {
            "digestive_issue": "基本不会",
            "picky_eater": "有一点",
            "response_tone": "直接理性",
        },
    )

    top = answered.recommendation.top_products[0].product
    assert top.features["life_stage"] == "幼猫"


def test_pet_food_allergy_blocks_matching_ingredients() -> None:
    service = DecisionService(rule_settings())

    started = service.start("成猫想改善毛发状态，但怀疑鱼肉会过敏", "demo_user")
    assert started.inferred_answers["allergy"] == "有"
    answered = service.answer(
        started.session_id,
        "demo_user",
        {
            "digestive_issue": "基本不会",
            "picky_eater": "不挑食",
            "budget_range": "预算不敏感",
            "response_tone": "直接理性",
        },
    )

    top = answered.recommendation.top_products[0].product
    ingredient_text = " ".join([top.name, *top.features.get("main_ingredients", []), *top.strengths])
    assert "鱼肉" not in ingredient_text
    assert "鱼油" not in ingredient_text


def test_supplement_guardrail_flow() -> None:
    service = DecisionService(rule_settings())

    started = service.start("我想买鱼油，不知道怎么选", "demo_user")
    assert started.route.domain == "supplement"

    answered = service.answer(
        started.session_id,
        "demo_user",
        {
            "health_goal": "心血管健康",
            "medical_context": "有",
            "allergy": "不确定",
            "budget_range": "200元以上",
            "explanation_level": "表格对比",
        },
    )

    assert answered.recommendation.recommendation_status == "no_suitable_candidate"
    assert not answered.recommendation.top_products
    assert "咨询专业人士" in answered.recommendation.summary


def test_supplement_questions_use_single_budget_and_tone_controls() -> None:
    service = DecisionService(rule_settings())
    started = service.start("我想买鱼油保健品，不知道哪个适合我", "demo_user")
    question_ids = [question.id for question in started.questions]
    budget_question = next(question for question in started.questions if question.id == "budget_range")

    assert started.route.domain == "supplement"
    assert "explanation_level" not in question_ids
    assert "response_tone" in question_ids
    assert budget_question.options == ["预算优先", "预算不敏感"]
    assert "/kg" not in budget_question.reason


def test_unknown_category_uses_real_product_candidates_not_archetypes(monkeypatch) -> None:
    def fake_candidates(self, query, discovery, limit=12):
        return [
            ProductOffer(
                id="jd_search_100001",
                domain="general",
                name="美的 MB-HS453S 电饭煲 4L",
                brand="美的",
                price=0,
                spec="规格待核验",
                popularity_score=95,
                features={
                    "external_search_result": True,
                    "price_unverified": True,
                    "detected_claims": ["宿舍", "预约", "不粘内胆"],
                },
                strengths=["真实商品候选", "可跳转京东核验"],
                weaknesses=["价格、规格和参数需打开商品页核验"],
                risk_tags=["external_data_unverified"],
                target_users=["宿舍", "小家庭"],
                source_type="jd_search_result",
                source_url="https://item.jd.com/100001.html",
                data_quality="jd_candidate_price_unverified",
                jd_sku="100001",
                last_updated="待核验",
            )
        ]

    monkeypatch.setattr(
        "app.services.external_discovery_service.ExternalDiscoveryService.discover_product_candidates",
        fake_candidates,
    )
    service = DecisionService(rule_settings())
    started = service.start("我想买电饭煲，宿舍用，不知道怎么选", "demo_user")

    assert started.route.domain == "general"
    assert started.candidate_products[0].name == "美的 MB-HS453S 电饭煲 4L"
    assert all(product.price == 0 for product in started.candidate_products)
    assert all(product.features["price_unverified"] for product in started.candidate_products)
    assert all(product.source_type != "external_search_archetype" for product in started.candidate_products)
    assert started.external_discovery["mode"] == "search_fallback"

    answered = service.answer(
        started.session_id,
        "demo_user",
        {
            "priority": "使用体验",
            "budget_range": "预算不敏感",
            "response_tone": "直接理性",
        },
    )

    assert answered.recommendation.recommendation_status == "recommended"
    assert answered.recommendation.top_products
    assert answered.recommendation.top_products[0].product.price == 0


def test_general_ranking_uses_user_answers_over_hotlist_order() -> None:
    products = [
        ProductOffer(
            id="warm_rank_1",
            domain="general",
            name="雅诗兰黛DW持妆粉底液17#1W1暖调偏白色30ml",
            brand="雅诗兰黛",
            price=0,
            spec="规格待核验",
            popularity_score=100,
            features={"detected_claims": ["持妆", "暖调", "遮瑕"], "price_unverified": True},
            strengths=["公开热榜候选"],
            weaknesses=["价格待核验"],
            risk_tags=["external_data_unverified"],
            target_users=["持妆"],
            source_type="external_hotlist",
            last_updated="待核验",
        ),
        ProductOffer(
            id="neutral_rank_5",
            domain="general",
            name="玫珂菲不挑皮粉底液103N自然中性色30ml",
            brand="玫珂菲",
            price=0,
            spec="规格待核验",
            popularity_score=88,
            features={"detected_claims": ["不挑皮", "自然", "中性"], "price_unverified": True},
            strengths=["公开热榜候选"],
            weaknesses=["价格待核验"],
            risk_tags=["external_data_unverified"],
            target_users=["日常妆", "中性"],
            source_type="external_hotlist",
            last_updated="待核验",
        ),
    ]
    risks = [
        RiskResult(product_id=product.id, risk_score=20, risk_level="medium", notes=["参数待核验"])
        for product in products
    ]
    profile = UserProfile(
        scenario_profile={
            "q1": "持妆强",
            "q2": "日常妆",
            "q3": "中性",
            "budget_range": "预算不敏感",
        }
    )

    ranked = RankingService().rank(products, risks, profile)

    assert ranked[0].product.id == "neutral_rank_5"
    assert ranked[0].final_score > ranked[1].final_score


def test_registered_general_hotlist_can_seed_external_candidates() -> None:
    service = DecisionService(rule_settings())
    started = service.start("我想买降噪耳机，通勤用，不知道怎么选", "demo_user")

    assert started.route.domain == "general"
    assert started.external_discovery["mode"] == "hotlist_snapshot"
    assert started.external_discovery["selected_source"]["id"] == "jd_bluetooth_headphones"
    assert len(started.candidate_products) == 10
    assert started.candidate_products[0].source_type == "external_hotlist"
    assert any(question.id == "product_claim" for question in started.questions)


def test_all_blocked_candidates_return_no_suitable_status() -> None:
    service = DecisionService(rule_settings())

    started = service.start("我想买保健品，正在服药而且不确定有没有过敏", "demo_user")
    answered = service.answer(
        started.session_id,
        "demo_user",
        {
            "health_goal": "基础营养补充",
            "medical_context": "有",
            "allergy": "不确定",
            "budget_range": "100-200元",
            "explanation_level": "表格对比",
        },
    )

    assert answered.recommendation.recommendation_status == "no_suitable_candidate"
    assert answered.recommendation.status_reason
    assert answered.recommendation.knowledge_evidence
    assert not answered.recommendation.top_products
    assert "咨询专业人士" in answered.recommendation.summary


def test_pet_food_playbook_routes_specific_scenarios() -> None:
    service = DecisionService(rule_settings())

    kitten = service.start("我是幼猫需要高品质配方的奶糕，不考虑性价比", "demo_user")
    assert kitten.route.domain == "pet_food"
    assert kitten.route.scenario == "kitten_growth"
    assert kitten.inferred_answers["pet_age_stage"] == "幼猫"
    assert kitten.inferred_answers["priority"] == "成分更好"
    assert "pet_age_stage" in [question.id for question in kitten.questions]

    coat = service.start("我想找能美毛去泪痕的猫粮", "demo_user")
    assert coat.route.domain == "pet_food"
    assert coat.route.scenario == "coat_tear_stain"
    assert coat.inferred_answers["priority"] == "美毛泪痕"
    assert "priority" in [question.id for question in coat.questions]


def test_kitten_answers_penalize_adult_only_food() -> None:
    service = DecisionService(rule_settings())

    started = service.start("我是幼猫需要高品质配方的奶糕，不考虑性价比", "demo_user")
    answered = service.answer(
        started.session_id,
        "demo_user",
        {
            "digestive_issue": "偶尔",
            "budget_range": "预算不敏感",
            "response_tone": "直接理性",
            "picky_eater": "有一点",
        },
    )

    assert answered.recommendation.top_products[0].product.features["life_stage"] == "幼猫"


def test_high_budget_does_not_reuse_low_budget_memory_or_overreward_value() -> None:
    service = DecisionService(rule_settings())

    started = service.start("我想买猫粮，不知道哪个适合我", "demo_user")
    answered = service.answer(
        started.session_id,
        "demo_user",
        {
            "priority": "美毛泪痕",
            "digestive_issue": "经常",
            "pet_age_stage": "成猫",
            "picky_eater": "有一点",
            "budget_range": "预算不敏感",
            "response_tone": "温柔可爱",
        },
    )

    assert answered.recommendation.user_profile.decision_profile["budget_sensitivity"] == 0.35
    assert all(memory.key != "budget_sensitive" for memory in answered.proposed_memories)
    assert answered.recommendation.top_products[0].score_breakdown["value_match"] == 82.0


def test_coat_priority_beats_generic_digestive_match() -> None:
    service = DecisionService(rule_settings())

    started = service.start("我想买猫粮，不知道哪个适合我", "demo_user")
    answered = service.answer(
        started.session_id,
        "demo_user",
        {
            "priority": "美毛泪痕",
            "digestive_issue": "经常",
            "pet_age_stage": "成猫",
            "picky_eater": "不挑食",
            "budget_range": "预算不敏感",
            "response_tone": "温柔可爱",
        },
    )

    top = answered.recommendation.top_products[0]
    assert top.product.id == "pet_food_009"
    assert "匹配美毛泪痕主目标" in top.suitable_reasons


def test_hard_age_exclusion_removes_incompatible_food() -> None:
    service = DecisionService(rule_settings())

    started = service.start("幼猫想买便宜一点但别买成猫粮", "demo_user")
    answered = service.answer(
        started.session_id,
        "demo_user",
        {
            "digestive_issue": "基本不会",
            "picky_eater": "有一点",
            "response_tone": "直接理性",
        },
    )

    assert all(item.product.features["life_stage"] in ["幼猫", "全阶段"] for item in answered.recommendation.top_products)
    assert all(item.product.id != "pet_food_007" for item in answered.recommendation.top_products)


def test_user_can_change_constraints_and_rerank_same_session() -> None:
    service = DecisionService(rule_settings())

    started = service.start("我想买猫粮，不知道哪个适合我", "demo_user")
    first = service.answer(
        started.session_id,
        "demo_user",
        {
            "priority": "性价比",
            "digestive_issue": "基本不会",
            "pet_age_stage": "成猫",
            "picky_eater": "不挑食",
            "budget_range": "预算优先",
            "response_tone": "直接理性",
        },
    )
    second = service.answer(
        started.session_id,
        "demo_user",
        {
            "priority": "美毛泪痕",
            "digestive_issue": "基本不会",
            "pet_age_stage": "成猫",
            "picky_eater": "不挑食",
            "budget_range": "预算不敏感",
            "response_tone": "直接理性",
        },
    )

    assert first.recommendation.top_products[0].product.id != second.recommendation.top_products[0].product.id
    assert any(event.agent == "StateUpdateAgent" for event in second.trace_events)


def test_session_can_resume_after_service_restart() -> None:
    settings = rule_settings()
    service = DecisionService(settings)
    started = service.start("我是幼猫需要高品质配方的奶糕，不考虑性价比", "demo_user")

    restarted = DecisionService(settings)
    answered = restarted.answer(
        started.session_id,
        "demo_user",
        {
            "digestive_issue": "偶尔",
            "picky_eater": "有一点",
            "response_tone": "直接理性",
        },
    )

    assert answered.recommendation.top_products[0].product.features["life_stage"] == "幼猫"


def test_llm_failure_falls_back_without_breaking_recommendation() -> None:
    class BrokenLLM:
        enabled = True
        last_error = "test invalid json"
        last_input_tokens = 5
        last_output_tokens = 0
        last_called = True

        def sdk_available(self):
            return True

        def generate_json(self, **kwargs):
            self.last_error = "test invalid json"
            return None

    service = DecisionService(rule_settings())
    broken = BrokenLLM()
    service.llm_adapter = broken
    service.response_adaptation_service.llm_adapter = broken

    started = service.start("我想买猫粮，不知道哪个适合我", "demo_user")
    answered = service.answer(
        started.session_id,
        "demo_user",
        {
            "priority": "成分更好",
            "digestive_issue": "偶尔",
            "pet_age_stage": "成猫",
            "picky_eater": "有一点",
            "budget_range": "预算不敏感",
            "response_tone": "直接理性",
        },
    )

    assert answered.recommendation.response_mode == "rules_fallback"
    assert answered.recommendation.top_products


def test_general_dynamic_questions_can_be_generated_by_llm() -> None:
    class QuestionLLM:
        enabled = True
        last_error = None
        last_input_tokens = 12
        last_output_tokens = 8
        last_called = False

        def sdk_available(self):
            return True

        def generate_json(self, **kwargs):
            self.last_called = True
            if kwargs["schema_model"] is AIDynamicQuestionPlan:
                return AIDynamicQuestionPlan.model_validate({
                    "questions": [
                        {
                            "id": "wearing_style",
                            "label": "这些候选里，你更想要哪种佩戴方式？",
                            "reason": "热榜候选同时出现开放式、不入耳和半入耳，佩戴方式会明显改变排序。",
                            "options": ["开放式", "不入耳", "半入耳"],
                        }
                    ],
                    "reasoning_summary": "按候选标题差异生成问题。",
                })
            return None

    service = DecisionService(rule_settings())
    service.llm_adapter = QuestionLLM()
    service.question_service.llm_adapter = service.llm_adapter
    started = service.start("我想买降噪耳机，通勤用，不知道怎么选", "demo_user")

    assert started.questions[0].id == "wearing_style"
    assert "开放式" in started.questions[0].options
    question_event = [event for event in started.trace_events if event.step == "question_generation"][-1]
    assert "question_mode=llm" in question_event.output_summary
    assert question_event.token_input == 12


def test_llm_adapter_retries_transient_connection_error(monkeypatch) -> None:
    calls = {"count": 0}

    class FakeResponse:
        output_text = '{"summary":"AI 摘要正常"}'

    class FakeResponses:
        def create(self, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise ConnectionError("temporary connection error")
            return FakeResponse()

    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeClient))
    settings = rule_settings().model_copy(
        update={
            "llm_enabled": True,
            "openai_api_key": "test-key",
            "llm_max_retries": 1,
        }
    )
    adapter = LLMAdapter(settings)

    result = adapter.generate_json(
        schema_model=AIResponseSummary,
        system_prompt="返回摘要",
        user_payload={"top_products": []},
    )

    assert calls["count"] == 2
    assert result.summary == "AI 摘要正常"
    assert adapter.last_error is None


def test_product_discovery_cache_is_reported() -> None:
    service = DecisionService(rule_settings())

    first = service.start("我想买猫粮，不知道哪个适合我", "demo_user")
    second = service.start("我想买猫粮，不知道哪个适合我", "demo_user")

    first_discovery = [event for event in first.trace_events if event.step == "product_discovery"][-1]
    second_discovery = [event for event in second.trace_events if event.step == "product_discovery"][-1]

    assert "cache=miss" in first_discovery.output_summary
    assert "cache=hit" in second_discovery.output_summary


def test_unverified_jd_rank_does_not_affect_candidates() -> None:
    service = DecisionService(rule_settings())

    started = service.start("我想买猫粮，不知道哪个适合我", "demo_user")
    assert all(product.market_rank is None for product in started.candidate_products)
    assert all(product.last_updated == "2026-09-03" for product in started.candidate_products)


def test_verified_rank_preserves_product_date_and_requires_matching_package() -> None:
    from app.data_providers.local_product_provider import LocalProductProvider

    provider = LocalProductProvider(rule_settings().data_dir)
    product = provider.list_products("pet_food")[0].model_copy(update={"jd_sku": "test-sku"})
    provider._ranking_signals = {product.id: {
        "rank": 1, "jd_sku": "test-sku", "spec": product.spec,
        "match_status": "verified_exact_sku", "observed_at": "2026-09-08",
    }}
    updated = provider._apply_ranking_signal(product)
    assert updated.market_rank == 1
    assert updated.last_updated == product.last_updated
    assert updated.ranking_observed_at == "2026-09-08"
    assert updated.data_quality == "rank_verified_price_unverified"
    wrong_package = product.model_copy(update={"spec": "different-package"})
    assert provider._apply_ranking_signal(wrong_package).market_rank is None


def test_comparison_returns_price_not_transformed_score() -> None:
    service = DecisionService(rule_settings())
    started = service.start("我想买猫粮", "demo_user")
    comparison = service._current_comparison(started.session_id, [], started.candidate_products)
    assert comparison["value_unit_price"] == min(p.unit_price for p in started.candidate_products)
    assert comparison["value_unit_label"] == "元/kg"


def test_evaluation_counts_only_unanswered_questions() -> None:
    from app.evaluation.evaluation_service import EvaluationService

    evaluator = EvaluationService(rule_settings())
    case = evaluator._load_cases()[0]
    started = evaluator.decision_service.start(case["query"], "demo_user")
    result = evaluator._run_case(case)
    assert started.inferred_answers
    assert result.question_count == len([q for q in started.questions if q.id not in started.inferred_answers])
    assert result.question_count < len(started.questions)


def test_evaluation_loads_independent_experiment_set() -> None:
    from app.evaluation.evaluation_service import EvaluationService

    evaluator = EvaluationService(rule_settings())
    summary = evaluator.run()

    assert summary.independent_cases >= 100
    assert summary.dataset_validation.validation_passed
    assert summary.dataset_validation.duplicate_queries == 0
    assert {"regression", "holdout", "independent"}.issubset(
        {item.split for item in summary.split_summaries}
    )
    assert {
        "unfamiliar_expression",
        "combination_constraints",
        "no_suitable_candidate",
        "condition_revision",
        "budget_unit_price",
        "allergy_boundary",
        "supplement_guardrail",
    }.issubset(summary.dataset_validation.independent_categories)


def test_condition_revision_cases_execute_second_turn() -> None:
    from app.evaluation.evaluation_service import EvaluationService

    evaluator = EvaluationService(rule_settings())
    case = next(
        item
        for item in evaluator._load_cases()
        if item.get("split") == "independent" and item.get("category") == "condition_revision"
    )
    result = evaluator._run_case(case)

    assert result.revision_test_passed
    assert result.actual_status == result.expected_status


def test_dynamic_questions_explain_source_and_evidence() -> None:
    service = DecisionService(rule_settings())

    started = service.start("我是幼猫需要高品质配方的奶糕，不考虑性价比", "demo_user")
    by_id = {question.id: question for question in started.questions}

    assert by_id["pet_age_stage"].source == "scenario_playbook"
    assert "life_stage_diff" in by_id["pet_age_stage"].evidence
    assert by_id["response_tone"].source == "communication_preference"


def test_confirmed_memory_is_persisted_and_merged(tmp_path) -> None:
    settings = rule_settings().model_copy(update={"project_root": tmp_path})
    (tmp_path / "data").mkdir()
    service = MemoryService(settings.data_dir)
    memory = MemoryItem(
        id="mem_test",
        user_id="demo_user",
        memory_type="communication_preference",
        domain="global",
        key="response_tone",
        value="cute_supportive",
        confidence=0.9,
        source="test",
        evidence="test evidence",
        requires_confirmation=True,
    )

    service.apply_memory_decision(memory, "remember")
    saved = service.list_memories("demo_user")

    assert len(saved) == 1
    assert saved[0].requires_confirmation is False


def test_memory_expires_and_confidence_decays(tmp_path) -> None:
    settings = rule_settings().model_copy(update={"project_root": tmp_path})
    (tmp_path / "data").mkdir()
    service = MemoryService(settings.data_dir)
    old = datetime.now(UTC) - timedelta(days=240)
    memory = MemoryItem(
        id="mem_old",
        user_id="demo_user",
        memory_type="communication_preference",
        domain="global",
        key="response_tone",
        value="cute_supportive",
        confidence=0.9,
        source="test",
        evidence="old evidence",
        created_at=old,
        last_updated=old,
        expires_at=old + timedelta(days=10),
        requires_confirmation=False,
    )

    service.save_memory(memory)

    assert service.list_memories("demo_user") == []
    raw = service._read_all_memories("demo_user")
    assert raw[0].status == "expired"


def test_memory_conflict_marks_old_value_inactive(tmp_path) -> None:
    settings = rule_settings().model_copy(update={"project_root": tmp_path})
    (tmp_path / "data").mkdir()
    service = MemoryService(settings.data_dir)
    first = MemoryItem(
        id="mem_first",
        user_id="demo_user",
        memory_type="communication_preference",
        domain="global",
        key="response_tone",
        value="cute_supportive",
        confidence=0.9,
        source="test",
        evidence="first",
    )
    second = MemoryItem(
        id="mem_second",
        user_id="demo_user",
        memory_type="communication_preference",
        domain="global",
        key="response_tone",
        value="direct_rational",
        confidence=0.88,
        source="test",
        evidence="second",
    )

    service.save_memory(first)
    service.save_memory(second)

    active = service.list_memories("demo_user")
    raw = service._read_all_memories("demo_user")

    assert [item.value for item in active] == ["direct_rational"]
    assert any(item.status == "conflicted" and item.value == "cute_supportive" for item in raw)
