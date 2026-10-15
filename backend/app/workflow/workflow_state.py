from app.schemas import WorkflowNodeRecord


class DecisionWorkflowState:
    def __init__(self) -> None:
        self.nodes: list[WorkflowNodeRecord] = []

    def complete(
        self,
        *,
        node_id: str,
        agent: str,
        tool_name: str,
        reason: str,
        handoff_to: str | None = None,
        fallback_used: bool = False,
    ) -> None:
        self.nodes.append(
            WorkflowNodeRecord(
                node_id=node_id,
                agent=agent,
                tool_name=tool_name,
                status="fallback" if fallback_used else "completed",
                handoff_to=handoff_to,
                fallback_used=fallback_used,
                reason=reason,
            )
        )

    def fail(
        self,
        *,
        node_id: str,
        agent: str,
        tool_name: str,
        reason: str,
    ) -> None:
        self.nodes.append(
            WorkflowNodeRecord(
                node_id=node_id,
                agent=agent,
                tool_name=tool_name,
                status="failed",
                reason=reason,
            )
        )
