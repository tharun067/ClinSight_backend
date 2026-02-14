"""
Audit logs router for compliance and monitoring.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from typing import Optional, List
from datetime import datetime, timedelta
import logging
import csv
import io

from src.database.postgres import get_db
from src.models.user import User
from src.models.audit_log import AuditLog, AuditAction, AuditStatus
from src.schemas.audit import AuditLogResponse, AuditLogListResponse
from src.utils.auth import get_current_active_user, require_roles

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/", response_model=AuditLogListResponse)
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    user_id: Optional[str] = Query(None),
    patient_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db)
):
    """
    List audit logs with filtering and pagination.
    
    Admin and Compliance officers only.
    """
    
    # Build base query
    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)
    
    # Apply filters
    filters = []
    
    if user_id:
        filters.append(AuditLog.user_uuid == user_id)
    
    if patient_id:
        filters.append(AuditLog.patient_uuid == patient_id)
    
    if action:
        try:
            audit_action = AuditAction(action)
            filters.append(AuditLog.action == audit_action)
        except ValueError:
            pass
    
    if status_filter:
        try:
            audit_status = AuditStatus(status_filter)
            filters.append(AuditLog.status == audit_status)
        except ValueError:
            pass
    
    if start_date:
        filters.append(AuditLog.timestamp >= start_date)
    
    if end_date:
        filters.append(AuditLog.timestamp <= end_date)
    
    # Apply filters to queries
    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))
    
    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    # Apply pagination
    offset = (page - 1) * page_size
    query = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(page_size)
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    # Calculate pagination info
    total_pages = (total + page_size - 1) // page_size
    
    return AuditLogListResponse(
        logs=logs,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )

@router.get("/user/{user_id}", response_model=List[AuditLogResponse])
async def get_user_audit_logs(
    user_id: str,
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db)
):
    """Get audit logs for a specific user."""
    
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.user_uuid == user_id)
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    
    return logs

@router.get("/patient/{patient_id}", response_model=List[AuditLogResponse])
async def get_patient_audit_logs(
    patient_id: str,
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db)
):
    """Get audit logs related to a specific patient."""
    
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.patient_uuid == patient_id)
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    
    return logs

@router.get("/export")
async def export_audit_logs(
    user_id: Optional[str] = Query(None),
    patient_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db)
):
    """
    Export audit logs to CSV file.
    
    Returns a downloadable CSV file with filtered audit logs.
    """
    
    # Build query with filters
    query = select(AuditLog)
    filters = []
    
    if user_id:
        filters.append(AuditLog.user_uuid == user_id)
    
    if patient_id:
        filters.append(AuditLog.patient_uuid == patient_id)
    
    if action:
        try:
            audit_action = AuditAction(action)
            filters.append(AuditLog.action == audit_action)
        except ValueError:
            pass
    
    if start_date:
        filters.append(AuditLog.timestamp >= start_date)
    
    if end_date:
        filters.append(AuditLog.timestamp <= end_date)
    
    if filters:
        query = query.where(and_(*filters))
    
    query = query.order_by(AuditLog.timestamp.desc())
    
    # Execute query
    result = await db.execute(query)
    logs = result.scalars().all()
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        'Timestamp',
        'User ID',
        'Username',
        'Action',
        'Target Type',
        'Target UUID',
        'Patient UUID',
        'Status',
        'IP Address',
        'Details'
    ])
    
    # Write data
    for log in logs:
        writer.writerow([
            log.timestamp.isoformat(),
            log.user_uuid,
            log.username,
            log.action.value if log.action else '',
            log.target_type or '',
            log.target_uuid or '',
            log.patient_uuid or '',
            log.status.value if log.status else '',
            log.ip_address or '',
            log.action_details or ''
        ])
    
    # Prepare response
    output.seek(0)
    
    # Generate filename
    filename = f"audit_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )

@router.get("/stats")
async def get_audit_statistics(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get audit log statistics for dashboard.
    
    Returns activity metrics for the specified time period.
    """
    
    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    # Total logs count
    total_result = await db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.timestamp >= start_date)
    )
    total_logs = total_result.scalar()
    
    # Logs by action
    action_result = await db.execute(
        select(
            AuditLog.action,
            func.count(AuditLog.id).label('count')
        )
        .where(AuditLog.timestamp >= start_date)
        .group_by(AuditLog.action)
    )
    logs_by_action = {
        row.action.value if row.action else 'unknown': row.count
        for row in action_result.all()
    }
    
    # Logs by status
    status_result = await db.execute(
        select(
            AuditLog.status,
            func.count(AuditLog.id).label('count')
        )
        .where(AuditLog.timestamp >= start_date)
        .group_by(AuditLog.status)
    )
    logs_by_status = {
        row.status.value if row.status else 'unknown': row.count
        for row in status_result.all()
    }
    
    # Most active users
    user_result = await db.execute(
        select(
            AuditLog.username,
            func.count(AuditLog.id).label('count')
        )
        .where(AuditLog.timestamp >= start_date)
        .group_by(AuditLog.username)
        .order_by(func.count(AuditLog.id).desc())
        .limit(10)
    )
    most_active_users = [
        {"username": row.username, "activity_count": row.count}
        for row in user_result.all()
    ]
    
    # Failed actions (errors)
    failed_result = await db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(
            and_(
                AuditLog.timestamp >= start_date,
                or_(
                    AuditLog.status == AuditStatus.ERROR,
                    AuditLog.status == AuditStatus.UNAUTHORIZED
                )
            )
        )
    )
    failed_actions = failed_result.scalar()
    
    # Success rate
    success_rate = ((total_logs - failed_actions) / total_logs * 100) if total_logs > 0 else 100
    
    # Activity by day (last 7 days)
    daily_activity = []
    for i in range(7):
        day_start = end_date - timedelta(days=i+1)
        day_end = end_date - timedelta(days=i)
        
        day_result = await db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                and_(
                    AuditLog.timestamp >= day_start,
                    AuditLog.timestamp < day_end
                )
            )
        )
        count = day_result.scalar()
        
        daily_activity.append({
            "date": day_start.strftime('%Y-%m-%d'),
            "count": count
        })
    
    daily_activity.reverse()
    
    return {
        "period_days": days,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_logs": total_logs,
        "failed_actions": failed_actions,
        "success_rate": round(success_rate, 2),
        "logs_by_action": logs_by_action,
        "logs_by_status": logs_by_status,
        "most_active_users": most_active_users,
        "daily_activity": daily_activity
    }

