"""
Google Gemini API service for multi-modal diagnostic generation.
"""
import google.generativeai as genai
from PIL import Image
from typing import List, Dict, Any, Optional
import logging
import time
import json
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings

logger = logging.getLogger(__name__)

# Configure Gemini API
if settings.GOOGLE_API_KEY:
    genai.configure(api_key=settings.GOOGLE_API_KEY)

class GeminiService:
    """Service for Google Gemini API multi-modal generation."""
    
    def __init__(self):
        self.model_name = settings.GEMINI_MODEL
        self.temperature = settings.GEMINI_TEMPERATURE
        self.max_tokens = settings.GEMINI_MAX_TOKENS
        
        # Initialize model
        try:
            self.model = genai.GenerativeModel(self.model_name)
            logger.info(f"Initialized Gemini model: {self.model_name}")
        except Exception as e:
            logger.error(f"Error initializing Gemini: {e}")
            self.model = None
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def generate_diagnostic_report(
        self,
        patient_data: Dict[str, Any],
        clinical_notes: Optional[str] = None,
        lab_results: Optional[List[Dict[str, Any]]] = None,
        vital_signs: Optional[Dict[str, Any]] = None,
        image_paths: Optional[List[str]] = None,
        retrieved_context: Optional[str] = None,
        citations: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Generate comprehensive diagnostic report using Gemini.
        
        Args:
            patient_data: Patient demographics and history
            clinical_notes: Clinical observations and symptoms
            lab_results: Laboratory test results
            vital_signs: Vital measurements
            image_paths: Paths to medical images (X-rays, CT scans)
            retrieved_context: Context from RAG retrieval
            citations: Available citations (PubMed, SNOMED)
            
        Returns:
            Diagnostic report with conditions, evidence, citations
        """
        if self.model is None:
            raise RuntimeError("Gemini model not initialized. Check GOOGLE_API_KEY.")
        
        start_time = time.time()
        
        try:
            # Build comprehensive prompt
            prompt = self._build_diagnostic_prompt(
                patient_data=patient_data,
                clinical_notes=clinical_notes,
                lab_results=lab_results,
                vital_signs=vital_signs,
                retrieved_context=retrieved_context,
                citations=citations
            )
            
            # Prepare content parts (text + images)
            content_parts = [prompt]
            
            # Add medical images if provided
            if image_paths:
                for img_path in image_paths:
                    try:
                        img = Image.open(img_path)
                        content_parts.append(img)
                        logger.info(f"Added image to analysis: {img_path}")
                    except Exception as e:
                        logger.warning(f"Failed to load image {img_path}: {e}")
            
            # Configure generation
            generation_config = genai.types.GenerationConfig(
                temperature=self.temperature,
                max_output_tokens=self.max_tokens,
            )
            
            # Generate response
            response = self.model.generate_content(
                content_parts,
                generation_config=generation_config
            )
            
            generation_time = int((time.time() - start_time) * 1000)
            
            # Parse response
            report_text = response.text
            
            # Extract structured data from response
            report_data = self._parse_diagnostic_response(report_text)
            
            logger.info(f"Generated diagnostic report ({generation_time}ms)")
            
            return {
                "summary": report_data.get("summary", report_text),
                "suggested_conditions": report_data.get("suggested_conditions", []),
                "evidence_summary": report_data.get("evidence_summary", ""),
                "differential_diagnoses": report_data.get("differential_diagnoses", []),
                "recommended_actions": report_data.get("recommended_actions", []),
                "important_considerations": report_data.get("important_considerations", []),
                "citations": citations or [],
                "model_name": self.model_name,
                "generation_time_ms": generation_time,
                "confidence_level": report_data.get("confidence_level", "Medium")
            }
            
        except Exception as e:
            logger.error(f"Gemini generation failed: {e}", exc_info=True)
            raise
    
    def _build_diagnostic_prompt(
        self,
        patient_data: Dict[str, Any],
        clinical_notes: Optional[str],
        lab_results: Optional[List[Dict[str, Any]]],
        vital_signs: Optional[Dict[str, Any]],
        retrieved_context: Optional[str],
        citations: Optional[List[Dict[str, Any]]]
    ) -> str:
        """Build comprehensive diagnostic reasoning prompt."""
        
        prompt = f"""You are an expert medical AI assistant specialized in diagnostic reasoning and evidence-based medicine.

**CRITICAL INSTRUCTIONS:**
1. Provide a structured diagnostic analysis
2. Support all conclusions with specific evidence
3. Include differential diagnoses ranked by likelihood
4. Highlight any areas of uncertainty
5. Use professional medical terminology
6. Reference provided citations when applicable

**PATIENT INFORMATION:**
- Name: {patient_data.get('name', 'Unknown')}
- Age: {patient_data.get('age', 'Unknown')}
- Gender: {patient_data.get('gender', 'Unknown')}
"""

        # Add clinical notes
        if clinical_notes:
            prompt += f"\n**CLINICAL PRESENTATION:**\n{clinical_notes}\n"
        
        # Add vital signs
        if vital_signs:
            prompt += "\n**VITAL SIGNS:**\n"
            for key, value in vital_signs.items():
                prompt += f"- {key}: {value}\n"
        
        # Add lab results
        if lab_results:
            prompt += "\n**LABORATORY RESULTS:**\n"
            for lab in lab_results:
                abnormal = f" [{lab.get('is_abnormal', '')}]" if lab.get('is_abnormal') else ""
                prompt += f"- {lab.get('test_name')}: {lab.get('test_value')} {lab.get('unit')}{abnormal}\n"
        
        # Add retrieved context from RAG
        if retrieved_context:
            prompt += f"\n**RETRIEVED MEDICAL KNOWLEDGE:**\n{retrieved_context}\n"
        
        # Add available citations
        if citations:
            prompt += "\n**AVAILABLE CITATIONS:**\n"
            for cite in citations[:5]:  # Top 5
                if cite.get('type') == 'PMID':
                    prompt += f"- PMID-{cite.get('id')}: {cite.get('title', '')}\n"
                elif cite.get('type') == 'SNOMED':
                    prompt += f"- SNOMED-{cite.get('id')}: {cite.get('term', '')}\n"
        
        prompt += """
**REQUIRED OUTPUT FORMAT:**

Provide your analysis in the following structured format:

## Clinical Summary
[Brief overview of the case]

## Key Findings
- [List key clinical, laboratory, and imaging findings]

## Differential Diagnoses
1. **[Most Likely Diagnosis]** (Confidence: X%)
   - Evidence: [Specific supporting evidence]
   - SNOMED Code: [If applicable]
   
2. **[Second Most Likely]** (Confidence: X%)
   - Evidence: [Specific supporting evidence]

3. **[Third Alternative]** (Confidence: X%)
   - Evidence: [Specific supporting evidence]

## Evidence Summary
[Comprehensive explanation of how findings support the diagnoses]

## Recommended Next Steps
- [Immediate actions]
- [Additional testing if needed]
- [Treatment considerations]

## Important Considerations
- [Risk factors]
- [Red flags to monitor]
- [Follow-up requirements]

## Confidence Level
[Overall confidence: High/Medium/Low with justification]

**IMPORTANT DISCLAIMERS:**
- This analysis is for decision support only
- Final diagnosis and treatment decisions rest with the physician
- Consider patient-specific factors not captured in this analysis
- This is not a substitute for clinical judgment

Please provide your comprehensive diagnostic analysis now:
"""
        
        return prompt
    
    def _parse_diagnostic_response(self, response_text: str) -> Dict[str, Any]:
        """Parse structured data from Gemini response."""
        
        # Initialize result
        result = {
            "summary": "",
            "suggested_conditions": [],
            "evidence_summary": "",
            "differential_diagnoses": [],
            "recommended_actions": [],
            "important_considerations": [],
            "confidence_level": "Medium"
        }
        
        try:
            # Extract sections using markers
            sections = {
                "Clinical Summary": "",
                "Key Findings": "",
                "Differential Diagnoses": "",
                "Evidence Summary": "",
                "Recommended Next Steps": "",
                "Important Considerations": "",
                "Confidence Level": ""
            }
            
            current_section = None
            lines = response_text.split('\n')
            
            for line in lines:
                line = line.strip()
                
                # Check if line is a section header
                for section_name in sections.keys():
                    if section_name.lower() in line.lower() and line.startswith('#'):
                        current_section = section_name
                        break
                
                # Add content to current section
                if current_section and line and not line.startswith('#'):
                    sections[current_section] += line + "\n"
            
            # Parse differential diagnoses
            diff_diag_text = sections.get("Differential Diagnoses", "")
            diagnoses = []
            
            for line in diff_diag_text.split('\n'):
                if line.strip().startswith(('1.', '2.', '3.', '4.', '5.')):
                    # Extract diagnosis name and confidence
                    parts = line.split('(Confidence:')
                    if len(parts) >= 2:
                        diagnosis_name = parts[0].strip().lstrip('0123456789.').strip('*').strip()
                        confidence_str = parts[1].split('%')[0].strip().rstrip(')')
                        try:
                            confidence = int(confidence_str)
                        except:
                            confidence = 50
                        
                        diagnoses.append({
                            "condition": diagnosis_name,
                            "confidence": confidence,
                            "evidence": ""  # Could extract if needed
                        })
            
            result["summary"] = sections.get("Clinical Summary", "").strip()
            result["suggested_conditions"] = diagnoses
            result["evidence_summary"] = sections.get("Evidence Summary", "").strip()
            result["differential_diagnoses"] = diagnoses
            
            # Extract recommended actions
            actions_text = sections.get("Recommended Next Steps", "")
            actions = [line.strip('- ').strip() for line in actions_text.split('\n') if line.strip().startswith('-')]
            result["recommended_actions"] = actions
            
            # Extract considerations
            considerations_text = sections.get("Important Considerations", "")
            considerations = [line.strip('- ').strip() for line in considerations_text.split('\n') if line.strip().startswith('-')]
            result["important_considerations"] = considerations
            
            # Extract confidence
            confidence_text = sections.get("Confidence Level", "").lower()
            if "high" in confidence_text:
                result["confidence_level"] = "High"
            elif "low" in confidence_text:
                result["confidence_level"] = "Low"
            else:
                result["confidence_level"] = "Medium"
            
        except Exception as e:
            logger.error(f"Error parsing diagnostic response: {e}")
            result["summary"] = response_text
        
        return result
    
    async def analyze_image(
        self,
        image_path: str,
        query: Optional[str] = None
    ) -> str:
        """
        Analyze a medical image using Gemini Vision.
        
        Args:
            image_path: Path to medical image
            query: Specific question about the image
            
        Returns:
            Image analysis text
        """
        if self.model is None:
            raise RuntimeError("Gemini model not initialized")
        
        try:
            img = Image.open(image_path)
            
            prompt = query or """Analyze this medical image and provide:
1. Modality identification (X-ray, CT, MRI, etc.)
2. Anatomical region
3. Key findings or abnormalities
4. Clinical significance
5. Quality of the image

Be specific and use medical terminology."""
            
            response = self.model.generate_content([prompt, img])
            
            logger.info(f"Analyzed medical image: {image_path}")
            
            return response.text
            
        except Exception as e:
            logger.error(f"Image analysis failed: {e}", exc_info=True)
            raise

# Global instance
_gemini_service: Optional[GeminiService] = None

def get_gemini_service() -> GeminiService:
    """Get or create Gemini service instance."""
    global _gemini_service
    if _gemini_service is None:
        _gemini_service = GeminiService()
    return _gemini_service
