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

    def __init__(self, db_session: Session):
        self.db_session = db_session
        self.message_count = 0
        self.conversation_count = 0
        self.error_count = 0

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

        logger.info(
            f"Parse complete: {self.message_count} messages, "
            f"{self.conversation_count} conversations, {self.error_count} errors"
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
            sender_email = self._to_str(message.sender_email_address if hasattr(message, 'sender_email_address') else "")
            sender_name = self._to_str(message.sender_name if hasattr(message, 'sender_name') else "")

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
            return date_start <= delivery_date <= date_end
        except:
            return False

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
                        enrichment_status="pending"
                    )
                    self.db_session.add(msg)
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
