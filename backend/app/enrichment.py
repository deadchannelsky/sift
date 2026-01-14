"""
Enrichment Pipeline - Run LLM extraction tasks on messages

Handles:
- Loading messages from database
- Running 4 extraction tasks per message
- Parsing JSON responses
- Storing results with confidence scores
- Error handling and logging
"""
import json
import time
from datetime import datetime
from typing import List, Dict, Optional
from sqlalchemy.orm import Session

from app.models import Message, Extraction
from app.ollama_client import OllamaClient
from app.prompt_manager import PromptManager
from app.utils import logger


class ExtractionResult:
    """Result from a single extraction task"""

    def __init__(self, task_name: str, prompt_id: str, raw_response: str):
        self.task_name = task_name
        self.prompt_id = prompt_id
        self.raw_response = raw_response
        self.parsed_json = None
        self.confidence = None
        self.error = None
        self.processing_time_ms = 0

        self._parse_response()

    def _parse_response(self):
        """Parse LLM response as JSON"""
        try:
            # Try to extract JSON from response
            # Sometimes LLM includes markdown code blocks or extra text
            response = self.raw_response.strip()

            # Remove markdown code blocks if present
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
                response = response.strip()

            self.parsed_json = json.loads(response)

            # Extract confidence from parsed data
            if "extractions" in self.parsed_json:
                confidences = [
                    e.get("confidence", 0)
                    for e in self.parsed_json["extractions"]
                    if isinstance(e, dict)
                ]
                if confidences:
                    self.confidence = sum(confidences) / len(confidences)

        except json.JSONDecodeError as e:
            self.error = f"JSON parse error: {e}"
            logger.warning(f"Failed to parse JSON from {self.task_name}: {e}")
            logger.debug(f"Raw response: {self.raw_response[:200]}")
        except Exception as e:
            self.error = f"Unexpected error: {e}"
            logger.error(f"Error parsing extraction result: {e}")

    def is_valid(self) -> bool:
        """Check if extraction was successful"""
        return self.parsed_json is not None and self.error is None

    def __repr__(self):
        status = "✅" if self.is_valid() else "❌"
        conf = f"{self.confidence:.2f}" if self.confidence else "N/A"
        return f"ExtractionResult({status} {self.task_name}, conf={conf})"


