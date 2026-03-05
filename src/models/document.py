"""
Document ORM model for patient file uploads.
Includes AI extraction status and results tracking.
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, BigInteger, Text, Enum as SQLEnum, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from enum import Enum
import uuid

from src.database.postgres import Base


class DocumentType(str, Enum):
    INSURANCE_CARD = "Insurance Card"
    ID_PASSPORT = "ID / Passport"
    LAB_RESULTS = "Lab Results"
    PRIOR_RECORDS = "Prior Records"
    REFERRAL_LETTER = "Referral Letter"
    XRAY_IMAGE = "X-ray Image"
    CT_IMAGE = "CT Image"
    MRI_IMAGE = "MRI Image"
    OTHER = "Other"


class ExtractionStatus(str, Enum):
    NOT_STARTED = "not_started"       # File type not supported for extraction
    PENDING = "pending"               # Queued, background task not yet run
    PROCESSING = "processing"         # Actively being processed
    COMPLETED = "completed"           # Extraction succeeded (even if 0 items found)
    FAILED = "failed"                 # Extraction error


class Document(Base):
    __tablename__ = "documents"

    uuid = Column(String(36), primary_key=True, unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    mime_type = Column(String(100), nullable=False)
    document_type = Column(SQLEnum(DocumentType), nullable=False, index=True)
    patient_uuid = Column(String(36), ForeignKey("patients.uuid"), nullable=False, index=True)
    uploaded_by = Column(String(36), ForeignKey("users.uuid"), nullable=False)
    upload_date = Column(DateTime(timezone=True), server_default=func.now())
    notes = Column(Text)

    # AI Extraction tracking
    extraction_status = Column(
        SQLEnum(ExtractionStatus),
        default=ExtractionStatus.NOT_STARTED,
        nullable=False,
        index=True,
    )
    extraction_started_at = Column(DateTime(timezone=True))
    extraction_completed_at = Column(DateTime(timezone=True))
    extraction_error = Column(Text)           # Error message if failed

    # Extraction results summary (JSON)
    # Example:
    # {
    #   "labs_extracted": 5,
    #   "vitals_extracted": 1,
    #   "imaging_extracted": 1,
    #   "lab_ids": ["uuid1", "uuid2", ...],
    #   "vital_ids": ["uuid3"],
    #   "imaging_ids": ["uuid4"],
    #   "raw_text_length": 3200
    # }
    extraction_results = Column(JSON)

    patient = relationship("Patient", back_populates="documents")
    uploader = relationship("User", foreign_keys=[uploaded_by], back_populates="uploaded_documents")

    def __repr__(self):
        return f"<Document(uuid={self.uuid}, type={self.document_type.value}, extraction={self.extraction_status.value})>"
