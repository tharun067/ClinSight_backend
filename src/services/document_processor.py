"""
Document processing service for automatic AI data extraction.
Extracts lab results, vitals, and imaging data from uploaded PDFs and images.
Updates Document.extraction_status and extraction_results so callers can
poll GET /api/documents/{id}/extraction-status to see what was found.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional
import re

import fitz  # PyMuPDF
from PIL import Image
import pytesseract

from src.services.gemini import GeminiService
from src.services.groq import GroqService
from src.models.lab_vital import LabResult, VitalSign
from src.models.imaging_study import ImagingStudy, ImagingModality, ImagingStatus
from src.models.document import Document, ExtractionStatus
from src.database.postgres import AsyncSessionLocal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

try:
    pytesseract.get_tesseract_version()
    TESSERACT_AVAILABLE = True
    logger.info("Tesseract-OCR is available")
except Exception:
    TESSERACT_AVAILABLE = False
    logger.warning("Tesseract-OCR not found. Image OCR will be skipped.")


class DocumentProcessor:
    """Processes uploaded medical documents to extract structured data via Gemini AI."""

    def __init__(self):
        self.gemini_service = GeminiService()
        self.groq_service = GroqService()

    # ── Public entry point ────────────────────────────────────────────────────

    async def process_document(
        self,
        file_path: str,
        file_extension: str,
        patient_uuid: str,
        user_uuid: str,
        document_uuid: str,
    ) -> Dict[str, Any]:
        """
        Process a document in a background task.
        Updates Document.extraction_status at each stage so the caller can poll.
        """
        async with AsyncSessionLocal() as db:
            # Mark as PROCESSING
            await self._set_status(db, document_uuid, ExtractionStatus.PROCESSING,
                                   started_at=datetime.now(timezone.utc))
            try:
                result = await self._process_with_session(
                    file_path=file_path,
                    file_extension=file_extension,
                    patient_uuid=patient_uuid,
                    user_uuid=user_uuid,
                    document_uuid=document_uuid,
                    db=db,
                )
                # Mark as COMPLETED with result summary
                await self._set_status(
                    db, document_uuid, ExtractionStatus.COMPLETED,
                    completed_at=datetime.now(timezone.utc),
                    results=result,
                )
                return result
            except Exception as e:
                logger.error(f"Document processing failed for {file_path}: {e}", exc_info=True)
                await db.rollback()
                async with AsyncSessionLocal() as db2:
                    await self._set_status(
                        db2, document_uuid, ExtractionStatus.FAILED,
                        completed_at=datetime.now(timezone.utc),
                        error=str(e),
                    )
                return {"labs": [], "vitals": [], "imaging": [], "error": str(e)}

    # ── Internal processing ───────────────────────────────────────────────────

    async def _process_with_session(
        self,
        file_path: str,
        file_extension: str,
        patient_uuid: str,
        user_uuid: str,
        document_uuid: str,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        # 1. Extract text
        if file_extension == ".pdf":
            text = await self._extract_pdf_text(file_path)
        elif file_extension in (".jpg", ".jpeg", ".png", ".tiff", ".bmp"):
            text = await self._extract_image_text(file_path)
        else:
            raise ValueError(f"Unsupported file type: {file_extension}")

        if not text or len(text) < 20:
            logger.warning(f"Insufficient text from {file_path} ({len(text)} chars)")
            return {
                "labs_extracted": 0, "vitals_extracted": 0, "imaging_extracted": 0,
                "lab_ids": [], "vital_ids": [], "imaging_ids": [],
                "raw_text_length": len(text),
                "message": "No readable text found in document.",
            }

        # 2. AI extraction
        extracted = await self._extract_medical_data_with_ai(text)

        # 3. Persist records
        lab_ids, vital_ids, imaging_ids = [], [], []

        for lab_data in extracted.get("lab_results", []):
            try:
                parsed_test_date = self._parse_date(lab_data.get("test_date")) or datetime.now(timezone.utc)
                async with db.begin_nested():
                    lab = LabResult(
                        patient_uuid=patient_uuid,
                        test_name=(lab_data.get("test_name") or "Unknown Test").strip(),
                        test_value=float(lab_data.get("test_value", 0) or 0),
                        unit=(lab_data.get("unit") or "unknown").strip(),
                        reference_range_low=lab_data.get("reference_range_low"),
                        reference_range_high=lab_data.get("reference_range_high"),
                        test_date=parsed_test_date,
                        is_abnormal=lab_data.get("is_abnormal"),
                        ordered_by=user_uuid,
                    )
                    db.add(lab)
                    await db.flush()
                    lab_ids.append(lab.uuid)
            except Exception as e:
                logger.error(f"Failed to save lab result: {e}")

        vitals_data = extracted.get("vital_signs", {})
        has_vitals = any(v is not None for v in vitals_data.values() if v != "°C" and v != "°F")
        if vitals_data and has_vitals:
            try:
                async with db.begin_nested():
                    vitals = VitalSign(
                        patient_uuid=patient_uuid,
                        measurement_date=self._parse_date(vitals_data.get("measurement_date")) or datetime.now(timezone.utc),
                        temperature=vitals_data.get("temperature"),
                        temperature_unit=vitals_data.get("temperature_unit") or "°C",
                        systolic_bp=vitals_data.get("systolic_bp"),
                        diastolic_bp=vitals_data.get("diastolic_bp"),
                        heart_rate=vitals_data.get("heart_rate"),
                        respiratory_rate=vitals_data.get("respiratory_rate"),
                        oxygen_saturation=vitals_data.get("oxygen_saturation"),
                        weight=vitals_data.get("weight"),
                        height=vitals_data.get("height"),
                        recorded_by=user_uuid,
                    )
                    if vitals.weight and vitals.height and vitals.height > 0:
                        vitals.bmi = round(vitals.weight / (vitals.height / 100) ** 2, 2)
                    db.add(vitals)
                    await db.flush()
                    vital_ids.append(vitals.uuid)
            except Exception as e:
                logger.error(f"Failed to save vitals: {e}")

        imaging_data = extracted.get("imaging_study", {})
        if imaging_data and imaging_data.get("modality"):
            try:
                async with db.begin_nested():
                    imaging = ImagingStudy(
                        patient_uuid=patient_uuid,
                        study_date=self._parse_date(imaging_data.get("study_date")) or datetime.now(timezone.utc),
                        modality=self._parse_modality(imaging_data.get("modality")),
                        body_part=imaging_data.get("body_part"),
                        findings=imaging_data.get("findings"),
                        impression=imaging_data.get("impression"),
                        status=ImagingStatus.COMPLETE,
                        interpreted_by=user_uuid,
                    )
                    db.add(imaging)
                    await db.flush()
                    imaging_ids.append(imaging.uuid)
            except Exception as e:
                logger.error(f"Failed to save imaging study: {e}")

        await db.commit()

        summary = {
            "labs_extracted": len(lab_ids),
            "vitals_extracted": len(vital_ids),
            "imaging_extracted": len(imaging_ids),
            "lab_ids": lab_ids,
            "vital_ids": vital_ids,
            "imaging_ids": imaging_ids,
            "raw_text_length": len(text),
            "ai_provider": extracted.get("ai_provider"),
        }
        if extracted.get("extraction_warning"):
            summary["extraction_warning"] = extracted["extraction_warning"]
        logger.info(f"Extraction complete for doc {document_uuid}: {summary}")
        return summary

    # ── Status helpers ────────────────────────────────────────────────────────

    async def _set_status(
        self,
        db: AsyncSession,
        document_uuid: str,
        status: ExtractionStatus,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        results: Optional[Dict] = None,
        error: Optional[str] = None,
    ):
        try:
            doc = (await db.execute(
                select(Document).where(Document.uuid == document_uuid)
            )).scalars().first()
            if doc:
                doc.extraction_status = status
                if started_at:
                    doc.extraction_started_at = started_at
                if completed_at:
                    doc.extraction_completed_at = completed_at
                if results is not None:
                    doc.extraction_results = results
                if error:
                    doc.extraction_error = error
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to update extraction status: {e}")

    # ── Text extraction ───────────────────────────────────────────────────────

    async def _extract_pdf_text(self, file_path: str) -> str:
        def _run():
            doc = fitz.open(file_path)
            text = "".join(page.get_text() for page in doc)
            doc.close()
            return text.strip()
        try:
            text = await asyncio.to_thread(_run)
            logger.info(f"PDF extracted {len(text)} chars from {file_path}")
            return text
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            return ""

    async def _extract_image_text(self, file_path: str) -> str:
        if not TESSERACT_AVAILABLE:
            logger.warning("Tesseract not available — skipping OCR")
            return ""
        def _run():
            return pytesseract.image_to_string(Image.open(file_path))
        try:
            text = await asyncio.to_thread(_run)
            logger.info(f"Image OCR extracted {len(text)} chars from {file_path}")
            return text.strip()
        except Exception as e:
            logger.error(f"Image OCR failed: {e}")
            return ""

    # ── AI extraction ─────────────────────────────────────────────────────────

    async def _extract_medical_data_with_ai(self, text: str) -> Dict[str, Any]:
        prompt = self._build_extraction_prompt(text)

        try:
            data = await self._extract_with_gemini(prompt)
            data["ai_provider"] = "gemini"
            return data
        except Exception as gemini_error:
            logger.error(f"AI extraction with Gemini failed: {gemini_error}")
            gemini_error_text = str(gemini_error)

        if self._is_quota_or_rate_limit_error(gemini_error_text):
            logger.warning("Gemini quota/rate limit hit. Trying Groq fallback for extraction.")
        else:
            logger.warning("Gemini extraction failed. Trying Groq fallback for extraction.")

        try:
            data = await self._extract_with_groq(prompt)
            data["ai_provider"] = "groq"
            data["extraction_warning"] = (
                "Gemini extraction unavailable; used Groq fallback. "
                f"Reason: {gemini_error_text[:300]}"
            )
            return data
        except Exception as groq_error:
            logger.error(f"AI extraction with Groq fallback failed: {groq_error}")
            return self._empty_extraction(
                warning=(
                    "AI extraction unavailable from both Gemini and Groq. "
                    f"Gemini error: {gemini_error_text[:180]}; "
                    f"Groq error: {str(groq_error)[:180]}"
                )
            )

    def _build_extraction_prompt(self, text: str) -> str:
        return f"""You are a medical data extraction AI. Extract structured medical data from the text below.

Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{{
  "lab_results": [
    {{
      "test_name": "string",
      "test_value": number,
      "unit": "string",
      "reference_range_low": number or null,
      "reference_range_high": number or null,
      "test_date": "YYYY-MM-DD" or null,
      "is_abnormal": "Normal" or "High" or "Low" or null
    }}
  ],
  "vital_signs": {{
    "temperature": number or null,
    "temperature_unit": "°C" or "°F",
    "systolic_bp": number or null,
    "diastolic_bp": number or null,
    "heart_rate": number or null,
    "respiratory_rate": number or null,
    "oxygen_saturation": number or null,
    "weight": number or null,
    "height": number or null,
    "measurement_date": "YYYY-MM-DD" or null
  }},
  "imaging_study": {{
    "modality": "X-Ray" or "CT" or "MRI" or "Ultrasound" or "PET" or null,
    "body_part": "string" or null,
    "findings": "string" or null,
    "impression": "string" or null,
    "study_date": "YYYY-MM-DD" or null
  }}
}}

