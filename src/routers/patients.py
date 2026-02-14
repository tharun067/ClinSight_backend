"""
Patient management router.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from typing import Optional
import logging
from datetime import datetime

from src.database.postgres import get_db
from src.models.user import User
from src.models.patient import Patient, PatientStatus
from src.schemas.patient import PatientCreate, PatientUpdate, PatientResponse, PatientListResponse
from src.utils.auth import get_current_active_user, require_roles, log_audit
from src.models.audit_log import AuditAction

router = APIRouter()
logger = logging.getLogger(__name__)

def generate_mrn() -> str:
    """Generate a unique MRN."""
    import random
    return f"MRN-{random.randint(10000, 99999)}"

@router.post("/", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
async def create_patient(
    patient_data: PatientCreate,
    request: Request,
    current_user: User = Depends(require_roles("intake")),
    db: AsyncSession = Depends(get_db)
):
    """
    Register a new patient (Intake Officer only).
    """
    # Generate unique MRN
    mrn = generate_mrn()
    
    # Check if MRN exists (unlikely but safe)
    while True:
        result = await db.execute(select(Patient).where(Patient.mrn == mrn))
        if not result.scalars().first():
            break
        mrn = generate_mrn()
    
    # Create patient
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
        registered_by=current_user.uuid
    )
    
    db.add(new_patient)
    await db.commit()
    await db.refresh(new_patient)
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.CREATE_PATIENT,
        target_type="patient",
        target_uuid=new_patient.uuid,
        patient_uuid=new_patient.uuid,
        request=request
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
    current_user: User = Depends(require_roles("intake", "nurse", "radiologist", "physician")),
    db: AsyncSession = Depends(get_db)
):
    """
    List all patients with search and filtering.
    """
    query = select(Patient)
    
    # Apply search filter
    if search:
        query = query.where(
            or_(
                Patient.full_name.ilike(f"%{search}%"),
                Patient.mrn.ilike(f"%{search}%"),
                Patient.email.ilike(f"%{search}%")
            )
        )
    
    # Apply status filter
    if status_filter:
        query = query.where(Patient.status == status_filter)
    
    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    # Paginate
    offset = (page - 1) * page_size
    query = query.order_by(Patient.last_activity.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    patients = result.scalars().all()
    
    return {
        "patients": patients,
        "total": total,
        "page": page,
        "page_size": page_size
    }

@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("intake", "nurse", "radiologist", "physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """Get patient details."""
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    patient = result.scalars().first()
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # If user is a patient, only allow viewing own record
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.VIEW_PATIENT,
        target_type="patient",
        target_uuid=patient.uuid,
        patient_uuid=patient.uuid,
        request=request
    )
    
    return patient

@router.put("/{patient_id}", response_model=PatientResponse)
async def update_patient(
    patient_id: str,
    patient_data: PatientUpdate,
    request: Request,
    current_user: User = Depends(require_roles("intake", "nurse", "physician")),
    db: AsyncSession = Depends(get_db)
):
    """Update patient information."""
    result = await db.execute(select(Patient).where(Patient.uuid == patient_id))
    patient = result.scalars().first()
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # Update fields
    for key, value in patient_data.dict(exclude_unset=True).items():
        setattr(patient, key, value)
    
    await db.commit()
    await db.refresh(patient)
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.UPDATE_PATIENT,
        target_type="patient",
        target_uuid=patient.uuid,
        patient_uuid=patient.uuid,
        request=request
    )
    
    return patient
