"""
PostgreSQL async database connection and session management.
"""
from typing import AsyncGenerator
import logging
import asyncpg

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base

from src.config import settings

logger = logging.getLogger(__name__)

# async engine
engine: AsyncEngine = create_async_engine(
    settings.SQLALCHEMY_DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
)

# async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession
)

# Base class for declarative models
Base = declarative_base()

async def init_db() -> None:
    """Initialize database by creating all tables."""
    try:
        logger.info("Initializing PostgreSQL database...")
        
        # First, create the database if it doesn't exist
        await create_database_if_not_exists()
        
        # Import all models to ensure they're registered
        from src.models import (
            User, Patient, Document, ImagingStudy,
            LabResult, VitalSign, ClinicalNote,
            DiagnosticReport, AuditLog
        )

        async with engine.begin() as conn:
            # Drop all tables (for development only)
            # await conn.run_sync(Base.metadata.drop_all)
            
            # Create all tables
            await conn.run_sync(Base.metadata.create_all)
        
        logger.info("Database initialized successfully.")
        
        # Create default users
        await create_default_users()
        
    except Exception as e:
        logger.error(f"Error initializing database: {e}", exc_info=True)
        raise

async def create_database_if_not_exists() -> None:
    """Create the database if it doesn't exist."""
    try:
        # Connect to the default 'postgres' database to create our database
        connection = await asyncpg.connect(
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            database='postgres'
        )
        
        try:
            # Check if database exists
            databases = await connection.fetch(
                "SELECT datname FROM pg_database WHERE datname = $1;",
                settings.POSTGRES_DB
            )
            
            if not databases:
                logger.info(f"Creating database '{settings.POSTGRES_DB}'...")
                await connection.execute(f'CREATE DATABASE {settings.POSTGRES_DB};')
                logger.info(f"Database '{settings.POSTGRES_DB}' created successfully.")
            else:
                logger.info(f"Database '{settings.POSTGRES_DB}' already exists.")
        finally:
            await connection.close()
    except Exception as e:
        logger.error(f"Error creating database: {e}", exc_info=True)
        raise

async def close_db() -> None:
    """Close async engine connections."""
    try:
        await engine.dispose()
        logger.info("Database connections closed successfully.")
    except Exception as e:
        logger.error(f"Error closing database connections: {e}")
        raise

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency injection for async database sessions.
    
    Usage (FastAPI):
        async def endpoint(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def create_default_users():
    """Create default demo users for testing."""
    from src.models import User, UserRole
    from src.utils.security import hash_password
    from sqlalchemy import select
    
    default_users = [
        {
            "username": "intake",
            "email": "jane.smith@hospital.demo",
            "full_name": "Jane Smith",
            "password": "demo",
            "role": UserRole.INTAKE
        },
        {
            "username": "nurse",
            "email": "maria.lopez@hospital.demo",
            "full_name": "Maria Lopez",
            "password": "demo",
            "role": UserRole.NURSE
        },
        {
            "username": "radiologist",
            "email": "david.chen@hospital.demo",
            "full_name": "David Chen",
            "password": "demo",
            "role": UserRole.RADIOLOGIST
        },
        {
            "username": "physician",
            "email": "sarah.williams@hospital.demo",
            "full_name": "Sarah Williams",
            "password": "demo",
            "role": UserRole.PHYSICIAN
        },
        {
            "username": "admin",
            "email": "admin@hospital.demo",
            "full_name": "Admin User",
            "password": "demo",
            "role": UserRole.ADMIN,
            "is_superuser": True
        },
        {
            "username": "compliance",
            "email": "compliance@hospital.demo",
            "full_name": "Audit User",
            "password": "demo",
            "role": UserRole.COMPLIANCE
        }
    ]
    
    async with AsyncSessionLocal() as session:
        for user_data in default_users:
            # Check if user already exists
            result = await session.execute(
                select(User).where(User.username == user_data["username"])
            )
            existing_user = result.scalars().first()
            
            if not existing_user:
                hashed_pwd = hash_password(user_data["password"])
                new_user = User(
                    username=user_data["username"],
                    email=user_data["email"],
                    full_name=user_data["full_name"],
                    hashed_password=hashed_pwd,
                    role=user_data["role"],
                    is_superuser=user_data.get("is_superuser", False),
                    is_active=True
                )
                session.add(new_user)
        
        await session.commit()
        logger.info("Default users created successfully.")