Rules:
- Only extract data explicitly present in the text
- Use null for missing values
- Split BP into systolic_bp and diastolic_bp
- Return empty arrays/null objects if nothing found

Text:
{text[:4000]}
"""

    async def _extract_with_gemini(self, prompt: str) -> Dict[str, Any]:
        try:
            def _call():
                return self.gemini_service.model.generate_content(
                    prompt,
                    generation_config={"temperature": 0.1, "max_output_tokens": 2048},
                )

            response = await asyncio.to_thread(_call)
            return self._parse_ai_json(response.text)
        except Exception:
            raise

    async def _extract_with_groq(self, prompt: str) -> Dict[str, Any]:
        result = await self.groq_service.generate_completion(
            prompt=prompt,
            system_prompt="You extract structured medical data and return strict JSON only.",
            temperature=0.1,
            max_tokens=2048,
        )
        return self._parse_ai_json(result.get("content", ""))

    def _parse_ai_json(self, raw_text: str) -> Dict[str, Any]:
        raw = (raw_text or "").strip()
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'^```\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        data = json.loads(raw)
        data.setdefault("lab_results", [])
        data.setdefault("vital_signs", {})
        data.setdefault("imaging_study", {})
        return data

    def _empty_extraction(self, warning: Optional[str] = None) -> Dict[str, Any]:
        data = {"lab_results": [], "vital_signs": {}, "imaging_study": {}, "ai_provider": "none"}
        if warning:
            data["extraction_warning"] = warning
        return data

    def _is_quota_or_rate_limit_error(self, error_text: str) -> bool:
        lowered = (error_text or "").lower()
        markers = [
            "quota",
            "429",
            "rate limit",
            "rate-limit",
            "resource_exhausted",
            "exceeded your current quota",
            "please retry in",
        ]
        return any(marker in lowered for marker in markers)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None

    def _parse_modality(self, s: Optional[str]) -> ImagingModality:
        if not s:
            return ImagingModality.XRAY
        return {
            "x-ray": ImagingModality.XRAY, "xray": ImagingModality.XRAY,
            "ct": ImagingModality.CT, "mri": ImagingModality.MRI,
            "ultrasound": ImagingModality.ULTRASOUND, "pet": ImagingModality.PET,
        }.get(s.lower(), ImagingModality.XRAY)


_document_processor: Optional[DocumentProcessor] = None


def get_document_processor() -> DocumentProcessor:
    global _document_processor
    if _document_processor is None:
        _document_processor = DocumentProcessor()
    return _document_processor
