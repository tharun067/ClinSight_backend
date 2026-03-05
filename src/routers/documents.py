"""
Document upload and management router.

Role permissions:
- nurse:     upload/view/bulk-upload documents for patients
- physician: view/download/delete documents
- admin:     full access
- patient:   upload and view own documents only

New endpoints:
- GET /api/documents/{id}/extraction-status  → poll AI extraction progress + results
- GET /api/documents/patient/{patient_id}/extractions → all extraction results for a patient
"""
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
import os, uuid, aiofiles, logging
from pathlib import Path

from src.database.postgres import get_db
from src.models.user import User, UserRole
from src.models.patient import Patient
from src.models.document import Document, DocumentType, ExtractionStatus
from src.models.lab_vital import LabResult, VitalSign
from src.models.imaging_study import ImagingStudy
from src.schemas.document import DocumentUploadResponse, DocumentResponse, DocumentExtractionResponse, ExtractionResultsDetail
from src.utils.auth import require_roles, log_audit
from src.utils.validators import validate_uuid
from src.models.audit_log import AuditAction
from src.config import settings
from src.services.embedding import get_embedding_service
from src.services.document_processor import get_document_processor
from src.database.vector_db import get_vector_db
import asyncio

router = APIRouter()
logger = logging.getLogger(__name__)

EXTRACTABLE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".bmp"}


def validate_file_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in settings.ALLOWED_EXTENSIONS


def parse_document_type(value: str) -> DocumentType:
    if not value:
        raise ValueError("Document type is required")
    normalized = value.strip().lower()
    for doc_type in DocumentType:
        if doc_type.value.lower() == normalized:
            return doc_type
    raise ValueError(f"Invalid document type: {value}")


