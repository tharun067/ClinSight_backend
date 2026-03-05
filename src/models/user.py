"""
User model with role-based access control for ClinSight.
Roles: physician, admin, nurse, patient
"""

from sqlalchemy import Column, String, Boolean, DateTime, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from enum import Enum
import uuid

from src.database.postgres import Base

class UserRole(str, Enum):
    """User role enumeration."""
    NURSE = "nurse"
    PHYSICIAN = "physician"
    ADMIN = "admin"
    PATIENT = "patient"

class User(Base):
    """
    User model for authentication and authorization.
    Four roles: physician (view/update all), admin (full access),
    nurse (add patients/reports), patient (own data only).
    """
    __tablename__ = "users"

    uuid = Column(String(36), primary_key=True, unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    
    role = Column(SQLEnum(UserRole), nullable=False, default=UserRole.PATIENT, index=True)
    
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_login = Column(DateTime(timezone=True))
    
    uploaded_documents = relationship("Document", foreign_keys="[Document.uploaded_by]", back_populates="uploader")
    clinical_notes = relationship("ClinicalNote", back_populates="author")
    imaging_interpretations = relationship("ImagingStudy", foreign_keys="[ImagingStudy.interpreted_by]", back_populates="interpreter")
    audit_logs = relationship("AuditLog", back_populates="user")
    patient_record = relationship("Patient", uselist=False, back_populates="user", cascade="all, delete-orphan", foreign_keys="[Patient.user_uuid]")

    def __repr__(self):
        return f"<User(uuid={self.uuid}, username={self.username}, role={self.role.value})>"
    
    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission based on their role."""
        role_permissions = {
            UserRole.NURSE: {
                "register_patient", "add_patient", "view_worklist",
                "enter_labs", "enter_vitals", "add_reports",
                "view_clinical_notes", "view_patient_overview",
                "upload_documents", "view_imaging", "add_clinical_notes",
            },
            UserRole.PHYSICIAN: {
                "view_worklist", "view_patient_overview", "view_all_patients",
                "update_patient_records", "diagnostic_support",
                "view_imaging", "view_labs", "view_clinical_notes",
                "add_clinical_notes", "update_clinical_notes",
                "delete_clinical_notes", "delete_labs", "delete_imaging",
            },
            UserRole.ADMIN: {
                "view_audit_logs", "manage_users", "view_system_stats",
                "register_patient", "add_patient", "view_worklist",
                "enter_labs", "enter_vitals", "add_reports",
                "view_clinical_notes", "view_patient_overview",
                "upload_documents", "view_imaging", "view_all_patients",
                "update_patient_records", "diagnostic_support",
                "view_labs", "add_clinical_notes", "update_clinical_notes",
                "delete_clinical_notes", "delete_labs", "delete_imaging",
                "delete_patients", "delete_documents",
            },
            UserRole.PATIENT: {
                "view_own_record", "add_own_records", "update_own_profile",
                "upload_own_documents", "view_own_labs", "view_own_vitals",
                "add_own_labs", "add_own_vitals", "add_own_notes",
                "view_own_imaging",
            },
        }
        return permission in role_permissions.get(self.role, set())
