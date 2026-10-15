from typing import Any

from app.schemas.agent import AgentTask, WorkResult
from app.tracing.trace_service import trace_step


RESEARCH_AGENT_PROMPT = """ResearchAgent:
- 负责商品和知识资料侧工作。
- 可以识别品类、选择外部热榜、召回商品、分析商品差异、召回 RAG 证据。
- 不做最终排序，不写长期记忆，不生成面向用户的最终结论。"""

DECISION_AGENT_PROMPT = """DecisionAgent:
- 负责决策计算侧工作。
- 可以理解用户回答、生成必要追问、构建用户画像、执行风险判断和个性化排序。
- 风险和排序必须调用确定性工具，不允许只靠语言模型判断。"""

RESPONSE_AGENT_PROMPT = """ResponseAgent:
- 负责最终表达和交付。
- 只能基于 ResearchAgent/DecisionAgent 的 WorkResult 汇总，不编造商品事实。
- 负责调用表达适配和记忆提案工具。"""


class ResearchAgent:
    name = "ResearchAgent"
    prompt = RESEARCH_AGENT_PROMPT

    def __init__(self, owner) -> None:
        self.owner = owner

    def run_start(self, *, session_id: str, task: AgentTask, state: dict[str, Any]) -> WorkResult:
        query = state["query"]
        used_tools: list[str] = []
        with trace_step() as timer:
            route = self.owner.tools.call("route_domain", query=query)
            used_tools.append("route_domain")
        self.owner._record_agent_tool(
            session_id=session_id,
            agent="ResearchAgent",
            step="research.route_domain",
            tool_name="route_domain",
            input_summary=query,
            output_summary=f"domain={route.domain}, confidence={route.confidence}",
            latency_ms=timer.latency_ms(),
        )

        with trace_step() as timer:
            external_discovery = self.owner.tools.call(
                "discover_external_hotlist",
                query=query,
                domain=route.domain,
                scenario=route.scenario,
                refresh=state.get("use_external_refresh", False),
            )
            used_tools.append("discover_external_hotlist")
        self.owner._record_agent_tool(
            session_id=session_id,
            agent="ResearchAgent",
            step="research.external_discovery",
            tool_name="discover_external_hotlist",
            input_summary=f"domain={route.domain}, scenario={route.scenario or 'general'}",
            output_summary=f"mode={external_discovery.get('mode')}, entries={len((external_discovery.get('snapshot') or {}).get('entries') or [])}",
            latency_ms=timer.latency_ms(),
            fallback_used=external_discovery.get("mode") != "hotlist_snapshot",
            token_input=self.owner.external_discovery_service.last_llm_input_tokens,
            token_output=self.owner.external_discovery_service.last_llm_output_tokens,
        )

        with trace_step() as timer:
            products = self.owner.tools.call("search_products", domain=route.domain, query=query, limit=20)
            used_tools.append("search_products")
            if not products and external_discovery.get("mode") == "hotlist_snapshot":
                products = self.owner.external_discovery_service.to_product_offers(
                    external_discovery,
                    route.domain,
                    limit=20,
                )
            if not products and route.domain == "general":
                products = self.owner.external_discovery_service.discover_product_candidates(
                    query=query,
                    discovery=external_discovery,
                    limit=12,
                )
        self.owner._record_agent_tool(
            session_id=session_id,
            agent="ResearchAgent",
            step="product_discovery",
            tool_name="search_products",
            input_summary=f"domain={route.domain}, query={query}",
            output_summary=f"retrieved={len(products)}, cache={'hit' if self.owner.product_search_service.last_cache_hit else 'miss'}",
            latency_ms=timer.latency_ms(),
        )

        with trace_step() as timer:
            feature_analysis = self.owner.tools.call(
                "analyze_product_features",
                domain=route.domain,
                products=products,
            )
            used_tools.append("analyze_product_features")
        self.owner._record_agent_tool(
            session_id=session_id,
            agent="ResearchAgent",
            step="research.feature_analysis",
            tool_name="analyze_product_features",
            input_summary=f"products={len(products)}",
            output_summary=", ".join(insight.label for insight in feature_analysis.insights[:4]),
            latency_ms=timer.latency_ms(),
        )

        output = {
            "route": route,
            "external_discovery": external_discovery,
            "products": products,
            "feature_analysis": feature_analysis,
        }
        return WorkResult(
            task_id=task.id,
            agent_name="ResearchAgent",
            summary=f"召回 {len(products)} 个候选并完成差异分析。",
            output=output,
            used_tools=used_tools,
        )


