"""Validated request models shared by the editor API."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EditItem(BaseModel):
    kind: Literal["text", "cover"] = "text"
    page: int = Field(ge=0)
    pageId: str | None = None
    lineId: str | None = None
    originalHash: str | None = None
    bbox: list[float] = Field(min_length=4, max_length=4)
    original: str = ""
    text: str = ""
    fontSize: float | None = Field(default=None, ge=4, le=200, allow_inf_nan=False)

    @field_validator("bbox")
    @classmethod
    def valid_bbox(cls, value: list[float]) -> list[float]:
        if not all(math.isfinite(v) for v in value):
            raise ValueError("bbox 必须是有限数值")
        if value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("bbox 范围无效")
        return value

    @model_validator(mode="after")
    def require_anchor_for_text(self):
        if self.kind == "text" and not all(
            (self.pageId, self.lineId, self.originalHash)
        ):
            raise ValueError("文本编辑缺少页面或行锚点")
        if self.kind == "cover" and not self.pageId:
            raise ValueError("视觉遮挡缺少页面身份")
        if self.kind == "cover" and self.text:
            raise ValueError("视觉遮挡不能包含替换文本")
        return self


class ApplyEditsRequest(BaseModel):
    expectedRevision: int = Field(ge=0)
    edits: list[EditItem] = Field(min_length=1, max_length=1000)


class AddTextRequest(BaseModel):
    expectedRevision: int = Field(ge=0)
    page: int = Field(ge=0)
    pageId: str
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=10000)
    fontSize: float = Field(default=12, ge=4, le=200, allow_inf_nan=False)
    color: str = "#000000"


class RotatePageRequest(BaseModel):
    expectedRevision: int = Field(ge=0)
    page: int = Field(ge=0)
    angle: int


class DeletePagesRequest(BaseModel):
    expectedRevision: int = Field(ge=0)
    pages: list[int] = Field(min_length=1)


class MovePageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expectedRevision: int = Field(ge=0)
    fromPage: int = Field(alias="from", ge=0)
    toPage: int = Field(alias="to", ge=0)


class HistoryActionRequest(BaseModel):
    expectedRevision: int = Field(ge=0)
