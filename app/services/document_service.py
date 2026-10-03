"""
Document ingestion service.

Orchestrates the full upload pipeline:

    PDF upload -> save to disk -> text extraction -> chunking ->
    embedding -> vector store indexing -> metadata persistence

This is the single entry point the API layer calls when a user uploads a
PDF, keeping the route handler itself thin (Clean Architecture: use-case
orchestration lives in the service layer, not the controller).
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.exceptions import EmptyDocumentError, FileTooLargeError, UnsupportedFileTypeError
from app.domain.models import Document, DocumentStatus
from app.infrastructure.repository import DocumentRepository
from app.services.chunking_service import ChunkingService
from app.services.pdf_service import PDFExtractionService
from app.services.vector_store_service import VectorStoreService

logger = logging.getLogger(__name__)


class DocumentService:
    """Coordinates PDF ingestion: extraction, chunking, and indexing."""

    def __init__(
        self,
        db: Session,
        pdf_service: PDFExtractionService | None = None,
        chunking_service: ChunkingService | None = None,
        vector_store_service: VectorStoreService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repo = DocumentRepository(db)
        self._pdf_service = pdf_service or PDFExtractionService()
        self._chunking_service = chunking_service or ChunkingService(
            chunk_size=self._settings.chunk_size, chunk_overlap=self._settings.chunk_overlap
        )
        self._vector_store = vector_store_service or VectorStoreService(settings=self._settings)

    def validate_upload(self, filename: str, size_bytes: int) -> None:
        if not filename.lower().endswith(".pdf"):
            raise UnsupportedFileTypeError()
        if size_bytes > self._settings.max_upload_bytes:
            raise FileTooLargeError(
                f"File exceeds the {self._settings.max_upload_mb}MB upload limit."
            )

    def save_upload(self, filename: str, content: bytes) -> tuple[str, Path]:
        """Persist the raw upload bytes to disk under a unique document id."""
        document_id = str(uuid.uuid4())
        safe_name = Path(filename).name
        destination = self._settings.upload_dir / f"{document_id}_{safe_name}"
        destination.write_bytes(content)
        logger.info("Saved upload '%s' (%d bytes) -> %s", filename, len(content), destination)
        return document_id, destination

    def ingest(
        self, document_id: str, filename: str, stored_path: Path, size_bytes: int
    ) -> Document:
        """Run the extraction -> chunking -> embedding -> indexing pipeline."""
        document = Document(
            id=document_id,
            filename=filename,
            stored_path=str(stored_path),
            status=DocumentStatus.UPLOADED,
            size_bytes=size_bytes,
        )
        self._repo.add(document)

        try:
            self._repo.update_status(document_id, DocumentStatus.PROCESSING)

            pages = self._pdf_service.extract_pages(stored_path)
            chunks = self._chunking_service.chunk_pages(pages, document_id, filename)

            if not chunks:
                raise EmptyDocumentError()

            self._repo.update_status(
                document_id,
                DocumentStatus.PROCESSING,
                page_count=len(pages),
                chunk_count=len(chunks),
            )
            self._vector_store.add_chunks(chunks)

            self._repo.update_status(
                document_id,
                DocumentStatus.INDEXED,
                page_count=len(pages),
                chunk_count=len(chunks),
            )
            logger.info(
                "Indexed document '%s' (%d pages, %d chunks)", filename, len(pages), len(chunks)
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ingestion failed for document %s", document_id)
            self._repo.update_status(
                document_id, DocumentStatus.FAILED, error_message=str(exc)
            )
            raise

        return self._repo.get(document_id)  # type: ignore[return-value]

    def list_documents(self) -> list[Document]:
        return self._repo.list_all()

    def get_document(self, document_id: str) -> Document | None:
        return self._repo.get(document_id)

    def get_context_for_selection(
        self, document_id: str, page_number: int, selected_text: str
    ) -> str:
        document = self._repo.get(document_id)
        if document is None:
            return ""
        return self._pdf_service.extract_surrounding_context(
            Path(document.stored_path), page_number, selected_text
        )

    def delete_document(self, document_id: str) -> bool:
        document = self._repo.get(document_id)
        if document is None:
            return False

        stored_path = Path(document.stored_path)
        if stored_path.exists():
            stored_path.unlink(missing_ok=True)

        self._vector_store.delete_document(document_id)
        return self._repo.delete(document_id)