class DecisionAgent:
    name = "DecisionAgent"
    prompt = DECISION_AGENT_PROMPT

    def __init__(self, owner) -> None:
        self.owner = owner

    def run_start(self, *, session_id: str, task: AgentTask, state: dict[str, Any]) -> WorkResult:
        route = state["route"]
        products = state.get("products", [])
        used_tools: list[str] = []
        with trace_step() as timer:
            domain_config = self.owner.domain_service.get_config(route.domain)
            questions = self.owner.tools.call(
                "generate_dynamic_questions",
                domain_config=domain_config,
                products=products,
                scenario=route.scenario,
            )
            used_tools.append("generate_dynamic_questions")
            question_mode = self.owner.question_service.last_mode
            question_input_tokens = self.owner.llm_adapter.last_input_tokens if question_mode == "llm" else 0
            question_output_tokens = self.owner.llm_adapter.last_output_tokens if question_mode == "llm" else 0
            inferred_answers = self.owner.tools.call(
                "infer_query_answers",
                domain=route.domain,
                query=state["query"],
                scenario=route.scenario,
            )
            used_tools.append("infer_query_answers")
        self.owner._record_agent_tool(
            session_id=session_id,
            agent="DecisionAgent",
            step="question_generation",
            tool_name="generate_dynamic_questions + infer_query_answers",
            input_summary=f"products={len(products)}",
            output_summary=f"questions={len(questions)}, inferred={list(inferred_answers.keys())}, question_mode={question_mode}",
            latency_ms=timer.latency_ms(),
            token_input=question_input_tokens,
            token_output=question_output_tokens,
        )
        return WorkResult(
            task_id=task.id,
            agent_name="DecisionAgent",
            summary=f"生成 {len(questions)} 个动态问题，并识别 {len(inferred_answers)} 个已知条件。",
            output={"questions": questions, "inferred_answers": inferred_answers},
            used_tools=used_tools,
        )

    def run_answer(self, *, session_id: str, task: AgentTask, state: dict[str, Any]) -> WorkResult:
        route = state["route"]
        products = state.get("products", [])
        answers = state["answers"]
        used_tools: list[str] = []

        with trace_step() as timer:
            user_profile = self.owner.tools.call(
                "build_user_profile",
                user_id=state["user_id"],
                domain=route.domain,
                answers=answers,
            )
            used_tools.append("build_user_profile")
        self.owner._record_agent_tool(
            session_id=session_id,
            agent="DecisionAgent",
            step="decision.user_modeling",
            tool_name="build_user_profile",
            input_summary=f"answers={len(answers)}",
            output_summary=f"risk_sensitivity={user_profile.decision_profile.get('risk_sensitivity')}",
            latency_ms=timer.latency_ms(),
        )

        with trace_step() as timer:
            risk_results = self.owner.tools.call("evaluate_risk", products=products, user_profile=user_profile)
            used_tools.append("evaluate_risk")
        high_risk_count = sum(1 for item in risk_results if item.risk_level == "high")
        blocked_count = sum(1 for item in risk_results if item.blocked)
        no_suitable_candidate = not products or blocked_count == len(products)
        self.owner._record_agent_tool(
            session_id=session_id,
            agent="DecisionAgent",
            step="decision.risk_analysis",
            tool_name="evaluate_risk",
            input_summary=f"products={len(products)}",
            output_summary=f"high_risk={high_risk_count}, blocked={blocked_count}, no_suitable={no_suitable_candidate}",
            latency_ms=timer.latency_ms(),
            fallback_used=high_risk_count > 0 or no_suitable_candidate,
        )

        with trace_step() as timer:
            ranked = self.owner.tools.call(
                "rank_products",
                products=products,
                risk_results=risk_results,
                user_profile=user_profile,
            )
            used_tools.append("rank_products")
        self.owner._record_agent_tool(
            session_id=session_id,
            agent="DecisionAgent",
            step="decision.personalized_ranking",
            tool_name="rank_products",
            input_summary=f"products={len(products)}",
            output_summary=f"top={ranked[0].product.name if ranked else 'none'}",
            latency_ms=timer.latency_ms(),
        )

        with trace_step() as timer:
            knowledge_evidence = self.owner.tools.call(
                "retrieve_knowledge",
                domain=route.domain,
                query=state["query"],
                products=[item.product for item in ranked[:5]] if ranked else products[:5],
                user_profile=user_profile,
            )
            used_tools.append("retrieve_knowledge")
        self.owner._record_agent_tool(
            session_id=session_id,
            agent="DecisionAgent",
            step="decision.knowledge_retrieval",
            tool_name="retrieve_knowledge",
            input_summary=f"domain={route.domain}, top_products={min(len(ranked), 5)}",
            output_summary=f"evidence={len(knowledge_evidence)}, cache={'hit' if self.owner.knowledge_service.last_cache_hit else 'miss'}",
            latency_ms=timer.latency_ms(),
        )

        return WorkResult(
            task_id=task.id,
            agent_name="DecisionAgent",
            summary=f"完成风险判断、排序和 {len(knowledge_evidence)} 条 RAG 证据召回。",
            output={
                "user_profile": user_profile,
                "risk_results": risk_results,
                "ranked": ranked,
                "knowledge_evidence": knowledge_evidence,
                "no_suitable_candidate": no_suitable_candidate,
            },
            used_tools=used_tools,
        )


