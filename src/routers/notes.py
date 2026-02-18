"""
Clinical notes router.
Fixed: now a standalone file (was concatenated with labs.py previously).
Admin included in all require_roles.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
import logging

from src.database.postgres import get_db
from src.models.user import User, UserRole
from src.models.patient import Patient
from src.models.clinical import ClinicalNote
from src.schemas.clinical import ClinicalNoteCreate, ClinicalNoteResponse
from src.utils.auth import require_roles, log_audit
from src.utils.validators import validate_uuid
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
    current_user: User = Depends(require_roles("physician", "nurse", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Create a clinical note. Auto-generates embeddings for semantic retrieval."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    if not result.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    clinical_note = ClinicalNote(
        patient_uuid=patient_id,
        author_uuid=current_user.uuid,
        title=note_data.title,
        content=note_data.content,
        note_type=note_data.note_type,
        note_date=note_data.note_date,
    )
    db.add(clinical_note)
    await db.commit()
    await db.refresh(clinical_note)

    # Embed note for RAG retrieval
    try:
        text_content = f"{note_data.title}. {note_data.content}"
        embedding_service = get_embedding_service()
        embedding = embedding_service.embed_text(text_content)
        get_vector_db().add_vectors(
            embeddings=embedding,
            metadata=[{
                "clinical_note_id": clinical_note.uuid,
                "patient_id": patient_id,
                "note_type": note_data.note_type or "general",
                "title": note_data.title,
                "text": text_content,
            }],
        )
    except Exception as e:
        logger.warning(f"Failed to embed clinical note {clinical_note.uuid}: {e}")

    await log_audit(
        db=db, user=current_user, action=AuditAction.ADD_CLINICAL_NOTE,
        target_type="clinical_note", target_uuid=clinical_note.uuid,
        patient_uuid=patient_id, action_details=f"Created note: {note_data.title}", request=request,
    )
    return clinical_note


@router.get("/", response_model=List[ClinicalNoteResponse])
async def list_clinical_notes(
    request: Request,
    patient_id: Optional[str] = Query(None),
    note_type: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "admin", "compliance", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """List clinical notes with optional filters."""
    query = select(ClinicalNote)
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record:
            return []
        query = query.where(ClinicalNote.patient_uuid == current_user.patient_record.uuid)
    elif patient_id:
        query = query.where(ClinicalNote.patient_uuid == patient_id)
    if note_type:
        query = query.where(ClinicalNote.note_type == note_type)
    query = query.order_by(ClinicalNote.note_date.desc())
    return (await db.execute(query)).scalars().all()


@router.get("/patient/{patient_id}", response_model=List[ClinicalNoteResponse])
async def get_patient_notes(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "admin", "compliance", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get all clinical notes for a patient."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    notes = (await db.execute(
        select(ClinicalNote).where(ClinicalNote.patient_uuid == patient_id).order_by(ClinicalNote.note_date.desc())
    )).scalars().all()

    await log_audit(
        db=db, user=current_user, action=AuditAction.VIEW_CLINICAL_NOTES,
        target_type="clinical_notes", patient_uuid=patient_id, request=request,
    )
    return notes


@router.get("/{note_id}", response_model=ClinicalNoteResponse)
async def get_clinical_note(
    note_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "admin", "compliance", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific clinical note."""
    note = (await db.execute(select(ClinicalNote).where(ClinicalNote.uuid == note_id))).scalars().first()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clinical note not found")
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != note.patient_uuid:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    await log_audit(
        db=db, user=current_user, action=AuditAction.VIEW_CLINICAL_NOTES,
        target_type="clinical_note", target_uuid=note_id,
        patient_uuid=note.patient_uuid, request=request,
    )
    return note


