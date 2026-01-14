"""
Sift Backend - FastAPI application
Entry point for PST parsing and enrichment pipeline
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uuid
import os
from pathlib import Path

from app.models import init_db, get_session, ProcessingJob, Message, Conversation
from app.pst_parser import PSTParser
from app.ollama_client import OllamaClient
from app.utils import logger, get_db_path, ensure_data_dir, BACKEND_DIR
import json as json_lib

# Initialize FastAPI app
app = FastAPI(
    title="Sift Backend",
    description="Email Intelligence Extraction - PST Parser & Enrichment",
    version="0.1.0"
)

# Add CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Ollama client (initialized on startup)
ollama_client = None

# Initialize database and Ollama on startup
@app.on_event("startup")
async def startup_event():
    global ollama_client
    logger.info("=== Sift Backend Starting ===")
    ensure_data_dir()

    # Initialize database
    db_path = get_db_path()
    init_db(db_path)
    logger.info(f"Database initialized: {db_path}")

    # Initialize Ollama client
    try:
        config_path = BACKEND_DIR / "config.json"
        with open(config_path) as f:
            config = json_lib.load(f)

        ollama_config = config.get("ollama", {})
        url = ollama_config.get("url", "http://localhost:11434")
        model = ollama_config.get("model")
        timeout = ollama_config.get("timeout_seconds", 30)
        max_retries = ollama_config.get("max_retries", 3)
        retry_backoff = ollama_config.get("retry_backoff_ms", 500)

        ollama_client = OllamaClient(
            url=url,
            model=model,
            timeout_seconds=timeout,
            max_retries=max_retries,
            retry_backoff_ms=retry_backoff
        )

        # Test connection
        if ollama_client.test_connection():
            # List available models
            ollama_client.list_models()
            if model:
                ollama_client.set_model(model)
        else:
            logger.warning("Ollama not available - enrichment will not work")

    except Exception as e:
        logger.error(f"Error initializing Ollama client: {e}")
        ollama_client = None


# Request/Response models
class ParseRequest(BaseModel):
    pst_filename: str  # Filename in data/ folder (e.g., "sample.pst")
    date_start: str = "2025-10-01"
    date_end: str = "2025-12-31"
    min_conversation_messages: int = 3
    max_messages: Optional[int] = None  # Limit total messages parsed (for testing)


class StatusResponse(BaseModel):
    job_id: str
    status: str
    total_messages: int
    processed_messages: int
    current_task: Optional[str]
    progress_percent: float
    error: Optional[str] = None


class ResultsResponse(BaseModel):
    job_id: str
    status: str
    message_count: int
    conversation_count: int
    errors: int


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "name": "Sift Backend",
        "version": "0.1.0",
        "status": "running"
    }


@app.post("/parse")
async def parse_pst(
    request: ParseRequest,
    background_tasks: BackgroundTasks = None
):
    """
    Start parsing a PST file already on the server

    Args:
        request: JSON body with:
            - pst_filename: Filename in data/ folder (e.g., "sample.pst")
            - date_start: Start date (YYYY-MM-DD)
            - date_end: End date (YYYY-MM-DD)
            - min_conversation_messages: Minimum messages in thread

    Returns:
        {
            "job_id": "abc123",
            "status": "queued",
            "message": "Parsing started"
        }
    """
    try:
        # Validate PST file exists (in backend/data/ folder)
        data_dir = BACKEND_DIR / "data"
        pst_path = data_dir / request.pst_filename

        if not pst_path.exists():
            raise HTTPException(status_code=404, detail=f"PST file not found: {pst_path}")

        if not str(pst_path).endswith(".pst"):
            raise HTTPException(status_code=400, detail="File must be .pst format")

        logger.info(f"Starting parse job for: {pst_path}")

        # Create job record
        job_id = str(uuid.uuid4())[:8]
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        job = ProcessingJob(
            job_id=job_id,
            pst_filename=str(pst_path),
            date_range_start=request.date_start,
            date_range_end=request.date_end,
            status="queued"
        )
        session.add(job)
        session.commit()

        # Queue background task
        if background_tasks:
            background_tasks.add_task(
                _parse_pst_task,
                job_id,
                str(pst_path),
                request.date_start,
                request.date_end,
                request.min_conversation_messages,
                request.max_messages
            )

        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"Parsing started for: {request.pst_filename}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting parse job: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/status/{job_id}")
async def get_status(job_id: str) -> StatusResponse:
    """
    Check status of parsing/enrichment job

    Args:
        job_id: Job ID returned from /parse endpoint

    Returns:
        {
            "job_id": "abc123",
            "status": "parsing",
            "total_messages": 350,
            "processed_messages": 150,
            "current_task": "task_a_projects",
            "progress_percent": 42.9,
            "error": null
        }
    """
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()

        if not job:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

        progress_percent = (
            (job.processed_messages / job.total_messages * 100)
            if job.total_messages > 0
            else 0
        )

        return StatusResponse(
            job_id=job.job_id,
            status=job.status,
            total_messages=job.total_messages,
            processed_messages=job.processed_messages,
            current_task=job.current_task,
            progress_percent=progress_percent,
            error=job.error_message
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/results/{job_id}")
async def get_results(job_id: str) -> ResultsResponse:
    """
    Get parsed messages from completed job

    Args:
        job_id: Job ID returned from /parse endpoint

    Returns:
        {
            "job_id": "abc123",
            "status": "completed",
            "message_count": 287,
            "conversation_count": 45,
            "errors": 2
        }
    """
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()

        if not job:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

        if job.status != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"Job not completed. Status: {job.status}"
            )

        return ResultsResponse(
            job_id=job.job_id,
            status=job.status,
            message_count=job.processed_messages,
            conversation_count=0,  # TODO: count actual conversations
            errors=0  # TODO: count actual errors
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting results: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/models")
async def list_models():
    """List all available Ollama models"""
    if not ollama_client:
        raise HTTPException(status_code=503, detail="Ollama not initialized")

    try:
        models = ollama_client.list_models()
        return {
            "available_models": [
                {
                    "name": m.name,
                    "size_gb": round(m.size_gb, 2),
                    "quantization": m.quantization
                }
                for m in models
            ],
            "current_model": ollama_client.model
        }
    except Exception as e:
        logger.error(f"Error listing models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/models/{model_name}")
async def set_model(model_name: str):
    """Switch to a different model

    Args:
        model_name: Name of the model to use (e.g., "granite-4.0-h-tiny")

    Returns:
        Success status and model info
    """
    if not ollama_client:
        raise HTTPException(status_code=503, detail="Ollama not initialized")

    try:
        if ollama_client.set_model(model_name):
            return {
                "status": "success",
                "current_model": ollama_client.model,
                "message": f"Model set to: {model_name}"
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{model_name}' not found on server"
            )
    except Exception as e:
        logger.error(f"Error setting model: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def get_stats():
    """Get database statistics"""
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        # Query statistics
        message_count = session.query(Message).count()
        conversation_count = session.query(Conversation).count()
        pending_enrichment = session.query(Message).filter_by(enrichment_status="pending").count()
        job_count = session.query(ProcessingJob).count()

        # Sample recent messages
        recent_messages = session.query(Message).order_by(Message.created_at.desc()).limit(5).all()

        # Sample conversations
        top_conversations = session.query(Conversation).order_by(Conversation.message_count.desc()).limit(5).all()

        return {
            "database": {
                "messages": message_count,
                "conversations": conversation_count,
                "pending_enrichment": pending_enrichment,
                "jobs": job_count
            },
            "recent_messages": [
                {
                    "msg_id": m.msg_id[:8] + "...",
                    "subject": m.subject[:50] if m.subject else "(no subject)",
                    "sender": m.sender_email,
                    "date": str(m.delivery_date) if m.delivery_date else None,
                    "status": m.enrichment_status
                }
                for m in recent_messages
            ],
            "top_conversations": [
                {
                    "topic": c.conversation_topic[:60],
                    "messages": c.message_count,
                    "start": str(c.date_range_start) if c.date_range_start else None,
                    "end": str(c.date_range_end) if c.date_range_end else None
                }
                for c in top_conversations
            ]
        }

    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# BACKGROUND TASKS
# ============================================================================

def _parse_pst_task(
    job_id: str,
    pst_path: str,
    date_start: str,
    date_end: str,
    min_conversation_messages: int,
    max_messages: Optional[int] = None
):
    """Background task to parse PST file"""
    try:
        logger.info(f"Starting PST parsing job: {job_id}")
        if max_messages:
            logger.info(f"Limiting to {max_messages} messages for testing")

        # Update job status
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
        job.status = "parsing"
        session.commit()

        # Parse PST
        parser = PSTParser(session)
        msg_count, conv_count, err_count = parser.parse_file(
            pst_path,
            date_start,
            date_end,
            min_conversation_messages,
            max_messages
        )

        # Update job with results
        job.status = "completed"
        job.processed_messages = msg_count
        job.total_messages = msg_count
        session.commit()

        logger.info(
            f"PST parsing completed: job_id={job_id}, "
            f"messages={msg_count}, conversations={conv_count}, errors={err_count}"
        )

    except Exception as e:
        logger.error(f"Error in PST parsing task: {e}")
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)
        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
        if job:
            job.status = "failed"
            job.error_message = str(e)
            session.commit()


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=5000,
        reload=True,
        log_level="info"
    )
