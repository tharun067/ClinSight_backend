"""
Document ORM model for patient file uploads.
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, BigInteger, Text, Enum as SQLEnum
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

    patient = relationship("Patient", back_populates="documents")
    uploader = relationship("User", foreign_keys=[uploaded_by], back_populates="uploaded_documents")

    def __repr__(self):
        return f"<Document(uuid={self.uuid}, type={self.document_type.value})>"

    def to_dict(self):
        return {
            "id": self.uuid, "filename": self.original_filename,
            "document_type": self.document_type.value, "file_size": self.file_size,
            "upload_date": self.upload_date.isoformat() if self.upload_date else None,
            "patient_id": self.patient_uuid, "notes": self.notes,
        }
