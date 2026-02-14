"""
Multi-modal embedding service for medical data.
Supports text (BioBERT) and image (BioViL/BiomedCLIP) embeddings.
"""
import torch
from transformers import AutoTokenizer, AutoModel, AutoImageProcessor
from PIL import Image
import numpy as np
from typing import List, Union, Optional
import logging
from pathlib import Path

from src.config import settings

logger = logging.getLogger(__name__)

class EmbeddingService:
    """
    Multi-modal embedding service for medical text and images.
    """
    
    def __init__(self):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info(f"Using device: {self.device}")
        
        # Initialize text model (BioBERT)
        self.text_tokenizer = None
        self.text_model = None
        self._init_text_model()
        
        # Initialize image model (BiomedCLIP)
        self.image_processor = None
        self.image_model = None
        self._init_image_model()
    
    def _init_text_model(self):
        """Initialize BioBERT for text embeddings."""
        try:
            logger.info(f"Loading text model: {settings.TEXT_EMBEDDING_MODEL}")
            self.text_tokenizer = AutoTokenizer.from_pretrained(settings.TEXT_EMBEDDING_MODEL)
            self.text_model = AutoModel.from_pretrained(settings.TEXT_EMBEDDING_MODEL)
            self.text_model.to(self.device)
            self.text_model.eval()
            logger.info("Text embedding model loaded successfully")
        except Exception as e:
            logger.error(f"Error loading text model: {e}")
            logger.warning("Text embeddings will not be available")
    
    def _init_image_model(self):
        """Initialize BiomedCLIP for image embeddings."""
        try:
            logger.info(f"Loading image model: {settings.IMAGE_EMBEDDING_MODEL}")
            # BiomedCLIP requires the open_clip library
            import open_clip
            
            # Load BiomedCLIP model from HuggingFace hub via open_clip
            model_name = f"hf-hub:{settings.IMAGE_EMBEDDING_MODEL}"
            self.image_model, _, self.image_processor = open_clip.create_model_and_transforms(
                model_name,
                device=self.device
            )
            self.image_model.eval()
            
            logger.info("Image embedding model loaded successfully")
        except Exception as e:
            logger.error(f"Error loading image model: {e}")
            logger.warning("Image embeddings will not be available")
            # Set to None to indicate unavailable
            self.image_model = None
            self.image_processor = None
    
    def embed_text(self, texts: Union[str, List[str]]) -> np.ndarray:
        """
        Generate embeddings for clinical text using BioBERT.
        
        Args:
            texts: Single text or list of texts
            
        Returns:
            Numpy array of embeddings (n, 768)
        """
        if self.text_model is None:
            raise RuntimeError("Text embedding model not initialized")
        
        # Ensure texts is a list
        if isinstance(texts, str):
            texts = [texts]
        
        embeddings_list = []
        batch_size = settings.EMBEDDING_BATCH_SIZE
        
        with torch.no_grad():
            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i:i + batch_size]
                
                # Tokenize
                encoded = self.text_tokenizer(
                    batch_texts,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors='pt'
                ).to(self.device)
                
                # Get embeddings
                outputs = self.text_model(**encoded)
                
                # Use [CLS] token embedding (first token)
                embeddings = outputs.last_hidden_state[:, 0, :]
                
                # Normalize embeddings for cosine similarity
                embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
                
                embeddings_list.append(embeddings.cpu().numpy())
        
        # Concatenate all batches
        all_embeddings = np.vstack(embeddings_list)
        
        logger.info(f"Generated {len(all_embeddings)} text embeddings")
        return all_embeddings
    
    def embed_image(self, image_paths: Union[str, List[str]]) -> np.ndarray:
        """
        Generate embeddings for medical images using BiomedCLIP.
        
        Args:
            image_paths: Single image path or list of image paths
            
        Returns:
            Numpy array of embeddings (n, 768)
        """
        if self.image_model is None:
            raise RuntimeError("Image embedding model not initialized")
        
        # Ensure image_paths is a list
        if isinstance(image_paths, str):
            image_paths = [image_paths]
        
        embeddings_list = []
        
        with torch.no_grad():
            for img_path in image_paths:
                try:
                    # Load and preprocess image
                    image = Image.open(img_path).convert('RGB')
                    
                    # Process image with open_clip transforms
                    image_tensor = self.image_processor(image).unsqueeze(0).to(self.device)
                    
                    # Get image embeddings from vision encoder
                    image_features = self.image_model.encode_image(image_tensor)
                    
                    # Normalize embeddings
                    image_features = torch.nn.functional.normalize(image_features, p=2, dim=1)
                    
                    embeddings_list.append(image_features.cpu().numpy())
                    
                except Exception as e:
                    logger.error(f"Error processing image {img_path}: {e}")
                    # Get the dimension from the model or use a default
                    dim = 512 if self.image_model else 768  # BiomedCLIP typically uses 512
                    embeddings_list.append(np.zeros((1, dim)))
        
        all_embeddings = np.vstack(embeddings_list)
        
        logger.info(f"Generated {len(all_embeddings)} image embeddings")
        return all_embeddings
    
    def embed_multimodal(
        self,
        texts: Optional[List[str]] = None,
        image_paths: Optional[List[str]] = None
    ) -> np.ndarray:
        """
        Generate embeddings for multi-modal input (text + images).
        Concatenates text and image embeddings.
        
        Args:
            texts: List of clinical texts
            image_paths: List of image paths
            
        Returns:
            Numpy array of embeddings
        """
        embeddings = []
        
        if texts:
            text_emb = self.embed_text(texts)
            embeddings.append(text_emb)
        
        if image_paths:
            image_emb = self.embed_image(image_paths)
            embeddings.append(image_emb)
        
        if not embeddings:
            raise ValueError("Must provide either texts or image_paths")
        
        # Concatenate embeddings if both modalities present
        if len(embeddings) > 1:
            # Average pooling of modalities
            combined = np.mean(embeddings, axis=0)
        else:
            combined = embeddings[0]
        
        return combined
    
    def embed_clinical_note(self, note_text: str, chunk_size: int = 500) -> np.ndarray:
        """
        Embed a clinical note by chunking and averaging embeddings.
        
        Args:
            note_text: Full clinical note text
            chunk_size: Characters per chunk
            
        Returns:
            Average embedding for the note
        """
        # Split into chunks
        chunks = []
        for i in range(0, len(note_text), chunk_size):
            chunk = note_text[i:i + chunk_size]
            if chunk.strip():
                chunks.append(chunk)
        
        if not chunks:
            chunks = [note_text]
        
        # Generate embeddings for all chunks
        chunk_embeddings = self.embed_text(chunks)
        
        # Average embeddings
        note_embedding = np.mean(chunk_embeddings, axis=0, keepdims=True)
        
        return note_embedding

# Global instance
_embedding_service: Optional[EmbeddingService] = None

def get_embedding_service() -> EmbeddingService:
    """Get or create embedding service instance."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service

async def init_embedding_service():
    """Initialize embedding service."""
    global _embedding_service
    _embedding_service = EmbeddingService()
    logger.info("Embedding service initialized")

async def close_embedding_service():
    """Close embedding service and free resources."""
    global _embedding_service
    if _embedding_service:
        # Free GPU memory
        if _embedding_service.text_model:
            del _embedding_service.text_model
        if _embedding_service.image_model:
            del _embedding_service.image_model
        
        torch.cuda.empty_cache() if torch.cuda.is_available() else None
        _embedding_service = None
    logger.info("Embedding service closed")
