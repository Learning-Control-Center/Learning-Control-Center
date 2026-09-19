from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthorityActivationRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


class AuthoritySurfaceRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    roadmap_presentation: Literal["v2", "v1_read_only"]
    recommendation_presentation: Literal["v2", "v1_read_only"]
    today_presentation: Literal["v2", "v1_read_only"]
    reason: str = Field(min_length=1, max_length=2000)
