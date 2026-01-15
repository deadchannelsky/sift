"""
SQLAlchemy ORM models for Sift database schema
"""
from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean, Float, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import datetime

Base = declarative_base()


class Conversation(Base):
    """Represents an email thread/conversation"""
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    conversation_id = Column(String(255), unique=True, nullable=False, index=True)
    conversation_topic = Column(String(512), nullable=False)
    message_count = Column(Integer, default=0)
    date_range_start = Column(DateTime, nullable=True)
    date_range_end = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Conversation(id={self.id}, topic='{self.conversation_topic}', messages={self.message_count})>"


class Message(Base):
    """Represents a single email message"""
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    msg_id = Column(String(255), unique=True, nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)

    # Email metadata
    subject = Column(String(512), nullable=True)
    sender_email = Column(String(255), nullable=True, index=True)
    sender_name = Column(String(255), nullable=True)
    recipients = Column(Text, nullable=True)  # CSV list
    cc = Column(Text, nullable=True)  # CSV list
    delivery_date = Column(DateTime, nullable=True, index=True)
    message_class = Column(String(100), nullable=True)  # e.g., "IPM.Note", "IPM.Schedule.Meeting.Request"

    # Content
    body_snippet = Column(Text, nullable=True)  # First 500 chars
    body_full = Column(Text, nullable=True)  # Full body (optional, for detailed analysis)

    # Attachments
    has_ics_attachment = Column(Boolean, default=False)
    attachment_count = Column(Integer, default=0)

    # Processing metadata
    message_index = Column(Integer, nullable=True)  # Position in conversation
    enrichment_status = Column(String(50), default="pending")  # pending, processing, completed, failed, filtered
    enrichment_error = Column(Text, nullable=True)  # Error message if processing failed
    relevance_score = Column(Float, nullable=True)  # 0.0-1.0 LLM confidence from relevance filter
    is_spurious = Column(Boolean, default=False, index=True)  # True if marked as non-work-relevant
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship
    conversation = relationship("Conversation", back_populates="messages")
    attachments = relationship("Attachment", back_populates="message", cascade="all, delete-orphan")
    extractions = relationship("Extraction", back_populates="message", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Message(msg_id={self.msg_id}, subject='{self.subject}')>"


class Attachment(Base):
    """Represents an email attachment"""
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    filename = Column(String(512), nullable=True)
    file_size = Column(Integer, nullable=True)
    is_ics = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship
    message = relationship("Message", back_populates="attachments")

    def __repr__(self):
        return f"<Attachment(msg_id={self.message_id}, filename='{self.filename}')>"


class Extraction(Base):
    """Stores LLM extraction results (projects, stakeholders, importance, meetings)"""
    __tablename__ = "extractions"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False, index=True)
    task_name = Column(String(50), nullable=False)  # task_a, task_b, task_c, task_d
    prompt_version = Column(String(50), nullable=False)  # e.g., "task_a_projects_v1"

    # Raw extraction result (JSON string)
    extraction_json = Column(Text, nullable=False)

    # Metadata
    confidence = Column(String(50), nullable=True)  # high, medium, low (aggregated)
    processing_time_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship
    message = relationship("Message", back_populates="extractions")

    def __repr__(self):
        return f"<Extraction(msg_id={self.message_id}, task={self.task_name})>"


class ProcessingJob(Base):
    """Tracks PST parsing and enrichment jobs"""
    __tablename__ = "processing_jobs"

    id = Column(Integer, primary_key=True)
    job_id = Column(String(100), unique=True, nullable=False, index=True)
    status = Column(String(50), default="pending")  # pending, parsing, enriching, completed, failed

    # File info
    pst_filename = Column(String(512), nullable=True)
    date_range_start = Column(String(10), nullable=True)  # YYYY-MM-DD
    date_range_end = Column(String(10), nullable=True)

    # Progress
    total_messages = Column(Integer, default=0)
    processed_messages = Column(Integer, default=0)
    current_task = Column(String(100), nullable=True)  # task_a, task_b, etc.
    cancelled = Column(Boolean, default=False)  # Flag to signal cancellation from UI

    # Results
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<ProcessingJob(job_id={self.job_id}, status={self.status})>"


def init_db(db_path: str = "data/messages.db"):
    """Initialize database and create all tables

    Note: check_same_thread=False allows FastAPI background tasks (which run in threads)
    to safely access the database. SQLite handles thread safety internally.

    Timeout=15 gives background tasks time to complete writes before reads timeout.
    WAL mode enables concurrent readers while writes are in progress.
    """
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={
            "check_same_thread": False,
            "timeout": 15  # 15 second timeout for lock waits (default is 5)
        },
        pool_pre_ping=True  # Verify connections before using
    )

    # Enable Write-Ahead Logging for better concurrency
    # This allows readers to access the database while writes are in progress
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.commit()

    Base.metadata.create_all(engine)
    return engine


def get_session(engine):
    """Get a database session"""
    Session = sessionmaker(bind=engine)
    return Session()


def clear_all_tables(db_session):
    """
    Clear all database tables (destructive operation)

    Deletes all records from ProcessingJob, Extraction, Attachment,
    Message, and Conversation tables. Use with caution.

    Clears in reverse dependency order to respect foreign key constraints:
    1. ProcessingJob (independent)
    2. Extraction (references Message)
    3. Attachment (references Message)
    4. Message (references Conversation)
    5. Conversation (root table)

    Args:
        db_session: SQLAlchemy session
    """
    from sqlalchemy import text
    from .utils import logger

    logger.info("Clearing all database tables...")

    try:
        # Disable foreign key constraints temporarily (SQLite specific)
        db_session.execute(text("PRAGMA foreign_keys = OFF"))

        # Delete in order (respecting dependencies)
        db_session.query(ProcessingJob).delete()
        db_session.query(Extraction).delete()
        db_session.query(Attachment).delete()
        db_session.query(Message).delete()
        db_session.query(Conversation).delete()

        db_session.commit()

        # Re-enable foreign key constraints
        db_session.execute(text("PRAGMA foreign_keys = ON"))

        logger.info("✅ All database tables cleared successfully")

    except Exception as e:
        db_session.rollback()
        logger.error(f"Error clearing database tables: {e}")
        raise
