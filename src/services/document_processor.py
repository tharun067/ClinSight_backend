"""
Document processing service for automatic data extraction.
Extracts lab results, vitals, and imaging data from uploaded PDFs and images.
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import re

import fitz  # PyMuPDF
from PIL import Image
import pytesseract

from src.services.gemini import GeminiService
from src.models.lab_vital import LabResult, VitalSign
from src.models.imaging_study import ImagingStudy, ImagingModality, ImagingStatus
from src.database.postgres import AsyncSessionLocal
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Check if Tesseract is available for OCR
try:
    pytesseract.get_tesseract_version()
    logger.info("Tesseract-OCR is available")
except pytesseract.TesseractNotFoundError:
    logger.warning(
        "Tesseract-OCR not found. Image text extraction will fail. "
        "Install from: https://github.com/UB-Mannheim/tesseract/wiki"
    )
except Exception:
    # Tesseract may be available but version check failed
    pass


class DocumentProcessor:
    """Processes uploaded medical documents to extract structured data."""
    
    def __init__(self):
        self.gemini_service = GeminiService()
    
    async def extract_text_from_pdf(self, file_path: str) -> str:
        """Extract text from PDF using PyMuPDF."""
        try:
            def _extract():
                doc = fitz.open(file_path)
                text = ""
                for page in doc:
                    text += page.get_text()
                doc.close()
                return text
            
            text = await asyncio.to_thread(_extract)
            logger.info(f"Extracted {len(text)} characters from PDF: {file_path}")
            return text.strip()
        except Exception as e:
            logger.error(f"Failed to extract text from PDF {file_path}: {e}")
            return ""
    
    async def extract_text_from_image(self, file_path: str) -> str:
        """Extract text from image using OCR (pytesseract)."""
        try:
            def _extract():
                image = Image.open(file_path)
                # Use OCR to extract text
                text = pytesseract.image_to_string(image)
                return text
            
            text = await asyncio.to_thread(_extract)
            logger.info(f"Extracted {len(text)} characters from image: {file_path}")
            return text.strip()
        except Exception as e:
            logger.error(f"Failed to extract text from image {file_path}: {e}")
            return ""
    
    async def process_document(
        self,
        file_path: str,
        file_extension: str,
        patient_uuid: str,
        user_uuid: str,
    ) -> Dict[str, Any]:
        """Process a document and extract medical data with its own DB session."""
        async with AsyncSessionLocal() as db:
            try:
                return await self._process_with_session(
                    file_path=file_path,
                    file_extension=file_extension,
                    patient_uuid=patient_uuid,
                    user_uuid=user_uuid,
                    db=db,
                )
            except Exception as e:
                logger.error(f"Document processing failed for {file_path}: {e}")
                await db.rollback()
                return {"labs": [], "vitals": [], "imaging": []}
    
    async def _process_with_session(
        self,
        file_path: str,
        file_extension: str,
        patient_uuid: str,
        user_uuid: str,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Process a document internally with existing session.
        
        Returns:
            Dict with extracted data: {
                "labs": [...],
                "vitals": [...],
                "imaging": [...]
            }
        """
        # Extract text based on file type
        if file_extension in [".pdf"]:
            text = await self.extract_text_from_pdf(file_path)
        elif file_extension in [".jpg", ".jpeg", ".png", ".tiff", ".bmp"]:
            text = await self.extract_text_from_image(file_path)
        else:
            logger.warning(f"Unsupported file type for processing: {file_extension}")
            return {"labs": [], "vitals": [], "imaging": []}
        
        if not text or len(text) < 20:
            logger.warning(f"Insufficient text extracted from {file_path}")
            return {"labs": [], "vitals": [], "imaging": []}
        
        # Use AI to extract structured data
        extracted_data = await self._extract_medical_data_with_ai(text)
        
        # Create database records
        results = {
            "labs": [],
            "vitals": [],
            "imaging": []
        }
        
        # Create lab results
        if extracted_data.get("lab_results"):
            for lab_data in extracted_data["lab_results"]:
                try:
                    lab = LabResult(
                        patient_uuid=patient_uuid,
                        test_name=lab_data.get("test_name"),
                        test_value=lab_data.get("test_value"),
                        unit=lab_data.get("unit"),
                        reference_range_low=lab_data.get("reference_range_low"),
                        reference_range_high=lab_data.get("reference_range_high"),
                        test_date=self._parse_date(lab_data.get("test_date")),
                        is_abnormal=lab_data.get("is_abnormal"),
                        ordered_by=user_uuid,
                    )
                    db.add(lab)
                    results["labs"].append(lab_data.get("test_name"))
                except Exception as e:
                    logger.error(f"Failed to create lab result: {e}")
        
        # Create vital signs
        if extracted_data.get("vital_signs"):
            try:
                vitals_data = extracted_data["vital_signs"]
                vitals = VitalSign(
                    patient_uuid=patient_uuid,
                    measurement_date=self._parse_date(vitals_data.get("measurement_date")) or datetime.now(),
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
                # Auto-calculate BMI
                if vitals.weight and vitals.height and vitals.height > 0:
                    height_m = vitals.height / 100
                    vitals.bmi = round(vitals.weight / (height_m ** 2), 2)
                
                db.add(vitals)
                results["vitals"].append("Vital signs recorded")
            except Exception as e:
                logger.error(f"Failed to create vital signs: {e}")
        
        # Create imaging study
        if extracted_data.get("imaging_study"):
            try:
                imaging_data = extracted_data["imaging_study"]
                imaging = ImagingStudy(
                    patient_uuid=patient_uuid,
                    study_date=self._parse_date(imaging_data.get("study_date")) or datetime.now(),
                    modality=self._parse_modality(imaging_data.get("modality")),
                    body_part=imaging_data.get("body_part"),
                    findings=imaging_data.get("findings"),
                    impression=imaging_data.get("impression"),
                    status=ImagingStatus.COMPLETE,
                    interpreted_by=user_uuid,
                )
                db.add(imaging)
                results["imaging"].append(imaging_data.get("modality"))
            except Exception as e:
                logger.error(f"Failed to create imaging study: {e}")
        
        # Commit all records
        try:
            await db.commit()
            logger.info(f"Successfully extracted data: {results}")
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to commit extracted data: {e}")
        
        return results
    
    async def _extract_medical_data_with_ai(self, text: str) -> Dict[str, Any]:
        """Use Gemini AI to extract structured medical data from text."""
        prompt = f"""
You are a medical data extraction AI. Extract structured medical data from the following text.

Return ONLY valid JSON (no markdown, no explanations) with this exact structure:
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
1. Only extract data that is explicitly present in the text
2. Use null for missing values
3. Convert all measurements to standard units
4. For BP, split into systolic_bp and diastolic_bp
5. Return empty arrays/objects if no data found

Text to analyze:
{text[:4000]}
"""
        
        try:
            # Use Gemini to extract data
            def _call():
                return self.gemini_service.model.generate_content(
                    prompt,
                    generation_config={
                        "temperature": 0.1,  # Low temperature for structured extraction
                        "max_output_tokens": 2048,
                    }
                )
            
            response = await asyncio.to_thread(_call)
            response_text = response.text.strip()
            
            # Remove markdown code blocks if present
            response_text = re.sub(r'^```json\s*', '', response_text)
            response_text = re.sub(r'\s*```$', '', response_text)
            
            # Parse JSON response
            extracted_data = json.loads(response_text)
            
            # Validate structure
            if not isinstance(extracted_data, dict):
                raise ValueError("Response is not a dictionary")
            
            # Ensure required keys exist
            extracted_data.setdefault("lab_results", [])
            extracted_data.setdefault("vital_signs", {})
            extracted_data.setdefault("imaging_study", {})
            
            logger.info(f"Extracted data: {len(extracted_data.get('lab_results', []))} labs, "
                       f"vitals: {bool(extracted_data.get('vital_signs'))}, "
                       f"imaging: {bool(extracted_data.get('imaging_study'))}")
            
            return extracted_data
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            return {"lab_results": [], "vital_signs": {}, "imaging_study": {}}
        except Exception as e:
            logger.error(f"AI extraction failed: {e}")
            return {"lab_results": [], "vital_signs": {}, "imaging_study": {}}
    
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse date string to datetime."""
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except:
            try:
                return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            except:
                return None
    
    def _parse_modality(self, modality_str: Optional[str]) -> ImagingModality:
        """Parse modality string to enum."""
        if not modality_str:
            return ImagingModality.XRAY
        
        modality_map = {
            "x-ray": ImagingModality.XRAY,
            "xray": ImagingModality.XRAY,
            "ct": ImagingModality.CT,
            "mri": ImagingModality.MRI,
            "ultrasound": ImagingModality.ULTRASOUND,
            "pet": ImagingModality.PET,
        }
        
        return modality_map.get(modality_str.lower(), ImagingModality.XRAY)


# Singleton instance
_document_processor: Optional[DocumentProcessor] = None


def get_document_processor() -> DocumentProcessor:
    """Get or create the document processor singleton."""
    global _document_processor
    if _document_processor is None:
        _document_processor = DocumentProcessor()
    return _document_processor
