import httpx
from typing import List, Dict, Any, Optional
import logging
import time
import json
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings

logger = logging.getLogger(__name__)

class GroqService:
    """
    Service for Groq API (ultra-fast LLM inference).
    
    Groq provides:
    - Fast inference (up to 500 tokens/second)
    - Lower cost than GPT-4/Gemini
    - Good for text-only tasks
    - Multiple model options (Llama, Mixtral, Gemma)
    """
    
    def __init__(self):
        self.api_key = getattr(settings, 'GROQ_API_KEY', None)
        self.base_url = "https://api.groq.com/openai/v1"
        self.model = getattr(settings, 'GROQ_MODEL', 'mixtral-8x7b-32768')  # Default model
        self.temperature = getattr(settings, 'GROQ_TEMPERATURE', 0.3)
        self.max_tokens = getattr(settings, 'GROQ_MAX_TOKENS', 2048)
        
        if not self.api_key:
            logger.warning("GROQ_API_KEY not set. Groq service will not be available.")
        else:
            logger.info(f"Initialized Groq service with model: {self.model}")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def generate_completion(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Generate text completion using Groq.
        
        Args:
            prompt: User prompt
            system_prompt: Optional system instruction
            temperature: Sampling temperature (default: 0.3)
            max_tokens: Maximum tokens to generate
            
        Returns:
            Dict with generated text and metadata
        """
        if not self.api_key:
            raise RuntimeError("Groq API key not configured")
        
        start_time = time.time()
        
        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        # Prepare request
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                result = response.json()
            
            generation_time = int((time.time() - start_time) * 1000)
            
            # Extract response
            content = result['choices'][0]['message']['content']
            
            logger.info(f"Groq completion generated ({generation_time}ms, {len(content)} chars)")
            
            return {
                "content": content,
                "model": self.model,
                "generation_time_ms": generation_time,
                "tokens_used": result.get('usage', {}),
                "finish_reason": result['choices'][0].get('finish_reason')
            }
            
        except httpx.HTTPError as e:
            logger.error(f"Groq API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Groq generation failed: {e}", exc_info=True)
            raise
    
    async def summarize_clinical_note(
        self,
        clinical_note: str,
        max_length: int = 200
    ) -> str:
        """
        Summarize clinical notes using Groq.
        Fast and cost-effective for text summarization.
        
        Args:
            clinical_note: Full clinical note text
            max_length: Maximum words in summary
            
        Returns:
            Summarized clinical note
        """
        system_prompt = """You are a medical AI assistant specialized in clinical note summarization.
Extract the most important clinical information while maintaining medical accuracy.
Focus on: diagnosis, symptoms, findings, treatment plan, and follow-up."""
        
        prompt = f"""Summarize the following clinical note in {max_length} words or less:

{clinical_note}

Provide a concise, structured summary."""
        
        result = await self.generate_completion(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_length * 2  # Rough token estimate
        )
        
        return result['content']
    
    async def extract_medical_entities(
        self,
        text: str
    ) -> Dict[str, List[str]]:
        """
        Extract medical entities from text using Groq.
        
        Args:
            text: Medical text
            
        Returns:
            Dict with entity types and values
        """
        system_prompt = """You are a medical NLP assistant. Extract medical entities from text.
Return a JSON object with these categories:
- symptoms: list of symptoms
- diagnoses: list of diagnoses
- medications: list of medications
- procedures: list of procedures
- lab_tests: list of lab tests
- body_parts: list of anatomical locations"""
        
        prompt = f"""Extract medical entities from this text:

{text}

Return ONLY a JSON object, no explanation."""
        
        result = await self.generate_completion(
            prompt=prompt,
            system_prompt=system_prompt
        )
        
        try:
            # Parse JSON response
            entities = json.loads(result['content'])
            return entities
        except json.JSONDecodeError:
            logger.warning("Failed to parse entity extraction result")
            return {
                "symptoms": [],
                "diagnoses": [],
                "medications": [],
                "procedures": [],
                "lab_tests": [],
                "body_parts": []
            }
    
    async def generate_differential_diagnosis(
        self,
        symptoms: List[str],
        lab_results: Optional[Dict[str, Any]] = None,
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Generate differential diagnoses using Groq.
        Fast alternative to Gemini for text-only diagnostic reasoning.
        
        Args:
            symptoms: List of presenting symptoms
            lab_results: Optional lab test results
            patient_age: Patient age
            patient_gender: Patient gender
            
        Returns:
            List of differential diagnoses with confidence
        """
        system_prompt = """You are an expert diagnostic AI assistant.
Generate differential diagnoses based on clinical presentation.
Rank diagnoses by likelihood and provide supporting evidence.
Return a JSON array of diagnoses."""
        
        # Build patient context
        context = f"Patient: {patient_age or 'Unknown'} years old, {patient_gender or 'Unknown'} gender\n"
        context += f"Presenting Symptoms: {', '.join(symptoms)}\n"
        
        if lab_results:
            context += "Laboratory Results:\n"
            for test, value in lab_results.items():
                context += f"  - {test}: {value}\n"
        
        prompt = f"""{context}

Provide top 5 differential diagnoses as JSON array:
[
  {{
    "diagnosis": "condition name",
    "confidence": 0-100,
    "supporting_evidence": "brief explanation",
    "snomed_code": "code if known"
  }}
]

Return ONLY the JSON array, no explanation."""
        
        result = await self.generate_completion(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.2  # Lower temperature for factual output
        )
        
        try:
            diagnoses = json.loads(result['content'])
            return diagnoses
        except json.JSONDecodeError:
            logger.warning("Failed to parse differential diagnosis result")
            return []
    
    async def format_diagnostic_report(
        self,
        raw_findings: str,
        patient_data: Dict[str, Any]
    ) -> str:
        """
        Format raw findings into a professional diagnostic report.
        
        Args:
            raw_findings: Unstructured diagnostic findings
            patient_data: Patient demographics
            
        Returns:
            Formatted diagnostic report
        """
        system_prompt = """You are a medical report writer.
Format diagnostic findings into a professional clinical report.
Use proper medical terminology and clear structure."""
        
        prompt = f"""Format these diagnostic findings into a professional report:

Patient: {patient_data.get('name')} ({patient_data.get('age')} years, {patient_data.get('gender')})
MRN: {patient_data.get('mrn')}

Findings:
{raw_findings}

Create a structured report with sections:
1. Clinical Presentation
2. Diagnostic Findings
3. Assessment
4. Plan

Use professional medical language."""
        
        result = await self.generate_completion(
            prompt=prompt,
            system_prompt=system_prompt
        )
        
        return result['content']
    
    async def validate_clinical_reasoning(
        self,
        diagnosis: str,
        evidence: List[str]
    ) -> Dict[str, Any]:
        """
        Validate clinical reasoning and identify potential issues.
        
        Args:
            diagnosis: Proposed diagnosis
            evidence: Supporting evidence
            
        Returns:
            Validation result with feedback
        """
        system_prompt = """You are a medical quality assurance AI.
Evaluate clinical reasoning for logical consistency and evidence support.
Identify any gaps, contradictions, or concerns."""
        
        prompt = f"""Evaluate this clinical reasoning:

Proposed Diagnosis: {diagnosis}

Supporting Evidence:
{chr(10).join(f"- {e}" for e in evidence)}

Provide evaluation as JSON:
{{
  "is_valid": true/false,
  "confidence": 0-100,
  "concerns": ["list any concerns"],
  "missing_evidence": ["what's missing"],
  "recommendations": ["suggestions"]
}}"""
        
        result = await self.generate_completion(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.1
        )
        
        try:
            validation = json.loads(result['content'])
            return validation
        except json.JSONDecodeError:
            return {
                "is_valid": True,
                "confidence": 50,
                "concerns": [],
                "missing_evidence": [],
                "recommendations": []
            }

# Global instance
_groq_service: Optional[GroqService] = None

def get_groq_service() -> GroqService:
    """Get or create Groq service instance."""
    global _groq_service
    if _groq_service is None:
        _groq_service = GroqService()
    return _groq_service
