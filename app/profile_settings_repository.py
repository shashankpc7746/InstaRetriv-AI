import json
from datetime import datetime, timezone
from pathlib import Path


class ProfileSettingsRepository:
    def __init__(self, settings_file: str) -> None:
        self.settings_path = Path(settings_file)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.settings_path.exists():
            self._write({"private_access_code_hash": None, "updated_at": None})

    def _read(self) -> dict:
        raw = self.settings_path.read_text(encoding="utf-8").strip() or "{}"
        data = json.loads(raw)
        if "private_access_code_hash" not in data:
            data["private_access_code_hash"] = None
        if "updated_at" not in data:
            data["updated_at"] = None
        return data

    def _write(self, data: dict) -> None:
        self.settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_private_access_code_hash(self) -> str | None:
        data = self._read()
        value = data.get("private_access_code_hash")
        if not value:
            return None
        return str(value)

    def set_private_access_code_hash(self, code_hash: str) -> None:
        data = self._read()
        data["private_access_code_hash"] = code_hash
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write(data)
