"""
Lab results and vital signs router.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
import logging

from src.database.postgres import get_db
from src.models.user import User
from src.models.patient import Patient
from src.models.lab_vital import LabResult, VitalSign
from src.schemas.clinical import (
    LabResultCreate, LabResultResponse,
    VitalSignCreate, VitalSignResponse
)
from src.utils.auth import get_current_active_user, require_roles, log_audit
from src.models.audit_log import AuditAction

router = APIRouter()
logger = logging.getLogger(__name__)

# ==================== LAB RESULTS ====================

@router.post("/labs", response_model=LabResultResponse, status_code=status.HTTP_201_CREATED)
async def create_lab_result(
    lab_data: LabResultCreate,
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician")),
    db: AsyncSession = Depends(get_db)
):
    """
    Add a lab result for a patient.
    
    Automatically calculates abnormal flag based on reference ranges.
    """
    
    # Validate patient
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    patient = result.scalars().first()
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # Calculate abnormal flag
    is_abnormal = None
    if lab_data.reference_range_high and lab_data.reference_range_low:
        if lab_data.test_value > lab_data.reference_range_high:
            is_abnormal = "High"
        elif lab_data.test_value < lab_data.reference_range_low:
            is_abnormal = "Low"
        else:
            is_abnormal = "Normal"
    elif lab_data.reference_range_high:
        if lab_data.test_value > lab_data.reference_range_high:
            is_abnormal = "High"
    elif lab_data.reference_range_low:
        if lab_data.test_value < lab_data.reference_range_low:
            is_abnormal = "Low"
    
    # Override with provided flag if exists
    if lab_data.is_abnormal:
        is_abnormal = lab_data.is_abnormal
    
    # Create lab result
    lab_result = LabResult(
        patient_uuid=patient_id,
        test_name=lab_data.test_name,
        test_value=lab_data.test_value,
        unit=lab_data.unit,
        reference_range_low=lab_data.reference_range_low,
        reference_range_high=lab_data.reference_range_high,
        is_abnormal=is_abnormal,
        test_date=lab_data.test_date,
        notes=lab_data.notes,
        ordered_by=current_user.uuid
    )
    
    db.add(lab_result)
    await db.commit()
    await db.refresh(lab_result)
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.ENTER_LABS,
        target_type="lab_result",
        target_uuid=lab_result.uuid,
        patient_uuid=patient_id,
        action_details=f"Added {lab_data.test_name}: {lab_data.test_value} {lab_data.unit}",
        request=request
    )
    
    logger.info(f"Lab result created: {lab_result.uuid} - {lab_data.test_name}")
    
    return lab_result

@router.get("/labs", response_model=List[LabResultResponse])
async def list_lab_results(
    request: Request,
    patient_id: Optional[str] = Query(None),
    test_name: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """List lab results with optional filters."""
    
    query = select(LabResult)
    
    # Patient role restriction
    if current_user.role.value == "patient":
        if not current_user.patient_record:
            return []
        query = query.where(LabResult.patient_uuid == current_user.patient_record.uuid)
    elif patient_id:
        query = query.where(LabResult.patient_uuid == patient_id)
    
    # Filter by test name
    if test_name:
        query = query.where(LabResult.test_name.ilike(f"%{test_name}%"))
    
    query = query.order_by(LabResult.test_date.desc())
    result = await db.execute(query)
    labs = result.scalars().all()
    
    return labs

@router.get("/labs/patient/{patient_id}", response_model=List[LabResultResponse])
async def get_patient_labs(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """Get all lab results for a specific patient."""
    
    # Check patient access
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    result = await db.execute(
        select(LabResult)
        .where(LabResult.patient_uuid == patient_id)
        .order_by(LabResult.test_date.desc())
    )
    labs = result.scalars().all()
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.VIEW_LABS,
        target_type="lab_results",
        patient_uuid=patient_id,
        request=request
    )
    
    return labs

@router.get("/labs/{lab_id}", response_model=LabResultResponse)
async def get_lab_result(
    lab_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """Get specific lab result."""
    
    result = await db.execute(select(LabResult).where(LabResult.uuid == lab_id))
    lab = result.scalars().first()
    
    if not lab:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lab result not found"
        )
    
    # Check permissions
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != lab.patient_uuid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    return lab

@router.put("/labs/{lab_id}", response_model=LabResultResponse)
async def update_lab_result(
    lab_id: str,
    lab_update: LabResultCreate,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician")),
    db: AsyncSession = Depends(get_db)
):
    """Update a lab result."""
    
    result = await db.execute(select(LabResult).where(LabResult.uuid == lab_id))
    lab = result.scalars().first()
    
    if not lab:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lab result not found"
        )
    
    # Update fields
    lab.test_name = lab_update.test_name
    lab.test_value = lab_update.test_value
    lab.unit = lab_update.unit
    lab.reference_range_low = lab_update.reference_range_low
    lab.reference_range_high = lab_update.reference_range_high
    lab.test_date = lab_update.test_date
    lab.notes = lab_update.notes
    
    # Recalculate abnormal flag
    is_abnormal = None
    if lab_update.reference_range_high and lab_update.reference_range_low:
        if lab_update.test_value > lab_update.reference_range_high:
            is_abnormal = "High"
        elif lab_update.test_value < lab_update.reference_range_low:
            is_abnormal = "Low"
        else:
            is_abnormal = "Normal"
    
    lab.is_abnormal = lab_update.is_abnormal or is_abnormal
    
    await db.commit()
    await db.refresh(lab)
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.ENTER_LABS,
        target_type="lab_result",
        target_uuid=lab_id,
        patient_uuid=lab.patient_uuid,
        action_details=f"Updated {lab.test_name}",
        request=request
    )
    
    return lab

@router.delete("/labs/{lab_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lab_result(
    lab_id: str,
    request: Request,
    current_user: User = Depends(require_roles("physician")),
    db: AsyncSession = Depends(get_db)
):
    """Delete a lab result."""
    
    result = await db.execute(select(LabResult).where(LabResult.uuid == lab_id))
    lab = result.scalars().first()
    
    if not lab:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lab result not found"
        )
    
    patient_uuid = lab.patient_uuid
    await db.delete(lab)
    await db.commit()
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.OTHER,
        target_type="lab_result",
        target_uuid=lab_id,
        patient_uuid=patient_uuid,
        action_details="Deleted lab result",
        request=request
    )
    
    logger.info(f"Lab result deleted: {lab_id}")

# ==================== VITAL SIGNS ====================

@router.post("/vitals", response_model=VitalSignResponse, status_code=status.HTTP_201_CREATED)
async def create_vital_signs(
    vitals_data: VitalSignCreate,
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician")),
    db: AsyncSession = Depends(get_db)
):
    """
    Add vital signs for a patient.
    
    Automatically calculates BMI if height and weight are provided.
    """
    
    # Validate patient
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    patient = result.scalars().first()
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # Calculate BMI if height and weight provided
    bmi = None
    if vitals_data.weight and vitals_data.height:
        # BMI = weight(kg) / (height(m))^2
        height_m = vitals_data.height / 100  # Convert cm to m
        bmi = vitals_data.weight / (height_m ** 2)
    
    # Create vital signs
    vital_signs = VitalSign(
        patient_uuid=patient_id,
        temperature=vitals_data.temperature,
        systolic_bp=vitals_data.systolic_bp,
        diastolic_bp=vitals_data.diastolic_bp,
        heart_rate=vitals_data.heart_rate,
        respiratory_rate=vitals_data.respiratory_rate,
        oxygen_saturation=vitals_data.oxygen_saturation,
        weight=vitals_data.weight,
        height=vitals_data.height,
        bmi=bmi,
        measurement_date=vitals_data.measurement_date,
        recorded_by=current_user.uuid
    )
    
    db.add(vital_signs)
    await db.commit()
    await db.refresh(vital_signs)
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.ENTER_VITALS,
        target_type="vital_signs",
        target_uuid=vital_signs.uuid,
        patient_uuid=patient_id,
        action_details="Recorded vital signs",
        request=request
    )
    
    logger.info(f"Vital signs created: {vital_signs.uuid}")
    
    return vital_signs

@router.get("/vitals", response_model=List[VitalSignResponse])
async def list_vital_signs(
    request: Request,
    patient_id: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """List vital signs with optional patient filter."""
    
    query = select(VitalSign)
    
    # Patient role restriction
    if current_user.role.value == "patient":
        if not current_user.patient_record:
            return []
        query = query.where(VitalSign.patient_uuid == current_user.patient_record.uuid)
    elif patient_id:
        query = query.where(VitalSign.patient_uuid == patient_id)
    
    query = query.order_by(VitalSign.measurement_date.desc())
    result = await db.execute(query)
    vitals = result.scalars().all()
    
    return vitals

@router.get("/vitals/patient/{patient_id}", response_model=List[VitalSignResponse])
async def get_patient_vitals(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """Get all vital signs for a specific patient."""
    
    # Check patient access
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    result = await db.execute(
        select(VitalSign)
        .where(VitalSign.patient_uuid == patient_id)
        .order_by(VitalSign.measurement_date.desc())
    )
    vitals = result.scalars().all()
    
    return vitals

@router.get("/vitals/latest/{patient_id}", response_model=VitalSignResponse)
async def get_latest_vitals(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """Get most recent vital signs for a patient."""
    
    # Check patient access
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    result = await db.execute(
        select(VitalSign)
        .where(VitalSign.patient_uuid == patient_id)
        .order_by(VitalSign.measurement_date.desc())
        .limit(1)
    )
    vitals = result.scalars().first()
    
    if not vitals:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No vital signs found for this patient"
        )
    
    return vitals

@router.delete("/vitals/{vitals_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vital_signs(
    vitals_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician")),
    db: AsyncSession = Depends(get_db)
):
    """Delete vital signs record."""
    
    result = await db.execute(select(VitalSign).where(VitalSign.uuid == vitals_id))
    vitals = result.scalars().first()
    
    if not vitals:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vital signs not found"
        )
    
    patient_uuid = vitals.patient_uuid
    await db.delete(vitals)
    await db.commit()
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.OTHER,
        target_type="vital_signs",
        target_uuid=vitals_id,
        patient_uuid=patient_uuid,
        action_details="Deleted vital signs",
        request=request
    )
    
    logger.info(f"Vital signs deleted: {vitals_id}")
