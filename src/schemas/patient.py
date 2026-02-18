"""
Pydantic schemas for patient management.
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime


class PatientCreate(BaseModel):
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
    full_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    status: Optional[str] = None
    chief_complaint: Optional[str] = None
    visit_type: Optional[str] = None


class PatientResponse(BaseModel):
    uuid: str
    mrn: str
    full_name: str
    date_of_birth: date
    gender: str
    phone: Optional[str]
    address: Optional[str]
    email: Optional[str]
    visit_type: Optional[str]
    chief_complaint: Optional[str]
    status: str
    last_activity: Optional[datetime]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class PatientListResponse(BaseModel):
    patients: List[PatientResponse]
    total: int
    page: int
    page_size: int