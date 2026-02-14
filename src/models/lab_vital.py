"""
Lab result and vital signs models.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, Float, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from src.database.postgres import Base

class LabResult(Base):
    """
    Lab result model for storing laboratory test results.
    """
    __tablename__ = "lab_results"

    uuid = Column(String(36), primary_key=True, unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    
    # Lab test information
    test_name = Column(String(100), nullable=False, index=True)  # e.g., "WBC", "CRP", "Platelets"
    test_value = Column(Float, nullable=False)
    unit = Column(String(50), nullable=False)  # e.g., "×10⁹/L", "mg/L"
    
    # Reference range
    reference_range_low = Column(Float)
    reference_range_high = Column(Float)
    
    # Flags
    is_abnormal = Column(String(10))  # "High", "Low", "Normal", "Critical"
    
    # Test metadata
    test_date = Column(DateTime(timezone=True), nullable=False)
    notes = Column(Text)
    
    # Patient association
    patient_uuid = Column(String(36), ForeignKey("patients.uuid"), nullable=False, index=True)
    
    # Ordering provider
    ordered_by = Column(String(36), ForeignKey("users.uuid"))
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    patient = relationship("Patient", back_populates="lab_results")

    def __repr__(self):
        return f"<LabResult(uuid={self.uuid}, test={self.test_name}, value={self.test_value} {self.unit})>"
    
    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.uuid,
            "test_name": self.test_name,
            "test_value": self.test_value,
            "unit": self.unit,
            "reference_range_low": self.reference_range_low,
            "reference_range_high": self.reference_range_high,
            "is_abnormal": self.is_abnormal,
            "test_date": self.test_date.isoformat() if self.test_date else None,
            "notes": self.notes,
            "patient_id": self.patient_uuid
        }


class VitalSign(Base):
    """
    Vital signs model for storing patient vital measurements.
    """
    __tablename__ = "vital_signs"

    uuid = Column(String(36), primary_key=True, unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    
    # Vital measurements
    temperature = Column(Float)  # °C
    temperature_unit = Column(String(10), default="°C")
    
    systolic_bp = Column(Float)  # mmHg
    diastolic_bp = Column(Float)  # mmHg
    
    heart_rate = Column(Float)  # bpm
    respiratory_rate = Column(Float)  # breaths/min
    
    oxygen_saturation = Column(Float)  # %
    
    # Additional measurements
    weight = Column(Float)  # kg
    height = Column(Float)  # cm
    bmi = Column(Float)
    
    # Measurement metadata
    measurement_date = Column(DateTime(timezone=True), nullable=False)
    notes = Column(Text)
    
    # Patient association
    patient_uuid = Column(String(36), ForeignKey("patients.uuid"), nullable=False, index=True)
    
    # Recorded by
    recorded_by = Column(String(36), ForeignKey("users.uuid"))
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    patient = relationship("Patient", back_populates="vital_signs")

    def __repr__(self):
        return f"<VitalSign(uuid={self.uuid}, patient={self.patient_uuid}, date={self.measurement_date})>"
    
    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.uuid,
            "temperature": self.temperature,
            "temperature_unit": self.temperature_unit,
            "systolic_bp": self.systolic_bp,
            "diastolic_bp": self.diastolic_bp,
            "heart_rate": self.heart_rate,
            "respiratory_rate": self.respiratory_rate,
            "oxygen_saturation": self.oxygen_saturation,
            "weight": self.weight,
            "height": self.height,
            "bmi": self.bmi,
            "measurement_date": self.measurement_date.isoformat() if self.measurement_date else None,
            "notes": self.notes,
            "patient_id": self.patient_uuid
        }
