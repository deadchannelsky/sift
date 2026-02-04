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

            # Log empty responses
            if not response:
                logger.warning(f"[{self.task_name}] LLM returned EMPTY response")
                self.error = "Empty response from LLM"
                return

            # Remove markdown code blocks if present
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
                response = response.strip()

            # Try to find JSON object in response (handle trailing text)
            # Look for opening brace and find matching close
            if not response.startswith("{") and "{" in response:
                # JSON might be preceded by text
                json_start = response.find("{")
                response = response[json_start:]
                logger.debug(f"[{self.task_name}] Trimmed prefix text before JSON")

            # Try parsing
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
            # Show more of the response to understand what's happening
            preview = self.raw_response[:500] if self.raw_response else "(empty)"
            logger.warning(f"[{self.task_name}] RAW RESPONSE PREVIEW: {preview}")
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

    # Max body length before truncation (leave room for prompt overhead)
    MAX_BODY_CHARS = 28000

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
        # Standard tasks run in parallel
        self.task_names = ["task_a_projects", "task_b_stakeholders", "task_c_importance", "task_d_meetings"]
        # Task E is special - two-prompt sequential (summary first, then sentiment)
        self.task_e_enabled = True
        self.stats = {
            "messages_processed": 0,
            "extractions_successful": 0,
            "extractions_failed": 0,
            "total_processing_time_ms": 0,
            "task_e_processed": 0,
            "bodies_truncated": 0,
        }

    def _truncate_body(self, body: str) -> str:
        """Smart truncation for bodies exceeding context limit.

        Keeps first half and last half to preserve opening context and conclusions.
        """
        if not body or len(body) <= self.MAX_BODY_CHARS:
            return body

        self.stats["bodies_truncated"] += 1
        half = self.MAX_BODY_CHARS // 2
        return body[:half] + "\n\n[... content truncated for length ...]\n\n" + body[-half:]

    def enrich_message(self, message: Message, config: Dict) -> Dict[str, ExtractionResult]:
        """Enrich a single message with all extraction tasks

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

        # Run standard tasks A-D
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

                # Validate stakeholder extractions against actual message recipients
                if task_name == "task_b_stakeholders":
                    result = self.validate_stakeholder_extraction(result, message)

                results[task_name] = result

            except Exception as e:
                logger.error(f"Error enriching message {message.msg_id} with {task_name}: {e}")
                result = ExtractionResult(task_name, "", "")
                result.error = str(e)
                results[task_name] = result

        # Run Task E (two-prompt sequential: summary -> sentiment)
        if self.task_e_enabled:
            task_e_results = self._run_task_e(message, message_data, config)
            results.update(task_e_results)

        return results

    def _run_task_e(self, message: Message, message_data: Dict, config: Dict) -> Dict[str, ExtractionResult]:
        """Run Task E: Summary + Sentiment (two prompts, sequential)

        E1 (Summary) runs first with full body, produces summary/email_type/topics.
        E2 (Sentiment) runs second, receives E1 output for context efficiency.

        Args:
            message: Message object
            message_data: Pre-extracted message fields
            config: Config dict

        Returns:
            Dict with task_e_summary and task_e_sentiment results
        """
        results = {}

        # === TASK E1: Summary & Classification ===
        try:
            prompt_id_e1 = config.get("prompts", {}).get("task_e_summary", "task_e_summary_v1")
            prompt_e1 = self.prompts.get_prompt(prompt_id_e1)

            if not prompt_e1:
                logger.warning(f"Task E1 prompt not found: {prompt_id_e1}")
                result_e1 = ExtractionResult("task_e_summary", prompt_id_e1, "")
                result_e1.error = f"Prompt not found: {prompt_id_e1}"
                results["task_e_summary"] = result_e1
                return results

            # Prepare E1 data with truncated full body
            e1_data = message_data.copy()
            body = message_data.get("body_full") or message_data.get("body_snippet") or ""
            e1_data["body"] = self._truncate_body(body)

            # Call LLM for E1
            filled_prompt_e1 = prompt_e1.substitute_variables(e1_data)
            start_time = time.time()
            response_e1 = self.ollama.generate(filled_prompt_e1)
            processing_time_e1 = (time.time() - start_time) * 1000

            result_e1 = ExtractionResult("task_e_summary", prompt_id_e1, response_e1)
            result_e1.processing_time_ms = int(processing_time_e1)
            results["task_e_summary"] = result_e1

            if not result_e1.is_valid():
                logger.warning(f"Task E1 failed for {message.msg_id}, skipping E2")
                return results

            self.stats["task_e_processed"] += 1

        except Exception as e:
            logger.error(f"Error in Task E1 for {message.msg_id}: {e}")
            result_e1 = ExtractionResult("task_e_summary", "", "")
            result_e1.error = str(e)
            results["task_e_summary"] = result_e1
            return results

        # === TASK E2: Sentiment & Relationship ===
        try:
            prompt_id_e2 = config.get("prompts", {}).get("task_e_sentiment", "task_e_sentiment_v1")
            prompt_e2 = self.prompts.get_prompt(prompt_id_e2)

            if not prompt_e2:
                logger.warning(f"Task E2 prompt not found: {prompt_id_e2}")
                result_e2 = ExtractionResult("task_e_sentiment", prompt_id_e2, "")
                result_e2.error = f"Prompt not found: {prompt_id_e2}"
                results["task_e_sentiment"] = result_e2
                return results

            # Prepare E2 data with E1 results for context
            e1_json = result_e1.parsed_json
            e2_data = message_data.copy()
            e2_data["summary"] = e1_json.get("summary", "")
            e2_data["email_type"] = e1_json.get("email_type", "unknown")

            # Call LLM for E2
            filled_prompt_e2 = prompt_e2.substitute_variables(e2_data)
            start_time = time.time()
            response_e2 = self.ollama.generate(filled_prompt_e2)
            processing_time_e2 = (time.time() - start_time) * 1000

            result_e2 = ExtractionResult("task_e_sentiment", prompt_id_e2, response_e2)
            result_e2.processing_time_ms = int(processing_time_e2)
            results["task_e_sentiment"] = result_e2

        except Exception as e:
            logger.error(f"Error in Task E2 for {message.msg_id}: {e}")
            result_e2 = ExtractionResult("task_e_sentiment", "", "")
            result_e2.error = str(e)
            results["task_e_sentiment"] = result_e2

        return results

    def validate_stakeholder_extraction(
        self,
        extraction_result: ExtractionResult,
        message: Message
    ) -> ExtractionResult:
        """
        Validate stakeholder extractions against actual message recipients

        Filters out hallucinated emails that don't appear in message.recipients or message.cc

        Args:
            extraction_result: Raw LLM extraction result
            message: Original message with real recipient data

        Returns:
            Filtered ExtractionResult with only valid stakeholders
        """
        if not extraction_result.is_valid() or not extraction_result.parsed_json:
            return extraction_result

        # Build set of valid emails from message
        valid_emails = set()

        # Add sender
        if message.sender_email:
            valid_emails.add(message.sender_email.lower().strip())

        # Add recipients
        if message.recipients:
            for email in message.recipients.split(','):
                email = email.strip().lower()
                if email:
                    valid_emails.add(email)

        # Add CC
        if message.cc:
            for email in message.cc.split(','):
                email = email.strip().lower()
                if email:
                    valid_emails.add(email)

        # Filter extractions
        original_json = extraction_result.parsed_json
        if "extractions" not in original_json:
            return extraction_result

        filtered_extractions = []
        rejected_count = 0

        for stakeholder in original_json["extractions"]:
            extracted_email = stakeholder.get("email", "").lower().strip()

            if extracted_email in valid_emails:
                # Valid - keep it
                filtered_extractions.append(stakeholder)
            else:
                # Hallucinated - log and reject
                rejected_count += 1
                logger.warning(
                    f"REJECTED hallucinated stakeholder: {stakeholder.get('stakeholder')} "
                    f"({extracted_email}) - not in message recipients. "
                    f"Valid emails: {valid_emails}"
                )

        # Update extraction result
        extraction_result.parsed_json["extractions"] = filtered_extractions

        if rejected_count > 0:
            logger.info(
                f"Stakeholder validation: {len(filtered_extractions)} valid, "
                f"{rejected_count} rejected (hallucinated)"
            )

        return extraction_result

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
