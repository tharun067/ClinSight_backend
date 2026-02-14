"""
Imaging studies router for radiology workflow.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, List
import logging
from datetime import datetime

from src.database.postgres import get_db
from src.models.user import User
from src.models.patient import Patient
from src.models.imaging_study import ImagingStudy, ImagingModality, ImagingStatus
from src.schemas.imaging import ImagingStudyCreate, ImagingStudyUpdate, ImagingStudyResponse
from src.utils.auth import get_current_active_user, require_roles, log_audit
from src.models.audit_log import AuditAction
from src.services.embedding import get_embedding_service
from src.database.vector_db import get_vector_db

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/", response_model=ImagingStudyResponse, status_code=status.HTTP_201_CREATED)
async def create_imaging_study(
    study_data: ImagingStudyCreate,
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician")),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new imaging study.
    """
    
    # Validate patient
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    patient = result.scalars().first()
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # Validate modality
    try:
        modality = ImagingModality(study_data.modality)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid modality. Must be one of: {[m.value for m in ImagingModality]}"
        )
    
    # Create imaging study
    imaging_study = ImagingStudy(
        patient_uuid=patient_id,
        study_date=study_data.study_date,
        modality=modality,
        body_part=study_data.body_part,
        description=study_data.description,
        status=ImagingStatus.PENDING
    )
    
    db.add(imaging_study)
    await db.commit()
    await db.refresh(imaging_study)
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.OTHER,
        target_type="imaging_study",
        target_uuid=imaging_study.uuid,
        patient_uuid=patient_id,
        action_details=f"Created {modality.value} study for {study_data.body_part}",
        request=request
    )
    
    logger.info(f"Imaging study created: {imaging_study.uuid}")
    
    return imaging_study

@router.get("/", response_model=List[ImagingStudyResponse])
async def list_imaging_studies(
    request: Request,
    patient_id: Optional[str] = Query(None),
    modality: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """
    List imaging studies with optional filters.
    """
    
    query = select(ImagingStudy)
    
    # Patient role restriction
    if current_user.role.value == "patient":
        if not current_user.patient_record:
            return []
        query = query.where(ImagingStudy.patient_uuid == current_user.patient_record.uuid)
    elif patient_id:
        query = query.where(ImagingStudy.patient_uuid == patient_id)
    
    # Filter by modality
    if modality:
        try:
            mod = ImagingModality(modality)
            query = query.where(ImagingStudy.modality == mod)
        except ValueError:
            pass
    
    # Filter by status
    if status_filter:
        try:
            stat = ImagingStatus(status_filter)
            query = query.where(ImagingStudy.status == stat)
        except ValueError:
            pass
    
    query = query.order_by(ImagingStudy.study_date.desc())
    result = await db.execute(query)
    studies = result.scalars().all()
    
    return studies

@router.get("/patient/{patient_id}", response_model=List[ImagingStudyResponse])
async def get_patient_imaging_studies(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all imaging studies for a specific patient.
    """
    
    # Check patient access
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    # Get studies
    result = await db.execute(
        select(ImagingStudy)
        .where(ImagingStudy.patient_uuid == patient_id)
        .order_by(ImagingStudy.study_date.desc())
    )
    studies = result.scalars().all()
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.VIEW_IMAGING,
        target_type="imaging_studies",
        patient_uuid=patient_id,
        request=request
    )
    
    return studies

@router.get("/{study_id}", response_model=ImagingStudyResponse)
async def get_imaging_study(
    study_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """Get imaging study details."""
    
    result = await db.execute(select(ImagingStudy).where(ImagingStudy.uuid == study_id))
    study = result.scalars().first()
    
    if not study:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Imaging study not found"
        )
    
    # Check permissions
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != study.patient_uuid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.VIEW_IMAGING,
        target_type="imaging_study",
        target_uuid=study_id,
        patient_uuid=study.patient_uuid,
        request=request
    )
    
    return study

@router.put("/{study_id}", response_model=ImagingStudyResponse)
async def update_imaging_study(
    study_id: str,
    study_update: ImagingStudyUpdate,
    request: Request,
    current_user: User = Depends(require_roles("radiologist", "physician")),
    db: AsyncSession = Depends(get_db)
):
    """
    Update imaging study (general update).
    """
    
    result = await db.execute(select(ImagingStudy).where(ImagingStudy.uuid == study_id))
    study = result.scalars().first()
    
    if not study:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Imaging study not found"
        )
    
    # Update fields
    update_data = study_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(study, key, value)
    
    await db.commit()
    await db.refresh(study)
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.OTHER,
        target_type="imaging_study",
        target_uuid=study_id,
        patient_uuid=study.patient_uuid,
        action_details="Updated imaging study",
        request=request
    )
    
    return study

@router.put("/{study_id}/interpret", response_model=ImagingStudyResponse)
async def add_interpretation(
    study_id: str,
    interpretation: ImagingStudyUpdate,
    request: Request,
    current_user: User = Depends(require_roles("radiologist")),
    db: AsyncSession = Depends(get_db)
):
    """
    Add radiologist interpretation to imaging study.
    
    This endpoint is specifically for radiologists to add their findings and impression.
    """
    
    result = await db.execute(select(ImagingStudy).where(ImagingStudy.uuid == study_id))
    study = result.scalars().first()
    
    if not study:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Imaging study not found"
        )
    
    # Update interpretation fields
    if interpretation.findings:
        study.findings = interpretation.findings
    if interpretation.impression:
        study.impression = interpretation.impression
    
    # Update status and metadata
    study.status = ImagingStatus(interpretation.status) if interpretation.status else ImagingStatus.COMPLETE
    study.interpreted_by = current_user.uuid
    study.interpretation_date = datetime.now()
    
    await db.commit()
    await db.refresh(study)
    
    # Generate embeddings for findings (for retrieval)
    if study.findings:
        try:
            embedding_service = get_embedding_service()
            text_content = f"{study.modality.value} of {study.body_part}. Findings: {study.findings}. Impression: {study.impression or ''}"
            embedding = embedding_service.embed_text(text_content)
            
            # Store in vector database
            vector_db = get_vector_db()
            vector_db.add_vectors(
                embeddings=embedding,
                metadata=[{
                    "imaging_study_id": study.uuid,
                    "patient_id": study.patient_uuid,
                    "modality": study.modality.value,
                    "body_part": study.body_part,
                    "text": text_content
                }]
            )
            
            logger.info(f"Imaging findings embedded: {study.uuid}")
        except Exception as e:
            logger.warning(f"Failed to embed findings: {e}")
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.ADD_IMAGING_NOTE,
        target_type="imaging_study",
        target_uuid=study_id,
        patient_uuid=study.patient_uuid,
        action_details=f"Added interpretation for {study.modality.value}",
        request=request
    )
    
    logger.info(f"Radiologist interpretation added to study {study_id} by {current_user.username}")
    
    return study

@router.delete("/{study_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_imaging_study(
    study_id: str,
    request: Request,
    current_user: User = Depends(require_roles("physician", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """Delete an imaging study."""
    
    result = await db.execute(select(ImagingStudy).where(ImagingStudy.uuid == study_id))
    study = result.scalars().first()
    
    if not study:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Imaging study not found"
        )
    
    patient_uuid = study.patient_uuid
    await db.delete(study)
    await db.commit()
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.OTHER,
        target_type="imaging_study",
        target_uuid=study_id,
        patient_uuid=patient_uuid,
        action_details="Deleted imaging study",
        request=request
    )
    
    logger.info(f"Imaging study deleted: {study_id}")
