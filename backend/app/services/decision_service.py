import json
from pathlib import Path
from threading import RLock
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.agents import LLMAdapter
from app.core.config import Settings
from app.data_providers import JDTopListProvider, LocalProductProvider, MarketResearchProvider
from app.graph import MultiAgentDecisionGraph
from app.memory import MemoryService
from app.schemas import (
    DecisionAnswerResponse,
    DecisionStartResponse,
    DomainRoute,
    FeatureAnalysis,
    ProductOffer,
    Question,
    RecommendationResult,
    RiskResult,
    RuntimeMetrics,
    WorkflowNodeRecord,
)
from app.services.domain_service import DomainService
from app.services.external_discovery_service import ExternalDiscoveryService
from app.services.feature_analysis_service import FeatureAnalysisService
from app.services.knowledge_service import KnowledgeService
from app.services.profile_service import ProfileService
from app.services.product_search_service import ProductSearchService
from app.services.question_service import QuestionService
from app.services.query_understanding_service import QueryUnderstandingService
from app.services.ranking_service import RankingService
from app.services.response_adaptation_service import ResponseAdaptationService
from app.services.risk_service import RiskService
from app.tracing import TraceService
from app.tracing.trace_service import trace_step
from app.workflow import DecisionWorkflowState, ToolRegistry


class StartPlannerState(TypedDict, total=False):
    query: str
    user_id: str
    use_external_refresh: bool
    route: DomainRoute
    external_discovery: dict
    products: list[ProductOffer]
    feature_analysis: FeatureAnalysis
    questions: list[Question]
    inferred_answers: dict
    next_action: str
    planner_reason: str
    planner_turn: int