async def _do_upload(
    request, file, patient_id, document_type, notes, current_user, db
) -> Document:
    """Shared upload logic used by both upload endpoints."""
    patient_id = validate_uuid(patient_id, "Patient ID")

    if not (await db.execute(select(Patient).where(Patient.uuid == patient_id))).scalars().first():
        raise HTTPException(status_code=404, detail="Patient not found")
    if not validate_file_extension(file.filename):
        raise HTTPException(status_code=400, detail=f"File type not allowed. Supported: {settings.ALLOWED_EXTENSIONS}")
    try:
        doc_type = parse_document_type(document_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid document type. Options: {[t.value for t in DocumentType]}")

    file_ext = Path(file.filename).suffix.lower()
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)

    try:
        content = await file.read()
        if len(content) > settings.MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail=f"File too large. Max: {settings.MAX_UPLOAD_SIZE // (1024*1024)}MB")
        async with aiofiles.open(file_path, "wb") as out_file:
            await out_file.write(content)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File write failed: {e}")
        raise HTTPException(status_code=500, detail="File upload failed")

    # Determine if extraction will be attempted
    will_extract = file_ext in EXTRACTABLE_EXTENSIONS
    extraction_status = ExtractionStatus.PENDING if will_extract else ExtractionStatus.NOT_STARTED

    document = Document(
        filename=unique_filename,
        original_filename=file.filename,
        file_path=file_path,
        file_size=len(content),
        mime_type=file.content_type or "application/octet-stream",
        document_type=doc_type,
        patient_uuid=patient_id,
        uploaded_by=current_user.uuid,
        notes=notes,
        extraction_status=extraction_status,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    if will_extract:
        # Launch background extraction — passes document_uuid so processor can update status
        processor = get_document_processor()
        asyncio.create_task(processor.process_document(
            file_path=file_path,
            file_extension=file_ext,
            patient_uuid=patient_id,
            user_uuid=current_user.uuid,
            document_uuid=document.uuid,
        ))

    # Basic embedding for semantic search
    if file_ext in (".pdf", ".txt"):
        try:
            text_content = f"Document: {file.filename}. Type: {document_type}. Notes: {notes or ''}."
            embedding = get_embedding_service().embed_text(text_content)
            get_vector_db().add_vectors(embeddings=embedding, metadata=[{
                "document_id": document.uuid, "patient_id": patient_id,
                "document_type": document_type, "filename": file.filename, "text": text_content,
            }])
        except Exception as e:
            logger.warning(f"Failed to embed document {document.uuid}: {e}")

    await log_audit(
        db=db, user=current_user, action=AuditAction.UPLOAD_DOCUMENT,
        target_type="document", target_uuid=document.uuid, patient_uuid=patient_id,
        action_details=f"Uploaded {document_type}: {file.filename}" +
                      (" — AI extraction queued" if will_extract else ""),
        request=request,
    )
    return document


# ── Upload endpoints ─────────────────────────────────────────────────────────

@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    patient_id: str = Form(...),
    document_type: str = Form(...),
    notes: Optional[str] = Form(None),
    current_user: User = Depends(require_roles("nurse", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a document. If the file is a PDF or image, AI extraction is automatically
    queued in the background. Poll GET /api/documents/{id}/extraction-status to see results.
    """
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record:
            raise HTTPException(status_code=403, detail="No patient record linked.")
        if current_user.patient_record.uuid != patient_id:
            raise HTTPException(status_code=403, detail="You can only upload to your own record.")

    return await _do_upload(request, file, patient_id, document_type, notes, current_user, db)


@router.post("/my-documents/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_my_document(
    request: Request,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    notes: Optional[str] = Form(None),
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Patient: upload a document to own record. AI extraction is queued automatically."""
    if not current_user.patient_record:
        raise HTTPException(status_code=403, detail="No patient record linked.")
    return await _do_upload(
        request, file, current_user.patient_record.uuid,
        document_type, notes, current_user, db,
    )


@router.post("/bulk-upload", response_model=List[DocumentUploadResponse])
async def bulk_upload_documents(
    request: Request,
    files: List[UploadFile] = File(...),
    patient_id: str = Form(...),
    document_types: str = Form(...),
    current_user: User = Depends(require_roles("nurse", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Bulk upload multiple documents (Nurse or Admin). AI extraction is queued for each eligible file."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    if not (await db.execute(select(Patient).where(Patient.uuid == patient_id))).scalars().first():
        raise HTTPException(status_code=404, detail="Patient not found")

    doc_types_list = [t.strip() for t in document_types.split(",")]
    if len(doc_types_list) != len(files):
        raise HTTPException(status_code=400, detail="Number of document_types must match number of files")

    uploaded = []
    for file, doc_type_str in zip(files, doc_types_list):
        try:
            doc = await _do_upload(request, file, patient_id, doc_type_str, None, current_user, db)
            uploaded.append(doc)
        except Exception as e:
            logger.error(f"Skipping {file.filename}: {e}")

    await log_audit(
        db=db, user=current_user, action=AuditAction.UPLOAD_DOCUMENT,
        target_type="documents", patient_uuid=patient_id,
        action_details=f"Bulk uploaded {len(uploaded)} documents", request=request,
    )
    return uploaded


# ── Extraction status endpoints ───────────────────────────────────────────────

@router.get("/{document_id}/extraction-status", response_model=DocumentExtractionResponse)
async def get_extraction_status(
    document_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """
    Poll AI extraction status and results for a document.

    Extraction statuses:
    - **not_started** — file type not supported (e.g. Insurance Card, .txt)
    - **pending**     — queued, AI hasn't started yet
    - **processing**  — AI is actively extracting data
    - **completed**   — done; see `results` for what was found
    - **failed**      — extraction error; see `extraction_error`

    When **completed**, `results` contains:
    - `labs_extracted` / `lab_ids` — lab results auto-created from this doc
    - `vitals_extracted` / `vital_ids` — vital sign records created
    - `imaging_extracted` / `imaging_ids` — imaging studies created
    - `raw_text_length` — characters of text found in the document
    """
    document = (await db.execute(
        select(Document).where(Document.uuid == document_id)
    )).scalars().first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != document.patient_uuid:
            raise HTTPException(status_code=403, detail="Access denied")

    results = None
    if document.extraction_results:
        r = document.extraction_results
        results = ExtractionResultsDetail(
            labs_extracted=r.get("labs_extracted", 0),
            vitals_extracted=r.get("vitals_extracted", 0),
            imaging_extracted=r.get("imaging_extracted", 0),
            lab_ids=r.get("lab_ids", []),
            vital_ids=r.get("vital_ids", []),
            imaging_ids=r.get("imaging_ids", []),
            raw_text_length=r.get("raw_text_length", 0),
            ai_provider=r.get("ai_provider"),
            extraction_warning=r.get("extraction_warning"),
        )

    return DocumentExtractionResponse(
        document_id=document.uuid,
        original_filename=document.original_filename,
        document_type=document.document_type.value,
        patient_id=document.patient_uuid,
        upload_date=document.upload_date,
        extraction_status=document.extraction_status.value,
        extraction_started_at=document.extraction_started_at,
        extraction_completed_at=document.extraction_completed_at,
        extraction_error=document.extraction_error,
        results=results,
    )


@router.get("/patient/{patient_id}/extractions", response_model=List[DocumentExtractionResponse])
async def get_patient_extractions(
    patient_id: str,
    request: Request,
    status_filter: Optional[str] = Query(None, description="Filter by: pending, processing, completed, failed, not_started"),
    current_user: User = Depends(require_roles("nurse", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all document extraction results for a patient — useful for a summary
    dashboard showing what AI has pulled from all uploaded documents.
    """
    patient_id = validate_uuid(patient_id, "Patient ID")
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(status_code=403, detail="Access denied")

    query = select(Document).where(Document.patient_uuid == patient_id)
    if status_filter:
        try:
            query = query.where(Document.extraction_status == ExtractionStatus(status_filter))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status. Options: {[s.value for s in ExtractionStatus]}")
    query = query.order_by(Document.upload_date.desc())
    docs = (await db.execute(query)).scalars().all()

    response = []
    for doc in docs:
        results = None
        if doc.extraction_results:
            r = doc.extraction_results
            results = ExtractionResultsDetail(
                labs_extracted=r.get("labs_extracted", 0),
                vitals_extracted=r.get("vitals_extracted", 0),
                imaging_extracted=r.get("imaging_extracted", 0),
                lab_ids=r.get("lab_ids", []),
                vital_ids=r.get("vital_ids", []),
                imaging_ids=r.get("imaging_ids", []),
                raw_text_length=r.get("raw_text_length", 0),
                ai_provider=r.get("ai_provider"),
                extraction_warning=r.get("extraction_warning"),
            )
        response.append(DocumentExtractionResponse(
            document_id=doc.uuid,
            original_filename=doc.original_filename,
            document_type=doc.document_type.value,
            patient_id=doc.patient_uuid,
            upload_date=doc.upload_date,
            extraction_status=doc.extraction_status.value,
            extraction_started_at=doc.extraction_started_at,
            extraction_completed_at=doc.extraction_completed_at,
            extraction_error=doc.extraction_error,
            results=results,
        ))
    return response


# ── Standard document endpoints ───────────────────────────────────────────────

@router.get("/", response_model=List[DocumentResponse])
async def list_documents(
    request: Request,
    patient_id: Optional[str] = Query(None),
    document_type: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("nurse", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """List documents. Patients only see their own. Includes extraction_status in response."""
    query = select(Document)
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record:
            return []
        query = query.where(Document.patient_uuid == current_user.patient_record.uuid)
    elif patient_id:
        query = query.where(Document.patient_uuid == patient_id)
    if document_type:
        try:
            query = query.where(Document.document_type == DocumentType(document_type))
        except ValueError:
            pass
    query = query.order_by(Document.upload_date.desc())
    return (await db.execute(query)).scalars().all()


@router.get("/my-documents", response_model=List[DocumentResponse])
async def get_my_documents(
    request: Request,
    document_type: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Patient: get own documents with extraction status."""
    if not current_user.patient_record:
        raise HTTPException(status_code=403, detail="No patient record linked.")
    query = select(Document).where(Document.patient_uuid == current_user.patient_record.uuid)
    if document_type:
        try:
            query = query.where(Document.document_type == DocumentType(document_type))
        except ValueError:
            pass
    query = query.order_by(Document.upload_date.desc())
    return (await db.execute(query)).scalars().all()


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str, request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get document details including extraction_status and extraction_results summary."""
    document = (await db.execute(select(Document).where(Document.uuid == document_id))).scalars().first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != document.patient_uuid:
            raise HTTPException(status_code=403, detail="Access denied")
    await log_audit(
        db=db, user=current_user, action=AuditAction.VIEW_PATIENT,
        target_type="document", target_uuid=document_id,
        patient_uuid=document.patient_uuid, request=request,
    )
    return document


@router.get("/{document_id}/download")
async def download_document(
    document_id: str, request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Download the original document file."""
    from fastapi.responses import FileResponse
    document = (await db.execute(select(Document).where(Document.uuid == document_id))).scalars().first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != document.patient_uuid:
            raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(document.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")
    await log_audit(
        db=db, user=current_user, action=AuditAction.DOWNLOAD_DOCUMENT,
        target_type="document", target_uuid=document_id,
        patient_uuid=document.patient_uuid, request=request,
    )
    return FileResponse(path=document.file_path, filename=document.original_filename, media_type=document.mime_type)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str, request: Request,
    current_user: User = Depends(require_roles("physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document and its physical file (Physician or Admin only)."""
    document = (await db.execute(select(Document).where(Document.uuid == document_id))).scalars().first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        if os.path.exists(document.file_path):
            os.remove(document.file_path)
    except Exception as e:
        logger.error(f"Failed to delete file {document.file_path}: {e}")
    patient_uuid = document.patient_uuid
    await db.delete(document)
    await db.commit()
    await log_audit(
        db=db, user=current_user, action=AuditAction.OTHER,
        target_type="document", target_uuid=document_id,
        patient_uuid=patient_uuid, action_details="Deleted document", request=request,
    )
