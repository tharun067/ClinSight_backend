"""
Diagnostic support router for AI-powered clinical decision support.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import logging

from src.database.postgres import get_db
from src.models.user import User
from src.models.patient import Patient
from src.models.clinical import DiagnosticReport
from src.schemas.clinical import DiagnosticQuery, DiagnosticReportResponse
from src.utils.auth import get_current_active_user, require_roles, log_audit
from src.models.audit_log import AuditAction
from src.services.diagnostic import get_diagnostic_service

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/generate", response_model=DiagnosticReportResponse)
async def generate_diagnostic_report(
    query_data: DiagnosticQuery,
    request: Request,
    current_user: User = Depends(require_roles("physician")),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate AI-powered diagnostic report (Physician only).
    
    **This endpoint orchestrates:**
    1. **BioBERT** - Text embedding for clinical notes
    2. **BioViL/BiomedCLIP** - Image embedding for X-rays/CT scans
    3. **FAISS** - Vector similarity search
    4. **Neo4j** - SNOMED CT knowledge graph queries
    5. **Groq** - Fast entity extraction, differential diagnosis (cost-effective)
    6. **Gemini** - Multi-modal diagnostic reasoning (comprehensive)
    
    **Token Optimization:**
    - Groq handles: Entity extraction, summaries, validation (~70% cost reduction)
    - Gemini handles: Final diagnostic reasoning with images (high-quality output)
    
    **Required:**
    - patient_id: Patient UUID (required)
    
    **Optional:**
    - query: Specific clinical question
    - clinical_notes: Additional observations
    - include_images: Include imaging in analysis (default: true)
    """
    
    # Validate patient exists
    if not query_data.patient_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="patient_id is required"
        )
    
    result = await db.execute(
        select(Patient).where(Patient.uuid == query_data.patient_id)
    )
    patient = result.scalars().first()
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    try:
        # Get diagnostic service
        diagnostic_service = get_diagnostic_service(db)
        
        # Generate comprehensive diagnostic report
        logger.info(f"Generating diagnostic report for patient {patient.uuid}")
        
        report_data = await diagnostic_service.generate_diagnostic_report(
            patient_id=patient.uuid,
            query=query_data.query or query_data.clinical_notes,
            include_images=query_data.include_images
        )
        
        # Log audit
        await log_audit(
            db=db,
            user=current_user,
            action=AuditAction.GENERATE_DIAGNOSTIC,
            target_type="diagnostic_report",
            patient_uuid=patient.uuid,
            action_details=f"Generated diagnostic report using AI (Gemini + Groq)",
            request=request
        )
        
        logger.info(f"Diagnostic report generated successfully for patient {patient.uuid}")
        
        # Format response
        return DiagnosticReportResponse(
            uuid=report_data.get('report_id', 'generated'),  # Would be from saved report
            title=f"AI Diagnostic Analysis - {patient.full_name}",
            summary=report_data['full_summary'],
            suggested_conditions=report_data['suggested_conditions'],
            evidence_summary=report_data['evidence_summary'],
            citations=report_data['citations'],
            created_at=report_data['generated_at']
        )
        
    except Exception as e:
        logger.error(f"Diagnostic report generation failed: {e}", exc_info=True)
        
        # Log failed attempt
        await log_audit(
            db=db,
            user=current_user,
            action=AuditAction.GENERATE_DIAGNOSTIC,
            target_type="diagnostic_report",
            patient_uuid=patient.uuid,
            action_details=f"Failed: {str(e)}",
            request=request,
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate diagnostic report: {str(e)}"
        )

