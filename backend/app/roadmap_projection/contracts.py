from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PositionOverrideInput(StrictModel):
    node_key: str = Field(min_length=1, max_length=255)
    position_x: int = Field(ge=-1_000_000, le=1_000_000)
    position_y: int = Field(ge=-1_000_000, le=1_000_000)


class ProjectionPreferenceInput(StrictModel):
    show_prerequisites: bool = False
    show_recommended_before: bool = True
    show_supports: bool = True
    show_related: bool = True
