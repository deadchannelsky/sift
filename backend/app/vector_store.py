"""
Vector Store wrapper for RAG - Manages embeddings and semantic search via ChromaDB
"""

import json
import requests
from typing import List, Dict, Optional
from app.utils import logger


class NoOpEmbeddingFunction:
    """
    No-op embedding function for ChromaDB 0.3.21.

    ChromaDB 0.3.21 always initializes a default embedding function even when
    embeddings are provided directly. This class prevents loading the default
    SentenceTransformerEmbeddingFunction (which requires PyTorch and causes errors).

    Since we generate embeddings externally via Ollama and add them directly to
    ChromaDB, we don't need an embedding function at all.
    """
    def __call__(self, texts: List[str]) -> List[List[float]]:
        """Return empty embeddings - never called since we provide embeddings directly"""
        return []


class VectorStore:
    """ChromaDB-backed vector store for semantic search over enriched messages"""

    def __init__(self, ollama_url: str, persist_dir: str = "./data/chroma"):
        """Initialize vector store with ChromaDB

        Args:
            ollama_url: Ollama API endpoint (e.g., "http://localhost:11434")
            persist_dir: Directory for ChromaDB persistence
        """
        try:
            import chromadb
            import chromadb.config as cc
        except ImportError:
            raise ImportError("chromadb not installed. Run: pip install chromadb==0.3.21")

        self.ollama_url = ollama_url
        self.embedding_model = "hf.co/bartowski/granite-embedding-125m-english-GGUF:F16"

        # Initialize ChromaDB with persistent storage (0.3.21 API)
        config = cc.Settings(
            chroma_db_impl="duckdb+parquet",
            persist_directory=persist_dir,
            anonymized_telemetry=False
        )
        self.client = chromadb.Client(config)
        self.collection = self.client.get_or_create_collection(
            name="messages",
            metadata={"description": "Sift enriched email messages for RAG"},
            embedding_function=NoOpEmbeddingFunction()
        )

        logger.info(f"VectorStore initialized with ChromaDB at {persist_dir}")

    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text using Ollama's embedding endpoint

        Args:
            text: Text to embed

        Returns:
            768-dimensional embedding vector

        Raises:
            Exception: If Ollama embedding fails
        """
        try:
            response = requests.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": self.embedding_model, "prompt": text},
                timeout=30
            )
            response.raise_for_status()
            return response.json()["embedding"]
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            raise

    def index_message(
        self,
        message_id: int,
        subject: str,
        body: str,
        extractions: Dict,
        metadata: Dict
    ) -> None:
        """Add or update message in vector store

        Args:
            message_id: Database message ID
            subject: Email subject
            body: Email body (full or snippet)
            extractions: Dict of extracted data by task (projects, stakeholders, importance, meetings)
            metadata: Additional metadata (sender, date, etc.)
        """
        try:
            # Build rich text for embedding
            text_parts = [
                f"Subject: {subject}",
                f"Body: {body[:1000]}"  # Limit body size for embedding
            ]

            # Add extracted projects
            if "task_a_projects" in extractions:
                projects_data = extractions["task_a_projects"]
                if isinstance(projects_data, str):
                    projects_data = json.loads(projects_data)

                if "extractions" in projects_data:
                    projects = [p.get("project", "") for p in projects_data["extractions"]]
                    if projects:
                        text_parts.append(f"Projects: {', '.join(projects)}")

            # Add extracted stakeholders
            if "task_b_stakeholders" in extractions:
                stakeholders_data = extractions["task_b_stakeholders"]
                if isinstance(stakeholders_data, str):
                    stakeholders_data = json.loads(stakeholders_data)

                if "extractions" in stakeholders_data:
                    people = [s.get("stakeholder", "") for s in stakeholders_data["extractions"]]
                    if people:
                        text_parts.append(f"Stakeholders: {', '.join(people)}")

            # Add importance tier
            if "task_c_importance" in extractions:
                importance_data = extractions["task_c_importance"]
                if isinstance(importance_data, str):
                    importance_data = json.loads(importance_data)

                tier = importance_data.get("importance_tier", "")
                if tier:
                    text_parts.append(f"Importance: {tier}")

            combined_text = "\n\n".join(text_parts)

            # Generate embedding
            embedding = self.generate_embedding(combined_text)

            # Add to ChromaDB
            self.collection.add(
                ids=[str(message_id)],
                embeddings=[embedding],
                metadatas=[metadata],
                documents=[combined_text]
            )

            logger.debug(f"Indexed message {message_id} in vector store")

        except Exception as e:
            logger.error(f"Error indexing message {message_id}: {e}")
            raise

    def search(
        self,
        query: str,
        top_k: int = 10,
        where_filter: Optional[Dict] = None
    ) -> Dict:
        """Search for similar messages

        Args:
            query: Natural language query
            top_k: Number of results to return
            where_filter: Optional ChromaDB where filter for metadata (e.g., {"importance_tier": "CRITICAL"})

        Returns:
            Dict with keys:
                - ids: List of message IDs
                - distances: List of similarity distances
                - metadatas: List of metadata dicts
                - documents: List of document texts
        """
        try:
            # Generate query embedding
            query_embedding = self.generate_embedding(query)

            # Search ChromaDB
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where_filter,
                include=["embeddings", "metadatas", "documents", "distances"]
            )

            logger.debug(f"Search query found {len(results['ids'][0])} results (top-{top_k})")

            return {
                "ids": results["ids"][0] if results["ids"] else [],
                "distances": results["distances"][0] if results["distances"] else [],
                "metadatas": results["metadatas"][0] if results["metadatas"] else [],
                "documents": results["documents"][0] if results["documents"] else []
            }

        except Exception as e:
            logger.error(f"Error searching vector store: {e}")
            raise

    def get_collection_size(self) -> int:
        """Get number of messages in vector store"""
        return self.collection.count()

    def clear_collection(self) -> None:
        """Clear all embeddings from collection (for re-indexing)"""
        try:
            # Get all IDs
            all_data = self.collection.get()
            if all_data["ids"]:
                self.collection.delete(ids=all_data["ids"])
            logger.info("Cleared vector store collection")
        except Exception as e:
            logger.error(f"Error clearing collection: {e}")
            raise
