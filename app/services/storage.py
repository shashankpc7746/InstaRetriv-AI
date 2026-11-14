"""File storage backends.

Files are never handed out as public URLs. Local files are served by ``GET /files/{id}``
directly; Cloudinary files are uploaded as ``type="authenticated"`` assets and streamed
server-side through the same endpoint using short-lived signed delivery URLs.

Stored path formats:
- local:       "<upload_dir>/<uuid4><.ext>"
- cloudinary:  "cloudinary://<resource_type>/<delivery_type>/<public_id>[?format=<ext>]"
- legacy:      "https://res.cloudinary.com/..." (older public uploads, still readable)
"""

import logging
import os
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool


logger = logging.getLogger("instaretriv.storage")

DEFAULT_MAX_UPLOAD_BYTES = 15 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
STREAM_CHUNK_BYTES = 64 * 1024
CLOUDINARY_SCHEME = "cloudinary://"
CLOUDINARY_FOLDER = "instaretriv"
CLOUDINARY_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "gif"})
CLOUDINARY_RESOURCE_TYPES = frozenset({"image", "raw", "video"})

_SAFE_SUFFIX_RE = re.compile(r"^[a-z0-9]{1,10}$")

_CONTENT_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}
DEFAULT_CONTENT_TYPE = "application/octet-stream"


class UploadRejectedError(ValueError):
    """The upload itself is unacceptable (empty, too large, bad name). Message is user-facing."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class StorageUnavailableError(RuntimeError):
    """The storage backend cannot be reached or is not configured."""


class StoredFileNotFoundError(FileNotFoundError):
    """The stored object no longer exists."""


def is_remote_storage_path(path: str) -> bool:
    value = (path or "").strip().lower()
    return value.startswith(("http://", "https://", CLOUDINARY_SCHEME))


def _extension_of(file_name_or_ext: str) -> str:
    value = (file_name_or_ext or "").strip().lower()
    # Accept "pdf", ".pdf", "report.pdf", "a/b/report.PDF".
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    return value


def guess_content_type(file_name_or_ext: str) -> str:
    return _CONTENT_TYPES.get(_extension_of(file_name_or_ext), DEFAULT_CONTENT_TYPE)


def sanitize_suffix(file_name: str | None) -> str:
    """Return a safe lowercase suffix like '.pdf', or '' when the name has no usable extension."""
    name = (file_name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if "." not in name:
        return ""
    ext = name.rsplit(".", 1)[-1].lower()
    if not _SAFE_SUFFIX_RE.match(ext):
        return ""
    return f".{ext}"


def _max_upload_mb_text(max_bytes: int) -> str:
    megabytes = max_bytes / (1024 * 1024)
    return f"{megabytes:g} MB"


async def read_upload_limited(upload_file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload in chunks, rejecting it as soon as it exceeds ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload_file.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadRejectedError(
                f"File is too large. Maximum size is {_max_upload_mb_text(max_bytes)}.",
                status_code=413,
            )
        chunks.append(chunk)
    if total == 0:
        raise UploadRejectedError("Uploaded file is empty.", status_code=400)
    return b"".join(chunks)


def _redact_url(url: str) -> str:
    """Scheme + host only: signed URLs carry credentials in the path/query."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid url>"
    return f"{parts.scheme}://{parts.netloc}/…" if parts.netloc else "<invalid url>"


def stream_url(url: str, timeout: float = 20) -> Iterator[bytes]:
    """Fetch ``url`` and return an iterator over its bytes.

    The request and status check happen eagerly (when this function is called), so the
    caller can map errors to HTTP responses before streaming starts:
    404/410 -> StoredFileNotFoundError; other >= 400 or network errors -> StorageUnavailableError.
    """
    import requests

    try:
        response = requests.get(url, stream=True, timeout=timeout)
    except requests.RequestException as exc:
        logger.warning("Remote file fetch failed for %s: %s", _redact_url(url), type(exc).__name__)
        raise StorageUnavailableError("Remote storage is unreachable") from exc

    status = response.status_code
    if status in {404, 410}:
        response.close()
        logger.warning("Remote file missing (HTTP %s) at %s", status, _redact_url(url))
        raise StoredFileNotFoundError("Stored file not found")
    if status >= 400:
        response.close()
        logger.warning("Remote storage returned HTTP %s for %s", status, _redact_url(url))
        raise StorageUnavailableError(f"Remote storage returned HTTP {status}")

    return _iter_response(response, url)


