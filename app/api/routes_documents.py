"""
Document management endpoints: upload, list, retrieve, delete.

Implements the "Upload PDF / Multiple PDFs / Document Library / Automatic
Text Extraction" features by delegating to :class:`DocumentService`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from openai import OpenAIError

from app.api.deps import get_document_service
from app.core.exceptions import (
    EmptyDocumentError,
    FileTooLargeError,
    PDFChatGPTError,
    UnsupportedFileTypeError,
)
from app.domain.schemas import (
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from app.services.document_service import DocumentService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["Documents"])


@router.post(
    "",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and index a PDF document",
    description=(
        "Uploads a PDF, extracts its text, splits it into overlapping chunks, "
        "generates embeddings, and stores them in the FAISS vector index so "
        "the document becomes immediately searchable."
    ),
)
async def upload_document(
    file: UploadFile = File(..., description="PDF file to upload."),
    service: DocumentService = Depends(get_document_service),
) -> DocumentUploadResponse:
    content = await file.read()

    try:
        service.validate_upload(file.filename or "upload.pdf", len(content))
        document_id, stored_path = service.save_upload(file.filename or "upload.pdf", content)
        document = service.ingest(
            document_id, file.filename or "upload.pdf", stored_path, len(content)
        )
    except (UnsupportedFileTypeError, FileTooLargeError, EmptyDocumentError) as exc:
        logger.warning("Upload rejected: %s", exc.message)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    except OpenAIError as exc:
        logger.exception("PDF was saved but embedding generation failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "PDF saved and text extracted, but indexing failed because the embedding "
                "provider rejected the request. Check API quota and retry."
            ),
        ) from exc
    except PDFChatGPTError as exc:
        logger.exception("Ingestion failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.message
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("PDF saved but indexing failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "PDF saved, but indexing failed. The saved file remains available in the library."
            ),
        ) from exc

    return DocumentUploadResponse(document=DocumentResponse.model_validate(document))


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List all uploaded documents",
)
def list_documents(
    service: DocumentService = Depends(get_document_service),
) -> DocumentListResponse:
    documents = service.list_documents()
    return DocumentListResponse(
        documents=[DocumentResponse.model_validate(d) for d in documents],
        total=len(documents),
    )


@router.get(
    "/{document_id}/file",
    response_class=FileResponse,
    summary="Stream the original uploaded PDF",
)
def get_document_file(
    document_id: str, service: DocumentService = Depends(get_document_service)
) -> FileResponse:
    document = service.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    stored_path = Path(document.stored_path)
    if not stored_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF file not found.")

    return FileResponse(stored_path, media_type="application/pdf")


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get a single document's metadata",
)
def get_document(
    document_id: str, service: DocumentService = Depends(get_document_service)
) -> DocumentResponse:
    document = service.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return DocumentResponse.model_validate(document)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a document and remove it from the vector index",
)
def delete_document(
    document_id: str, service: DocumentService = Depends(get_document_service)
) -> Response:
    deleted = service.delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
