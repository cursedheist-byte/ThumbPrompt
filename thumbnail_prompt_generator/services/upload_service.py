"""
Validates and stores uploaded reference images.

Design choices:
- Images are validated for extension + size before anything touches disk.
- Files are saved to a per-generation temp path under Config.UPLOAD_FOLDER
  only long enough to be read into memory for the Gemini call and, for
  the face image, potentially referenced again at the finalize step.
  A best-effort cleanup pass (cleanup_old_uploads) removes anything
  older than UPLOAD_RETENTION_MINUTES so we don't permanently retain
  creators' faces/reference art.
"""

import os
import time
import uuid

from werkzeug.utils import secure_filename

from config import Config


class UploadValidationError(Exception):
    pass


def _allowed_extension(filename: str) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in Config.ALLOWED_IMAGE_EXTENSIONS


def validate_and_read(file_storage, field_label: str) -> bytes:
    """Validate an uploaded file and return its raw bytes. Raises
    UploadValidationError with a user-facing message on any problem."""
    if file_storage is None or file_storage.filename == "":
        raise UploadValidationError(f"No file provided for {field_label}.")

    if not _allowed_extension(file_storage.filename):
        raise UploadValidationError(
            f"{field_label}: unsupported file type. Allowed types: "
            f"{', '.join(sorted(Config.ALLOWED_IMAGE_EXTENSIONS))}."
        )

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)

    if size == 0:
        raise UploadValidationError(f"{field_label}: uploaded file is empty.")

    if size > Config.MAX_IMAGE_SIZE_BYTES:
        max_mb = Config.MAX_IMAGE_SIZE_BYTES / (1024 * 1024)
        raise UploadValidationError(
            f"{field_label}: file is too large ({size / (1024 * 1024):.1f} MB). "
            f"Maximum allowed is {max_mb:.0f} MB."
        )

    data = file_storage.read()
    file_storage.stream.seek(0)
    return data


def save_temp_file(data: bytes, original_filename: str, generation_id: str) -> str:
    """Persist bytes to a namespaced temp path and return the path.
    Used only where we need the file to survive between the analyze and
    finalize requests (currently: none by default, since we re-derive
    everything from the in-memory upload per request; kept here so a
    future flow that needs on-disk persistence has a ready helper)."""
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    safe_name = secure_filename(original_filename) or "upload"
    unique_name = f"{generation_id}_{uuid.uuid4().hex[:8]}_{safe_name}"
    path = os.path.join(Config.UPLOAD_FOLDER, unique_name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def cleanup_old_uploads():
    """Delete temp uploads older than UPLOAD_RETENTION_MINUTES. Call this
    periodically (e.g. from a scheduled job or on app boot) — MVP does
    not wire up a scheduler, so it's exposed as a plain function a
    future cron/worker can call."""
    if not os.path.isdir(Config.UPLOAD_FOLDER):
        return
    cutoff = time.time() - (Config.UPLOAD_RETENTION_MINUTES * 60)
    for name in os.listdir(Config.UPLOAD_FOLDER):
        if name == ".gitkeep":
            continue
        path = os.path.join(Config.UPLOAD_FOLDER, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            continue
