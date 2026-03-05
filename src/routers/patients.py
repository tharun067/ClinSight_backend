"""
Patient management router.

Role permissions:
- nurse:     create patients, view patient list, view/update patient records
- physician: view all patients, view/update patient records
- admin:     full access including delete
- patient:   view and update own record only (via /my-record)
"""
import secrets
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from typing import Optional
import logging
from datetime import datetime

from src.database.postgres import get_db
from src.models.user import User, UserRole
from src.models.patient import Patient, PatientStatus
from src.schemas.patient import PatientCreate, PatientUpdate, PatientResponse, PatientListResponse
from src.utils.auth import get_current_active_user, require_roles, log_audit
from src.utils.validators import validate_uuid
from src.models.audit_log import AuditAction

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_MRN_ATTEMPTS = 20


def generate_mrn() -> str:
    """Generate a cryptographically random MRN."""
    number = secrets.randbelow(90000) + 10000
    return f"MRN-{number}"


# ── Patient portal endpoints (must be before /{patient_id}) ──────────────────

@router.get("/my-record", response_model=PatientResponse)
async def get_my_patient_record(
    request: Request,
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Patient: get own medical record."""
    if not current_user.patient_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No patient record linked. Use POST /api/patients/link-my-record.",
        )
    await db.refresh(current_user.patient_record)
    await log_audit(
        db=db, user=current_user, action=AuditAction.VIEW_PATIENT,
        target_type="patient", target_uuid=current_user.patient_record.uuid,
        patient_uuid=current_user.patient_record.uuid, request=request,
    )
    return current_user.patient_record


@router.patch("/my-record", response_model=PatientResponse)
async def update_my_record(
    patient_data: PatientUpdate,
    request: Request,
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Patient: update own record fields (phone, address, email)."""
    if not current_user.patient_record:
        raise HTTPException(status_code=404, detail="No patient record linked.")
    patient = current_user.patient_record
    allowed_fields = {"phone", "address", "email"}
    update_data = patient_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key in allowed_fields:
            setattr(patient, key, value)
    await db.commit()
    await db.refresh(patient)
    await log_audit(
        db=db, user=current_user, action=AuditAction.UPDATE_PATIENT,
        target_type="patient", target_uuid=patient.uuid,
        patient_uuid=patient.uuid, request=request,
    )
    return patient


@router.post("/link-my-record")
async def link_patient_record(
    mrn: str,
    request: Request,
    current_user: User = Depends(require_roles("patient")),
    db: AsyncSession = Depends(get_db),
):
    """Patient: link account to a medical record by MRN."""
    existing_link = (await db.execute(
        select(Patient).where(Patient.user_uuid == current_user.uuid)
    )).scalars().first()
    if existing_link:
        raise HTTPException(status_code=400, detail="Account already linked to a patient record.")
    patient = (await db.execute(select(Patient).where(Patient.mrn == mrn))).scalars().first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient record not found.")
    if patient.email and patient.email.lower() != current_user.email.lower():
        raise HTTPException(status_code=403, detail="Email on file does not match your account email.")
    if patient.user_uuid:
        raise HTTPException(status_code=400, detail="This patient record is already linked to another account.")
    patient.user_uuid = current_user.uuid
    await db.commit()
    await db.refresh(patient)
    await log_audit(
        db=db, user=current_user, action=AuditAction.OTHER,
        action_details=f"Linked patient record {mrn}",
        target_type="patient", target_uuid=patient.uuid,
        patient_uuid=patient.uuid, request=request,
    )
    logger.info(f"Patient record {mrn} linked to user {current_user.username}")
    return {"message": "Patient record linked successfully", "patient_id": patient.uuid, "mrn": patient.mrn}


# ── Staff/admin endpoints ──────────────────────────────────────────────────────

@router.post("/", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
async def create_patient(
    patient_data: PatientCreate,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Create/register a new patient (Nurse or Admin)."""
    mrn = generate_mrn()
    for _ in range(_MAX_MRN_ATTEMPTS):
        if not (await db.execute(select(Patient).where(Patient.mrn == mrn))).scalars().first():
            break
        mrn = generate_mrn()
    else:
        raise HTTPException(status_code=500, detail="Unable to generate unique MRN. Please retry.")

    new_patient = Patient(
        mrn=mrn,
        full_name=patient_data.full_name,
        date_of_birth=patient_data.date_of_birth,
        gender=patient_data.gender,
        phone=patient_data.phone,
        address=patient_data.address,
        email=patient_data.email,
        visit_type=patient_data.visit_type,
        chief_complaint=patient_data.chief_complaint,
        visit_date=patient_data.visit_date or datetime.now(),
        status=PatientStatus.ACTIVE,
        registered_by=current_user.uuid,
    )
    db.add(new_patient)
    await db.commit()
    await db.refresh(new_patient)
    await log_audit(
        db=db, user=current_user, action=AuditAction.CREATE_PATIENT,
        target_type="patient", target_uuid=new_patient.uuid,
        patient_uuid=new_patient.uuid, request=request,
    )
    logger.info(f"Patient created: {new_patient.mrn} by {current_user.username}")
    return new_patient


@router.get("/", response_model=PatientListResponse)
async def list_patients(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    current_user: User = Depends(require_roles("nurse", "physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """List all patients with search and pagination (Nurse, Physician, Admin)."""
    query = select(Patient)
    if search:
        query = query.where(
            or_(
                Patient.full_name.ilike(f"%{search}%"),
                Patient.mrn.ilike(f"%{search}%"),
                Patient.email.ilike(f"%{search}%"),
            )
        )
    if status_filter:
        query = query.where(Patient.status == status_filter)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar()
    offset = (page - 1) * page_size
    query = query.order_by(Patient.last_activity.desc()).offset(offset).limit(page_size)
    patients = (await db.execute(query)).scalars().all()

    return {"patients": patients, "total": total, "page": page, "page_size": page_size}


@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get patient details. Patients may only view their own record."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    patient = (await db.execute(select(Patient).where(Patient.uuid == patient_id))).scalars().first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(status_code=403, detail="Access denied")

    await log_audit(
        db=db, user=current_user, action=AuditAction.VIEW_PATIENT,
        target_type="patient", target_uuid=patient.uuid,
        patient_uuid=patient.uuid, request=request,
    )
    return patient


@router.put("/{patient_id}", response_model=PatientResponse)
async def update_patient(
    patient_id: str,
    patient_data: PatientUpdate,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Update patient record (Nurse, Physician, Admin)."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    patient = (await db.execute(select(Patient).where(Patient.uuid == patient_id))).scalars().first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    for key, value in patient_data.model_dump(exclude_unset=True).items():
        setattr(patient, key, value)
    await db.commit()
    await db.refresh(patient)

    await log_audit(
        db=db, user=current_user, action=AuditAction.UPDATE_PATIENT,
        target_type="patient", target_uuid=patient.uuid,
        patient_uuid=patient.uuid, request=request,
    )
    return patient


@router.patch("/{patient_id}/status", response_model=PatientResponse)
async def update_patient_status(
    patient_id: str,
    new_status: str,
    request: Request,
    current_user: User = Depends(require_roles("nurse", "physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Update patient status (active/discharged/inactive). Nurse, Physician, Admin."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    patient = (await db.execute(select(Patient).where(Patient.uuid == patient_id))).scalars().first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    try:
        patient.status = PatientStatus(new_status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status. Options: {[s.value for s in PatientStatus]}")
    await db.commit()
    await db.refresh(patient)
    await log_audit(
        db=db, user=current_user, action=AuditAction.UPDATE_PATIENT,
        target_type="patient", target_uuid=patient.uuid,
        patient_uuid=patient.uuid,
        action_details=f"Status updated to {new_status}", request=request,
    )
    return patient


@router.delete("/{patient_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_patient(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a patient record (Admin only)."""
    patient_id = validate_uuid(patient_id, "Patient ID")
    patient = (await db.execute(select(Patient).where(Patient.uuid == patient_id))).scalars().first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    await db.delete(patient)
    await db.commit()
    await log_audit(
        db=db, user=current_user, action=AuditAction.OTHER,
        action_details="Deleted patient record",
        target_type="patient", target_uuid=patient_id, request=request,
    )
    logger.info(f"Patient {patient_id} deleted by {current_user.username}")
