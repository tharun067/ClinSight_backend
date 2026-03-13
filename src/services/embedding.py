import asyncio
import numpy as np
import warnings
from collections import OrderedDict
from typing import List, Union, Optional, Dict
import logging

from src.config import settings

logger = logging.getLogger(__name__)


class ServiceNotReadyError(RuntimeError):
    """Raised when a request arrives before background model loading completes."""
    pass


class EmbeddingService:
    def __init__(self):
        self._torch = None
        self._pil_image = None
        self._text_cache: OrderedDict[str, np.ndarray] = OrderedDict()

        torch = self._get_torch()
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info(f"Embedding service using device: {self.device}")
        self.text_tokenizer = None
        self.text_model = None
        self.image_processor = None
        self.image_model = None
        self._init_text_model()
        self._init_image_model()

    def _get_torch(self):
        if self._torch is None:
            import torch

            self._torch = torch
        return self._torch

    def _get_pil_image(self):
        if self._pil_image is None:
            from PIL import Image

            self._pil_image = Image
        return self._pil_image

    def _get_cached_text_embedding(self, text: str) -> Optional[np.ndarray]:
        cached = self._text_cache.get(text)
        if cached is None:
            return None

        self._text_cache.move_to_end(text)
        return cached.copy()

    def _store_cached_text_embedding(self, text: str, embedding: np.ndarray) -> None:
        self._text_cache[text] = embedding.astype("float32", copy=True)
        self._text_cache.move_to_end(text)
        while len(self._text_cache) > settings.EMBEDDING_CACHE_SIZE:
            self._text_cache.popitem(last=False)

    def _init_text_model(self):
        try:
            torch = self._get_torch()
            from transformers import AutoModel, AutoTokenizer

            logger.info(f"Loading text model: {settings.TEXT_EMBEDDING_MODEL}")
            self.text_tokenizer = AutoTokenizer.from_pretrained(settings.TEXT_EMBEDDING_MODEL)
            self.text_model = AutoModel.from_pretrained(settings.TEXT_EMBEDDING_MODEL)
            self.text_model.to(self.device)
            self.text_model.eval()
            logger.info("Text embedding model loaded")
        except Exception as e:
            logger.error(f"Failed to load text model: {e}")

    def _init_image_model(self):
        try:
            warnings.filterwarnings(
                "ignore",
                message=r"Importing from timm\.models\.layers is deprecated.*",
                category=FutureWarning,
            )
            warnings.filterwarnings(
                "ignore",
                message=r"You are using `torch\.load` with `weights_only=False`.*",
                category=FutureWarning,
                module=r"open_clip\.factory",
            )
            import open_clip
            logger.info(f"Loading image model: {settings.IMAGE_EMBEDDING_MODEL}")
            model_name = f"hf-hub:{settings.IMAGE_EMBEDDING_MODEL}"
            self.image_model, _, self.image_processor = open_clip.create_model_and_transforms(
                model_name, device=self.device
            )
            self.image_model.eval()
            logger.info("Image embedding model loaded")
        except Exception as e:
            logger.error(f"Failed to load image model: {e}")
            self.image_model = None
            self.image_processor = None

    def embed_text(self, texts: Union[str, List[str]]) -> np.ndarray:
        """
        Generate BioBERT embeddings for clinical text.
        Returns ndarray of shape (n, settings.VECTOR_DIMENSION).
        """
        if self.text_model is None:
            raise RuntimeError("Text embedding model not initialized")

        if isinstance(texts, str):
            texts = [texts]

        cached_results: Dict[int, np.ndarray] = {}
        uncached_indices: List[int] = []
        uncached_texts: List[str] = []
        for index, text in enumerate(texts):
            cached = self._get_cached_text_embedding(text)
            if cached is not None:
                cached_results[index] = cached
            else:
                uncached_indices.append(index)
                uncached_texts.append(text)

        computed_embeddings = []
        if uncached_texts:
            torch = self._get_torch()
            with torch.no_grad():
                for i in range(0, len(uncached_texts), settings.EMBEDDING_BATCH_SIZE):
                    batch = uncached_texts[i : i + settings.EMBEDDING_BATCH_SIZE]
                    encoded = self.text_tokenizer(
                        batch, padding=True, truncation=True, max_length=512, return_tensors="pt"
                    ).to(self.device)
                    outputs = self.text_model(**encoded)
                    emb = outputs.last_hidden_state[:, 0, :]
                    emb = torch.nn.functional.normalize(emb, p=2, dim=1)
                    computed_embeddings.append(emb.cpu().numpy().astype("float32"))

            computed_array = np.vstack(computed_embeddings)
            for index, text, embedding in zip(uncached_indices, uncached_texts, computed_array):
                self._store_cached_text_embedding(text, embedding)
                cached_results[index] = embedding

        return np.vstack([cached_results[i] for i in range(len(texts))])

    def embed_image(self, image_paths: Union[str, List[str]]) -> np.ndarray:
        """
        Generate BiomedCLIP embeddings for medical images.
        Falls back to zero-vectors of shape (n, settings.VECTOR_DIMENSION) on failure
        so FAISS never receives a mismatched dimension.
        """
        if self.image_model is None:
            raise RuntimeError("Image embedding model not initialized")

        if isinstance(image_paths, str):
            image_paths = [image_paths]

        torch = self._get_torch()
        image_module = self._get_pil_image()
        embeddings_list = []
        with torch.no_grad():
            for img_path in image_paths:
                try:
                    image = image_module.open(img_path).convert("RGB")
                    image_tensor = self.image_processor(image).unsqueeze(0).to(self.device)
                    features = self.image_model.encode_image(image_tensor)
                    features = torch.nn.functional.normalize(features, p=2, dim=1)

                    vec = features.cpu().numpy()
                    # Pad or truncate to settings.VECTOR_DIMENSION so FAISS never mismatches
                    if vec.shape[1] != settings.VECTOR_DIMENSION:
                        padded = np.zeros((1, settings.VECTOR_DIMENSION), dtype="float32")
                        copy_dim = min(vec.shape[1], settings.VECTOR_DIMENSION)
                        padded[0, :copy_dim] = vec[0, :copy_dim]
                        vec = padded

                    embeddings_list.append(vec)
                except Exception as e:
                    logger.error(f"Error processing image {img_path}: {e}")
                    # Zero-vector with CORRECT dimension — avoids FAISS crash
                    embeddings_list.append(
                        np.zeros((1, settings.VECTOR_DIMENSION), dtype="float32")
                    )

        return np.vstack(embeddings_list)

    def embed_multimodal(
        self,
        texts: Optional[List[str]] = None,
        image_paths: Optional[List[str]] = None,
    ) -> np.ndarray:
        """Average-pool text and image embeddings when both are provided."""
        parts = []
        if texts:
            parts.append(self.embed_text(texts))
        if image_paths:
            parts.append(self.embed_image(image_paths))
        if not parts:
            raise ValueError("Must provide texts or image_paths")
        return np.mean(parts, axis=0) if len(parts) > 1 else parts[0]

    def embed_clinical_note(self, note_text: str, chunk_size: int = 500) -> np.ndarray:
        """Chunk a long note and return the mean embedding."""
        chunks = [
            note_text[i : i + chunk_size]
            for i in range(0, len(note_text), chunk_size)
            if note_text[i : i + chunk_size].strip()
        ] or [note_text]
        return np.mean(self.embed_text(chunks), axis=0, keepdims=True)


# ── Global singleton ────────────────────────────────────────────────────────

_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        raise ServiceNotReadyError(
            "Embedding service is not ready yet. "
            "Models are still loading in the background — please retry in a moment."
        )
    return _embedding_service


def embedding_service_ready() -> bool:
    return _embedding_service is not None


async def init_embedding_service():
    global _embedding_service
    # Run blocking model loading in a thread so it doesn't block the event loop
    _embedding_service = await asyncio.to_thread(EmbeddingService)
    logger.info("Embedding service initialized")


async def close_embedding_service():
    global _embedding_service
    if _embedding_service:
        if _embedding_service.text_model:
            del _embedding_service.text_model
        if _embedding_service.image_model:
            del _embedding_service.image_model
        torch_module = getattr(_embedding_service, "_torch", None)
        if torch_module and torch_module.cuda.is_available():
            torch_module.cuda.empty_cache()
        _embedding_service = None
    logger.info("Embedding service closed")