def _iter_response(response, url: str) -> Iterator[bytes]:
    import requests

    try:
        for chunk in response.iter_content(chunk_size=STREAM_CHUNK_BYTES):
            if chunk:
                yield chunk
    except requests.RequestException as exc:
        logger.warning("Remote file stream interrupted for %s: %s", _redact_url(url), type(exc).__name__)
        raise StorageUnavailableError("Remote file stream interrupted") from exc
    finally:
        response.close()


def _resolve_existing_path(storage_path: str, fallback_dir: Path | None = None) -> Path | None:
    """Return an existing local file for ``storage_path`` or None (never raises)."""
    value = (storage_path or "").strip()
    if not value or is_remote_storage_path(value):
        return None
    candidates = [Path(value)]
    if fallback_dir is not None:
        # Legacy metadata may hold an absolute path from another machine; try the same
        # file name inside the configured upload dir.
        base_name = value.replace("\\", "/").rsplit("/", 1)[-1]
        if base_name and base_name not in {".", ".."}:
            candidates.append(fallback_dir / base_name)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except (OSError, ValueError):
            continue
    return None


def format_cloudinary_path(resource_type: str, public_id: str, delivery_type: str = "authenticated", file_format: str | None = None) -> str:
    path = f"{CLOUDINARY_SCHEME}{resource_type}/{delivery_type}/{public_id}"
    if file_format:
        path += "?" + urlencode({"format": file_format})
    return path


def parse_cloudinary_path(storage_path: str) -> tuple[str, str, str, str | None]:
    """Parse a cloudinary:// path into (resource_type, delivery_type, public_id, format).

    Raises StoredFileNotFoundError for malformed paths.
    """
    value = (storage_path or "").strip()
    if not value.lower().startswith(CLOUDINARY_SCHEME):
        raise StoredFileNotFoundError("Not a Cloudinary storage path")
    remainder = value[len(CLOUDINARY_SCHEME):]
    remainder, _, query = remainder.partition("?")
    parts = remainder.split("/", 2)
    if len(parts) != 3 or not all(parts):
        raise StoredFileNotFoundError("Malformed Cloudinary storage path")
    resource_type, delivery_type, public_id = parts
    if resource_type not in CLOUDINARY_RESOURCE_TYPES:
        raise StoredFileNotFoundError("Malformed Cloudinary storage path")
    file_format = (parse_qs(query).get("format") or [None])[0] or None
    return resource_type, delivery_type, public_id, file_format


class LocalStorageService:
    backend_name = "local"

    def __init__(self, upload_dir: str, max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES) -> None:
        self.upload_path = Path(upload_dir)
        self.upload_path.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max(1, int(max_bytes))

    async def save(self, upload_file: UploadFile) -> str:
        data = await read_upload_limited(upload_file, self.max_bytes)
        stored_name = f"{uuid4()}{sanitize_suffix(upload_file.filename)}"
        destination = self.upload_path / stored_name
        await run_in_threadpool(self._write_atomic, destination, data)
        return str(destination)

    @staticmethod
    def _write_atomic(destination: Path, data: bytes) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=str(destination.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, destination)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def resolve_local_path(self, storage_path: str) -> Path | None:
        return _resolve_existing_path(storage_path, self.upload_path)

    def iter_remote(self, storage_path: str) -> Iterator[bytes]:
        value = (storage_path or "").strip()
        if value.lower().startswith(("http://", "https://")):
            return stream_url(value)
        if value.lower().startswith(CLOUDINARY_SCHEME):
            raise StorageUnavailableError("Cloudinary is not configured")
        raise StoredFileNotFoundError("Not a remote storage path")


