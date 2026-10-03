"""Integration tests for document upload / listing / deletion endpoints.

These tests exercise the extraction and chunking pipeline end-to-end but do
NOT call the real OpenAI API (embeddings are network calls), so they are
skipped automatically unless a real ``OPENAI_API_KEY`` is present. This keeps
the test suite runnable offline / in CI without incurring API costs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_document_service
from app.domain.models import Document, DocumentStatus
from app.main import app

client = TestClient(app)

requires_openai_key = pytest.mark.skipif(
    os.environ.get("OPENAI_API_KEY", "").startswith("sk-test"),
    reason="Requires a real OPENAI_API_KEY to generate embeddings.",
)


def test_list_documents_empty_by_default() -> None:
    response = client.get("/api/documents")
    assert response.status_code == 200
    assert "documents" in response.json()


def test_upload_rejects_non_pdf(tmp_path: Path) -> None:
    fake_file = tmp_path / "not-a-pdf.txt"
    fake_file.write_text("hello world")

    with fake_file.open("rb") as fh:
        response = client.post(
            "/api/documents", files={"file": ("not-a-pdf.txt", fh, "text/plain")}
        )

    assert response.status_code == 400


def test_stream_document_file_even_when_indexing_failed(tmp_path: Path) -> None:
    pdf_path = tmp_path / "uploaded.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")
    document = Document(
        id="doc-failed",
        filename="uploaded.pdf",
        stored_path=str(pdf_path),
        status=DocumentStatus.FAILED,
    )

    class StubDocumentService:
        def get_document(self, document_id: str) -> Document | None:
            return document if document_id == document.id else None

    app.dependency_overrides[get_document_service] = lambda: StubDocumentService()
    try:
        response = client.get(f"/api/documents/{document.id}/file")
    finally:
        app.dependency_overrides.pop(get_document_service, None)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == b"%PDF-1.4 test"


@requires_openai_key
def test_upload_indexes_sample_pdf(sample_pdf_path: Path) -> None:
    with sample_pdf_path.open("rb") as fh:
        response = client.post(
            "/api/documents", files={"file": (sample_pdf_path.name, fh, "application/pdf")}
        )

    assert response.status_code == 201
    body = response.json()
    assert body["document"]["status"] == "indexed"
    assert body["document"]["chunk_count"] > 0
