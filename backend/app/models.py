"""
SQLAlchemy ORM models for Sift database schema
"""
from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean, Float, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

Base = declarative_base()


# ============================================================================
# PYDANTIC MODELS (for request validation)
# ============================================================================

class AggregationSettings(BaseModel):
    """Settings for aggregation pipeline - overrides config.json defaults"""
    min_role_confidence: float = Field(
        default=0.65,
        ge=0.5,
        le=0.95,
        description="Minimum confidence score for stakeholder role inference (0.5-0.95)"
    )
    min_mention_count: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Minimum number of mentions to include stakeholder (1-10)"
    )
    exclude_generic_names: bool = Field(
        default=True,
        description="Filter out generic names like 'User', 'Admin', 'Team'"
    )
    enable_name_deduplication: bool = Field(
        default=True,
        description="Merge similar stakeholder names across conversations"
    )
    name_similarity_threshold: float = Field(
        default=0.80,
        ge=0.5,
        le=1.0,
        description="Similarity threshold for name deduplication (0.5-1.0, higher = more strict)"
    )
    validate_email_domains: bool = Field(
        default=True,
        description="Filter out emails with invalid domains"
    )
    enable_diagnostics: bool = Field(
        default=True,
        description="Generate detailed diagnostic reports"
    )


# ============================================================================
# SQLALCHEMY MODELS
# ============================================================================


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


class ProjectClusterMetadata(Base):
    """Stores post-aggregation filter results for project clusters"""
    __tablename__ = "project_cluster_metadata"

    id = Column(Integer, primary_key=True)
    cluster_canonical_name = Column(String(512), nullable=False, index=True, unique=True)

    # Post-aggregation filter results
    post_agg_filter_enabled = Column(Boolean, default=False)
    post_agg_user_role = Column(String(100), nullable=True)  # Role used for filtering (IT Solution Architect, PM, etc.)
    post_agg_confidence = Column(Float, nullable=True)  # 0.0-1.0 relevance score from LLM
    post_agg_reasoning = Column(Text, nullable=True)  # Full reasoning chain from LLM
    post_agg_is_relevant = Column(Boolean, nullable=True)  # True if LLM considers it relevant
    post_agg_user_threshold = Column(Float, default=0.75)  # User-set confidence threshold for filtering
    post_agg_filtered = Column(Boolean, default=False)  # True if hidden from reports due to low confidence
    post_agg_filtered_at = Column(DateTime, nullable=True)
    post_agg_filter_version = Column(String(50), nullable=True)  # Prompt version used (e.g., "task_post_aggregation_filter_v1")

    # Tracking
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ProjectClusterMetadata(name={self.cluster_canonical_name}, confidence={self.post_agg_confidence})>"


# ============================================================================
# RAG (RETRIEVAL-AUGMENTED GENERATION) MODELS
# ============================================================================

class RAGSession(Base):
    """RAG chat session metadata"""
    __tablename__ = "rag_sessions"

    id = Column(String(100), primary_key=True)  # UUID
    created_at = Column(DateTime, default=datetime.utcnow)
    last_query_at = Column(DateTime, nullable=True)
    query_count = Column(Integer, default=0)

    def __repr__(self):
        return f"<RAGSession(id={self.id}, queries={self.query_count})>"


class RAGQueryHistory(Base):
    """Query/response history for each RAG session"""
    __tablename__ = "rag_query_history"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(100), ForeignKey("rag_sessions.id"), index=True)
    query = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    citations_json = Column(Text, nullable=True)  # JSON array: [{"message_id": N, "subject": "...", "date": "...", "sender": "...", "snippet": "..."}]
    retrieved_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("RAGSession", backref="queries")

    def __repr__(self):
        return f"<RAGQueryHistory(session={self.session_id}, retrieved={self.retrieved_count})>"


class MessageEmbedding(Base):
    """Track which messages are embedded in ChromaDB for RAG"""
    __tablename__ = "message_embeddings"

    message_id = Column(Integer, ForeignKey("messages.id"), primary_key=True, index=True)
    embedding_generated_at = Column(DateTime, default=datetime.utcnow)
    embedding_model = Column(String(100), default="nomic-embed-text")
    indexed_in_chroma = Column(Boolean, default=True)

    message = relationship("Message", backref="embedding_metadata")

    def __repr__(self):
        return f"<MessageEmbedding(msg={self.message_id}, model={self.embedding_model})>"


# ============================================================================
# REPL (CODE-BASED EXPLORATION) MODELS
# ============================================================================

class REPLSession(Base):
    """REPL exploration session metadata"""
    __tablename__ = "repl_sessions"

    id = Column(String(100), primary_key=True)  # UUID
    model_used = Column(String(100), nullable=True)  # Model used for code generation
    corpus_message_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_query_at = Column(DateTime, nullable=True)
    query_count = Column(Integer, default=0)

    def __repr__(self):
        return f"<REPLSession(id={self.id}, queries={self.query_count}, model={self.model_used})>"


class REPLQueryHistory(Base):
    """Query/trace history for each REPL session"""
    __tablename__ = "repl_query_history"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(100), ForeignKey("repl_sessions.id"), index=True)
    query = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    trace_json = Column(Text, nullable=True)  # JSON array of exploration steps
    model_used = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("REPLSession", backref="queries")

    def __repr__(self):
        return f"<REPLQueryHistory(session={self.session_id}, query={self.query[:50]}...)>"


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
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))

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
