"""Unit tests for selected-text explanation prompt construction."""

from __future__ import annotations

from app.services.chat_service import ChatService


def test_explanation_prompt_keeps_selection_context_and_question_separate() -> None:
    prompt = ChatService.build_explanation_prompt(
        selected_text="Backpropagation updates model weights.",
        user_question="Explain this simply.",
        document_name="ml.pdf",
        page_number=4,
        surrounding_context="Previous sentence. Following sentence.",
        contexts=[],
    )

    assert "Selected text (primary source, ml.pdf, page 4):" in prompt
    assert "Backpropagation updates model weights." in prompt
    assert "Nearby PDF context (use only if relevant):" in prompt
    assert "User's question (answer this request about the selected text):" in prompt
    assert prompt.index("Selected text") < prompt.index("User's question")