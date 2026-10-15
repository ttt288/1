from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


SupervisorAction = Literal["delegate", "clarify", "finalize"]
WorkStatus = Literal["completed", "needs_clarification", "failed"]


class AgentTask(BaseModel):
    id: str = Field(default_factory=lambda: f"task_{uuid4().hex[:10]}")
    phase: Literal["start", "answer"]
    agent_name: str = Field(min_length=3, max_length=60)
    objective: str = Field(min_length=4, max_length=240)
    worker_prompt: str = Field(default="", max_length=1200)
    input: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class WorkResult(BaseModel):
    task_id: str
    agent_name: str
    status: WorkStatus = "completed"
    summary: str
    output: dict[str, Any] = Field(default_factory=dict)
    used_tools: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    error: str | None = None


class SupervisorDecision(BaseModel):
    action: SupervisorAction
    next_agent: str | None = Field(default=None, max_length=60)
    task: AgentTask | None = None
    reason: str
    clarify_questions: list[str] = Field(default_factory=list)
    final_summary: str = ""