class ResponseAgent:
    name = "ResponseAgent"
    prompt = RESPONSE_AGENT_PROMPT

    def __init__(self, owner) -> None:
        self.owner = owner

    def run_answer(self, *, session_id: str, task: AgentTask, state: dict[str, Any]) -> WorkResult:
        route = state["route"]
        ranked = state.get("ranked", [])
        products = state.get("products", [])
        risk_results = state.get("risk_results", [])
        user_profile = state["user_profile"]
        no_suitable_candidate = state.get("no_suitable_candidate", False)
        display_ranked = [] if no_suitable_candidate else ranked[:3]
        used_tools: list[str] = []

        with trace_step() as timer:
            if no_suitable_candidate:
                summary = self.owner._no_suitable_summary(route.domain, risk_results, products)
            else:
                summary = self.owner.tools.call(
                    "adapt_response",
                    domain=route.domain,
                    ranked=ranked,
                    communication_profile=user_profile.communication_profile,
                )
                used_tools.append("adapt_response")
        self.owner._record_agent_tool(
            session_id=session_id,
            agent="ResponseAgent",
            step="response.final_summary",
            tool_name="adapt_response",
            input_summary=f"top_products={min(len(display_ranked), 3)}",
            output_summary=self.owner._response_mode_summary(),
            latency_ms=timer.latency_ms(),
            token_input=self.owner.llm_adapter.last_input_tokens,
            token_output=self.owner.llm_adapter.last_output_tokens,
            fallback_used=self.owner._response_mode() == "rules_fallback",
        )

        with trace_step() as timer:
            proposed_memories = self.owner.tools.call("propose_memory_updates", user_profile=user_profile)
            used_tools.append("propose_memory_updates")
        self.owner._record_agent_tool(
            session_id=session_id,
            agent="ResponseAgent",
            step="response.memory_update",
            tool_name="propose_memory_updates",
            input_summary="profile and conversation",
            output_summary=f"proposals={len(proposed_memories)}",
            latency_ms=timer.latency_ms(),
        )

        return WorkResult(
            task_id=task.id,
            agent_name="ResponseAgent",
            summary="完成最终摘要、展示结构和记忆提案。",
            output={
                "summary": summary,
                "display_ranked": display_ranked,
                "proposed_memories": proposed_memories,
                "response_mode": self.owner._response_mode(),
            },
            used_tools=used_tools,
        )
