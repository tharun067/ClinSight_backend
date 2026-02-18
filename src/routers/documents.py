"""
Document upload and management router.
Fixed: admin included in all role guards, audit action for GET corrected to VIEW_PATIENT.
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
from src.models.document import Document, DocumentType
from src.schemas.document import DocumentUploadResponse, DocumentResponse
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


def validate_file_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in settings.ALLOWED_EXTENSIONS


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    patient_id: str = Form(...),
    document_type: str = Form(...),
    notes: Optional[str] = Form(None),
    current_user: User = Depends(require_roles("intake", "nurse", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document for a patient."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No patient record linked.")
        if current_user.patient_record.uuid != patient_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only upload to your own record.")

    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    if not result.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    if not validate_file_extension(file.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Supported: {settings.ALLOWED_EXTENSIONS}",
        )

    try:
        doc_type = DocumentType(document_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid document type. Must be one of: {[t.value for t in DocumentType]}",
        )

    file_ext = Path(file.filename).suffix.lower()
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)

    try:
        content = await file.read()
        if len(content) > settings.MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Max: {settings.MAX_UPLOAD_SIZE // (1024*1024)}MB",
            )
        async with aiofiles.open(file_path, "wb") as out_file:
            await out_file.write(content)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File upload failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="File upload failed")

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
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    # Process document to extract medical data (async background task)
    processing_started = False
    if file_ext in (".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".bmp"):
        try:
            # Start background processing for data extraction
            processor = get_document_processor()
            # Background task will create its own DB session
            asyncio.create_task(
                processor.process_document(
                    file_path=file_path,
                    file_extension=file_ext,
                    patient_uuid=patient_id,
                    user_uuid=current_user.uuid,
                )
            )
            processing_started = True
            logger.info(f"Started background processing for document {document.uuid}")
        except Exception as e:
            logger.warning(f"Failed to start document processing for {document.uuid}: {e}")
    
    # Embed text-based documents for semantic search
    if file_ext in (".pdf", ".txt"):
        try:
            # Basic embedding for document search
            text_content = f"Document: {file.filename}. Type: {document_type}. Notes: {notes or ''}."
            embedding = get_embedding_service().embed_text(text_content)
            get_vector_db().add_vectors(
                embeddings=embedding,
                metadata=[{
                    "document_id": document.uuid,
                    "patient_id": patient_id,
                    "document_type": document_type,
                    "filename": file.filename,
                    "text": text_content,
                }],
            )
        except Exception as e:
            logger.warning(f"Failed to embed document {document.uuid}: {e}")

    await log_audit(
        db=db, user=current_user, action=AuditAction.UPLOAD_DOCUMENT,
        target_type="document", target_uuid=document.uuid,
        patient_uuid=patient_id,
        action_details=f"Uploaded {document_type}: {file.filename}" + 
                      (" - Auto-extraction started" if processing_started else ""),
        request=request,
    )
    
    # Add processing status to response
    response = document
    if processing_started:
        # Add a note to the response indicating background processing
        logger.info(f"Document {document.uuid} uploaded successfully, extracting medical data in background")
    
    return response


@router.post("/bulk-upload", response_model=List[DocumentUploadResponse])
async def bulk_upload_documents(
    request: Request,
    files: List[UploadFile] = File(...),
    patient_id: str = Form(...),
    document_types: str = Form(...),
    current_user: User = Depends(require_roles("intake", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Bulk upload multiple documents for a patient (Intake Officer or Admin)."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    if not result.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    doc_types_list = [t.strip() for t in document_types.split(",")]
    if len(doc_types_list) != len(files):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Number of document_types must match number of files",
        )

    uploaded = []
    for file, doc_type_str in zip(files, doc_types_list):
        if not validate_file_extension(file.filename):
            logger.warning(f"Skipping invalid file: {file.filename}")
            continue
        try:
            doc_type = DocumentType(doc_type_str)
            file_ext = Path(file.filename).suffix.lower()
            unique_filename = f"{uuid.uuid4()}{file_ext}"
            file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)
            content = await file.read()
            if len(content) > settings.MAX_UPLOAD_SIZE:
                logger.warning(f"Skipping oversized file: {file.filename}")
                continue
            async with aiofiles.open(file_path, "wb") as out_file:
                await out_file.write(content)
            document = Document(
                filename=unique_filename,
                original_filename=file.filename,
                file_path=file_path,
                file_size=len(content),
                mime_type=file.content_type or "application/octet-stream",
                document_type=doc_type,
                patient_uuid=patient_id,
                uploaded_by=current_user.uuid,
            )
            db.add(document)
            uploaded.append(document)
        except Exception as e:
            logger.error(f"Error uploading {file.filename}: {e}")

    await db.commit()
    for doc in uploaded:
        await db.refresh(doc)

    await log_audit(
        db=db, user=current_user, action=AuditAction.UPLOAD_DOCUMENT,
        target_type="documents", patient_uuid=patient_id,
        action_details=f"Bulk uploaded {len(uploaded)} documents", request=request,
    )
    return uploaded


@router.get("/", response_model=List[DocumentResponse])
async def list_documents(
    request: Request,
    patient_id: Optional[str] = Query(None),
    document_type: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("intake", "nurse", "radiologist", "physician", "admin", "compliance", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """List documents with optional filters."""
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
    """Get all documents from the patient's own record."""
    if not current_user.patient_record:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No patient record linked.")
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
    document_id: str,
    request: Request,
    current_user: User = Depends(require_roles("intake", "nurse", "radiologist", "physician", "admin", "compliance", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get document details."""
    document = (await db.execute(select(Document).where(Document.uuid == document_id))).scalars().first()
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != document.patient_uuid:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    await log_audit(
        db=db, user=current_user, action=AuditAction.VIEW_PATIENT,
        target_type="document", target_uuid=document_id,
        patient_uuid=document.patient_uuid, request=request,
    )
    return document


@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    request: Request,
    current_user: User = Depends(require_roles("intake", "nurse", "radiologist", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Download a document file."""
    from fastapi.responses import FileResponse
    document = (await db.execute(select(Document).where(Document.uuid == document_id))).scalars().first()
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != document.patient_uuid:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if not os.path.exists(document.file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found on disk")
    await log_audit(
        db=db, user=current_user, action=AuditAction.DOWNLOAD_DOCUMENT,
        target_type="document", target_uuid=document_id,
        patient_uuid=document.patient_uuid, request=request,
    )
    return FileResponse(
        path=document.file_path,
        filename=document.original_filename,
        media_type=document.mime_type,
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    request: Request,
    current_user: User = Depends(require_roles("intake", "physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document and its physical file."""
    document = (await db.execute(select(Document).where(Document.uuid == document_id))).scalars().first()
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    try:
        if os.path.exists(document.file_path):
            os.remove(document.file_path)
    except Exception as e:
        logger.error(f"Failed to delete physical file {document.file_path}: {e}")
    patient_uuid = document.patient_uuid
    await db.delete(document)
    await db.commit()
    await log_audit(
        db=db, user=current_user, action=AuditAction.OTHER,
        target_type="document", target_uuid=document_id,
        patient_uuid=patient_uuid, action_details="Deleted document", request=request,
    )


@router.post("/my-documents/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_my_document(
    request: Request,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    notes: Optional[str] = Form(None),
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Patient uploads a document directly to their own record."""
    if not current_user.patient_record:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No patient record linked.")
    patient_id = current_user.patient_record.uuid

    if not validate_file_extension(file.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Supported: {settings.ALLOWED_EXTENSIONS}",
        )
    try:
        doc_type = DocumentType(document_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid document type.",
        )

    file_ext = Path(file.filename).suffix.lower()
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)

    try:
        content = await file.read()
        if len(content) > settings.MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Max: {settings.MAX_UPLOAD_SIZE // (1024*1024)}MB",
            )
        async with aiofiles.open(file_path, "wb") as out_file:
            await out_file.write(content)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Patient file upload failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="File upload failed")

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
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    await log_audit(
        db=db, user=current_user, action=AuditAction.UPLOAD_DOCUMENT,
        target_type="document", target_uuid=document.uuid,
        patient_uuid=patient_id, action_details=f"Patient uploaded {file.filename}", request=request,
    )
    return document