from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


def is_remote_storage_path(path: str) -> bool:
    value = (path or "").strip().lower()
    return value.startswith("http://") or value.startswith("https://")


class LocalStorageService:
    def __init__(self, upload_dir: str) -> None:
        self.upload_path = Path(upload_dir)
        self.upload_path.mkdir(parents=True, exist_ok=True)

    async def save(self, upload_file: UploadFile) -> str:
        file_suffix = Path(upload_file.filename or "").suffix
        stored_name = f"{uuid4()}{file_suffix}"
        destination = self.upload_path / stored_name

        data = await upload_file.read()
        destination.write_bytes(data)

        return str(destination)


class CloudinaryStorageService:
    def __init__(self, cloud_name: str, api_key: str, api_secret: str) -> None:
        try:
            import cloudinary
        except ImportError as exc:
            raise RuntimeError("cloudinary package is required for cloudinary storage backend") from exc

        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True,
        )

    async def save(self, upload_file: UploadFile) -> str:
        try:
            import cloudinary.uploader
        except ImportError as exc:
            raise RuntimeError("cloudinary package is required for cloudinary storage backend") from exc

        data = await upload_file.read()
        if not data:
            raise RuntimeError("empty file payload")

        file_name = upload_file.filename or "document"
        file_suffix = Path(file_name).suffix.lower()
        image_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        resource_type = "image" if file_suffix in image_suffixes else "raw"
        public_id = f"instaretriv/{uuid4()}"

        upload_result = cloudinary.uploader.upload(
            data,
            resource_type=resource_type,
            type="upload",
            access_mode="public",
            public_id=public_id,
            filename=file_name,
            use_filename=False,
            unique_filename=False,
            overwrite=False,
        )

        secure_url = str(upload_result.get("secure_url", "")).strip()
        if not secure_url:
            raise RuntimeError("cloudinary upload succeeded without secure_url")
        return secure_url
