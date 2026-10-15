from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents import DecisionAgent, ResearchAgent, ResponseAgent, SupervisorAgent
from app.schemas.agent import SupervisorDecision, WorkResult
from app.workflow import DecisionWorkflowState


class MultiAgentState(TypedDict, total=False):
    phase: str
    query: str
    user_id: str
    use_external_refresh: bool
    answers: dict[str, Any]
    route: Any
    external_discovery: dict[str, Any]
    products: list[Any]
    feature_analysis: Any
    questions: list[Any]
    inferred_answers: dict[str, Any]
    user_profile: Any
    risk_results: list[Any]
    ranked: list[Any]
    knowledge_evidence: list[Any]
    no_suitable_candidate: bool
    recommendation: dict[str, Any]
    summary: str
    display_ranked: list[Any]
    response_mode: str
    proposed_memories: list[Any]
    results: list[WorkResult]
    supervisor_decision: SupervisorDecision
    next_agent: str
    supervisor_turn: int


class MultiAgentDecisionGraph:
    def __init__(self, owner) -> None:
        self.owner = owner
        self.supervisor = SupervisorAgent(owner.llm_adapter)
        self.research_agent = ResearchAgent(owner)
        self.decision_agent = DecisionAgent(owner)
        self.response_agent = ResponseAgent(owner)

    def run_start(
        self,
        *,
        session_id: str,
        workflow: DecisionWorkflowState,
        query: str,
        user_id: str,
        use_external_refresh: bool,
    ) -> MultiAgentState:
        return self._run(
            session_id=session_id,
            workflow=workflow,
            initial_state={
                "phase": "start",
                "query": query,
                "user_id": user_id,
                "use_external_refresh": use_external_refresh,
                "results": [],
            },
        )

    def run_answer(
        self,
        *,
        session_id: str,
        workflow: DecisionWorkflowState,
        state: dict[str, Any],
    ) -> MultiAgentState:
        initial_state: MultiAgentState = {
            "phase": "answer",
            "query": state["query"],
            "user_id": state["user_id"],
            "route": state["route"],
            "external_discovery": state.get("external_discovery", {}),
            "products": state.get("candidate_products", []),
            "feature_analysis": state.get("feature_analysis"),
            "questions": state.get("questions", []),
            "inferred_answers": state.get("inferred_answers", {}),
            "answers": state["answers"],
            "results": [],
        }
        return self._run(session_id=session_id, workflow=workflow, initial_state=initial_state)

    def _run(
        self,
        *,
        session_id: str,
        workflow: DecisionWorkflowState,
        initial_state: MultiAgentState,
    ) -> MultiAgentState:
        graph = StateGraph(MultiAgentState)

        def supervisor_node(state: MultiAgentState) -> MultiAgentState:
            turn = int(state.get("supervisor_turn", 0)) + 1
            if turn > 8:
                raise RuntimeError("SupervisorAgent failed to finalize within 8 turns")
            decision = self.supervisor.decide(phase=state["phase"], state=state)
            workflow.complete(
                node_id=f"supervisor_{state['phase']}_{turn}",
                agent="SupervisorAgent",
                tool_name="supervisor.decide",
                reason=f"{decision.reason} mode={self.supervisor.last_mode}",
                handoff_to=decision.next_agent or decision.action,
                fallback_used=self.supervisor.last_mode == "rules" and self.owner.llm_adapter.enabled,
            )
            self.owner.trace_service.add_event(
                session_id=session_id,
                step="supervisor",
                agent="SupervisorAgent",
                event_type="agent_decision",
                input_summary=self._state_summary(state),
                output_summary=(
                    f"action={decision.action}, next={decision.next_agent}, "
                    f"mode={self.supervisor.last_mode}, reason={decision.reason}"
                ),
                token_input=self.owner.llm_adapter.last_input_tokens if self.supervisor.last_mode == "llm" else 0,
                token_output=self.owner.llm_adapter.last_output_tokens if self.supervisor.last_mode == "llm" else 0,
                error=self.supervisor.last_error,
            )
            return {
                "supervisor_decision": decision,
                "next_agent": decision.next_agent or decision.action,
                "supervisor_turn": turn,
            }

        def dynamic_worker_node(state: MultiAgentState) -> MultiAgentState:
            decision = state["supervisor_decision"]
            if decision.task is None:
                raise RuntimeError("Supervisor delegated without an AgentTask")
            result = self._run_dynamic_worker(
                session_id=session_id,
                task=decision.task,
                state=state,
            )
            self.owner.trace_service.add_event(
                session_id=session_id,
                step="dynamic_subagent",
                agent=result.agent_name,
                event_type="subagent_completed",
                input_summary=f"task={decision.task.agent_name}, tools={result.used_tools}",
                output_summary=result.summary,
            )
            return self._merge_result(state, result)

        def route_next(state: MultiAgentState) -> str:
            decision = state["supervisor_decision"]
            if decision.action in ["finalize", "clarify"]:
                return "finalize"
            return "dynamic_worker"

        graph.add_node("supervisor", supervisor_node)
        graph.add_node("dynamic_worker", dynamic_worker_node)
        graph.add_edge(START, "supervisor")
        graph.add_conditional_edges(
            "supervisor",
            route_next,
            {
                "dynamic_worker": "dynamic_worker",
                "finalize": END,
            },
        )
        graph.add_edge("dynamic_worker", "supervisor")
        previous_workflow = getattr(self.owner, "_active_workflow", None)
        self.owner._active_workflow = workflow
        try:
            return graph.compile().invoke(initial_state, {"recursion_limit": 18})
        finally:
            self.owner._active_workflow = previous_workflow

    def _run_dynamic_worker(self, *, session_id: str, task, state: MultiAgentState) -> WorkResult:
        agent_name = task.agent_name
        allowed_tools = set(task.allowed_tools)
        self.owner.trace_service.add_event(
            session_id=session_id,
            step="dynamic_subagent",
            agent="SupervisorAgent",
            event_type="subagent_created",
            input_summary=f"phase={task.phase}, tools={sorted(allowed_tools)}",
            output_summary=f"created={agent_name}, objective={task.objective}",
        )
        if agent_name == "ResearchAgent":
            return self.research_agent.run_start(session_id=session_id, task=task, state=state)
        if agent_name == "DecisionAgent":
            if task.phase == "start":
                return self.decision_agent.run_start(session_id=session_id, task=task, state=state)
            return self.decision_agent.run_answer(session_id=session_id, task=task, state=state)
        if agent_name == "ResponseAgent":
            return self.response_agent.run_answer(session_id=session_id, task=task, state=state)
        if {"route_domain", "discover_external_hotlist", "search_products", "analyze_product_features"} & allowed_tools:
            return self.research_agent.run_start(session_id=session_id, task=task, state=state)
        if {"generate_dynamic_questions", "infer_query_answers"} & allowed_tools and task.phase == "start":
            return self.decision_agent.run_start(session_id=session_id, task=task, state=state)
        if {"build_user_profile", "evaluate_risk", "rank_products", "retrieve_knowledge"} & allowed_tools:
            return self.decision_agent.run_answer(session_id=session_id, task=task, state=state)
        if {"adapt_response", "propose_memory_updates"} & allowed_tools:
            return self.response_agent.run_answer(session_id=session_id, task=task, state=state)
        raise RuntimeError(f"Unsupported dynamic worker task: {agent_name} tools={sorted(allowed_tools)}")

    def _merge_result(self, state: MultiAgentState, result: WorkResult) -> MultiAgentState:
        output = dict(result.output)
        output["results"] = [*state.get("results", []), result]
        if result.agent_name == "ResponseAgent":
            output["recommendation"] = {"status": "ready", "summary": result.summary}
        return output

    def _state_summary(self, state: MultiAgentState) -> str:
        return (
            f"phase={state.get('phase')}, "
            f"domain={getattr(state.get('route'), 'domain', 'unknown')}, "
            f"products={len(state.get('products') or [])}, "
            f"questions={len(state.get('questions') or [])}, "
            f"ranked={len(state.get('ranked') or [])}, "
            f"results={len(state.get('results') or [])}"
        )
