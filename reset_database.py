"""
Database reset utility script.
Drops all tables and recreates them from scratch.

Usage:
    python reset_database.py
"""
import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

from src.config import settings
from src.database.postgres import Base

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def reset_database():
    """Drop all tables and recreate them."""
    try:
        # Create async engine
        engine = create_async_engine(
            settings.SQLALCHEMY_DATABASE_URL,
            echo=False,
        )
        
        logger.warning("⚠️  RESETTING DATABASE - ALL DATA WILL BE LOST!")
        
        # Import all models to ensure they're registered with Base
        from src.models import (
            User, Patient, Document, ImagingStudy,
            LabResult, VitalSign, ClinicalNote,
            DiagnosticReport, AuditLog
        )
        
        async with engine.begin() as conn:
            # Drop all tables
            logger.info("Dropping all tables...")
            await conn.run_sync(Base.metadata.drop_all)
            logger.info("✅ All tables dropped successfully.")
            
            # Drop all custom ENUM types (PostgreSQL specific)
            logger.info("Dropping custom ENUM types...")
            enum_types = [
                'userrole', 'patientstatus', 'visittype', 'gender',
                'imagingmodality', 'imagingstatus', 'documenttype',
                'extractionstatus', 'auditaction', 'auditstatus'
            ]
            for enum_type in enum_types:
                try:
                    await conn.execute(text(f"DROP TYPE IF EXISTS {enum_type} CASCADE"))
                except Exception as e:
                    logger.warning(f"Could not drop type {enum_type}: {e}")
            logger.info("✅ ENUM types dropped successfully.")
            
            # Create all tables
            logger.info("Creating all tables...")
            await conn.run_sync(Base.metadata.create_all)
            logger.info("✅ All tables created successfully.")
        
        await engine.dispose()
        logger.info("✅ Database reset completed successfully!")
        
    except Exception as e:
        logger.error(f"❌ Error resetting database: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    asyncio.run(reset_database())
