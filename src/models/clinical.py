"""
Clinical note and diagnostic report models.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from src.database.postgres import Base

class ClinicalNote(Base):
    """
    Clinical note model for storing clinical documentation.
    """
    __tablename__ = "clinical_notes"

    uuid = Column(String(36), primary_key=True, unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    
    # Note content
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    note_type = Column(String(50))  # e.g., "Progress Note", "Consultation", "Discharge Summary"
    
    # Patient association
    patient_uuid = Column(String(36), ForeignKey("patients.uuid"), nullable=False, index=True)
    
    # Author
    author_uuid = Column(String(36), ForeignKey("users.uuid"), nullable=False)
    
    # Timestamps
    note_date = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    patient = relationship("Patient", back_populates="clinical_notes")
    author = relationship("User", back_populates="clinical_notes")

    def __repr__(self):
        return f"<ClinicalNote(uuid={self.uuid}, title={self.title}, patient={self.patient_uuid})>"
    
    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.uuid,
            "title": self.title,
            "content": self.content,
            "note_type": self.note_type,
            "note_date": self.note_date.isoformat() if self.note_date else None,
            "patient_id": self.patient_uuid,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }


class DiagnosticReport(Base):
    """
    AI-generated diagnostic report model.
    Stores diagnostic support outputs with citations.
    """
    __tablename__ = "diagnostic_reports"

    uuid = Column(String(36), primary_key=True, unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    
    # Report content
    title = Column(String(255))
    summary = Column(Text, nullable=False)
    
    # Input data references
    query = Column(Text)  # Original clinical query
    
    # AI-generated output
    suggested_conditions = Column(JSON)  # List of conditions with confidence scores
    evidence_summary = Column(Text)
    citations = Column(JSON)  # List of PubMed IDs and SNOMED codes
    
    # Confidence and metadata
    overall_confidence = Column(String(20))  # "High", "Medium", "Low"
    
    # Model information
    model_name = Column(String(100))
    model_version = Column(String(50))
    generation_time_ms = Column(String(20))
    
    # Patient association
    patient_uuid = Column(String(36), ForeignKey("patients.uuid"), nullable=False, index=True)
    
    # Generated for (physician)
    generated_for_uuid = Column(String(36), ForeignKey("users.uuid"), nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    patient = relationship("Patient", back_populates="diagnostic_reports")

    def __repr__(self):
        return f"<DiagnosticReport(uuid={self.uuid}, patient={self.patient_uuid})>"
    
    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.uuid,
            "title": self.title,
            "summary": self.summary,
            "query": self.query,
            "suggested_conditions": self.suggested_conditions,
            "evidence_summary": self.evidence_summary,
            "citations": self.citations,
            "overall_confidence": self.overall_confidence,
            "model_name": self.model_name,
            "generation_time_ms": self.generation_time_ms,
            "patient_id": self.patient_uuid,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
