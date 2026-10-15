from typing import Literal

from pydantic import BaseModel, Field


class RankingEntry(BaseModel):
    rank: int = Field(ge=1, le=20)
    jd_sku: str = Field(pattern=r"^\d{5,20}$")
    title: str = Field(min_length=1, max_length=500)
    product_url: str
    image_url: str | None = None
    spec: str | None = None
    review_count_label: str | None = None
    price: float | None = Field(default=None, ge=0)


class RankingSnapshot(BaseModel):
    source_url: str
    source_name: str = "京东猫粮公开排行榜"
    observed_at: str
    content_changed_at: str
    source_published_at: str | None = None
    content_hash: str
    raw_sha256: str
    raw_file: str
    entries: list[RankingEntry] = Field(min_length=10, max_length=20)


class RefreshState(BaseModel):
    last_checked_at: str | None = None
    last_success_at: str | None = None
    status: Literal["never", "updated", "unchanged", "failed"] = "never"
    error: str | None = None
