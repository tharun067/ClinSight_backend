"""
Document upload and management router.
"""
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Optional
import os
import uuid
import aiofiles
import logging
from pathlib import Path
from datetime import datetime

from src.database.postgres import get_db
from src.models.user import User
from src.models.patient import Patient
from src.models.document import Document, DocumentType
from src.schemas.document import DocumentUploadResponse, DocumentResponse
from src.utils.auth import get_current_active_user, require_roles, log_audit
from src.models.audit_log import AuditAction
from src.config import settings
from src.services.embedding import get_embedding_service
from src.database.vector_db import get_vector_db

router = APIRouter()
logger = logging.getLogger(__name__)

def validate_file_extension(filename: str) -> bool:
    """Validate file extension."""
    ext = Path(filename).suffix.lower()
    return ext in settings.ALLOWED_EXTENSIONS

@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    patient_id: str = Form(...),
    document_type: str = Form(...),
    notes: Optional[str] = Form(None),
    current_user: User = Depends(require_roles("intake", "nurse", "physician")),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload a document for a patient.
    
    Supported file types: PDF, JPG, JPEG, PNG
    """
    
    # Validate patient exists
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    patient = result.scalars().first()
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # Validate file extension
    if not validate_file_extension(file.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Supported: {settings.ALLOWED_EXTENSIONS}"
        )
    
    # Validate document type
    try:
        doc_type = DocumentType(document_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid document type. Must be one of: {[t.value for t in DocumentType]}"
        )
    
    # Generate unique filename
    file_ext = Path(file.filename).suffix.lower()
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)
    
    # Read and validate file size
    try:
        content = await file.read()
        
        if len(content) > settings.MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Max size: {settings.MAX_UPLOAD_SIZE / 1024 / 1024}MB"
            )
        
        # Save file
        async with aiofiles.open(file_path, 'wb') as out_file:
            await out_file.write(content)
        
        logger.info(f"File saved: {unique_filename} ({len(content)} bytes)")
        
    except Exception as e:
        logger.error(f"File upload failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File upload failed"
        )
    
    # Create database record
    document = Document(
        filename=unique_filename,
        original_filename=file.filename,
        file_path=file_path,
        file_size=len(content),
        mime_type=file.content_type or "application/octet-stream",
        document_type=doc_type,
        patient_uuid=patient_id,
        uploaded_by=current_user.uuid,
        notes=notes
    )
    
    db.add(document)
    await db.commit()
    await db.refresh(document)
    
    # Generate embeddings for text-based documents (PDF, TXT)
    if file_ext in ['.pdf', '.txt']:
        try:
            # Extract text content (simplified - would use proper PDF parser)
            text_content = f"Document: {file.filename}, Type: {document_type}, Notes: {notes or ''}"
            
            # Generate embedding
            embedding_service = get_embedding_service()
            embedding = embedding_service.embed_text(text_content)
            
            # Store in vector database
            vector_db = get_vector_db()
            vector_db.add_vectors(
                embeddings=embedding,
                metadata=[{
                    "document_id": document.uuid,
                    "patient_id": patient_id,
                    "document_type": document_type,
                    "filename": file.filename,
                    "text": text_content
                }]
            )
            
            logger.info(f"Document embedded in vector database: {document.uuid}")
        except Exception as e:
            logger.warning(f"Failed to generate embeddings: {e}")
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.UPLOAD_DOCUMENT,
        target_type="document",
        target_uuid=document.uuid,
        patient_uuid=patient_id,
        action_details=f"Uploaded {document_type}: {file.filename}",
        request=request
    )
    
    logger.info(f"Document uploaded: {document.uuid} for patient {patient_id}")
    
    return document

@router.post("/bulk-upload", response_model=List[DocumentUploadResponse])
async def bulk_upload_documents(
    request: Request,
    files: List[UploadFile] = File(...),
    patient_id: str = Form(...),
    document_types: str = Form(...),  # Comma-separated
    current_user: User = Depends(require_roles("intake")),
    db: AsyncSession = Depends(get_db)
):
    """
    Bulk upload multiple documents for a patient (Intake Officer only).
    
    document_types should be comma-separated matching the number of files.
    """
    
    # Validate patient
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    patient = result.scalars().first()
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # Parse document types
    doc_types_list = [t.strip() for t in document_types.split(',')]
    
    if len(doc_types_list) != len(files):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Number of document types must match number of files"
        )
    
    uploaded_documents = []
    
    for file, doc_type_str in zip(files, doc_types_list):
        try:
            # Validate and upload each file
            if not validate_file_extension(file.filename):
                logger.warning(f"Skipping invalid file: {file.filename}")
                continue
            
            doc_type = DocumentType(doc_type_str)
            
            # Generate unique filename
            file_ext = Path(file.filename).suffix.lower()
            unique_filename = f"{uuid.uuid4()}{file_ext}"
            file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)
            
            # Read and save file
            content = await file.read()
            
            if len(content) > settings.MAX_UPLOAD_SIZE:
                logger.warning(f"File too large, skipping: {file.filename}")
                continue
            
            async with aiofiles.open(file_path, 'wb') as out_file:
                await out_file.write(content)
            
            # Create database record
            document = Document(
                filename=unique_filename,
                original_filename=file.filename,
                file_path=file_path,
                file_size=len(content),
                mime_type=file.content_type or "application/octet-stream",
                document_type=doc_type,
                patient_uuid=patient_id,
                uploaded_by=current_user.uuid
            )
            
            db.add(document)
            uploaded_documents.append(document)
            
        except Exception as e:
            logger.error(f"Error uploading {file.filename}: {e}")
            continue
    
    await db.commit()
    
    # Refresh all documents
    for doc in uploaded_documents:
        await db.refresh(doc)
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.UPLOAD_DOCUMENT,
        target_type="documents",
        patient_uuid=patient_id,
        action_details=f"Bulk uploaded {len(uploaded_documents)} documents",
        request=request
    )
    
    logger.info(f"Bulk upload: {len(uploaded_documents)} documents for patient {patient_id}")
    
    return uploaded_documents

@router.get("/", response_model=List[DocumentResponse])
async def list_documents(
    request: Request,
    patient_id: Optional[str] = Query(None),
    document_type: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("intake", "nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """
    List documents with optional filters.
    """
    
    query = select(Document)
    
    # If user is a patient, only show their documents
    if current_user.role.value == "patient":
        if not current_user.patient_record:
            return []
        query = query.where(Document.patient_uuid == current_user.patient_record.uuid)
    elif patient_id:
        query = query.where(Document.patient_uuid == patient_id)
    
    # Filter by document type
    if document_type:
        try:
            doc_type = DocumentType(document_type)
            query = query.where(Document.document_type == doc_type)
        except ValueError:
            pass
    
    query = query.order_by(Document.upload_date.desc())
    result = await db.execute(query)
    documents = result.scalars().all()
    
    return documents

@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    request: Request,
    current_user: User = Depends(require_roles("intake", "nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """Get document details."""
    
    result = await db.execute(select(Document).where(Document.uuid == document_id))
    document = result.scalars().first()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    # Check permissions
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != document.patient_uuid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.VIEW_PATIENT,
        target_type="document",
        target_uuid=document_id,
        patient_uuid=document.patient_uuid,
        request=request
    )
    
    return document

@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    request: Request,
    current_user: User = Depends(require_roles("intake", "physician")),
    db: AsyncSession = Depends(get_db)
):
    """Delete a document."""
    
    result = await db.execute(select(Document).where(Document.uuid == document_id))
    document = result.scalars().first()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    # Delete physical file
    try:
        if os.path.exists(document.file_path):
            os.remove(document.file_path)
            logger.info(f"Deleted physical file: {document.file_path}")
    except Exception as e:
        logger.error(f"Failed to delete physical file: {e}")
    
    # Delete from database
    patient_uuid = document.patient_uuid
    await db.delete(document)
    await db.commit()
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.OTHER,
        target_type="document",
        target_uuid=document_id,
        patient_uuid=patient_uuid,
        action_details="Deleted document",
        request=request
    )
    
    logger.info(f"Document deleted: {document_id}")

@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    request: Request,
    current_user: User = Depends(require_roles("intake", "nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """Download a document file."""
    from fastapi.responses import FileResponse
    
    result = await db.execute(select(Document).where(Document.uuid == document_id))
    document = result.scalars().first()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    # Check permissions
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != document.patient_uuid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    # Verify file exists
    if not os.path.exists(document.file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found on disk"
        )
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.DOWNLOAD_DOCUMENT,
        target_type="document",
        target_uuid=document_id,
        patient_uuid=document.patient_uuid,
        request=request
    )
    
    return FileResponse(
        path=document.file_path,
        filename=document.original_filename,
        media_type=document.mime_type
    )
