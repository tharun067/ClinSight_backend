"""
Clinical notes router for documentation.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
import logging

from src.database.postgres import get_db
from src.models.user import User
from src.models.patient import Patient
from src.models.clinical import ClinicalNote
from src.schemas.clinical import ClinicalNoteCreate, ClinicalNoteResponse
from src.utils.auth import get_current_active_user, require_roles, log_audit
from src.models.audit_log import AuditAction
from src.services.embedding import get_embedding_service
from src.services.groq import get_groq_service
from src.database.vector_db import get_vector_db

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/", response_model=ClinicalNoteResponse, status_code=status.HTTP_201_CREATED)
async def create_clinical_note(
    note_data: ClinicalNoteCreate,
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("physician", "nurse")),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a clinical note for a patient.
    
    Automatically generates embeddings for semantic search.
    """
    
    # Validate patient
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    patient = result.scalars().first()
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # Create clinical note
    clinical_note = ClinicalNote(
        patient_uuid=patient_id,
        author_uuid=current_user.uuid,
        title=note_data.title,
        content=note_data.content,
        note_type=note_data.note_type,
        note_date=note_data.note_date
    )
    
    db.add(clinical_note)
    await db.commit()
    await db.refresh(clinical_note)
    
    # Generate embeddings for search/retrieval
    try:
        embedding_service = get_embedding_service()
        
        # Combine title and content for embedding
        text_content = f"{note_data.title}. {note_data.content}"
        embedding = embedding_service.embed_text(text_content)
        
        # Store in vector database
        vector_db = get_vector_db()
        vector_db.add_vectors(
            embeddings=embedding,
            metadata=[{
                "clinical_note_id": clinical_note.uuid,
                "patient_id": patient_id,
                "note_type": note_data.note_type or "general",
                "title": note_data.title,
                "text": text_content
            }]
        )
        
        logger.info(f"Clinical note embedded: {clinical_note.uuid}")
    except Exception as e:
        logger.warning(f"Failed to embed clinical note: {e}")
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.ADD_CLINICAL_NOTE,
        target_type="clinical_note",
        target_uuid=clinical_note.uuid,
        patient_uuid=patient_id,
        action_details=f"Created note: {note_data.title}",
        request=request
    )
    
    logger.info(f"Clinical note created: {clinical_note.uuid}")
    
    return clinical_note

@router.get("/", response_model=List[ClinicalNoteResponse])
async def list_clinical_notes(
    request: Request,
    patient_id: Optional[str] = Query(None),
    note_type: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """List clinical notes with optional filters."""
    
    query = select(ClinicalNote)
    
    # Patient role restriction
    if current_user.role.value == "patient":
        if not current_user.patient_record:
            return []
        query = query.where(ClinicalNote.patient_uuid == current_user.patient_record.uuid)
    elif patient_id:
        query = query.where(ClinicalNote.patient_uuid == patient_id)
    
    # Filter by note type
    if note_type:
        query = query.where(ClinicalNote.note_type == note_type)
    
    query = query.order_by(ClinicalNote.note_date.desc())
    result = await db.execute(query)
    notes = result.scalars().all()
    
    return notes

@router.get("/patient/{patient_id}", response_model=List[ClinicalNoteResponse])
async def get_patient_notes(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """Get all clinical notes for a specific patient."""
    
    # Check patient access
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    result = await db.execute(
        select(ClinicalNote)
        .where(ClinicalNote.patient_uuid == patient_id)
        .order_by(ClinicalNote.note_date.desc())
    )
    notes = result.scalars().all()
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.VIEW_CLINICAL_NOTES,
        target_type="clinical_notes",
        patient_uuid=patient_id,
        request=request
    )
    
    return notes

@router.get("/{note_id}", response_model=ClinicalNoteResponse)
async def get_clinical_note(
    note_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """Get specific clinical note."""
    
    result = await db.execute(select(ClinicalNote).where(ClinicalNote.uuid == note_id))
    note = result.scalars().first()
    
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clinical note not found"
        )
    
    # Check permissions
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != note.patient_uuid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.VIEW_CLINICAL_NOTES,
        target_type="clinical_note",
        target_uuid=note_id,
        patient_uuid=note.patient_uuid,
        request=request
    )
    
    return note

