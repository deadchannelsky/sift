"""
PST file parser - Extract messages and store to SQLite using libratom
"""
from libratom.lib.pff import PffArchive
import hashlib
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session
from typing import Optional, Tuple

from .models import Conversation, Message, Attachment
from .utils import logger, ProgressTracker, TaskTimer, ensure_data_dir


class PSTParser:
    """Parse PST file and extract messages to SQLite"""

    def __init__(self, db_session: Session, ollama_client=None, prompt_manager=None, config=None):
        self.db_session = db_session
        self.ollama_client = ollama_client
        self.prompt_manager = prompt_manager
        self.config = config or {}
        self.message_count = 0
        self.conversation_count = 0
        self.error_count = 0
        self.filtered_count = 0  # Track spurious emails filtered

    def parse_file(
        self,
        pst_path: str,
        date_start: str = "2025-10-01",
        date_end: str = "2025-12-31",
        min_conversation_messages: int = 3,
        max_messages: Optional[int] = None
    ) -> Tuple[int, int, int]:
        """
        Parse PST file and store to database

        Args:
            pst_path: Path to .pst file
            date_start: Start date (YYYY-MM-DD)
            date_end: End date (YYYY-MM-DD)
            min_conversation_messages: Minimum messages in thread to include
            max_messages: Maximum messages to parse (None for unlimited)

        Returns:
            Tuple of (message_count, conversation_count, error_count)
        """
        ensure_data_dir()

        if not Path(pst_path).exists():
            logger.error(f"PST file not found: {pst_path}")
            raise FileNotFoundError(f"PST file not found: {pst_path}")

        # Parse dates
        try:
            date_start_dt = datetime.strptime(date_start, "%Y-%m-%d")
            date_end_dt = datetime.strptime(date_end, "%Y-%m-%d")
        except ValueError as e:
            logger.error(f"Invalid date format: {e}")
            raise

        logger.info(f"Opening PST file: {pst_path}")

        try:
            with TaskTimer(f"PST parsing: {Path(pst_path).name}"):
                # Open PST archive using libratom (returns pypff objects)
                archive = PffArchive(pst_path)

                # Dictionary to track conversations: topic -> list of messages
                conversations = {}

                # Track total messages extracted (for max_messages limit)
                # Use dict for mutability across recursive calls
                counter = {"count": 0, "scanned": 0, "date_filtered": 0}

                logger.info(f"Date range filter: {date_start} to {date_end}")

                # Extract messages from all folders (libratom returns pypff folder objects)
                for folder in archive.folders():
                    try:
                        # Iterate through messages in this folder using pypff API
                        for msg_idx in range(folder.number_of_sub_messages):
                            # Check if we've hit the max_messages limit
                            if max_messages and counter["count"] >= max_messages:
                                logger.info(f"Reached max_messages limit: {max_messages}")
                                break

                            try:
                                message = folder.get_sub_message(msg_idx)
                                msg_data = self._extract_message(message)
                                counter["scanned"] += 1

                                if msg_data and self._is_in_date_range(
                                    msg_data["delivery_date"],
                                    date_start_dt,
                                    date_end_dt
                                ):
                                    # Group by conversation topic
                                    topic = msg_data["conversation_topic"]
                                    if topic not in conversations:
                                        conversations[topic] = []
                                    conversations[topic].append(msg_data)
                                    counter["count"] += 1
                                else:
                                    counter["date_filtered"] += 1

                            except Exception as e:
                                logger.warning(f"Error extracting message: {e}")
                                self.error_count += 1

                        # Check max_messages before recursing into subfolders
                        if max_messages and counter["count"] >= max_messages:
                            logger.info(f"Reached max_messages limit: {max_messages}")
                            break

                        # Recurse into subfolders
                        for subfolder_idx in range(folder.number_of_sub_folders):
                            try:
                                subfolder = folder.get_sub_folder(subfolder_idx)
                                self._process_folder(subfolder, conversations, date_start_dt, date_end_dt, max_messages, counter)
                            except Exception as e:
                                logger.warning(f"Error accessing subfolder: {e}")

                    except Exception as e:
                        logger.warning(f"Error processing folder: {e}")

                logger.info(
                    f"Message extraction: {counter['count']} accepted, "
                    f"{counter['date_filtered']} filtered by date range, "
                    f"{counter['scanned']} total scanned"
                )
                logger.info(
                    f"Date range filter: {date_start_dt.date()} to {date_end_dt.date()} "
                    f"({counter['date_filtered']} messages outside this range)"
                )
                logger.info(f"Found {len(conversations)} conversations after date range filter")

                # Show message distribution before filtering by minimum
                total_messages_in_convs = sum(len(msgs) for msgs in conversations.values())
                logger.info(f"Total messages in conversations: {total_messages_in_convs}")

                # Filter by minimum message count and store to DB
                stored_count = 0
                conversations_meeting_threshold = 0
                conversations_filtered_out = 0

                for topic, messages in conversations.items():
                    if len(messages) >= min_conversation_messages:
                        stored_count += self._store_conversation(topic, messages)
                        conversations_meeting_threshold += 1
                    else:
                        conversations_filtered_out += 1

                logger.info(
                    f"Conversations: {conversations_meeting_threshold} meet min threshold "
                    f"({min_conversation_messages} msgs), {conversations_filtered_out} filtered out"
                )
                logger.info(f"Messages to store: {stored_count}")

                self.message_count = stored_count
                self.conversation_count = len(
                    [c for c in self.db_session.query(Conversation).all()]
                )

        except Exception as e:
            logger.error(f"Error parsing PST: {e}")
            raise

        # Log sample dates for debugging date range issues
        if hasattr(self, '_date_samples') and self._date_samples:
            samples_breakdown = []
            for date_tuple in self._date_samples:
                if isinstance(date_tuple, tuple):
                    date, in_range = date_tuple
                    date_str = str(date.date()) if hasattr(date, 'date') else str(date)
                    status = "✓IN" if in_range else "✗OUT"
                    samples_breakdown.append(f"{date_str}({status})")
                else:
                    # Fallback for old format
                    date_str = str(date_tuple.date()) if hasattr(date_tuple, 'date') else str(date_tuple)
                    samples_breakdown.append(date_str)
            logger.info(f"Sample email dates (20 total): {', '.join(samples_breakdown)}")

        logger.info(
            f"Parse complete: {self.message_count} messages, "
            f"{self.conversation_count} conversations, {self.error_count} errors, "
            f"{self.filtered_count} filtered (spurious)"
        )

        return self.message_count, self.conversation_count, self.error_count


    def _process_folder(self, folder, conversations: dict, date_start, date_end, max_messages=None, counter_dict=None, depth=0):
        """Recursively process folder and subfolders"""
        if depth > 20:  # Prevent infinite recursion
            return

        # Initialize counter dict if needed (to track messages across recursion)
        if counter_dict is None:
            counter_dict = {"count": 0}

        try:
            # Process messages in this folder
            for msg_idx in range(folder.number_of_sub_messages):
                # Check if we've hit the max_messages limit
                if max_messages and counter_dict["count"] >= max_messages:
                    logger.info(f"Reached max_messages limit: {max_messages}")
                    return

                try:
                    message = folder.get_sub_message(msg_idx)
                    msg_data = self._extract_message(message)

                    if msg_data and self._is_in_date_range(msg_data["delivery_date"], date_start, date_end):
                        topic = msg_data["conversation_topic"]
                        if topic not in conversations:
                            conversations[topic] = []
                        conversations[topic].append(msg_data)
                        counter_dict["count"] += 1

                except Exception as e:
                    logger.warning(f"Error extracting message: {e}")
                    self.error_count += 1

            # Recurse into subfolders
            for subfolder_idx in range(folder.number_of_sub_folders):
                # Check limit before recursing
                if max_messages and counter_dict["count"] >= max_messages:
                    logger.info(f"Reached max_messages limit: {max_messages}")
                    return

                try:
                    subfolder = folder.get_sub_folder(subfolder_idx)
                    self._process_folder(subfolder, conversations, date_start, date_end, max_messages, counter_dict, depth + 1)
                except Exception as e:
                    logger.warning(f"Error accessing subfolder: {e}")

        except Exception as e:
            logger.warning(f"Error processing folder: {e}")

    def _to_str(self, value) -> str:
        """Convert bytes or any value to string"""
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode('utf-8', errors='ignore')
        return str(value)

    def _extract_message(self, message) -> Optional[dict]:
        """Extract relevant fields from a pypff message object"""
        try:
            # Basic fields - pypff API (handle both bytes and strings)
            subject = self._to_str(message.subject if hasattr(message, 'subject') else "")

            # Sender extraction - try multiple approaches
            sender_email = ""
            sender_name = self._to_str(message.sender_name if hasattr(message, 'sender_name') else "")

            # Approach 1: Direct sender_email_address
            if hasattr(message, 'sender_email_address') and message.sender_email_address:
                sender_email = self._to_str(message.sender_email_address)

            # Approach 2: If empty or X.500 format, try transport headers
            if not sender_email or sender_email.startswith('/') or '@' not in sender_email:
                # Try to get from transport headers (contains From: header)
                if hasattr(message, 'transport_headers') and message.transport_headers:
                    headers = self._to_str(message.transport_headers)
                    # Parse From: header
                    import re
                    from_match = re.search(r'From:.*?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', headers, re.IGNORECASE)
                    if from_match:
                        sender_email = from_match.group(1)

            # Approach 3: Try sender entry id properties (some PST files use this)
            if not sender_email or '@' not in sender_email:
                # Try PR_SENDER_SMTP_ADDRESS if available
                if hasattr(message, 'sender_smtp_address') and message.sender_smtp_address:
                    smtp_addr = self._to_str(message.sender_smtp_address)
                    if '@' in smtp_addr:
                        sender_email = smtp_addr

            # Approach 4: Extract from X.500 DN if that's all we have
            if sender_email and sender_email.startswith('/') and '@' not in sender_email:
                # X.500 format like /O=ORG/OU=EXCHANGE/CN=RECIPIENTS/CN=username
                # Try to extract the CN value as a username
                import re
                cn_match = re.search(r'/CN=([^/]+)$', sender_email, re.IGNORECASE)
                if cn_match:
                    # Use CN as a pseudo-identifier (not a real email but better than empty)
                    extracted_cn = cn_match.group(1).lower()
                    # Don't overwrite - keep as identifier with marker
                    sender_email = f"{extracted_cn}@x500.local"

            # Log diagnostic for first few messages
            if not hasattr(self, '_sender_debug_count'):
                self._sender_debug_count = 0
            if self._sender_debug_count < 5:
                raw_sender = self._to_str(message.sender_email_address if hasattr(message, 'sender_email_address') else "NONE")
                logger.info(f"Sender debug: raw='{raw_sender[:50]}', extracted='{sender_email}', name='{sender_name}'")
                self._sender_debug_count += 1

            # Recipients (comma-separated)
            recipients = []
            if hasattr(message, 'recipients'):
                try:
                    for recipient in message.recipients:
                        if hasattr(recipient, 'email_address'):
                            recipients.append(self._to_str(recipient.email_address))
                        else:
                            recipients.append(self._to_str(recipient))
                except:
                    pass
            recipients_str = ",".join(recipients)

            # CC (comma-separated)
            cc = []
            if hasattr(message, 'cc_recipients'):
                try:
                    for cc_recipient in message.cc_recipients:
                        if hasattr(cc_recipient, 'email_address'):
                            cc.append(self._to_str(cc_recipient.email_address))
                        else:
                            cc.append(self._to_str(cc_recipient))
                except:
                    pass
            cc_str = ",".join(cc)

            # Delivery date
            delivery_date = message.client_submit_time if hasattr(message, 'client_submit_time') else None

            # Body (pypff has plain_text_body and html_body)
            body = ""
            if hasattr(message, 'plain_text_body'):
                body = self._to_str(message.plain_text_body or "")
            if not body and hasattr(message, 'html_body'):
                body = self._to_str(message.html_body or "")
            body_snippet = (body[:500] if body else "").replace("\n", " ")

            # Message class
            message_class = self._to_str(message.message_class if hasattr(message, 'message_class') else "IPM.Note")

            # Attachments
            has_ics = False
            attachment_count = 0
            if hasattr(message, 'attachments'):
                try:
                    attachment_count = len(message.attachments) if message.attachments else 0
                    if message.attachments:
                        for att in message.attachments:
                            filename = att.filename if hasattr(att, 'filename') else ""
                            if filename and filename.lower().endswith('.ics'):
                                has_ics = True
                except:
                    pass

            # Conversation topic (use subject or sender as grouping key)
            conversation_topic = subject if subject else f"Conversation with {sender_name or sender_email}"
            if not conversation_topic:
                conversation_topic = f"Message from {self._to_str(sender_name or sender_email)}"
            conversation_topic = self._to_str(conversation_topic)

            # Generate unique message ID (include body to distinguish similar emails)
            msg_id = self._generate_msg_id(sender_email, subject, delivery_date, body)

            return {
                "msg_id": msg_id,
                "conversation_topic": conversation_topic,
                "subject": subject,
                "sender_email": sender_email,
                "sender_name": sender_name,
                "recipients": recipients_str,
                "cc": cc_str,
                "delivery_date": delivery_date,
                "message_class": message_class,
                "body_snippet": body_snippet,
                "body_full": body,
                "has_ics_attachment": has_ics,
                "attachment_count": attachment_count,
            }

        except Exception as e:
            logger.warning(f"Error extracting message fields: {e}")
            return None

    def _generate_msg_id(self, sender: str, subject: str, date, body: str = "") -> str:
        """Generate unique message ID

        Uses sender, subject, date, and body to create a more unique identifier.
        This helps detect true duplicates vs. similar emails.
        """
        # Ensure all values are strings before encoding
        sender_str = self._to_str(sender)
        subject_str = self._to_str(subject)
        date_str = self._to_str(date)
        body_str = self._to_str(body)

        # Include first 500 chars of body to distinguish similar emails
        # Full body would be too much, 500 chars is a good balance
        body_prefix = body_str[:500] if body_str else ""

        combined = f"{sender_str}:{subject_str}:{date_str}:{body_prefix}".encode('utf-8', errors='ignore')
        return hashlib.sha256(combined).hexdigest()[:32]

    def _is_in_date_range(self, delivery_date, date_start, date_end) -> bool:
        """Check if delivery date is within range"""
        if not delivery_date:
            return False
        try:
            in_range = date_start <= delivery_date <= date_end
            # Track date distribution for debugging (both accepted and rejected)
            if not hasattr(self, '_date_samples'):
                self._date_samples = []
            if len(self._date_samples) < 20:  # Keep first 20 samples from all dates
                self._date_samples.append((delivery_date, in_range))
            return in_range
        except Exception as e:
            logger.warning(f"Date comparison error: {e}, delivery_date={delivery_date}, start={date_start}, end={date_end}")
            return False

    def _check_relevance(self, msg_data: dict) -> tuple:
        """Check if message is work-relevant using LLM classification

        Returns:
            Tuple of (relevance_score: float, is_spurious: bool)
        """
        import json

        # Check if filtering is enabled
        parsing_config = self.config.get("parsing", {})
        if not parsing_config.get("enable_relevance_filter", False):
            return 0.5, False  # Filtering disabled, assume relevant

        # Need Ollama and PromptManager
        if not self.ollama_client or not self.prompt_manager:
            logger.warning("Relevance filtering enabled but Ollama/PromptManager not available")
            return 0.5, False  # Fail-safe: assume relevant

        try:
            # Get filter prompt
            prompt_id = parsing_config.get("filter_prompt", "task_filter_relevance_v1")
            prompt = self.prompt_manager.get_prompt(prompt_id)

            if not prompt:
                logger.warning(f"Relevance filter prompt not found: {prompt_id}")
                return 0.5, False

            # Prepare message data for prompt
            prompt_data = {
                "subject": msg_data.get("subject", ""),
                "sender_email": msg_data.get("sender_email", ""),
                "sender_name": msg_data.get("sender_name", ""),
                "recipients": msg_data.get("recipients", ""),
                "delivery_date": str(msg_data.get("delivery_date", "")),
                "body_snippet": msg_data.get("body_snippet", "")[:500]
            }

            # Fill prompt template
            filled_prompt = prompt.substitute_variables(prompt_data)

            # Call LLM
            response = self.ollama_client.generate(filled_prompt)

            # Handle empty response
            if not response or not response.strip():
                logger.warning(f"Relevance filter: LLM returned empty response for '{msg_data.get('subject', 'No subject')[:40]}'")
                return 0.5, False  # Fail-safe: assume work-relevant

            # Parse JSON response
            try:
                result = json.loads(response)
            except json.JSONDecodeError as parse_error:
                # Log the actual response for debugging
                response_snippet = response[:200] if len(response) > 200 else response
                logger.warning(f"Relevance filter JSON parse error: {parse_error}. Response: '{response_snippet}'")
                return 0.5, False  # Fail-safe

            classification = result.get("classification", "SPURIOUS")
            confidence = float(result.get("confidence", 0.5))

            # Get threshold from config
            threshold = parsing_config.get("relevance_threshold", 0.80)

            # Determine if spurious
            is_spurious = (classification == "SPURIOUS" and confidence >= threshold)

            if is_spurious:
                logger.info(f"Filtered spurious email: {msg_data.get('subject', 'No subject')[:50]} (confidence={confidence:.2f})")

            return confidence, is_spurious

        except Exception as e:
            logger.warning(f"Relevance filter error: {e}")
            return 0.5, False  # Fail-safe: assume relevant on error

    def _store_conversation(self, topic: str, messages: list) -> int:
        """Store conversation and messages to database"""
        try:
            # Create conversation record
            conversation_id = hashlib.md5(topic.encode()).hexdigest()[:16]

            # Check if already exists
            existing = self.db_session.query(Conversation).filter_by(
                conversation_id=conversation_id
            ).first()

            if existing:
                return 0  # Already stored

            conv = Conversation(
                conversation_id=conversation_id,
                conversation_topic=topic,
                message_count=len(messages),
                date_range_start=min([m["delivery_date"] for m in messages if m["delivery_date"]]),
                date_range_end=max([m["delivery_date"] for m in messages if m["delivery_date"]])
            )
            self.db_session.add(conv)
            self.db_session.flush()

            # Add messages
            stored = 0
            duplicates = 0
            for idx, msg_data in enumerate(messages):
                try:
                    # Check if message already exists (duplicate detection)
                    existing_msg = self.db_session.query(Message).filter_by(
                        msg_id=msg_data["msg_id"]
                    ).first()

                    if existing_msg:
                        logger.info(f"Skipping duplicate message: {msg_data['subject'][:50]}")
                        duplicates += 1
                        continue

                    # Check relevance if filtering enabled
                    relevance_score, is_spurious = self._check_relevance(msg_data)

                    # Set enrichment status based on filter result
                    enrichment_status = "filtered" if is_spurious else "pending"

                    msg = Message(
                        msg_id=msg_data["msg_id"],
                        conversation_id=conv.id,
                        subject=msg_data["subject"],
                        sender_email=msg_data["sender_email"],
                        sender_name=msg_data["sender_name"],
                        recipients=msg_data["recipients"],
                        cc=msg_data["cc"],
                        delivery_date=msg_data["delivery_date"],
                        message_class=msg_data["message_class"],
                        body_snippet=msg_data["body_snippet"],
                        body_full=msg_data["body_full"],
                        has_ics_attachment=msg_data["has_ics_attachment"],
                        attachment_count=msg_data["attachment_count"],
                        message_index=idx,
                        relevance_score=relevance_score,
                        is_spurious=is_spurious,
                        enrichment_status=enrichment_status
                    )
                    self.db_session.add(msg)

                    # Track filtered count
                    if is_spurious:
                        self.filtered_count += 1

                    stored += 1
                except Exception as e:
                    logger.warning(f"Error storing message: {e}")
                    self.error_count += 1

            self.db_session.commit()

            if duplicates > 0:
                logger.info(f"  Skipped {duplicates} duplicate messages in conversation")
            logger.info(f"Stored conversation: {topic[:60]} ({len(messages)} messages)")
            return stored

        except Exception as e:
            logger.error(f"Error storing conversation: {e}")
            self.db_session.rollback()
            return 0
