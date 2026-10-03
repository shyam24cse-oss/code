"""
Chat endpoints.

Provides three ways to interact with the RAG pipeline:

* ``POST /api/chat`` - synchronous structured JSON answer (demonstrates
  OpenAI Responses API structured output constrained to a Pydantic schema).
* ``POST /api/chat/stream`` - Server-Sent Events streaming endpoint used by
  the web UI for a token-by-token typing effect.
* ``GET /api/conversations`` / ``GET /api/conversations/{id}`` - conversation
  history retrieval (conversation memory).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_chat_service, get_document_service, get_memory_service
from app.core.exceptions import MissingAPIKeyError, OpenAIServiceError, VectorStoreNotReadyError
from app.domain.models import MessageRole
from app.domain.schemas import (
    ChatMessageResponse,
    ChatRequest,
    ConversationListResponse,
    ConversationResponse,
    ExplainChatRequest,
    StructuredAnswer,
)
from app.services.chat_service import ChatService
from app.services.document_service import DocumentService
from app.services.memory_service import MemoryService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Chat"])


@router.post(
    "/chat/explain/stream",
    summary="Explain selected PDF text and stream the answer",
)
def explain_selected_text(
    request: ExplainChatRequest,
    chat_service: ChatService = Depends(get_chat_service),
    document_service: DocumentService = Depends(get_document_service),
    memory_service: MemoryService = Depends(get_memory_service),
) -> StreamingResponse:
    document = document_service.get_document(request.document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    conversation = memory_service.get_or_create_conversation(
        request.conversation_id, title_hint=request.user_question
    )
    history = memory_service.to_prompt_history(memory_service.get_history(conversation.id))
    try:
        surrounding_context = document_service.get_context_for_selection(
            document.id, request.page_number, request.selected_text
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unable to extract neighboring PDF context: %s", exc)
        surrounding_context = ""

    contexts = []
    if document.status.value == "indexed":
        try:
            contexts = chat_service.retrieve_context(
                f"{request.selected_text}\n{request.user_question}",
                top_k=3,
                document_ids=[document.id],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vector retrieval unavailable for PDF explanation: %s", exc)

    location = f"{document.filename}, page {request.page_number}"
    stored_user_message = (
        f"Selected text ({location}):\n{request.selected_text}\n\n"
        f"User question:\n{request.user_question}"
    )
    memory_service.append_message(conversation.id, MessageRole.USER, stored_user_message)
    sources = chat_service.contexts_to_sources(contexts)

    def event_stream():
        yield _sse_event("meta", {"conversation_id": conversation.id})
        collected = []
        try:
            for token in chat_service.stream_explanation(
                request.selected_text,
                request.user_question,
                document.filename,
                request.page_number,
                surrounding_context,
                contexts,
                history,
            ):
                collected.append(token)
                yield _sse_event("token", {"text": token})
        except (OpenAIServiceError, MissingAPIKeyError) as exc:
            yield _sse_event("error", {"detail": exc.message})
            return

        answer = "".join(collected)
        memory_service.append_message(
            conversation.id,
            MessageRole.ASSISTANT,
            answer,
            sources=[source.model_dump() for source in sources],
        )
        yield _sse_event("sources", {"sources": [source.model_dump() for source in sources]})
        yield _sse_event("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post(
    "/chat",
    response_model=StructuredAnswer,
    summary="Ask a question and receive a structured JSON answer",
    description=(
        "Retrieves relevant chunks via semantic search and asks the OpenAI "
        "Responses API to answer using a constrained JSON schema "
        "(answer, key_points, sources, confidence)."
    ),
)
def chat(
    request: ChatRequest,
    chat_service: ChatService = Depends(get_chat_service),
    memory_service: MemoryService = Depends(get_memory_service),
) -> StructuredAnswer:
    conversation = memory_service.get_or_create_conversation(
        request.conversation_id, title_hint=request.question
    )
    history = memory_service.to_prompt_history(memory_service.get_history(conversation.id))

    try:
        contexts = chat_service.retrieve_context(
            request.question, top_k=request.top_k, document_ids=request.document_ids
        )
        answer = chat_service.generate_structured_answer(request.question, contexts, history)
    except VectorStoreNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except MissingAPIKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED, detail=exc.message
        ) from exc
    except OpenAIServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc

    memory_service.append_message(conversation.id, MessageRole.USER, request.question)
    memory_service.append_message(
        conversation.id,
        MessageRole.ASSISTANT,
        answer.answer,
        sources=[s.model_dump() for s in answer.sources],
    )
    return answer


@router.post(
    "/chat/stream",
    summary="Ask a question and stream the answer via Server-Sent Events",
    description=(
        "Streams the model's answer token-by-token as it is generated, using "
        "the OpenAI Responses API streaming mode. The final SSE event contains "
        "the resolved source citations."
    ),
)
def chat_stream(
    request: ChatRequest,
    chat_service: ChatService = Depends(get_chat_service),
    memory_service: MemoryService = Depends(get_memory_service),
) -> StreamingResponse:
    conversation = memory_service.get_or_create_conversation(
        request.conversation_id, title_hint=request.question
    )
    history = memory_service.to_prompt_history(memory_service.get_history(conversation.id))

    try:
        contexts = chat_service.retrieve_context(
            request.question, top_k=request.top_k, document_ids=request.document_ids
        )
    except VectorStoreNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc

    sources = chat_service.contexts_to_sources(contexts)
    memory_service.append_message(conversation.id, MessageRole.USER, request.question)

    def event_stream():
        collected = []
        yield _sse_event("meta", {"conversation_id": conversation.id})
        try:
            for token in chat_service.stream_answer(request.question, contexts, history):
                collected.append(token)
                yield _sse_event("token", {"text": token})
        except (OpenAIServiceError, MissingAPIKeyError) as exc:
            yield _sse_event("error", {"detail": exc.message})
            return

        full_answer = "".join(collected)
        memory_service.append_message(
            conversation.id,
            MessageRole.ASSISTANT,
            full_answer,
            sources=[s.model_dump() for s in sources],
        )
        yield _sse_event("sources", {"sources": [s.model_dump() for s in sources]})
        yield _sse_event("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="List all conversations",
)
def list_conversations(
    memory_service: MemoryService = Depends(get_memory_service),
) -> ConversationListResponse:
    conversations = memory_service.list_conversations()
    return ConversationListResponse(
        conversations=[ConversationResponse.model_validate(c) for c in conversations],
        total=len(conversations),
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Get a conversation and its full message history",
)
def get_conversation(
    conversation_id: str, memory_service: MemoryService = Depends(get_memory_service)
) -> ConversationResponse:
    conversation = memory_service.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    messages = memory_service.get_history(conversation_id)
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        messages=[ChatMessageResponse.model_validate(m) for m in messages],
    )
