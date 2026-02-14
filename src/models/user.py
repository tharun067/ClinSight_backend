"""
User model with role-based access control for ClinSight.
"""

from sqlalchemy import Column, String, Boolean, DateTime, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from enum import Enum
import uuid

from src.database.postgres import Base

class UserRole(str, Enum):
    """User role enumeration."""
    INTAKE = "intake"
    NURSE = "nurse"
    RADIOLOGIST = "radiologist"
    PHYSICIAN = "physician"
    ADMIN = "admin"
    COMPLIANCE = "compliance"
    PATIENT = "patient"

class User(Base):
    """
    User model for authentication and authorization.
    Supports multiple healthcare roles with specific permissions.
    """
    __tablename__ = "users"

    uuid = Column(String(36), primary_key=True, unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    
    # Role-based access control
    role = Column(SQLEnum(UserRole), nullable=False, default=UserRole.PATIENT, index=True)
    
    # Status flags
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_login = Column(DateTime(timezone=True))
    
    # Relationships
    uploaded_documents = relationship("Document", foreign_keys="[Document.uploaded_by]", back_populates="uploader")
    clinical_notes = relationship("ClinicalNote", back_populates="author")
    imaging_interpretations = relationship("ImagingStudy", foreign_keys="[ImagingStudy.interpreted_by]", back_populates="interpreter")
    audit_logs = relationship("AuditLog", back_populates="user")
    
    # Patient-specific relationship (for patient role users)
    patient_record = relationship("Patient", uselist=False, back_populates="user", cascade="all, delete-orphan", foreign_keys="[Patient.user_uuid]")

    def __repr__(self):
        return f"<User(uuid={self.uuid}, username={self.username}, role={self.role.value})>"
    
    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission based on their role."""
        role_permissions = {
            UserRole.INTAKE: {
                "register_patient", "upload_documents", "view_worklist"
            },
            UserRole.NURSE: {
                "view_worklist", "enter_labs", "enter_vitals", 
                "view_clinical_notes", "view_patient_overview"
            },
            UserRole.RADIOLOGIST: {
                "view_worklist", "review_imaging", "add_imaging_notes",
                "view_patient_overview", "view_labs"
            },
            UserRole.PHYSICIAN: {
                "view_worklist", "view_patient_overview", "diagnostic_support",
                "view_imaging", "view_labs", "view_clinical_notes", "add_clinical_notes"
            },
            UserRole.ADMIN: {
                "view_audit_logs", "manage_users", "view_system_stats"
            },
            UserRole.COMPLIANCE: {
                "view_audit_logs", "view_system_overview"
            },
            UserRole.PATIENT: {
                "view_own_record", "update_profile"
            }
        }
        
        return permission in role_permissions.get(self.role, set())
