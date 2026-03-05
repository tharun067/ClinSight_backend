from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List
import os
import secrets


class Settings(BaseSettings):
    """Application configuration with environment variable support."""

    # Application
    APP_NAME: str = "ClinSight - Medical Diagnosis Support System"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Security — SECRET_KEY has NO default; must be set via env var
    SECRET_KEY: str = Field(
        ...,
        min_length=32,
        description="JWT secret key — set via SECRET_KEY env var, minimum 32 characters",
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    
    DATABASE_URL: str = Field(default="", description="Full database URL for production")

    @property
    def SQLALCHEMY_DATABASE_URL(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

    # Neo4j
    NEO4J_URI: str = "neo4j+s://77ff0dfd.databases.neo4j.io"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = Field(..., description="Neo4j password")

    # Vector Database (FAISS)
    VECTOR_DIMENSION: int = 768
    FAISS_INDEX_PATH: str = "./data/faiss_index"

    # Google Gemini
    GOOGLE_API_KEY: str = Field(default="", description="Google API key for Gemini")
    GEMINI_MODEL: str = "gemini-1.5-pro"
    GEMINI_TEMPERATURE: float = 0.3
    GEMINI_MAX_TOKENS: int = 2048

    # Groq
    GROQ_API_KEY: str = Field(default="", description="Groq API key")
    GROQ_MODEL: str = "mixtral-8x7b-32768"
    GROQ_TEMPERATURE: float = 0.3
    GROQ_MAX_TOKENS: int = 2048

    # File uploads
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10 MB
    ALLOWED_EXTENSIONS: set = {".dcm", ".png", ".jpg", ".jpeg", ".pdf", ".txt"}
    DOCUMENT_TYPES: List[str] = [
        "Insurance Card",
        "ID / Passport",
        "Lab Results",
        "Prior Records",
        "Referral Letter",
        "Other",
    ]

    # Embedding models
    IMAGE_EMBEDDING_MODEL: str = (
        "microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224"
    )
    TEXT_EMBEDDING_MODEL: str = "dmis-lab/biobert-v1.1"
    EMBEDDING_BATCH_SIZE: int = 32

    # Retrieval
    TOP_K_VECTOR: int = 5
    TOP_K_GRAPH: int = 10
    SIMILARITY_THRESHOLD: float = 0.7

    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "https://clinsight1.netlify.app"
    ]

    ROLES: List[str] = [
        "nurse", "physician",
        "admin", "patient",
    ]
    PATIENT_STATUS: List[str] = ["Active", "Pending", "Discharged"]
    VISIT_TYPES: List[str] = ["Outpatient", "Emergency", "Inpatient"]
    GENDER_OPTIONS: List[str] = ["Male", "Female", "Other", "Prefer not to say"]
    IMAGING_MODALITIES: List[str] = ["X-ray", "CT", "MRI", "Ultrasound", "PET", "Mammography"]
    LAB_TESTS: List[str] = ["WBC", "CRP", "Platelets", "Hemoglobin", "Glucose", "Creatinine", "ALT", "AST"]

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

    @field_validator("UPLOAD_DIR", "FAISS_INDEX_PATH")
    def create_directories(cls, v):
        os.makedirs(v, exist_ok=True)
        return v


settings = Settings()