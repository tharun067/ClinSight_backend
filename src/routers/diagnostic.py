"""
Diagnostic support router (AI Clinical Decision Support).
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import logging

from src.database.postgres import get_db
from src.models.user import User, UserRole
from src.models.patient import Patient
from src.models.clinical import DiagnosticReport
from src.schemas.clinical import DiagnosticQuery, DiagnosticReportResponse
from src.utils.auth import require_roles, log_audit
from src.models.audit_log import AuditAction, AuditStatus
from src.services.diagnostic import get_diagnostic_service

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request body schemas ──────────────────────────────────────────────────────

class ImageAnalysisRequest(BaseModel):
    image_path: str
    query: Optional[str] = None


class SummarizeNoteRequest(BaseModel):
    note_text: str
    max_length: int = 200


class ExtractEntitiesRequest(BaseModel):
    text: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/generate", response_model=DiagnosticReportResponse)
async def generate_diagnostic_report(
    query_data: DiagnosticQuery,
    request: Request,
    current_user: User = Depends(require_roles("physician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate AI-powered diagnostic report (Physician or Admin).

    Pipeline: BioBERT text embedding → BiomedCLIP image embedding →
    FAISS similarity retrieval → Neo4j SNOMED graph → Groq entity extraction →
    Groq differential diagnosis → Gemini full report → Groq executive summary.
    """
    if not query_data.patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")
    patient = (await db.execute(select(Patient).where(Patient.uuid == query_data.patient_id))).scalars().first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    try:
        diagnostic_service = get_diagnostic_service(db, physician_uuid=current_user.uuid)
        report_data = await diagnostic_service.generate_diagnostic_report(
            patient_id=patient.uuid,
            query=query_data.query or query_data.clinical_notes,
            include_images=query_data.include_images,
        )
        await log_audit(db=db, user=current_user, action=AuditAction.GENERATE_DIAGNOSTIC,
                        target_type="diagnostic_report", patient_uuid=patient.uuid,
                        action_details="Generated AI diagnostic report", request=request)
        return DiagnosticReportResponse(
            uuid=report_data.get("report_id", "generated"),
            title=f"AI Diagnostic Analysis — {patient.full_name}",
            summary=report_data["full_summary"],
            suggested_conditions=report_data["suggested_conditions"],
            evidence_summary=report_data["evidence_summary"],
            citations=report_data["citations"],
            created_at=report_data["generated_at"],
        )
    except Exception as e:
        logger.error(f"Diagnostic report generation failed: {e}", exc_info=True)
        await log_audit(db=db, user=current_user, action=AuditAction.GENERATE_DIAGNOSTIC,
                        status=AuditStatus.FAILED, target_type="diagnostic_report",
                        patient_uuid=patient.uuid, action_details=f"Failed: {str(e)}", request=request)
        raise HTTPException(status_code=500, detail=f"Failed to generate diagnostic report: {str(e)}")


@router.get("/reports/{patient_id}", response_model=list[DiagnosticReportResponse])
async def get_patient_diagnostic_reports(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get all diagnostic reports for a patient. Patients can only view their own."""
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(status_code=403, detail="Access denied")
    reports = (await db.execute(
        select(DiagnosticReport).where(DiagnosticReport.patient_uuid == patient_id)
        .order_by(DiagnosticReport.created_at.desc())
    )).scalars().all()
    await log_audit(db=db, user=current_user, action=AuditAction.VIEW_DIAGNOSTIC,
                    target_type="diagnostic_reports", patient_uuid=patient_id, request=request)
    return reports


@router.get("/reports/detail/{report_id}", response_model=DiagnosticReportResponse)
async def get_diagnostic_report_detail(
    report_id: str,
    request: Request,
    current_user: User = Depends(require_roles("physician", "admin", "patient")),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific diagnostic report by ID."""
    report = (await db.execute(
        select(DiagnosticReport).where(DiagnosticReport.uuid == report_id)
    )).scalars().first()
    if not report:
        raise HTTPException(status_code=404, detail="Diagnostic report not found")
    if current_user.role == UserRole.PATIENT:
        if not current_user.patient_record or current_user.patient_record.uuid != report.patient_uuid:
            raise HTTPException(status_code=403, detail="Access denied")
    await log_audit(db=db, user=current_user, action=AuditAction.VIEW_DIAGNOSTIC,
                    target_type="diagnostic_report", target_uuid=report_id,
                    patient_uuid=report.patient_uuid, request=request)
    return report


@router.post("/analyze-image")
async def analyze_medical_image(
    body: ImageAnalysisRequest,
    current_user: User = Depends(require_roles("physician", "admin")),
):
    """
    Analyze a single medical image using Gemini Vision.
    Send `image_path` (server-side path) and an optional `query` in the request body.
    """
    from src.services.gemini import get_gemini_service
    try:
        analysis = await get_gemini_service().analyze_image(
            image_path=body.image_path, query=body.query
        )
        return {"image_path": body.image_path, "analysis": analysis, "model": "gemini-1.5-pro"}
    except Exception as e:
        logger.error(f"Image analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/summarize-note")
async def summarize_clinical_note(
    body: SummarizeNoteRequest,
    current_user: User = Depends(require_roles("physician", "nurse", "admin")),
):
    """
    Summarize clinical notes using Groq (fast and cost-effective).
    Send `note_text` and optional `max_length` (50–500 words) in the request body.
    """
    if not (50 <= body.max_length <= 500):
        raise HTTPException(status_code=400, detail="max_length must be between 50 and 500")
    from src.services.groq import get_groq_service
    try:
        summary = await get_groq_service().summarize_clinical_note(
            clinical_note=body.note_text, max_length=body.max_length
        )
        return {
            "original_length": len(body.note_text.split()),
            "summary_length": len(summary.split()),
            "summary": summary,
            "model": "mixtral-8x7b-32768",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/extract-entities")
async def extract_medical_entities(
    body: ExtractEntitiesRequest,
    current_user: User = Depends(require_roles("physician", "nurse", "admin")),
):
    """
    Extract medical entities (symptoms, diagnoses, medications) from text using Groq.
    Send the clinical `text` in the request body.
    """
    from src.services.groq import get_groq_service
    try:
        entities = await get_groq_service().extract_medical_entities(body.text)
        return {"text_length": len(body.text), "entities": entities, "model": "mixtral-8x7b-32768"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/capabilities")
async def get_diagnostic_capabilities(
    current_user: User = Depends(require_roles("physician", "admin")),
):
    """Get information about available AI diagnostic capabilities."""
    return {
        "available_models": {
            "gemini": {
                "name": "Google Gemini 1.5 Pro",
                "capabilities": ["multi-modal", "vision", "long-context"],
                "use_cases": ["Full diagnostic reports", "Image analysis"],
            },
            "groq": {
                "name": "Mixtral-8x7B (via Groq)",
                "capabilities": ["text-only", "ultra-fast"],
                "use_cases": ["Summaries", "Entity extraction", "Differential diagnosis"],
                "cost_savings": "~70% vs Gemini for text-only",
            },
            "biobert": {"name": "BioBERT", "local": True, "use_cases": ["Clinical note embedding"]},
            "biomedclip": {"name": "BiomedCLIP", "local": True, "use_cases": ["X-ray/CT embedding"]},
        },
        "features": {
            "hybrid_retrieval": True,
            "knowledge_graph": True,
            "multi_modal": True,
            "citation_generation": True,
            "snomed_ct": True,
        },
    }
