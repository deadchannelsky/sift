"""
Ollama API client with model discovery and flexible model selection

Handles:
- Connection to Ollama server
- Model listing and selection
- Batch processing with retry logic
- Timeout handling and backoff
- Streaming response parsing
"""
import requests
import time
import json
from typing import List, Dict, Optional
from app.utils import logger


class OllamaModel:
    """Represents an available Ollama model"""
    def __init__(self, name: str, size_gb: float = None, quantization: str = None):
        self.name = name
        self.size_gb = size_gb
        self.quantization = quantization

    def __repr__(self):
        return f"OllamaModel(name={self.name}, size={self.size_gb}GB, quant={self.quantization})"


class OllamaClient:
    """Client for interacting with Ollama API"""

    def __init__(self, url: str, model: Optional[str] = None, timeout_seconds: int = 30, max_retries: int = 3, retry_backoff_ms: int = 500):
        """
        Initialize Ollama client

        Args:
            url: Ollama API URL (e.g., http://localhost:11434)
            model: Model name to use (can be None, set later with set_model())
            timeout_seconds: Request timeout
            max_retries: Number of retries on failure
            retry_backoff_ms: Initial backoff in milliseconds (exponential)
        """
        self.url = url.rstrip('/')
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_ms = retry_backoff_ms
        self.available_models = []

    def test_connection(self) -> bool:
        """Test if Ollama server is accessible"""
        try:
            response = requests.get(
                f"{self.url}/api/tags",
                timeout=5
            )
            response.raise_for_status()
            logger.info(f"✅ Connected to Ollama at {self.url}")
            return True
        except Exception as e:
            logger.error(f"❌ Cannot connect to Ollama at {self.url}: {e}")
            return False

    def list_models(self) -> List[OllamaModel]:
        """List all available models on Ollama server"""
        try:
            response = requests.get(
                f"{self.url}/api/tags",
                timeout=self.timeout_seconds
            )
            response.raise_for_status()
            data = response.json()

            models = []
            if "models" in data:
                for model_info in data["models"]:
                    name = model_info.get("name", "")
                    size_gb = model_info.get("size", 0) / 1024 / 1024 / 1024  # Convert bytes to GB
                    # Try to extract quantization from name (e.g., Q8_0, Q4_K_M)
                    quant = None
                    if "Q" in name:
                        parts = name.split(":")
                        if len(parts) > 1:
                            quant = parts[-1]

                    models.append(OllamaModel(name, size_gb, quant))

            self.available_models = models
            logger.info(f"Found {len(models)} models on Ollama server")
            return models

        except Exception as e:
            logger.error(f"Error listing models: {e}")
            return []

    def get_model_info(self, model_name: str) -> Dict:
        """Get detailed info about a specific model"""
        models = self.list_models()
        for model in models:
            if model.name == model_name:
                return {
                    "name": model.name,
                    "size_gb": model.size_gb,
                    "quantization": model.quantization
                }
        return None

    def set_model(self, model_name: str) -> bool:
        """Set the model to use for queries

        Args:
            model_name: Name of the model to use

        Returns:
            True if model exists and is set, False otherwise
        """
        models = self.list_models()
        available_names = [m.name for m in models]

        if model_name not in available_names:
            logger.error(f"❌ Model '{model_name}' not found on server")
            logger.info(f"Available models: {', '.join(available_names)}")
            return False

        self.model = model_name
        logger.info(f"✅ Model set to: {model_name}")
        return True

    def test_model(self) -> bool:
        """Test if selected model is available and working

        Returns:
            True if model is available and responsive
        """
        if not self.model:
            logger.error("No model selected")
            return False

        try:
            # Try a simple generation to verify model is loaded
            response = requests.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": "test",
                    "stream": False
                },
                timeout=5
            )
            if response.status_code == 200:
                logger.info(f"✅ Model '{self.model}' is available and responding")
                return True
            else:
                logger.error(f"❌ Model test failed: {response.status_code}")
                logger.error(f"Response: {response.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"❌ Model test error: {e}")
            return False

    def generate(self, prompt: str, stream: bool = False) -> str:
        """Generate response from Ollama

        Args:
            prompt: Input prompt
            stream: Whether to use streaming (not used in MVP, for future)

        Returns:
            Generated response text
        """
        if not self.model:
            raise ValueError("No model selected. Call set_model() first.")

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False
        }

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    f"{self.url}/api/generate",
                    json=payload,
                    timeout=self.timeout_seconds
                )
                response.raise_for_status()
                data = response.json()
                return data.get("response", "")

            except requests.exceptions.Timeout:
                last_error = f"Timeout (attempt {attempt + 1}/{self.max_retries})"
                if attempt < self.max_retries - 1:
                    backoff = self.retry_backoff_ms * (2 ** attempt) / 1000
                    logger.warning(f"Timeout, retrying in {backoff:.1f}s...")
                    time.sleep(backoff)

            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {e}"
                if attempt < self.max_retries - 1:
                    backoff = self.retry_backoff_ms * (2 ** attempt) / 1000
                    logger.warning(f"Connection error, retrying in {backoff:.1f}s...")
                    time.sleep(backoff)

            except Exception as e:
                logger.error(f"Error calling Ollama: {e}")
                raise

        # All retries exhausted
        raise RuntimeError(f"Ollama request failed after {self.max_retries} retries: {last_error}")

    def chat(self, messages: List[Dict[str, str]]) -> str:
        """Chat interface (alternative to generate)

        Args:
            messages: List of {"role": "user"|"assistant", "content": "..."} dicts

        Returns:
            Assistant response
        """
        if not self.model:
            raise ValueError("No model selected. Call set_model() first.")

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False
        }

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    f"{self.url}/api/chat",
                    json=payload,
                    timeout=self.timeout_seconds
                )
                response.raise_for_status()
                data = response.json()
                return data.get("message", {}).get("content", "")

            except requests.exceptions.Timeout:
                last_error = f"Timeout (attempt {attempt + 1}/{self.max_retries})"
                if attempt < self.max_retries - 1:
                    backoff = self.retry_backoff_ms * (2 ** attempt) / 1000
                    logger.warning(f"Timeout, retrying in {backoff:.1f}s...")
                    time.sleep(backoff)

            except Exception as e:
                logger.error(f"Error in chat: {e}")
                if attempt == self.max_retries - 1:
                    raise
                backoff = self.retry_backoff_ms * (2 ** attempt) / 1000
                time.sleep(backoff)

        raise RuntimeError(f"Ollama chat failed after {self.max_retries} retries: {last_error}")

    def batch_generate(self, prompts: List[str], show_progress: bool = True) -> List[str]:
        """Generate responses for multiple prompts

        Args:
            prompts: List of prompts
            show_progress: Whether to log progress

        Returns:
            List of responses in same order as prompts
        """
        responses = []
        total = len(prompts)

        for idx, prompt in enumerate(prompts):
            try:
                response = self.generate(prompt)
                responses.append(response)

                if show_progress and (idx + 1) % max(1, total // 10) == 0:
                    logger.info(f"Batch progress: {idx + 1}/{total}")

            except Exception as e:
                logger.error(f"Error processing prompt {idx + 1}/{total}: {e}")
                responses.append(None)  # Mark failed prompt

        return responses
