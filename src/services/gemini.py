import asyncio
import google.generativeai as genai
from PIL import Image
from typing import List, Dict, Any, Optional
import logging
import time
import json
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings

logger = logging.getLogger(__name__)

if settings.GOOGLE_API_KEY:
    genai.configure(api_key=settings.GOOGLE_API_KEY)


class GeminiService:
    def __init__(self):
        self.model_name = settings.GEMINI_MODEL
        self.temperature = settings.GEMINI_TEMPERATURE
        self.max_tokens = settings.GEMINI_MAX_TOKENS
        try:
            self.model = genai.GenerativeModel(self.model_name)
            logger.info(f"Gemini model initialized: {self.model_name}")
        except Exception as e:
            logger.error(f"Error initializing Gemini: {e}")
            self.model = None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def generate_diagnostic_report(
        self,
        patient_data: Dict[str, Any],
        clinical_notes: Optional[str] = None,
        lab_results: Optional[List[Dict[str, Any]]] = None,
        vital_signs: Optional[Dict[str, Any]] = None,
        image_paths: Optional[List[str]] = None,
        retrieved_context: Optional[str] = None,
        citations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Generate a comprehensive diagnostic report.
        The actual Gemini call runs in a thread pool to avoid blocking the event loop.
        """
        if self.model is None:
            raise RuntimeError("Gemini model not initialized. Check GOOGLE_API_KEY.")

        start_time = time.time()

        prompt = self._build_diagnostic_prompt(
            patient_data, clinical_notes, lab_results, vital_signs, retrieved_context, citations
        )

        # Build content list (text + optional images)
        content_parts: List[Any] = [prompt]
        if image_paths:
            for img_path in image_paths:
                try:
                    content_parts.append(Image.open(img_path))
                except Exception as e:
                    logger.warning(f"Failed to load image {img_path}: {e}")

        generation_config = genai.types.GenerationConfig(
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
        )

        # ← Fixed: run blocking sync call off the event loop
        def _call():
            return self.model.generate_content(content_parts, generation_config=generation_config)

        response = await asyncio.to_thread(_call)

        generation_time = int((time.time() - start_time) * 1000)
        report_data = self._parse_diagnostic_response(response.text)

        logger.info(f"Gemini diagnostic report generated ({generation_time}ms)")
        return {
            "summary": report_data.get("summary", response.text),
            "suggested_conditions": report_data.get("suggested_conditions", []),
            "evidence_summary": report_data.get("evidence_summary", ""),
            "differential_diagnoses": report_data.get("differential_diagnoses", []),
            "recommended_actions": report_data.get("recommended_actions", []),
            "important_considerations": report_data.get("important_considerations", []),
            "citations": citations or [],
            "model_name": self.model_name,
            "generation_time_ms": generation_time,
            "confidence_level": report_data.get("confidence_level", "Medium"),
        }

    def _build_diagnostic_prompt(
        self,
        patient_data: Dict[str, Any],
        clinical_notes: Optional[str],
        lab_results: Optional[List[Dict[str, Any]]],
        vital_signs: Optional[Dict[str, Any]],
        retrieved_context: Optional[str],
        citations: Optional[List[Dict[str, Any]]],
    ) -> str:
        prompt = f"""You are an expert medical AI assistant specialized in diagnostic reasoning.

**PATIENT INFORMATION:**
- Name: {patient_data.get('name', 'Unknown')}
- Age: {patient_data.get('age', 'Unknown')}
- Gender: {patient_data.get('gender', 'Unknown')}
"""
        if clinical_notes:
            prompt += f"\n**CLINICAL PRESENTATION:**\n{clinical_notes}\n"
        if vital_signs:
            prompt += "\n**VITAL SIGNS:**\n" + "".join(
                f"- {k}: {v}\n" for k, v in vital_signs.items() if v
            )
        if lab_results:
            prompt += "\n**LABORATORY RESULTS:**\n"
            for lab in lab_results:
                abnormal = f" [{lab.get('is_abnormal')}]" if lab.get("is_abnormal") else ""
                prompt += f"- {lab.get('test_name')}: {lab.get('test_value')} {lab.get('unit')}{abnormal}\n"
        if retrieved_context:
            prompt += f"\n**RETRIEVED MEDICAL KNOWLEDGE:**\n{retrieved_context}\n"
        if citations:
            prompt += "\n**AVAILABLE CITATIONS:**\n"
            for cite in citations[:5]:
                if cite.get("type") == "SNOMED":
                    prompt += f"- SNOMED-{cite.get('id')}: {cite.get('term', '')}\n"

        prompt += """
**REQUIRED OUTPUT FORMAT:**

## Clinical Summary
[Brief overview]

## Differential Diagnoses
1. **[Most Likely]** (Confidence: X%)
   - Evidence: ...
2. **[Second]** (Confidence: X%)
3. **[Third]** (Confidence: X%)

## Evidence Summary
[How findings support the diagnoses]

## Recommended Next Steps
- ...

## Confidence Level
[High/Medium/Low with justification]

**DISCLAIMER:** This is decision support only. Final clinical judgment rests with the physician.
"""
        return prompt

    def _parse_diagnostic_response(self, response_text: str) -> Dict[str, Any]:
        result = {
            "summary": "",
            "suggested_conditions": [],
            "evidence_summary": "",
            "differential_diagnoses": [],
            "recommended_actions": [],
            "important_considerations": [],
            "confidence_level": "Medium",
        }
        try:
            sections: Dict[str, str] = {
                "Clinical Summary": "",
                "Differential Diagnoses": "",
                "Evidence Summary": "",
                "Recommended Next Steps": "",
                "Important Considerations": "",
                "Confidence Level": "",
            }
            current = None
            for line in response_text.split("\n"):
                line = line.strip()
                for name in sections:
                    if name.lower() in line.lower() and line.startswith("#"):
                        current = name
                        break
                else:
                    if current and line:
                        sections[current] += line + "\n"

            diagnoses = []
            for line in sections["Differential Diagnoses"].split("\n"):
                if line.strip() and line.strip()[0].isdigit():
                    parts = line.split("(Confidence:")
                    if len(parts) >= 2:
                        name = parts[0].strip().lstrip("0123456789.").strip("* ").strip()
                        conf_str = parts[1].split("%")[0].strip().rstrip(")")
                        try:
                            conf = int(conf_str)
                        except ValueError:
                            conf = 50
                        diagnoses.append({"condition": name, "confidence": conf, "evidence": ""})

            result["summary"] = sections["Clinical Summary"].strip()
            result["suggested_conditions"] = diagnoses
            result["differential_diagnoses"] = diagnoses
            result["evidence_summary"] = sections["Evidence Summary"].strip()
            result["recommended_actions"] = [
                l.strip("- ").strip()
                for l in sections["Recommended Next Steps"].split("\n")
                if l.strip().startswith("-")
            ]
            result["important_considerations"] = [
                l.strip("- ").strip()
                for l in sections["Important Considerations"].split("\n")
                if l.strip().startswith("-")
            ]
            cl = sections["Confidence Level"].lower()
            result["confidence_level"] = "High" if "high" in cl else "Low" if "low" in cl else "Medium"
        except Exception as e:
            logger.error(f"Error parsing Gemini response: {e}")
            result["summary"] = response_text
        return result

    async def analyze_image(self, image_path: str, query: Optional[str] = None) -> str:
        """Analyze a single medical image. Runs sync Gemini call in thread pool."""
        if self.model is None:
            raise RuntimeError("Gemini model not initialized")

        prompt = query or (
            "Analyze this medical image and provide:\n"
            "1. Modality identification\n2. Anatomical region\n"
            "3. Key findings or abnormalities\n4. Clinical significance\n"
            "5. Image quality assessment\nUse medical terminology."
        )
        img = Image.open(image_path)

        def _call():
            return self.model.generate_content([prompt, img])

        response = await asyncio.to_thread(_call)
        return response.text


_gemini_service: Optional[GeminiService] = None


def get_gemini_service() -> GeminiService:
    global _gemini_service
    if _gemini_service is None:
        _gemini_service = GeminiService()
    return _gemini_service