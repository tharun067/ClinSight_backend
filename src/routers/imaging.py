"""
Imaging studies router.

Role permissions:
- nurse:     create imaging studies, view studies
- physician: create/update/interpret/delete imaging studies, view all
- admin:     full access
- patient:   view own imaging studies only
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
import logging
from datetime import datetime

from src.database.postgres import get_db
from src.models.user import User, UserRole
from src.models.patient import Patient
from src.models.imaging_study import ImagingStudy, ImagingModality, ImagingStatus
from src.schemas.imaging import ImagingStudyCreate, ImagingStudyUpdate, ImagingStudyResponse
from src.utils.auth import require_roles, log_audit
from src.utils.validators import validate_uuid
from src.models.audit_log import AuditAction
from src.services.embedding import get_embedding_service
from src.database.vector_db import get_vector_db

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/", response_model=ImagingStudyResponse, status_code=status.HTTP_201_CREATED)
async def create_imaging_study(
    study_data: ImagingStudyCreate, patient_id: str, request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new imaging study record (Nurse, Physician, Admin)."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    if not (await db.execute(select(Patient).where(Patient.uuid == patient_id))).scalars().first():
        raise HTTPException(status_code=404, detail="Patient not found")
    try:
        modality = ImagingModality(study_data.modality)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid modality. Options: {[m.value for m in ImagingModality]}")
    study = ImagingStudy(
        patient_uuid=patient_id, study_date=study_data.study_date, modality=modality,
        body_part=study_data.body_part, description=study_data.description,
        status=ImagingStatus.PENDING,
    )
    db.add(study)
    await db.commit()
    await db.refresh(study)
    await log_audit(
        db=db, user=current_user, action=AuditAction.OTHER,
        target_type="imaging_study", target_uuid=study.uuid, patient_uuid=patient_id,
        action_details=f"Created {modality.value} study for {study_data.body_part}", request=request,
    )
    return study


@router.get("/", response_model=List[ImagingStudyResponse])
async def list_imaging_studies(
    request: Request,
    patient_id: Optional[str] = Query(None),
    modality: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("nurse", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """List imaging studies. Patients only see their own."""
    query = select(ImagingStudy)
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record:
            return []
        query = query.where(ImagingStudy.patient_uuid == current_user.patient_record.uuid)
    elif patient_id:
        query = query.where(ImagingStudy.patient_uuid == patient_id)
    if modality:
        try:
            query = query.where(ImagingStudy.modality == ImagingModality(modality))
        except ValueError:
            pass
    if status_filter:
        try:
            query = query.where(ImagingStudy.status == ImagingStatus(status_filter))
        except ValueError:
            pass
    return (await db.execute(query.order_by(ImagingStudy.study_date.desc()))).scalars().all()


# ── Patient portal — MUST be before /{study_id} to avoid path conflict ────────

@router.get("/my-imaging", response_model=List[ImagingStudyResponse])
async def get_my_imaging_studies(
    request: Request,
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Patient: get own imaging studies."""
    if not current_user.patient_record:
        raise HTTPException(status_code=403, detail="No patient record linked.")
    studies = (await db.execute(
        select(ImagingStudy)
        .where(ImagingStudy.patient_uuid == current_user.patient_record.uuid)
        .order_by(ImagingStudy.study_date.desc())
    )).scalars().all()
    return studies


@router.get("/patient/{patient_id}", response_model=List[ImagingStudyResponse])
async def get_patient_imaging_studies(
    patient_id: str, request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get all imaging studies for a specific patient."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(status_code=403, detail="Access denied")
    studies = (await db.execute(
        select(ImagingStudy).where(ImagingStudy.patient_uuid == patient_id).order_by(ImagingStudy.study_date.desc())
    )).scalars().all()
    await log_audit(
        db=db, user=current_user, action=AuditAction.VIEW_IMAGING,
        target_type="imaging_studies", patient_uuid=patient_id, request=request,
    )
    return studies


@router.get("/{study_id}", response_model=ImagingStudyResponse)
async def get_imaging_study(
    study_id: str, request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get imaging study details."""
    study = (await db.execute(select(ImagingStudy).where(ImagingStudy.uuid == study_id))).scalars().first()
    if not study:
        raise HTTPException(status_code=404, detail="Imaging study not found")
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != study.patient_uuid:
            raise HTTPException(status_code=403, detail="Access denied")
    await log_audit(
        db=db, user=current_user, action=AuditAction.VIEW_IMAGING,
        target_type="imaging_study", target_uuid=study_id, patient_uuid=study.patient_uuid, request=request,
    )
    return study


@router.put("/{study_id}/interpret", response_model=ImagingStudyResponse)
async def add_interpretation(
    study_id: str, interpretation: ImagingStudyUpdate, request: Request,
    current_user: User = Depends(require_roles("physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Add interpretation/findings to an imaging study (Physician, Admin). Auto-embeds findings for RAG."""
    study = (await db.execute(select(ImagingStudy).where(ImagingStudy.uuid == study_id))).scalars().first()
    if not study:
        raise HTTPException(status_code=404, detail="Imaging study not found")
    if interpretation.findings:
        study.findings = interpretation.findings
    if interpretation.impression:
        study.impression = interpretation.impression
    try:
        study.status = ImagingStatus(interpretation.status) if interpretation.status else ImagingStatus.COMPLETE
    except ValueError:
        study.status = ImagingStatus.COMPLETE
    study.interpreted_by = current_user.uuid
    study.interpretation_date = datetime.now()
    await db.commit()
    await db.refresh(study)
    if study.findings:
        try:
            text_content = (f"{study.modality.value} of {study.body_part}. "
                            f"Findings: {study.findings}. Impression: {study.impression or ''}")
            embedding = get_embedding_service().embed_text(text_content)
            get_vector_db().add_vectors(embeddings=embedding, metadata=[{
                "imaging_study_id": study.uuid, "patient_id": study.patient_uuid,
                "modality": study.modality.value, "body_part": study.body_part, "text": text_content,
            }])
        except Exception as e:
            logger.warning(f"Failed to embed imaging findings {study_id}: {e}")
    await log_audit(
        db=db, user=current_user, action=AuditAction.ADD_IMAGING_NOTE,
        target_type="imaging_study", target_uuid=study_id, patient_uuid=study.patient_uuid,
        action_details=f"Added interpretation for {study.modality.value}", request=request,
    )
    return study


@router.put("/{study_id}", response_model=ImagingStudyResponse)
async def update_imaging_study(
    study_id: str, study_update: ImagingStudyUpdate, request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Update an imaging study (Nurse, Physician, Admin)."""
    study = (await db.execute(select(ImagingStudy).where(ImagingStudy.uuid == study_id))).scalars().first()
    if not study:
        raise HTTPException(status_code=404, detail="Imaging study not found")
    for key, value in study_update.model_dump(exclude_unset=True).items():
        setattr(study, key, value)
    await db.commit()
    await db.refresh(study)
    return study


@router.delete("/{study_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_imaging_study(
    study_id: str, request: Request,
    current_user: User = Depends(require_roles("physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Delete an imaging study (Physician or Admin only)."""
    study = (await db.execute(select(ImagingStudy).where(ImagingStudy.uuid == study_id))).scalars().first()
    if not study:
        raise HTTPException(status_code=404, detail="Imaging study not found")
    patient_uuid = study.patient_uuid
    await db.delete(study)
    await db.commit()
    await log_audit(
        db=db, user=current_user, action=AuditAction.OTHER,
        target_type="imaging_study", target_uuid=study_id,
        patient_uuid=patient_uuid, action_details="Deleted imaging study", request=request,
    )
