"""
Pydantic schemas for document management.
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class DocumentUploadResponse(BaseModel):
    """Response after document upload."""
    uuid: str
    filename: str
    document_type: str
    file_size: int
    patient_id: str = Field(..., alias="patient_uuid")
    upload_date: datetime

    class Config:
        from_attributes = True
        populate_by_name = True

class DocumentResponse(BaseModel):
    """Document details response."""
    uuid: str
    original_filename: str
    document_type: str
    file_size: int
    patient_id: str = Field(..., alias="patient_uuid")
    upload_date: datetime
    notes: Optional[str]

    class Config:
        from_attributes = True
        populate_by_name = True
