"""
Pydantic schemas for authentication.
"""
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional
from datetime import datetime

class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    full_name: str = Field(..., max_length=100)

class UserCreate(UserBase):
    """Schema for user registration."""
    password: str = Field(..., min_length=4, max_length=100)
    role: Optional[str] = "patient"  # Default role

class UserLogin(BaseModel):
    """Schema for user login."""
    username: str
    password: str

class UserResponse(UserBase):
    """Schema for user data in responses."""
    uuid: str
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    """JWT token response schema."""
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

class TokenData(BaseModel):
    """Data encoded in JWT token."""
    username: Optional[str] = None
    user_id: Optional[str] = None
