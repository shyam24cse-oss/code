"""Tests for the selected-text explanation endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import deps
from app.domain.models import Conversation, Document, DocumentStatus
from app.main import app

client = TestClient(app)


def test_explain_stream_passes_selection_and_question_separately() -> None:
    document = Document(
        id="doc-1",
        filename="machine-learning.pdf",
        stored_path="missing.pdf",
        status=DocumentStatus.FAILED,
    )
    conversation = Conversation(id="conversation-1", title="Explain this")
    captured: dict[str, object] = {}

    class StubDocumentService:
        def get_document(self, document_id: str) -> Document | None:
            return document if document_id == document.id else None

        def get_context_for_selection(
            self, document_id: str, page_number: int, selected_text: str
        ) -> str:
            captured["document_id"] = document_id
            captured["page_number"] = page_number
            return "Previous sentence. Next sentence."

    class StubMemoryService:
        def get_or_create_conversation(self, conversation_id: str | None, title_hint: str):
            return conversation

        def get_history(self, conversation_id: str):
            return []

        def to_prompt_history(self, messages):
            return []

        def append_message(self, conversation_id: str, role, content: str, sources=None):
            return None

    class StubChatService:
        @staticmethod
        def contexts_to_sources(contexts):
            return []

        def stream_explanation(
            self,
            selected_text: str,
            user_question: str,
            document_name: str,
            page_number: int,
            surrounding_context: str,
            contexts,
            history,
        ):
            captured.update(
                selected_text=selected_text,
                user_question=user_question,
                document_name=document_name,
                page_number=page_number,
                surrounding_context=surrounding_context,
            )
            yield "A simple explanation."

    app.dependency_overrides[deps.get_document_service] = lambda: StubDocumentService()
    app.dependency_overrides[deps.get_memory_service] = lambda: StubMemoryService()
    app.dependency_overrides[deps.get_chat_service] = lambda: StubChatService()
    try:
        response = client.post(
            "/api/chat/explain/stream",
            json={
                "selected_text": "Gradient descent updates model weights.",
                "user_question": "Explain this simply.",
                "document_id": document.id,
                "page_number": 7,
            },
        )
    finally:
        app.dependency_overrides.pop(deps.get_document_service, None)
        app.dependency_overrides.pop(deps.get_memory_service, None)
        app.dependency_overrides.pop(deps.get_chat_service, None)

    assert response.status_code == 200
    assert "event: token" in response.text
    assert "A simple explanation." in response.text
    assert captured == {
        "document_id": document.id,
        "page_number": 7,
        "selected_text": "Gradient descent updates model weights.",
        "user_question": "Explain this simply.",
        "document_name": "machine-learning.pdf",
        "surrounding_context": "Previous sentence. Next sentence.",
    }


def test_explain_stream_rejects_blank_selection() -> None:
    response = client.post(
        "/api/chat/explain/stream",
        json={
            "selected_text": "   ",
            "user_question": "Explain this.",
            "document_id": "doc-1",
            "page_number": 1,
        },
    )

    assert response.status_code == 422