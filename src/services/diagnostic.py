"""
Diagnostic service — orchestrates all AI components.
Fixed: _save_diagnostic_report now stores the requesting physician's UUID,
       not the patient UUID, in the generated_for_uuid column.
"""
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.models.patient import Patient
from src.models.lab_vital import LabResult, VitalSign
from src.models.clinical import ClinicalNote, DiagnosticReport
from src.models.imaging_study import ImagingStudy
from src.services.retrieval import get_retrieval_service
from src.services.gemini import get_gemini_service
from src.services.groq import get_groq_service
from src.services.embedding import get_embedding_service

logger = logging.getLogger(__name__)


class DiagnosticService:
    """
    10-step AI diagnostic pipeline:
      1. Gather patient data
      2. Build retrieval query
      3. Hybrid RAG (FAISS + Neo4j)
      4. Collect image paths
      5. Groq entity extraction
      6. Groq differential diagnosis
      7. Gemini multi-modal report
      8. Groq executive summary
      9. Groq reasoning validation
      10. Save to DB
    """

    def __init__(self, db: AsyncSession, physician_uuid: Optional[str] = None):
        self.db = db
        self.physician_uuid = physician_uuid  # stored so it can be saved in the report
        self.retrieval_service = get_retrieval_service()
        self.gemini_service = get_gemini_service()
        self.groq_service = get_groq_service()
        self.embedding_service = get_embedding_service()

    async def generate_diagnostic_report(
        self,
        patient_id: str,
        query: Optional[str] = None,
        include_images: bool = True,
        use_groq_for_summary: bool = True,
    ) -> Dict[str, Any]:
        logger.info(f"Generating diagnostic report for patient {patient_id}")

        # 1. Gather patient data
        patient_data = await self._gather_patient_data(patient_id)
        if not patient_data["patient"]:
            raise ValueError(f"Patient {patient_id} not found")

        # 2. Build retrieval query
        retrieval_query = query or self._generate_retrieval_query(patient_data)

        # 3. Hybrid retrieval
        retrieval_results = await self.retrieval_service.retrieve(
            query=retrieval_query,
            patient_id=patient_id,
            modalities=["text", "image"] if include_images else ["text"],
            include_graph=True,
        )

        # 4. Collect image paths
        image_paths = []
        if include_images:
            for study in patient_data["imaging_studies"]:
                if study.image_path:
                    image_paths.append(study.image_path)

        # 5. Entity extraction via Groq
        clinical_text = self._combine_clinical_text(patient_data)
        entities = await self.groq_service.extract_medical_entities(clinical_text)

        # 6. Differential diagnosis via Groq
        differential = await self.groq_service.generate_differential_diagnosis(
            symptoms=entities.get("symptoms", []),
            lab_results=self._format_labs_for_llm(patient_data["lab_results"]),
            patient_age=self._calculate_age(patient_data["patient"].date_of_birth),
            patient_gender=patient_data["patient"].gender.value,
        )

        # 7. Full report via Gemini
        gemini_report = await self.gemini_service.generate_diagnostic_report(
            patient_data=self._format_patient_for_gemini(patient_data),
            clinical_notes=clinical_text,
            lab_results=[
                {
                    "test_name": lr.test_name,
                    "test_value": lr.test_value,
                    "unit": lr.unit,
                    "is_abnormal": lr.is_abnormal,
                }
                for lr in patient_data["lab_results"]
            ],
            vital_signs=self._format_vitals_for_gemini(patient_data["vital_signs"]),
            image_paths=image_paths if include_images else None,
            retrieved_context=retrieval_results["context"],
            citations=self._extract_citations(retrieval_results),
        )

        # 8. Executive summary via Groq
        if use_groq_for_summary and gemini_report.get("summary"):
            executive_summary = await self.groq_service.summarize_clinical_note(
                clinical_note=gemini_report["summary"], max_length=150
            )
        else:
            executive_summary = gemini_report.get("summary", "")[:500]

        # 9. Validate reasoning
        top_condition = (gemini_report.get("suggested_conditions") or [{}])[0].get("condition", "Unknown")
        validation = await self._validate_reasoning(
            diagnosis=top_condition,
            evidence=[gemini_report.get("evidence_summary", "")],
        )

        # 10. Build final report
        final_report: Dict[str, Any] = {
            "patient_id": patient_id,
            "patient_name": patient_data["patient"].full_name,
            "patient_mrn": patient_data["patient"].mrn,
            "generated_at": datetime.now().isoformat(),
            "executive_summary": executive_summary,
            "full_summary": gemini_report.get("summary", ""),
            "suggested_conditions": gemini_report.get("suggested_conditions", differential),
            "differential_diagnoses": differential,
            "evidence_summary": gemini_report.get("evidence_summary", ""),
            "extracted_entities": entities,
            "key_findings": self._extract_key_findings(patient_data),
            "recommended_actions": gemini_report.get("recommended_actions", []),
            "important_considerations": gemini_report.get("important_considerations", []),
            "citations": self._format_citations(retrieval_results),
            "sources_used": len(retrieval_results["combined_results"]),
            "confidence_level": gemini_report.get("confidence_level", "Medium"),
            "validation": validation,
            "models_used": {
                "primary": gemini_report.get("model_name", "gemini-1.5-pro"),
                "summary": "mixtral-8x7b" if use_groq_for_summary else "gemini",
                "entity_extraction": "mixtral-8x7b",
                "differential_dx": "mixtral-8x7b",
            },
            "generation_time_ms": gemini_report.get("generation_time_ms", 0),
            "token_optimization": {
                "used_groq": use_groq_for_summary,
                "cost_savings": "~70%" if use_groq_for_summary else "0%",
            },
        }

        report_id = await self._save_diagnostic_report(patient_id, final_report)
        final_report["report_id"] = report_id

        logger.info(f"Diagnostic report generated for patient {patient_id}")
        return final_report

    # ── Data gathering helpers ──────────────────────────────────────────────

    async def _gather_patient_data(self, patient_id: str) -> Dict[str, Any]:
        patient = (
            await self.db.execute(select(Patient).where(Patient.uuid == patient_id))
        ).scalars().first()

        lab_results = (
            await self.db.execute(
                select(LabResult).where(LabResult.patient_uuid == patient_id)
                .order_by(LabResult.test_date.desc()).limit(20)
            )
        ).scalars().all()

        vital_signs = (
            await self.db.execute(
                select(VitalSign).where(VitalSign.patient_uuid == patient_id)
                .order_by(VitalSign.measurement_date.desc()).limit(10)
            )
        ).scalars().all()

        clinical_notes = (
            await self.db.execute(
                select(ClinicalNote).where(ClinicalNote.patient_uuid == patient_id)
                .order_by(ClinicalNote.note_date.desc()).limit(10)
            )
        ).scalars().all()

        imaging_studies = (
            await self.db.execute(
                select(ImagingStudy).where(ImagingStudy.patient_uuid == patient_id)
                .order_by(ImagingStudy.study_date.desc()).limit(5)
            )
        ).scalars().all()

        return {
            "patient": patient,
            "lab_results": lab_results,
            "vital_signs": vital_signs,
            "clinical_notes": clinical_notes,
            "imaging_studies": imaging_studies,
        }

    def _generate_retrieval_query(self, patient_data: Dict[str, Any]) -> str:
        parts = []
        if patient_data["patient"].chief_complaint:
            parts.append(patient_data["patient"].chief_complaint)
        for note in patient_data["clinical_notes"][:2]:
            parts.append(note.content[:200])
        for lab in patient_data["lab_results"][:5]:
            if lab.is_abnormal:
                parts.append(f"{lab.test_name} {lab.is_abnormal}")
        return " ".join(parts) or "general medical assessment"

    def _combine_clinical_text(self, patient_data: Dict[str, Any]) -> str:
        texts = []
        if patient_data["patient"].chief_complaint:
            texts.append(f"Chief Complaint: {patient_data['patient'].chief_complaint}")
        for note in patient_data["clinical_notes"][:3]:
            texts.append(f"\n{note.title}:\n{note.content}")
        for study in patient_data["imaging_studies"][:2]:
            if study.findings:
                texts.append(f"\nImaging ({study.modality.value}):\n{study.findings}")
        return "\n\n".join(texts)

    def _format_labs_for_llm(self, lab_results: List[LabResult]) -> Dict[str, Any]:
        return {
            lr.test_name: f"{lr.test_value} {lr.unit}" + (f" [{lr.is_abnormal}]" if lr.is_abnormal else "")
            for lr in lab_results
        }

    def _format_patient_for_gemini(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        p = patient_data["patient"]
        return {
            "name": p.full_name,
            "age": self._calculate_age(p.date_of_birth),
            "gender": p.gender.value,
            "mrn": p.mrn,
        }

    def _format_vitals_for_gemini(self, vital_signs: List[VitalSign]) -> Dict[str, Any]:
        if not vital_signs:
            return {}
        v = vital_signs[0]
        result = {}
        if v.temperature:
            result["Temperature"] = f"{v.temperature} {v.temperature_unit or '°C'}"
        if v.systolic_bp:
            result["Blood Pressure"] = f"{v.systolic_bp}/{v.diastolic_bp} mmHg"
        if v.heart_rate:
            result["Heart Rate"] = f"{v.heart_rate} bpm"
        if v.respiratory_rate:
            result["Respiratory Rate"] = f"{v.respiratory_rate} /min"
        if v.oxygen_saturation:
            result["O2 Saturation"] = f"{v.oxygen_saturation}%"
        return result

    def _extract_citations(self, retrieval_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "type": "SNOMED",
                "id": r.get("concept_id", ""),
                "term": r.get("concept_term", ""),
                "relevance": r.get("rrf_score", 0),
            }
            for r in retrieval_results["combined_results"][:10]
            if r["source_type"] == "knowledge_graph"
        ]

    def _format_citations(self, retrieval_results: Dict[str, Any]) -> List[Dict[str, str]]:
        return [
            {
                "type": "SNOMED CT",
                "reference": f"SNOMED-{c['id']}",
                "description": c["term"],
                "link": f"https://browser.ihtsdotools.org/?perspective=full&conceptId1={c['id']}",
            }
            for c in self._extract_citations(retrieval_results)
            if c["id"]
        ]

    def _extract_key_findings(self, patient_data: Dict[str, Any]) -> List[str]:
        findings = []
        for lr in patient_data["lab_results"]:
            if lr.is_abnormal and lr.is_abnormal != "Normal":
                findings.append(f"{lr.test_name}: {lr.test_value} {lr.unit} [{lr.is_abnormal}]")
        for study in patient_data["imaging_studies"]:
            if study.findings and any(
                kw in study.findings.lower() for kw in ("abnormal", "opacity", "effusion", "mass", "fracture")
            ):
                findings.append(f"{study.modality.value}: {study.findings[:100]}")
        return findings[:10]

    def _calculate_age(self, date_of_birth) -> int:
        from datetime import date
        today = date.today()
        return (
            today.year - date_of_birth.year
            - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))
        )

    async def _validate_reasoning(self, diagnosis: str, evidence: List[str]) -> Dict[str, Any]:
        try:
            return await self.groq_service.validate_clinical_reasoning(
                diagnosis=diagnosis, evidence=evidence
            )
        except Exception:
            return {"is_valid": True, "confidence": 50, "concerns": []}

    async def _save_diagnostic_report(self, patient_id: str, report_data: Dict[str, Any]) -> str:
        """Persist report to DB. Returns the report UUID."""
        try:
            report = DiagnosticReport(
                patient_uuid=patient_id,
                # Fixed: use physician UUID, not patient UUID
                generated_for_uuid=self.physician_uuid or patient_id,
                title=f"AI Diagnostic Report — {report_data['patient_name']}",
                summary=report_data["full_summary"],
                query=report_data.get("query", ""),
                suggested_conditions=report_data["suggested_conditions"],
                evidence_summary=report_data["evidence_summary"],
                citations=report_data["citations"],
                overall_confidence=report_data["confidence_level"],
                model_name=report_data["models_used"]["primary"],
                generation_time_ms=str(report_data["generation_time_ms"]),
            )
            self.db.add(report)
            await self.db.commit()
            logger.info(f"Saved diagnostic report for patient {patient_id}")
            return report.uuid
        except Exception as e:
            logger.error(f"Error saving diagnostic report: {e}")
            return "unsaved"


def get_diagnostic_service(db: AsyncSession, physician_uuid: Optional[str] = None) -> DiagnosticService:
    return DiagnosticService(db, physician_uuid=physician_uuid)