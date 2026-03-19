from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


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
