"""
Pydantic schemas for audit logs.
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class UserBasicInfo(BaseModel):
    uuid: str
    username: str
    full_name: Optional[str] = None
    role: str

    class Config:
        from_attributes = True


class AuditLogResponse(BaseModel):
    uuid: str
    timestamp: datetime
    action: str
    action_details: Optional[str] = None
    status: str
    target_type: Optional[str] = None
    patient_id: Optional[str] = Field(None, alias="patient_uuid")
    user_id: Optional[str] = Field(None, alias="user_uuid")
    target_id: Optional[str] = Field(None, alias="target_uuid")
    user: Optional[UserBasicInfo] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    class Config:
        from_attributes = True
        populate_by_name = True


class AuditLogListResponse(BaseModel):
    logs: List[AuditLogResponse]
    total: int
    page: int
    page_size: int
    total_pages: int