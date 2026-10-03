"""Unit tests for PDFExtractionService."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import UnsupportedFileTypeError
from app.services.pdf_service import PDFExtractionService


def test_extract_pages_returns_text_per_page(sample_pdf_path: Path) -> None:
    service = PDFExtractionService()
    pages = service.extract_pages(sample_pdf_path)

    assert len(pages) == 3
    assert "Retrieval-Augmented Generation" in pages[0]
    assert "Semantic Search" in pages[2]


def test_extract_pages_rejects_non_pdf(tmp_path: Path) -> None:
    fake_file = tmp_path / "not-a-pdf.txt"
    fake_file.write_text("hello world")

    service = PDFExtractionService()
    with pytest.raises(UnsupportedFileTypeError):
        service.extract_pages(fake_file)


def test_extract_surrounding_context_uses_selection_and_adjacent_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PDFExtractionService()
    pages = [
        "previous material " * 30,
        "before selected sentence after",
        "following material " * 30,
    ]
    monkeypatch.setattr(service, "extract_pages", lambda _: pages)

    context = service.extract_surrounding_context(
        Path("document.pdf"), 2, "selected sentence", radius=8
    )

    assert "Previous page (p. 1)" in context
    assert "Selected page before passage (p. 2): before " in context
    assert "Selected page after passage (p. 2):  after" in context
    assert "Following page (p. 3)" in context
