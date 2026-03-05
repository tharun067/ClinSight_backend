import uuid
from fastapi import HTTPException, status


def validate_uuid(value: str, field_name: str = "ID") -> str:
    """
    Validate that a string is a valid UUID format.
    
    Args:
        value: The string to validate
        field_name: Human-readable name of the field (for error messages)
    
    Returns:
        The validated UUID string
        
    Raises:
        HTTPException: If the UUID is invalid
    """
    # Check for common frontend issues
    if value in ("undefined", "null", ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}: '{value}'. Please ensure you have a valid patient record linked to your account. Use /api/patients/my-record to view your record or /api/patients/link-my-record to link an existing medical record."
        )
    
    # Validate UUID format
    try:
        uuid.UUID(value)
        return value
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name} format: '{value}'. Expected a valid UUID."
        )
