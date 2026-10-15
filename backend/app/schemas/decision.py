from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


SupportedDomain = Literal["pet_food", "supplement", "general"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class DecisionStartRequest(BaseModel):
    query: str = Field(min_length=1)
    user_id: str = "demo_user"
    use_external_refresh: bool = False


class DecisionAnswerRequest(BaseModel):
    session_id: str
    user_id: str = "demo_user"
    answers: dict[str, Any]


class MemoryDecisionRequest(BaseModel):
    memory: "MemoryItem"
    action: Literal["remember", "session_only", "reject"]


class DomainRoute(BaseModel):
    domain: SupportedDomain
    intent: str
    confidence: float = Field(ge=0, le=1)
    need_clarification: bool = False
    scenario: str | None = None
    scenario_confidence: float = Field(default=0, ge=0, le=1)
    scenario_evidence: list[str] = Field(default_factory=list)


class ProductOffer(BaseModel):
    id: str
    domain: SupportedDomain
    name: str
    brand: str
    price: float = Field(ge=0)
    currency: str = "CNY"
    spec: str
    unit_price: float | None = None
    popularity_score: float = Field(default=0, ge=0, le=100)
    features: dict[str, Any] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    target_users: list[str] = Field(default_factory=list)
    avoid_for: list[str] = Field(default_factory=list)
    source_type: str = "seed_dataset"
    source_url: str | None = None
    source_name: str | None = None
    market_rank: int | None = None
    review_count_label: str | None = None
    data_quality: str = "curated_seed"
    jd_sku: str | None = None
    ranking_observed_at: str | None = None
    last_updated: str


class FeatureInsight(BaseModel):
    dimension: str
    label: str
    summary: str
    values: list[str] = Field(default_factory=list)
    product_count: int = 0
    related_question_ids: list[str] = Field(default_factory=list)
    importance: Literal["low", "medium", "high"] = "medium"


class FeatureAnalysis(BaseModel):
    domain: SupportedDomain
    total_products: int
    insights: list[FeatureInsight] = Field(default_factory=list)


class Question(BaseModel):
    id: str
    label: str
    reason: str
    answer_type: Literal["single_choice", "multi_choice", "number", "text", "range"]
    options: list[str] = Field(default_factory=list)
    required: bool = True
    source: Literal["scenario_playbook", "product_difference", "communication_preference"] = "product_difference"
    evidence: list[str] = Field(default_factory=list)


class WorkflowNodeRecord(BaseModel):
    node_id: str
    agent: str
    tool_name: str
    status: Literal["pending", "completed", "fallback", "failed"] = "pending"
    handoff_to: str | None = None
    fallback_used: bool = False
    reason: str = ""


class DecisionStartResponse(BaseModel):
    session_id: str
    route: DomainRoute
    candidate_products: list[ProductOffer]
    feature_analysis: FeatureAnalysis
    questions: list[Question]
    inferred_answers: dict[str, Any] = Field(default_factory=dict)
    external_discovery: dict[str, Any] = Field(default_factory=dict)
    workflow_nodes: list[WorkflowNodeRecord] = Field(default_factory=list)
    trace_events: list["TraceEvent"]


class UserProfile(BaseModel):
    user_id: str = "demo_user"
    scenario_profile: dict[str, Any] = Field(default_factory=dict)
    decision_profile: dict[str, Any] = Field(default_factory=dict)
    communication_profile: dict[str, Any] = Field(default_factory=dict)


class RiskResult(BaseModel):
    product_id: str
    risk_level: Literal["low", "medium", "high"]
    risk_score: float = Field(ge=0, le=100)
    notes: list[str] = Field(default_factory=list)
    blocked: bool = False


class RecommendationItem(BaseModel):
    product: ProductOffer
    rank: int
    final_score: float = Field(ge=0, le=100)
    score_breakdown: dict[str, float]
    suitable_reasons: list[str]
    tradeoffs: list[str]
    risk_notes: list[str]
    confidence: float = Field(ge=0, le=1)


class KnowledgeEvidence(BaseModel):
    id: str
    domain: str
    source: str
    title: str
    content: str
    matched_terms: list[str] = Field(default_factory=list)
    relevance_score: float = Field(ge=0, le=1)
    chunk_id: str | None = None
    chunk_type: str = "rule"
    decision_dimensions: list[str] = Field(default_factory=list)
    retrieval_stage: str = "lexical"
    rerank_score: float = Field(default=0, ge=0, le=1)
    rerank_reason: str = ""
    embedding_score: float = Field(default=0, ge=0, le=1)


class RecommendationResult(BaseModel):
    session_id: str
    domain: SupportedDomain
    user_profile: UserProfile
    top_products: list[RecommendationItem]
    knowledge_evidence: list[KnowledgeEvidence] = Field(default_factory=list)
    summary: str
    response_mode: Literal["rules", "llm", "rules_fallback"] = "rules"
    recommendation_status: Literal["recommended", "needs_more_info", "no_suitable_candidate"] = "recommended"
    status_reason: str = ""
    table_columns: list[str]
    needs_human_confirmation: bool = False


class MemoryItem(BaseModel):
    id: str
    user_id: str
    memory_type: Literal["explicit_fact", "inferred_preference", "communication_preference"]
    domain: str = "global"
    key: str
    value: Any
    confidence: float = Field(ge=0, le=1)
    source: str
    evidence: str
    created_at: datetime = Field(default_factory=utc_now)
    last_updated: datetime = Field(default_factory=utc_now)
    last_accessed: datetime | None = None
    expires_at: datetime | None = None
    access_count: int = 0
    status: Literal["active", "expired", "conflicted", "superseded"] = "active"
    conflict_of: str | None = None
    requires_confirmation: bool = False


class TraceEvent(BaseModel):
    trace_id: str
    session_id: str
    step: str
    agent: str
    event_type: str
    input_summary: str
    output_summary: str
    latency_ms: int = 0
    token_input: int = 0
    token_output: int = 0
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class RuntimeMetrics(BaseModel):
    total_latency_ms: int = 0
    llm_calls: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cost_control_notes: list[str] = Field(default_factory=list)


class DecisionAnswerResponse(BaseModel):
    recommendation: RecommendationResult
    proposed_memories: list[MemoryItem]
    trace_events: list[TraceEvent]
    current_comparison: dict[str, Any] | None = None
    runtime_metrics: RuntimeMetrics = Field(default_factory=RuntimeMetrics)
    workflow_nodes: list[WorkflowNodeRecord] = Field(default_factory=list)
