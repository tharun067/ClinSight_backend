"""
Pydantic schemas for imaging studies.
"""
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class ImagingStudyCreate(BaseModel):
    """Create imaging study."""
    study_date: datetime
    modality: str
    body_part: str
    description: Optional[str] = None

class ImagingStudyUpdate(BaseModel):
    """Update imaging study."""
    findings: Optional[str] = None
    impression: Optional[str] = None
    status: Optional[str] = None

class ImagingStudyResponse(BaseModel):
    """Imaging study response."""
    uuid: str
    study_date: datetime
    modality: str
    body_part: str
    status: str
    findings: Optional[str]
    impression: Optional[str]

    class Config:
        from_attributes = True
