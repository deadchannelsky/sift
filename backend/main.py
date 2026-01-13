"""
Sift Backend - FastAPI application
Entry point for PST parsing and enrichment pipeline
"""
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uuid
import os
from pathlib import Path

from app.models import init_db, get_session, ProcessingJob
from app.pst_parser import PSTParser
from app.utils import logger, get_db_path, ensure_data_dir

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

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    logger.info("=== Sift Backend Starting ===")
    ensure_data_dir()
    db_path = get_db_path()
    init_db(db_path)
    logger.info(f"Database initialized: {db_path}")


# Request/Response models
class ParseRequest(BaseModel):
    date_start: str = "2025-10-01"
    date_end: str = "2025-12-31"
    min_conversation_messages: int = 3


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
    file: UploadFile = File(...),
    request: ParseRequest = None,
    background_tasks: BackgroundTasks = None
):
    """
    Upload PST file and start parsing job

    Args:
        file: PST file to parse
        request: JSON body with date_start, date_end, min_conversation_messages

    Returns:
        {
            "job_id": "abc123",
            "status": "queued",
            "message": "PST file uploaded, parsing started"
        }
    """
    if request is None:
        request = ParseRequest()

    # Validate file
    if not file.filename.endswith(".pst"):
        raise HTTPException(status_code=400, detail="File must be .pst format")

    try:
        # Save uploaded file
        ensure_data_dir()
        pst_dir = Path("data")
        pst_path = pst_dir / file.filename

        with open(pst_path, "wb") as f:
            contents = await file.read()
            f.write(contents)

        logger.info(f"PST file uploaded: {pst_path} ({len(contents)} bytes)")

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
                request.min_conversation_messages
            )

        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"PST file uploaded: {file.filename}. Parsing started."
        }

    except Exception as e:
        logger.error(f"Error uploading PST: {e}")
        raise HTTPException(status_code=500, detail=f"Error uploading file: {str(e)}")


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


# ============================================================================
# BACKGROUND TASKS
# ============================================================================

def _parse_pst_task(
    job_id: str,
    pst_path: str,
    date_start: str,
    date_end: str,
    min_conversation_messages: int
):
    """Background task to parse PST file"""
    try:
        logger.info(f"Starting PST parsing job: {job_id}")

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
            min_conversation_messages
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