class DecisionService:
    def __init__(self, settings: Settings) -> None:
        self.domain_service = DomainService(settings.data_dir)
        self.llm_adapter = LLMAdapter(settings)
        local_provider = LocalProductProvider(settings.data_dir)
        self.product_search_service = ProductSearchService(
            providers=[
                JDTopListProvider(settings.data_dir),
                MarketResearchProvider(local_provider),
            ]
        )
        self.question_service = QuestionService(self.llm_adapter)
        self.feature_analysis_service = FeatureAnalysisService()
        self.knowledge_service = KnowledgeService(settings.data_dir)
        self.external_discovery_service = ExternalDiscoveryService(settings.data_dir, self.llm_adapter)
        self.query_understanding_service = QueryUnderstandingService(self.llm_adapter)
        self.response_adaptation_service = ResponseAdaptationService(self.llm_adapter)
        self.profile_service = ProfileService()
        self.risk_service = RiskService()
        self.ranking_service = RankingService()
        self.memory_service = MemoryService(settings.data_dir)
        self.trace_service = TraceService()
        self.tools = ToolRegistry()
        self._register_tools()
        self.multi_agent_graph = MultiAgentDecisionGraph(self)
        self._session_dir = settings.data_dir / "sessions"
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._session_lock = RLock()
        self._sessions: dict[str, dict] = {}

    def _register_tools(self) -> None:
        self.tools.register(
            name="route_domain",
            description="识别用户需求所属品类和子场景。",
            handler=lambda query: self.domain_service.route(query),
        )
        self.tools.register(
            name="search_products",
            description="按品类召回候选商品，带本地 TTL 缓存。",
            handler=lambda domain, query, limit: self.product_search_service.search(domain, query, limit=limit),
        )
        self.tools.register(
            name="discover_external_hotlist",
            description="根据品类、子场景和 query 选择公开热榜源；失败时回退到搜索入口。",
            handler=lambda query, domain, scenario, refresh: self.external_discovery_service.discover(
                query=query,
                domain=domain,
                scenario=scenario,
                refresh=refresh,
            ),
        )
        self.tools.register(
            name="analyze_product_features",
            description="分析候选商品在价格、场景、功能、风险上的差异。",
            handler=lambda domain, products: self.feature_analysis_service.analyze(domain, products),
        )
        self.tools.register(
            name="generate_dynamic_questions",
            description="根据场景 playbook 和商品差异生成关键问题。",
            handler=lambda domain_config, products, scenario: self.question_service.generate_questions(
                domain_config,
                products,
                scenario,
            ),
        )
        self.tools.register(
            name="infer_query_answers",
            description="从用户自然语言中抽取已明确的回答，避免重复追问。",
            handler=lambda domain, query, scenario: self.query_understanding_service.infer_answers(
                domain,
                query,
                scenario,
            ),
        )
        self.tools.register(
            name="build_user_profile",
            description="把本次回答和长期表达偏好合并为用户画像。",
            handler=self._build_user_profile,
        )
        self.tools.register(
            name="evaluate_risk",
            description="执行宠物食品/保健品风险规则。",
            handler=lambda products, user_profile: [
                self.risk_service.evaluate(product, user_profile) for product in products
            ],
        )
        self.tools.register(
            name="rank_products",
            description="结合画像、风险、热度、价格和偏好进行个性化排序。",
            handler=lambda products, risk_results, user_profile: self.ranking_service.rank(
                products,
                risk_results,
                user_profile,
            ),
        )
        self.tools.register(
            name="retrieve_knowledge",
            description="从本地知识库检索推荐依据。",
            handler=lambda domain, query, products, user_profile: self.knowledge_service.retrieve(
                domain=domain,
                query=query,
                products=products,
                user_profile=user_profile,
            ),
        )
        self.tools.register(
            name="adapt_response",
            description="用 LLM 或规则生成适配用户表达偏好的摘要。",
            handler=lambda domain, ranked, communication_profile: self.response_adaptation_service.build_summary(
                domain=domain,
                ranked=ranked,
                communication_profile=communication_profile,
            ),
        )
        self.tools.register(
            name="propose_memory_updates",
            description="提出需要用户确认的长期记忆写入。",
            handler=lambda user_profile: self.memory_service.propose_updates(user_profile),
        )

    def _build_user_profile(self, user_id: str, domain: str, answers: dict):
        user_profile = self.profile_service.build_profile(user_id, domain, answers)
        return self.memory_service.merge_into_profile(user_profile)

    def start(self, query: str, user_id: str, use_external_refresh: bool = False) -> DecisionStartResponse:
        session_id = f"session_{uuid4().hex[:10]}"
        workflow = DecisionWorkflowState()
        loop_state = self.multi_agent_graph.run_start(
            session_id=session_id,
            workflow=workflow,
            query=query,
            user_id=user_id,
            use_external_refresh=use_external_refresh,
        )

        route = loop_state["route"]
        external_discovery = loop_state.get("external_discovery", {})
        products = loop_state.get("products", [])
        feature_analysis = loop_state["feature_analysis"]
        questions = loop_state["questions"]
        inferred_answers = loop_state["inferred_answers"]

        session_state = {
            "query": query,
            "user_id": user_id,
            "route": route,
            "candidate_products": products,
            "external_discovery": external_discovery,
            "feature_analysis": feature_analysis,
            "questions": questions,
            "inferred_answers": inferred_answers,
            "workflow_nodes": workflow.nodes,
            "latest_answers": {},
            "revision_count": 0,
        }
        with self._session_lock:
            self._sessions[session_id] = session_state
            self._save_session(session_id, session_state)

        return DecisionStartResponse(
            session_id=session_id,
            route=route,
            candidate_products=products,
            feature_analysis=feature_analysis,
            questions=questions,
            inferred_answers=inferred_answers,
            external_discovery=external_discovery,
            workflow_nodes=workflow.nodes,
            trace_events=self.trace_service.list_events(session_id),
        )

    def answer(self, session_id: str, user_id: str, answers: dict) -> DecisionAnswerResponse:
        with self._session_lock:
            if session_id not in self._sessions:
                loaded = self._load_session(session_id)
                if loaded:
                    self._sessions[session_id] = loaded

        if session_id not in self._sessions:
            raise KeyError(f"Unknown session_id: {session_id}")

        session = self._sessions[session_id]
        workflow = DecisionWorkflowState()
        for node in session.get("workflow_nodes", []):
            workflow.nodes.append(node)
        route = session["route"]
        products = session["candidate_products"]
        previous_answers = dict(session.get("latest_answers") or {})
        changed_keys = [
            key for key, value in answers.items()
            if previous_answers.get(key) != value
        ]
        if previous_answers and changed_keys:
            session["revision_count"] = int(session.get("revision_count", 0)) + 1
            self.trace_service.add_event(
                session_id=session_id,
                step="state_revision",
                agent="StateUpdateAgent",
                event_type="agent_completed",
                input_summary=f"changed={changed_keys}",
                output_summary="用户中途修改约束，保留候选池并重新构建画像、风险和排序。",
                latency_ms=0,
            )
        answers = {
            **session.get("inferred_answers", {}),
            **answers,
            "_scenario": route.scenario,
        }

        agent_state = self.multi_agent_graph.run_answer(
            session_id=session_id,
            workflow=workflow,
            state={
                **session,
                "answers": answers,
            },
        )
        user_profile = agent_state["user_profile"]
        risk_results = agent_state["risk_results"]
        ranked = agent_state["ranked"]
        knowledge_evidence = agent_state["knowledge_evidence"]
        no_suitable_candidate = agent_state.get("no_suitable_candidate", False)
        display_ranked = agent_state.get("display_ranked", [] if no_suitable_candidate else ranked[:3])
        summary = agent_state["summary"]
        proposed_memories = agent_state["proposed_memories"]
        response_mode = agent_state["response_mode"]
        recommendation = RecommendationResult(
            session_id=session_id,
            domain=route.domain,
            user_profile=user_profile,
            top_products=display_ranked,
            knowledge_evidence=knowledge_evidence,
            summary=summary,
            response_mode=response_mode,
            recommendation_status="no_suitable_candidate" if no_suitable_candidate else "recommended",
            status_reason=self._no_suitable_reason(risk_results, products) if no_suitable_candidate else "",
            table_columns=["排名", "产品", "价格", "单价", "适合原因", "长处", "缺点", "风险提示", "置信度"],
        )

        response = DecisionAnswerResponse(
            recommendation=recommendation,
            proposed_memories=proposed_memories,
            trace_events=self.trace_service.list_events(session_id),
            current_comparison=self._current_comparison(
                session_id=session_id,
                ranked=recommendation.top_products,
                products=products,
            ),
            runtime_metrics=self._runtime_metrics(session_id),
            workflow_nodes=workflow.nodes,
        )
        with self._session_lock:
            session["latest_answers"] = dict(answers)
            session["workflow_nodes"] = workflow.nodes
            session["last_recommendation"] = recommendation
            self._sessions[session_id] = session
            self._save_session(session_id, session)
        return response

    def list_traces(self, session_id: str):
        return self.trace_service.list_events(session_id)

    def list_memories(self, user_id: str):
        return self.memory_service.list_memories(user_id)

    def apply_memory_decision(self, memory, action: str):
        return self.memory_service.apply_memory_decision(memory, action)

    def _record_agent_tool(
        self,
        *,
        session_id: str,
        agent: str,
        step: str,
        tool_name: str,
        input_summary: str,
        output_summary: str,
        latency_ms: int = 0,
        token_input: int = 0,
        token_output: int = 0,
        fallback_used: bool = False,
    ) -> None:
        workflow = getattr(self, "_active_workflow", None)
        if workflow is not None:
            workflow.complete(
                node_id=step.replace(".", "_"),
                agent=agent,
                tool_name=tool_name,
                reason=output_summary,
                handoff_to="SupervisorAgent",
                fallback_used=fallback_used,
            )
        self.trace_service.add_event(
            session_id=session_id,
            step=step,
            agent=agent,
            event_type="tool_completed",
            input_summary=input_summary,
            output_summary=output_summary,
            latency_ms=latency_ms,
            token_input=token_input,
            token_output=token_output,
        )

    def _run_start_agent_graph(
        self,
        session_id: str,
        workflow: DecisionWorkflowState,
        initial_state: StartPlannerState,
    ) -> StartPlannerState:
        graph = StateGraph(StartPlannerState)

        def plan_next_action(state: StartPlannerState) -> StartPlannerState:
            turn = int(state.get("planner_turn", 0)) + 1
            if turn > 7:
                raise RuntimeError("PlannerAgent failed to finish start graph within 7 turns")
            action, reason = self._next_start_action(state)
            workflow.complete(
                node_id=f"planner_start_{turn}",
                agent="PlannerAgent",
                tool_name="langgraph.plan_next_action",
                reason=reason,
                handoff_to=action,
            )
            self.trace_service.add_event(
                session_id=session_id,
                step="planner_loop",
                agent="PlannerAgent",
                event_type="agent_decision",
                input_summary=self._planner_state_summary(state),
                output_summary=f"next={action}, reason={reason}",
            )
            return {
                "next_action": action,
                "planner_reason": reason,
                "planner_turn": turn,
            }

        def route_domain_node(state: StartPlannerState) -> StartPlannerState:
            query = state["query"]
            with trace_step() as timer:
                route = self.tools.call("route_domain", query=query)
            workflow.complete(
                node_id="domain_router",
                agent="DomainRouterAgent",
                tool_name="route_domain",
                reason="先识别品类和子场景，决定后续召回和问题模板。",
                handoff_to="planner",
            )
            self.trace_service.add_event(
                session_id=session_id,
                step="domain_router",
                agent="DomainRouterAgent",
                event_type="agent_completed",
                input_summary=query,
                output_summary=f"domain={route.domain}, confidence={route.confidence}",
                latency_ms=timer.latency_ms(),
            )
            return {"route": route}

        def discover_external_hotlist_node(state: StartPlannerState) -> StartPlannerState:
            route = state["route"]
            with trace_step() as timer:
                external_discovery = self.tools.call(
                    "discover_external_hotlist",
                    query=state["query"],
                    domain=route.domain,
                    scenario=route.scenario,
                    refresh=state["use_external_refresh"],
                )
            workflow.complete(
                node_id="external_discovery",
                agent="ExternalDiscoveryAgent",
                tool_name="discover_external_hotlist",
                reason="选择可用公开热榜源或搜索入口；外部数据只作为候选召回和热度参考。",
                handoff_to="planner",
                fallback_used=external_discovery.get("mode") != "hotlist_snapshot",
            )
            self.trace_service.add_event(
                session_id=session_id,
                step="external_discovery",
                agent="ExternalDiscoveryAgent",
                event_type="tool_completed",
                input_summary=f"domain={route.domain}, scenario={route.scenario or 'general'}",
                output_summary=(
                    f"mode={external_discovery.get('mode')}, "
                    f"source={(external_discovery.get('selected_source') or {}).get('id', 'none')}, "
                    f"method={external_discovery.get('discovery_method')}, "
                    f"entries={len((external_discovery.get('snapshot') or {}).get('entries') or [])}"
                ),
                latency_ms=timer.latency_ms(),
                token_input=self.external_discovery_service.last_llm_input_tokens,
                token_output=self.external_discovery_service.last_llm_output_tokens,
            )
            return {"external_discovery": external_discovery}

        def search_products_node(state: StartPlannerState) -> StartPlannerState:
            route = state["route"]
            external_discovery = state.get("external_discovery", {})
            with trace_step() as timer:
                products = self.tools.call("search_products", domain=route.domain, query=state["query"], limit=20)
                if not products and external_discovery.get("mode") == "hotlist_snapshot":
                    products = self.external_discovery_service.to_product_offers(
                        external_discovery,
                        route.domain,
                        limit=20,
                    )
                if not products and route.domain == "general":
                    products = self.external_discovery_service.discover_product_candidates(
                        query=state["query"],
                        discovery=external_discovery,
                        limit=12,
                    )
            workflow.complete(
                node_id="product_discovery",
                agent="ProductDiscoveryAgent",
                tool_name="search_products",
                reason="召回候选商品池，后续问题必须基于这些商品的真实差异。",
                handoff_to="planner",
            )
            self.trace_service.add_event(
                session_id=session_id,
                step="product_discovery",
                agent="ProductDiscoveryAgent",
                event_type="tool_completed",
                input_summary=f"domain={route.domain}, query={state['query']}",
                output_summary=(
                    f"retrieved={len(products)} products, external_mode={external_discovery.get('mode')}, "
                    f"cache={'hit' if self.product_search_service.last_cache_hit else 'miss'}"
                ),
                latency_ms=timer.latency_ms(),
            )
            return {"products": products}

        def analyze_product_features_node(state: StartPlannerState) -> StartPlannerState:
            route = state["route"]
            products = state.get("products", [])
            with trace_step() as timer:
                feature_analysis = self.tools.call(
                    "analyze_product_features",
                    domain=route.domain,
                    products=products,
                )
            workflow.complete(
                node_id="feature_analysis",
                agent="FeatureAnalysisAgent",
                tool_name="analyze_product_features",
                reason="提取候选商品之间的决策差异，用于动态问题生成。",
                handoff_to="planner",
            )
            self.trace_service.add_event(
                session_id=session_id,
                step="feature_analysis",
                agent="FeatureAnalysisAgent",
                event_type="agent_completed",
                input_summary=f"products={len(products)}",
                output_summary=", ".join(insight.label for insight in feature_analysis.insights[:4]),
                latency_ms=timer.latency_ms(),
            )
            return {"feature_analysis": feature_analysis}

        def generate_dynamic_questions_node(state: StartPlannerState) -> StartPlannerState:
            route = state["route"]
            products = state.get("products", [])
            with trace_step() as timer:
                domain_config = self.domain_service.get_config(route.domain)
                questions = self.tools.call(
                    "generate_dynamic_questions",
                    domain_config=domain_config,
                    products=products,
                    scenario=route.scenario,
                )
                question_mode = self.question_service.last_mode
                question_input_tokens = self.llm_adapter.last_input_tokens if question_mode == "llm" else 0
                question_output_tokens = self.llm_adapter.last_output_tokens if question_mode == "llm" else 0
                inferred_answers = self.tools.call(
                    "infer_query_answers",
                    domain=route.domain,
                    query=state["query"],
                    scenario=route.scenario,
                )
            workflow.complete(
                node_id="question_generation",
                agent="DynamicQuestionAgent",
                tool_name="generate_dynamic_questions + infer_query_answers",
                reason="从商品差异中选择问题，并跳过用户输入里已经明确的信息。",
                handoff_to="planner",
            )
            self.trace_service.add_event(
                session_id=session_id,
                step="question_generation",
                agent="DynamicQuestionAgent",
                event_type="agent_completed",
                input_summary=f"products={len(products)}",
                output_summary=(
                    f"scenario={route.scenario or 'general'}, "
                    f"questions={len(questions)}, inferred={list(inferred_answers.keys())}, "
                    f"question_mode={question_mode}"
                    + (
                        f", question_error={self.question_service.last_error}"
                        if self.question_service.last_error
                        else ""
                    )
                ),
                latency_ms=timer.latency_ms(),
                token_input=question_input_tokens,
                token_output=question_output_tokens,
            )
            return {"questions": questions, "inferred_answers": inferred_answers}

        def route_next_node(state: StartPlannerState) -> str:
            return state["next_action"]

        graph.add_node("planner", plan_next_action)
        graph.add_node("route_domain", route_domain_node)
        graph.add_node("discover_external_hotlist", discover_external_hotlist_node)
        graph.add_node("search_products", search_products_node)
        graph.add_node("analyze_product_features", analyze_product_features_node)
        graph.add_node("generate_dynamic_questions", generate_dynamic_questions_node)
        graph.add_edge(START, "planner")
        graph.add_conditional_edges(
            "planner",
            route_next_node,
            {
                "route_domain": "route_domain",
                "discover_external_hotlist": "discover_external_hotlist",
                "search_products": "search_products",
                "analyze_product_features": "analyze_product_features",
                "generate_dynamic_questions": "generate_dynamic_questions",
                "wait_user_answers": END,
            },
        )
        for node_name in [
            "route_domain",
            "discover_external_hotlist",
            "search_products",
            "analyze_product_features",
            "generate_dynamic_questions",
        ]:
            graph.add_edge(node_name, "planner")

        compiled = graph.compile()
        return compiled.invoke(initial_state, {"recursion_limit": 16})

    def _next_start_action(self, state: dict) -> tuple[str, str]:
        if "route" not in state:
            return "route_domain", "还不知道品类和场景，先调用 route_domain。"
        if "external_discovery" not in state:
            return (
                "discover_external_hotlist",
                "已经识别品类，需要判断是否有外部公开榜单可作为候选召回补充。",
            )
        if "products" not in state:
            return "search_products", "已有品类和数据源信息，下一步召回候选商品。"
        if "feature_analysis" not in state:
            return "analyze_product_features", "已有候选池，先分析商品差异再决定追问内容。"
        if "questions" not in state or "inferred_answers" not in state:
            return "generate_dynamic_questions", "已有差异分析，生成动态追问并抽取用户已说明的信息。"
        return "wait_user_answers", "信息收集阶段已完成，等待用户回答后进入推荐阶段。"

    def _planner_state_summary(self, state: dict) -> str:
        route = state.get("route")
        external = state.get("external_discovery") or {}
        return (
            f"has_route={route is not None}, "
            f"domain={getattr(route, 'domain', 'unknown')}, "
            f"external_mode={external.get('mode', 'unknown')}, "
            f"products={len(state.get('products') or [])}, "
            f"features={'feature_analysis' in state}, "
            f"questions={len(state.get('questions') or [])}"
        )

    def _session_path(self, session_id: str) -> Path:
        safe_id = "".join(char for char in session_id if char.isalnum() or char in "_-")
        return self._session_dir / f"{safe_id}.json"

    def _save_session(self, session_id: str, session: dict) -> None:
        serializable = {}
        for key, value in session.items():
            if key == "last_recommendation":
                continue
            if hasattr(value, "model_dump"):
                serializable[key] = value.model_dump(mode="json")
            elif isinstance(value, list):
                serializable[key] = [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in value
                ]
            else:
                serializable[key] = value
        path = self._session_path(session_id)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)

    def _load_session(self, session_id: str) -> dict | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            "query": raw["query"],
            "user_id": raw["user_id"],
            "route": DomainRoute.model_validate(raw["route"]),
            "candidate_products": [ProductOffer.model_validate(item) for item in raw["candidate_products"]],
            "external_discovery": raw.get("external_discovery", {}),
            "feature_analysis": FeatureAnalysis.model_validate(raw["feature_analysis"]),
            "questions": [Question.model_validate(item) for item in raw["questions"]],
            "inferred_answers": raw.get("inferred_answers", {}),
            "workflow_nodes": [WorkflowNodeRecord.model_validate(item) for item in raw.get("workflow_nodes", [])],
            "latest_answers": raw.get("latest_answers", {}),
            "revision_count": raw.get("revision_count", 0),
        }

    def _response_mode_summary(self) -> str:
        if not self.llm_adapter.enabled:
            return "mode=rules"
        if self.llm_adapter.last_error:
            return f"mode=rules_fallback, llm_error={self.llm_adapter.last_error}"
        return "mode=llm"

    def _response_mode(self) -> str:
        if not self.llm_adapter.enabled:
            return "rules"
        if self.llm_adapter.last_error:
            return "rules_fallback"
        return "llm"

    def _no_suitable_summary(
        self,
        domain: str,
        risk_results: list[RiskResult],
        products: list[ProductOffer] | None = None,
    ) -> str:
        reason = self._no_suitable_reason(risk_results, products)
        if products is not None and not products:
            return f"当前品类还没有接入可验证商品库。{reason} 可以先完成需求澄清，等接入对应商品搜索工具后再生成商品排序。"
        if domain == "supplement":
            return f"当前没有足够安全的主推荐。{reason} 建议先补充使用人群、正在服用的药物和过敏信息，再重新筛选。"
        return f"当前没有足够合适的主推荐。{reason} 建议放宽部分条件或补充过敏、年龄阶段、肠胃情况后重新筛选。"

    def _no_suitable_reason(
        self,
        risk_results: list[RiskResult],
        products: list[ProductOffer] | None = None,
    ) -> str:
        if products is not None and not products:
            return "当前只识别到通用品类需求，没有对应的本地商品库或外部商品召回工具。"
        notes = []
        for result in risk_results:
            notes.extend(result.notes)
        if not notes:
            return "所有候选都被风险或硬约束拦截。"
        top_notes = list(dict.fromkeys(notes))[:2]
        return "主要原因：" + "；".join(top_notes)

    def _current_comparison(self, session_id: str, ranked: list, products: list) -> dict:
        agent_top = ranked[0] if ranked else None
        popularity_top = max(products, key=lambda item: item.popularity_score) if products else None
        price_candidates = [item for item in products if item.unit_price]
        value_top = min(price_candidates, key=lambda item: item.unit_price or 999999) if price_candidates else None
        differences = []
        if agent_top and popularity_top and agent_top.product.id != popularity_top.id:
            differences.append("没有直接选择热度最高商品")
        if agent_top and value_top and agent_top.product.id != value_top.id:
            differences.append("没有直接选择最低单价商品")
        if not agent_top:
            conclusion = "当前没有足够结果可对比。"
        elif differences:
            conclusion = "当前 Agent 根据本次画像重新排序，" + "，".join(differences) + "。"
        else:
            conclusion = "当前 Agent Top1 与热门/低价 baseline 一致。"
        return {
            "session_id": session_id,
            "agent_top_product": agent_top.product.name if agent_top else None,
            "popularity_top_product": popularity_top.name if popularity_top else None,
            "value_top_product": value_top.name if value_top else None,
            "agent_score": agent_top.final_score if agent_top else 0,
            "popularity_score": popularity_top.popularity_score if popularity_top else 0,
            "value_score": value_top.unit_price if value_top else 0,
            "value_unit_price": value_top.unit_price if value_top else None,
            "value_unit_label": "元/kg" if value_top and value_top.domain == "pet_food" else "元/计价单位",
            "conclusion": conclusion,
        }

    def _runtime_metrics(self, session_id: str) -> RuntimeMetrics:
        events = self.trace_service.list_events(session_id)
        cache_hits = sum(1 for event in events if "cache=hit" in event.output_summary)
        cache_misses = sum(1 for event in events if "cache=miss" in event.output_summary)
        input_tokens = sum(event.token_input for event in events)
        output_tokens = sum(event.token_output for event in events)
        llm_calls = sum(1 for event in events if event.token_input or event.token_output)
        notes = [
            "商品召回和 RAG 检索使用本地 TTL 缓存。",
            "LLM 只处理 Top3 极简摘要，不参与排序和风险判断。",
        ]
        if cache_hits:
            notes.append(f"本次命中缓存 {cache_hits} 次。")
        if any("rules_fallback" in event.output_summary for event in events):
            notes.append("仅表达适配节点触发兜底，排序、风险和 RAG 结果不受影响。")
        return RuntimeMetrics(
            total_latency_ms=sum(event.latency_ms for event in events),
            llm_calls=llm_calls,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            cost_control_notes=notes,
        )
