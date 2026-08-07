"""Pydantic schemas for request/response validation."""
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


# Board Schemas
class BoardCreate(BaseModel):
    """Schema for creating a board."""
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)


class BoardUpdate(BaseModel):
    """Schema for updating a board."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)


class BoardResponse(BaseModel):
    """Schema for board response."""
    id: int
    user_id: int
    title: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Column Schemas
class ColumnCreate(BaseModel):
    """Schema for creating a column."""
    title: str = Field(..., min_length=1, max_length=255)
    position: int = Field(default=0, ge=0)


class ColumnUpdate(BaseModel):
    """Schema for updating a column."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    position: Optional[int] = Field(None, ge=0)


class ColumnResponse(BaseModel):
    """Schema for column response."""
    id: int
    board_id: int
    title: str
    position: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Card Schemas
class CardCreate(BaseModel):
    """Schema for creating a card."""
    column_id: int
    title: str = Field(..., min_length=1, max_length=255)
    details: Optional[str] = Field(None, max_length=5000)
    position: int = Field(default=0, ge=0)


class CardUpdate(BaseModel):
    """Schema for updating a card."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    details: Optional[str] = Field(None, max_length=5000)


class CardMove(BaseModel):
    """Schema for moving a card to another column."""
    column_id: int
    position: int = Field(default=0, ge=0)


class CardResponse(BaseModel):
    """Schema for card response."""
    id: int
    column_id: int
    title: str
    details: Optional[str]
    position: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Board with nested data
class ColumnWithCards(ColumnResponse):
    """Column with cards."""
    cards: List[CardResponse] = []


class BoardDetail(BoardResponse):
    """Board with all columns and cards."""
    columns: List[ColumnWithCards] = []


# Error Schemas
class ErrorResponse(BaseModel):
    """Schema for error responses."""
    detail: str
    status_code: int


# Success Schemas
class SuccessResponse(BaseModel):
    """Schema for success responses."""
    message: str
    data: Optional[dict] = None
