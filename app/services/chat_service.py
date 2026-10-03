"""
Chat / RAG orchestration service.

This is the top of the RAG pipeline: it takes a user question, retrieves
relevant context via :class:`RetrievalService`, builds a grounded prompt
using a LangChain ``PromptTemplate``, and calls the **OpenAI Responses API**
(the latest OpenAI Python SDK interface, superseding Chat Completions) to
generate an answer.

Two generation modes are supported:

* :meth:`ChatService.stream_answer` - token-by-token streaming, used by the
  ``/api/chat/stream`` SSE endpoint for a responsive chat UI.
* :meth:`ChatService.generate_structured_answer` - a single structured-output
  call (``client.responses.parse``) that returns a JSON object validated
  against the :class:`StructuredAnswer` Pydantic schema, demonstrating
  constrained/structured generation.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from langchain_core.prompts import PromptTemplate
from openai import APIError, OpenAI, OpenAIError

from app.config import Settings, get_settings
from app.core.constants import (
    DEFAULT_SYSTEM_PROMPT,
    PDF_READER_SYSTEM_PROMPT,
    STRUCTURED_SYSTEM_PROMPT,
)
from app.core.exceptions import MissingAPIKeyError, OpenAIServiceError
from app.domain.models import RetrievedContext
from app.domain.schemas import SourceReference, StructuredAnswer
from app.services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)

_USER_PROMPT_TEMPLATE = PromptTemplate.from_template(
    """Context excerpts from the user's uploaded documents:

{context}

---

Conversation so far is provided as prior turns. Answer the following
question using only the context above.

