"""
Imaging study model for radiology data.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from enum import Enum
import uuid

from src.database.postgres import Base

class ImagingModality(str, Enum):
    """Imaging modality enumeration."""
    XRAY = "X-ray"
    CT = "CT"
    MRI = "MRI"
    ULTRASOUND = "Ultrasound"
    PET = "PET"
    MAMMOGRAPHY = "Mammography"

class ImagingStatus(str, Enum):
    """Imaging study status."""
    PENDING = "Pending interpretation"
    IN_PROGRESS = "In progress"
    COMPLETE = "Complete"

class ImagingStudy(Base):
    """
    Imaging study model for radiology records.
    Stores imaging metadata and interpretations.
    """
    __tablename__ = "imaging_studies"

    uuid = Column(String(36), primary_key=True, unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    
    # Study information
    study_date = Column(DateTime(timezone=True), nullable=False)
    modality = Column(SQLEnum(ImagingModality), nullable=False, index=True)
    body_part = Column(String(100), nullable=False)
    
    # Study description
    description = Column(Text)
    
    # Status
    status = Column(SQLEnum(ImagingStatus), default=ImagingStatus.PENDING, index=True)
    
    # Findings and interpretation
    findings = Column(Text)
    impression = Column(Text)
    
    # Patient association
    patient_uuid = Column(String(36), ForeignKey("patients.uuid"), nullable=False, index=True)
    
    # Radiologist who interpreted
    interpreted_by = Column(String(36), ForeignKey("users.uuid"))
    interpretation_date = Column(DateTime(timezone=True))
    
    # Image file reference (if stored)
    image_path = Column(String(500))
    
    # DICOM metadata
    series_uid = Column(String(100))
    study_uid = Column(String(100))
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    patient = relationship("Patient", back_populates="imaging_studies")
    interpreter = relationship("User", foreign_keys=[interpreted_by], back_populates="imaging_interpretations")

    def __repr__(self):
        return f"<ImagingStudy(uuid={self.uuid}, modality={self.modality.value}, patient={self.patient_uuid})>"
    
    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.uuid,
            "study_date": self.study_date.isoformat() if self.study_date else None,
            "modality": self.modality.value,
            "body_part": self.body_part,
            "description": self.description,
            "status": self.status.value,
            "findings": self.findings,
            "impression": self.impression,
            "patient_id": self.patient_uuid,
            "interpretation_date": self.interpretation_date.isoformat() if self.interpretation_date else None
        }
