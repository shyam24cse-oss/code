"""
PDF text-extraction service.

Wraps ``pypdf`` to extract per-page text from uploaded PDF files. Isolating
this behind a service means the rest of the application never needs to know
which PDF library is in use.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.exceptions import EmptyDocumentError, UnsupportedFileTypeError

logger = logging.getLogger(__name__)


class PDFExtractionService:
    """Extracts text content from PDF files, page by page."""

    def extract_pages(self, file_path: Path) -> list[str]:
        """Return a list of page texts (index 0 == page 1)."""
        if file_path.suffix.lower() != ".pdf":
            raise UnsupportedFileTypeError(f"'{file_path.suffix}' is not a supported file type.")

        try:
            reader = PdfReader(str(file_path))
        except PdfReadError as exc:
            logger.exception("Failed to read PDF: %s", file_path)
            raise UnsupportedFileTypeError("The uploaded file is not a valid PDF.") from exc

        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise UnsupportedFileTypeError(
                    "The PDF is password-protected and cannot be processed."
                ) from exc

        pages: list[str] = []
        for index, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001
                logger.warning("Failed to extract text from page %s of %s", index + 1, file_path)
                text = ""
            pages.append(text.strip())

        if not any(pages):
            raise EmptyDocumentError(
                "No extractable text was found. The PDF may be scanned images without OCR."
            )

        logger.info("Extracted %d pages from %s", len(pages), file_path.name)
        return pages

    def extract_surrounding_context(
        self, file_path: Path, page_number: int, selected_text: str, radius: int = 600
    ) -> str:
        """Return bounded text around a selection and its adjacent pages."""
        pages = self.extract_pages(file_path)
        if page_number > len(pages):
            return ""

        page_text = pages[page_number - 1]
        words = [re.escape(word) for word in selected_text.split()]
        selected_match = (
            re.search(r"\s+".join(words), page_text, re.IGNORECASE) if words else None
        )

        if selected_match:
            before = page_text[max(0, selected_match.start() - radius) : selected_match.start()]
            after = page_text[selected_match.end() : selected_match.end() + radius]
        else:
            before = page_text[:radius]
            after = page_text[-radius:]

        context_parts = []
        if page_number > 1:
            previous_page = pages[page_number - 2][-400:]
            context_parts.append(f"Previous page (p. {page_number - 1}): {previous_page}")
        context_parts.append(f"Selected page before passage (p. {page_number}): {before}")
        context_parts.append(f"Selected page after passage (p. {page_number}): {after}")
        if page_number < len(pages):
            following_page = pages[page_number][:400]
            context_parts.append(f"Following page (p. {page_number + 1}): {following_page}")
        return "\n\n".join(part for part in context_parts if part.strip())