class CloudinaryStorageService:
    backend_name = "cloudinary"

    def __init__(self, cloud_name: str, api_key: str, api_secret: str, max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES) -> None:
        if not (cloud_name.strip() and api_key.strip() and api_secret.strip()):
            raise StorageUnavailableError("Cloudinary is not configured")
        try:
            import cloudinary
        except ImportError as exc:
            raise StorageUnavailableError("cloudinary package is required for the cloudinary storage backend") from exc

        self._credentials = {
            "cloud_name": cloud_name.strip(),
            "api_key": api_key.strip(),
            "api_secret": api_secret.strip(),
        }
        cloudinary.config(secure=True, **self._credentials)
        self.max_bytes = max(1, int(max_bytes))

    async def save(self, upload_file: UploadFile) -> str:
        data = await read_upload_limited(upload_file, self.max_bytes)
        suffix = sanitize_suffix(upload_file.filename)
        extension = suffix.lstrip(".")
        resource_type = "image" if extension in CLOUDINARY_IMAGE_EXTENSIONS else "raw"
        # Raw assets keep the extension in the public_id (that is how Cloudinary delivers them);
        # images get the format appended at delivery time instead.
        base_id = f"{CLOUDINARY_FOLDER}/{uuid4()}"
        public_id = f"{base_id}{suffix}" if resource_type == "raw" else base_id
        upload_name = f"document{suffix}"

        result = await run_in_threadpool(self._upload, data, resource_type, public_id, upload_name)

        stored_public_id = str(result.get("public_id") or public_id).strip()
        file_format = None
        if resource_type == "image":
            file_format = str(result.get("format") or extension or "").strip().lower() or None
        return format_cloudinary_path(resource_type, stored_public_id, "authenticated", file_format)

    def _upload(self, data: bytes, resource_type: str, public_id: str, upload_name: str) -> dict:
        try:
            import cloudinary.uploader

            result = cloudinary.uploader.upload(
                data,
                resource_type=resource_type,
                type="authenticated",
                public_id=public_id,
                filename=upload_name,
                use_filename=False,
                unique_filename=False,
                overwrite=False,
                **self._credentials,
            )
        except Exception as exc:  # cloudinary.exceptions.Error, network errors, etc.
            logger.error("Cloudinary upload failed: %s: %s", type(exc).__name__, str(exc)[:200])
            raise StorageUnavailableError("Cloudinary upload failed") from exc

        if not isinstance(result, dict) or not result.get("public_id"):
            logger.error("Cloudinary upload returned an unexpected response")
            raise StorageUnavailableError("Cloudinary upload returned an unexpected response")
        return result

    def resolve_local_path(self, storage_path: str) -> Path | None:
        return _resolve_existing_path(storage_path)

    def signed_delivery_url(self, storage_path: str) -> str:
        resource_type, delivery_type, public_id, file_format = parse_cloudinary_path(storage_path)
        try:
            import cloudinary.utils

            options: dict = {
                "resource_type": resource_type,
                "type": delivery_type,
                "sign_url": True,
                "secure": True,
                **self._credentials,
            }
            if file_format:
                options["format"] = file_format
            url, _ = cloudinary.utils.cloudinary_url(public_id, **options)
        except Exception as exc:
            logger.error("Could not build Cloudinary delivery URL: %s", type(exc).__name__)
            raise StorageUnavailableError("Could not build Cloudinary delivery URL") from exc
        return url

    def iter_remote(self, storage_path: str) -> Iterator[bytes]:
        value = (storage_path or "").strip()
        if value.lower().startswith(CLOUDINARY_SCHEME):
            return stream_url(self.signed_delivery_url(value))
        if value.lower().startswith(("http://", "https://")):
            return stream_url(value)
        raise StoredFileNotFoundError("Not a remote storage path")
