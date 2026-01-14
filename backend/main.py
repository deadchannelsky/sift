"""
Sift Backend - FastAPI application
Entry point for PST parsing and enrichment pipeline
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid
import os
from pathlib import Path

from app.models import init_db, get_session, ProcessingJob, Message, Conversation, Extraction
from app.pst_parser import PSTParser
from app.ollama_client import OllamaClient
from app.prompt_manager import PromptManager
from app.enrichment import EnrichmentEngine
from app.aggregator import AggregationEngine
from app.reporter import ReporterEngine
from app.file_upload import (
    sanitize_filename, validate_pst_file, check_disk_space,
    save_uploaded_file, cleanup_old_uploads, get_upload_stats
)
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

# Global objects (initialized on startup)
ollama_client = None
prompt_manager = None
config = {}

# Initialize database and Ollama on startup
@app.on_event("startup")
async def startup_event():
    global ollama_client, prompt_manager, config
    logger.info("=== Sift Backend Starting ===")
    ensure_data_dir()

    # Initialize database
    db_path = get_db_path()
    init_db(db_path)
    logger.info(f"Database initialized: {db_path}")

    # Load config (from root sift directory, not backend/)
    try:
        config_path = BACKEND_DIR.parent / "config.json"  # Root of sift/
        with open(config_path) as f:
            config = json_lib.load(f)
        logger.info(f"✅ Loaded config from: {config_path}")
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        config = {}

    # Initialize Ollama client
    try:

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
                # Test if model is loaded and responding
                if not ollama_client.test_model():
                    logger.warning(f"⚠️  Model '{model}' not responding. You may need to pull it on the server:")
                    logger.warning(f"    ollama pull {model}")
        else:
            logger.warning("Ollama not available - enrichment will not work")

    except Exception as e:
        logger.error(f"Error initializing Ollama client: {e}")
        ollama_client = None

    # Initialize PromptManager
    try:
        prompt_manager = PromptManager()
        if prompt_manager.prompts:
            logger.info(f"✅ Loaded {len(prompt_manager.prompts)} prompts for tasks: {', '.join(prompt_manager.list_tasks())}")
        else:
            logger.warning("No prompts found - enrichment will not work")
    except Exception as e:
        logger.error(f"Error initializing PromptManager: {e}")
        prompt_manager = None


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


class EnrichRequest(BaseModel):
    """Request to start enrichment job"""
    max_messages: Optional[int] = None  # Limit messages for testing (None = all)
    batch_size: int = 5  # Messages per batch


class EnrichStatusResponse(BaseModel):
    """Status of enrichment job"""
    job_id: str
    status: str  # pending, processing, completed, failed
    total_messages: int
    processed_messages: int
    current_task: Optional[str]
    progress_percent: float
    error: Optional[str] = None


class AggregateRequest(BaseModel):
    """Request to start aggregation"""
    output_formats: list = ["json"]


class AggregateStatusResponse(BaseModel):
    """Status of aggregation job"""
    job_id: str
    status: str  # pending, processing, completed, failed
    total_messages: int
    processed_messages: int
    projects_found: int
    stakeholders_found: int
    progress_percent: float
    error: Optional[str] = None


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.post("/upload")
async def upload_pst_file(file: UploadFile = File(...)):
    """
    Upload PST file for processing

    Accepts large PST files (multi-GB) with streaming to disk.
    Validates file format before accepting.

    Args:
        file: PST file to upload

    Returns:
        Dictionary with filename and file size

    Errors:
        400: Invalid file format or failed validation
        500: Server error (disk space, IO error, etc.)
    """
    try:
        # Sanitize filename
        safe_filename = sanitize_filename(file.filename)
        if not safe_filename.lower().endswith('.pst'):
            safe_filename += '.pst'

        # Get upload directory
        upload_dir = Path(config.get("output", {}).get("dir", "./data"))
        file_path = upload_dir / safe_filename

        # Read file content while checking size
        logger.info(f"Uploading file: {safe_filename}")
        file_content = await file.read()
        file_size = len(file_content)

        # Check disk space before saving
        has_space, space_msg = check_disk_space(upload_dir, file_size)
        if not has_space:
            logger.error(space_msg)
            raise HTTPException(status_code=507, detail=space_msg)

        # Save file with streaming
        success, save_msg = save_uploaded_file(file_path, file_content)
        if not success:
            logger.error(save_msg)
            raise HTTPException(status_code=500, detail=save_msg)

        # Validate PST file
        is_valid, validation_msg = validate_pst_file(file_path)
        if not is_valid:
            # Delete invalid file
            try:
                file_path.unlink()
            except:
                pass
            logger.error(f"Invalid PST file: {validation_msg}")
            raise HTTPException(status_code=400, detail=validation_msg)

        # Cleanup old uploads (keep latest 5)
        cleanup_old_uploads(upload_dir, keep_latest_n=5)

        # Get upload stats
        stats = get_upload_stats(upload_dir)

        logger.info(f"✅ Upload successful: {safe_filename} ({file_size / (1024**2):.1f}MB)")

        return {
            "filename": safe_filename,
            "size": file_size,
            "size_mb": round(file_size / (1024 ** 2), 2),
            "upload_stats": stats
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@app.get("/pst-files")
async def list_pst_files():
    """
    List all PST files available in the data directory

    Returns:
        {
            "files": [
                {"filename": "example.pst", "size_bytes": 1024000, "size_mb": 1.0, "uploaded_at": "2025-01-14T12:34:56"},
                ...
            ]
        }
    """
    try:
        data_dir = Path("./data")

        if not data_dir.exists():
            return {"files": []}

        # Find all .pst files in data directory
        pst_files = list(data_dir.glob("*.pst"))

        files_list = []
        for pst_file in sorted(pst_files, key=lambda x: x.stat().st_mtime, reverse=True):
            stat = pst_file.stat()
            files_list.append({
                "filename": pst_file.name,
                "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / (1024 ** 2), 2),
                "uploaded_at": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })

        return {"files": files_list}

    except Exception as e:
        logger.error(f"Error listing PST files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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


@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """
    Cancel a running job (parsing, enrichment, or aggregation)

    Args:
        job_id: Job ID to cancel

    Returns:
        {
            "job_id": "abc123",
            "status": "cancelled",
            "message": "Job cancellation requested"
        }
    """
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()

        if not job:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

        # Mark job for cancellation
        job.cancelled = True
        session.commit()

        logger.info(f"Cancellation requested for job: {job_id}")

        return {
            "job_id": job.job_id,
            "status": job.status,
            "message": "Job cancellation requested"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling job: {e}")
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


@app.get("/reports/{filename}")
async def download_report(filename: str):
    """
    Download a generated report file

    Supported files:
    - aggregated_projects.json
    - aggregated_stakeholders.json
    - Q4_2025_Summary.md
    - projects_summary.csv
    - stakeholders_summary.csv
    - project_stakeholder_matrix.csv

    Args:
        filename: Name of report file to download

    Returns:
        File content with appropriate content-type header

    Errors:
        404: File not found
        403: Invalid filename (path traversal attempt)
    """
    try:
        # Security: Prevent path traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            raise HTTPException(status_code=403, detail="Invalid filename")

        # Allowed report files
        allowed_files = {
            "aggregated_projects.json": "application/json",
            "aggregated_stakeholders.json": "application/json",
            "Q4_2025_Summary.md": "text/markdown",
            "projects_summary.csv": "text/csv",
            "stakeholders_summary.csv": "text/csv",
            "project_stakeholder_matrix.csv": "text/csv"
        }

        if filename not in allowed_files:
            raise HTTPException(status_code=403, detail="File type not allowed for download")

        # Get file path
        data_dir = Path(config.get("output", {}).get("dir", "./data"))
        file_path = data_dir / filename

        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"Report file not found: {filename}")

        # Return file with correct content-type
        media_type = allowed_files[filename]
        logger.info(f"Downloading report: {filename}")

        return FileResponse(
            path=file_path,
            filename=filename,
            media_type=media_type
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading report: {e}")
        raise HTTPException(status_code=500, detail=f"Error downloading report: {str(e)}")


@app.post("/enrich")
async def start_enrichment(request: EnrichRequest, background_tasks: BackgroundTasks = None):
    """Start enrichment job for parsed messages

    Args:
        request: EnrichRequest with max_messages and batch_size
        background_tasks: FastAPI background tasks

    Returns:
        Job ID and status
    """
    if not ollama_client:
        raise HTTPException(status_code=503, detail="Ollama not initialized")

    if not prompt_manager:
        raise HTTPException(status_code=503, detail="PromptManager not initialized")

    if not ollama_client.model:
        raise HTTPException(status_code=400, detail="No model selected. Use /models/{model_name} to select a model.")

    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        # Count pending messages
        pending_count = session.query(Message).filter_by(enrichment_status="pending").count()

        if pending_count == 0:
            raise HTTPException(status_code=400, detail="No messages to enrich")

        # Create job record
        job_id = str(uuid.uuid4())[:8]
        job = ProcessingJob(
            job_id=job_id,
            pst_filename="enrichment_job",
            status="queued",
            total_messages=min(pending_count, request.max_messages) if request.max_messages else pending_count
        )
        session.add(job)
        session.commit()

        # Queue background task
        if background_tasks:
            background_tasks.add_task(
                _enrich_messages_task,
                job_id,
                request.max_messages,
                request.batch_size
            )

        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"Enrichment job queued. Processing {job.total_messages} messages"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting enrichment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/enrich/{job_id}/status")
async def get_enrichment_status(job_id: str) -> EnrichStatusResponse:
    """Check enrichment job status"""
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

        return EnrichStatusResponse(
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
        logger.error(f"Error getting enrichment status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/aggregate")
async def start_aggregation(request: AggregateRequest, background_tasks: BackgroundTasks = None):
    """Start aggregation job to cluster projects and deduplicate stakeholders

    Args:
        request: AggregateRequest with output formats
        background_tasks: FastAPI background tasks

    Returns:
        Job ID and status
    """
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        # Count completed messages
        completed_count = session.query(Message).filter_by(enrichment_status="completed").count()

        if completed_count == 0:
            raise HTTPException(status_code=400, detail="No enriched messages to aggregate")

        # Create job record
        job_id = str(uuid.uuid4())[:8]
        job = ProcessingJob(
            job_id=job_id,
            pst_filename="aggregation_job",
            status="queued",
            total_messages=completed_count
        )
        session.add(job)
        session.commit()

        # Queue background task
        if background_tasks:
            background_tasks.add_task(_aggregate_data_task, job_id)

        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"Aggregation job queued. Processing {completed_count} enriched messages"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting aggregation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/aggregate/{job_id}/status")
async def get_aggregation_status(job_id: str) -> AggregateStatusResponse:
    """Check aggregation job status"""
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

        # Parse projects/stakeholders counts from error_message field (used to store extra data)
        projects_found = 0
        stakeholders_found = 0
        if job.error_message and job.status == "completed":
            try:
                stats = json_lib.loads(job.error_message)
                projects_found = stats.get("projects_found", 0)
                stakeholders_found = stats.get("stakeholders_found", 0)
            except:
                pass

        return AggregateStatusResponse(
            job_id=job.job_id,
            status=job.status,
            total_messages=job.total_messages,
            processed_messages=job.processed_messages,
            projects_found=projects_found,
            stakeholders_found=stakeholders_found,
            progress_percent=progress_percent,
            error=None if job.status != "failed" else job.error_message
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting aggregation status: {e}")
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


def _enrich_messages_task(job_id: str, max_messages: Optional[int] = None, batch_size: int = 5):
    """Background task to enrich messages with LLM"""
    try:
        logger.info(f"Starting enrichment job: {job_id}")

        # Get database session
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        # Get job and update status
        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
        if not job:
            logger.error(f"Job not found: {job_id}")
            return

        job.status = "processing"
        session.commit()

        # Get pending messages (limited by max_messages if specified)
        query = session.query(Message).filter_by(enrichment_status="pending")
        if max_messages:
            query = query.limit(max_messages)

        pending_messages = query.all()
        total = len(pending_messages)

        if total == 0:
            logger.info("No messages to enrich")
            job.status = "completed"
            job.processed_messages = 0
            session.commit()
            return

        logger.info(f"Processing {total} messages")

        # Initialize enrichment engine
        engine_for_enrichment = EnrichmentEngine(ollama_client, prompt_manager, session)

        # Process in batches
        for batch_idx in range(0, total, batch_size):
            # Check if cancellation was requested
            job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
            if job.cancelled:
                logger.info(f"Enrichment job cancelled: {job_id}")
                job.status = "cancelled"
                job.current_task = None
                session.commit()
                return

            batch = pending_messages[batch_idx:batch_idx + batch_size]
            message_ids = [msg.id for msg in batch]

            job.current_task = f"batch_{batch_idx // batch_size + 1}"
            session.commit()

            # Enrich batch
            engine_for_enrichment.enrich_batch(message_ids, config, show_progress=False)

            # Update job progress
            job.processed_messages = batch_idx + len(batch)
            session.commit()

            progress = (job.processed_messages / total) * 100
            logger.info(f"Enrichment progress: {progress:.1f}% ({job.processed_messages}/{total})")

        # Job complete
        job.status = "completed"
        job.processed_messages = total
        job.current_task = None
        session.commit()

        logger.info(f"Enrichment complete: {total} messages processed")

    except Exception as e:
        logger.error(f"Error in enrichment task: {e}")
        try:
            db_path = get_db_path()
            engine = init_db(db_path)
            session = get_session(engine)
            job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(e)
                session.commit()
        except:
            pass


def _aggregate_data_task(job_id: str):
    """Background task to aggregate projects and stakeholders"""
    try:
        logger.info(f"Starting aggregation job: {job_id}")

        # Get database session
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        # Update job status
        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
        if not job:
            logger.error(f"Job not found: {job_id}")
            return

        job.status = "processing"
        job.current_task = "aggregation"
        session.commit()

        # Check if cancellation was requested
        if job.cancelled:
            logger.info(f"Aggregation job cancelled: {job_id}")
            job.status = "cancelled"
            job.current_task = None
            session.commit()
            return

        # Initialize aggregation engine
        aggregator = AggregationEngine(session, config)

        # Run aggregation
        stats = aggregator.run_aggregation()

        # Write JSON outputs
        output_dir = config.get("output", {}).get("dir", "./data")
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        aggregator.write_json_outputs(str(output_path))

        # Generate Markdown and CSV reports
        try:
            reporter = ReporterEngine(config)
            reporter.generate_all_reports(str(output_path), str(output_path))
            logger.info("Generated Markdown and CSV reports")
        except Exception as e:
            logger.error(f"Report generation failed: {e}")
            # Don't fail the whole job if reporting fails

        # Update job with results
        job.status = "completed"
        job.processed_messages = stats["messages_processed"]
        job.current_task = None

        # Store stats in error_message field (hack but works)
        job.error_message = json_lib.dumps({
            "projects_found": stats["projects_found"],
            "stakeholders_found": stats["stakeholders_found"]
        })
        session.commit()

        logger.info(
            f"Aggregation complete: job_id={job_id}, "
            f"messages={stats['messages_processed']}, "
            f"projects={stats['projects_found']}, "
            f"stakeholders={stats['stakeholders_found']}"
        )

    except Exception as e:
        logger.error(f"Error in aggregation task: {e}")
        try:
            db_path = get_db_path()
            engine = init_db(db_path)
            session = get_session(engine)
            job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(e)
                session.commit()
        except:
            pass


# ============================================================================
# STATIC FILE SERVING (Frontend) - MUST BE LAST (after all API routes)
# ============================================================================

# Mount frontend directory for serving static files
# NOTE: This must be mounted AFTER all API endpoints, otherwise it will
# intercept all requests before they reach the API handlers
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
    logger.info(f"✅ Frontend served from: {frontend_dir}")
else:
    logger.warning(f"⚠️ Frontend directory not found: {frontend_dir}")


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
