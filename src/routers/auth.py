"""
Authentication router — self-registration, staff registration, login, user management.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import timedelta
import logging

from src.database.postgres import get_db
from src.models.user import User, UserRole
from src.models.audit_log import AuditLog, AuditAction, AuditStatus
from src.schemas.auth import UserCreate, UserResponse, Token, UserLogin
from src.utils.security import hash_password, verify_password
from src.utils.auth import create_access_token, log_audit, require_roles
from src.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    """Self-registration (patient role only)."""
    if (await db.execute(select(User).where(User.username == user_data.username))).scalars().first():
        raise HTTPException(status_code=400, detail="Username already registered")
    if (await db.execute(select(User).where(User.email == user_data.email))).scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    if user_data.role and user_data.role != UserRole.PATIENT.value:
        raise HTTPException(status_code=400, detail="Self-registration only supports the patient role.")
    try:
        hashed_pwd = hash_password(user_data.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    new_user = User(username=user_data.username, email=user_data.email, hashed_password=hashed_pwd,
                    full_name=user_data.full_name, role=UserRole.PATIENT)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    logger.info(f"New patient user registered: {new_user.username}")
    return new_user


@router.post("/bootstrap/admin", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def bootstrap_admin(user_data: UserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    """Bootstrap first admin — only works if NO admin exists."""
    if (await db.execute(select(User).where(User.role == UserRole.ADMIN))).scalars().first():
        raise HTTPException(status_code=403, detail="Admin already exists. Use /api/auth/register/staff.")
    if (await db.execute(select(User).where(User.username == user_data.username))).scalars().first():
        raise HTTPException(status_code=400, detail="Username already registered")
    if (await db.execute(select(User).where(User.email == user_data.email))).scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    if not user_data.role or user_data.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=400, detail="Bootstrap endpoint only creates admin users. Set role='admin'.")
    try:
        hashed_pwd = hash_password(user_data.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    new_user = User(username=user_data.username, email=user_data.email, hashed_password=hashed_pwd,
                    full_name=user_data.full_name, role=UserRole.ADMIN, is_superuser=True)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    logger.warning(f"Bootstrap admin created: {new_user.username}")
    return new_user


@router.post("/register/staff", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_staff(user_data: UserCreate, request: Request,
                         db: AsyncSession = Depends(get_db),
                         current_user: User = Depends(require_roles("admin"))):
    """Register staff account (admin only)."""
    if (await db.execute(select(User).where(User.username == user_data.username))).scalars().first():
        raise HTTPException(status_code=400, detail="Username already registered")
    if (await db.execute(select(User).where(User.email == user_data.email))).scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    try:
        user_role = UserRole(user_data.role) if user_data.role else None
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {[r.value for r in UserRole]}")
    if not user_role or user_role == UserRole.PATIENT:
        raise HTTPException(status_code=400, detail="Staff registration requires a non-patient role.")
    try:
        hashed_pwd = hash_password(user_data.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    new_user = User(username=user_data.username, email=user_data.email, hashed_password=hashed_pwd,
                    full_name=user_data.full_name, role=user_role)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    await log_audit(db=db, user=current_user, action=AuditAction.OTHER, status=AuditStatus.SUCCESS,
                    action_details=f"Created staff user {new_user.username} with role {new_user.role.value}",
                    target_type="user", target_uuid=new_user.uuid, request=request)
    logger.info(f"Staff user registered: {new_user.username} ({new_user.role.value})")
    return new_user


@router.post("/login", response_model=Token)
async def login(request: Request, user_data: UserLogin, db: AsyncSession = Depends(get_db)):
    """JSON login — returns JWT access token."""
    result = await db.execute(select(User).where(User.username == user_data.username))
    user = result.scalars().first()
    if not user or not verify_password(user_data.password, user.hashed_password):
        if user:
            await log_audit(db=db, user=user, action=AuditAction.LOGIN,
                            status=AuditStatus.FAILED, action_details="Invalid password", request=request)
        raise HTTPException(status_code=401, detail="Invalid username or password",
                            headers={"WWW-Authenticate": "Bearer"})
    if not user.is_active:
        await log_audit(db=db, user=user, action=AuditAction.LOGIN,
                        status=AuditStatus.DENIED, action_details="Inactive account", request=request)
        raise HTTPException(status_code=400, detail="Inactive user account")
    access_token = create_access_token(
        data={"sub": user.username, "user_id": user.uuid},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    await log_audit(db=db, user=user, action=AuditAction.LOGIN, status=AuditStatus.SUCCESS, request=request)
    logger.info(f"User logged in: {user.username} ({user.role.value})")
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": UserResponse(uuid=user.uuid, username=user.username, email=user.email,
                             full_name=user.full_name, role=user.role.value,
                             is_active=user.is_active, created_at=user.created_at),
    }


@router.get("/users", response_model=list[UserResponse])
async def list_users(request: Request, current_user: User = Depends(require_roles("admin")),
                     db: AsyncSession = Depends(get_db)):
    """List all users (Admin only)."""
    return (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: str, request: Request, current_user: User = Depends(require_roles("admin")),
                   db: AsyncSession = Depends(get_db)):
    """Get a specific user by ID (Admin only)."""
    user = (await db.execute(select(User).where(User.uuid == user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/users/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(user_id: str, request: Request,
                          current_user: User = Depends(require_roles("admin")),
                          db: AsyncSession = Depends(get_db)):
    """Deactivate a user account (Admin only)."""
    user = (await db.execute(select(User).where(User.uuid == user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.uuid == current_user.uuid:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account.")
    user.is_active = False
    await db.commit()
    await db.refresh(user)
    await log_audit(db=db, user=current_user, action=AuditAction.OTHER,
                    action_details=f"Deactivated user {user.username}",
                    target_type="user", target_uuid=user_id, request=request)
    return user


@router.patch("/users/{user_id}/activate", response_model=UserResponse)
async def activate_user(user_id: str, request: Request,
                        current_user: User = Depends(require_roles("admin")),
                        db: AsyncSession = Depends(get_db)):
    """Reactivate a user account (Admin only)."""
    user = (await db.execute(select(User).where(User.uuid == user_id))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = True
    await db.commit()
    await db.refresh(user)
    await log_audit(db=db, user=current_user, action=AuditAction.OTHER,
                    action_details=f"Activated user {user.username}",
                    target_type="user", target_uuid=user_id, request=request)
    return user
