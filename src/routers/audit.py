"""
Audit log router — was completely missing from the codebase.
Compliance and Admin can view audit logs.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import Optional
import logging

from src.database.postgres import get_db
from src.models.user import User
from src.models.audit_log import AuditLog, AuditAction, AuditStatus
from src.schemas.audit import AuditLogResponse, AuditLogListResponse
from src.utils.auth import require_roles, log_audit

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_model=AuditLogListResponse)
async def list_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user_id: Optional[str] = Query(None, description="Filter by user UUID"),
    patient_id: Optional[str] = Query(None, description="Filter by patient UUID"),
    action: Optional[str] = Query(None, description="Filter by action type"),
    status_filter: Optional[str] = Query(None, alias="status"),
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve paginated audit logs (Admin and Compliance only).
    Supports filtering by user, patient, action, and status.
    """
    query = select(AuditLog).options(selectinload(AuditLog.user))

    if user_id:
        query = query.where(AuditLog.user_uuid == user_id)
    if patient_id:
        query = query.where(AuditLog.patient_uuid == patient_id)
    if action:
        try:
            action_enum = AuditAction(action)
            query = query.where(AuditLog.action == action_enum)
        except ValueError:
            pass  # Unknown action value — ignore filter
    if status_filter:
        try:
            status_enum = AuditStatus(status_filter)
            query = query.where(AuditLog.status == status_enum)
        except ValueError:
            pass

    # Total count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate — newest first
    offset = (page - 1) * page_size
    query = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(page_size)
    logs = (await db.execute(query)).scalars().all()

    total_pages = max(1, (total + page_size - 1) // page_size)

    # Log the fact that audit logs were accessed
    await log_audit(
        db=db, user=current_user, action=AuditAction.VIEW_AUDIT_LOG,
        target_type="audit_logs",
        action_details=f"Viewed audit log page {page}", request=request,
    )

    return AuditLogListResponse(
        logs=logs,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/patient/{patient_id}", response_model=AuditLogListResponse)
async def get_patient_audit_logs(
    patient_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db),
):
    """Get all audit log entries related to a specific patient."""
    query = (
        select(AuditLog)
        .options(selectinload(AuditLog.user))
        .where(AuditLog.patient_uuid == patient_id)
    )
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    offset = (page - 1) * page_size
    query = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(page_size)
    logs = (await db.execute(query)).scalars().all()

    await log_audit(
        db=db, user=current_user, action=AuditAction.VIEW_AUDIT_LOG,
        target_type="audit_logs", patient_uuid=patient_id,
        action_details=f"Viewed patient audit logs for {patient_id}", request=request,
    )

    return AuditLogListResponse(
        logs=logs,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get("/user/{user_id}", response_model=AuditLogListResponse)
async def get_user_audit_logs(
    user_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db),
):
    """Get all audit log entries for a specific user."""
    query = (
        select(AuditLog)
        .options(selectinload(AuditLog.user))
        .where(AuditLog.user_uuid == user_id)
    )
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    offset = (page - 1) * page_size
    query = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(page_size)
    logs = (await db.execute(query)).scalars().all()

    return AuditLogListResponse(
        logs=logs,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get("/{log_id}", response_model=AuditLogResponse)
async def get_audit_log(
    log_id: str,
    request: Request,
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single audit log entry by UUID."""
    log = (await db.execute(
        select(AuditLog).options(selectinload(AuditLog.user)).where(AuditLog.uuid == log_id)
    )).scalars().first()

    if not log:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit log not found")

    return log


@router.get("/actions/summary")
async def get_action_summary(
    request: Request,
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db),
):
    """
    Return a count breakdown of all audit actions.
    Useful for compliance dashboards.
    """
    result = await db.execute(
        select(AuditLog.action, func.count(AuditLog.uuid).label("count"))
        .group_by(AuditLog.action)
        .order_by(func.count(AuditLog.uuid).desc())
    )
    rows = result.all()
    return {
        "summary": [{"action": row.action.value, "count": row.count} for row in rows]
    }