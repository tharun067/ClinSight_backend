"""
Audit log model for compliance and activity tracking.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from enum import Enum
import uuid

from src.database.postgres import Base

class AuditAction(str, Enum):
    """Audit action types."""
    LOGIN = "Login"
    LOGOUT = "Logout"
    VIEW_PATIENT = "Viewed patient"
    CREATE_PATIENT = "Created patient"
    UPDATE_PATIENT = "Updated patient"
    UPLOAD_DOCUMENT = "Uploaded document"
    VIEW_IMAGING = "Viewed imaging"
    ADD_IMAGING_NOTE = "Added imaging note"
    VIEW_LABS = "Viewed labs"
    ENTER_LABS = "Entered labs"
    ENTER_VITALS = "Entered vitals"
    VIEW_CLINICAL_NOTES = "Viewed clinical notes"
    ADD_CLINICAL_NOTE = "Added clinical note"
    GENERATE_DIAGNOSTIC = "Generated diagnostic"
    VIEW_DIAGNOSTIC = "Viewed diagnostic"
    DOWNLOAD_DOCUMENT = "Downloaded document"
    VIEW_AUDIT_LOG = "Viewed audit log"
    OTHER = "Other"

class AuditStatus(str, Enum):
    """Audit status."""
    SUCCESS = "Success"
    FAILED = "Failed"
    DENIED = "Access Denied"

class AuditLog(Base):
    """
    Audit log model for tracking all user activities.
    Used for compliance, security, and activity monitoring.
    """
    __tablename__ = "audit_logs"

    uuid = Column(String(36), primary_key=True, unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    
    # User who performed the action
    user_uuid = Column(String(36), ForeignKey("users.uuid"), nullable=False, index=True)
    
    # Action information
    action = Column(SQLEnum(AuditAction), nullable=False, index=True)
    action_details = Column(Text)  # Additional context
    
    # Target information (if applicable)
    target_type = Column(String(50))  # "patient", "document", "imaging", etc.
    target_uuid = Column(String(36), index=True)  # UUID of the target resource
    patient_uuid = Column(String(36), ForeignKey("patients.uuid"), index=True)  # If action relates to a patient
    
    # Status
    status = Column(SQLEnum(AuditStatus), default=AuditStatus.SUCCESS, index=True)
    
    # Network information
    ip_address = Column(String(45))  # IPv4 or IPv6
    user_agent = Column(String(255))
    
    # Timestamp
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    
    # Relationships
    user = relationship("User", back_populates="audit_logs")

    def __repr__(self):
        return f"<AuditLog(uuid={self.uuid}, user={self.user_uuid}, action={self.action.value})>"
    
    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.uuid,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "user_id": self.user_uuid,
            "action": self.action.value,
            "action_details": self.action_details,
            "target_type": self.target_type,
            "target_id": self.target_uuid,
            "patient_id": self.patient_uuid,
            "status": self.status.value,
            "ip_address": self.ip_address
        }
