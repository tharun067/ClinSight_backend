"""
Pydantic schemas for patient management.
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime

class PatientCreate(BaseModel):
    """Schema for creating a patient."""
    full_name: str = Field(..., min_length=2, max_length=100)
    date_of_birth: date
    gender: str
    phone: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    visit_type: Optional[str] = None
    chief_complaint: Optional[str] = None
    visit_date: Optional[datetime] = None

class PatientUpdate(BaseModel):
    """Schema for updating patient data."""
    full_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    status: Optional[str] = None

class PatientResponse(BaseModel):
    """Schema for patient response."""
    uuid: str
    mrn: str
    full_name: str
    date_of_birth: date
    gender: str
    phone: Optional[str]
    address: Optional[str]
    status: str
    last_activity: datetime

    class Config:
        from_attributes = True

class PatientListResponse(BaseModel):
    """Paginated patient list."""
    patients: list[PatientResponse]
    total: int
    page: int
    page_size: int