@router.get("/reports/{patient_id}", response_model=list[DiagnosticReportResponse])
async def get_patient_diagnostic_reports(
    patient_id: str,
    request: Request,
    current_user: User = Depends(require_roles("physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all diagnostic reports for a patient.
    
    Physicians can view any patient.
    Patients can only view their own reports.
    """
    
    # If user is a patient, verify they own the record
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != patient_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    # Get reports
    result = await db.execute(
        select(DiagnosticReport)
        .where(DiagnosticReport.patient_uuid == patient_id)
        .order_by(DiagnosticReport.created_at.desc())
    )
    reports = result.scalars().all()
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.VIEW_DIAGNOSTIC,
        target_type="diagnostic_reports",
        patient_uuid=patient_id,
        request=request
    )
    
    return reports

@router.get("/reports/detail/{report_id}", response_model=DiagnosticReportResponse)
async def get_diagnostic_report_detail(
    report_id: str,
    request: Request,
    current_user: User = Depends(require_roles("physician", "patient")),
    db: AsyncSession = Depends(get_db)
):
    """Get detailed diagnostic report by ID."""
    
    result = await db.execute(
        select(DiagnosticReport).where(DiagnosticReport.uuid == report_id)
    )
    report = result.scalars().first()
    
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnostic report not found"
        )
    
    # Check permissions
    if current_user.role.value == "patient":
        if not current_user.patient_record or current_user.patient_record.uuid != report.patient_uuid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    
    # Log audit
    await log_audit(
        db=db,
        user=current_user,
        action=AuditAction.VIEW_DIAGNOSTIC,
        target_type="diagnostic_report",
        target_uuid=report_id,
        patient_uuid=report.patient_uuid,
        request=request
    )
    
    return report

@router.post("/analyze-image")
async def analyze_medical_image(
    image_path: str,
    query: Optional[str] = None,
    current_user: User = Depends(require_roles("physician", "radiologist")),
):
    """
    Analyze a single medical image using Gemini Vision.
    
    Quick image analysis without full diagnostic report.
    """
    from src.services.gemini import get_gemini_service
    
    try:
        gemini_service = get_gemini_service()
        analysis = await gemini_service.analyze_image(
            image_path=image_path,
            query=query
        )
        
        return {
            "image_path": image_path,
            "analysis": analysis,
            "model": "gemini-1.5-pro-vision"
        }
    except Exception as e:
        logger.error(f"Image analysis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Image analysis failed: {str(e)}"
        )

@router.post("/summarize-note")
async def summarize_clinical_note(
    note_text: str,
    max_length: int = 200,
    current_user: User = Depends(require_roles("physician", "nurse")),
):
    """
    Summarize clinical notes using Groq (fast & cost-effective).
    
    Uses Mixtral-8x7B for quick text summarization.
    """
    from src.services.groq import get_groq_service
    
    try:
        groq_service = get_groq_service()
        summary = await groq_service.summarize_clinical_note(
            clinical_note=note_text,
            max_length=max_length
        )
        
        return {
            "original_length": len(note_text.split()),
            "summary_length": len(summary.split()),
            "summary": summary,
            "model": "mixtral-8x7b-32768"
        }
    except Exception as e:
        logger.error(f"Note summarization failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Summarization failed: {str(e)}"
        )

@router.post("/extract-entities")
async def extract_medical_entities(
    text: str,
    current_user: User = Depends(require_roles("physician", "nurse")),
):
    """
    Extract medical entities from text using Groq.
    
    Extracts: symptoms, diagnoses, medications, procedures, lab tests, body parts
    """
    from src.services.groq import get_groq_service
    
    try:
        groq_service = get_groq_service()
        entities = await groq_service.extract_medical_entities(text)
        
        return {
            "text_length": len(text),
            "entities": entities,
            "model": "mixtral-8x7b-32768"
        }
    except Exception as e:
        logger.error(f"Entity extraction failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Entity extraction failed: {str(e)}"
        )

@router.get("/capabilities")
async def get_diagnostic_capabilities(
    current_user: User = Depends(require_roles("physician")),
):
    """
    Get information about available AI diagnostic capabilities.
    """
    return {
        "available_models": {
            "gemini": {
                "name": "Google Gemini 1.5 Pro",
                "capabilities": ["multi-modal", "vision", "long-context"],
                "use_cases": ["Full diagnostic reports", "Image analysis"],
                "cost": "Higher",
                "speed": "Moderate"
            },
            "groq": {
                "name": "Mixtral-8x7B (via Groq)",
                "capabilities": ["text-only", "ultra-fast"],
                "use_cases": ["Summaries", "Entity extraction", "Quick reasoning"],
                "cost": "Lower (~70% savings)",
                "speed": "Very Fast (up to 500 tokens/sec)"
            },
            "biobert": {
                "name": "BioBERT",
                "capabilities": ["medical-text-embedding"],
                "use_cases": ["Clinical note embedding", "Semantic search"],
                "local": True
            },
            "biomedclip": {
                "name": "BiomedCLIP",
                "capabilities": ["medical-image-embedding"],
                "use_cases": ["X-ray/CT embedding", "Image similarity"],
                "local": True
            }
        },
        "features": {
            "hybrid_retrieval": True,
            "knowledge_graph": True,
            "multi_modal": True,
            "citation_generation": True,
            "cost_optimization": True,
            "snomed_ct": True
        },
        "token_optimization": {
            "enabled": True,
            "strategy": "Use Groq for text-only tasks, Gemini for multi-modal",
            "estimated_savings": "60-70% on text-only operations"
        }
    }
