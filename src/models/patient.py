"""
Patient model for ClinSight medical records system.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, Enum as SQLEnum, Text, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from enum import Enum
import uuid

from src.database.postgres import Base

class PatientStatus(str, Enum):
    """Patient status enumeration."""
    ACTIVE = "Active"
    PENDING = "Pending"
    DISCHARGED = "Discharged"

class VisitType(str, Enum):
    """Visit type enumeration."""
    OUTPATIENT = "Outpatient"
    EMERGENCY = "Emergency"
    INPATIENT = "Inpatient"

class Gender(str, Enum):
    """Gender enumeration."""
    MALE = "Male"
    FEMALE = "Female"
    OTHER = "Other"
    PREFER_NOT_TO_SAY = "Prefer not to say"

class Patient(Base):
    """
    Patient model for medical records.
    Stores demographics, visit information, and clinical data.
    """
    __tablename__ = "patients"

    uuid = Column(String(36), primary_key=True, unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    
    # Medical Record Number (unique identifier)
    mrn = Column(String(20), unique=True, index=True, nullable=False)
    
    # Demographics
    full_name = Column(String(100), nullable=False, index=True)
    date_of_birth = Column(Date, nullable=False)
    gender = Column(SQLEnum(Gender), nullable=False)
    
    # Contact information
    phone = Column(String(20))
    address = Column(Text)
    email = Column(String(100))
    
    # Visit information
    visit_type = Column(SQLEnum(VisitType))
    chief_complaint = Column(Text)
    visit_date = Column(DateTime(timezone=True))
    
    # Status
    status = Column(SQLEnum(PatientStatus), default=PatientStatus.PENDING, index=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_activity = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Link to user account (for patient portal)
    user_uuid = Column(String(36), ForeignKey("users.uuid"), unique=True, nullable=True)
    
    # Registered by (intake officer)
    registered_by = Column(String(36), ForeignKey("users.uuid"))
    
    # Relationships
    user = relationship("User", foreign_keys=[user_uuid], back_populates="patient_record")
    registrar = relationship("User", foreign_keys=[registered_by])
    
    documents = relationship("Document", back_populates="patient", cascade="all, delete-orphan")
    imaging_studies = relationship("ImagingStudy", back_populates="patient", cascade="all, delete-orphan")
    lab_results = relationship("LabResult", back_populates="patient", cascade="all, delete-orphan")
    vital_signs = relationship("VitalSign", back_populates="patient", cascade="all, delete-orphan")
    clinical_notes = relationship("ClinicalNote", back_populates="patient", cascade="all, delete-orphan")
    diagnostic_reports = relationship("DiagnosticReport", back_populates="patient", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Patient(uuid={self.uuid}, mrn={self.mrn}, name={self.full_name}, status={self.status.value})>"
    
    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.uuid,
            "mrn": self.mrn,
            "name": self.full_name,
            "dob": self.date_of_birth.isoformat() if self.date_of_birth else None,
            "gender": self.gender.value if self.gender else None,
            "phone": self.phone,
            "address": self.address,
            "email": self.email,
            "visit_type": self.visit_type.value if self.visit_type else None,
            "chief_complaint": self.chief_complaint,
            "visit_date": self.visit_date.isoformat() if self.visit_date else None,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None
        }
