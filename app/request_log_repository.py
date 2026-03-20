import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RequestLogRepository:
    def __init__(self, log_file: str) -> None:
        self.log_path = Path(log_file)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.write_text("[]", encoding="utf-8")

    def _read_all(self) -> list[dict[str, Any]]:
        raw = self.log_path.read_text(encoding="utf-8").strip() or "[]"
        return json.loads(raw)

    def _write_all(self, logs: list[dict[str, Any]]) -> None:
        self.log_path.write_text(json.dumps(logs, indent=2), encoding="utf-8")

    def add(self, payload: dict[str, Any]) -> None:
        logs = self._read_all()
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        logs.append(payload)
        self._write_all(logs)

    def latest(self, limit: int = 20) -> list[dict[str, Any]]:
        logs = self._read_all()
        return logs[-limit:]