@router.get("/recent")
async def get_recent_activity(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get most recent audit log entries.
    
    Useful for real-time activity monitoring.
    """
    
    result = await db.execute(
        select(AuditLog)
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    
    return {
        "recent_activity": [
            {
                "timestamp": log.timestamp.isoformat(),
                "username": log.username,
                "action": log.action.value if log.action else None,
                "target_type": log.target_type,
                "status": log.status.value if log.status else None,
                "details": log.action_details
            }
            for log in logs
        ],
        "count": len(logs)
    }

@router.get("/security-events")
async def get_security_events(
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(require_roles("admin", "compliance")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get security-related events (failed logins, unauthorized access).
    
    Critical for security monitoring and compliance.
    """
    
    start_date = datetime.now() - timedelta(days=days)
    
    # Failed logins
    failed_login_result = await db.execute(
        select(AuditLog)
        .where(
            and_(
                AuditLog.timestamp >= start_date,
                AuditLog.action == AuditAction.LOGIN,
                AuditLog.status == AuditStatus.ERROR
            )
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(50)
    )
    failed_logins = failed_login_result.scalars().all()
    
    # Unauthorized access attempts
    unauthorized_result = await db.execute(
        select(AuditLog)
        .where(
            and_(
                AuditLog.timestamp >= start_date,
                AuditLog.status == AuditStatus.UNAUTHORIZED
            )
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(50)
    )
    unauthorized_attempts = unauthorized_result.scalars().all()
    
    # Suspicious IP addresses (multiple failed attempts)
    suspicious_ips_result = await db.execute(
        select(
            AuditLog.ip_address,
            func.count(AuditLog.id).label('failed_count')
        )
        .where(
            and_(
                AuditLog.timestamp >= start_date,
                or_(
                    AuditLog.status == AuditStatus.ERROR,
                    AuditLog.status == AuditStatus.UNAUTHORIZED
                ),
                AuditLog.ip_address.isnot(None)
            )
        )
        .group_by(AuditLog.ip_address)
        .having(func.count(AuditLog.id) >= 3)
        .order_by(func.count(AuditLog.id).desc())
    )
    suspicious_ips = [
        {"ip_address": row.ip_address, "failed_attempts": row.failed_count}
        for row in suspicious_ips_result.all()
    ]
    
    return {
        "period_days": days,
        "failed_logins": [
            {
                "timestamp": log.timestamp.isoformat(),
                "username": log.username,
                "ip_address": log.ip_address,
                "details": log.action_details
            }
            for log in failed_logins
        ],
        "unauthorized_attempts": [
            {
                "timestamp": log.timestamp.isoformat(),
                "username": log.username,
                "action": log.action.value if log.action else None,
                "target_type": log.target_type,
                "ip_address": log.ip_address
            }
            for log in unauthorized_attempts
        ],
        "suspicious_ips": suspicious_ips,
        "total_security_events": len(failed_logins) + len(unauthorized_attempts)
    }

@router.get("/compliance-report")
async def generate_compliance_report(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    current_user: User = Depends(require_roles("compliance", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate HIPAA compliance report for a date range.
    
    Includes all access logs, modifications, and security events.
    """
    
    # Total activity
    total_result = await db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(
            and_(
                AuditLog.timestamp >= start_date,
                AuditLog.timestamp <= end_date
            )
        )
    )
    total_activity = total_result.scalar()
    
    # Patient data access
    patient_access_result = await db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(
            and_(
                AuditLog.timestamp >= start_date,
                AuditLog.timestamp <= end_date,
                AuditLog.patient_uuid.isnot(None)
            )
        )
    )
    patient_data_access = patient_access_result.scalar()
    
    # Unique users
    unique_users_result = await db.execute(
        select(func.count(func.distinct(AuditLog.user_uuid)))
        .where(
            and_(
                AuditLog.timestamp >= start_date,
                AuditLog.timestamp <= end_date
            )
        )
    )
    unique_users = unique_users_result.scalar()
    
    # Security incidents
    security_incidents_result = await db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(
            and_(
                AuditLog.timestamp >= start_date,
                AuditLog.timestamp <= end_date,
                or_(
                    AuditLog.status == AuditStatus.ERROR,
                    AuditLog.status == AuditStatus.UNAUTHORIZED
                )
            )
        )
    )
    security_incidents = security_incidents_result.scalar()
    
    return {
        "report_period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        },
        "summary": {
            "total_activity": total_activity,
            "patient_data_access": patient_data_access,
            "unique_users": unique_users,
            "security_incidents": security_incidents
        },
        "compliance_status": "COMPLIANT" if security_incidents == 0 else "REVIEW_REQUIRED",
        "generated_by": current_user.username,
        "generated_at": datetime.now().isoformat()
    }
