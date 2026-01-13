"""
Utility functions: logging, progress tracking, error handling
"""
import logging
import os
from datetime import datetime
from pathlib import Path

# Ensure logs directory exists (relative to backend installation)
# Get the backend directory (where this file is located)
BACKEND_DIR = Path(__file__).parent.parent
LOG_DIR = BACKEND_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Configure logging
def setup_logger(name: str = "sift") -> logging.Logger:
    """Set up logger with file and console handlers"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Prevent duplicate handlers
    if logger.hasHandlers():
        return logger

    # File handler
    log_file = LOG_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_enrichment.log"
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.INFO)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    # Formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


logger = setup_logger()


class ProgressTracker:
    """Track progress of PST parsing and enrichment"""
    def __init__(self, total: int, label: str = "Processing"):
        self.total = total
        self.current = 0
        self.label = label

    def update(self, count: int = 1):
        """Update progress"""
        self.current += count
        percent = (self.current / self.total * 100) if self.total > 0 else 0
        logger.info(f"{self.label}: {self.current}/{self.total} ({percent:.1f}%)")

    def log_message(self, msg_id: str, subject: str, confidence: float = None):
        """Log a processed message"""
        if confidence is not None:
            logger.info(f"  MSG {msg_id}: {subject[:60]} [confidence={confidence:.2f}]")
        else:
            logger.info(f"  MSG {msg_id}: {subject[:60]}")

    def log_error(self, msg_id: str, error: str):
        """Log an error"""
        logger.error(f"  MSG {msg_id}: {error}")


class TaskTimer:
    """Simple timer for measuring task performance"""
    def __init__(self, task_name: str):
        self.task_name = task_name
        self.start_time = None
        self.end_time = None

    def __enter__(self):
        self.start_time = datetime.now()
        logger.info(f"Started: {self.task_name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = datetime.now()
        duration_ms = (self.end_time - self.start_time).total_seconds() * 1000
        if exc_type:
            logger.error(f"Failed: {self.task_name} ({duration_ms:.0f}ms) - {exc_val}")
        else:
            logger.info(f"Completed: {self.task_name} ({duration_ms:.0f}ms)")

    @property
    def duration_ms(self):
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds() * 1000
        return None


def ensure_data_dir():
    """Ensure data directory exists (relative to backend installation)"""
    data_dir = BACKEND_DIR / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "outputs").mkdir(exist_ok=True)
    return data_dir


def get_db_path() -> str:
    """Get path to SQLite database (relative to backend installation)"""
    data_dir = ensure_data_dir()
    return str(data_dir / "messages.db")