Question: {question}"""
)


class ChatService:
    """Coordinates retrieval and generation to answer questions over PDFs."""

    def __init__(
        self,
        retrieval_service: RetrievalService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._retrieval_service = retrieval_service or RetrievalService(settings=self._settings)
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            if not self._settings.openai_api_key:
                raise MissingAPIKeyError()
            self._client = OpenAI(
                api_key=self._settings.openai_api_key,
                base_url=self._settings.openai_api_base,
                timeout=self._settings.openai_request_timeout,
            )
        return self._client

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def retrieve_context(
        self,
        question: str,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
    ) -> list[RetrievedContext]:
        return self._retrieval_service.retrieve(question, top_k=top_k, document_ids=document_ids)

    def _build_user_prompt(self, question: str, contexts: list[RetrievedContext]) -> str:
        context_block = self._retrieval_service.format_context(contexts)
        return _USER_PROMPT_TEMPLATE.format(context=context_block, question=question)

    @staticmethod
    def build_explanation_prompt(
        selected_text: str,
        user_question: str,
        document_name: str,
        page_number: int,
        surrounding_context: str,
        contexts: list[RetrievedContext],
    ) -> str:
        retrieved_context = RetrievalService.format_context(contexts) if contexts else "None."
        return (
            f"Selected text (primary source, {document_name}, page {page_number}):\n"
            f"{selected_text}\n\n"
            f"Nearby PDF context (use only if relevant):\n{surrounding_context or 'None.'}\n\n"
            f"Retrieved PDF excerpts (use only if relevant):\n{retrieved_context}\n\n"
            f"User's question (answer this request about the selected text):\n{user_question}"
        )

    @staticmethod
    def contexts_to_sources(contexts: list[RetrievedContext]) -> list[SourceReference]:
        return [
            SourceReference(
                document_id=c.chunk.document_id,
                document_name=c.chunk.document_name,
                page_number=c.chunk.page_number,
                snippet=(c.chunk.content[:280] + "...")
                if len(c.chunk.content) > 280
                else c.chunk.content,
                relevance_score=round(c.score, 4),
            )
            for c in contexts
        ]

    # ------------------------------------------------------------------
    # Streaming generation (OpenAI Responses API)
    # ------------------------------------------------------------------
    def stream_answer(
        self,
        question: str,
        contexts: list[RetrievedContext],
        history: list[dict[str, str]] | None = None,
    ) -> Iterator[str]:
        """Yield answer text incrementally using the Responses API streaming mode."""
        user_prompt = self._build_user_prompt(question, contexts)
        conversation_input = [*(history or []), {"role": "user", "content": user_prompt}]

        try:
            with self.client.responses.stream(
                model=self._settings.openai_chat_model,
                instructions=DEFAULT_SYSTEM_PROMPT,
                input=conversation_input,  # type: ignore[arg-type]
                max_output_tokens=self._settings.openai_max_output_tokens,
            ) as stream:
                for event in stream:
                    if event.type == "response.output_text.delta":
                        yield event.delta
                    elif event.type == "response.error":
                        logger.error("Responses API stream error: %s", event.error)
                        raise OpenAIServiceError(str(event.error))
                stream.until_done()
        except OpenAIError as exc:
            logger.exception("OpenAI streaming call failed")
            raise OpenAIServiceError(f"Streaming generation failed: {exc}") from exc

    def stream_explanation(
        self,
        selected_text: str,
        user_question: str,
        document_name: str,
        page_number: int,
        surrounding_context: str,
        contexts: list[RetrievedContext],
        history: list[dict[str, str]] | None = None,
    ) -> Iterator[str]:
        """Stream an answer focused on selected PDF text and its question."""
        prompt = self.build_explanation_prompt(
            selected_text,
            user_question,
            document_name,
            page_number,
            surrounding_context,
            contexts,
        )
        conversation_input = [*(history or []), {"role": "user", "content": prompt}]

        try:
            with self.client.responses.stream(
                model=self._settings.openai_chat_model,
                instructions=PDF_READER_SYSTEM_PROMPT,
                input=conversation_input,  # type: ignore[arg-type]
                max_output_tokens=self._settings.openai_max_output_tokens,
            ) as stream:
                for event in stream:
                    if event.type == "response.output_text.delta":
                        yield event.delta
                    elif event.type == "response.error":
                        logger.error("PDF explanation stream error: %s", event.error)
                        raise OpenAIServiceError(str(event.error))
                stream.until_done()
        except OpenAIError as exc:
            logger.exception("OpenAI PDF explanation call failed")
            raise OpenAIServiceError(f"Explanation generation failed: {exc}") from exc

    def generate_answer(
        self,
        question: str,
        contexts: list[RetrievedContext],
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """Non-streaming variant used for tests / fallback clients."""
        return "".join(self.stream_answer(question, contexts, history))

    # ------------------------------------------------------------------
    # Structured output generation (OpenAI Responses API + Pydantic schema)
    # ------------------------------------------------------------------
    def generate_structured_answer(
        self,
        question: str,
        contexts: list[RetrievedContext],
        history: list[dict[str, str]] | None = None,
    ) -> StructuredAnswer:
        """Call the Responses API with a Pydantic schema to force structured JSON output."""
        user_prompt = self._build_user_prompt(question, contexts)
        conversation_input = [*(history or []), {"role": "user", "content": user_prompt}]

        try:
            response = self.client.responses.parse(
                model=self._settings.openai_chat_model,
                instructions=STRUCTURED_SYSTEM_PROMPT,
                input=conversation_input,  # type: ignore[arg-type]
                text_format=StructuredAnswer,
                max_output_tokens=self._settings.openai_max_output_tokens,
            )
        except APIError as exc:
            logger.exception("OpenAI structured call failed")
            raise OpenAIServiceError(f"Structured generation failed: {exc}") from exc
        except OpenAIError as exc:
            logger.exception("OpenAI client error during structured call")
            raise OpenAIServiceError(f"Structured generation failed: {exc}") from exc

        parsed = response.output_parsed
        if parsed is None:
            raise OpenAIServiceError("The model did not return a parseable structured answer.")

        # Ensure citations reflect what was actually retrieved, even if the
        # model omitted or altered them.
        if not parsed.sources:
            parsed.sources = self.contexts_to_sources(contexts)
        return parsed
