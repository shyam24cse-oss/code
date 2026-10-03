"""
Pydantic v2 schemas used at the API boundary.

These models define the public contract of the HTTP API: request payloads,
response bodies, and the structured-output schema used to constrain the
OpenAI Responses API. They are intentionally decoupled from the internal
domain dataclasses in :mod:`app.domain.models`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
class DocumentResponse(BaseModel):
    """Public representation of an uploaded document."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    status: str
    page_count: int
    chunk_count: int
    size_bytes: int
    uploaded_at: datetime
    error_message: str | None = None


class DocumentUploadResponse(BaseModel):
    """Response returned immediately after a successful upload + indexing."""

    document: DocumentResponse
    message: str = "Document uploaded and indexed successfully."


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    """Incoming chat / question request."""

    question: str = Field(..., min_length=1, max_length=4000, description="User's question.")
    conversation_id: str | None = Field(
        default=None, description="Existing conversation id to continue, or None for a new one."
    )
    document_ids: list[str] | None = Field(
        default=None,
        description="Optional subset of document ids to restrict retrieval to.",
    )
    top_k: int | None = Field(
        default=None, ge=1, le=20, description="Override the number of retrieved chunks."
    )


class ExplainChatRequest(BaseModel):
    selected_text: str = Field(..., min_length=1, max_length=12000)
    user_question: str = Field(..., min_length=1, max_length=4000)
    document_id: str = Field(..., min_length=1)
    page_number: int = Field(..., ge=1)
    conversation_id: str | None = None

    @field_validator("selected_text", "user_question")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Selected text and question must not be empty.")
        return value


class SourceReference(BaseModel):
    """A single citation returned alongside an answer."""

    document_id: str
    document_name: str
    page_number: int
    snippet: str
    relevance_score: float


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: str
    content: str
    sources: list[SourceReference] = Field(default_factory=list)
    created_at: datetime


class StructuredAnswer(BaseModel):
    """Schema enforced on the OpenAI Responses API structured output call."""

    answer_found: bool = Field(..., description="Whether the context contained an answer.")
    answer: str = Field(..., description="The final answer in Markdown, or an explanation.")
    key_points: list[str] = Field(
        default_factory=list, description="Short bullet points summarizing the answer."
    )
    sources: list[SourceReference] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model's self-rated confidence.")


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    messages: list[ChatMessageResponse] = Field(default_factory=list)


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    total: int


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    app_name: str
    version: str
    environment: str
    openai_configured: bool


class ErrorResponse(BaseModel):
    detail: str
    error_type: str
