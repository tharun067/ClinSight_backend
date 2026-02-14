"""
Authentication router for user registration and login.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import timedelta
import logging

from src.database.postgres import get_db
from src.models.user import User, UserRole
from src.models.audit_log import AuditLog, AuditAction, AuditStatus
from src.schemas.auth import UserCreate, UserResponse, Token, UserLogin
from src.utils.security import hash_password, verify_password
from src.utils.auth import create_access_token, log_audit
from src.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Register a new user account.
    
    - **username**: Unique username (3-50 characters)
    - **email**: Valid email address
    - **password**: Password (min 4 chars)
    - **full_name**: Full name
    - **role**: User role (defaults to 'patient')
    """
    # Check if username exists
    result = await db.execute(select(User).where(User.username == user_data.username))
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Check if email exists
    result = await db.execute(select(User).where(User.email == user_data.email))
    existing_email = result.scalars().first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Validate role
    try:
        user_role = UserRole(user_data.role) if user_data.role else UserRole.PATIENT
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {[r.value for r in UserRole]}"
        )
    
    # Create new user
    hashed_pwd = hash_password(user_data.password)
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_pwd,
        full_name=user_data.full_name,
        role=user_role
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    logger.info(f"New user registered: {new_user.username} (role: {new_user.role.value})")

    return new_user

@router.post("/token", response_model=Token)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """
    OAuth2 compatible token endpoint for user login.
    
    Returns JWT access token for authenticated requests.
    """
    # Authenticate user
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalars().first()
    
    if not user or not verify_password(form_data.password, user.hashed_password):
        # Log failed login attempt
        if user:
            await log_audit(
                db=db,
                user=user,
                action=AuditAction.LOGIN,
                status=AuditStatus.FAILED,
                action_details="Invalid password",
                request=request
            )
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        await log_audit(
            db=db,
            user=user,
            action=AuditAction.LOGIN,
            status=AuditStatus.DENIED,
            action_details="Inactive account",
            request=request
        )
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user account"
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "user_id": user.uuid},
        expires_delta=access_token_expires
    )
    
    # Log successful login
    await log_audit(
        db=db,
        user=user,
        action=AuditAction.LOGIN,
        status=AuditStatus.SUCCESS,
        request=request
    )
    
    logger.info(f"User logged in: {user.username} (role: {user.role.value})")

    user_response = UserResponse(
        uuid=user.uuid,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        is_active=user.is_active,
        created_at=user.created_at
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user_response
    }

@router.post("/login", response_model=Token)
async def login_json(
    request: Request,
    user_data: UserLogin,
    db: AsyncSession = Depends(get_db)
):
    """
    Alternative JSON-based login endpoint.
    
    Accepts username and password in JSON body.
    """
    # Authenticate user
    result = await db.execute(select(User).where(User.username == user_data.username))
    user = result.scalars().first()
    
    if not user or not verify_password(user_data.password, user.hashed_password):
        if user:
            await log_audit(
                db=db,
                user=user,
                action=AuditAction.LOGIN,
                status=AuditStatus.FAILED,
                action_details="Invalid password",
                request=request
            )
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    if not user.is_active:
        await log_audit(
            db=db,
            user=user,
            action=AuditAction.LOGIN,
            status=AuditStatus.DENIED,
            action_details="Inactive account",
            request=request
        )
        
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account"
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "user_id": user.uuid},
        expires_delta=access_token_expires
    )
    
    # Log successful login
    await log_audit(
        db=db,
        user=user,
        action=AuditAction.LOGIN,
        status=AuditStatus.SUCCESS,
        request=request
    )
    
    user_response = UserResponse(
        uuid=user.uuid,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        is_active=user.is_active,
        created_at=user.created_at
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user_response
    }
