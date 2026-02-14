"""
Pydantic schemas for audit logs.
"""
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class AuditLogResponse(BaseModel):
    """Audit log response."""
    uuid: str
    timestamp: datetime
    action: str
    action_details: Optional[str]
    status: str
    target_type: Optional[str]
    patient_id: Optional[str]

    class Config:
        from_attributes = True

class AuditLogListResponse(BaseModel):
    """Paginated audit log list."""
    logs: list[AuditLogResponse]
    total: int
    page: int
    page_size: int
