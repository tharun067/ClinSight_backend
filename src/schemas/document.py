"""
Pydantic schemas for document management.
"""
from pydantic import BaseModel, Field
from typing import Optional, Any, Dict
from datetime import datetime


class DocumentUploadResponse(BaseModel):
    uuid: str
    filename: str
    original_filename: str
    document_type: str
    file_size: int
    patient_id: str = Field(..., alias="patient_uuid")
    upload_date: datetime
    extraction_status: str
    notes: Optional[str] = None

    class Config:
        from_attributes = True
        populate_by_name = True


class ExtractionResultsDetail(BaseModel):
    """Detailed extraction results returned from the status endpoint."""
    labs_extracted: int = 0
    vitals_extracted: int = 0
    imaging_extracted: int = 0
    lab_ids: list[str] = []
    vital_ids: list[str] = []
    imaging_ids: list[str] = []
    raw_text_length: int = 0
    ai_provider: Optional[str] = None
    extraction_warning: Optional[str] = None


class DocumentExtractionResponse(BaseModel):
    """Full extraction status + results for a document."""
    document_id: str
    original_filename: str
    document_type: str
    patient_id: str
    upload_date: datetime
    extraction_status: str                          # pending / processing / completed / failed / not_started
    extraction_started_at: Optional[datetime] = None
    extraction_completed_at: Optional[datetime] = None
    extraction_error: Optional[str] = None
    results: Optional[ExtractionResultsDetail] = None

    class Config:
        from_attributes = True


class DocumentResponse(BaseModel):
    uuid: str
    original_filename: str
    document_type: str
    file_size: int
    patient_id: str = Field(..., alias="patient_uuid")
    upload_date: datetime
    notes: Optional[str] = None
    extraction_status: str
    extraction_completed_at: Optional[datetime] = None
    extraction_results: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True
        populate_by_name = True
