"""
Lab results and vital signs router.
Fixed:
  - Uses correct model field names (measurement_date, systolic_bp, recorded_by, ordered_by)
  - VitalSignCreate schema fields match model exactly
  - BMI auto-calculation uses schema fields that now exist
  - Admin included in all require_roles guards
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
    VitalSignCreate, VitalSignResponse,
)
from src.utils.auth import require_roles, log_audit
from src.utils.validators import validate_uuid
from src.models.audit_log import AuditAction

router = APIRouter()
logger = logging.getLogger(__name__)


# ═══════════════════════════════ LAB RESULTS ════════════════════════════════

@router.post("/labs", response_model=LabResultResponse, status_code=status.HTTP_201_CREATED)
async def create_lab_result(
    lab_data: LabResultCreate,
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Add a lab result for a patient."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    if not result.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    # Auto-calculate abnormal flag
    is_abnormal = lab_data.is_abnormal
    if not is_abnormal and lab_data.reference_range_high and lab_data.reference_range_low:
        if lab_data.test_value > lab_data.reference_range_high:
            is_abnormal = "High"
        elif lab_data.test_value < lab_data.reference_range_low:
            is_abnormal = "Low"
        else:
            is_abnormal = "Normal"
    elif not is_abnormal and lab_data.reference_range_high and lab_data.test_value > lab_data.reference_range_high:
        is_abnormal = "High"
    elif not is_abnormal and lab_data.reference_range_low and lab_data.test_value < lab_data.reference_range_low:
        is_abnormal = "Low"

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
        ordered_by=current_user.uuid,  # ← correct field name
    )
    db.add(lab_result)
    await db.commit()
    await db.refresh(lab_result)

    await log_audit(
        db=db, user=current_user, action=AuditAction.ENTER_LABS,
        target_type="lab_result", target_uuid=lab_result.uuid,
        patient_uuid=patient_id,
        action_details=f"Added {lab_data.test_name}: {lab_data.test_value} {lab_data.unit}",
        request=request,
    )
    return lab_result


@router.get("/labs", response_model=List[LabResultResponse])
async def list_lab_results(
    request: Request,
    patient_id: Optional[str] = Query(None),
    test_name: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "admin", "compliance", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """List lab results with optional filters."""
    from src.models.user import UserRole
    query = select(LabResult)
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record:
            return []
        query = query.where(LabResult.patient_uuid == current_user.patient_record.uuid)
    elif patient_id:
        query = query.where(LabResult.patient_uuid == patient_id)
    if test_name:
        query = query.where(LabResult.test_name.ilike(f"%{test_name}%"))
    query = query.order_by(LabResult.test_date.desc())
    return (await db.execute(query)).scalars().all()


@router.get("/labs/patient/{patient_id}", response_model=List[LabResultResponse])
async def get_patient_labs(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "admin", "compliance", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get all lab results for a specific patient."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    from src.models.user import UserRole
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    labs = (await db.execute(
        select(LabResult).where(LabResult.patient_uuid == patient_id).order_by(LabResult.test_date.desc())
    )).scalars().all()

    await log_audit(
        db=db, user=current_user, action=AuditAction.VIEW_LABS,
        target_type="lab_results", patient_uuid=patient_id, request=request,
    )
    return labs


@router.get("/labs/{lab_id}", response_model=LabResultResponse)
async def get_lab_result(
    lab_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "admin", "compliance", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific lab result."""
    lab_id = validate_uuid(lab_id, "Lab Result ID")
    lab = (await db.execute(select(LabResult).where(LabResult.uuid == lab_id))).scalars().first()
    if not lab:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lab result not found")
    from src.models.user import UserRole
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != lab.patient_uuid:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return lab