@router.put("/{note_id}", response_model=ClinicalNoteResponse)
async def update_clinical_note(
    note_id: str,
    note_update: ClinicalNoteCreate,
    request: Request,
    current_user: User = Depends(require_roles("physician", "nurse")),
    db: AsyncSession = Depends(get_db)
):
    """Update a clinical note."""
    
    result = await db.execute(select(ClinicalNote).where(ClinicalNote.uuid == note_id))
    note = result.scalars().first()
    
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clinical note not found"
        )
    
    # Check if user is the author
    if note.author_uuid != current_user.uuid and current_user.role.value != "physician":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the author or a physician can update this note"
        )
    
    # Update fields
    note.title = note_update.title
    note.content = note_update.content
    note.note_type = note_update.note_type
    note.note_date = note_update.note_date
    
    await db.commit()
    await db.refresh(note)
    
    # Re-generate embeddings
    try:
        embedding_service = get_embedding_service()
        text_content = f"{note.title}. {note.content}"
        embedding = embedding_service.embed_text(text_content)
        
        # Update in vector database (simplified - would need to delete old first)
        vector_db = get_vector_db()
        vector_db.add_vectors(
            embeddings=embedding,
            metadata=[{
                "clinical_note_id": note.uuid,
                "patient_id": note.patient_uuid,
                "note_type": note.note_type or "general",
                "title": note.title,
                "text": text_content
            }]
        )
    except Exception as e:
        logger.warning(f"Failed to re-embed note: {e}")
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.ADD_CLINICAL_NOTE,
        target_type="clinical_note",
        target_uuid=note_id,
        patient_uuid=note.patient_uuid,
        action_details="Updated clinical note",
        request=request
    )
    
    return note

@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_clinical_note(
    note_id: str,
    request: Request,
    current_user: User = Depends(require_roles("physician")),
    db: AsyncSession = Depends(get_db)
):
    """Delete a clinical note (physicians only)."""
    
    result = await db.execute(select(ClinicalNote).where(ClinicalNote.uuid == note_id))
    note = result.scalars().first()
    
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clinical note not found"
        )
    
    patient_uuid = note.patient_uuid
    await db.delete(note)
    await db.commit()
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.OTHER,
        target_type="clinical_note",
        target_uuid=note_id,
        patient_uuid=patient_uuid,
        action_details="Deleted clinical note",
        request=request
    )
    
    logger.info(f"Clinical note deleted: {note_id}")

@router.post("/{note_id}/summarize")
async def summarize_clinical_note(
    note_id: str,
    request: Request,
    max_length: int = Query(200, ge=50, le=500),
    current_user: User = Depends(require_roles("physician", "nurse")),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate AI summary of a clinical note using Groq.
    
    Fast and cost-effective summarization.
    """
    
    # Get the note
    result = await db.execute(select(ClinicalNote).where(ClinicalNote.uuid == note_id))
    note = result.scalars().first()
    
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clinical note not found"
        )
    
    try:
        # Use Groq for fast summarization
        groq_service = get_groq_service()
        summary = await groq_service.summarize_clinical_note(
            clinical_note=note.content,
            max_length=max_length
        )
        
        # Log audit
        await log_audit(
            db=db,
            user=current_user,
            action=AuditAction.VIEW_CLINICAL_NOTES,
            target_type="clinical_note",
            target_uuid=note_id,
            patient_uuid=note.patient_uuid,
            action_details="Generated AI summary",
            request=request
        )
        
        return {
            "note_id": note_id,
            "original_title": note.title,
            "original_length": len(note.content.split()),
            "summary": summary,
            "summary_length": len(summary.split()),
            "model": "mixtral-8x7b-32768 (Groq)"
        }
        
    except Exception as e:
        logger.error(f"Note summarization failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Summarization failed: {str(e)}"
        )
