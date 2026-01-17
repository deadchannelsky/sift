"""
RAG Engine - Retrieval-Augmented Generation for multi-turn chat queries
"""

import json
from typing import Dict, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from app.utils import logger
from app.vector_store import VectorStore


class RAGEngine:
    """Multi-turn conversational RAG engine with semantic search"""

    def __init__(
        self,
        db_session: Session,
        ollama_client,
        vector_store: VectorStore,
        prompt_manager
    ):
        """Initialize RAG engine

        Args:
            db_session: SQLAlchemy database session
            ollama_client: OllamaClient for LLM calls
            vector_store: VectorStore instance for semantic search
            prompt_manager: PromptManager for loading RAG prompt (future use)
        """
        self.db = db_session
        self.ollama = ollama_client
        self.vector_store = vector_store
        self.prompts = prompt_manager

    def query(
        self,
        user_query: str,
        chat_history: List[Dict],
        top_k: int = 10,
        max_context_tokens: int = 4000
    ) -> Dict:
        """Process RAG query with chat history

        Args:
            user_query: User's natural language question
            chat_history: List of previous turns [{"role": "user"/"assistant", "content": "..."}]
            top_k: Number of messages to retrieve
            max_context_tokens: Max tokens for context (rough estimate)

        Returns:
            Dict with keys:
                - answer: LLM-generated response
                - citations: List of cited messages with details
                - retrieved_count: Number of messages retrieved
        """
        try:
            logger.info(f"Processing RAG query: {user_query[:100]}...")

            # 1. Search for relevant messages
            search_results = self.vector_store.search(user_query, top_k=top_k)
            message_ids = [int(msg_id) for msg_id in search_results["ids"]]

            if not message_ids:
                logger.warning("No similar messages found")
                return {
                    "answer": "I couldn't find any emails relevant to your query. Try rephrasing or asking about specific projects or people mentioned in your emails.",
                    "citations": [],
                    "retrieved_count": 0
                }

            # 2. Load full message details from database
            from app.models import Message, Extraction

            messages = self.db.query(Message).filter(Message.id.in_(message_ids)).all()
            extractions_by_msg = {}

            for msg in messages:
                exts = self.db.query(Extraction).filter_by(message_id=msg.id).all()
                extractions_by_msg[msg.id] = {
                    ext.task_name: json.loads(ext.extraction_json) for ext in exts
                }

            # 3. Build context for LLM (respect token limit)
            context_parts = []
            citations = []
            estimated_tokens = 0

            for msg in messages:
                formatted = self._format_message_context(msg, extractions_by_msg.get(msg.id, {}))
                msg_tokens = len(formatted.split()) * 1.3  # Rough estimate

                if estimated_tokens + msg_tokens > max_context_tokens and context_parts:
                    logger.debug(f"Context limit reached after {len(context_parts)} messages")
                    break

                context_parts.append(formatted)
                estimated_tokens += msg_tokens

                # Track citation
                citations.append({
                    "message_id": msg.id,
                    "subject": msg.subject,
                    "date": msg.delivery_date.isoformat() if msg.delivery_date else "",
                    "sender": msg.sender_email,
                    "snippet": (msg.body_full or msg.body_snippet or "")[:150]
                })

            # 4. Build prompt with context + chat history
            context_text = "\n\n---EMAIL SEPARATOR---\n\n".join(context_parts)

            system_prompt = (
                "You are an email intelligence assistant with access to a user's enriched email data. "
                "Your job is to answer questions based ONLY on the provided email context. "
                "Be conversational but cite specific emails when making claims. "
                "If information isn't in the context, clearly say so. "
                "Format your response for clarity, using bullet points or sections as needed."
            )

            # Build messages for LLM
            llm_messages = [
                {"role": "system", "content": system_prompt}
            ]

            # Add recent chat history (last 5 turns to maintain context)
            for turn in chat_history[-5:]:
                llm_messages.append({
                    "role": turn.get("role", "user"),
                    "content": turn.get("content", "")
                })

            # Add current query with context
            llm_messages.append({
                "role": "user",
                "content": f"Context from emails:\n\n{context_text}\n\n---\n\nQuestion: {user_query}"
            })

            # 5. Generate answer via LLM
            logger.debug(f"Sending prompt to LLM ({estimated_tokens} tokens estimated, {len(context_parts)} messages)")
            answer = self.ollama.chat(llm_messages)

            if not answer or not answer.strip():
                logger.warning("Empty response from LLM")
                return {
                    "answer": "I encountered an error processing your query. Please try again.",
                    "citations": citations[:5],  # Return top 5 anyway
                    "retrieved_count": len(messages)
                }

            logger.info(f"Generated answer for query ({len(citations)} citations)")

            return {
                "answer": answer,
                "citations": citations,
                "retrieved_count": len(messages)
            }

        except Exception as e:
            logger.error(f"Error processing RAG query: {e}")
            raise

    def _format_message_context(self, message, extractions: Dict) -> str:
        """Format message and extractions as readable context for LLM

        Args:
            message: Message object
            extractions: Dict of task_name -> extraction JSON

        Returns:
            Formatted text context
        """
        parts = [
            f"[MESSAGE {message.id}]",
            f"Subject: {message.subject or '(no subject)'}",
            f"From: {message.sender_name or 'Unknown'} <{message.sender_email}>",
            f"Date: {message.delivery_date.strftime('%Y-%m-%d %H:%M') if message.delivery_date else 'Unknown'}",
            f"To: {message.recipients or 'Unknown'}",
            "",
            f"Body:\n{message.body_full or message.body_snippet or '(empty)'}",
        ]

        # Add extracted intelligence
        if "task_a_projects" in extractions:
            data = extractions["task_a_projects"]
            if "extractions" in data:
                projects = [p.get("project", "") for p in data["extractions"] if p.get("project")]
                if projects:
                    parts.append(f"\nIdentified Projects: {', '.join(projects)}")

        if "task_b_stakeholders" in extractions:
            data = extractions["task_b_stakeholders"]
            if "extractions" in data:
                people = []
                for s in data["extractions"]:
                    name = s.get("stakeholder", "")
                    role = s.get("inferred_role", "")
                    if name:
                        people.append(f"{name} ({role})" if role else name)
                if people:
                    parts.append(f"Key Stakeholders: {', '.join(people)}")

        if "task_c_importance" in extractions:
            data = extractions["task_c_importance"]
            tier = data.get("importance_tier", "")
            if tier:
                parts.append(f"Importance Tier: {tier}")

        if "task_d_meetings" in extractions:
            data = extractions["task_d_meetings"]
            if data.get("is_meeting_related"):
                meeting_date = data.get("inferred_meeting_date", "")
                attendees = data.get("inferred_attendees", [])
                meeting_info = f"Meeting-related"
                if meeting_date:
                    meeting_info += f" (inferred date: {meeting_date})"
                if attendees:
                    meeting_info += f" - Attendees: {', '.join(attendees)}"
                parts.append(f"\n{meeting_info}")

        return "\n".join(parts)