@router.put("/{note_id}", response_model=ClinicalNoteResponse)
async def update_clinical_note(
    note_id: str,
    note_update: ClinicalNoteCreate,
    request: Request,
    current_user: User = Depends(require_roles("physician", "nurse", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Update a clinical note (author, physician, or admin)."""
    note = (await db.execute(select(ClinicalNote).where(ClinicalNote.uuid == note_id))).scalars().first()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clinical note not found")

    # Non-physician nurses can only edit their own notes
    if current_user.role == UserRole.NURSE and note.author_uuid != current_user.uuid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nurses can only edit their own notes.",
        )

    note.title = note_update.title
    note.content = note_update.content
    note.note_type = note_update.note_type
    note.note_date = note_update.note_date
    await db.commit()
    await db.refresh(note)

    # Re-embed updated content
    try:
        text_content = f"{note.title}. {note.content}"
        embedding = get_embedding_service().embed_text(text_content)
        get_vector_db().add_vectors(
            embeddings=embedding,
            metadata=[{
                "clinical_note_id": note.uuid,
                "patient_id": note.patient_uuid,
                "note_type": note.note_type or "general",
                "title": note.title,
                "text": text_content,
            }],
        )
    except Exception as e:
        logger.warning(f"Failed to re-embed note {note_id}: {e}")

    await log_audit(
        db=db, user=current_user, action=AuditAction.ADD_CLINICAL_NOTE,
        target_type="clinical_note", target_uuid=note_id,
        patient_uuid=note.patient_uuid, action_details="Updated clinical note", request=request,
    )
    return note


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_clinical_note(
    note_id: str,
    request: Request,
    current_user: User = Depends(require_roles("physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a clinical note (physician or admin only)."""
    note = (await db.execute(select(ClinicalNote).where(ClinicalNote.uuid == note_id))).scalars().first()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clinical note not found")
    patient_uuid = note.patient_uuid
    await db.delete(note)
    await db.commit()
    await log_audit(
        db=db, user=current_user, action=AuditAction.OTHER,
        target_type="clinical_note", target_uuid=note_id,
        patient_uuid=patient_uuid, action_details="Deleted clinical note", request=request,
    )


@router.post("/{note_id}/summarize")
async def summarize_clinical_note(
    note_id: str,
    request: Request,
    max_length: int = Query(200, ge=50, le=500),
    current_user: User = Depends(require_roles("physician", "nurse", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Generate an AI summary of a clinical note using Groq."""
    note = (await db.execute(select(ClinicalNote).where(ClinicalNote.uuid == note_id))).scalars().first()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clinical note not found")
    try:
        summary = await get_groq_service().summarize_clinical_note(
            clinical_note=note.content, max_length=max_length
        )
        await log_audit(
            db=db, user=current_user, action=AuditAction.VIEW_CLINICAL_NOTES,
            target_type="clinical_note", target_uuid=note_id,
            patient_uuid=note.patient_uuid, action_details="Generated AI summary", request=request,
        )
        return {
            "note_id": note_id,
            "original_title": note.title,
            "original_length": len(note.content.split()),
            "summary": summary,
            "summary_length": len(summary.split()),
            "model": "mixtral-8x7b-32768 (Groq)",
        }
    except Exception as e:
        logger.error(f"Note summarization failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# ── Patient portal ────────────────────────────────────────────────────────────

@router.post("/my-notes", response_model=ClinicalNoteResponse, status_code=status.HTTP_201_CREATED)
async def create_my_clinical_note(
    note_data: ClinicalNoteCreate,
    request: Request,
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Patient adds a note (symptoms, concerns) to their own record."""
    if not current_user.patient_record:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No patient record linked.")
    patient_id = current_user.patient_record.uuid

    clinical_note = ClinicalNote(
        patient_uuid=patient_id,
        author_uuid=current_user.uuid,
        title=note_data.title,
        content=note_data.content,
        note_type=note_data.note_type or "patient_note",
        note_date=note_data.note_date,
    )
    db.add(clinical_note)
    await db.commit()
    await db.refresh(clinical_note)

    try:
        text_content = f"{note_data.title}. {note_data.content}"
        embedding = get_embedding_service().embed_text(text_content)
        get_vector_db().add_vectors(
            embeddings=embedding,
            metadata=[{
                "clinical_note_id": clinical_note.uuid,
                "patient_id": patient_id,
                "note_type": "patient_note",
                "title": note_data.title,
                "text": text_content,
            }],
        )
    except Exception as e:
        logger.warning(f"Failed to embed patient note {clinical_note.uuid}: {e}")

    await log_audit(
        db=db, user=current_user, action=AuditAction.ADD_CLINICAL_NOTE,
        target_type="clinical_note", target_uuid=clinical_note.uuid,
        patient_uuid=patient_id, action_details=f"Patient added note: {note_data.title}", request=request,
    )
    return clinical_note


@router.get("/my-notes", response_model=List[ClinicalNoteResponse])
async def get_my_clinical_notes(
    request: Request,
    note_type: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get all clinical notes from the patient's own record."""
    if not current_user.patient_record:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No patient record linked.")
    query = select(ClinicalNote).where(ClinicalNote.patient_uuid == current_user.patient_record.uuid)
    if note_type:
        query = query.where(ClinicalNote.note_type == note_type)
    query = query.order_by(ClinicalNote.note_date.desc())
    return (await db.execute(query)).scalars().all()