from typing import Any

from pydantic import BaseModel, Field


class AIQueryUnderstanding(BaseModel):
    inferred_answers: dict[str, Any] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""


class AIResponseSummary(BaseModel):
    summary: str


class AIDynamicQuestion(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_]{2,40}$")
    label: str = Field(min_length=4, max_length=80)
    reason: str = Field(min_length=6, max_length=180)
    options: list[str] = Field(min_length=2, max_length=6)


class AIDynamicQuestionPlan(BaseModel):
    questions: list[AIDynamicQuestion] = Field(min_length=1, max_length=5)
    reasoning_summary: str = ""


class AIHotlistDiscovery(BaseModel):
    category_keyword: str = Field(min_length=1, max_length=40)
    hotlist_urls: list[str] = Field(default_factory=list, max_length=5)
    reasoning_summary: str = ""


class AIProductCandidate(BaseModel):
    name: str = Field(min_length=4, max_length=160)
    brand: str = Field(default="待核验", max_length=80)
    product_url: str = Field(default="", max_length=300)
    claims: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=160)


class AIProductDiscovery(BaseModel):
    category_keyword: str = Field(min_length=1, max_length=40)
    products: list[AIProductCandidate] = Field(default_factory=list, max_length=12)
    reasoning_summary: str = ""
