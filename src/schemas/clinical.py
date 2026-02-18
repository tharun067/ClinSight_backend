"""
Pydantic schemas for clinical data.
Fixed: VitalSignCreate now includes weight and height fields.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class LabResultCreate(BaseModel):
    test_name: str
    test_value: float
    unit: str
    test_date: datetime
    reference_range_low: Optional[float] = None
    reference_range_high: Optional[float] = None
    is_abnormal: Optional[str] = None
    notes: Optional[str] = None


class LabResultResponse(BaseModel):
    uuid: str
    test_name: str
    test_value: float
    unit: str
    test_date: datetime
    reference_range_low: Optional[float]
    reference_range_high: Optional[float]
    is_abnormal: Optional[str]
    notes: Optional[str]
    patient_id: Optional[str] = Field(None, alias="patient_uuid")

    class Config:
        from_attributes = True
        populate_by_name = True


class VitalSignCreate(BaseModel):
    """Create vital sign — all measurements optional except date."""
    measurement_date: datetime
    temperature: Optional[float] = None
    temperature_unit: Optional[str] = "°C"
    systolic_bp: Optional[float] = None
    diastolic_bp: Optional[float] = None
    heart_rate: Optional[float] = None
    respiratory_rate: Optional[float] = None
    oxygen_saturation: Optional[float] = None
    weight: Optional[float] = None    # kg
    height: Optional[float] = None    # cm
    notes: Optional[str] = None


class VitalSignResponse(BaseModel):
    uuid: str
    measurement_date: datetime
    temperature: Optional[float]
    temperature_unit: Optional[str]
    systolic_bp: Optional[float]
    diastolic_bp: Optional[float]
    heart_rate: Optional[float]
    respiratory_rate: Optional[float]
    oxygen_saturation: Optional[float]
    weight: Optional[float]
    height: Optional[float]
    bmi: Optional[float]
    notes: Optional[str]
    patient_id: Optional[str] = Field(None, alias="patient_uuid")

    class Config:
        from_attributes = True
        populate_by_name = True


class ClinicalNoteCreate(BaseModel):
    title: str
    content: str
    note_type: Optional[str] = None
    note_date: datetime


class ClinicalNoteResponse(BaseModel):
    uuid: str
    title: str
    content: str
    note_type: Optional[str]
    note_date: datetime
    patient_id: Optional[str] = Field(None, alias="patient_uuid")

    class Config:
        from_attributes = True
        populate_by_name = True


class DiagnosticQuery(BaseModel):
    patient_id: Optional[str] = None
    query: Optional[str] = None
    clinical_notes: Optional[str] = None
    include_images: bool = True


class DiagnosticReportResponse(BaseModel):
    uuid: str
    title: Optional[str]
    summary: str
    suggested_conditions: Optional[List[Dict[str, Any]]]
    evidence_summary: Optional[str]
    citations: Optional[List[Dict[str, Any]]]
    created_at: datetime

    class Config:
        from_attributes = True