from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.config import settings
from src.database.postgres import get_db
from src.models.user import User, UserRole
from src.models.audit_log import AuditLog, AuditAction, AuditStatus

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate JWT and return the authenticated user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        user_uuid: str = payload.get("user_id")
        if not username or not user_uuid:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user account",
        )

    # Refresh last_login timestamp
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def require_roles(*allowed_roles: str):
    """
    Role-based access dependency.

    Admin users bypass all role checks — they can access every endpoint.
    Usage:  Depends(require_roles("physician", "nurse"))
    """

    async def role_checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        # Admin is superuser — always allowed
        if current_user.role == UserRole.ADMIN:
            return current_user
        if current_user.role.value not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {', '.join(allowed_roles)}",
            )
        return current_user

    return role_checker


async def log_audit(
    db: AsyncSession,
    user: User,
    action: AuditAction,
    status: AuditStatus = AuditStatus.SUCCESS,
    action_details: Optional[str] = None,
    target_type: Optional[str] = None,
    target_uuid: Optional[str] = None,
    patient_uuid: Optional[str] = None,
    request: Optional[Request] = None,
):
    """Create an audit log entry. Never raises — audit failures must not break requests."""
    try:
        ip_address = None
        user_agent = None
        if request:
            ip_address = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent", "")[:255]

        audit_log = AuditLog(
            user_uuid=user.uuid,
            action=action,
            action_details=action_details,
            target_type=target_type,
            target_uuid=target_uuid,
            patient_uuid=patient_uuid,
            status=status,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(audit_log)
        await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Audit log write failed: {e}")