@router.put("/labs/{lab_id}", response_model=LabResultResponse)
async def update_lab_result(
    lab_id: str,
    lab_update: LabResultCreate,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Update a lab result."""
    lab_id = validate_uuid(lab_id, "Lab Result ID")
    lab = (await db.execute(select(LabResult).where(LabResult.uuid == lab_id))).scalars().first()
    if not lab:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lab result not found")

    lab.test_name = lab_update.test_name
    lab.test_value = lab_update.test_value
    lab.unit = lab_update.unit
    lab.reference_range_low = lab_update.reference_range_low
    lab.reference_range_high = lab_update.reference_range_high
    lab.test_date = lab_update.test_date
    lab.notes = lab_update.notes

    is_abnormal = lab_update.is_abnormal
    if not is_abnormal and lab_update.reference_range_high and lab_update.reference_range_low:
        if lab_update.test_value > lab_update.reference_range_high:
            is_abnormal = "High"
        elif lab_update.test_value < lab_update.reference_range_low:
            is_abnormal = "Low"
        else:
            is_abnormal = "Normal"
    lab.is_abnormal = is_abnormal

    await db.commit()
    await db.refresh(lab)
    await log_audit(
        db=db, user=current_user, action=AuditAction.ENTER_LABS,
        target_type="lab_result", target_uuid=lab_id,
        patient_uuid=lab.patient_uuid, action_details=f"Updated {lab.test_name}", request=request,
    )
    return lab


@router.delete("/labs/{lab_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lab_result(
    lab_id: str,
    request: Request,
    current_user: User = Depends(require_roles("physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a lab result."""
    lab = (await db.execute(select(LabResult).where(LabResult.uuid == lab_id))).scalars().first()
    if not lab:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lab result not found")
    patient_uuid = lab.patient_uuid
    await db.delete(lab)
    await db.commit()
    await log_audit(
        db=db, user=current_user, action=AuditAction.OTHER,
        target_type="lab_result", target_uuid=lab_id,
        patient_uuid=patient_uuid, action_details="Deleted lab result", request=request,
    )


# Patient portal — labs
@router.post("/my-labs", response_model=LabResultResponse, status_code=status.HTTP_201_CREATED)
async def create_my_lab_result(
    lab_data: LabResultCreate,
    request: Request,
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Patient uploads their own external lab result."""
    if not current_user.patient_record:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No patient record linked.")
    patient_id = current_user.patient_record.uuid

    is_abnormal = lab_data.is_abnormal
    if not is_abnormal and lab_data.reference_range_high and lab_data.reference_range_low:
        if lab_data.test_value > lab_data.reference_range_high:
            is_abnormal = "High"
        elif lab_data.test_value < lab_data.reference_range_low:
            is_abnormal = "Low"
        else:
            is_abnormal = "Normal"

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
        ordered_by=current_user.uuid,  # ← correct field name (was entered_by)
    )
    db.add(lab_result)
    await db.commit()
    await db.refresh(lab_result)
    await log_audit(
        db=db, user=current_user, action=AuditAction.ENTER_LABS,
        target_type="lab_result", target_uuid=lab_result.uuid,
        patient_uuid=patient_id,
        action_details=f"Patient added {lab_data.test_name}: {lab_data.test_value} {lab_data.unit}",
        request=request,
    )
    return lab_result


@router.get("/my-labs", response_model=List[LabResultResponse])
async def get_my_lab_results(
    request: Request,
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get all lab results from the patient's own record."""
    if not current_user.patient_record:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No patient record linked.")
    labs = (await db.execute(
        select(LabResult)
        .where(LabResult.patient_uuid == current_user.patient_record.uuid)
        .order_by(LabResult.test_date.desc())
    )).scalars().all()
    return labs


# ═══════════════════════════════ VITAL SIGNS ════════════════════════════════

@router.post("/vitals", response_model=VitalSignResponse, status_code=status.HTTP_201_CREATED)
async def create_vital_signs(
    vitals_data: VitalSignCreate,
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Add vital signs for a patient. BMI auto-calculated if height + weight provided."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    if not result.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    bmi = None
    if vitals_data.weight and vitals_data.height and vitals_data.height > 0:
        height_m = vitals_data.height / 100
        bmi = round(vitals_data.weight / (height_m ** 2), 2)

    vital_signs = VitalSign(
        patient_uuid=patient_id,
        measurement_date=vitals_data.measurement_date,   # ← correct field name
        temperature=vitals_data.temperature,
        temperature_unit=vitals_data.temperature_unit or "°C",
        systolic_bp=vitals_data.systolic_bp,             # ← correct field name
        diastolic_bp=vitals_data.diastolic_bp,           # ← correct field name
        heart_rate=vitals_data.heart_rate,
        respiratory_rate=vitals_data.respiratory_rate,
        oxygen_saturation=vitals_data.oxygen_saturation,
        weight=vitals_data.weight,
        height=vitals_data.height,
        bmi=bmi,
        notes=vitals_data.notes,
        recorded_by=current_user.uuid,                   # ← correct field name
    )
    db.add(vital_signs)
    await db.commit()
    await db.refresh(vital_signs)
    await log_audit(
        db=db, user=current_user, action=AuditAction.ENTER_VITALS,
        target_type="vital_signs", target_uuid=vital_signs.uuid,
        patient_uuid=patient_id, action_details="Recorded vital signs", request=request,
    )
    return vital_signs


@router.get("/vitals", response_model=List[VitalSignResponse])
async def list_vital_signs(
    request: Request,
    patient_id: Optional[str] = Query(None),
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "admin", "compliance", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """List vital signs with optional patient filter."""
    from src.models.user import UserRole
    query = select(VitalSign)
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record:
            return []
        query = query.where(VitalSign.patient_uuid == current_user.patient_record.uuid)
    elif patient_id:
        query = query.where(VitalSign.patient_uuid == patient_id)
    query = query.order_by(VitalSign.measurement_date.desc())
    return (await db.execute(query)).scalars().all()


@router.get("/vitals/patient/{patient_id}", response_model=List[VitalSignResponse])
async def get_patient_vitals(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "admin", "compliance", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get all vital signs for a specific patient."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    from src.models.user import UserRole
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    vitals = (await db.execute(
        select(VitalSign).where(VitalSign.patient_uuid == patient_id).order_by(VitalSign.measurement_date.desc())
    )).scalars().all()
    return vitals


@router.get("/vitals/latest/{patient_id}", response_model=VitalSignResponse)
async def get_latest_vitals(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "radiologist", "physician", "admin", "compliance", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get most recent vital signs for a patient."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    from src.models.user import UserRole
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    vital = (await db.execute(
        select(VitalSign).where(VitalSign.patient_uuid == patient_id)
        .order_by(VitalSign.measurement_date.desc()).limit(1)
    )).scalars().first()
    if not vital:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No vital signs found")
    return vital


@router.delete("/vitals/{vitals_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vital_signs(
    vitals_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a vital signs record."""
    vitals_id = validate_uuid(vitals_id, "Vital Signs ID")
    vitals = (await db.execute(select(VitalSign).where(VitalSign.uuid == vitals_id))).scalars().first()
    if not vitals:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vital signs not found")
    patient_uuid = vitals.patient_uuid
    await db.delete(vitals)
    await db.commit()
    await log_audit(
        db=db, user=current_user, action=AuditAction.OTHER,
        target_type="vital_signs", target_uuid=vitals_id,
        patient_uuid=patient_uuid, action_details="Deleted vital signs", request=request,
    )


# Patient portal — vitals
@router.post("/my-vitals", response_model=VitalSignResponse, status_code=status.HTTP_201_CREATED)
async def create_my_vital_signs(
    vital_data: VitalSignCreate,
    request: Request,
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Patient logs their own home vital signs."""
    if not current_user.patient_record:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No patient record linked.")
    patient_id = current_user.patient_record.uuid

    bmi = None
    if vital_data.weight and vital_data.height and vital_data.height > 0:
        height_m = vital_data.height / 100
        bmi = round(vital_data.weight / (height_m ** 2), 2)

    vital_sign = VitalSign(
        patient_uuid=patient_id,
        measurement_date=vital_data.measurement_date,    # ← correct field name
        temperature=vital_data.temperature,
        temperature_unit=vital_data.temperature_unit or "°C",
        systolic_bp=vital_data.systolic_bp,              # ← correct field name
        diastolic_bp=vital_data.diastolic_bp,            # ← correct field name
        heart_rate=vital_data.heart_rate,
        respiratory_rate=vital_data.respiratory_rate,
        oxygen_saturation=vital_data.oxygen_saturation,
        weight=vital_data.weight,
        height=vital_data.height,
        bmi=bmi,
        notes=vital_data.notes,
        recorded_by=current_user.uuid,                   # ← correct field name
    )
    db.add(vital_sign)
    await db.commit()
    await db.refresh(vital_sign)
    await log_audit(
        db=db, user=current_user, action=AuditAction.ENTER_VITALS,
        target_type="vital_sign", target_uuid=vital_sign.uuid,
        patient_uuid=patient_id, action_details="Patient logged home vital signs", request=request,
    )
    return vital_sign


@router.get("/my-vitals", response_model=List[VitalSignResponse])
async def get_my_vital_signs(
    request: Request,
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get all vital signs from the patient's own record."""
    if not current_user.patient_record:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No patient record linked.")
    vitals = (await db.execute(
        select(VitalSign)
        .where(VitalSign.patient_uuid == current_user.patient_record.uuid)
        .order_by(VitalSign.measurement_date.desc())
    )).scalars().all()
    return vitals