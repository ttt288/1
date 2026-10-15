from typing import Any

from app.agents import LLMAdapter
from app.schemas.agent import AgentTask, SupervisorDecision


SUPERVISOR_PROMPT = """你是消费决策系统的 SupervisorAgent。
你不直接做商品搜索、排序或生成最终回复，而是根据当前 state 动态创建一个短生命周期 WorkerAgent。
只输出 JSON，不要输出 Markdown、解释文字或代码块。

你需要为 WorkerAgent 输出:
- next_agent: 只能是 ResearchAgent、DecisionAgent、ResponseAgent。
- task.objective: 子 Agent 本轮目标，控制在 40 个汉字以内。
- task.worker_prompt: 给该子 Agent 的独立系统提示词，控制在 80 个汉字以内。
- task.allowed_tools: 只能从下方白名单选择，必须使用英文工具名原文。

Worker 与工具白名单:
- ResearchAgent: route_domain, discover_external_hotlist, search_products, analyze_product_features
- DecisionAgent start 阶段: generate_dynamic_questions, infer_query_answers
- DecisionAgent answer 阶段: build_user_profile, evaluate_risk, rank_products, retrieve_knowledge
- ResponseAgent: adapt_response, propose_memory_updates

约束:
- 风险、排序、RAG 必须由工具执行，不允许凭空编造。
- 信息不足时可以选择 clarify。
- 已经具备最终输出所需字段时选择 finalize。
- 不要创建没有工具权限的空 Agent。
- 不要发明新工具名，不要把中文能力描述放进 allowed_tools。
- reason 控制在 45 个汉字以内。
- 输出必须符合 SupervisorDecision JSON schema。"""


class SupervisorAgent:
    def __init__(self, llm_adapter: LLMAdapter) -> None:
        self.llm_adapter = llm_adapter
        self.last_mode = "rules"
        self.last_error: str | None = None

    def decide(self, *, phase: str, state: dict[str, Any]) -> SupervisorDecision:
        payload = self._compact_state(phase, state)
        decision = self.llm_adapter.generate_json(
            schema_model=SupervisorDecision,
            system_prompt=SUPERVISOR_PROMPT,
            user_payload=payload,
            max_output_tokens=900,
        )
        if decision is not None:
            decision = self._normalize_decision(phase, decision)
        if decision is not None and self._valid_decision(decision) and self._makes_progress(phase, state, decision):
            self.last_mode = "llm"
            self.last_error = None
            return decision

        self.last_mode = "rules"
        self.last_error = self.llm_adapter.last_error or "LLM decision would repeat completed work."
        return self._fallback_decision(phase, state)

    def _compact_state(self, phase: str, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "phase": phase,
            "query": state.get("query", ""),
            "has_route": "route" in state,
            "domain": getattr(state.get("route"), "domain", None),
            "products_count": len(state.get("products") or []),
            "has_feature_analysis": "feature_analysis" in state,
            "questions_count": len(state.get("questions") or []),
            "has_user_profile": "user_profile" in state,
            "ranked_count": len(state.get("ranked") or []),
            "knowledge_count": len(state.get("knowledge_evidence") or []),
            "has_recommendation": "recommendation" in state,
            "completed_workers": [item.agent_name for item in state.get("results", [])],
        }

    def _fallback_decision(self, phase: str, state: dict[str, Any]) -> SupervisorDecision:
        if phase == "start":
            if "products" not in state or "feature_analysis" not in state:
                return SupervisorDecision(
                    action="delegate",
                    next_agent="ResearchAgent",
                    reason="需要先完成品类识别、商品召回和商品差异分析。",
                    task=AgentTask(
                        phase="start",
                        agent_name="ResearchAgent",
                        objective="召回候选商品并分析商品差异。",
                        worker_prompt="你是 ResearchAgent，只负责品类识别、商品召回、外部热榜和商品差异分析，不做排序和最终推荐。",
                        allowed_tools=[
                            "route_domain",
                            "discover_external_hotlist",
                            "search_products",
                            "analyze_product_features",
                        ],
                    ),
                )
            if "questions" not in state or "inferred_answers" not in state:
                return SupervisorDecision(
                    action="delegate",
                    next_agent="DecisionAgent",
                    reason="已有候选和差异分析，需要生成动态追问并抽取用户已说明的信息。",
                    task=AgentTask(
                        phase="start",
                        agent_name="DecisionAgent",
                        objective="生成动态问题并理解用户自然语言里已明确的条件。",
                        worker_prompt="你是 DecisionAgent，只负责需求理解和动态追问，不输出最终推荐。",
                        allowed_tools=["generate_dynamic_questions", "infer_query_answers"],
                    ),
                )
            return SupervisorDecision(action="finalize", reason="信息收集阶段已完成。", final_summary="ready")

        if "ranked" not in state or "knowledge_evidence" not in state:
            return SupervisorDecision(
                action="delegate",
                next_agent="DecisionAgent",
                reason="需要根据用户回答构建画像、执行风险判断、排序并召回推荐证据。",
                task=AgentTask(
                    phase="answer",
                    agent_name="DecisionAgent",
                    objective="完成画像、风险、排序和 RAG 证据召回。",
                    worker_prompt="你是 DecisionAgent，必须调用画像、风险、排序和 RAG 工具，不能凭语言模型自由判断安全性和排名。",
                    allowed_tools=[
                        "build_user_profile",
                        "evaluate_risk",
                        "rank_products",
                        "retrieve_knowledge",
                    ],
                ),
            )
        if "recommendation" not in state:
            return SupervisorDecision(
                action="delegate",
                next_agent="ResponseAgent",
                reason="已有排序和证据，需要生成最终摘要和记忆提案。",
                task=AgentTask(
                    phase="answer",
                    agent_name="ResponseAgent",
                    objective="生成最终回答、展示字段和记忆提案。",
                    worker_prompt="你是 ResponseAgent，只能基于已有排序、风险和 RAG 证据生成摘要与记忆提案，不编造商品事实。",
                    allowed_tools=["adapt_response", "propose_memory_updates"],
                ),
            )
        return SupervisorDecision(action="finalize", reason="最终推荐已经生成。", final_summary="done")

    def _valid_decision(self, decision: SupervisorDecision) -> bool:
        if decision.action == "delegate":
            return decision.next_agent is not None and decision.task is not None
        if decision.action == "clarify":
            return bool(decision.clarify_questions)
        return True

    def _normalize_decision(self, phase: str, decision: SupervisorDecision) -> SupervisorDecision:
        if decision.action == "delegate" and decision.next_agent and decision.task:
            decision.task.agent_name = decision.next_agent
            decision.task.phase = phase
        return decision

    def _makes_progress(self, phase: str, state: dict[str, Any], decision: SupervisorDecision) -> bool:
        if decision.action != "delegate" or decision.next_agent is None:
            return True
        if phase == "start":
            if decision.next_agent == "ResearchAgent":
                return "products" not in state or "feature_analysis" not in state
            if decision.next_agent == "DecisionAgent":
                return "questions" not in state or "inferred_answers" not in state
            return False
        if decision.next_agent == "DecisionAgent":
            return "ranked" not in state or "knowledge_evidence" not in state
        if decision.next_agent == "ResponseAgent":
            return "recommendation" not in state
        return False
