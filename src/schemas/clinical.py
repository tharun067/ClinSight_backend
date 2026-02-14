"""
Pydantic schemas for clinical data.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class LabResultCreate(BaseModel):
    """Create lab result."""
    test_name: str
    test_value: float
    unit: str
    test_date: datetime
    reference_range_low: Optional[float] = None
    reference_range_high: Optional[float] = None
    is_abnormal: Optional[str] = None
    notes: Optional[str] = None

class LabResultResponse(BaseModel):
    """Lab result response."""
    uuid: str
    test_name: str
    test_value: float
    unit: str
    test_date: datetime
    is_abnormal: Optional[str]

    class Config:
        from_attributes = True

class VitalSignCreate(BaseModel):
    """Create vital sign."""
    temperature: Optional[float] = None
    systolic_bp: Optional[float] = None
    diastolic_bp: Optional[float] = None
    heart_rate: Optional[float] = None
    respiratory_rate: Optional[float] = None
    oxygen_saturation: Optional[float] = None
    measurement_date: datetime

class VitalSignResponse(BaseModel):
    """Vital sign response."""
    uuid: str
    temperature: Optional[float]
    systolic_bp: Optional[float]
    diastolic_bp: Optional[float]
    heart_rate: Optional[float]
    respiratory_rate: Optional[float]
    measurement_date: datetime

    class Config:
        from_attributes = True

class ClinicalNoteCreate(BaseModel):
    """Create clinical note."""
    title: str
    content: str
    note_type: Optional[str] = None
    note_date: datetime

class ClinicalNoteResponse(BaseModel):
    """Clinical note response."""
    uuid: str
    title: str
    content: str
    note_type: Optional[str]
    note_date: datetime

    class Config:
        from_attributes = True

class DiagnosticQuery(BaseModel):
    """Diagnostic support query."""
    patient_id: Optional[str] = None
    query: Optional[str] = None
    clinical_notes: Optional[str] = None
    include_images: bool = True

class DiagnosticReportResponse(BaseModel):
    """Diagnostic report response."""
    uuid: str
    title: Optional[str]
    summary: str
    suggested_conditions: Optional[List[Dict[str, Any]]]
    evidence_summary: Optional[str]
    citations: Optional[List[Dict[str, Any]]]
    created_at: datetime

    class Config:
        from_attributes = True
