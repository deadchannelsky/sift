"""
Sift Backend - FastAPI application
Entry point for PST parsing and enrichment pipeline
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Tuple
from datetime import datetime
import uuid
import os
import time
from pathlib import Path

from app.models import init_db, get_session, ProcessingJob, Message, Conversation, Extraction, AggregationSettings, ProjectClusterMetadata, REPLSession, REPLQueryHistory
from app.pst_parser import PSTParser
from app.ollama_client import OllamaClient
from app.prompt_manager import PromptManager
from app.enrichment import EnrichmentEngine
from app.aggregator import AggregationEngine
from app.reporter import ReporterEngine
from app.post_aggregation_filter import PostAggregationFilter
from app.repl_engine import REPLEngine
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
# Allow any localhost origin for development (frontend can be on any port)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5000",
        "http://localhost:8000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5000",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8080",
    ],
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
    clear_database: bool = False  # Clear all tables before parsing if True
    relevance_threshold: float = 0.80  # Confidence threshold for spam filter (0.0-1.0)


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
    """Request to start aggregation with optional settings override"""
    output_formats: list = ["json"]
    aggregation_settings: Optional[AggregationSettings] = None


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


class PostAggregationFilterRequest(BaseModel):
    """Request to start post-aggregation quality filter"""
    role_description: str  # Free-text description of user's role and responsibilities
    confidence_threshold: float = 0.75  # Min confidence to include project


class PostAggregationFilterStatusResponse(BaseModel):
    """Status of post-aggregation filter job"""
    job_id: str
    status: str  # pending, processing, completed, failed
    role_description: str
    confidence_threshold: float
    total_projects: int
    processed_projects: int
    progress_percent: float
    projects_included: int
    projects_excluded: int
    error: Optional[str] = None


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.options("/upload")
async def upload_options():
    """Handle CORS preflight requests for file upload"""
    return {"status": "ok"}


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
    file_content = None
    file_path = None

    try:
        # Sanitize filename
        safe_filename = sanitize_filename(file.filename)
        if not safe_filename.lower().endswith('.pst'):
            safe_filename += '.pst'

        logger.info(f"Upload endpoint reached. File: {safe_filename}, Content-Type: {file.content_type}")

        # Get upload directory
        upload_dir = Path(config.get("output", {}).get("dir", "./data"))
        file_path = upload_dir / safe_filename

        # Read file content while checking size
        logger.info(f"Uploading file: {safe_filename}")
        file_content = await file.read()
        file_size = len(file_content)
        logger.info(f"File read complete: {file_size / (1024**3):.2f}GB")

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

    finally:
        # Ensure file handle is closed and content is garbage collected
        try:
            await file.close()
        except:
            pass

        # Clear large file content from memory
        if file_content is not None:
            del file_content


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

        # Clear database if requested by user
        if request.clear_database:
            logger.warning("⚠️  User requested database clear before parsing")
            from app.models import clear_all_tables
            try:
                clear_all_tables(session)
                logger.info("Database cleared successfully")
            except Exception as e:
                logger.error(f"Failed to clear database: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to clear database: {str(e)}"
                )

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
                request.max_messages,
                request.relevance_threshold
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
    import time

    max_retries = 3
    retry_delay = 0.1  # seconds

    for attempt in range(max_retries):
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

            session.close()

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
            if "database is locked" in str(e) and attempt < max_retries - 1:
                # Transient lock contention, retry
                logger.debug(f"Database locked, retrying ({attempt + 1}/{max_retries})...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
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


@app.post("/models/{model_name:path}")
async def set_model(model_name: str):
    """Switch to a different model

    Args:
        model_name: Name of the model to use (e.g., "granite-4.0-h-tiny" or "hf.co/org/model:variant")
                   URL-encoded names with slashes and colons are automatically decoded

    Returns:
        Success status and model info
    """
    if not ollama_client:
        raise HTTPException(status_code=503, detail="Ollama not initialized")

    try:
        logger.info(f"Attempting to switch model to: {model_name}")

        if ollama_client.set_model(model_name):
            logger.info(f"✅ Model switched successfully to: {model_name}")
            return {
                "status": "success",
                "current_model": ollama_client.model,
                "message": f"Model set to: {model_name}"
            }
        else:
            logger.warning(f"Model '{model_name}' not found on server")
            raise HTTPException(
                status_code=400,
                detail=f"Model '{model_name}' not found on server"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting model '{model_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/pipeline/resume")
async def check_pipeline_resume():
    """
    Check if pipeline can be resumed from a previous stage

    Returns:
        {
            "can_resume": bool,
            "stage": "parse" | "enrich" | "aggregate" | null,
            "message": "Description of what can be resumed",
            "last_job_id": "job_id" | null,
            "stats": {
                "total_messages": int,
                "pending_enrichment": int,
                "completed_enrichment": int,
                "conversations": int
            }
        }
    """
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        message_count = session.query(Message).count()
        conversation_count = session.query(Conversation).count()
        pending_enrichment = session.query(Message).filter_by(enrichment_status="pending").count()
        completed_enrichment = session.query(Message).filter_by(enrichment_status="completed").count()

        # Get last job
        last_job = session.query(ProcessingJob).order_by(ProcessingJob.created_at.desc()).first()

        resume_stage = None
        message = ""

        if message_count > 0 and completed_enrichment > 0:
            # Can jump to aggregation
            resume_stage = "aggregate"
            message = f"Resume at Aggregation: {completed_enrichment} messages enriched, ready to aggregate"
        elif message_count > 0 and pending_enrichment > 0:
            # Can jump to enrichment
            resume_stage = "enrich"
            message = f"Resume at Enrichment: {pending_enrichment} messages parsed, ready for enrichment"
        elif message_count > 0:
            # Messages exist but unclear state
            resume_stage = "enrich"
            message = f"Resume at Enrichment: {message_count} messages in database"

        logger.info(f"Pipeline resume check: stage={resume_stage}, messages={message_count}, enriched={completed_enrichment}")

        return {
            "can_resume": resume_stage is not None,
            "stage": resume_stage,
            "message": message,
            "last_job_id": last_job.job_id if last_job else None,
            "stats": {
                "total_messages": message_count,
                "conversations": conversation_count,
                "pending_enrichment": pending_enrichment,
                "completed_enrichment": completed_enrichment
            }
        }

    except Exception as e:
        logger.error(f"Error checking pipeline resume status: {e}")
        return {
            "can_resume": False,
            "stage": None,
            "message": f"Error checking resume status: {str(e)}",
            "last_job_id": None,
            "stats": {}
        }


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
        request: AggregateRequest with optional settings override
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

        # Prepare aggregation settings (merge request settings with config defaults)
        aggregation_settings = None
        if request.aggregation_settings:
            # Convert Pydantic model to dict for merging
            settings_dict = request.aggregation_settings.dict()

            # Build merged config structure
            aggregation_settings = {
                "stakeholder_filtering": {
                    "min_role_confidence": settings_dict["min_role_confidence"],
                    "min_mention_count": settings_dict["min_mention_count"],
                    "exclude_generic_names": settings_dict["exclude_generic_names"],
                    "enable_name_deduplication": settings_dict["enable_name_deduplication"],
                    "name_similarity_threshold": settings_dict["name_similarity_threshold"],
                    "validate_email_domains": settings_dict["validate_email_domains"],
                    "enable_filtering": True
                },
                "diagnostics": {
                    "enable_diagnostics": settings_dict["enable_diagnostics"],
                    "output_raw_extractions": True,
                    "output_filter_log": True,
                    "output_comparison": True
                }
            }
            logger.info(f"Aggregation job {job_id}: Using custom settings from request")

        # Queue background task with optional settings override
        if background_tasks:
            background_tasks.add_task(_aggregate_data_task, job_id, aggregation_settings)

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


@app.get("/config/aggregation-defaults")
async def get_aggregation_defaults() -> dict:
    """Return current aggregation default settings from config.json"""
    try:
        stakeholder_filtering = config.get("stakeholder_filtering", {})
        diagnostics = config.get("diagnostics", {})

        return {
            "min_role_confidence": stakeholder_filtering.get("min_role_confidence", 0.65),
            "min_mention_count": stakeholder_filtering.get("min_mention_count", 2),
            "exclude_generic_names": stakeholder_filtering.get("exclude_generic_names", True),
            "enable_name_deduplication": stakeholder_filtering.get("enable_name_deduplication", True),
            "name_similarity_threshold": stakeholder_filtering.get("name_similarity_threshold", 0.80),
            "validate_email_domains": stakeholder_filtering.get("validate_email_domains", True),
            "enable_diagnostics": diagnostics.get("enable_diagnostics", True)
        }
    except Exception as e:
        logger.error(f"Error fetching aggregation defaults: {e}")
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


@app.post("/post-aggregate-filter")
async def start_post_aggregation_filter(
    request: PostAggregationFilterRequest,
    background_tasks: BackgroundTasks = None
):
    """
    Start post-aggregation quality filter job

    Evaluates aggregated projects for relevance to user role using LLM.
    Returns confidence scores and reasoning for filtering decisions.

    Args:
        request: PostAggregationFilterRequest with user role and threshold
        background_tasks: FastAPI background tasks

    Returns:
        Job ID and status
    """
    try:
        # Load aggregated projects from JSON
        output_dir = config.get("output", {}).get("dir", "./data")
        projects_file = Path(output_dir) / "aggregated_projects.json"

        if not projects_file.exists():
            raise HTTPException(status_code=400, detail="No aggregated projects found. Run aggregation first.")

        with open(projects_file, "r") as f:
            aggregation_data = json_lib.load(f)

        projects = aggregation_data.get("projects", [])

        if not projects:
            raise HTTPException(status_code=400, detail="No projects in aggregation output")

        # Create job record (reuse ProcessingJob for simplicity)
        job_id = str(uuid.uuid4())[:8]
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        job = ProcessingJob(
            job_id=job_id,
            pst_filename="post_agg_filter",
            status="queued",
            total_messages=len(projects)  # Reuse for project count
        )
        session.add(job)
        session.commit()

        # Queue background task
        if background_tasks:
            background_tasks.add_task(
                _post_aggregation_filter_task,
                job_id,
                request.role_description,
                request.confidence_threshold,
                projects
            )

        return {
            "job_id": job_id,
            "status": "queued",
            "role_description": request.role_description,
            "confidence_threshold": request.confidence_threshold,
            "message": f"Filter job queued. Evaluating {len(projects)} projects"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting post-aggregation filter: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/post-aggregate-filter/{job_id}/status")
async def get_post_aggregation_filter_status(job_id: str) -> PostAggregationFilterStatusResponse:
    """Check post-aggregation filter job status"""
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

        # Extract filter results from error_message field (used to store metadata)
        role_description = "User role description"
        confidence_threshold = 0.75
        projects_included = 0
        projects_excluded = 0

        if job.error_message and job.status == "completed":
            try:
                stats = json_lib.loads(job.error_message)
                role_description = stats.get("role_description", role_description)
                confidence_threshold = stats.get("confidence_threshold", confidence_threshold)
                projects_included = stats.get("projects_included", 0)
                projects_excluded = stats.get("projects_excluded", 0)
            except:
                pass

        return PostAggregationFilterStatusResponse(
            job_id=job.job_id,
            status=job.status,
            role_description=role_description,
            confidence_threshold=confidence_threshold,
            total_projects=job.total_messages,
            processed_projects=job.processed_messages,
            progress_percent=progress_percent,
            projects_included=projects_included,
            projects_excluded=projects_excluded,
            error=None if job.status != "failed" else job.error_message
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting filter status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/post-aggregate-filter/results/summary")
async def get_post_aggregation_filter_results():
    """Get summary of last post-aggregation filter run"""
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        # Get all filter metadata
        all_metadata = session.query(ProjectClusterMetadata).filter_by(
            post_agg_filter_enabled=True
        ).all()

        if not all_metadata:
            raise HTTPException(status_code=404, detail="No filter results found")

        # Get latest filter run (by updated_at)
        latest_run = max(all_metadata, key=lambda m: m.updated_at)

        # Calculate statistics
        total_projects = len(all_metadata)
        excluded_projects = len([m for m in all_metadata if m.post_agg_filtered])
        included_projects = total_projects - excluded_projects

        confidences = [m.post_agg_confidence for m in all_metadata if m.post_agg_confidence is not None]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0

        # Get excluded projects with reasoning
        excluded_with_reasoning = []
        for m in all_metadata:
            if m.post_agg_filtered:
                try:
                    reasoning = json_lib.loads(m.post_agg_reasoning) if m.post_agg_reasoning else []
                except:
                    reasoning = [m.post_agg_reasoning or "No reasoning available"]

                excluded_with_reasoning.append({
                    "name": m.cluster_canonical_name,
                    "confidence": m.post_agg_confidence or 0,
                    "reasoning": reasoning[0] if reasoning else "Unknown reason"
                })

        return {
            "last_filter_run": latest_run.updated_at.isoformat() if latest_run else None,
            "user_role": latest_run.post_agg_user_role if latest_run else "Unknown",
            "confidence_threshold": latest_run.post_agg_user_threshold if latest_run else 0.75,
            "total_projects": total_projects,
            "included_projects": included_projects,
            "excluded_projects": excluded_projects,
            "confidence_avg": avg_confidence,
            "excluded_projects_preview": excluded_with_reasoning[:10]  # Top 10 excluded
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting filter results: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# RAG (RETRIEVAL-AUGMENTED GENERATION) ENDPOINTS
# ============================================================================

@app.post("/rag/embeddings/generate")
async def generate_embeddings(background_tasks: BackgroundTasks):
    """
    Trigger background job to generate embeddings for all enriched messages

    Creates vector embeddings using ChromaDB + nomic-embed-text for semantic search.
    This is a prerequisite for RAG queries.

    Returns:
        Job ID and status for polling
    """
    try:
        from app.models import RAGSession
        from app.vector_store import VectorStore
        import requests as req

        job_id = str(uuid.uuid4())[:8]
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        # Validate embedding model is available before starting job
        try:
            embedding_model = config.get("ollama", {}).get("embedding_model")
            test_store = VectorStore(ollama_url=ollama_client.url, embedding_model=embedding_model)

            # Test Ollama connection and model availability
            response = req.get(f"{ollama_client.url}/api/tags", timeout=5)
            response.raise_for_status()
            models = response.json().get("models", [])

            # Check if embedding model exists
            model_names = [m["name"] for m in models]
            if not any(test_store.embedding_model in name for name in model_names):
                raise HTTPException(
                    status_code=400,
                    detail=f"Embedding model '{test_store.embedding_model}' not found in Ollama. Available models: {', '.join(model_names)}"
                )
        except req.exceptions.ConnectionError:
            raise HTTPException(
                status_code=503,
                detail="Cannot connect to Ollama server. Please ensure Ollama is running."
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error validating embedding model: {e}")
            raise HTTPException(status_code=500, detail=f"Validation error: {str(e)}")

        # Create job record
        job = ProcessingJob(
            job_id=job_id,
            pst_filename="rag_embeddings",
            status="queued"
        )
        session.add(job)
        session.commit()

        logger.info(f"Starting embedding generation job: {job_id}")

        # Add background task
        background_tasks.add_task(_generate_embeddings_task, job_id)

        return {"job_id": job_id, "status": "queued"}

    except Exception as e:
        logger.error(f"Error starting embedding generation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/rag/embeddings/status/{job_id}")
async def get_embedding_status(job_id: str):
    """Get status of embedding generation job"""
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Calculate progress percentage from processed/total messages
        progress_percent = 0
        if job.total_messages > 0:
            progress_percent = int((job.processed_messages / job.total_messages) * 100)

        return {
            "job_id": job_id,
            "status": job.status,
            "progress_percent": progress_percent,
            "message": f"Embedding generation {job.status}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting embedding status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rag/session")
async def create_rag_session():
    """
    Create a new RAG chat session

    Returns:
        Session ID for subsequent queries
    """
    try:
        from app.models import RAGSession

        session_id = str(uuid.uuid4())[:8]
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        rag_session = RAGSession(id=session_id)
        session.add(rag_session)
        session.commit()

        logger.info(f"Created RAG session: {session_id}")

        return {"session_id": session_id}

    except Exception as e:
        logger.error(f"Error creating RAG session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rag/{session_id}/query")
async def query_rag(session_id: str, request: dict):
    """
    Submit a question to the RAG engine

    Args:
        session_id: RAG session ID
        request: {"query": "...", "chat_history": [...]}

    Returns:
        {"answer": "...", "citations": [...], "retrieved_count": N}
    """
    try:
        from app.models import RAGQueryHistory, RAGSession
        from app.rag_engine import RAGEngine

        query = request.get("query", "").strip()
        chat_history = request.get("chat_history", [])

        if not query:
            raise HTTPException(status_code=400, detail="Query cannot be empty")

        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        # Verify session exists
        rag_session = session.query(RAGSession).filter_by(id=session_id).first()
        if not rag_session:
            raise HTTPException(status_code=404, detail="RAG session not found")

        # Initialize RAG components
        try:
            from app.vector_store import VectorStore
            embedding_model = config.get("ollama", {}).get("embedding_model")
            vector_store = VectorStore(ollama_client.url, embedding_model=embedding_model)
        except ImportError as e:
            raise HTTPException(
                status_code=503,
                detail=f"Vector store not available: {e}. Run: pip install chromadb"
            )

        rag_engine = RAGEngine(session, ollama_client, vector_store, prompt_manager)

        # Process query
        logger.info(f"Processing RAG query in session {session_id}: {query[:80]}...")
        result = rag_engine.query(query, chat_history)

        # Store in history
        history = RAGQueryHistory(
            session_id=session_id,
            query=query,
            answer=result["answer"],
            citations_json=json_lib.dumps(result["citations"]),
            retrieved_count=result["retrieved_count"]
        )
        session.add(history)

        # Update session metadata
        rag_session.last_query_at = datetime.utcnow()
        rag_session.query_count += 1

        session.commit()

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing RAG query: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/rag/{session_id}/history")
async def get_rag_history(session_id: str):
    """Get conversation history for a RAG session"""
    try:
        from app.models import RAGQueryHistory, RAGSession

        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        # Verify session exists
        rag_session = session.query(RAGSession).filter_by(id=session_id).first()
        if not rag_session:
            raise HTTPException(status_code=404, detail="RAG session not found")

        # Get history in chronological order
        history = session.query(RAGQueryHistory).filter_by(
            session_id=session_id
        ).order_by(RAGQueryHistory.created_at).all()

        messages = []
        for h in history:
            # User message
            messages.append({
                "role": "user",
                "content": h.query,
                "timestamp": h.created_at.isoformat()
            })
            # Assistant message
            citations = []
            if h.citations_json:
                try:
                    citations = json_lib.loads(h.citations_json)
                except:
                    pass

            messages.append({
                "role": "assistant",
                "content": h.answer,
                "citations": citations,
                "timestamp": h.created_at.isoformat()
            })

        return {"session_id": session_id, "messages": messages}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting RAG history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/rag/message/{message_id}")
async def get_message_details(message_id: int):
    """
    Get full message details for citation expansion

    Args:
        message_id: Database message ID

    Returns:
        Full message with extracted data
    """
    try:
        from app.models import Message, Extraction

        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        message = session.query(Message).filter_by(id=message_id).first()
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")

        # Get extractions
        extractions = session.query(Extraction).filter_by(message_id=message_id).all()

        extraction_data = {}
        for ext in extractions:
            try:
                extraction_data[ext.task_name] = json_lib.loads(ext.extraction_json)
            except:
                extraction_data[ext.task_name] = {}

        return {
            "message_id": message.id,
            "subject": message.subject or "(no subject)",
            "sender": {
                "name": message.sender_name or "Unknown",
                "email": message.sender_email or "unknown@example.com"
            },
            "recipients": message.recipients or "unknown",
            "date": message.delivery_date.isoformat() if message.delivery_date else "",
            "body": message.body_full or message.body_snippet or "(empty)",
            "extractions": extraction_data
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting message details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# REPL (CODE-BASED EXPLORATION) ENDPOINTS
# ============================================================================

@app.get("/repl/corpus/stats")
async def get_repl_corpus_stats():
    """Get corpus statistics for REPL exploration

    Returns message count, date range, unique senders/projects
    """
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        db = get_session(engine)

        try:
            repl_engine = REPLEngine(db, ollama_client, prompt_manager)
            stats = repl_engine.get_corpus_stats()

            return {
                "success": True,
                "stats": stats
            }

        finally:
            db.close()

    except Exception as e:
        logger.error(f"Error getting REPL corpus stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/repl/session")
async def create_repl_session():
    """Create new REPL exploration session

    Returns session ID and corpus stats
    """
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        db = get_session(engine)

        try:
            # Load corpus stats
            repl_engine = REPLEngine(db, ollama_client, prompt_manager)
            stats = repl_engine.get_corpus_stats()

            if stats["total_messages"] == 0:
                raise HTTPException(
                    status_code=400,
                    detail="No enriched messages available. Run the enrichment pipeline first."
                )

            # Create session
            session_id = str(uuid.uuid4())
            session = REPLSession(
                id=session_id,
                model_used=ollama_client.model if ollama_client else None,
                corpus_message_count=stats["total_messages"]
            )
            db.add(session)
            db.commit()

            logger.info(f"Created REPL session {session_id} with {stats['total_messages']} messages")

            return {
                "success": True,
                "session_id": session_id,
                "corpus_stats": stats,
                "current_model": ollama_client.model if ollama_client else None
            }

        finally:
            db.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating REPL session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/repl/{session_id}/query")
async def repl_query(session_id: str, request: dict):
    """Execute REPL query with code generation

    Request body:
        - question: Natural language question about the corpus
        - max_iterations: Max exploration iterations (default 3)
        - model: Optional model override for this query

    Returns:
        - answer: Final interpreted answer
        - trace: List of exploration steps (code, result, interpretation)
        - corpus_stats: Corpus statistics
        - model_used: Model that was used
    """
    try:
        question = request.get("question", "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="Question is required")

        max_iterations = request.get("max_iterations", 3)
        model_override = request.get("model")

        db_path = get_db_path()
        engine = init_db(db_path)
        db = get_session(engine)

        try:
            # Verify session exists
            session = db.query(REPLSession).filter_by(id=session_id).first()
            if not session:
                raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

            # Execute REPL query
            repl_engine = REPLEngine(db, ollama_client, prompt_manager)
            result = repl_engine.query(
                user_question=question,
                max_iterations=max_iterations,
                model_override=model_override
            )

            # Save to history
            history_entry = REPLQueryHistory(
                session_id=session_id,
                query=question,
                answer=result["answer"],
                trace_json=json_lib.dumps(result["trace"], default=str),
                model_used=result["model_used"]
            )
            db.add(history_entry)

            # Update session
            session.last_query_at = datetime.utcnow()
            session.query_count += 1
            session.model_used = result["model_used"]

            db.commit()

            logger.info(f"REPL query completed: {len(result['trace'])} steps, model={result['model_used']}")

            return {
                "success": True,
                "answer": result["answer"],
                "trace": result["trace"],
                "corpus_stats": result["corpus_stats"],
                "model_used": result["model_used"],
                "session_id": session_id
            }

        finally:
            db.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing REPL query: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/repl/{session_id}/history")
async def get_repl_history(session_id: str):
    """Get query history for REPL session

    Returns list of previous queries with their traces
    """
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        db = get_session(engine)

        try:
            session = db.query(REPLSession).filter_by(id=session_id).first()
            if not session:
                raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

            history = db.query(REPLQueryHistory).filter_by(
                session_id=session_id
            ).order_by(REPLQueryHistory.created_at.desc()).all()

            return {
                "success": True,
                "session_id": session_id,
                "query_count": len(history),
                "history": [
                    {
                        "id": h.id,
                        "query": h.query,
                        "answer": h.answer,
                        "trace": json_lib.loads(h.trace_json) if h.trace_json else [],
                        "model_used": h.model_used,
                        "created_at": h.created_at.isoformat() if h.created_at else None
                    }
                    for h in history
                ]
            }

        finally:
            db.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting REPL history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# DATA INSPECTOR ENDPOINTS
# ============================================================================

@app.get("/inspector/stats")
async def get_inspector_stats():
    """Get message statistics for the data inspector

    Returns counts of total, enriched, pending, failed messages
    and count of messages with Task E extractions.
    """
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        db = get_session(engine)

        try:
            total = db.query(Message).count()
            enriched = db.query(Message).filter_by(enrichment_status="completed").count()
            pending = db.query(Message).filter_by(enrichment_status="pending").count()
            failed = db.query(Message).filter_by(enrichment_status="failed").count()

            # Count messages with Task E extractions
            task_e_count = db.query(Extraction).filter_by(task_name="task_e_summary").count()

            return {
                "success": True,
                "stats": {
                    "total": total,
                    "enriched": enriched,
                    "pending": pending,
                    "failed": failed,
                    "task_e": task_e_count
                }
            }

        finally:
            db.close()

    except Exception as e:
        logger.error(f"Error getting inspector stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/inspector/messages")
async def get_inspector_messages(
    status: str = "all",
    search: str = "",
    page: int = 1,
    page_size: int = 25
):
    """Get paginated list of messages for the inspector

    Args:
        status: Filter by enrichment_status (all, completed, pending, failed)
        search: Search term for subject or sender
        page: Page number (1-indexed)
        page_size: Number of messages per page

    Returns:
        List of message summaries with pagination info
    """
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        db = get_session(engine)

        try:
            query = db.query(Message)

            # Filter by status
            if status != "all":
                query = query.filter_by(enrichment_status=status)

            # Search filter
            if search:
                search_pattern = f"%{search}%"
                query = query.filter(
                    (Message.subject.ilike(search_pattern)) |
                    (Message.sender_email.ilike(search_pattern)) |
                    (Message.sender_name.ilike(search_pattern))
                )

            # Get total count before pagination
            total_count = query.count()

            # Order and paginate
            offset = (page - 1) * page_size
            messages = query.order_by(Message.delivery_date.desc()).offset(offset).limit(page_size).all()

            return {
                "success": True,
                "messages": [
                    {
                        "id": msg.id,
                        "subject": (msg.subject or "")[:80],
                        "sender_email": msg.sender_email or "",
                        "sender_name": msg.sender_name or "",
                        "date": msg.delivery_date.strftime("%Y-%m-%d") if msg.delivery_date else "",
                        "status": msg.enrichment_status or "pending"
                    }
                    for msg in messages
                ],
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_count": total_count,
                    "total_pages": (total_count + page_size - 1) // page_size
                }
            }

        finally:
            db.close()

    except Exception as e:
        logger.error(f"Error getting inspector messages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/inspector/message/{message_id}")
async def get_inspector_message_detail(message_id: int):
    """Get detailed message data including all extractions

    Args:
        message_id: Database ID of the message

    Returns:
        Full message data and all extraction results
    """
    try:
        db_path = get_db_path()
        engine = init_db(db_path)
        db = get_session(engine)

        try:
            message = db.query(Message).filter_by(id=message_id).first()
            if not message:
                raise HTTPException(status_code=404, detail=f"Message {message_id} not found")

            # Get all extractions for this message
            extractions = db.query(Extraction).filter_by(message_id=message_id).all()

            extraction_data = {}
            for ext in extractions:
                try:
                    parsed = json_lib.loads(ext.extraction_json) if ext.extraction_json else None
                    extraction_data[ext.task_name] = {
                        "data": parsed,
                        "confidence": ext.confidence,
                        "prompt_version": ext.prompt_version,
                        "processing_time_ms": ext.processing_time_ms
                    }
                except json_lib.JSONDecodeError:
                    extraction_data[ext.task_name] = {
                        "data": None,
                        "error": "JSON parse error",
                        "raw": ext.extraction_json[:500] if ext.extraction_json else None
                    }

            return {
                "success": True,
                "message": {
                    "id": message.id,
                    "msg_id": message.msg_id,
                    "subject": message.subject or "",
                    "sender_email": message.sender_email or "",
                    "sender_name": message.sender_name or "",
                    "recipients": message.recipients or "",
                    "cc": message.cc or "",
                    "date": message.delivery_date.isoformat() if message.delivery_date else "",
                    "body_snippet": (message.body_full or message.body_snippet or "")[:2000],
                    "body_length": len(message.body_full or message.body_snippet or ""),
                    "message_class": message.message_class or "",
                    "enrichment_status": message.enrichment_status or "pending",
                    "is_spurious": message.is_spurious
                },
                "extractions": extraction_data
            }

        finally:
            db.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting message detail: {e}")
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
    max_messages: Optional[int] = None,
    relevance_threshold: float = 0.80
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

        # Parse PST (with AI-powered relevance filtering)
        # Override threshold from request parameter
        parse_config = config.copy()
        if "parsing" not in parse_config:
            parse_config["parsing"] = {}
        parse_config["parsing"]["relevance_threshold"] = relevance_threshold

        parser = PSTParser(
            session,
            ollama_client=ollama_client,
            prompt_manager=prompt_manager,
            config=parse_config
        )
        msg_count, conv_count, err_count = parser.parse_file(
            pst_path,
            date_start,
            date_end,
            min_conversation_messages,
            max_messages
        )

        # Log filtering statistics
        if parser.filtered_count > 0:
            filter_pct = (parser.filtered_count / (msg_count + parser.filtered_count) * 100) if msg_count else 0
            logger.info(f"Filtered {parser.filtered_count} spurious emails ({filter_pct:.1f}% of total)")

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
        # IMPORTANT: Filter out spurious emails marked by relevance filter
        query = session.query(Message).filter(
            Message.enrichment_status == "pending",
            Message.is_spurious == False  # Exclude filtered emails
        )
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


def _aggregate_data_task(job_id: str, aggregation_settings: dict = None):
    """Background task to aggregate projects and stakeholders

    Args:
        job_id: Unique job identifier
        aggregation_settings: Optional dict of aggregation settings to override config.json defaults
    """
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

        # Prepare effective config (merge request settings with defaults)
        effective_config = config.copy() if config else {}
        if aggregation_settings:
            if "stakeholder_filtering" not in effective_config:
                effective_config["stakeholder_filtering"] = {}
            if "diagnostics" not in effective_config:
                effective_config["diagnostics"] = {}

            effective_config["stakeholder_filtering"].update(aggregation_settings.get("stakeholder_filtering", {}))
            effective_config["diagnostics"].update(aggregation_settings.get("diagnostics", {}))
            logger.info(f"Aggregation job {job_id}: Using override settings")

        # Initialize aggregation engine with effective config
        aggregator = AggregationEngine(session, effective_config)

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

def _generate_embeddings_task(job_id: str):
    """
    Background task to generate embeddings for RAG

    Indexes all enriched messages into ChromaDB using nomic-embed-text embeddings
    for semantic search during RAG queries.

    Args:
        job_id: Unique job identifier
    """
    try:
        from app.models import Message, Extraction, MessageEmbedding
        from app.vector_store import VectorStore

        logger.info(f"Starting embedding generation job: {job_id}")

        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        # Update job status
        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        job.status = "processing"
        session.commit()

        # Initialize vector store
        embedding_model = config.get("ollama", {}).get("embedding_model")
        vector_store = VectorStore(ollama_client.url, embedding_model=embedding_model)
        logger.info(f"Vector store initialized at {vector_store.collection.count()} embeddings")

        # Get all enriched messages
        messages = session.query(Message).filter_by(
            enrichment_status="completed"
        ).all()

        logger.info(f"Found {len(messages)} enriched messages to embed")

        if not messages:
            job.status = "completed"
            session.commit()
            logger.warning("No enriched messages found for embedding")
            return

        # Set total message count for progress tracking
        job.total_messages = len(messages)
        session.commit()

        # Index each message
        for idx, msg in enumerate(messages):
            try:
                # Get extractions
                extractions = session.query(Extraction).filter_by(
                    message_id=msg.id
                ).all()

                extractions_by_task = {
                    ext.task_name: ext.extraction_json for ext in extractions
                }

                # Build metadata
                metadata = {
                    "message_id": msg.id,
                    "subject": msg.subject or "",
                    "sender": msg.sender_email or "",
                    "date": msg.delivery_date.isoformat() if msg.delivery_date else "",
                    "importance_tier": extractions_by_task.get("task_c_importance", ""),
                }

                # Index message
                vector_store.index_message(
                    msg.id,
                    msg.subject or "",
                    msg.body_full or msg.body_snippet or "",
                    extractions_by_task,
                    metadata
                )

                # Throttle requests to prevent Ollama overload (0.5 second delay between embeddings)
                time.sleep(0.5)

                # Record embedding metadata
                embedding_record = session.query(MessageEmbedding).filter_by(
                    message_id=msg.id
                ).first()

                if not embedding_record:
                    embedding_record = MessageEmbedding(message_id=msg.id)
                    session.add(embedding_record)

                session.commit()

                # Update progress
                job.processed_messages = idx + 1
                session.commit()

                if (idx + 1) % 50 == 0:
                    progress = int((idx + 1) / len(messages) * 100)
                    logger.info(f"Embedded {idx + 1}/{len(messages)} messages ({progress}%)")

            except Exception as e:
                logger.error(f"Error embedding message {msg.id}: {e}")
                # Continue with next message
                continue

        # Mark job as complete
        job.status = "completed"
        job.processed_messages = job.total_messages
        session.commit()

        logger.info(f"✅ Embedding generation complete: {len(messages)} messages indexed")

    except Exception as e:
        logger.error(f"Critical error in embedding generation task: {e}")
        try:
            db_path = get_db_path()
            engine = init_db(db_path)
            session = get_session(engine)
            job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(e)
                session.commit()
        except Exception as update_error:
            logger.error(f"Failed to update job status: {update_error}")


# Mount frontend directory for serving static files
# NOTE: This must be mounted AFTER all API endpoints, otherwise it will
# intercept all requests before they reach the API handlers
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
    logger.info(f"✅ Frontend served from: {frontend_dir}")
else:
    logger.warning(f"⚠️ Frontend directory not found: {frontend_dir}")


def _post_aggregation_filter_task(
    job_id: str,
    role_description: str,
    confidence_threshold: float,
    projects: List[Dict]
):
    """
    Background task to run post-aggregation quality filter

    Evaluates aggregated projects for relevance to user's role using LLM.
    Stores results in ProjectClusterMetadata and updates job status.

    Args:
        job_id: Unique job identifier
        role_description: User's role and responsibilities (free-text description)
        confidence_threshold: Min confidence to include project (0.0-1.0)
        projects: List of aggregated project dicts from aggregation output
    """
    try:
        logger.info(f"Starting post-aggregation filter job: {job_id}")

        # Get database session and update job status
        db_path = get_db_path()
        engine = init_db(db_path)
        session = get_session(engine)

        job = session.query(ProcessingJob).filter_by(job_id=job_id).first()
        if not job:
            logger.error(f"Job not found: {job_id}")
            return

        job.status = "processing"
        job.current_task = "post_aggregation_filter"
        session.commit()

        # Initialize filter
        filter_engine = PostAggregationFilter(session, ollama_client, prompt_manager, config)

        # Run filter
        included_projects, excluded_projects, filter_results = filter_engine.filter_projects(
            projects,
            role_description,
            confidence_threshold
        )

        # Store results in database
        session.commit()

        # Write filtered projects JSON
        output_dir = config.get("output", {}).get("dir", "./data")
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Write included projects to filtered_projects.json
        filtered_output = {
            "role_description": role_description,
            "confidence_threshold": confidence_threshold,
            "total_projects": len(projects),
            "included_count": len(included_projects),
            "excluded_count": len(excluded_projects),
            "avg_confidence": filter_engine.stats.get("avg_confidence", 0),
            "confidence_distribution": filter_engine.stats.get("confidence_distribution", {}),
            "projects": included_projects,
            "filter_results": filter_results
        }

        filtered_file = output_path / "filtered_projects.json"
        with open(filtered_file, "w", encoding="utf-8") as f:
            json_lib.dump(filtered_output, f, indent=2, ensure_ascii=False)

        logger.info(f"Wrote filtered projects to {filtered_file}")

        # Update job status with statistics
        job.status = "completed"
        job.processed_messages = len(projects)
        job.error_message = json_lib.dumps({
            "role_description": role_description,
            "confidence_threshold": confidence_threshold,
            "projects_included": len(included_projects),
            "projects_excluded": len(excluded_projects),
            "avg_confidence": filter_engine.stats.get("avg_confidence", 0)
        })
        session.commit()

        logger.info(
            f"Filter complete: job_id={job_id}, "
            f"role_description_length={len(role_description)}, "
            f"included={len(included_projects)}, "
            f"excluded={len(excluded_projects)}, "
            f"avg_confidence={filter_engine.stats.get('avg_confidence', 0):.2f}"
        )

    except Exception as e:
        logger.error(f"Error in post-aggregation filter task: {e}")
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
