"""
File upload handling for PST files

Handles:
- Safe file validation (extension, magic bytes)
- Streaming file saving (no memory buffer)
- Disk space checking
- Filename sanitization
- Upload cleanup
"""
import os
import re
from pathlib import Path
from typing import Tuple, Optional
from app.utils import logger


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent path traversal attacks

    Args:
        filename: Original filename from upload

    Returns:
        Safe filename with dangerous characters removed
    """
    # Remove path separators and parent directory references
    filename = filename.replace("\\", "").replace("/", "")
    filename = filename.replace("..", "")

    # Keep only alphanumeric, dots, hyphens, underscores
    filename = re.sub(r'[^\w\.\-]', '', filename)

    # Prevent empty filename
    if not filename:
        filename = "uploaded_file.pst"

    return filename


def validate_pst_file(file_path: Path) -> Tuple[bool, str]:
    """
    Validate that file is a valid PST file

    Checks:
    - File extension is .pst
    - File has PST magic bytes (0x21 0x42 0x44 0x4E = !BDN in ASCII)
    - File is not empty

    Args:
        file_path: Path to uploaded file

    Returns:
        (is_valid, error_message)
    """
    # Check extension
    if file_path.suffix.lower() != ".pst":
        return False, f"Invalid file extension: {file_path.suffix}. Expected .pst"

    # Check file exists and has size
    if not file_path.exists():
        return False, "File not found after upload"

    file_size = file_path.stat().st_size
    if file_size == 0:
        return False, "Uploaded file is empty"

    # Check PST magic bytes
    # PST files start with: 0x21 0x42 0x44 0x4E (!BDN in ASCII)
    try:
        with open(file_path, "rb") as f:
            magic_bytes = f.read(4)

        if magic_bytes != b'\x21\x42\x44\x4E':
            return False, "File is not a valid PST file (invalid magic bytes)"
    except IOError as e:
        return False, f"Error reading file: {e}"

    return True, ""


def check_disk_space(target_dir: Path, required_bytes: int) -> Tuple[bool, str]:
    """
    Check if target directory has enough free space

    Args:
        target_dir: Directory where file will be saved
        required_bytes: Number of bytes needed

    Returns:
        (has_space, message)
    """
    if not target_dir.exists():
        return False, f"Target directory does not exist: {target_dir}"

    stat = os.statvfs(str(target_dir))
    available_bytes = stat.f_bavail * stat.f_frsize

    # Add 10% buffer to available space
    safe_available = available_bytes * 0.9

    if required_bytes > safe_available:
        required_gb = required_bytes / (1024 ** 3)
        available_gb = safe_available / (1024 ** 3)
        return False, f"Not enough disk space. Required: {required_gb:.1f}GB, Available: {available_gb:.1f}GB"

    return True, ""


def save_uploaded_file(file_path: Path, file_content: bytes, chunk_size: int = 1024 * 1024) -> Tuple[bool, str]:
    """
    Save uploaded file to disk with streaming

    Args:
        file_path: Path where file should be saved
        file_content: Binary content to save
        chunk_size: Size of chunks to write at a time (default 1MB)

    Returns:
        (success, message)
    """
    try:
        # Create parent directory if needed
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file (streaming for large files)
        with open(file_path, "wb") as f:
            if isinstance(file_content, bytes):
                f.write(file_content)
            else:
                # Handle stream/generator
                while True:
                    chunk = file_content.read(chunk_size) if hasattr(file_content, 'read') else None
                    if not chunk:
                        break
                    f.write(chunk)

        logger.info(f"Saved uploaded file: {file_path} ({file_path.stat().st_size} bytes)")
        return True, ""

    except IOError as e:
        logger.error(f"Error saving file: {e}")
        return False, f"Error saving file: {e}"


def cleanup_old_uploads(upload_dir: Path, keep_latest_n: int = 5) -> int:
    """
    Clean up old uploaded files, keeping only the latest N files

    Args:
        upload_dir: Directory containing uploaded files
        keep_latest_n: Number of latest files to keep

    Returns:
        Number of files deleted
    """
    if not upload_dir.exists():
        return 0

    # Get all PST files in directory
    pst_files = sorted(
        upload_dir.glob("*.pst"),
        key=lambda p: p.stat().st_mtime,
        reverse=True  # Most recent first
    )

    # Delete files beyond the keep limit
    deleted_count = 0
    for pst_file in pst_files[keep_latest_n:]:
        try:
            pst_file.unlink()
            logger.info(f"Cleaned up old upload: {pst_file}")
            deleted_count += 1
        except Exception as e:
            logger.warning(f"Could not delete {pst_file}: {e}")

    return deleted_count


def get_upload_stats(upload_dir: Path) -> dict:
    """
    Get statistics about uploaded files

    Args:
        upload_dir: Directory containing uploads

    Returns:
        Dictionary with upload statistics
    """
    if not upload_dir.exists():
        return {
            "total_files": 0,
            "total_size_gb": 0,
            "latest_file": None
        }

    pst_files = list(upload_dir.glob("*.pst"))
    total_size = sum(f.stat().st_size for f in pst_files)
    total_gb = total_size / (1024 ** 3)

    latest_file = None
    if pst_files:
        latest = max(pst_files, key=lambda p: p.stat().st_mtime)
        latest_file = {
            "name": latest.name,
            "size_mb": latest.stat().st_size / (1024 ** 2),
            "modified": latest.stat().st_mtime
        }

    return {
        "total_files": len(pst_files),
        "total_size_gb": round(total_gb, 2),
        "latest_file": latest_file
    }
