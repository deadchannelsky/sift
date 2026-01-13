"""
PST file parser - Extract messages and store to SQLite
"""
import pypff
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
        min_conversation_messages: int = 3
    ) -> Tuple[int, int, int]:
        """
        Parse PST file and store to database

        Args:
            pst_path: Path to .pst file
            date_start: Start date (YYYY-MM-DD)
            date_end: End date (YYYY-MM-DD)
            min_conversation_messages: Minimum messages in thread to include

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
                pst_file = pypff.file()
                pst_file.open(pst_path)

                # Get root folder
                root_folder = pst_file.root_folder

                # Dictionary to track conversations: topic -> list of messages
                conversations = {}

                # Walk folder tree and extract messages
                self._walk_folder(
                    root_folder,
                    conversations,
                    date_start_dt,
                    date_end_dt
                )

                logger.info(f"Found {len(conversations)} conversations")

                # Filter by minimum message count and store to DB
                stored_count = 0
                for topic, messages in conversations.items():
                    if len(messages) >= min_conversation_messages:
                        stored_count += self._store_conversation(topic, messages)

                self.message_count = stored_count
                self.conversation_count = len(
                    [c for c in self.db_session.query(Conversation).all()]
                )

                pst_file.close()

        except Exception as e:
            logger.error(f"Error parsing PST: {e}")
            raise

        logger.info(
            f"Parse complete: {self.message_count} messages, "
            f"{self.conversation_count} conversations, {self.error_count} errors"
        )

        return self.message_count, self.conversation_count, self.error_count

    def _walk_folder(self, folder, conversations, date_start, date_end, depth=0):
        """Recursively walk PST folder tree"""
        if depth > 20:  # Prevent infinite recursion
            return

        try:
            # Process messages in this folder
            for message_idx in range(folder.number_of_sub_messages):
                try:
                    message = folder.get_sub_message(message_idx)
                    msg_data = self._extract_message(message)

                    if msg_data and self._is_in_date_range(msg_data["delivery_date"], date_start, date_end):
                        # Group by conversation topic
                        topic = msg_data["conversation_topic"]
                        if topic not in conversations:
                            conversations[topic] = []
                        conversations[topic].append(msg_data)

                except Exception as e:
                    logger.warning(f"Error extracting message: {e}")
                    self.error_count += 1

            # Recurse into subfolders
            for subfolder_idx in range(folder.number_of_sub_folders):
                try:
                    subfolder = folder.get_sub_folder(subfolder_idx)
                    self._walk_folder(subfolder, conversations, date_start, date_end, depth + 1)
                except Exception as e:
                    logger.warning(f"Error accessing subfolder: {e}")

        except Exception as e:
            logger.warning(f"Error walking folder: {e}")

    def _extract_message(self, message) -> Optional[dict]:
        """Extract relevant fields from a pypff message"""
        try:
            # Basic fields
            subject = message.subject if hasattr(message, 'subject') else ""
            sender_email = message.sender_email_address if hasattr(message, 'sender_email_address') else ""
            sender_name = message.sender_name if hasattr(message, 'sender_name') else ""

            # Recipients (comma-separated)
            recipients = []
            if hasattr(message, 'recipients'):
                try:
                    for recipient in message.recipients:
                        recipients.append(recipient.email_address)
                except:
                    pass
            recipients_str = ",".join(recipients)

            # CC (comma-separated)
            cc = []
            if hasattr(message, 'cc_recipients'):
                try:
                    for cc_recipient in message.cc_recipients:
                        cc.append(cc_recipient.email_address)
                except:
                    pass
            cc_str = ",".join(cc)

            # Delivery date
            delivery_date = message.client_submit_time if hasattr(message, 'client_submit_time') else None

            # Body
            body = message.plain_text_body if hasattr(message, 'plain_text_body') else ""
            body_snippet = (body[:500] if body else "").replace("\n", " ")

            # Message class
            message_class = message.message_class if hasattr(message, 'message_class') else "IPM.Note"

            # Attachments
            has_ics = False
            attachment_count = 0
            if hasattr(message, 'attachments'):
                try:
                    attachment_count = message.number_of_attachments if hasattr(message, 'number_of_attachments') else 0
                    for att in message.attachments:
                        filename = att.filename if hasattr(att, 'filename') else ""
                        if filename.lower().endswith('.ics'):
                            has_ics = True
                except:
                    pass

            # Conversation topic (use subject or sender as grouping key)
            conversation_topic = subject if subject else f"Conversation with {sender_name or sender_email}"
            if not conversation_topic:
                conversation_topic = f"Message from {sender_name or sender_email}"

            # Generate unique message ID
            msg_id = self._generate_msg_id(sender_email, subject, delivery_date)

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

    def _generate_msg_id(self, sender: str, subject: str, date) -> str:
        """Generate unique message ID"""
        combined = f"{sender}:{subject}:{date}".encode()
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
            for idx, msg_data in enumerate(messages):
                try:
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
            logger.info(f"Stored conversation: {topic[:60]} ({len(messages)} messages)")
            return stored

        except Exception as e:
            logger.error(f"Error storing conversation: {e}")
            self.db_session.rollback()
            return 0