class EnrichmentEngine:
    """Run enrichment pipeline on messages"""

    def __init__(self, ollama_client: OllamaClient, prompt_manager: PromptManager, db_session: Session):
        """Initialize enrichment engine

        Args:
            ollama_client: OllamaClient instance
            prompt_manager: PromptManager instance
            db_session: SQLAlchemy session for database access
        """
        self.ollama = ollama_client
        self.prompts = prompt_manager
        self.db = db_session
        self.task_names = ["task_a_projects", "task_b_stakeholders", "task_c_importance", "task_d_meetings"]
        self.stats = {
            "messages_processed": 0,
            "extractions_successful": 0,
            "extractions_failed": 0,
            "total_processing_time_ms": 0,
        }

    def enrich_message(self, message: Message, config: Dict) -> Dict[str, ExtractionResult]:
        """Enrich a single message with all 4 extraction tasks

        Args:
            message: Message object from database
            config: Config dict with prompt selections

        Returns:
            Dict mapping task_name -> ExtractionResult
        """
        results = {}
        message_data = {
            "subject": message.subject or "",
            "sender_email": message.sender_email or "",
            "sender_name": message.sender_name or "",
            "recipients": message.recipients or "",
            "cc": message.cc or "",
            "delivery_date": str(message.delivery_date) if message.delivery_date else "",
            "body_snippet": message.body_snippet or "",
            "body_full": message.body_full or "",
            "message_class": message.message_class or "",
        }

        for task_name in self.task_names:
            try:
                # Get prompt for this task from config
                prompt_id = config.get("prompts", {}).get(task_name, f"{task_name}_v1")
                prompt = self.prompts.get_prompt(prompt_id)

                if not prompt:
                    logger.warning(f"Prompt not found: {prompt_id}")
                    results[task_name] = ExtractionResult(task_name, prompt_id, "")
                    results[task_name].error = f"Prompt not found: {prompt_id}"
                    continue

                # Substitute variables in prompt
                filled_prompt = prompt.substitute_variables(message_data)

                # Call Ollama
                start_time = time.time()
                response = self.ollama.generate(filled_prompt)
                processing_time = (time.time() - start_time) * 1000

                # Parse response
                result = ExtractionResult(task_name, prompt_id, response)
                result.processing_time_ms = int(processing_time)

                results[task_name] = result

            except Exception as e:
                logger.error(f"Error enriching message {message.msg_id} with {task_name}: {e}")
                result = ExtractionResult(task_name, "", "")
                result.error = str(e)
                results[task_name] = result

        return results

    def store_extractions(self, message_id: int, msg_id: str, results: Dict[str, ExtractionResult]):
        """Store extraction results in database

        Args:
            message_id: Database ID of message
            msg_id: Public message ID
            results: Dict of task_name -> ExtractionResult
        """
        for task_name, result in results.items():
            try:
                if result.error:
                    # Store error
                    extraction = Extraction(
                        message_id=message_id,
                        task_name=task_name,
                        prompt_version=result.prompt_id,
                        extraction_json=json.dumps({"error": result.error}),
                        confidence="error",
                        processing_time_ms=result.processing_time_ms
                    )
                else:
                    # Store successful extraction
                    confidence_level = "high" if (result.confidence and result.confidence >= 0.75) else "medium" if (result.confidence and result.confidence >= 0.50) else "low"

                    extraction = Extraction(
                        message_id=message_id,
                        task_name=task_name,
                        prompt_version=result.prompt_id,
                        extraction_json=json.dumps(result.parsed_json),
                        confidence=confidence_level,
                        processing_time_ms=result.processing_time_ms
                    )
                    self.stats["extractions_successful"] += 1

                self.db.add(extraction)

            except Exception as e:
                logger.error(f"Error storing extraction for {msg_id}: {e}")
                self.stats["extractions_failed"] += 1

    def enrich_batch(self, message_ids: List[int], config: Dict, show_progress: bool = True) -> Dict:
        """Enrich a batch of messages

        Args:
            message_ids: List of message IDs to enrich
            config: Config dict with settings
            show_progress: Whether to log progress

        Returns:
            Stats dict with processing results
        """
        total = len(message_ids)
        logger.info(f"Starting enrichment of {total} messages")

        for idx, msg_id in enumerate(message_ids):
            try:
                # Get message from DB
                message = self.db.query(Message).filter_by(id=msg_id).first()
                if not message:
                    logger.warning(f"Message not found: {msg_id}")
                    continue

                # Skip if already enriched
                if message.enrichment_status == "completed":
                    logger.debug(f"Skipping already enriched message: {message.msg_id}")
                    continue

                # Enrich
                results = self.enrich_message(message, config)

                # Store results
                self.store_extractions(message.id, message.msg_id, results)

                # Update message status
                message.enrichment_status = "completed"
                message.processed_at = datetime.utcnow()
                self.db.commit()

                self.stats["messages_processed"] += 1

                if show_progress and (idx + 1) % max(1, total // 10) == 0:
                    logger.info(f"Enrichment progress: {idx + 1}/{total}")

            except Exception as e:
                logger.error(f"Error enriching message {msg_id}: {e}")
                try:
                    message.enrichment_status = "failed"
                    message.enrichment_error = str(e)
                    self.db.commit()
                except:
                    pass

        logger.info(f"Enrichment complete: {self.stats['messages_processed']} messages processed")
        return self.stats

    def get_enrichment_stats(self) -> Dict:
        """Get enrichment statistics

        Returns:
            Dict with processing stats
        """
        return self.stats.copy()
